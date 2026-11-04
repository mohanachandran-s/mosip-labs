"""Diff coverage: are the production lines this change added or modified actually executed by the tests?

  python -m crewline diff-coverage --repo <dir> --base <ref> --jacoco <jacoco.xml | lcov.info | dir> [--threshold 80] [--include <glob>]
  Default: every changed source file that is not a test (Java, Kotlin, Python, JS/TS, Go). --include narrows it.
  Python: coverage.xml from `pytest --cov --cov-report=xml`. JS/TS: coverage/lcov.info (Jest/Vitest).

Reads JaCoCo XML reports (`target/site/jacoco/jacoco.xml`) and lcov reports (`coverage/lcov.info`), searched recursively
under a directory, and the change
(working tree vs base). A changed line counts only if JaCoCo reports it as executable (it has instructions);
it is covered when at least one instruction ran (ci > 0).

  DCV-001  diff coverage below the threshold                                 BLOCK
  DCV-002  a changed source file has executable lines but is not in any report (not instrumented / tests not run)  BLOCK
  DCV-003  uncovered changed lines (listed per file)                         INFO
  DCV-004  report older than the newest changed source file (stale run)      BLOCK
  DCV-005  nothing measured: 0 lines is never a silent pass. --include missed the changed source BLOCK; no source changed or
           no changed line is executable WARN

Untested changed lines are the usual hiding place for a fix that only works on the happy path.
"""
from __future__ import annotations

import fnmatch
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .coding_standards import is_test_path
from .gitio import changed_files, git, top
from .result import BLOCK, INFO, WARN, Result

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
SOURCE_SUFFIXES = {".java", ".kt", ".py", ".js", ".jsx", ".mjs", ".ts", ".tsx", ".go"}
NOT_PRODUCTION = re.compile(r"(^|/)setup\.py$|\.config\.(js|mjs|cjs|ts)$|\.d\.ts$")


def is_source(path: str) -> bool:
    return Path(path).suffix in SOURCE_SUFFIXES and not is_test_path(path) and not NOT_PRODUCTION.search(path)


def changed_lines(repo: str, base: str, include: str | None = None) -> dict[str, set[int]]:
    root = top(repo)
    out: dict[str, set[int]] = {}
    files = {p: k for p, k in changed_files(repo, base).items()
             if k != "D" and (fnmatch.fnmatchcase(p, include) if include else is_source(p))}
    for path, kind in files.items():
        if kind == "A" and not git(root, "ls-files", "--", path).strip():      # untracked new file: every line
            n = len((root / path).read_text(encoding="utf-8", errors="replace").splitlines())
            out[path] = set(range(1, n + 1))
            continue
        lines: set[int] = set()
        for line in git(root, "diff", "-U0", base, "--", path).splitlines():
            m = HUNK.match(line)
            if m:
                start, count = int(m.group(1)), int(m.group(2) or 1)
                lines.update(range(start, start + count))
        out[path] = lines
    return out


def _load_lcov(r: Path, cov: dict[str, dict[int, bool]]) -> None:
    """lcov.info (Jest, Vitest, nyc): 'lcov:<absolute posix path>' -> {line: covered}."""
    project = r.parent.parent if r.parent.name == "coverage" else r.parent
    key = None
    for line in r.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("SF:"):
            sf = Path(line[3:].strip())
            key = "lcov:" + (sf if sf.is_absolute() else project / sf).resolve().as_posix().lower()
            cov.setdefault(key, {})
        elif line.startswith("DA:") and key:
            n, hits = line[3:].split(",")[:2]
            cov[key][int(n)] = cov[key].get(int(n), False) or int(hits) > 0


def load_reports(path: str) -> tuple[dict[str, dict[int, bool]], float]:
    """JaCoCo '<package>/<File.java>' or lcov 'lcov:<abs path>' -> {line: covered} for executable lines; newest mtime."""
    p = Path(path)
    reports = [p] if p.is_file() else (
        sorted(p.rglob("jacoco.xml")) + sorted(p.rglob("lcov.info")) + sorted(p.rglob("coverage.xml")))
    cov: dict[str, dict[int, bool]] = {}
    newest = 0.0
    for r in reports:
        if r.name.endswith(".info"):
            newest = max(newest, r.stat().st_mtime)
            _load_lcov(r, cov)
            continue
        if r.name == "coverage.xml":
            newest = max(newest, r.stat().st_mtime)
            _load_cobertura(r, cov)
            continue
        newest = max(newest, r.stat().st_mtime)
        parser = ET.XMLParser()
        root = ET.parse(r, parser=parser).getroot()
        for pkg in root.iter("package"):
            for sf in pkg.iter("sourcefile"):
                key = f"{pkg.get('name')}/{sf.get('name')}"
                lines = cov.setdefault(key, {})
                for ln in sf.iter("line"):
                    mi, ci = int(ln.get("mi", 0)), int(ln.get("ci", 0))
                    if mi + ci:
                        nr = int(ln.get("nr"))
                        lines[nr] = lines.get(nr, False) or ci > 0
    return cov, newest


