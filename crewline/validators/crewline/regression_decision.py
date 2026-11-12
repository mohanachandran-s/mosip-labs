"""The human decisions around the full regression: one approval per decision, never one per failing test.

  crewline regression-decision --manifest <m> --waive --approver <who> --reason <why> [--repo <dir> --base <ref>]
      Skip the full regression. With --repo/--base the diff is checked: a change with no code (docs, coverage matrix,
      markdown, images) is recorded as `no_code_change`; anything else as `user_waiver`. Either way it is an attributed
      full_regression exception, so REG-007 and MAN-012 accept the gate without a run.
  crewline regression-decision --manifest <m> --fix --approver <who> --reason <why>
      The user chose to fix the new breaks: records approval `regression_fix`, which the move into repair
      (QUALITY_GATE -> IMPLEMENTATION, SYSTEM_TEST_EXECUTION -> FAILURE_TRIAGE) needs while full_regression is FAIL (TRN-006).
  crewline regression-decision --manifest <m> --defer --bug <KEY> --record <evidence/full-regression.json>
                               --approver <who> --reason <why> [--class APPLICATION|ENVIRONMENT|TEST_DATA|AUTOMATION]
      The user chose to raise a bug and not fix now: every unapproved new break (REG-001/REG-002) in the record is
      covered by ONE exception that names the bug, and a raised defect is added. Re-run
      `crewline regression ... --manifest <m> --out <same record>` and the deferred cases are APPROVED.

  RGD-001  approver missing, an agent, or reason missing                                         BLOCK
  RGD-002  --defer: the record has no unapproved new break, cannot be read, or the bug key does not fit   BLOCK
  RGD-003  --defer --class CODE_UNDER_CHANGE: what the change broke is fixed, not deferred (MAN-014)      BLOCK
  RGD-004  --waive --repo/--base: the diff changes code, so it is recorded as user_waiver, not no_code_change  INFO
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .manifest import TRACKER_KEY
from .result import BLOCK, INFO, Result

DEFERRED_ACTION = "regression_deferred"
FIX_APPROVAL = "regression_fix"
NON_CODE = re.compile(
    r"(\.(md|markdown|txt|csv|adoc|rst|png|jpe?g|gif|svg|pdf|xlsx?)$)|(^|/)(docs?|coverage)/|"
    r"(^|/)(LICENSE|NOTICE|CODEOWNERS|\.gitignore|\.gitattributes)$", re.I)
DEFER_CLASSES = ("APPLICATION", "ENVIRONMENT", "TEST_DATA", "AUTOMATION")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def code_files(repo: str, base: str) -> list[str]:
    from .gitio import changed_files
    return sorted(p for p, kind in changed_files(repo, base).items() if not NON_CODE.search(p.replace("\\", "/")))


def deferred_tests(doc: dict) -> dict[str, str]:
    """test id (lower case) -> 'approver (bug)' for every attributed regression deferral in the manifest."""
    out = {}
    for e in doc.get("exceptions") or []:
        if e.get("action") == DEFERRED_ACTION and all(e.get(k) for k in ("approver", "reason", "scope", "timestamp")):
            for t in e.get("test_ids") or []:
                out[str(t).lower()] = f"{e['approver']} ({e.get('bug', 'bug')})"
    return out


def _bug_fits(key: str) -> bool:
    return any(rx.match(key) for rx in TRACKER_KEY.values())


def run(manifest_path: str, mode: str, approver: str | None, reason: str | None, repo: str | None = None,
        base: str | None = None, bug: str | None = None, record: str | None = None, cls: str = "APPLICATION",
        follow_up: str | None = None) -> Result:
    from . import records
    res = Result("regression-decision", inputs={"manifest": manifest_path, "mode": mode})
    if not approver or not reason or records._is_agent(approver):
        res.add("RGD-001", BLOCK, "name the person who decided (--approver) and why (--reason); agents cannot approve",
                remediation="ask the user once, then record their answer")
        return res.finish()
    if mode == "fix":
        inner = records.approval_add(manifest_path, FIX_APPROVAL, approver, reason, "fix the new regression breaks")
        res.findings += inner.findings
        res.summary = inner.summary
        return res.finish()
    if mode == "waive":
        kind, changed = "user_waiver", None
        if repo and base:
            try:
                changed = code_files(repo, base)
            except RuntimeError as e:
                return Result.not_run("regression-decision", str(e))
            if not changed:
                kind = "no_code_change"
            else:
                res.add("RGD-004", INFO, f"recorded as user_waiver: the diff changes code ({', '.join(changed[:5])}"
                        f"{' ...' if len(changed) > 5 else ''})")
        scope = "full regression not run: " + ("the change has no code" if kind == "no_code_change" else "user waiver")
        doc = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        rec = {"action": "full_regression_waiver", "gate": "full_regression", "kind": kind, "approver": approver,
               "approver_type": "human", "reason": reason, "scope": scope, "timestamp": _now()}
        rec["follow_up" if follow_up else "no_follow_up"] = follow_up or reason
        doc.setdefault("exceptions", []).append(rec)
        Path(manifest_path).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        res.inputs["kind"] = kind
        res.summary = f"full_regression waived ({kind}) by {approver}"
        return res.finish()
    if mode == "defer":
        if cls == "CODE_UNDER_CHANGE":
            res.add("RGD-003", BLOCK, "a break this change caused is fixed before the PR, not deferred (MAN-014)",
                    remediation="choose --fix, or classify it (APPLICATION / ENVIRONMENT / TEST_DATA / AUTOMATION) "
                                "when the evidence shows it is not this change")
            return res.finish()
        if cls not in DEFER_CLASSES:
            res.add("RGD-002", BLOCK, f"--class {cls!r}: one of {', '.join(DEFER_CLASSES)}")
            return res.finish()
        if not bug or not _bug_fits(bug):
            res.add("RGD-002", BLOCK, f"--bug {bug!r} is not a Jira key or GitHub issue (raise it first, then defer)")
            return res.finish()
        try:
            rec_doc = json.loads(Path(record or "").read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            res.add("RGD-002", BLOCK, f"cannot read the full-regression record: {e}")
            return res.finish()
        breaks = sorted({f.get("evidence") for f in rec_doc.get("findings") or []
                         if f.get("rule_id") in ("REG-001", "REG-002") and not f.get("approved_by") and f.get("evidence")})
        if not breaks:
            res.add("RGD-002", BLOCK, "the record has no unapproved new break to defer")
            return res.finish()
        doc = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        doc.setdefault("exceptions", []).append({
            "action": DEFERRED_ACTION, "gate": "full_regression", "approver": approver, "approver_type": "human",
            "reason": reason, "scope": f"{len(breaks)} new regression break(s) deferred to {bug}", "timestamp": _now(),
            "bug": bug, "test_ids": breaks, "follow_up": bug})
        doc.setdefault("defects", []).append({
            "id": f"REGRESSION-{bug}", "title": f"{len(breaks)} full-regression break(s): {', '.join(breaks[:3])}"
            + (" ..." if len(breaks) > 3 else ""), "class": cls, "status": "raised", "bug": bug, "reason": reason,
            "found_by": "full_regression"})
        Path(manifest_path).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        res.inputs.update(bug=bug, test_ids=breaks)
        res.summary = (f"{len(breaks)} break(s) deferred to {bug} by {approver} (one approval). Re-run "
                       f"`crewline regression ... --manifest {manifest_path} --out {record}` to re-score")
        return res.finish()
    return Result.not_run("regression-decision", "pass one of --waive, --fix, --defer")
