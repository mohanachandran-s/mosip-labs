"""Task manifest checks and state-transition enforcement against core/workflow.yaml + the task-type template.

  MAN-001  manifest fails the JSON schema (templates/task-manifest.schema.json)
  MAN-002  current_state is not a known state, or not on the task type's path
  MAN-003  repair counters (attempt_count / dev_attempt_count) disagree with attempt_history
  MAN-004  outcome SUCCESS while a recorded required gate is not PASS / NOT_APPLICABLE
  MAN-005  an exception/approval record lacks approver, reason, scope or timestamp
  MAN-006..009  linked changes across repositories (see core/workflow.yaml linked_changes)
  MAN-010  an approval or exception was granted by an agent (the model cannot approve for itself)
  MAN-011  a reviewer is also an author of the change, or both reviews ran in the same context
  MAN-012  a gate the template requires is missing or not PASS / NOT_APPLICABLE (and has no exception)
  MAN-013  task_type unknown, or risk_tier lower than the template's
  MAN-014  at COMMIT_PR: a defect caused by the change is still open, or a pre-existing one is neither raised (bug key)
           nor deferred with a reason
  MAN-015  a gate in the template's `no_silent_na` is NOT_APPLICABLE without an attributed exception
  MAN-016  WARN: the task started on another Crewline version than the one running now (read CHANGELOG.md between
           the two before the next transition; the task keeps its records)
  MAN-017  tracker ticket: a move past INPUT_VALIDATION without a confirmed GitHub issue / Jira key or a human waiver
           (core/workflow.yaml tracker_guard); a key that does not fit its system; created_by_crewline without a
           create_tracker_item approval; both a key and a waiver; a posted closure comment without tracker_comment approval;
           pr.title not prefixed `<KEY>: ` (and leaving PR_CLOSEOUT without a recorded pr.title)
  MAN-018  subtasks under the ticket: past GITHUB_PLANNING without one subtask per kind the template lists (unless
           waived); a subtask key that does not fit the system; created_by_crewline without create_tracker_subtasks;
           a posted closure comment without tracker_comment; DONE while a subtask closure is neither posted nor declined
  MAN-019  reaching FINAL_REPORT with a waived finding (known limitation, gate exception) that has no follow-up link or
           accepted reason; a test suite no CI workflow runs (ci-coverage CIC-001) is refused on the same move
  TRN-001  transition is not an edge of the (template-projected) state machine
  TRN-002  entry, edge or exit guard not satisfied
  TRN-003  forced escalation: a repair cap reached, or application/security/specification issue
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .result import BLOCK, INFO, WARN, Result

CORE = Path(__file__).resolve().parents[2]
WORKFLOW = CORE / "core" / "workflow.yaml"
TEMPLATES = CORE / "core" / "workflows"
GATES = CORE / "core" / "quality-gates.yaml"
SCHEMA = CORE / "templates" / "task-manifest.schema.json"
APPROVAL_FIELDS = ("approver", "reason", "scope", "timestamp")
TIERS = ["L0", "L1", "L2", "L3"]
DEFAULT_TYPE = "test-automation"     # manifests from the SDET assistant era carry no task_type
NEVER_WAIVABLE = {"secrets", "requirement_clarity", "requirement_fit"}
TRACKER_KEY = {
    "github": re.compile(r"^([\w.-]+/[\w.-]+)?#\d+$"),       # owner/repo#12, or #12 on the primary upstream repo
    "jira": re.compile(r"^[A-Z][A-Z0-9]+-\d+$"),             # INJIVER-1386
}


def _get(doc: dict, path: str):
    cur = doc
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _gate(doc: dict, name: str):
    for g in doc.get("gates", []):
        if g.get("gate") == name:
            return g.get("status")
    return None


def load_workflow(path: Path = WORKFLOW) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_template(task_type: str | None) -> dict | None:
    p = TEMPLATES / f"{task_type or DEFAULT_TYPE}.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8")) if p.is_file() else None


def gate_states() -> dict[str, str]:
    spec = yaml.safe_load(GATES.read_text(encoding="utf-8"))["gates"]
    return {name: g.get("state", "") for name, g in spec.items()}


def agent_names() -> set[str]:
    names = {"orchestrator", "assistant", "agent", "ai", "claude", "cursor", "model"}
    for p in (CORE / "agents").rglob("*.md"):
        m = re.search(r"^name:\s*(\S+)", p.read_text(encoding="utf-8"), re.M)
        if m:
            names.add(m.group(1).lower())
    return names


class Ctx:
    """Everything a guard needs: the manifest, the workflow and the template's path."""

    def __init__(self, doc: dict):
        self.doc = doc
        self.wf = load_workflow()
        self.tpl = load_template(doc.get("task_type")) or {}
        self.order = list(self.wf["states"])
        self.path = self.tpl.get("path") or self.order
        self.gate_state = gate_states()

    def idx(self, s: str) -> int:
        return self.order.index(s) if s in self.order else -1

    def forward(self, s: str) -> str | None:
        """The main-line successor: states are declared in main-line order in core/workflow.yaml."""
        i = self.idx(s)
        return self.order[i + 1] if 0 <= i < len(self.order) - 1 else None

    def next_states(self, src: str) -> list[str]:
        """Template projection: edges into skipped states collapse forward; skipped loop-backs are dropped."""
        out = []
        for n in self.wf["states"].get(src, {}).get("next", []):
            if n not in self.path and self.idx(n) < self.idx(src):
                continue
            while n and n not in self.path:
                n = self.forward(n)
            if n and n not in out:
                out.append(n)
        return out

    def entry_guards(self, state: str) -> list[dict]:
        return self.wf.get("entry_guards", {}).get(state, []) + self.tpl.get("entry_guards", {}).get(state, [])

    def holds(self, guard: dict) -> tuple[bool, str]:
        doc = self.doc
        if "all_of" in guard:
            bad = [why for ok, why in (self.holds(g) for g in guard["all_of"]) if not ok]
            return not bad, "; ".join(bad)
        if "any_of" in guard:
            results = [self.holds(g) for g in guard["any_of"]]
            return any(ok for ok, _ in results), "none of: " + " | ".join(why for _, why in results)
        if "approval" in guard:
            # An approval authorises one guarded move; once a transition used it, it is consumed.
            ok = any(a.get("action") == guard["approval"] and all(a.get(k) for k in APPROVAL_FIELDS)
                     and not a.get("consumed_by") for a in doc.get("approvals", []))
            return ok, f"approval '{guard['approval']}' recorded with {', '.join(APPROVAL_FIELDS)}"
        gate = guard.get("gate") or (guard["path"].split(".", 1)[1] if guard.get("path", "").startswith("reviews.") else "")
        owner = self.gate_state.get(gate)
        if owner and owner not in self.path:
            return True, ""                  # the gate's (or review's) state is not on this task type's path: not applicable
        if gate in self.tpl.get("not_applicable_gates", {}) and gate not in self.tpl.get("required_gates", []) \
                and gate not in NEVER_WAIVABLE:
            return True, ""                  # the template states why this type has nothing for the gate to measure
        actual = _gate(doc, guard["gate"]) if "gate" in guard else _get(doc, guard["path"])
        label = f"gate {guard['gate']}" if "gate" in guard else guard["path"]
        for op, want in guard.items():
            if op in ("gate", "path"):
                continue
            if op == "eq":
                ok = actual == want
            elif op == "ne":
                ok = actual != want and actual is not None
            elif op == "in":
                ok = actual in want
            elif op in ("lt", "gte"):
                ok = isinstance(actual, (int, float)) and (actual < want if op == "lt" else actual >= want)
            else:
                return False, f"unknown guard operator {op}"
            if not ok:
                return False, f"{label} {op} {want!r} (actual {actual!r})"
        return True, ""