def _load_cobertura(r: Path, cov: dict[str, dict[int, bool]]) -> None:
    """pytest-cov / coverage.py coverage.xml: 'cov:<posix path>'."""
    root = ET.parse(r).getroot()
    for cls in root.iter("class"):
        fn = (cls.get("filename") or "").replace("\\", "/")
        if not fn:
            continue
        key = "cov:" + fn.lower()
        lines = cov.setdefault(key, {})
        for ln in cls.iter("line"):
            n, hits = int(ln.get("number", 0)), int(ln.get("hits", 0))
            if n:
                lines[n] = lines.get(n, False) or hits > 0


def _report_key(path: str) -> str | None:
    m = re.search(r"src/main/(?:java|kotlin)/(.+\.(?:java|kt))$", path)
    return m.group(1) if m else None


def _cobertura(cov: dict[str, dict[int, bool]], path: str) -> dict[int, bool] | None:
    """coverage.py paths are relative to its source root, so `python/src/pkg/a.py` is reported as `pkg/a.py`."""
    norm = path.replace("\\", "/").lower()
    hits = [k for k in cov if k.startswith("cov:") and (norm == k[4:] or norm.endswith("/" + k[4:]))]
    return cov[max(hits, key=len)] if hits else None


def run(repo: str, base: str, jacoco: str, threshold: float = 80.0, include: str | None = None) -> Result:
    res = Result("diff-coverage", inputs={"repo": repo, "base": base, "jacoco": jacoco, "threshold": threshold})
    try:
        diff = changed_lines(repo, base, include)
        cov, newest = load_reports(jacoco)
    except (RuntimeError, OSError, ET.ParseError) as e:
        return Result.not_run("diff-coverage", str(e))
    if not cov:
        return Result.not_run("diff-coverage", f"no jacoco.xml, lcov.info or coverage.xml under {jacoco} (run the tests with coverage)")
    root = top(repo)
    total = covered = 0
    per_file = {}
    for path, lines in sorted(diff.items()):
        key = _report_key(path)
        src = root / path
        if src.exists() and src.stat().st_mtime > newest:
            res.add("DCV-004", BLOCK, f"{path} changed after the coverage report was produced: re-run", path=path)
        report = cov.get(key) if key else None
        if report is None:
            report = cov.get("lcov:" + src.resolve().as_posix().lower())
        if report is None:
            report = _cobertura(cov, path)
        if report is None:
            if lines:
                res.add("DCV-002", BLOCK, f"{path}: not in any coverage report (tests not run with coverage?)", path=path)
            continue
        execl = sorted(l for l in lines if l in report)
        hit = [l for l in execl if report[l]]
        miss = [l for l in execl if not report[l]]
        total += len(execl)
        covered += len(hit)
        per_file[path] = {"executable": len(execl), "covered": len(hit), "uncovered_lines": miss}
        if miss:
            res.add("DCV-003", INFO, f"uncovered changed lines {miss}", path=path)
    pct = 100.0 * covered / total if total else 100.0
    res.inputs.update(changed_executable_lines=total, covered=covered, diff_coverage=round(pct, 1), files=per_file)
    if not diff:
        missed = sorted(p for p, k in changed_files(repo, base).items() if k != "D" and is_source(p))
        if include and missed:
            res.add("DCV-005", BLOCK, f"--include {include} matches none of the {len(missed)} changed source files "
                                      f"({', '.join(missed[:3])}): 0 lines measured is not a pass",
                    remediation="drop --include; the changed source files are found from the diff")
        else:
            res.add("DCV-005", WARN, "no production source changed: nothing to measure")
    elif not total and per_file:
        res.add("DCV-005", WARN, "no changed line is executable in the coverage report: nothing measured")
    if total and pct < threshold:
        res.add("DCV-001", BLOCK, f"diff coverage {pct:.1f}% < {threshold:.0f}% ({covered}/{total} changed executable lines)",
                remediation="add tests that execute the uncovered lines (DCV-003), or record an attributed exception")
    res.finish()
    measured = f"{pct:.1f}%" if total else "nothing measured"
    res.summary = f"diff coverage {measured} ({covered}/{total} changed executable lines); " + res.summary
    return res
