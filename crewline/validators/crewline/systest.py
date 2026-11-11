"""Which projects of a module are its system-test suites, read from the module's `.crewline/module.yaml`.

  stack.api_test: true           -> api-test
  stack.ui_test_projects: [...]  -> UI test projects (e.g. ui-test)
  stack.system_test_projects     -> any other suite folder (e.g. a DSL rig)
  stack.node_projects: [...]     -> UI source projects; a change there needs a UI test project run ("." = the repo root)

A module without these keys declares nothing, and the checks that use this stay quiet (generic modules).
A record counts for a suite by WHERE it ran (`cwd` of a `crewline run` record, `candidate` report of a regression
record), never by words in its command.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from . import workspace
from .gitio import head, worktree_digest

CODE_TYPES = {"feature", "bug-fix", "refactor", "dependency-upgrade", "database-migration", "test-automation"}
RUN_FIELDS = ("command", "cwd", "exit_code", "started_at", "commit", "worktree_digest", "log")


def _norm(p: str | Path) -> str:
    return str(p).replace("\\", "/").rstrip("/").lower()


def _abs(p: str | Path) -> str:
    try:
        return _norm(Path(str(p)).resolve())
    except OSError:
        return _norm(p)


def module_stack(doc: dict) -> dict:
    """Merged system-test facts of every task repository that carries .crewline/module.yaml."""
    out = {"system": [], "ui": [], "node": [], "repos": []}
    for repo in (doc.get("scope") or {}).get("repositories") or []:
        if not str(repo.get("path") or ""):
            continue
        root = Path(str(repo["path"])).resolve()
        ctx = workspace.context_dir(root, doc) if root.is_dir() else None     # a task worktree's base may not carry it
        if ctx is None or not (ctx / "module.yaml").is_file():
            continue
        try:
            mod = yaml.safe_load((ctx / "module.yaml").read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        stack = mod.get("stack") if isinstance(mod.get("stack"), dict) else {}
        ui = [str(p) for p in stack.get("ui_test_projects") or []]
        system = (["api-test"] if stack.get("api_test") else []) + ui + [str(p) for p in stack.get("system_test_projects") or []]
        out["repos"].append(str(root))
        for key, vals in (("system", system), ("ui", ui), ("node", [str(p) for p in stack.get("node_projects") or []])):
            out[key] += [(str(root), v) for v in vals]
    return out


def _under(path: str, root: str, project: str) -> bool:
    full = _abs(Path(root) / project)
    return bool(path) and (_abs(path) == full or _abs(path).startswith(full + "/"))


def ran_in(rec: dict, root: str, project: str) -> bool:
    """A `crewline run` record ran inside `project` of the repository at `root` (its cwd)."""
    return _under(str(rec.get("cwd") or ""), root, project)


def compared(rec: dict, root: str, project: str) -> bool:
    """A full-regression record's candidate (branch) report is inside `project`."""
    return _under(str((rec.get("inputs") or {}).get("candidate") or ""), root, project)


def missing_run_fields(rec: dict) -> list[str]:
    return [k for k in RUN_FIELDS if rec.get(k) in (None, "")]


def touches(approved_paths: list[str], project: str) -> bool:
    proj = _norm(project)
    if proj in ("", "."):
        return bool(approved_paths)                      # a UI project at the repo root: every change touches it
    return any(_norm(p) == proj or _norm(p).startswith(proj + "/") for p in approved_paths or [])


def report_sha256(path: str | Path) -> str:
    """Hash of a report file, or of every file under a report folder (sorted by relative path)."""
    p = Path(path)
    h = hashlib.sha256()
    files = [p] if p.is_file() else sorted(f for f in p.rglob("*") if f.is_file()) if p.is_dir() else []
    for f in files:
        h.update(str(f.relative_to(p) if p.is_dir() else f.name).encode())
        h.update(f.read_bytes())
    return h.hexdigest() if files else ""


def _existing_dir(path: str | Path) -> Path:
    start = Path(path)
    while not start.is_dir() and start != start.parent:
        start = start.parent
    return start


def repo_head(path: str | Path) -> str:
    try:
        return head(str(_existing_dir(path))) or ""
    except Exception:  # noqa: BLE001 - not a git checkout: no commit to compare
        return ""


def repo_digest(path: str | Path) -> str:
    """worktree_digest of the checkout holding `path`: what was tested, including uncommitted changes."""
    try:
        return worktree_digest(str(_existing_dir(path))) or ""
    except Exception:  # noqa: BLE001 - not a git checkout
        return ""


def stale_reason(inputs: dict) -> str:
    """Why a full-regression record no longer describes the branch, or "" when it still does."""
    cand, base = inputs.get("candidate") or "", inputs.get("baseline") or ""
    cand_report = inputs.get("candidate_copy") or cand          # hashes are of the copies under evidence/reports/
    base_report = inputs.get("baseline_copy") or base
    if not (inputs.get("baseline_sha256") and inputs.get("candidate_sha256") and inputs.get("commit")
            and inputs.get("worktree_digest")):
        return "has no report hashes / commit / worktree_digest (written by hand, or by Crewline < 0.7.0)"
    if inputs["baseline_sha256"] == inputs["candidate_sha256"]:
        return "compares a report with itself"
    if report_sha256(cand_report) != inputs["candidate_sha256"] or report_sha256(base_report) != inputs["baseline_sha256"]:
        return "a report changed or is gone since the record was written"
    if repo_head(cand) != inputs["commit"]:
        return f"ran at {inputs['commit'][:12]}, HEAD is now {repo_head(cand)[:12] or 'unknown'}"
    if repo_digest(cand) != inputs["worktree_digest"]:
        return "the uncommitted changes differ from the ones it ran on"
    return ""