def check(manifest_path: str) -> Result:
    res = Result("manifest", inputs={"manifest": manifest_path})
    doc = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    ctx = Ctx(doc)
    try:
        import jsonschema
        for err in jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8"))).iter_errors(doc):
            res.add("MAN-001", BLOCK, f"{'/'.join(map(str, err.path)) or '<root>'}: {err.message}")
    except ImportError:
        res.inputs["schema"] = "jsonschema not installed; schema check skipped"
    res.inputs["task_type"] = doc.get("task_type") or DEFAULT_TYPE
    if not ctx.tpl:
        res.add("MAN-013", BLOCK, f"unknown task_type {doc.get('task_type')!r} (core/workflows/)")
    elif doc.get("risk_tier") in TIERS and TIERS.index(doc["risk_tier"]) < TIERS.index(ctx.tpl.get("risk_tier", "L0")):
        res.add("MAN-013", BLOCK, f"risk_tier {doc['risk_tier']} is below the {res.inputs['task_type']} template's "
                                  f"{ctx.tpl['risk_tier']} (a tier can be raised, never lowered)")
    cur = doc.get("current_state")
    if cur not in set(ctx.wf["states"]) | set(ctx.wf["special_states"]):
        res.add("MAN-002", BLOCK, f"unknown current_state {cur!r}")
    elif cur in ctx.wf["states"] and cur not in ctx.path:
        res.add("MAN-002", BLOCK, f"current_state {cur} is not on the {res.inputs['task_type']} path")
    rep = doc.get("repair", {})
    hist = rep.get("attempt_history", [])
    for key, track in (("attempt_count", "sdet"), ("dev_attempt_count", "dev")):
        n = sum(1 for h in hist if h.get("track", "sdet") == track)
        if rep.get(key, 0) != n:
            res.add("MAN-003", BLOCK, f"repair.{key}={rep.get(key, 0)} but {n} {track} attempt(s) recorded")
    if doc.get("outcome") == "SUCCESS":
        for g in doc.get("gates", []):
            if g.get("required", True) and g.get("status") not in ("PASS", "NOT_APPLICABLE"):
                res.add("MAN-004", BLOCK, f"outcome SUCCESS but gate {g.get('gate')} is {g.get('status')}")
        _required_gates(ctx, res)
    agents = agent_names()
    for rec in doc.get("approvals", []) + doc.get("exceptions", []):
        missing = [k for k in APPROVAL_FIELDS if not rec.get(k)]
        if missing:
            res.add("MAN-005", BLOCK, f"approval/exception for {rec.get('action', '?')} missing {', '.join(missing)}")
        who = str(rec.get("approver", "")).lower()
        if rec.get("approver_type") == "agent" or who in agents or who.startswith("agent:"):
            res.add("MAN-010", BLOCK, f"{rec.get('action', '?')} approved by {rec.get('approver')!r}: "
                                      "approvals come from a human, never from an agent")
    _independence(doc, res)
    _check_linked(doc, res)
    _tracker(doc, res, agents)
    _version_drift(doc, res)
    return res.finish()


