# Crewline changelog

Every change that ships to users raises the version in `.claude-plugin/plugin.json` and `VERSION` (CI: `crewline version`),
because Claude Code updates an installed plugin only when that version changes. A task records the version it started on
(`crewline_version`); when a session runs a newer Crewline, the agents read the entries in between.

- **PATCH**: a fix; no rule or gate becomes stricter.
- **MINOR**: a new or stricter rule, gate, validator or agent. Tasks in progress continue; the agent tells the user what
  changed for them.
- **MAJOR**: a change that can break a task in progress (state machine, manifest schema). Finish or restart open tasks.

## 0.7.4
The rest of what PP-PYSDK found. Stricter rules for tasks in progress: REQ-005/006, PKT-005, MAN-019, CIC-001; DCV-005 can block.
Closes mosip/mosip-labs#117, #118, #119, #120, #121, #122.
- **#117:** `crewline manifest <m> --next` lists every legal move with each precondition and its state. `crewline status` shows state, repair
  attempts used and left, legal moves, open approvals; the orchestrator reads it before offering a choice. `crewline review record`, `approval add`
  and `limitation add` validate first and write nothing when refused. The repair cap and WAIVED rules are unchanged, now stated up front.
- **#118:** `diff-coverage` finds changed source from the diff (Java, Kotlin, Python, JS/TS, Go; not tests), with no default Java filter, and matches
  coverage.py paths relative to their source root. 0 lines measured is never a silent pass (DCV-005). Python, Kotlin and Go test paths count as tests (RFT-005).
  A mixed diff now needs coverage for every language in it, or `--include` to narrow it.
- **#119:** `requirements.untrusted_input`: an AC that reads like parsing untrusted input gets the question asked (REQ-006). True needs ACs tagged
  `resource_limits` for input size, nesting depth, memory, time and error behaviour (REQ-005), and `packets/THREAT_REVIEW.md` by the security-engineer before
  test design (PKT-005). The optional parallel reviews are not built.
- **#120:** STD-004 says plainly that only debug leftovers were checked. `discover` drafts `standards-proposal.md` when a repository has no checker
  (DSC-010), for the module owner. `--changed-lines` or `standards: {mode: changed-lines}` blocks only violations on lines the change touched (STD-006/007); a log with no
  `path:line` still blocks. The optional org-wide profile is not built.
- **#121:** by FINAL_REPORT every known limitation and gate exception links a follow-up or an accepted reason (MAN-019); token_budget exceptions are exempt. `crewline ci-coverage` compares the suites in `crewline run` records with `.github/workflows` (CIC-001 unit or component BLOCK, CIC-002 system WARN), on the move to FINAL_REPORT, accepted only through a human exception on gate `ci_coverage`. It reads workflow files rather than PR check names, because a check name does not say what ran, so it is a heuristic: tool family and folder in one workflow file.
- **#122:** [docs/how-crewline-works.md](docs/how-crewline-works.md), linked from the top of the README.
- Eval EV-66..70.

## 0.7.3
Found running PP-PYSDK on 0.7.0: 62M tokens, a hand-written hand-over, task-specific gate scripts, an active task that switched when
a file changed, and a session that had to sit in one worktree while editing another. Closes mosip/mosip-labs#114, #115, #116.
- `crewline resume [<id>]` writes `<task>/HANDOVER.md` from the manifest (state, resume_state, what blocks the next move, gates not PASS or
  PASS on another commit, repair attempts, approvals, token use, next commands) and makes the task active.
- `crewline gates refresh [--id] [--state S | --all] [--rerun]` re-runs the gates a command decides and records the result with commit and
  evidence (`evidence/gates/<gate>.json`). Evidence gates re-check their `crewline run` records; `--rerun` runs builds and tests again, never
  system tests. Review and human gates and a recorded NOT_APPLICABLE are not touched; a gate that needs a report path says what to run.
  A module with `api-test/` gets `deterministic_validation` NOT_RUN: run `testcase`, `expected-diff` and `coverage` yourself.
- `crewline task activate --id <id>` / `deactivate` pin the active task in `.crewline-work/active-task`. Order: `CREWLINE_TASK_MANIFEST`, the task
  whose worktree the session runs in, the file, then the newest open manifest. `task list` marks it; every guard deny names it (hook messages change).
- GRD-004 no longer applies to session scratchpads and temp folders; a checkout in a temp folder or the session's own folder stays guarded.
- `crewline task worktree --id <id> [--base <ref> --branch <name>]` creates the task worktree (needs approval `base_branch`) and enables the
  plugin in it. A session can now run in the task worktree: every hook and validator finds the task and the module's `.crewline/` from the
  clone and manifest; the context is not copied (ADR-0009). This also fixes SYS-002/003 silently seeing no suites when
  `scope.repositories[].path` was a task worktree without `.crewline/`.
