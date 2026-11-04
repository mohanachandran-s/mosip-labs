# ADR-0009: A task worktree finds the module's context; the context is not copied into it

**Status:** Accepted (0.7.3, mosip/mosip-labs#116)

## Context
Task worktrees are cut from the upstream base, which often has no `.crewline/` and no plugin enable (in inji/pixelpass the
context exists only on the onboarding worktree until that PR merges). On PP-PYSDK the session therefore ran from the PP-ONBOARD
worktree while every edit and test went to the PP-PYSDK worktree, and the hooks guarded a different folder from the one being changed.

## Options
1. Copy `.crewline/` and the plugin enable into each worktree, hidden through `.git/info/exclude`.
2. Let the guard and validators find the module root and context from the clone and the task manifest.

## Decision
Option 2 for the context. The plugin enable is the one file that has to be written: Claude Code reads it from the session's own
folder, and `~/.claude/settings.json` does not enable the plugin for every repository. `crewline task worktree` writes it to the
worktree's `.claude/settings.local.json`, keeping anything the user already set there.

- `workspace.module_top`: a checkout under `<main clone>/.crewline-work/worktrees/` is active once the clone keeps tasks.
- `workspace.context_dir`: this checkout's `.crewline/`, else the one recorded in `workspace.context_dir`, the main clone's, or
  the newest other task worktree's. `session`, `systest`, `coding-standards` and the TestRig profile lookup all use it.
- The task whose worktree the session runs in is the active task, ahead of `.crewline-work/active-task`, so two sessions in two
  worktrees cannot take each other's rails.

## Why not copy
- `.git/info/exclude` is shared by every worktree of the clone. Excluding `.crewline/` there hides new context files in the
  onboarding and knowledge tasks that really do commit them, and `git add` then skips them with no warning.
- Without the exclude, `git add -A` could put a copy of the context into a product PR. `change-scope` would catch it, late.
- A copy is a second source of truth: a knowledge delta written to it is lost when the worktree is removed, and it goes stale
  when the context changes.

## Consequences
- The context must exist somewhere in the clone. If none is found, `task worktree` warns and SessionStart stays silent.
- Removing the worktree that holds the context (before its PR merges) leaves the others without it, until the base has it.
- A worktree made by hand with `git worktree add` gets the plugin enable by running `crewline task worktree --id <id>` on it.
- Cursor: `.cursor/` is per checkout too and is not written here. Open the workspace at a checkout that has it; the validators
  themselves find the context from any worktree.