def _tracker(doc: dict, res: Result, agents: set[str]) -> None:
    """The tracker block itself; whether one is needed yet is the tracker_guard in transition()."""
    t = doc.get("tracker") or {}
    key, system, waived = t.get("key"), t.get("system"), t.get("waived")
    if key and waived:
        res.add("MAN-017", BLOCK, f"tracker has both key {key} and a waiver: keep the one that is true")
    if key:
        if system not in TRACKER_KEY:
            res.add("MAN-017", BLOCK, f"tracker.key {key} without tracker.system github|jira")
        elif not TRACKER_KEY[system].match(key):
            res.add("MAN-017", BLOCK, f"tracker.key {key!r} is not a {system} key "
                                      f"({'owner/repo#N or #N' if system == 'github' else 'PROJ-123'})")
        who = str(t.get("confirmed_by", "")).lower()
        if who and (who in agents or who.startswith("agent:")):
            res.add("MAN-010", BLOCK, f"tracker {key} confirmed by {t['confirmed_by']!r}: the user confirms the link")
    approved = {a.get("action") for a in doc.get("approvals", []) if all(a.get(k) for k in APPROVAL_FIELDS)}
    if t.get("created_by_crewline") and "create_tracker_item" not in approved:
        res.add("MAN-017", BLOCK, f"tracker {key or '?'} created by Crewline without an approval create_tracker_item",
                remediation="show the draft (system, project, type, title, body) and record the user's approval first")
    title = (doc.get("pr") or {}).get("title")
    if title and key and not title_has_key(title, key):
        res.add("MAN-017", BLOCK, pr_title_problem(doc, title), remediation=f"gh pr edit <n> --title \"{key}: <summary>\"")
    if (t.get("closure") or {}).get("status") == "posted" and "tracker_comment" not in approved:
        res.add("MAN-017", BLOCK, "closure comment posted without an approval tracker_comment")
    for label, rec in (("tracker", waived), ("subtasks", t.get("subtasks_waived"))):
        if not rec:
            continue
        missing = [k for k in APPROVAL_FIELDS if not rec.get(k)]
        if missing:
            res.add("MAN-005", BLOCK, f"{label} waiver missing {', '.join(missing)}")
        who = str(rec.get("approver", "")).lower()
        if rec.get("approver_type") == "agent" or who in agents or who.startswith("agent:"):
            res.add("MAN-010", BLOCK, f"{label} waived by {rec.get('approver')!r}: only the user can decline it")
    for s in t.get("subtasks") or []:
        sk = s.get("key", "")
        if system in TRACKER_KEY and not TRACKER_KEY[system].match(sk):
            res.add("MAN-018", BLOCK, f"subtask {s.get('kind', '?')} key {sk!r} is not a {system} key like the ticket's")
        if s.get("created_by_crewline") and "create_tracker_subtasks" not in approved:
            res.add("MAN-018", BLOCK, f"subtask {sk} created by Crewline without an approval create_tracker_subtasks",
                    remediation="show the drafted subtasks and record the user's approval first")
        if (s.get("closure") or {}).get("status") == "posted" and "tracker_comment" not in approved:
            res.add("MAN-018", BLOCK, f"closure comment on subtask {sk} posted without an approval tracker_comment")