- `crewline task` commands find the clone's task list from a main clone with no `.crewline/` at its top.
- Not built from #114: a cheaper model per role is documented, not set, because no evaluation has measured it. Token use per state and the
  budget warning already exist (`crewline usage`, `core/usage-budget.yaml`).
- Eval EV-63..65, ADR-0009.

## 0.7.2
Closes the GitHub work-tracking gaps against the SDET plan (section 7.5, 7.9): subtasks, their closure comments, and a PR
body that links the ticket.
- GITHUB_PLANNING: on templates that walk it, every later state needs one `tracker.subtasks[]` entry per kind the template
  lists (`subtasks`; default `core/workflow.yaml` `subtask_kinds`: testcase, implementation, testing, review, pr;
  test-automation: automation instead of implementation; documentation: implementation, review, pr), keyed in the same
  system as the ticket (GitHub sub-issue / Jira sub-task), or a human `tracker.subtasks_waived` (MAN-018). Like the ticket
  rule, tasks already past GITHUB_PLANNING are stopped at their next move until they link, create or waive subtasks.
- Creating subtasks needs the new single-use approval `create_tracker_subtasks` (replaces `create_github_issue_subtasks`,
  which had no enforcement). `templates/subtask-template.md` titles them `<KEY>: <kind> - <title>`.
- FINAL_REPORT -> DONE: every subtask's closure comment is posted on approval `tracker_comment` or declined (MAN-018).
- GRD-009 also checks the PR body: `gh pr create` needs `--body` / a readable `--body-file` that links the ticket (URL or
  key); `gh pr edit --body|--body-file` is checked too. `--fill` and `--body-file -` cannot be checked and are refused.
- Eval EV-62.

## 0.7.1
Every task has a tracker ticket before any design or code. Found on inji/pixelpass PP-PYSDK and PP-PYSDK-HARDEN:
GITHUB_PLANNING was a pass-through (8 seconds, `github.issue: null`) and Crewline never looked for or created a Jira item.
- New `tracker` block on the manifest (`system` github|jira, `key`, `url`, `title`, `issue_type`, `found_by`,
  `created_by_crewline`, `confirmed_by`, `linked_at`, `waived`, `closure`). `crewline task new` writes it empty.
- `core/workflow.yaml` `tracker_guard`: every move into a state after INPUT_VALIDATION, in every template, needs a key
  the user confirmed or a human waiver (MAN-017). It also applies to a WAITING_FOR_USER resume and to tasks already
  past INPUT_VALIDATION, which are stopped at their next move until they link or waive.
- INPUT_VALIDATION (orchestrator step 2b): find a ticket (request, branch, commits, `gh issue list --search`, Jira
  search) and ask the user to confirm it; else ask GitHub or Jira (project, type story/bug/task), draft it from the
  brief and ACs, create it only on the new single-use approval `create_tracker_item` (L2); or record the user's waiver.
  New permissions: `link_tracker_item`, `create_tracker_item`, `waive_tracker_item`, `comment_on_tracker_item`.
- MAN-017 also checks the record: key format per system (`owner/repo#N` / `#N`, `PROJ-123`), `created_by_crewline`
  without the approval, both a key and a waiver, a posted closure comment without approval `tracker_comment`.
  An agent as `confirmed_by` or waiver approver is MAN-010; a waiver without approver/reason/scope/timestamp is MAN-005.
- FINAL_REPORT -> DONE: the closure comment on the ticket is posted on approval or declined (`tracker.closure`).
- `crewline branch`: `--manifest` reads `tracker.key` + `requirements.story_keys`; `--story` is repeatable and takes a
  GitHub `#N` / `owner/repo#N`. Keys are word-bounded: `MOSIP-1` no longer matches a `MOSIP-17633` subject.
- `requirement-clarity` REQ-004 accepts `tracker.key` as the story/bug identity.
- `core/tools.yaml`: generic `jira_connector` (Atlassian MCP connector) and `github_cli` needed at INPUT_VALIDATION;
  the MOSIP pack keeps its site-specific `jira_connector`. A missing tool for the chosen system stops with the enable steps.
- PR title starts with the tracker key, `<KEY>: <summary>` (as mosip/pre-registration#1089:
  `MOSIP-38854: Handle missing identity name fields in NotificationService`; GitHub `#185: ...` or `owner/repo#185: ...`).
  The hook refuses `gh pr create` / `gh pr edit --title` otherwise (GRD-009, skipped when the ticket was waived);
  `pr.title` is recorded and checked before leaving PR_CLOSEOUT (MAN-017). PR template body links the ticket. Final report: ticket and closure comment. Eval EV-60.
