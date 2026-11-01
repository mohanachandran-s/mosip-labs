"""Unit-test integrity: the development counterpart of expected-diff. Compares the working tree with a base ref.

  TIN-001  test method removed (an @Test that existed on the base is gone)                      BLOCK
  TIN-002  test disabled (@Disabled / @Ignore / enabled=false / assumeTrue(false) added)         BLOCK
  TIN-003  fewer assertions in a test file than on the base                                      BLOCK
  TIN-004  test file deleted                                                                     BLOCK
  TIN-005  existing test file modified (only with --strict, e.g. a refactor)                     BLOCK
  TIN-006  expected exception / timeout relaxed (expected=, assertThrows type, timeout raised)   WARN

Covers JUnit 4/5 and TestNG Java under src/test/; Jest/Vitest/Mocha (`__tests__/`, `*.test.*`, `*.spec.*`);
pytest (`test_*.py`, `*_test.py`, `tests/*.py`); Go (`*_test.go`). Every BLOCK can be covered by an approval record in
the expected-diff format: {rule_id, target: <path>[::<method>], approver, reason, timestamp}.
"""
from __future__ import annotations

import re

from .expected_diff import load_approvals
from .gitio import changed_files, show, top
from .result import BLOCK, WARN, Result

TEST_FILE = re.compile(
    r"(^|/)src/test/.*\.java$|(^|/)__tests__/.*\.[cm]?[jt]sx?$|\.(test|spec)\.[cm]?[jt]sx?$"
    r"|(^|/)tests/.*\.py$|(^|/)test_[^/]+\.py$|_test\.py$|_test\.go$"
)
JAVA = {
    "method": re.compile(r"@(?:org\.junit\.(?:jupiter\.api\.)?)?Test\b[^\n]*\n(?:\s*@[^\n]*\n)*\s*(?:public\s+|protected\s+|private\s+)?"
                         r"(?:static\s+)?void\s+(\w+)\s*\("),
    "disabled": re.compile(r"@(Disabled|Ignore)\b|enabled\s*=\s*false|assumeTrue\(\s*false\s*\)"),
    "assert": re.compile(r"\b(assert\w*|verify|fail|expect\w*)\s*\("),
    "relax": re.compile(r"expected\s*=|timeout\s*=|assertThrows\s*\("),
}
JS = {  # Jest / Vitest / Mocha
    "method": re.compile(r"\b(?:it|test)(?:\.each\([^)]*\))?\s*\(\s*[\"'`]([^\"'`]+)[\"'`]"),
    "disabled": re.compile(r"\b(?:it|test|describe)\.(?:skip|todo)\s*\(|\bx(?:it|test|describe)\s*\(|\.only\s*\("),
    "assert": re.compile(r"\bexpect\s*\(|\bassert(?:\.\w+)?\s*\("),
    "relax": re.compile(r"\.toMatchSnapshot\s*\(|jest\.setTimeout|\btimeout\s*:"),
}
PY = {  # pytest / unittest
    "method": re.compile(r"\bdef\s+(test_\w+)\s*\("),
    "disabled": re.compile(r"@pytest\.mark\.(?:skip|skipif)\b|pytest\.skip\s*\(|@unittest\.skip"),
    "assert": re.compile(r"\bassert\b|\bpytest\.raises\s*\(|\bself\.assert\w+\s*\("),
    "relax": re.compile(r"pytest\.approx|timeout\s*="),
}
GO = {
    "method": re.compile(r"\bfunc\s+(Test\w+)\s*\("),
    "disabled": re.compile(r"\bt\.Skip(?:f|Now)?\s*\("),
    "assert": re.compile(r"\bt\.(?:Error|Fatal|Fail)(?:f|Now)?\s*\(|\bassert\."),
    "relax": re.compile(r"t\.Parallel\s*\("),
}


def _strip_comments(src: str) -> str:
    return re.sub(r"//[^\n]*|/\*.*?\*/", "", src, flags=re.S)


def run(repo: str, base: str, approvals_file: str | None = None, strict: bool = False) -> Result:
    res = Result("test-integrity", inputs={"repo": repo, "base": base, "strict": strict})
    try:
        root = top(repo)
        changed = {p: k for p, k in changed_files(repo, base).items() if TEST_FILE.search(p)}
    except RuntimeError as e:
        return Result.not_run("test-integrity", str(e))
    for path, kind in sorted(changed.items()):
        old = show(root, base, path)
        if old is None:
            continue                                   # a new test file can only add coverage
        if kind == "D" or not (root / path).exists():
            res.add("TIN-004", BLOCK, f"test file deleted: {path}", path=path, evidence=path)
            continue
        new = (root / path).read_text(encoding="utf-8", errors="replace")
        o, n = _strip_comments(old), _strip_comments(new)
        if path.endswith(".java"):
            rx = JAVA
        elif path.endswith(".py"):
            rx = PY
        elif path.endswith(".go"):
            rx = GO
        else:
            rx = JS
        for m in sorted(set(rx["method"].findall(o)) - set(rx["method"].findall(n))):
            res.add("TIN-001", BLOCK, f"test removed: {m}", path=path, evidence=f"{path}::{m}")
        if len(rx["disabled"].findall(n)) > len(rx["disabled"].findall(o)):
            res.add("TIN-002", BLOCK, "test disabled or focused (@Disabled/@Ignore, .skip/.todo/xit, .only added)",
                    path=path, evidence=path)
        a_old, a_new = len(rx["assert"].findall(o)), len(rx["assert"].findall(n))
        if a_new < a_old:
            res.add("TIN-003", BLOCK, f"assertions {a_old} -> {a_new}", path=path, evidence=path)
        if sorted(rx["relax"].findall(n)) != sorted(rx["relax"].findall(o)):
            res.add("TIN-006", WARN, "expected-exception / timeout declarations changed", path=path, evidence=path)
        if strict and o != n:
            res.add("TIN-005", BLOCK, "existing test changed (strict mode: behaviour-preserving task)", path=path, evidence=path)

    for appr in load_approvals(approvals_file):
        if str(appr.get("status", "")).upper().startswith(("SUPERSEDED", "REVOKED")):
            continue
        for f in res.findings:
            if f.rule_id == appr.get("rule_id") and appr.get("target") in (f.evidence, f.path) \
                    and appr.get("approver") and appr.get("reason"):
                f.approved_by = appr["approver"]
    res.inputs["changed_test_files"] = len(changed)
    return res.finish()
