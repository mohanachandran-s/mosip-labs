"""Specialist packets are the hand-off. A missing packet is a chat dump waiting to happen.

  Called from `manifest --to`. Not a standalone CLI.

  PKT-001  required packets/<STATE>.md missing or empty before the next specialist
  PKT-002  packet looks like a core/report dump (same bar as USE-008)
  PKT-003  FRESH_CONTEXT_REVIEW -> next: both reviewer context ids must be set and different
  REP-001  bug-fix IMPLEMENTATION_PLAN: evidence/reproduction.json missing or unreadable
  REP-002  reproduction.json exit_code is 0 (the base run must FAIL)
  DEPSEC-001  feature/dependency-upgrade review: evidence/dependency-security.json missing
  DEPSEC-002  dependency-security record exit_code is not 0 (no attributed exception)
  SYS-001  leaving SYSTEM_TEST_EXECUTION: no evidence/system-tests[-<suite>].json, or one is not a crewline run record
  SYS-002  no system-test record ran (cwd) inside a system-test suite the module declares (.crewline/module.yaml)
  SYS-003  the change touches a UI project (stack.node_projects) and no record ran inside a UI test project
  SYS-004  (INFO) no task repository declares system-test projects; the suite is not checked
  SYS-005  (WARN) a system-test record has a non-zero exit code (triage into defects before the PR)
  REG-007  code task QUALITY_GATE -> COMMIT_PR: no passing evidence/full-regression*.json (`crewline regression --out`),
           or one without report hashes / commit, comparing a report with itself, or run at another commit
  REG-008  no passing full-regression record whose candidate report is inside a declared system-test suite
  PKT-004  code task leaving a specialist state: packets/<STATE>.md does not name a Crewline specialist (agent, context_id)

What stays honour-system: a record with every field could still be typed by hand, and a packet can name an agent that
did not run. These rules make a skipped suite or state explicit and visible in review; they do not prove a run happened.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import systest
from .result import BLOCK, INFO, WARN, Result
from .usage import DUMP_RX, load_budget

DEPSEC_TYPES = {"feature", "dependency-upgrade"}
# states whose specialist writes packets/<STATE>.md on the way out (code task types)
SPECIALIST_STATES = ("REQUIREMENT_ANALYSIS", "ACCEPTANCE_REVIEW", "IMPACT_ANALYSIS", "TESTCASE_DESIGN", "TESTCASE_REVIEW")
APPROVAL_FIELDS = ("approver", "reason", "scope", "timestamp")

# to_state -> packet that must already be on disk (written by the specialist we are leaving)
PACKET_FOR = {
    "IMPLEMENTATION_REVIEW": "IMPLEMENTATION",
    "FRESH_CONTEXT_REVIEW": "FRESH_CONTEXT_REVIEW",  # source-only packet for review 2
    "SYSTEM_TEST_EXECUTION": "IMPLEMENTATION",
}


def packet_path(manifest_path: str, state: str) -> Path:
    return Path(manifest_path).resolve().parent / "packets" / f"{state}.md"


def _require_packet(manifest_path: str, need: str, doc: dict, res: Result, *, why: str) -> None:
    path = packet_path(manifest_path, need)
    if not path.is_file() or path.stat().st_size == 0:
        res.add("PKT-001", BLOCK, f"{why} needs packets/{need}.md (ACs, paths, FAIL findings — not the chat)",
                path=str(path), remediation=f"write the packet under {path.parent}")
        return
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        res.add("PKT-001", BLOCK, f"cannot read packets/{need}.md: {e}", path=str(path))
        return
    cap = load_budget(doc).get("packet_max_chars", 20000)
    if len(text) > cap:
        res.add("PKT-001", BLOCK, f"packets/{need}.md is {len(text)} chars (max {cap})", path=str(path))
    if DUMP_RX.search(text) or (len(text) > 8000 and "principles.md" in text):
        res.add("PKT-002", BLOCK, f"packets/{need}.md looks like a core/report dump", path=str(path))


def _require_reproduction(manifest_path: str, res: Result) -> None:
    path = Path(manifest_path).resolve().parent / "evidence" / "reproduction.json"
    if not path.is_file() or path.stat().st_size == 0:
        res.add("REP-001", BLOCK,
                "IMPLEMENTATION_PLAN (bug-fix) needs evidence/reproduction.json "
                "(crewline run of the failing test on the base; exit_code != 0)",
                path=str(path), remediation="run the new test on the base via crewline run; keep the failing record")
        return
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        res.add("REP-001", BLOCK, f"cannot read evidence/reproduction.json: {e}", path=str(path))
        return
    if not rec.get("command"):
        res.add("REP-001", BLOCK, "reproduction.json missing command", path=str(path))
        return
    if rec.get("exit_code") in (None, 0, "0"):
        res.add("REP-002", BLOCK, "reproduction.json exit_code is 0 (the base run must FAIL)", path=str(path))


def _has_exception(doc: dict, gate: str) -> bool:
    return any(e.get("gate") == gate and all(e.get(k) for k in APPROVAL_FIELDS)
               for e in doc.get("exceptions") or [])


def _require_depsec(manifest_path: str, doc: dict, res: Result) -> None:
    if (doc.get("task_type") or "") not in DEPSEC_TYPES:
        return
    if _has_exception(doc, "dependency_security"):
        return
    path = Path(manifest_path).resolve().parent / "evidence" / "dependency-security.json"
    if not path.is_file() or path.stat().st_size == 0:
        res.add("DEPSEC-001", BLOCK,
                "IMPLEMENTATION_REVIEW needs evidence/dependency-security.json "
                "(crewline run of the module-named scanner; do not invent one)",
                path=str(path), remediation="record the OWASP/CodeQL/module scanner via crewline run")
        return
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        res.add("DEPSEC-001", BLOCK, f"cannot read evidence/dependency-security.json: {e}", path=str(path))
        return
    if not rec.get("command"):
        res.add("DEPSEC-001", BLOCK, "dependency-security.json missing command", path=str(path))
        return
    if rec.get("exit_code") not in (0, "0"):
        res.add("DEPSEC-002", BLOCK, "dependency-security.json exit_code is not 0 (scanner must pass, or record an exception)",
                path=str(path))


def _require_system_tests(manifest_path: str, doc: dict, res: Result) -> None:
    if _has_exception(doc, "system_tests"):
        return
    folder = Path(manifest_path).resolve().parent / "evidence"
    files = sorted(folder.glob("system-tests*.json")) if folder.is_dir() else []
    if not files:
        res.add("SYS-001", BLOCK,
                "leaving SYSTEM_TEST_EXECUTION needs evidence/system-tests[-<suite>].json "
                "(crewline run on the branch build; stamp is not a run)", path=str(folder),
                remediation="python -m crewline run --repo <module>/<suite> --out <task>/evidence/system-tests-<suite>.json -- <suite>")
        return
    records = []
    for f in files:
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            res.add("SYS-001", BLOCK, f"cannot read evidence/{f.name}: {e}", path=str(f))
            continue
        missing = systest.missing_run_fields(rec) if isinstance(rec, dict) else list(systest.RUN_FIELDS)
        if missing:
            res.add("SYS-001", BLOCK, f"evidence/{f.name} is not a crewline run record (missing {', '.join(missing)})",
                    path=str(f), remediation="record the suite with crewline run; do not write the file by hand")
            continue
        if rec.get("exit_code") not in (0, "0"):
            res.add("SYS-005", WARN, f"evidence/{f.name}: exit_code {rec.get('exit_code')}; every failure needs a "
                    "triaged defects entry (MAN-014) before the PR", path=str(f))
        records.append(rec)
    if not records:
        return
    stack = systest.module_stack(doc)
    if not stack["system"]:
        res.add("SYS-004", INFO, "no task repository declares system-test projects in .crewline/module.yaml; "
                "suite not checked", path=str(folder))
        return
    if not any(systest.ran_in(r, root, p) for r in records for root, p in stack["system"]):
        names = ", ".join(sorted({p for _, p in stack["system"]}))
        res.add("SYS-002", BLOCK, f"no system-test record ran inside a system-test suite of the module ({names}); "
                "a unit/jest run or a hand-made check is not a system test", path=str(folder),
                remediation="crewline run --repo <module>/<suite> --out <task>/evidence/system-tests-<suite>.json -- <suite command>")
        return
    approved = (doc.get("scope") or {}).get("approved_paths") or []
    ui_change = [p for _, p in stack["node"] if systest.touches(approved, p)]
    if stack["ui"] and ui_change and not any(systest.ran_in(r, root, p) for r in records for root, p in stack["ui"]):
        res.add("SYS-003", BLOCK, f"the change touches {', '.join(ui_change)} but no record ran inside a UI test project "
                f"({', '.join(sorted({p for _, p in stack['ui']}))})", path=str(folder),
                remediation="crewline run --repo <module>/<ui suite> --out <task>/evidence/system-tests-ui.json -- <UI suite>")


def _records(folder: Path, pattern: str) -> list[dict]:
    out = []
    for p in sorted(folder.glob(pattern)):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def _require_full_regression(manifest_path: str, doc: dict, res: Result) -> None:
    if (doc.get("task_type") or "") not in systest.CODE_TYPES or _has_exception(doc, "full_regression"):
        return
    folder = Path(manifest_path).resolve().parent / "evidence"
    records = [r for r in _records(folder, "full-regression*.json")
               if isinstance(r, dict) and r.get("validator") == "full-regression"] if folder.is_dir() else []
    if not records:
        res.add("REG-007", BLOCK, "COMMIT_PR needs evidence/full-regression*.json from `crewline regression --out` "
                "(base vs branch); the gate stamp is not a run", path=str(folder),
                remediation="crewline regression --baseline <base report> --candidate <branch report> "
                            "--out <task>/evidence/full-regression[-<suite>].json")
        return
    good = []
    for r in records:
        inp = r.get("inputs") or {}
        cand = inp.get("candidate", "?")
        if r.get("status") != "PASS":
            res.add("REG-007", BLOCK, f"full-regression record is {r.get('status')}: {cand}", path=str(folder))
        elif systest.stale_reason(inp):
            res.add("REG-007", BLOCK, f"full-regression record for {cand} {systest.stale_reason(inp)}; "
                    "rerun `crewline regression --out` on the current branch", path=str(folder))
        else:
            good.append(r)
    stack = systest.module_stack(doc)
    if stack["system"] and not any(systest.compared(r, root, p) for r in good for root, p in stack["system"]):
        names = ", ".join(sorted({p for _, p in stack["system"]}))
        res.add("REG-008", BLOCK, f"no passing full-regression record compares a system-test suite of the module "
                f"({names}); unit-suite regression alone is not full_regression", path=str(folder))


def _known_agents() -> set[str]:
    root = Path(__file__).resolve().parents[2] / "agents"
    return {p.stem.lower() for p in root.rglob("*.md")} - {"orchestrator"}


def _require_specialist_packet(manifest_path: str, state: str, doc: dict, res: Result) -> None:
    if (doc.get("task_type") or "") not in systest.CODE_TYPES:
        return
    before = len(res.findings)
    _require_packet(manifest_path, state, doc, res, why=f"leaving {state}")
    if len(res.findings) > before:
        return
    text = packet_path(manifest_path, state).read_text(encoding="utf-8", errors="replace")
    agent = re.search(r"(?im)^\s*agent:\s*(\S+)", text)
    ctx = re.search(r"(?im)^\s*context_id:\s*(\S.*)$", text)
    # crewline-<agent>, crewline:<role>:<agent> or the bare contract name
    name = re.sub(r"^(crewline[-:])?([\w-]+:)?", "", agent.group(1).strip().lower()) if agent else ""
    known = _known_agents()                     # empty only when validators run without the core's agents/
    if not ctx or name in ("", "orchestrator") or (known and name not in known):
        res.add("PKT-004", BLOCK, f"packets/{state}.md must name the specialist that did {state}: `agent:` one of "
                "the Crewline agents (not the orchestrator) and `context_id:`", path=str(packet_path(manifest_path, state)),
                remediation=f"delegate {state} to its agent; it writes the packet")


def require_threat_review(manifest_path: str, doc: dict, res: Result, to_state: str) -> None:
    """PKT-005: `packets/THREAT_REVIEW.md` by the security-engineer, before tests and code for untrusted input."""
    if _has_exception(doc, "threat_review"):
        return
    path = packet_path(manifest_path, "THREAT_REVIEW")
    text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    agent = re.search(r"(?im)^\s*agent:\s*(\S+)", text)
    name = re.sub(r"^(crewline[-:])?([\w-]+:)?", "", agent.group(1).strip().lower()) if agent else ""
    if name != "security-engineer" or not re.search(r"(?im)^\s*context_id:\s*\S", text):
        res.add("PKT-005", BLOCK, f"{to_state}: the change parses untrusted input (requirements.untrusted_input): "
                "packets/THREAT_REVIEW.md by the security-engineer (`agent:`, `context_id:`) comes before test design",
                path=str(path), remediation="delegate the threat review (agents/security/security-engineer.md); limits it finds become ACs")


def enforce(manifest_path: str, to_state: str, doc: dict, res: Result) -> None:
    src = doc.get("current_state")
    if src == "DESIGN":
        _require_packet(manifest_path, "DESIGN", doc, res, why="leaving DESIGN")
    if src in SPECIALIST_STATES:
        _require_specialist_packet(manifest_path, src, doc, res)
    if src == "SYSTEM_TEST_EXECUTION":
        _require_system_tests(manifest_path, doc, res)
    if src == "QUALITY_GATE" and to_state == "COMMIT_PR":
        _require_full_regression(manifest_path, doc, res)
    if to_state == "IMPLEMENTATION_PLAN" and (doc.get("task_type") or "") == "bug-fix":
        _require_reproduction(manifest_path, res)
    if to_state == "IMPLEMENTATION_REVIEW":
        _require_depsec(manifest_path, doc, res)
    if to_state == "SYSTEM_TEST_EXECUTION" and src != "FRESH_CONTEXT_REVIEW":
        return  # environment retry / illegal edge: packet already consumed or transition will fail anyway
    need = PACKET_FOR.get(to_state)
    if not need:
        return
    _require_packet(manifest_path, need, doc, res, why=to_state)
    if to_state == "SYSTEM_TEST_EXECUTION":
        reviewers = (doc.get("reviews") or {}).get("reviewers") or {}
        a, b = reviewers.get("implementation_review"), reviewers.get("fresh_context_review")
        if not a or not b:
            res.add("PKT-003", BLOCK, "SYSTEM_TEST_EXECUTION needs reviews.reviewers for both reviews (different context ids)")
        elif a.lower() == b.lower():
            res.add("PKT-003", BLOCK, "fresh-context reviewer context_id matches review 1 (MAN-011 / new chat)")
