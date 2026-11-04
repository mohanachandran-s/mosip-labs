"""Platform-enforced rails: block shipping actions unless the task gates already pass.

  GRD-001  commit / push / PR with no active Crewline task
  GRD-002  always-denied action (force-push, hard reset, stash, branch delete, merge, production)
  GRD-003  commit / push / PR before the matching workflow state and gates
  GRD-004  write outside scope.approved_paths (and no scope_change grant)
  GRD-005  git commit without -s / --signoff
  GRD-006  --no-verify (skipping git hooks)
  GRD-007  raw dump of a log/report, or a test suite not run through `crewline run`
  GRD-008  Read of a dump file or a file over read_max_bytes
  GRD-009  `gh pr create` / `gh pr edit --title|--body` whose title does not start with the task's tracker key
           (`<KEY>: summary`) or whose body does not link the ticket (URL or key)

The Cursor / Claude adapter runs this from a platform hook so the model cannot skip
`crewline manifest` and still commit. Exit 0 PASS, 1 FAIL, 2 NOT_RUN.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path

from .change_scope import SENSITIVE, _match
from .manifest import CORE, check, pr_body_problem, pr_title_problem, transition
from .result import BLOCK, Result
from .workspace import TERMINAL_OUTCOMES, TERMINAL_STATES, active_manifest, tasks_root

TASKS = CORE / "tasks"
TERMINAL = TERMINAL_STATES
COMMIT_STATES = {"QUALITY_GATE", "COMMIT_PR"}
PUSH_STATES = {"COMMIT_PR", "PR_CLOSEOUT"}
ALWAYS_ALLOW_WRITE_PREFIXES = (
    str(CORE / "tasks").replace("\\", "/").lower() + "/",
)

DENY_RULE = {
    "deny_hooks": ("GRD-006", "--no-verify is refused: git hooks and Crewline gates must run"),
    "deny_force": ("GRD-002", "force-push is disabled in this version"),
    "deny_destructive": ("GRD-002", "hard reset / git clean -f is refused: never discard foreign or uncommitted work"),
    "deny_stash": ("GRD-002", "git stash is refused: never stash another person's uncommitted work"),
    "deny_delete": ("GRD-002", "deleting branches is disabled in this version"),
    "deny_merge": ("GRD-002", "merging the PR is a human action; the agent does not merge"),
    "deny_prod": ("GRD-002", "production deploy is disabled in this version"),
    "deny_dump": ("GRD-007", "do not cat/type a log or report: use `crewline clip --file <log>`"),
    "deny_raw_suite": ("GRD-007", "run builds/tests through `crewline run --repo <dir> --out <record> -- <command>`"),
}

READ_MAX_BYTES = 131072
DUMP_NAME = re.compile(r"(?i)(\.log$|full-report|error-report|surefire-reports|testng-results|jacoco\.xml)")
RAW_SUITE = re.compile(
    r"(^|[\s;&|])((mvn|mvnw)(\.cmd)?\b.*\b(test|verify|install)\b|"
    r"npm(\.cmd)?\s+t(est)?\b|pytest\b|gradlew?(\.bat)?\s+test\b)",
    re.I,
)
DUMP_CMD = re.compile(
    r"(^|[\s;&|])(cat|type|less|more|get-content|gc)(\.exe)?\s+",
    re.I,
)


def find_active_manifest(tasks_dir: Path | None = None, cwd: str | None = None) -> Path | None:
    return active_manifest(tasks_dir, cwd)[0]


def _name_task(res: Result, manifest_path: str | None) -> Result:
    """Every deny says which task it was judged against."""
    if manifest_path:
        task = Path(manifest_path).parent.name
        for f in res.findings:
            if f.severity == BLOCK and not f.approved_by:
                f.message += f" [active task {task}; switch: crewline task activate --id <id>]"
    return res


def _scratch(path: str, cwd: str | None) -> bool:
    """A session scratchpad or temp file: not the repository, so not under scope.approved_paths. A checkout that happens
    to live in a temp folder (it has a git top, or it is the session's folder) stays guarded."""
    roots = {tempfile.gettempdir(), os.environ.get("TEMP", ""), os.environ.get("TMP", ""), os.environ.get("TMPDIR", "")}
    if os.name != "nt":
        roots.add("/tmp")
    p = Path(path)
    if not p.is_absolute():
        return False                                  # relative paths are the session's own folder
    forms = {p, p.resolve()}
    bases = {f for r in roots if r for f in (Path(r), Path(r).resolve())}
    if not any(f.is_relative_to(b) for f in forms for b in bases):
        return False
    if cwd and any(f.is_relative_to(Path(cwd).resolve()) or f.is_relative_to(Path(cwd)) for f in forms):
        return False
    start = p.parent
    while not start.is_dir() and start != start.parent:
        start = start.parent
    return _git_top(start) is None


def _crewline_wrapped(low: str) -> bool:
    return bool(re.search(r"crewline(\.py)?(\s+\S+){0,8}\s+(run|clip)\b", low))


def classify_shell(command: str) -> str:
    """Map a shell command to a guard action: deny_*, commit, push, pr, other."""
    text = (command or "").strip()
    if not text:
        return "other"
    low = text.lower()
    if re.search(r"(^|\s)--no-verify(\s|$)", low):
        return "deny_hooks"
    if not _crewline_wrapped(low):
        if DUMP_CMD.search(low) and DUMP_NAME.search(low):
            return "deny_dump"
        if RAW_SUITE.search(low):
            return "deny_raw_suite"
    git = bool(re.search(r"(^|[\s;&|])git(\.exe)?\s", low + " ") or low.startswith("git ") or low.startswith("git.exe "))
    if git and re.search(r"\bpush\b", low) and re.search(r"(^|\s)(-f|--force|--force-with-lease)(\s|$)", low):
        return "deny_force"
    if git and re.search(r"\breset\b", low) and re.search(r"--hard", low):
        return "deny_destructive"
    if git and re.search(r"\bclean\b", low) and re.search(r"(^|\s)-[a-z]*f", low):
        return "deny_destructive"
    if git and re.search(r"\bstash\b", low):
        return "deny_stash"
    if git and re.search(r"\bbranch\b", low) and re.search(r"(^|\s)-[dD]\b", low):
        return "deny_delete"
    if re.search(r"\bgh(\.exe)?\s+pr\s+merge\b", low):
        return "deny_merge"
    if re.search(r"\b(helm|kubectl)\b", low) and re.search(r"\b(prod|production|live)\b", low):
        return "deny_prod"
    if git and re.search(r"\bcommit\b", low):
        return "commit"
    if git and re.search(r"\bpush\b", low):
        return "push"
    if re.search(r"\bgh(\.exe)?\s+pr\s+create\b", low):
        return "pr"
    if re.search(r"\bgh(\.exe)?\s+pr\s+edit\b", low) and (pr_title(text) is not None or _has_body(text)):
        return "pr_edit"
    return "other"


def _flag(command: str, long: str, short: str) -> str | None:
    """The value of `--long v`, `--long=v` or `-s v` in a gh command, or None when absent."""
    try:
        toks = shlex.split(command, posix=True)
    except ValueError:
        toks = command.split()
    for i, tok in enumerate(toks):
        if tok in (long, short) and i + 1 < len(toks):
            return toks[i + 1]
        if tok.startswith(long + "="):
            return tok.split("=", 1)[1]
    return None


def pr_title(command: str) -> str | None:
    return _flag(command, "--title", "-t")


def _has_body(command: str) -> bool:
    return _flag(command, "--body", "-b") is not None or _flag(command, "--body-file", "-F") is not None


def pr_body(command: str) -> str | None:
    """--body text, or the contents of --body-file (a file the hook can read; `-` / stdin cannot be checked)."""
    body = _flag(command, "--body", "-b")
    if body is not None:
        return body
    path = _flag(command, "--body-file", "-F")
    if path is None or path == "-":
        return None
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return None


def has_signoff(command: str) -> bool:
    if re.search(r"(^|\s)--signoff(\s|$)", command):
        return True
    for tok in command.split():
        if tok in ("-s", "--signoff"):
            return True
        if tok.startswith("-") and not tok.startswith("--") and "s" in tok[1:]:
            return True
    return False


def _load(manifest_path: str) -> dict:
    return json.loads(Path(manifest_path).read_text(encoding="utf-8"))


def _need_task(res: Result, manifest_path: str | None, action: str) -> dict | None:
    if not manifest_path:
        res.add("GRD-001", BLOCK, f"{action} needs an active Crewline task (<task workspace>/<id>/task-manifest.json: {tasks_root()})",
                remediation="INTAKE: create the manifest first, or set CREWLINE_TASK_MANIFEST")
        return None
    try:
        return _load(manifest_path)
    except (OSError, json.JSONDecodeError) as e:
        res.add("GRD-001", BLOCK, f"cannot read task manifest {manifest_path}: {e}")
        return None


def check_shell(command: str, manifest_path: str | None = None) -> Result:
    return _name_task(_check_shell(command, manifest_path), manifest_path)


def _check_shell(command: str, manifest_path: str | None = None) -> Result:
    res = Result("guard", inputs={"command": command, "manifest": manifest_path or ""})
    action = classify_shell(command)
    res.inputs["action"] = action
    if action.startswith("deny_"):
        rule, msg = DENY_RULE[action]
        res.add(rule, BLOCK, msg, evidence=command)
        return res.finish()
    if action == "other":
        return res.finish()
    if action == "commit" and not has_signoff(command):
        res.add("GRD-005", BLOCK, "git commit must be signed off (`git commit -s`)", evidence=command)
        return res.finish()
    if action == "pr_edit" and not manifest_path:
        return res.finish()                  # renaming a PR outside a task is not a shipping action
    doc = _need_task(res, manifest_path, action)
    if doc is None:
        return res.finish()
    state = doc.get("current_state")
    if action in ("pr", "pr_edit"):                  # an edit is checked only for the parts it changes
        title = pr_title(command)
        problems = [p for p in (
            pr_title_problem(doc, title) if action == "pr" or title is not None else "",
            pr_body_problem(doc, pr_body(command)) if action == "pr" or _has_body(command) else "") if p]
        for problem in problems:
            res.add("GRD-009", BLOCK, problem, evidence=command,
                    remediation="title `<KEY>: <summary>` (mosip/pre-registration#1089); body `Closes <ticket URL>` "
                                "(templates/pr-template.md) via --body or a readable --body-file")
        if problems or action == "pr_edit":
            return res.finish()
    if action == "commit":
        if state not in COMMIT_STATES:
            res.add("GRD-003", BLOCK, f"git commit is only allowed at QUALITY_GATE or COMMIT_PR, not {state}",
                    remediation="finish reviews, system tests and quality_gate; then crewline manifest --to COMMIT_PR")
            return res.finish()
        gate = transition(manifest_path, "COMMIT_PR") if state == "QUALITY_GATE" else check(manifest_path)
        return _merge(res, gate, "commit refused until COMMIT_PR gates pass")
    if action in ("push", "pr"):
        if state not in PUSH_STATES:
            res.add("GRD-003", BLOCK, f"{action} is only allowed at COMMIT_PR or PR_CLOSEOUT, not {state}",
                    remediation="record approval push_and_pr and pass dco / branch_scope / change_scope")
            return res.finish()
        if state == "COMMIT_PR":
            return _merge(res, transition(manifest_path, "PR_CLOSEOUT"), "push/PR refused until COMMIT_PR->PR_CLOSEOUT guards pass")
        return res.finish()
    return res.finish()


def _merge(res: Result, other: Result, prefix: str) -> Result:
    res.inputs["inner"] = other.validator
    for f in other.findings:
        if f.severity == BLOCK and not f.approved_by:
            res.add(f.rule_id, BLOCK, f"{prefix}: {f.message}", path=f.path, evidence=f.evidence,
                    remediation=f.remediation)
    return res.finish()


def _rel_repo_path(path: str, cwd: str | None = None) -> str:
    p = Path(path)
    if not p.is_absolute() and cwd:
        p = Path(cwd) / p
    try:
        p = p.resolve()
    except OSError:
        p = Path(path)
    text = str(p).replace("\\", "/")
    lower = text.lower()
    module_tasks = str(tasks_root(cwd)).replace("\\", "/").lower() + "/"
    for prefix in ALWAYS_ALLOW_WRITE_PREFIXES + (module_tasks,):
        if lower.startswith(prefix) or lower.startswith(prefix.rstrip("/")):
            return ""  # empty => always allowed (task workspace)
    # Prefer a path relative to cwd so approved_paths globs match; a file outside cwd (the session sits in a
    # subfolder, e.g. ui-test/, and writes .crewline/...) is relative to its own git top, never its bare name.
    root = Path(cwd).resolve() if cwd else p.parent
    try:
        return p.resolve().relative_to(root).as_posix()
    except ValueError:
        pass
    start = p.parent
    while not start.is_dir() and start != start.parent:
        start = start.parent
    top = _git_top(start)
    if top:
        try:
            return p.resolve().relative_to(top).as_posix()
        except ValueError:
            pass
    return p.name


def _git_top(start: Path) -> Path | None:
    try:
        r = subprocess.run(["git", "-C", str(start), "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                           encoding="utf-8", timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return Path(r.stdout.strip()).resolve() if r.returncode == 0 and r.stdout.strip() else None


def check_write(path: str, manifest_path: str | None = None, cwd: str | None = None) -> Result:
    return _name_task(_check_write(path, manifest_path, cwd), manifest_path)


def _check_write(path: str, manifest_path: str | None = None, cwd: str | None = None) -> Result:
    res = Result("guard", inputs={"path": path, "manifest": manifest_path or ""})
    rel = _rel_repo_path(path, cwd)
    if rel == "" or _scratch(path, cwd):
        return res.finish()
    if not manifest_path:
        return res.finish()  # drafting without a task is allowed; shipping is not
    try:
        doc = _load(manifest_path)
    except (OSError, json.JSONDecodeError) as e:
        res.add("GRD-001", BLOCK, f"cannot read task manifest: {e}")
        return res.finish()
    approved = doc.get("scope", {}).get("approved_paths") or []
    grants = [a for a in doc.get("approvals", []) if a.get("action") == "scope_change"
              and all(a.get(k) for k in ("approver", "reason", "scope", "timestamp"))]

    def granted(item: str) -> str | None:
        for a in grants:
            for g in str(a.get("scope", "")).replace(",", " ").split():
                if _match(item, g):
                    return a["approver"]
        return None

    if not approved:
        if any(_match(rel, s) for s in ("src/**", "**/src/**", "api-test/**", "helm/**",
                                         "db_upgrade_scripts/**", ".github/**")):
            res.add("GRD-004", BLOCK, f"{rel} has no scope.approved_paths yet (plan first)",
                    path=rel, remediation="IMPLEMENTATION_PLAN must list approved_paths before module edits")
        return res.finish()
    if not any(_match(rel, p) for p in approved):
        f = res.add("GRD-004", BLOCK, f"{rel} is outside scope.approved_paths", path=rel,
                    remediation="impact analysis + approval scope_change naming the file")
        f.approved_by = granted(rel)
    if any(_match(rel, s) for s in SENSITIVE) and not granted(rel):
        f = res.add("GRD-004", BLOCK, f"{rel} is a CI/build/deploy/config file (needs scope_change)", path=rel)
        f.approved_by = granted(rel)
    return res.finish()


def check_read(path: str, cwd: str | None = None, manifest_path: str | None = None) -> Result:
    return _name_task(_check_read(path, cwd), manifest_path)


def _check_read(path: str, cwd: str | None = None) -> Result:
    """Refuse dump files and oversized Reads so the model cannot load a report or a 4k-line class."""
    res = Result("guard", inputs={"path": path, "action": "read"})
    if not path:
        return res.finish()
    p = Path(path)
    if not p.is_absolute() and cwd:
        p = Path(cwd) / p
    text = str(p).replace("\\", "/")
    if DUMP_NAME.search(text):
        res.add("GRD-007", BLOCK, f"do not Read {p.name}: use `crewline clip --file` or a validator", path=text)
        return res.finish()
    try:
        size = p.stat().st_size
    except OSError:
        return res.finish()
    if size > READ_MAX_BYTES:
        res.add("GRD-008", BLOCK, f"{p.name} is {size} bytes (max {READ_MAX_BYTES}): Grep/clip, do not Read the whole file",
                path=text, remediation="python -m crewline clip --file <path>")
    return res.finish()


def run(kind: str, target: str, manifest_path: str | None = None, cwd: str | None = None) -> Result:
    if kind == "shell":
        return check_shell(target, manifest_path)
    if kind == "write":
        return check_write(target, manifest_path, cwd)
    if kind == "read":
        return check_read(target, cwd)
    return Result.not_run("guard", f"unknown kind {kind!r}")
