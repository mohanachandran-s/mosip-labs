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

from .result import BLOCK, WARN, Result

CORE = Path(__file__).resolve().parents[2]
WORKFLOW = CORE / "core" / "workflow.yaml"
TEMPLATES = CORE / "core" / "workflows"
GATES = CORE / "core" / "quality-gates.yaml"
SCHEMA = CORE / "templates" / "task-manifest.schema.json"
APPROVAL_FIELDS = ("approver", "reason", "scope", "timestamp")
TIERS = ["L0", "L1", "L2", "L3"]
DEFAULT_TYPE = "test-automation"     # manifests from the SDET assistant era carry no task_type
NEVER_WAIVABLE = {"secrets", "requirement_clarity", "requirement_fit"}


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
    _version_drift(doc, res)
    return res.finish()


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
