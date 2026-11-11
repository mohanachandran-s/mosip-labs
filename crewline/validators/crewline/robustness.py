"""Robustness gate: exception handling, error paths and obvious hot-path mistakes before review.

Review rounds on empty catch, swallowed Exception, missing API error tests, Thread.sleep and
'just add another query' are a process failure. This does not invent a profiler or a new exception
hierarchy (principle 1): it scans the production diff for high-signal anti-patterns and requires the
developer to record how they reused the module's handler and which test proves each API error path.
IMPLEMENTATION_REVIEW is refused until the gate PASSes.

  ROB-001  empty or comment-only catch/except in production code                                  BLOCK
  ROB-002  catch (Exception|Throwable|Error) / except Exception that does not rethrow             BLOCK
  ROB-003  new `throws Exception` (raw) on production code                                        BLOCK
  ROB-004  Thread.sleep / time.sleep / await setTimeout used as a wait in production              BLOCK
  ROB-005  api:true AC with no error-path record (status/error_code + test id)                    BLOCK
  ROB-006  code task missing implementation.robustness (exception_reuse + hot_path_notes)         BLOCK
  MAN-001  the manifest fails its schema (reported with JSON paths before any check reads it)     BLOCK

  python -m crewline robustness --repo <dir> --base <ref> --manifest <m>
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .coding_standards import _added_lines, is_test_path
from .gitio import changed_files, git, top
from .manifest import schema_errors
from .result import BLOCK, Result

CODE_TASKS = {"feature", "bug-fix", "database-migration", "dependency-upgrade"}
COMMENT_OR_EMPTY = re.compile(r"^(?://.*|/\*.*\*/|#.*|\s)*$", re.S)
BROAD_JAVA = re.compile(r"^(?:final\s+)?(Exception|Throwable|Error)\s+\w+$")
BROAD_PY = re.compile(r"^(Exception|BaseException|Error)(\s+as\s+\w+)?$")
SLEEP_RX = re.compile(
    r"Thread\.sleep\s*\(|TimeUnit\.\w+\.sleep\s*\(|time\.sleep\s*\("
    r"|await\s+new\s+Promise\([^)]*setTimeout"
)
THROWS_RAW = re.compile(r"\bthrows\s+Exception\b")
CATCH_HEAD = re.compile(r"\bcatch\s*\(")
EXCEPT_HEAD = re.compile(r"^\s*except\s+([^:]*)\s*:", re.M)


def _strip_body(body: str) -> str:
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    body = re.sub(r"//.*?$", "", body, flags=re.M)
    body = re.sub(r"#.*?$", "", body, flags=re.M)
    return body.strip()


def _java_catches(text: str) -> list[tuple[int, str, str, int, int]]:
    """(start_line, param, body, body_start_line, body_end_line) for catch (...) { }."""
    out = []
    i, n, lines_upto = 0, len(text), [1]
    for ch in text:
        lines_upto.append(lines_upto[-1] + (1 if ch == "\n" else 0))
    while True:
        m = CATCH_HEAD.search(text, i)
        if not m:
            break
        p0 = text.find("(", m.start())
        depth, p1 = 1, p0 + 1
        while p1 < n and depth:
            if text[p1] == "(":
                depth += 1
            elif text[p1] == ")":
                depth -= 1
            p1 += 1
        param = text[p0 + 1:p1 - 1].strip()
        brace = text.find("{", p1 - 1)
        if brace < 0:
            i = m.end()
            continue
        depth, j = 1, brace + 1
        while j < n and depth:
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
            j += 1
        body = text[brace + 1:j - 1]
        out.append((lines_upto[m.start()], param, body, lines_upto[brace], lines_upto[max(brace, j - 2)]))
        i = j
    return out


def _py_excepts(text: str) -> list[tuple[int, str, str, int, int]]:
    lines = text.splitlines()
    out = []
    for idx, line in enumerate(lines):
        m = EXCEPT_HEAD.match(line)
        if not m:
            continue
        indent = len(line) - len(line.lstrip())
        body_lines = []
        j = idx + 1
        while j < len(lines):
            raw = lines[j]
            if raw.strip() and (len(raw) - len(raw.lstrip())) <= indent:
                break
            body_lines.append(raw)
            j += 1
        out.append((idx + 1, m.group(1).strip() or "", "\n".join(body_lines), idx + 2, j))
    return out


def _touches(added_lines: set[int], start: int, end: int) -> bool:
    return any(start <= n <= end for n in added_lines)


def _error_tests(ac: dict) -> set[str]:
    return {t.lower() for t in (ac.get("test_ids") or []) + (ac.get("component_test_ids") or [])
            + (ac.get("system_test_ids") or [])}


def run(repo: str, base: str, manifest_path: str) -> Result:
    res = Result("robustness", inputs={"repo": repo, "base": base, "manifest": manifest_path})
    try:
        doc = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        root = top(repo)
        git(root, "rev-parse", "--verify", f"{base}^{{commit}}")
        changed = changed_files(repo, base)
    except (OSError, ValueError, RuntimeError) as e:
        return Result.not_run("robustness", str(e))
    for err in schema_errors(doc) or []:        # e.g. implementation.robustness as a string: name the field, never crash
        res.add("MAN-001", BLOCK, err, remediation="fix the manifest (templates/task-manifest.schema.json), then re-run")
    if res.findings:
        return res.finish()

    task_type = doc.get("task_type") or "test-automation"
    for path, kind in sorted(changed.items()):
        if kind == "D" or is_test_path(path):
            continue
        fp = root / path
        if not fp.is_file():
            continue
        added = _added_lines(root, base, path)
        if not added and kind == "A":
            added = list(enumerate(fp.read_text(encoding="utf-8", errors="replace").splitlines(), 1))
        added_nos = {n for n, _ in added}
        added_txt = "\n".join(t for _, t in added)
        for lineno, text in added:
            if SLEEP_RX.search(text):
                res.add("ROB-004", BLOCK, f"{path}:{lineno} waits with sleep/setTimeout in production code",
                        path=path, line=lineno, evidence=text.strip()[:120],
                        remediation="use the module's retry/backoff helper or an event; never sleep on a request path")
            if THROWS_RAW.search(text) and path.endswith((".java", ".kt")):
                res.add("ROB-003", BLOCK, f"{path}:{lineno} declares throws Exception — use the module's exception type",
                        path=path, line=lineno, evidence=text.strip()[:120])

        text = fp.read_text(encoding="utf-8", errors="replace")
        blocks = _java_catches(text) if path.endswith((".java", ".kt", ".js", ".ts", ".tsx")) else []
        if path.endswith(".py"):
            blocks = _py_excepts(text)
        for start, param, body, b0, b1 in blocks:
            if not _touches(added_nos, start, b1) and not (kind == "A"):
                continue
            stripped = _strip_body(body)
            if not stripped:
                res.add("ROB-001", BLOCK, f"{path}:{start} empty catch/except swallows the failure",
                        path=path, line=start, evidence=param or "(empty)",
                        remediation="handle it with the module's exception type / handler, or rethrow; do not leave it empty")
                continue
            broad = bool(BROAD_JAVA.match(param.replace("\n", " ").strip())) if not path.endswith(".py") \
                else bool(BROAD_PY.match(param) or param == "")
            if broad and not re.search(r"\bthrow\b|\braise\b", stripped):
                res.add("ROB-002", BLOCK, f"{path}:{start} catches {param or 'Exception'} and does not rethrow",
                        path=path, line=start, evidence=stripped.splitlines()[0][:120],
                        remediation="catch the specific type the module already uses, wrap it, and throw; logging-only is not handling")

        if path.endswith(".py") and re.search(r"^\s*except\s*:\s*$", added_txt, re.M):
            if not any(f.rule_id == "ROB-001" and f.path == path for f in res.findings):
                res.add("ROB-001", BLOCK, f"{path} adds a bare except:", path=path,
                        remediation="except the specific error; never except:")

    if task_type in CODE_TASKS:
        rob = (doc.get("implementation") or {}).get("robustness") or {}
        reuse, notes = (rob.get("exception_reuse") or "").strip(), (rob.get("hot_path_notes") or "").strip()
        if len(reuse) < 8 or len(notes) < 16:
            res.add("ROB-006", BLOCK, "implementation.robustness.exception_reuse and hot_path_notes are required "
                    "before review (which existing handler, and why this is not an extra round-trip / N+1)",
                    remediation="record exception_reuse as file:line of the module handler you reused, and hot_path_notes "
                    "naming the existing service/repo call — do not invent a new exception type or a new query")
        paths = {p.get("ac_id"): p for p in rob.get("error_paths") or [] if isinstance(p, dict)}
        results = {k.lower(): v for k, v in (doc.get("test_results") or {}).items()}
        for ac in (doc.get("requirements") or {}).get("acceptance_criteria") or []:
            if not ac.get("api"):
                continue
            aid = ac.get("id", "?")
            entry = paths.get(aid)
            tests = _error_tests(ac)
            if not entry or not (entry.get("status") or entry.get("error_code")) or not entry.get("test_id"):
                res.add("ROB-005", BLOCK, f"{aid}: api AC has no error-path record (HTTP status or error_code + test_id)",
                        evidence=aid, remediation="add implementation.robustness.error_paths[] for the negative case "
                        "the caller sees; the test_id must be on this AC and PASS")
                continue
            tid = str(entry.get("test_id"))
            if tid.lower() not in tests:
                res.add("ROB-005", BLOCK, f"{aid}: error-path test {tid} is not on this AC's test_ids/component_test_ids",
                        evidence=tid)
            elif results.get(tid.lower()) != "PASS":
                res.add("ROB-005", BLOCK, f"{aid}: error-path test {tid} has no PASS result on the branch "
                        f"(actual {results.get(tid.lower())!r})", evidence=tid)
    return res.finish()
