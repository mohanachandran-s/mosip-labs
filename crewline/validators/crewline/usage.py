"""Token usage per task, per workflow stage and per step, from the agent's own transcript.

  python -m crewline usage --manifest <task-manifest.json> --mark "<step label>"     # start a step (no transcript needed)
  python -m crewline usage --manifest <task-manifest.json> --transcript <session.jsonl> [--transcript ...] [--out usage.json]

The transcript (Claude Code: ~/.claude/projects/<workspace-slug>/<session-id>.jsonl, plus <session-id>/subagents/*.jsonl,
included automatically) records the API usage of every model call. Calls are assigned to the task by time:
  - stage = the workflow state the manifest was in when the call happened (manifest.started_at + state_history)
  - step  = the latest `usage_marks` label at or before the call
Token kinds: input (uncached), cache_write, cache_read, output. `fresh` = input + cache_write + output, the tokens that
were new to the model in that call; cache_read is the re-read conversation, cheap but often the largest number.
Calls are de-duplicated by message id. The numbers are what the API reported, not estimates.

  USE-001  no transcript records fall inside the task's time window   WARN
  USE-002  manifest has no started_at                                 BLOCK (cannot attribute usage)
  USE-003  task total tokens exceed usage-budget.yaml per_task        BLOCK
  USE-004  a workflow state exceeds its per_state / hot_states cap    BLOCK
  USE-005  a state is past warn_ratio of its cap                      WARN
  USE-006  no metered calls (typical on Cursor): adapter fire-break is the control  INFO
  USE-007  a specialist packet on disk exceeds packet_max_chars       BLOCK
  USE-008  a packet looks like a dump of Crewline core / a full report BLOCK
  USE-009  a recorded transition has no usage_mark (Cursor has no transcript; --apply must stamp one) BLOCK

Exceeding a budget is WAITING_FOR_USER: continue (attributed exception on token_budget), split the task, or abort.
"""
from __future__ import annotations

import bisect
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .result import BLOCK, INFO, WARN, Result

CORE = Path(__file__).resolve().parents[2]
BUDGET_FILE = CORE / "core" / "usage-budget.yaml"
DUMP_RX = re.compile(
    r"Carried over from the SDET Architect|core/workflow\.yaml|quality-gates\.yaml"
    r"|BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"
    r"|surefire-reports|ExtentHtmlReporter|full-report",
    re.I,
)

KINDS = ("input", "cache_write", "cache_read", "output")


def _ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def mark(manifest_path: str, label: str) -> Result:
    p = Path(manifest_path)
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc.setdefault("usage_marks", []).append({"label": label, "at": now_iso(), "state": doc.get("current_state")})
    p.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    res = Result("usage-mark", inputs={"label": label, "state": doc.get("current_state")})
    res.summary = f"step '{label}' started in {doc.get('current_state')}"
    return res.finish()


def calls(transcripts: list[str]) -> list[dict]:
    """[{at, model, input, cache_write, cache_read, output}] de-duplicated by message id, sorted by time."""
    files: list[Path] = []
    for t in transcripts:
        p = Path(t)
        files.append(p)
        sub = p.with_suffix("") / "subagents"
        if sub.is_dir():
            files += sorted(sub.glob("*.jsonl"))
    seen: dict[str, dict] = {}
    for f in files:
        with f.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                msg = d.get("message") if isinstance(d.get("message"), dict) else None
                u = (msg or {}).get("usage")
                if not u or not d.get("timestamp"):
                    continue
                key = msg.get("id") or f"{f.name}:{d.get('uuid')}"
                seen[key] = {"at": _ts(d["timestamp"]), "model": msg.get("model", ""), "subagent": f.parent.name == "subagents",
                             "input": u.get("input_tokens", 0) or 0, "cache_write": u.get("cache_creation_input_tokens", 0) or 0,
                             "cache_read": u.get("cache_read_input_tokens", 0) or 0, "output": u.get("output_tokens", 0) or 0}
    return sorted(seen.values(), key=lambda c: c["at"])


def load_budget(doc: dict) -> dict:
    raw = yaml.safe_load(BUDGET_FILE.read_text(encoding="utf-8"))
    over = (doc.get("usage") or {}).get("budget") or {}
    hot = dict(raw.get("hot_states") or {})
    if over.get("hot_states"):
        hot.update(over["hot_states"])
    elif "per_state" in over:
        hot = {}
    return {
        "per_task": int(over.get("per_task", raw["per_task"])),
        "per_state": int(over.get("per_state", raw["per_state"])),
        "warn_ratio": float(over.get("warn_ratio", raw.get("warn_ratio", 0.7))),
        "hot_states": hot,
        "packet_max_chars": int(over.get("packet_max_chars", raw.get("packet_max_chars", 20000))),
    }


def _check_packets(manifest_path: str, budget: dict, res: Result) -> None:
    root = Path(manifest_path).resolve().parent / "packets"
    if not root.is_dir():
        return
    cap = budget["packet_max_chars"]
    for f in sorted(p for p in root.rglob("*") if p.is_file()):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = f.relative_to(root).as_posix()
        if len(text) > cap:
            res.add("USE-007", BLOCK, f"packet {rel} is {len(text)} chars (max {cap}): send ACs + FAIL findings, not the repo",
                    path=rel)
        if DUMP_RX.search(text) or (len(text) > 8000 and "principles.md" in text):
            res.add("USE-008", BLOCK, f"packet {rel} looks like a core/report dump, not a specialist packet", path=rel)


def _bucket() -> dict:
    return {"calls": 0, **{k: 0 for k in KINDS}, "fresh": 0, "total": 0}