- Version: this adds a gate, which the policy above calls MINOR; it ships as 0.7.1 because 0.7.0 is still unmerged
  (fork PR #112) and the user chose to keep it on that PR.

Known gaps found on the same run, not changed here:
- The 3-attempt repair cap cannot be lifted by a human approval; only S_ESCALATE is legal. Nothing says so plainly.
- The FRESH_CONTEXT_REVIEW entry guard refuses an implementation review recorded as `WAIVED`.
- `diff-coverage`'s default `--include` is Java-only.
- RFT-005 test globs miss Python tests.
- `crewline manifest --to` prints "0 blocking" on a FAIL: `check()` sets the summary and `transition()` keeps it.
- Plugin hooks judge a write against the active task of the session's folder, not the repository the file is in (an
  edit to another repo is refused as GRD-004 of an unrelated task), and do not see files written from a Bash script.

## 0.7.0
System tests and regression must be the module's own suites, and specialist states need their specialist. Found on
INJIVERIFY-2464: a bug-fix passed system_tests with a hand-made browser check, full_regression with a jest run, and its
requirement and impact states in seconds without their agents.
- Leaving SYSTEM_TEST_EXECUTION: every `evidence/system-tests[-<suite>].json` is a full `crewline run` record (SYS-001),
  one ran inside (`cwd`) a system suite the module declares in `.crewline/module.yaml` (`stack.api_test`,
  `ui_test_projects`, `system_test_projects`) (SYS-002), and a change to a `stack.node_projects` UI project (`.` = repo
  root) has a run inside a UI rig (SYS-003). A command that only names a suite does not count. Non-zero exit: WARN SYS-005.
  Modules that declare none: INFO SYS-004, as before.
- Code task types, QUALITY_GATE -> COMMIT_PR: a PASS `evidence/full-regression[-<suite>].json` written by the new
  `crewline regression --out`, which stamps both report hashes, the commit and the worktree digest; refused when
  hand-written (no stamps), comparing a report with itself, or stale: another HEAD, other uncommitted changes, or a report
  that changed since (REG-007). One record's candidate report is inside a declared
  system suite (REG-008).
- `regression` reads Cucumber JSON (`cucumber.json`), so UI rigs feed full_regression.
- Code task types leaving REQUIREMENT_ANALYSIS, ACCEPTANCE_REVIEW, IMPACT_ANALYSIS, TESTCASE_DESIGN or TESTCASE_REVIEW:
  `packets/<STATE>.md` with `agent:` naming a Crewline agent other than the orchestrator, and `context_id:` (PKT-001/004).
  `cold-start` proves the refusal.
- `ui-automation` is an active contract on the module's own UI rig and its `.crewline/qa/` context (no longer "Not in the
  MVP"). `sdet-architect`, `orchestrator`, the skill and the Cursor rule name the new evidence.
- Guard: a write outside the hook's cwd matches `approved_paths` from the file's git top-level, not its bare name (GRD-004
  refused an approved `.crewline/...` path while the session was in `ui-test/`).
- Tasks in progress: a code task past these states continues; at its next transition it needs the new packets/records.
  Regression records from 0.6.0 have no stamps: rerun `crewline regression --out`.
  The worktree digest counts untracked files: keep the task folder outside the candidate repo or git-ignored there
  (`.crewline-work/` in `.git/info/exclude`), or every later write makes the record stale. It covers only the checkout
  that holds the candidate report; when the rig and the changed code are in different repos, rerun after code changes.
- Still honour-system: a record with every field, or a packet naming an agent that never ran, can be typed by hand.
  These rules make a skipped suite or state an explicit act that review can see; they do not prove that a run happened.

## 0.6.0
- P3: feature and database-migration `TESTCASE_DESIGN` requires `design_readiness` (tests from the approved design).
  `cold-start` proves the refuse-then-accept. Human walk still `evals/cold-start.md`.