def subtask_kinds(ctx: Ctx) -> list[str]:
    return list(ctx.tpl.get("subtasks", ctx.wf.get("subtask_kinds", [])))


def _subtask_guard(ctx: Ctx, src: str | None, to_state: str, res: Result) -> None:
    """Past GITHUB_PLANNING: one subtask per kind; at DONE: every subtask closed with a comment or declined."""
    t = ctx.doc.get("tracker") or {}
    if t.get("waived") or to_state not in ctx.wf["states"]:
        return
    subs = t.get("subtasks") or []
    if "GITHUB_PLANNING" in ctx.path and ctx.idx(to_state) > ctx.idx("GITHUB_PLANNING") and not t.get("subtasks_waived"):
        missing = [k for k in subtask_kinds(ctx) if not any(s.get("kind") == k and s.get("key") for s in subs)]
        if missing:
            res.add("MAN-018", BLOCK, f"no subtask under {t.get('key') or 'the ticket'} for: {', '.join(missing)} "
                                      f"({to_state} from {src})",
                    remediation="GITHUB_PLANNING: link existing subtasks, or draft them (templates/subtask-template.md) and "
                                "create them on approval create_tracker_subtasks; or record the user's tracker.subtasks_waived")
    if src == "FINAL_REPORT" and to_state == "DONE":
        for s in subs:
            if (s.get("closure") or {}).get("status") not in ("posted", "declined"):
                res.add("MAN-018", BLOCK, f"subtask {s.get('key', '?')} ({s.get('kind', '?')}) has no closure comment: "
                                          "post it on approval tracker_comment, or record that the user declined it")


def title_has_key(title: str, key: str) -> bool:
    """PR title prefix `<KEY>: summary` (mosip/pre-registration#1089). A GitHub key may be written as `#N`."""
    m = re.fullmatch(r"(?:[\w.-]+/[\w.-]+)?#(\d+)", key.strip())
    forms = [re.escape(key.strip())] + ([rf"#{m.group(1)}"] if m else [])
    return bool(re.match(rf"\s*(?:{'|'.join(forms)})\s*:", title or ""))


def pr_title_problem(doc: dict, title: str | None) -> str:
    """'' when the title is fine (or the ticket was waived); otherwise what is wrong."""
    t = doc.get("tracker") or {}
    if t.get("waived") or not t.get("key"):
        return "" if t.get("waived") else "no tracker.key yet (INPUT_VALIDATION)"
    if not title:
        return f"no PR title: pass --title \"{t['key']}: <summary>\""
    if not title_has_key(title, t["key"]):
        return f"PR title {title[:60]!r} must start with the tracker key: \"{t['key']}: <summary>\""
    return ""


