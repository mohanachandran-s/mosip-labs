---
name: crewline
description: "Use for MOSIP engineering work across the SDLC: features, bug fixes, refactors, dependency upgrades, DB migrations, incidents, release readiness, and API/UI test automation. Classifies the task, drives one gated workflow (requirements -> design -> tests from ACs -> code -> reviews -> system tests -> signed-off PR to upstream -> release readiness) with deterministic validators and human approvals."
---

# Crewline

Core: `${CLAUDE_PLUGIN_ROOT}` (the installed plugin). This file is a thin adapter. The workflow, templates, gates, permissions and agent contracts live in the core.

**Token fire-break:** after INTAKE do not re-read `principles.md` or every agent file. One subagent, smallest packet
(ACs + FAIL findings). Never paste reports. Never poll. `crewline usage --mark` each step; `crewline usage --manifest <m>`
(finds the transcript itself) at each stage change and after each subagent hand-back. FAIL the budget -> STOP
(continue / split / abort). USE-012 on main -> `/compact` or a new session via `crewline resume`. Many-file batches:
`packets/FACTS.md` first, ≤4 files per designer, ≤2 designers at once, fresh run instead of SendMessage past ~120k.
Every call re-reads ~100k of fixed context: batch independent reads into one shell call. Multi-repo task from a parent
folder: `crewline task new --type <t> --id <id> --repo <primary> --repo <dependency>` (fills `scope.repositories`);
never hand-edit the manifest JSON or read the schema to find fields.

**Search, clip, hand off:** Read / Grep / Glob after `local-context.md` (not `cat`/`find` of the tree). Bulky commands
through `crewline run`; read CLIP-002 or `crewline clip --file <log>`, never the raw log. Hooks deny `cat` of logs and
raw `mvn test` (GRD-007) and Read of dump/oversized files (GRD-008). Developer writes `code_refs` /
`test_ids` / evidence paths on the manifest; QA reads those + `.crewline/qa/` + the packet, not the chat.
Leaving DESIGN needs `packets/DESIGN.md`. Bug-fix IMPLEMENTATION_PLAN needs `evidence/reproduction.json` (failed base run).
IMPLEMENTATION needs a non-empty `scope.approved_paths`. Feature / dependency-upgrade review needs
`evidence/dependency-security.json`. Leaving SYSTEM_TEST_EXECUTION needs `evidence/system-tests[-<suite>].json` runs of
the module's own system suites (`.crewline/module.yaml`: api-test, ui-test; a UI change needs the UI rig: SYS-002/003).
COMMIT_PR needs `crewline regression --out <task>/evidence/full-regression[-<suite>].json` for those suites (REG-007/008).
Leaving REQUIREMENT_ANALYSIS, ACCEPTANCE_REVIEW, IMPACT_ANALYSIS, TESTCASE_DESIGN, TESTCASE_REVIEW needs the specialist's
`packets/<STATE>.md` with `agent:` and `context_id:` (PKT-004).
RTK is optional.

## How to run a task

Before offering a choice read `crewline status`; prepare a move with `crewline manifest <m> --next`; write records with `crewline review
record`, `approval add`, `limitation add`, never by hand. Waivers need a follow-up (MAN-019). Untrusted input needs limit ACs and a threat review before test design.
New session on a paused task: `crewline resume <id>` (never hand-write a hand-over); after a repair `crewline gates refresh
--all [--rerun]` (never a per-task gate script). Several open tasks: `crewline task activate --id <id>`. Work inside the task
worktree: `crewline task worktree --id <id> --base <ref> --branch <name>`, then open the session there.

1. At **INTAKE only**, read `${CLAUDE_PLUGIN_ROOT}/core/principles.md`. Run `crewline pack` and load only that pack's knowledge dirs
   (`packs/<id>/pack.yaml`). MOSIP (default) adds `knowledge/mosip` and `knowledge/frameworks`. Then the target
   repository's `.crewline/development/local-context.md` (staging copy: `<module repo>/.crewline/development/`)
   and, for test work, `.crewline/qa/module-context.md`, `coverage/README.md`, `coverage/AGENTS.md`.
   Repository guidance wins where it is more specific.
2. INTAKE: classify the request into one task type (`${CLAUDE_PLUGIN_ROOT}/core/workflows/`: feature, bug-fix, refactor,
   dependency-upgrade, database-migration, incident, release, test-automation) and its risk tier. Create the task manifest
   (`${CLAUDE_PLUGIN_ROOT}/templates/task-manifest.schema.json`) in the task workspace `<clone>/.crewline-work/tasks/<id>/`, never in a repository.
   **Tracker ticket before design or code** (INPUT_VALIDATION, every task; MAN-017): link the user-confirmed GitHub
   issue / Jira item, or draft one and create it in the system the user picks only on approval `create_tracker_item`,
   or record their waiver. Commit subjects carry `tracker.key`; the PR title starts with it
   (`<KEY>: <summary>`, GRD-009); the PR body links the ticket (GRD-009). GITHUB_PLANNING:
   one subtask per template kind under the ticket, created on approval `create_tracker_subtasks` or waived (MAN-018).
   Closure comments on the ticket and each subtask on approval `tracker_comment` before DONE (orchestrator step 2b).
