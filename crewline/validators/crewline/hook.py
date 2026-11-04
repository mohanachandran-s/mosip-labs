"""Platform hook protocol: stdin JSON -> stdout JSON (Cursor / Claude) or git exit codes.

Used by adapters/hooks/crewline_hook.py (CORE baked in at install).
Cursor: {permission, user_message, agent_message} and always exit 0.
Claude Code: hookSpecificOutput.permissionDecision and always exit 0.
Git: print findings to stderr, exit 0 PASS / 1 FAIL.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import session, version, workspace
from .guard import check_read, check_shell, check_write, find_active_manifest
from .result import BLOCK

SESSION_CONTEXT = (
    "Crewline hooks are active. git commit / push / gh pr create are denied until the "
    "active task's COMMIT_PR / PR_CLOSEOUT gates pass (python -m crewline guard). "
    "Force-push, hard reset, stash, branch delete, PR merge and production deploy are always denied. "
    "Do not work around a deny. Create or update tasks/<id>/task-manifest.json and run "
    "`python -m crewline manifest --to STATE --apply`."
)


def _permission(res, *, ask_on_fail: bool = False) -> dict:
    blocking = [f for f in res.findings if f.severity == BLOCK and not f.approved_by]
    if not blocking and res.status != "NOT_RUN":
        return {"permission": "allow"}
    lines = [f"{f.rule_id}: {f.message}" for f in blocking] or [res.summary]
    msg = "Crewline guard: " + "; ".join(lines)
    if ask_on_fail:
        return {"permission": "ask", "user_message": msg, "agent_message": msg}
    return {"permission": "deny", "user_message": msg, "agent_message": msg}


def to_claude(cursor_out: dict, event: str) -> dict:
    if "additional_context" in cursor_out:
        return {"hookSpecificOutput": {
            "hookEventName": event or "SessionStart",
            "additionalContext": cursor_out["additional_context"],
        }}
    perm = cursor_out.get("permission", "allow")
    out = {"hookSpecificOutput": {
        "hookEventName": event or "PreToolUse",
        "permissionDecision": perm,
    }}
    if perm != "allow":
        out["hookSpecificOutput"]["permissionDecisionReason"] = cursor_out.get("agent_message") or cursor_out.get("user_message") or ""
    return out


def _path_from(payload: dict) -> str:
    inp = payload.get("tool_input") or payload.get("input") or {}
    if isinstance(inp, dict):
        return str(inp.get("path") or inp.get("file_path") or inp.get("filePath") or "")
    return str(payload.get("path") or "")


def _command_from(payload: dict) -> str:
    inp = payload.get("tool_input") or payload.get("input") or {}
    if isinstance(inp, dict) and inp.get("command"):
        return str(inp["command"])
    return str(payload.get("command") or payload.get("command_line") or "")


def _cwd_from(payload: dict) -> str | None:
    cwd = payload.get("cwd") or payload.get("workspace_roots", [None])[0]
    if isinstance(cwd, list):
        return cwd[0] if cwd else None
    return cwd


PLUGIN_RAILS = (
    "Crewline hooks are active: git commit / push / gh pr create are denied until the active task's COMMIT_PR / "
    "PR_CLOSEOUT gates pass; force-push, --no-verify, hard reset, stash, branch delete, PR merge and production deploy "
    "are always denied. Do not work around a deny. Task records: {tasks} (create with `crewline task new`, move with "
    "`crewline manifest <m> --to STATE --apply`)."
)


def handle_plugin(mode: str, payload: dict) -> dict:
    """Plugin hooks run in every repository where the plugin is enabled: rails apply only where `.crewline/` exists."""
    cwd = _cwd_from(payload)
    if workspace.disabled():
        return {"additional_context": ""} if mode == "session" else {"permission": "allow"}
    active = workspace.module_top(cwd) is not None
    if mode == "session":
        text = session.context(cwd) or ""
        if active:
            text += " " + PLUGIN_RAILS.format(tasks=workspace.tasks_root(cwd))
            now = version.current()
            text += f" Crewline {now}."
            manifest, why = workspace.active_manifest(cwd=cwd)
            if manifest:
                text += f" Active task: {manifest} (chosen by {why}; switch with `crewline task activate --id <id>`)."
                try:
                    started = json.loads(manifest.read_text(encoding="utf-8")).get("crewline_version")
                except (OSError, ValueError):
                    started = None
                if started and started != now:
                    text += (f" That task started on Crewline {started}: read CHANGELOG.md ({started} -> {now}) "
                             "and tell the user if a change affects it.")
        return {"additional_context": text.strip()}
    if not active:
        return {"permission": "allow"}
    return handle(mode, payload, cwd=cwd)


def handle(mode: str, payload: dict, cwd: str | None = None) -> dict:
    manifest = str(find_active_manifest(cwd=cwd) or "") or None
    if mode == "session":
        extra = SESSION_CONTEXT
        if manifest:
            extra += f" Active task: {manifest}."
        return {"additional_context": extra}
    if mode == "shell":
        return _permission(check_shell(_command_from(payload), manifest))
    if mode == "write":
        return _permission(check_write(_path_from(payload), manifest, _cwd_from(payload)))
    if mode == "read":
        return _permission(check_read(_path_from(payload), _cwd_from(payload), manifest))
    return {"permission": "allow"}


def _git_commit_command(msg_file: str | None) -> str:
    signed = True
    if msg_file:
        p = Path(msg_file)
        if p.is_file():
            signed = "signed-off-by:" in p.read_text(encoding="utf-8", errors="replace").lower()
    return "git commit -s -m hook" if signed else "git commit -m unsigned"


def git_exit(mode: str, argv: list[str]) -> int:
    manifest = str(find_active_manifest() or "") or None
    if mode == "git-commit":
        msg = argv[0] if argv else None
        if not msg:
            git_dir = Path(".git")
            cand = git_dir / "COMMIT_EDITMSG"
            msg = str(cand) if cand.is_file() else None
        res = check_shell(_git_commit_command(msg), manifest)
    elif mode == "git-push":
        res = check_shell("git push origin HEAD", manifest)
    else:
        return 0
    blocking = [f for f in res.findings if f.severity == BLOCK and not f.approved_by]
    if blocking or res.status in ("FAIL", "NOT_RUN"):
        lines = [f"{f.rule_id}: {f.message}" for f in blocking] or [res.summary]
        sys.stderr.write("Crewline guard: " + "; ".join(lines) + "\n")
        return 1
    return 0


def detect_protocol(payload: dict, flag_claude: bool, flag_cursor: bool = False) -> str:
    if flag_cursor:                                # Cursor payloads also carry hook_event_name
        return "cursor"
    if flag_claude:
        return "claude"
    if payload.get("hook_event_name") or payload.get("tool_name"):
        return "claude"
    return "cursor"


def detect_mode(payload: dict) -> str:
    event = str(payload.get("hook_event_name") or "")
    if event == "SessionStart" or event == "sessionStart":
        return "session"
    tool = str(payload.get("tool_name") or "")
    if tool in ("Write", "Edit", "StrReplace"):
        return "write"
    if tool == "Read":
        return "read"
    if tool == "Bash" or _command_from(payload):
        return "shell"
    if payload.get("tool_input") or payload.get("path"):
        return "write"
    return "session"


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    flag_claude = "--claude" in args
    if flag_claude:
        args.remove("--claude")
    plugin = "--plugin" in args
    if plugin:
        args.remove("--plugin")
    flag_cursor = "--cursor" in args
    if flag_cursor:
        args.remove("--cursor")
    mode = args[0] if args else "auto"
    rest = args[1:]
    if mode in ("git-commit", "git-push"):
        return git_exit(mode, rest)

    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    protocol = detect_protocol(payload, flag_claude, flag_cursor)
    if mode == "auto":
        mode = detect_mode(payload)
    out = handle_plugin(mode, payload) if plugin else handle(mode, payload)
    if out.get("additional_context") == "":
        return 0                                   # nothing to say: print nothing
    if protocol == "cursor" and plugin and out.get("additional_context") and "ACTIVE" in out["additional_context"]:
        out["additional_context"] = (out["additional_context"]
            .replace("use the crewline skill workflow", "use the Crewline workflow (`python .cursor/crewline.py rule`)")
            .replace("in .claude/settings.local.json (\"enabledPlugins\": {\"crewline@mosip-labs\": false})",
                     "in .cursor/crewline.local.json ({\"enabled\": false})")
            .replace("`crewline task new`", "`python .cursor/crewline.py task new`")
            .replace("`crewline manifest ", "`python .cursor/crewline.py manifest "))
        out["additional_context"] += (" Cursor: run `python .cursor/crewline.py rule` before the first change and follow it; "
                                     "validators run as `python .cursor/crewline.py <command>`.")
    event = str(payload.get("hook_event_name") or ("SessionStart" if mode == "session" else "PreToolUse"))
    if protocol == "claude":
        out = to_claude(out, event)
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    return 0