def pr_body_problem(doc: dict, body: str | None) -> str:
    """'' when the PR body links the ticket (its URL or key, e.g. `Closes #185` / `Refs INJIVER-1386`) or it was waived."""
    from .branch import key_pattern
    t = doc.get("tracker") or {}
    if t.get("waived") or not t.get("key"):
        return "" if t.get("waived") else "no tracker.key yet (INPUT_VALIDATION)"
    if body is None:
        return f"no PR body: pass --body / --body-file that links {t['key']} (templates/pr-template.md)"
    if (t.get("url") and t["url"] in body) or key_pattern(t["key"]).search(body):
        return ""
    return f"PR body does not link the ticket: add `Closes {t.get('url') or t['key']}` (or Refs)"


def waivers_without_follow_up(doc: dict) -> list[str]:
    """token_budget is a decision to keep spending, not a finding that ships: nothing to follow up."""
    out = [k.get("id", "?") for k in doc.get("known_limitations") or [] if not (k.get("follow_up") or k.get("no_follow_up"))]
    for rec in (doc.get("exceptions") or []) + [a for a in doc.get("approvals", []) if a.get("action") == "gate_exception"]:
        if rec.get("gate") != "token_budget" and not (rec.get("follow_up") or rec.get("no_follow_up")):
            out.append(f"exception on {rec.get('gate') or rec.get('action', '?')}")
    return out


def _before_final_report(ctx: Ctx, manifest_path: str, to_state: str, res: Result) -> None:
    if to_state not in ctx.wf["states"] or ctx.idx(to_state) < ctx.idx("FINAL_REPORT"):
        return
    for item in waivers_without_follow_up(ctx.doc):
        res.add("MAN-019", BLOCK, f"{item} has no follow-up: link a ticket or task (`crewline limitation add --id <id> --follow-up "
                                  "<key>`; a new ticket on approval create_tracker_item), or record no_follow_up with the reason the user accepted")
    if to_state == "FINAL_REPORT" and "IMPLEMENTATION" in ctx.path:
        from . import ci_coverage
        for f in ci_coverage.run(manifest_path).findings:
            if f.severity == BLOCK and not f.approved_by:
                res.add(f.rule_id, BLOCK, f.message, remediation=f.remediation)


def _threat_review(ctx: Ctx, manifest_path: str, to_state: str, res: Result) -> None:
    """Untrusted input: a security-engineer threat review before tests and code exist, not after the repair cap."""
    if not (ctx.doc.get("requirements") or {}).get("untrusted_input") or to_state not in ctx.wf["states"]:
        return
    first = next((s for s in ("TESTCASE_DESIGN", "IMPLEMENTATION_PLAN", "IMPLEMENTATION") if s in ctx.path), None)
    if first and ctx.idx(to_state) >= ctx.idx(first):
        from . import packets
        packets.require_threat_review(manifest_path, ctx.doc, res, to_state)


def _guard_text(g: dict) -> str:
    if "all_of" in g:
        return " and ".join(_guard_text(x) for x in g["all_of"])
    if "any_of" in g:
        return "one of (" + " | ".join(_guard_text(x) for x in g["any_of"]) + ")"
    if "approval" in g:
        return f"approval {g['approval']}"
    name = f"gate {g['gate']}" if "gate" in g else g.get("path", "?")
    return " ".join(f"{name} {op} {val!r}" for op, val in g.items() if op not in ("gate", "path"))