3. S0_BOOTSTRAP_CHECK: `crewline tools` (from `${CLAUDE_PLUGIN_ROOT}/validators`) plus any connector/browser the task type needs.
   Missing -> stop and give the user the numbered steps from the merged tools (`core/tools.yaml` + active pack);
   never work around it.
   An action the platform refuses at runtime (e.g. `npm ci`) -> stop and offer the `permission_gated` options in
   `${CLAUDE_PLUGIN_ROOT}/core/tools.yaml` (you run it / narrowest project allow rule / leave unverified); record approval `tool_permission`.
4. Drive `${CLAUDE_PLUGIN_ROOT}/core/workflow.yaml` through the template's path. Before each transition:
   `crewline manifest <manifest> --to <STATE>`; move with `--apply`. Never move on a non-zero exit.
5. Delegate each state to the agent contract in `${CLAUDE_PLUGIN_ROOT}/agents/` (SDET specialty in `agents/qa/`) with a minimal
   context packet. Acceptance tests are designed from the ACs before the code. Review 2 runs in a fresh context.
   Record authors and reviewers in the manifest.
6. Fork model: ask the upstream base branch before creating any branch; branch from it; only task changes; push to `origin`;
   (to add work to an existing PR the user names: work on that PR's head branch, allow its existing commits, push to it - orchestrator step 4b);
   PR against the upstream branch (`crewline branch`). Linked repos each get their own branch, gates and PR, dependency first.
7. Evidence: run builds and tests through `crewline run --repo <dir> --out <record> -- <command>`; gates use
   `crewline evidence`, `change-scope`, `test-integrity`, `migration`, `trace`, `coding-standards`, `robustness`, `requirement-clarity`, `requirement-fit`, `branch-env`, `defect-reports`, `adapter-check`, `evals`, `regression` (TestRig reports or surefire XML),
   plus (MOSIP pack) the SDET validators (`testcase`, `expected-diff`, `scope`, `coverage`) and `secrets` / `dco`.
   Generic pack: skip TestRig CLIs (they return NOT_RUN); use the module's own suite.
8. Ask before every action `${CLAUDE_PLUGIN_ROOT}/core/permissions.yaml` marks as needing approval; record it (approver, reason, scope,
   timestamp). Approvals are human and single-use.
9. Failure triage: AUTOMATION -> SDET loop, CODE_UNDER_CHANGE -> developer loop (3 each); APPLICATION / SECURITY /
   SPECIFICATION escalate. Bugs carry the secret-scanned run report.
10. Finish with `${CLAUDE_PLUGIN_ROOT}/templates/final-report.md`. SUCCESS only when every gate the template requires has evidence.

## Claude Code specifics

- Specialists are installed as subagents named `crewline-<agent>` (`crewline-software-developer`, `crewline-sdet-architect`, ...).
  Give each the smallest context packet; use a NEW subagent for the fresh-context review and record its id as `context_id`.
- Use a git worktree per task and repository; never edit the user's main checkout.
- Credentials: never read their values into context; merge them into the worktree config, mark it skip-worktree, restore after.
- Long runs (full suites, builds) go in the background; gates read the `crewline run` record, not the console.
- Fail-closed hooks (plugin `hooks/hooks.json` in repositories with `.crewline/`, or `.claude/settings.json` plus
  `permissions.deny` for a manual install; PreToolUse + SessionStart) call
  `crewline guard`. They deny git commit / push / `gh pr create` until COMMIT_PR / PR_CLOSEOUT gates
  pass; they always deny force-push, `--no-verify`, hard reset, stash, branch delete, PR merge and production
  deploy. A hook deny is not a prompt to work around — fix the gate. Subagent tool allow-lists still apply.

## Rules

Never: run a second workflow or let a specialist move state; approve or review your own change; weaken, disable or delete
a test or expected result to pass; exceed three repair attempts per track; change files outside `scope.approved_paths`
without a `scope_change` approval; deploy to production; file an application bug without explicit confirmation; publish
raw run reports or logs; touch uncommitted work that is not yours; paste a result a command should produce; disable or
skip Crewline hooks (`disableAllHooks`, `--no-verify`, wrapping Bash); re-read core after INTAKE; dump a full report
into the chat; poll a long command or PR check; continue past a failed `token_budget`.

## Where Crewline is active and where work goes

- Active only in repositories with a `.crewline/` folder (the SessionStart hook says so at the start of a session), or
  where the user explicitly asks for Crewline. Elsewhere, behave as usual.
- Read the module's `.crewline/README.md` and `module.yaml` first, then the role folder for the work (`development/`, `qa/`, ...).
- Task workspace: `<clone>/.crewline-work/tasks/<task-id>/` (manifest, evidence, usage, reports); worktrees:
  `<clone>/.crewline-work/worktrees/<task-id>`. Add `.crewline-work/` to `<clone>/.git/info/exclude` on first use; never commit it.
- Validators: run `crewline <command>` (on PATH while the plugin is enabled), e.g. `crewline manifest <manifest> --to <STATE>`.
- Record `crewline_version` (`crewline version`) in the manifest at INTAKE (`crewline task new` does it). If MAN-016 or the
  session context reports a newer Crewline, read CHANGELOG.md between the two versions and tell the user what changes for
  the task before the next transition.