def _add(b: dict, c: dict) -> None:
    b["calls"] += 1
    for k in KINDS:
        b[k] += c[k]
    b["fresh"] += c["input"] + c["cache_write"] + c["output"]
    b["total"] += sum(c[k] for k in KINDS)


def report(manifest_path: str, transcripts: list[str], out: str | None = None) -> Result:
    res = Result("usage", inputs={"manifest": manifest_path, "transcripts": transcripts})
    doc = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if not doc.get("started_at"):
        res.add("USE-002", BLOCK, "manifest.started_at is missing: set it when the task is created")
        return res.finish()
    # a task can span sessions: transcripts recorded in the manifest are always included
    transcripts = list(dict.fromkeys(doc.get("usage_transcripts", []) + list(transcripts)))
    transcripts = [t for t in transcripts if Path(t).is_file()]
    res.inputs["transcripts"] = transcripts
    start = _ts(doc["started_at"])
    end = _ts(doc["finished_at"]) if doc.get("finished_at") else float("inf")
    hist = doc.get("state_history", [])
    stage_at = [start] + [_ts(h["at"]) for h in hist]
    stage_name = [doc.get("initial_state") or (hist[0]["from"] if hist else doc.get("current_state"))] + [h["to"] for h in hist]
    marks = sorted(doc.get("usage_marks", []), key=lambda m: m["at"])
    mark_at = [_ts(m["at"]) for m in marks]

    overall, by_stage, by_step = _bucket(), {}, {}
    for c in calls(transcripts):
        if not start <= c["at"] <= end:
            continue
        stage = stage_name[bisect.bisect_right(stage_at, c["at"]) - 1]
        i = bisect.bisect_right(mark_at, c["at"]) - 1
        step = marks[i]["label"] if i >= 0 else "(before first step mark)"
        for b in (overall, by_stage.setdefault(stage, _bucket()), by_step.setdefault(step, _bucket())):
            _add(b, c)
    budget = load_budget(doc)
    if not overall["calls"]:
        res.add("USE-001", WARN, "no model calls recorded in the task window (wrong transcript?)")
        res.add("USE-006", INFO, "no metered API calls: on Cursor the adapter token fire-break is the control; "
                "on Claude pass --transcript so the budget can stop the task")
    else:
        if overall["total"] > budget["per_task"]:
            res.add("USE-003", BLOCK, f"task total {overall['total']:,} exceeds budget {budget['per_task']:,} "
                    "(core/usage-budget.yaml). WAITING_FOR_USER: continue (exception token_budget) / split / abort")
        for stage, b in by_stage.items():
            cap = int(budget["hot_states"].get(stage, budget["per_state"]))
            if b["total"] > cap:
                res.add("USE-004", BLOCK, f"{stage}: {b['total']:,} exceeds per-state budget {cap:,} "
                        "(do not re-read core, do not poll, do not paste reports)")
            elif b["total"] > cap * budget["warn_ratio"]:
                res.add("USE-005", WARN, f"{stage}: {b['total']:,} is past {budget['warn_ratio']:.0%} of budget {cap:,}")
    hist = doc.get("state_history") or []
    marks = doc.get("usage_marks") or []
    if hist and not marks:
        res.add("USE-009", BLOCK, "state_history has hops but no usage_marks: `manifest --apply` stamps one; "
                "on Cursor that is the meter (USE-006)")
    for h in hist:
        label = f"{h.get('from')}->{h.get('to')}"
        hat = _ts(h["at"]) if h.get("at") else 0
        if not any(m.get("label") == label or _ts(m.get("at") or h.get("at") or "") >= hat for m in marks):
            res.add("USE-009", BLOCK, f"no usage_mark for {label}: `manifest --apply` stamps it")
    _check_packets(manifest_path, budget, res)
    data = {"task_id": doc.get("task_id"), "generated_at": now_iso(), "window": [doc["started_at"], doc.get("finished_at")],
            "budget": budget,
            "overall": overall, "by_stage": by_stage, "by_step": by_step,
            "note": "fresh = input + cache_write + output; total adds cache_read (re-read context)."}
    target = Path(out) if out else Path(manifest_path).with_name("usage.json")
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    md = target.with_suffix(".md")
    md.write_text(to_markdown(data), encoding="utf-8")
    res.inputs.update(overall=overall, report=str(md))
    res.summary = (f"{overall['calls']} calls, fresh {overall['fresh']:,} / total {overall['total']:,} tokens "
                   f"(output {overall['output']:,}); {md}")
    for stage, b in by_stage.items():
        res.add("USE-STAGE", INFO, f"{stage}: {b['calls']} calls, fresh {b['fresh']:,}, total {b['total']:,}")
    return res.finish()


def to_markdown(data: dict) -> str:
    def table(title: str, rows: dict) -> str:
        lines = [f"### {title}", "", "| | calls | input | cache write | cache read | output | fresh | total |",
                 "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for name, b in rows.items():
            lines.append(f"| {name} | {b['calls']} | {b['input']:,} | {b['cache_write']:,} | {b['cache_read']:,} | "
                         f"{b['output']:,} | {b['fresh']:,} | {b['total']:,} |")
        return "\n".join(lines) + "\n"
    return (f"# Token usage - {data['task_id']}\n\nWindow {data['window'][0]} -> {data['window'][1] or 'now'} "
            f"(generated {data['generated_at']}). {data['note']}\n\n"
            + table("Overall", {"task": data["overall"]}) + "\n" + table("By stage", data["by_stage"]) + "\n"
            + table("By step", data["by_step"]))