def next_report(manifest_path: str) -> Result:
    """`--next`: every legal move and every precondition with its current state, so a move is prepared, not tried."""
    res = Result("manifest-next", inputs={"manifest": manifest_path})
    doc = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    ctx = Ctx(doc)
    src = doc.get("current_state")
    base = check(manifest_path)
    known = {(f.rule_id, f.message) for f in base.findings}
    for f in base.findings:
        if f.severity == BLOCK and not f.approved_by:
            res.add("NXT-002", WARN, f"the manifest itself, fix first: {f.rule_id} {f.message}")
    waiting = src in ("WAITING_FOR_USER", "S_ESCALATE")
    moves = ([doc["resume_state"]] if doc.get("resume_state") else []) if waiting else ctx.next_states(src or "")
    if not waiting:
        forced = [g for g in ctx.wf.get("forced_escalation", {}).get(src, []) if ctx.holds(g)[0]]
        if forced:
            res.add("NXT-003", WARN, f"forced escalation in {src}: only S_ESCALATE is legal ({_guard_text(forced[0])})")
            moves = []
    from . import packets
    ready = []
    for to in moves:
        guards = ctx.entry_guards(to) if waiting else _guards_for(ctx, src, to)
        lines = [f"{'ok ' if ctx.holds(g)[0] else 'NOT'} {_guard_text(g)}" for g in guards]
        if ctx.wf.get("tracker_guard") and ctx.idx(to) > ctx.idx(ctx.wf.get("tracker_guard_after", "INPUT_VALIDATION")):
            lines.append(f"{'ok ' if ctx.holds(ctx.wf['tracker_guard'])[0] else 'NOT'} tracker ticket or waiver")
        need = packets.PACKET_FOR.get(to)
        if need:
            lines.append(f"{'ok ' if packets.packet_path(manifest_path, need).is_file() else 'NOT'} packets/{need}.md")
        t = transition(manifest_path, to)
        fresh = [f for f in t.findings if f.severity == BLOCK and not f.approved_by and (f.rule_id, f.message) not in known]
        lines += [f"NOT {f.rule_id}: {f.message}" for f in fresh if f.rule_id != "TRN-002" and not f.message.startswith("no tracker")]
        verdict = "READY" if not fresh else "not ready"
        if not fresh:
            ready.append(to)
        res.add("NXT-001", INFO, f"{src} -> {to}: {verdict}" + "".join(f"\n      {x}" for x in lines))
    res.add("NXT-004", INFO, "always legal: WAITING_FOR_USER (needs resume_state), S_ESCALATE; ready now: " + (", ".join(ready) or "none"))
    res.inputs["ready"] = ready
    return res.finish()


def _tracker_guard(ctx: Ctx, src: str | None, to_state: str, res: Result) -> None:
    """No design or code without a ticket: every move into a state after `tracker_guard_after` (any template)."""
    guard, after = ctx.wf.get("tracker_guard"), ctx.wf.get("tracker_guard_after", "INPUT_VALIDATION")
    if not guard or to_state not in ctx.wf["states"] or ctx.idx(to_state) <= ctx.idx(after):
        return
    ok, why = ctx.holds(guard)
    if not ok:
        res.add("MAN-017", BLOCK, f"no tracker ticket for {to_state} (from {src}): {why}",
                remediation="INPUT_VALIDATION: find the GitHub issue / Jira item (request, branch, commits, search) and "
                            "have the user confirm it; else ask GitHub or Jira, draft it, create it on approval "
                            "create_tracker_item; or record the user's waiver with a reason (tracker.waived)")


def _version_drift(doc: dict, res: Result) -> None:
    from . import version
    started, now = doc.get("crewline_version"), version.current()
    res.inputs["crewline_version"] = now
    if started and now and started != now:
        res.add("MAN-016", WARN, f"task started on Crewline {started}, running {now}: read CHANGELOG.md for "
                                 f"{started} -> {now} before the next transition; rules may have changed",
                remediation="continue under the new rules, or tell the user if a change affects this task")


def _required_gates(ctx: Ctx, res: Result, rule: str = "MAN-012") -> None:
    excepted = {e.get("gate") for e in ctx.doc.get("exceptions", []) if all(e.get(k) for k in APPROVAL_FIELDS)}
    strict = set(ctx.tpl.get("no_silent_na", []))
    for name in ctx.tpl.get("required_gates", []):
        status = _gate(ctx.doc, name)
        if status == "NOT_APPLICABLE" and name in strict and name not in excepted:
            res.add("MAN-015", BLOCK, f"gate {name} is NOT_APPLICABLE: SDET must run it, or a human records an exception "
                                      "(e.g. deferred to the post-merge run on a named environment)")
            continue
        if status in ("PASS", "NOT_APPLICABLE") or (status == "NOT_APPLICABLE" and name in excepted):
            continue
        if name in excepted and name not in NEVER_WAIVABLE and status is not None:
            continue
        res.add(rule, BLOCK, f"required gate {name} is {status or 'not recorded'} "
                             f"({ctx.doc.get('task_type') or DEFAULT_TYPE} template)")


