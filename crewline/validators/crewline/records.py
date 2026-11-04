"""Commands that write task records, so nobody hand-edits the manifest JSON, and `crewline status` to read them.

  REC-001  value refused before anything is written: the enum or schema says what is legal           BLOCK
  REC-002  recorded                                                                                   INFO
  MAN-010  an agent named as approver                                                                 BLOCK
  MAN-011  a reviewer who is an author of the change                                                  BLOCK

Every write validates the whole manifest against the schema first; a refused write leaves the file as it was.
A record says what a person decided or a reviewer returned. These commands do not decide: run them with what was said.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import manifest
from .result import BLOCK, INFO, Result

REPAIR_CAP = 3


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _save(path: str, doc: dict, res: Result, done: str) -> Result:
    import jsonschema
    schema = json.loads(manifest.SCHEMA.read_text(encoding="utf-8"))
    for err in jsonschema.Draft202012Validator(schema).iter_errors(doc):
        res.add("REC-001", BLOCK, f"{'/'.join(map(str, err.path)) or '<root>'}: {err.message}")
    if res.findings:
        return res.finish()
    Path(path).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    res.add("REC-002", INFO, done)
    return res.finish()


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _is_agent(name: str) -> bool:
    who = name.lower()
    return who in manifest.agent_names() or who.startswith("agent:")


def review_record(path: str, review: str, result: str, reviewer: str | None = None) -> Result:
    res = Result("review-record", inputs={"manifest": path, "review": review, "result": result})
    doc = _load(path)
    spec = json.loads(manifest.SCHEMA.read_text(encoding="utf-8"))["properties"]["reviews"]["properties"]
    if review not in spec or "enum" not in spec[review]:
        res.add("REC-001", BLOCK, f"unknown review {review!r}: {', '.join(k for k, v in spec.items() if 'enum' in v)}")
        return res.finish()
    legal = [v for v in spec[review]["enum"] if v != "NOT_STARTED"]
    if result not in legal:
        hint = " (a review that found problems is FINDINGS)" if result.upper() == "FAIL" else ""
        res.add("REC-001", BLOCK, f"{review} cannot be {result!r}: {', '.join(legal)}{hint}")
        return res.finish()
    if result != "NOT_APPLICABLE" and not reviewer:
        res.add("REC-001", BLOCK, "pass --reviewer <context id of the reviewer>")
        return res.finish()
    authors = {a.lower() for a in (doc.get("implementation") or {}).get("authors", [])}
    reviews = doc.setdefault("reviews", {})
    people = reviews.setdefault("reviewers", {})
    if reviewer:
        if reviewer.lower() in authors:
            res.add("MAN-011", BLOCK, f"{reviewer} is an author of the change: no self-review")
            return res.finish()
        other = {"implementation_review": "fresh_context_review", "fresh_context_review": "implementation_review"}.get(review)
        if other and (people.get(other) or "").lower() == reviewer.lower():
            res.add("MAN-011", BLOCK, f"{reviewer} already did {other}: the second review needs a new context")
            return res.finish()
        people[review] = reviewer
    reviews[review] = result
    return _save(path, doc, res, f"{review} = {result}" + (f" by {reviewer}" if reviewer else ""))


def approval_add(path: str, action: str, approver: str, reason: str, scope: str, gate: str | None = None,
                 exception: bool = False, follow_up: str | None = None, no_follow_up: str | None = None) -> Result:
    res = Result("approval-add", inputs={"manifest": path, "action": action, "approver": approver})
    if _is_agent(approver):
        res.add("MAN-010", BLOCK, f"{approver!r} is an agent: approvals come from a human, name the person who said yes")
        return res.finish()
    if exception and not gate:
        res.add("REC-001", BLOCK, "an exception names the gate it covers: pass --gate")
        return res.finish()
    doc = _load(path)
    rec = {"action": action, "approver": approver, "approver_type": "human", "reason": reason, "scope": scope, "timestamp": _now()}
    for key, val in (("gate", gate), ("follow_up", follow_up), ("no_follow_up", no_follow_up)):
        if val:
            rec[key] = val
    doc.setdefault("exceptions" if exception else "approvals", []).append(rec)
    return _save(path, doc, res, f"{'exception' if exception else 'approval'} {action} by {approver}")


def limitation_add(path: str, lim_id: str, text: str | None = None, refs: list[str] | None = None,
                   follow_up: str | None = None, no_follow_up: str | None = None) -> Result:
    """Add a known limitation, or re-run with the same id to link its follow-up (MAN-019 needs one by FINAL_REPORT)."""
    res = Result("limitation-add", inputs={"manifest": path, "id": lim_id})
    doc = _load(path)
    items = doc.setdefault("known_limitations", [])
    rec = next((k for k in items if k.get("id") == lim_id), None)
    if rec is None:
        if not text:
            res.add("REC-001", BLOCK, f"new limitation {lim_id} needs --text")
            return res.finish()
        rec = {"id": lim_id}
        items.append(rec)
    for key, val in (("text", text), ("refs", refs), ("follow_up", follow_up), ("no_follow_up", no_follow_up)):
        if val:
            rec[key] = val
    linked = rec.get("follow_up") or rec.get("no_follow_up")
    return _save(path, doc, res, f"{lim_id} recorded" + ("" if linked else "; it still needs a follow-up before FINAL_REPORT (MAN-019)"))


def status(path: str) -> Result:
    """What the orchestrator reads before offering options, so it never offers a choice the workflow forbids."""
    res = Result("status", inputs={"manifest": path})
    doc = _load(path)
    ctx = manifest.Ctx(doc)
    src = doc.get("current_state")
    rep = doc.get("repair") or {}
    sdet, dev = rep.get("attempt_count", 0), rep.get("dev_attempt_count", 0)
    if src in ("WAITING_FOR_USER", "S_ESCALATE"):
        moves = [doc["resume_state"]] if doc.get("resume_state") else []
    else:
        moves = ctx.next_states(src or "")
    forced = [g for g in ctx.wf.get("forced_escalation", {}).get(src, []) if ctx.holds(g)[0]]
    unused = [a.get("action") for a in doc.get("approvals", []) if not a.get("consumed_by")]
    lines = [
        f"{doc.get('task_id')}: {doc.get('task_type')} {doc.get('risk_tier')} {doc.get('outcome')}",
        f"state {src}" + (f", resumes to {doc['resume_state']}" if doc.get("resume_state") else ""),
        f"repair attempts: SDET {sdet} used, {max(REPAIR_CAP - sdet, 0)} left; developer {dev} used, {max(REPAIR_CAP - dev, 0)} left",
        "legal moves: " + ("S_ESCALATE only (forced)" if forced else ", ".join(moves) or "none") + "; WAITING_FOR_USER any time",
        "open approvals: " + (", ".join(unused) or "none"),
    ]
    if REPAIR_CAP - sdet <= 0 or REPAIR_CAP - dev <= 0:
        spent = "SDET" if REPAIR_CAP - sdet <= 0 else "developer"
        lines.append(f"do not offer another {spent} repair: the cap is 3 and no approval lifts it; S_ESCALATE is the way out")
    lines.append("`crewline manifest <m> --next` lists every precondition of each move")
    res.add("STS-001", INFO, "\n    ".join(lines))
    return res.finish()
