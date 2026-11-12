"""Full-suite regression gate: compare a full run on the base with a full run on the branch.

  REG-001  case PASSED on base and FAILS on branch (new break)                    BLOCK
  REG-002  case that is new on the branch FAILS                                   BLOCK
  REG-003  case PASSED on base and is SKIPPED on branch (a skip can hide a break) WARN
  REG-004  case present on base is missing from the branch run                    WARN
  REG-005  case FAILED on base and still fails (pre-existing, not ours)           INFO
  REG-006  case FAILED on base and passes on branch (fixed)                       INFO

  REG-009  a TestRig run (module + language) present on base has no full report on the branch BLOCK
  REG-010  --baseline-from-user: no human approver named (BLOCK), or a base report older than --max-age-days (WARN)

Reusing a base run (`--baseline-from-user --approver <who>`): when the user already has a full base run on the same
environment (e.g. the module's last released-run `testng-report/`), point --baseline at it instead of building and running
the base again. Pairing by `<env>-<module>-<lang>` refuses a base from another environment (REG-009). Only a report gives
per-test PASS->FAIL; bare totals ("945 run, 937 passed") cannot, so totals alone go through
`crewline regression-decision --waive`, not here.

Several TestRig full reports in one folder (one per language: `...-masterdata-eng-<date>-full-report...`) are paired by
the name before the date, newest per name, and every pair is compared; a single report compares as before.
With `--out`, the compared reports are copied to `<out dir>/reports/<out stem>/{base,branch}/` and the record hashes
those copies (REG-007), so the base checkout can be removed right after its run (principle 9).

`--allow-fail <uid>` covers REG-001/REG-002 for a case with an attributed quality-gate exception
(e.g. a known application defect). Both runs must be full suites (empty testCasesToExecute) on the
same environment, run one after the other. Reports: TestRig full-report HTML, JUnit/Surefire XML, Cucumber JSON (UI rig).
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from .report import case_status, find_full_report, junit_files
from .result import BLOCK, INFO, WARN, Result

RUN_KEY = re.compile(r"^(.+?)-\d{4}-\d{2}-\d{2}_\d{2}-\d{2}(?:-\d{2})?-full-report", re.I)


def full_reports(folder: str | Path) -> dict[str, Path]:
    """TestRig full reports in `folder` by run key (`<env>-<module>-<lang>`), newest per key; {} for anything else."""
    p = Path(folder)
    if not p.is_dir():
        return {}
    out: dict[str, Path] = {}
    for f in sorted(p.glob("*full-report*.html")):           # the timestamp sorts: the last one per key is the newest
        m = RUN_KEY.match(f.name)
        if m:
            out[m.group(1)] = f
    return out


def pairs(baseline: str, candidate: str) -> list[tuple[str, Path | None, Path | None]]:
    """(key, base report, branch report) when either side holds more than one keyed TestRig run, else []."""
    base, cand = full_reports(baseline), full_reports(candidate)
    if len(base) < 2 and len(cand) < 2:
        return []
    return [(k, base.get(k), cand.get(k)) for k in sorted(set(base) | set(cand))]


REPORT_DATE = re.compile(r"-(\d{4}-\d{2}-\d{2})_\d{2}-\d{2}")


def user_baseline(res: Result, approver: str | None, max_age_days: float = 14) -> None:
    """Record that the base report came from the user, and check it is attributed and recent."""
    from datetime import datetime, timezone
    from .records import _is_agent
    res.inputs["baseline_source"] = "user"
    if not approver or _is_agent(approver):
        res.add("REG-010", BLOCK, "a reused base report needs the person who supplied it (--approver <who>, not an agent)")
        return
    res.inputs["baseline_approver"] = approver
    now = datetime.now(timezone.utc).date()
    for f in res.inputs.get("baseline_files") or []:
        m = REPORT_DATE.search(Path(f).name)
        when = (datetime.strptime(m.group(1), "%Y-%m-%d").date() if m
                else datetime.fromtimestamp(Path(f).stat().st_mtime, timezone.utc).date())
        age = (now - when).days
        if age > max_age_days:
            res.add("REG-010", WARN, f"base report {Path(f).name} is {age} days old (> {max_age_days:g}): the environment "
                    "or test data may have moved since; a fresh base run is safer", evidence=Path(f).name)


def compared_files(path: str | Path) -> list[Path]:
    """The files a single comparison read: the full report, the cucumber.json, or the JUnit XMLs."""
    report = find_full_report(path)
    return junit_files(report) if report.is_dir() else [report]


def snapshot(res: Result, out: str | Path) -> tuple[Path, Path]:
    """Copy what `res` compared to <out dir>/reports/<out stem>/{base,branch}/ and record the copies."""
    root = Path(out).resolve().parent / "reports" / Path(out).stem
    dirs = {}
    for side, key in (("base", "baseline"), ("branch", "candidate")):
        dest = root / side
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        files = res.inputs.get(f"{key}_files") or []
        for f in files:
            shutil.copy2(f, dest / Path(f).name)
        res.inputs[f"{key}_copy"] = str(dest)
        dirs[side] = dest
    return dirs["base"], dirs["branch"]


def run(baseline: str, candidate: str, allow_fail: list[str] | dict[str, str] | None = None) -> Result:
    """`allow_fail`: ids whose break is covered (a list: by a quality-gate exception; a dict: id -> who approved)."""
    res = Result("full-regression", inputs={"baseline": str(Path(baseline).resolve()), "candidate": str(Path(candidate).resolve())})
    allow = ({k.lower(): v for k, v in allow_fail.items()} if isinstance(allow_fail, dict)
             else {a.lower(): "quality-gate exception" for a in (allow_fail or [])})
    paired = pairs(baseline, candidate)
    if paired:
        res.inputs["pairs"] = []
        res.inputs["baseline_files"], res.inputs["candidate_files"] = [], []
        for key, b, c in paired:
            if c is None:
                res.add("REG-009", BLOCK, f"{key}: run on base ({b.name}) has no full report on the branch",
                        evidence=key, remediation="run that module/language on the branch too, or remove the base report")
                continue
            if b is None:
                res.add("REG-004", WARN, f"{key}: branch run has no base run to compare with", evidence=key)
                continue
            try:
                base, cand = case_status(b), case_status(c)
            except (OSError, ValueError) as e:
                return Result.not_run("full-regression", f"{key}: {e}")
            if not base or not cand:
                return Result.not_run("full-regression", f"{key}: empty run: baseline {len(base)} / candidate {len(cand)} cases")
            res.inputs["pairs"].append({"key": key, "baseline_report": b.name, "candidate_report": c.name})
            res.inputs["baseline_files"].append(str(b))
            res.inputs["candidate_files"].append(str(c))
            _compare(res, base, cand, allow, f"[{key}] ")
        if not res.inputs["pairs"] and not res.findings:
            return Result.not_run("full-regression", "no base/branch report pair to compare")
        return res.finish()
    try:
        base, cand = case_status(baseline), case_status(candidate)
        res.inputs["baseline_report"] = find_full_report(baseline).name
        res.inputs["candidate_report"] = find_full_report(candidate).name
        res.inputs["baseline_files"] = [str(f) for f in compared_files(baseline)]
        res.inputs["candidate_files"] = [str(f) for f in compared_files(candidate)]
    except (OSError, ValueError) as e:
        return Result.not_run("full-regression", str(e))
    if not base or not cand:
        return Result.not_run("full-regression", f"empty run: baseline {len(base)} / candidate {len(cand)} cases")
    _compare(res, base, cand, allow)
    return res.finish()


def _compare(res: Result, base: dict, cand: dict, allow: dict[str, str], tag: str = "") -> None:
    for uid, now in sorted(cand.items()):
        was = base.get(uid)
        covered = uid.lower() in allow
        if was == "PASS" and now == "FAIL":
            f = res.add("REG-001", BLOCK, f"{tag}{uid}: PASS on base, FAIL on branch (new break)", evidence=uid)
        elif was is None and now == "FAIL":
            f = res.add("REG-002", BLOCK, f"{tag}{uid}: new case fails", evidence=uid)
        elif was == "PASS" and now == "SKIP":
            res.add("REG-003", WARN, f"{tag}{uid}: PASS on base, SKIPPED on branch", evidence=uid)
            continue
        elif was == "FAIL" and now == "FAIL":
            res.add("REG-005", INFO, f"{tag}{uid}: fails on base too (pre-existing)", evidence=uid)
            continue
        elif was == "FAIL" and now == "PASS":
            res.add("REG-006", INFO, f"{tag}{uid}: fixed (FAIL on base, PASS on branch)", evidence=uid)
            continue
        else:
            continue
        if covered:
            f.approved_by = allow[uid.lower()]
    for uid in sorted(set(base) - set(cand)):
        res.add("REG-004", WARN, f"{tag}{uid}: on base ({base[uid]}) but missing from the branch run", evidence=uid)

    for side, d in (("baseline_counts", base), ("candidate_counts", cand)):
        counts = res.inputs.setdefault(side, {"PASS": 0, "FAIL": 0, "SKIP": 0})
        for s in counts:
            counts[s] += sum(1 for v in d.values() if v == s)