def _defects(doc: dict, res: Result) -> None:
    """Bug raising -> bug fixing before the PR: what the change broke is fixed; what it found is raised."""
    for d in doc.get("defects", []):
        did, cls, st = d.get("id", "?"), d.get("class"), d.get("status")
        if cls == "CODE_UNDER_CHANGE" and st not in ("fixed",):
            res.add("MAN-014", BLOCK, f"defect {did} was introduced by this change and is {st}: fix it before the PR")
        elif cls in ("APPLICATION", "SECURITY") and st == "open" and not d.get("bug"):
            res.add("MAN-014", BLOCK, f"defect {did} ({cls}) found by {d.get('found_by', '?')} is open with no bug raised: "
                                      "raise it (defect-analyst, with the run report) or record why it is deferred")


def _independence(doc: dict, res: Result) -> None:
    authors = {a.lower() for a in doc.get("implementation", {}).get("authors", [])}
    reviewers = doc.get("reviews", {}).get("reviewers", {})
    for review, who in reviewers.items():
        if who and who.lower() in authors:
            res.add("MAN-011", BLOCK, f"{review} by {who}, who is an author of the change (no self-review)")
    a, b = reviewers.get("implementation_review"), reviewers.get("fresh_context_review")
    if a and b and a.lower() == b.lower():
        res.add("MAN-011", BLOCK, f"implementation and fresh-context reviews both by {a}: the second needs a new context")


def changed_repos(doc: dict) -> list[dict]:
    """Repos the task changes. Entries without a role: the first is primary, the rest are references."""
    repos = doc.get("scope", {}).get("repositories", [])
    return [r for i, r in enumerate(repos) if r.get("role", "primary" if i == 0 else "reference") in ("primary", "dependency")]


def _check_linked(doc: dict, res: Result) -> None:
    repos = changed_repos(doc)
    by_name = {r["name"]: r for r in repos}
    approvals = [a for a in doc.get("approvals", []) if a.get("action") == "base_branch"]
    for r in repos:
        if r.get("branch") and len(repos) > 1 and not any(
                r["name"] in a.get("scope", "") or (r.get("upstream_repo") or "~") in a.get("scope", "") for a in approvals):
            res.add("MAN-006", BLOCK, f"{r['name']}: no base_branch approval naming this repo (ask before creating its branch)")
        for dep in r.get("depends_on", []):
            if dep not in by_name:
                res.add("MAN-008", BLOCK, f"{r['name']} depends_on unknown repository '{dep}'")
            elif r.get("pull_request") and not by_name[dep].get("pull_request"):
                res.add("MAN-008", BLOCK, f"{r['name']} has a PR but its dependency {dep} has none: open dependency PRs first")
        if r.get("role") == "dependency" and r.get("branch") and not r.get("version_link"):
            res.add("MAN-009", BLOCK, f"{r['name']}: dependency change without version_link (how does the primary consume it?)")


def per_repo_gate_guards(doc: dict) -> list[dict]:
    """With more than one changed repo, branch_scope and dco must pass for each one (MAN-007)."""
    repos = changed_repos(doc)
    if len(repos) < 2:
        return []
    return [{"gate": f"{g}:{r['name']}", "eq": "PASS"} for r in repos for g in ("branch_scope", "dco")]


def _guards_for(ctx: Ctx, src: str, to_state: str) -> list[dict]:
    guards = ctx.entry_guards(to_state) + ctx.wf.get("transition_guards", {}).get(f"{src}->{to_state}", [])
    if ctx.idx(to_state) > ctx.idx(src):
        guards += ctx.wf.get("exit_guards", {}).get(src, {}).get("forward", [])
    return guards


