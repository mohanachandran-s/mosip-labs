"""Specialist packets are the hand-off. A missing packet is a chat dump waiting to happen.

  Called from `manifest --to`. Not a standalone CLI.

  PKT-001  required packets/<STATE>.md missing or empty before the next specialist
  PKT-002  packet looks like a core/report dump (same bar as USE-008)
  PKT-003  FRESH_CONTEXT_REVIEW -> next: both reviewer context ids must be set and different
  REP-001  bug-fix IMPLEMENTATION_PLAN: evidence/reproduction.json missing or unreadable
  REP-002  reproduction.json exit_code is 0 (the base run must FAIL)
  DEPSEC-001  feature/dependency-upgrade review: evidence/dependency-security.json missing
  DEPSEC-002  dependency-security record exit_code is not 0 (no attributed exception)
  SYS-001  leaving SYSTEM_TEST_EXECUTION: evidence/system-tests.json missing or unreadable
"""
from __future__ import annotations

import json
from pathlib import Path

from .result import BLOCK, Result
from .usage import DUMP_RX, load_budget

DEPSEC_TYPES = {"feature", "dependency-upgrade"}
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
    path = Path(manifest_path).resolve().parent / "evidence" / "system-tests.json"
    if not path.is_file() or path.stat().st_size == 0:
        res.add("SYS-001", BLOCK,
                "leaving SYSTEM_TEST_EXECUTION needs evidence/system-tests.json "
                "(crewline run on the branch build; stamp is not a run)",
                path=str(path), remediation="python -m crewline run --repo <dir> --out <task>/evidence/system-tests.json -- <suite>")
        return
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        res.add("SYS-001", BLOCK, f"cannot read evidence/system-tests.json: {e}", path=str(path))
        return
    if not rec.get("command") or rec.get("exit_code") in (None, ""):
        res.add("SYS-001", BLOCK, "system-tests.json needs command and exit_code", path=str(path))


def enforce(manifest_path: str, to_state: str, doc: dict, res: Result) -> None:
    src = doc.get("current_state")
    if src == "DESIGN":
        _require_packet(manifest_path, "DESIGN", doc, res, why="leaving DESIGN")
    if src == "SYSTEM_TEST_EXECUTION":
        _require_system_tests(manifest_path, doc, res)
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