- `crewline start --id` is a **conductor** (next `--to`, packet hint, transition check). It does not walk the crew.
- `crewline clip -- <command>` runs noisy bash and returns CLIP-002 (remaining RTK-shaped hole).
- test-integrity: pytest and Go (`test_*.py`, `*_test.go`). diff-coverage reads cobertura `coverage.xml`.
- Q8 cluster request template in `evals/q8-local-stack.md`. Q9 answered: mosip-labs + marketplace + Cursor bootstrap.
- This repo carries `.crewline/` so Crewline is an onboarded module.
- Cursor meter: `manifest --apply` stamps a `usage_mark` per transition; `crewline usage` FAILs USE-009 if a transition has no mark.
- Leaving DESIGN needs `packets/DESIGN.md` (PKT-001). Bug-fix IMPLEMENTATION_PLAN needs `evidence/reproduction.json` with exit_code != 0 (REP-001/002). The gate stamp is not enough.
- Docs: `crewline start` is a conductor; `ci/crewline.yml` is the spec (live `.github/workflows/` is not claimed).
- IMPLEMENTATION needs a non-empty `scope.approved_paths` (plan stamp is not a file list). Feature / dependency-upgrade review needs `evidence/dependency-security.json` (DEPSEC-001/002). `cold-start` checks `ci/crewline.yml` jobs (CS-005).
- Leaving SYSTEM_TEST_EXECUTION needs `evidence/system-tests.json` (SYS-001). The gate stamp is not a run.

## 0.5.0
- Honor-system holes closed: hooks deny `cat`/`type` of logs and reports and raw `mvn test`/`npm test`/`pytest`
  (GRD-007); Read of dump files or files over `read_max_bytes` (GRD-008). Cursor + Claude PreToolUse matcher `Read`.
- `manifest --to IMPLEMENTATION_REVIEW` / `FRESH_CONTEXT_REVIEW` / `SYSTEM_TEST_EXECUTION` (from review 2) needs
  `packets/<state>.md` (PKT-001..003). Fresh-context reviewer context ids must differ.
- `crewline task export|import` — portable task memory (manifest, packets, evidence JSON; logs stay behind).
- Q4 / Q5 recorded (ADR template; CodeQL + `crewline secrets` + module-named dependency scanner).
- Repo-root `.claude-plugin/marketplace.json` so the GitHub marketplace install resolves `./crewline`.

## 0.4.0
- Agent behaviour, not only the README: adapters + orchestrator + software-developer + sdet-architect now state
  the search / clip / hand-off loop (Read/Grep/Glob after `local-context.md`; `code_refs` on the manifest for QA).
- `crewline clip` (CLIP-001/002) digests a command log under `clip_max_chars`. `crewline run` prints the same digest.
  That is the Crewline-owned equivalent of RTK for builds/tests; RTK stays optional for other bash.

## 0.3.0
- Cursor without a Crewline clone: a module commits `templates/module-cursor/` as `.cursor/crewline.py`,
  `.cursor/hooks.json` and `.cursor/rules/crewline.mdc`. The bootstrap caches Crewline under `~/.crewline/` (refreshed
  at most daily from mosip-labs `develop`), runs the same plugin-mode hooks (`crewline hook --cursor --plugin`), and
  serves the live rule (`python .cursor/crewline.py rule`). Offline: it never blocks; a broken core asks the user.
- `crewline hook --cursor`: Cursor payloads also carry `hook_event_name`, so the protocol is no longer guessed.

## 0.2.1
- Fix: the plugin did not load when installed from GitHub ("Failed to load marketplace ... cache-miss"). The marketplace
  catalog moves to the mosip-labs root (`.claude-plugin/marketplace.json`, plugin source `./crewline`); a relative source
  resolves against the repository root, so `./` installed the whole repository.
- **Modules must refresh `.claude/settings.json`** from `templates/module-claude-settings.json` (no `path`;
  `sparsePaths: [".claude-plugin", "crewline"]`).
- Fix: an install is an enabled install (no `defaultEnabled: false`). A local-scope install wrote that default as
  `false` into `.claude/settings.local.json`, which overrides the module's opt-in, so Crewline stayed off.
- Silent outside repositories with `.crewline/` (no session note); the rails there already allowed everything.
- Knowledge: API TestRig run-configuration template (apitestrig.md §6b, used by the SDET at SYSTEM_TEST_EXECUTION);
  `-Dmodules` for partner-management-services is `partner` (the map said `pmp`).
- README: a machine installs the plugin once (`/plugin` or `claude plugin install crewline@mosip-labs`); Claude Code
  does not offer the install by itself.

## 0.2.0
First plugin release.
- Claude Code plugin with per-module opt-in (`.claude/settings.json`), active only in repositories with `.crewline/`.
- Guard rails in the plugin hooks (ADR-0007): commit / push / PR only after the gates pass; Bash and PowerShell.
- Task records in `<clone>/.crewline-work/tasks/`; each task records `crewline_version` (MAN-016 warns on drift).
- `discover`: long Windows paths, no local paths in drafts, `testcase_rules.id_key_from_input`.