def transition(manifest_path: str, to_state: str) -> Result:
    res = check(manifest_path)
    res.validator = "manifest-transition"
    doc = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    ctx = Ctx(doc)
    src = doc.get("current_state")
    res.inputs.update({"from": src, "to": to_state})

    if to_state in ctx.wf["special_states"]:
        if to_state == "WAITING_FOR_USER" and not doc.get("resume_state"):
            res.add("TRN-002", BLOCK, "WAITING_FOR_USER needs manifest.resume_state")
        return res.finish()
    if src in ("WAITING_FOR_USER", "S_ESCALATE"):
        if to_state != doc.get("resume_state"):
            res.add("TRN-001", BLOCK, f"{src} may only resume to {doc.get('resume_state')}")
        if src == "S_ESCALATE":
            ok, why = ctx.holds(ctx.wf.get("escalation_exit_guard", {"approval": "escalation_decision"}))
            if not ok:
                res.add("TRN-002", BLOCK, f"leaving S_ESCALATE needs a human decision: {why}")
        guards = ctx.entry_guards(to_state)
    else:
        allowed = ctx.next_states(src)
        if to_state not in allowed:
            res.add("TRN-001", BLOCK, f"{src} -> {to_state} is not an allowed transition for "
                                      f"{doc.get('task_type') or DEFAULT_TYPE}",
                    remediation=f"allowed: {', '.join(allowed) or 'none'}")
        guards = _guards_for(ctx, src, to_state)

    for guard in ctx.wf.get("forced_escalation", {}).get(src, []):
        ok, _ = ctx.holds(guard)
        if ok:
            res.add("TRN-003", BLOCK, f"forced escalation in {src}: {guard} - only S_ESCALATE is allowed",
                    remediation="produce templates/escalation-report.md and move to S_ESCALATE")
    _tracker_guard(ctx, src, to_state, res)
    _subtask_guard(ctx, src, to_state, res)
    _before_final_report(ctx, manifest_path, to_state, res)
    _threat_review(ctx, manifest_path, to_state, res)
    if src == "PR_CLOSEOUT" and ctx.idx(to_state) > ctx.idx(src):          # the PR as opened carries the key
        problem = pr_title_problem(doc, (doc.get("pr") or {}).get("title"))
        if problem:
            res.add("MAN-017", BLOCK, f"leaving PR_CLOSEOUT: {problem} (record pr.title as on GitHub)")
    extra = per_repo_gate_guards(doc) if to_state == "PR_CLOSEOUT" and src == "COMMIT_PR" else []
    for guard in guards + extra:
        ok, why = ctx.holds(guard)
        if not ok:
            res.add("MAN-007" if guard in extra else "TRN-002", BLOCK, f"guard not met for {to_state}: {why}")
    if to_state == "COMMIT_PR":
        _required_gates(ctx, res)
        _defects(doc, res)
    from . import packets
    packets.enforce(manifest_path, to_state, doc, res)
    return res.finish()


def apply(manifest_path: str, to_state: str, note: str = "") -> Result:
    """Check the transition and, only if it passes, record it. The single way state moves."""
    res = transition(manifest_path, to_state)
    if res.status != "PASS":
        return res
    p = Path(manifest_path)
    doc = json.loads(p.read_text(encoding="utf-8"))
    ctx = Ctx(doc)
    src = doc.get("current_state")
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc.setdefault("state_history", []).append({"from": src, "to": to_state, "note": note, "at": at})
    doc.setdefault("usage_marks", []).append({"label": f"{src}->{to_state}", "at": at, "state": to_state})
    if src == "WAITING_FOR_USER":                     # resuming: resume_state is consumed
        doc.pop("resume_state", None)
    if to_state in ctx.wf["special_states"]:
        guards = []
    elif src in ("WAITING_FOR_USER", "S_ESCALATE"):
        guards = ctx.entry_guards(to_state)
        if src == "S_ESCALATE":
            guards.append(ctx.wf.get("escalation_exit_guard", {"approval": "escalation_decision"}))
    else:
        guards = _guards_for(ctx, src, to_state)
    stamp = f"{src}->{to_state}@{at}"
    for action in {g["approval"] for g in guards if "approval" in g}:
        for a in doc.get("approvals", []):          # consume the oldest unconsumed matching approval
            if a.get("action") == action and not a.get("consumed_by"):
                a["consumed_by"] = stamp
                break
    doc["current_state"] = to_state
    p.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    res.summary += f"; applied {src} -> {to_state}"
    return res
