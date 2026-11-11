"""Where a task's records live.

- CREWLINE_TASKS=<dir>                        explicit (tests, CI)
- a repository with a `.crewline/` folder    <main clone>/.crewline-work/tasks/  (also from inside a task worktree:
                                              the main clone is found through git's common dir, so one clone has one
                                              task list)
- any other git clone (not yet onboarded)      <main clone>/.crewline-work/tasks/  (`task new` adds `.crewline-work/`
                                              to .git/info/exclude)
- outside any git clone, or in the Crewline   <core>/tasks/
  source clone itself

A task worktree (<main clone>/.crewline-work/worktrees/<id>) is cut from the upstream base, which usually has no `.crewline/`.
It is still an active Crewline checkout: the module's context is found by `context_dir`, never copied into it (ADR-0009).
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

CORE = Path(__file__).resolve().parents[2]


def _git(start: Path, *args: str) -> str:
    try:
        r = subprocess.run(["git", "-C", str(start), *args], capture_output=True, text=True, encoding="utf-8", timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def module_top(cwd: str | os.PathLike | None = None) -> Path | None:
    """The repository root when it carries `.crewline/` (Crewline is active there), else None."""
    start = Path(cwd or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    if not start.is_dir():
        start = start.parent
    top = _git(start, "rev-parse", "--show-toplevel")
    if top and ((Path(top) / ".crewline").is_dir() or _task_worktree(Path(top))):
        return Path(top)
    return None


def _task_worktree(top: Path) -> bool:
    """`top` is a checkout under <main clone>/.crewline-work/worktrees/ of a clone that keeps Crewline tasks."""
    work = main_clone(top) / ".crewline-work"
    try:
        return (work / "tasks").is_dir() and top.resolve().is_relative_to((work / "worktrees").resolve())
    except OSError:
        return False


def context_dir(cwd: str | os.PathLike | None = None, doc: dict | None = None) -> Path | None:
    """The module's `.crewline/` folder: this checkout's, else the one the task recorded, the main clone's, or the
    newest other task worktree's (the base branch may not carry it yet while onboarding is under review)."""
    start = Path(cwd or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    if not start.is_dir():
        start = start.parent
    top = Path(_git(start, "rev-parse", "--show-toplevel") or start)
    for here in (start, top):
        if (here / ".crewline").is_dir():
            return here / ".crewline"
    recorded = ((doc or {}).get("workspace") or {}).get("context_dir")
    if recorded and Path(recorded).is_dir():
        return Path(recorded)
    main = main_clone(top)
    if (main / ".crewline").is_dir():
        return main / ".crewline"
    found = [(p.stat().st_mtime, p) for p in (main / ".crewline-work" / "worktrees").glob("*/.crewline/module.yaml")]
    return max(found)[1].parent if found else None


def main_clone(top: Path) -> Path:
    common = _git(top, "rev-parse", "--path-format=absolute", "--git-common-dir")
    p = Path(common) if common else None
    return p.parent if p and p.name == ".git" else top


def tasks_root(cwd: str | os.PathLike | None = None) -> Path:
    env = os.environ.get("CREWLINE_TASKS")
    if env:
        return Path(env)
    top = module_top(cwd)
    if top:
        return main_clone(top) / ".crewline-work" / "tasks"
    start = Path(cwd or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    git_top = _git(start if start.is_dir() else start.parent, "rev-parse", "--show-toplevel")
    if git_top and not _is_crewline_source(Path(git_top)):
        # Any other git clone keeps its tasks, onboarded or not: <core>/tasks is the plugin cache, lost on update.
        return main_clone(Path(git_top)) / ".crewline-work" / "tasks"
    return CORE / "tasks"


def _is_crewline_source(top: Path) -> bool:
    """`top` is the clone Crewline itself runs from (developing Crewline): its tasks stay in <core>/tasks."""
    try:
        return CORE.resolve().is_relative_to(top.resolve())
    except OSError:
        return False


def disabled(env: dict | None = None) -> bool:
    env = os.environ if env is None else env
    return str(env.get("CREWLINE", "")).lower() in ("off", "0", "false", "disabled")


TERMINAL_STATES = {"DONE"}
TERMINAL_OUTCOMES = {"SUCCESS", "FAILED", "BLOCKED", "ESCALATED"}


def active_file(root: str | os.PathLike | None = None, cwd: str | os.PathLike | None = None) -> Path:
    """`.crewline-work/active-task` (in a task root outside a module clone: <root>/active-task)."""
    root = Path(root) if root else tasks_root(cwd)
    return root.parent / "active-task" if root.parent.name == ".crewline-work" else root / "active-task"


def _open_manifest(path: Path) -> bool:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return doc.get("current_state") not in TERMINAL_STATES and doc.get("outcome") not in TERMINAL_OUTCOMES


def active_manifest(tasks_dir: str | os.PathLike | None = None,
                    cwd: str | os.PathLike | None = None) -> tuple[Path | None, str]:
    """The task the rails apply to, and why. Most specific first: CREWLINE_TASK_MANIFEST (this process), the task whose
    worktree the session runs in, `crewline task activate`, and only then the newest open manifest."""
    env = os.environ.get("CREWLINE_TASK_MANIFEST", "").strip()
    if env:
        return (Path(env), "env") if Path(env).is_file() else (None, "env")
    root = Path(tasks_dir) if tasks_dir else tasks_root(cwd)
    if not root.is_dir():
        return None, ""
    start = Path(cwd or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    top = _git(start if start.is_dir() else start.parent, "rev-parse", "--show-toplevel")
    if top and _task_worktree(Path(top)):
        name = Path(top).name
        for task in (name, name.removesuffix("-base")):
            p = root / task / "task-manifest.json"
            if p.is_file() and _open_manifest(p):
                return p, "worktree"
    try:
        chosen = active_file(root).read_text(encoding="utf-8").strip()
    except OSError:
        chosen = ""
    if chosen:
        p = root / chosen / "task-manifest.json"
        if p.is_file() and _open_manifest(p):
            return p, "active-task"
    found = [(p.stat().st_mtime, p) for p in root.glob("*/task-manifest.json") if _open_manifest(p)]
    return (max(found)[1], "newest") if found else (None, "")
