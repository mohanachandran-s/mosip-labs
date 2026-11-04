"""SessionStart hook: tell the assistant whether Crewline applies to the repository the session opened in.

  crewline session-start        (run by hooks/hooks.json; reads CLAUDE_PROJECT_DIR, prints hook JSON, always exits 0)

- `.crewline/` present            -> Crewline is active: follow the crewline skill, read the module's context
- no `.crewline/`                 -> silent: the plugin may be enabled for every repository, so it says nothing and
                                     its rails allow everything there (onboarding starts when the user asks for it)
- CREWLINE=off in the environment -> silent (one-session opt-out)
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import yaml

from . import workspace


def repo_top(start: Path) -> Path:
    r = subprocess.run(["git", "-C", str(start), "rev-parse", "--show-toplevel"], capture_output=True, text=True, encoding="utf-8")
    return Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else start


def context(project_dir: str | None = None, env: dict | None = None) -> str | None:
    env = os.environ if env is None else env
    if str(env.get("CREWLINE", "")).lower() in ("off", "0", "false", "disabled"):
        return None
    top = repo_top(Path(project_dir or env.get("CLAUDE_PROJECT_DIR") or os.getcwd()))
    if workspace.module_top(top) is None:
        return None
    cl = workspace.context_dir(top)
    if cl is None:
        return None
    module, roles = top.name, []
    idx = cl / "module.yaml"
    if idx.is_file():
        try:
            data = yaml.safe_load(idx.read_text(encoding="utf-8")) or {}
            module = data.get("module", module)
            roles = list((data.get("roles") or {}).keys())
        except yaml.YAMLError:
            pass
    roles = roles or sorted(p.name for p in cl.iterdir() if p.is_dir())
    where = ".crewline/" if cl == top / ".crewline" else f"{cl.as_posix()}/ (this is a task worktree; its base has no .crewline/)"
    return (f"Crewline is ACTIVE for this repository (module {module}; context in {where}: {', '.join(roles) or 'no role folders'}). "
            "For any change to this repository (feature, bug fix, refactor, upgrade, migration, test automation, release, docs), "
            "use the crewline skill workflow: classify the task, create the manifest in .crewline-work/tasks/<task-id>/, ask the "
            "upstream base branch before branching, delegate to the Crewline role agents, run the `crewline` validators, and ask "
            f"the user for every commit, push, PR and bug. Read {cl.as_posix()}/README.md first; the repository's own AGENTS.md / "
            "CLAUDE.md win where they are more specific. Questions and read-only explanations need no task. The user can turn "
            "Crewline off for this repository in .claude/settings.local.json (\"enabledPlugins\": {\"crewline@mosip-labs\": false}) "
            "or for one session with CREWLINE=off.")


def run() -> int:
    try:
        text = context()
    except Exception:  # a hook must never break the session
        text = None
    if text:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))
    return 0
