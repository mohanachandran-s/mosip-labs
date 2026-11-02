"""Full-suite regression gate: compare a full run on the base with a full run on the branch.

  REG-001  case PASSED on base and FAILS on branch (new break)                    BLOCK
  REG-002  case that is new on the branch FAILS                                   BLOCK
  REG-003  case PASSED on base and is SKIPPED on branch (a skip can hide a break) WARN
  REG-004  case present on base is missing from the branch run                    WARN
  REG-005  case FAILED on base and still fails (pre-existing, not ours)           INFO
  REG-006  case FAILED on base and passes on branch (fixed)                       INFO

`--allow-fail <uid>` covers REG-001/REG-002 for a case with an attributed quality-gate exception
(e.g. a known application defect). Both runs must be full suites (empty testCasesToExecute) on the
same environment, run one after the other. Reports: TestRig full-report HTML, JUnit/Surefire XML, Cucumber JSON (UI rig).
"""
from __future__ import annotations

from pathlib import Path

from .report import case_status, find_full_report
from .result import BLOCK, INFO, WARN, Result


def run(baseline: str, candidate: str, allow_fail: list[str] | None = None) -> Result:
    res = Result("full-regression", inputs={"baseline": str(Path(baseline).resolve()), "candidate": str(Path(candidate).resolve())})
    allow = {a.lower() for a in (allow_fail or [])}
    try:
        base, cand = case_status(baseline), case_status(candidate)
        res.inputs["baseline_report"] = find_full_report(baseline).name
        res.inputs["candidate_report"] = find_full_report(candidate).name
    except (OSError, ValueError) as e:
        return Result.not_run("full-regression", str(e))
    if not base or not cand:
        return Result.not_run("full-regression", f"empty run: baseline {len(base)} / candidate {len(cand)} cases")

    for uid, now in sorted(cand.items()):
        was = base.get(uid)
        covered = uid.lower() in allow
        if was == "PASS" and now == "FAIL":
            f = res.add("REG-001", BLOCK, f"{uid}: PASS on base, FAIL on branch (new break)", evidence=uid)
        elif was is None and now == "FAIL":
            f = res.add("REG-002", BLOCK, f"{uid}: new case fails", evidence=uid)
        elif was == "PASS" and now == "SKIP":
            res.add("REG-003", WARN, f"{uid}: PASS on base, SKIPPED on branch", evidence=uid)
            continue
        elif was == "FAIL" and now == "FAIL":
            res.add("REG-005", INFO, f"{uid}: fails on base too (pre-existing)", evidence=uid)
            continue
        elif was == "FAIL" and now == "PASS":
            res.add("REG-006", INFO, f"{uid}: fixed (FAIL on base, PASS on branch)", evidence=uid)
            continue
        else:
            continue
        if covered:
            f.approved_by = "quality-gate exception"
    for uid in sorted(set(base) - set(cand)):
        res.add("REG-004", WARN, f"{uid}: on base ({base[uid]}) but missing from the branch run", evidence=uid)

    count = lambda d, s: sum(1 for v in d.values() if v == s)
    res.inputs["baseline_counts"] = {s: count(base, s) for s in ("PASS", "FAIL", "SKIP")}
    res.inputs["candidate_counts"] = {s: count(cand, s) for s in ("PASS", "FAIL", "SKIP")}
    return res.finish()
