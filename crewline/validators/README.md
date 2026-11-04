# Validators (`crewline`)

Deterministic, machine-readable checks. Run from this directory: `python -m crewline <validator> … [--json]`.
Exit: **0 PASS · 1 FAIL · 2 NOT_RUN**. A crash or missing prerequisite is NOT_RUN and never counts as PASS.
Result fields: `validator, status, summary, findings[rule_id, severity BLOCK|WARN|INFO, message, path, line, evidence, remediation, approved_by], inputs`.

| Validator | Rules | Notes |
|---|---|---|
| `coverage` | `COV:<gap-type>` | Runs the team's `CoverageCli check` (authoritative for IDs, verbs, orphans, rows, Story keys, endpoints). Never re-implemented here. |
| `testcase` | NAME-001..004, DEP-001..002, YAML-PARSE | `--scope changed --base <ref>` (default with `--base`) checks only added/modified cases, so existing debt does not block new work |
| `expected-diff` | EXP-001..009 | Base ref vs working tree; BLOCK unless `--approvals` has an attributed record. Refuses (NOT_RUN) when the base shows no YAML |
| `secrets` | SEC-001..005 / SEC-GL | Uses `gitleaks` when on PATH; values are never printed (sha256 fingerprint only) |
| `dco` | DCO-001..002 | Signed-off-by present and matching the author email, per non-merge commit |
| `manifest` | MAN-001..019, TRN-001..003 | Schema + invariants; tracker ticket before any state after INPUT_VALIDATION (MAN-017); one subtask per template kind past GITHUB_PLANNING and closure comments before DONE (MAN-018); `--to <STATE> [--apply]` enforces `core/workflow.yaml` + the task-type template (path projection, template guards, required gates), both repair caps, single-use human approvals, reviewer independence, risk tier, linked-change rules |
| `branch` | BR-001..005 | Fork model: branch cut from the recorded upstream base, only commits whose subject carries a task key (`--story` and/or `--manifest`: tracker.key, story_keys; Jira `PROJ-1`, GitHub `#N` / `owner/repo#N`, word-bounded) (or `--allow`), no merges, PR target = upstream repo/branch |
| `scope` | SCOPE-001..002 | Decides the test scope from the change: TARGETED (only new cases → those + `$ID` producers) or FULL |
| `regression` | REG-001..006 | Base vs branch from TestRig Extent full reports or JUnit/surefire XML: blocks pass→fail and failing new tests without an exception |
| `tools` | TOOL-000..002 | Local capabilities from `core/tools.yaml` + active pack overlay; prints numbered enable steps for anything missing |
| `pack` | PCK-001..002 | Show / `--list` / `--set` the product pack (`mosip` default). `CREWLINE_PACK` overrides for one process |
| `run` / `evidence` | EVD-001..004 | `run` executes the command and records exit code, commit, worktree digest, log, and a CLIP-002 digest; `evidence` rejects missing fields, stale records (code changed since), non-zero exits |
| `clip` | CLIP-001..002 | digest of a command log so the agent never loads the raw file (`clip_max_chars` in `core/usage-budget.yaml`) |
| `change-scope` | SCP-001..003 | Every changed file inside `scope.approved_paths`; CI/build/deploy/config files need an explicit `scope_change` approval |
| `test-integrity` | TIN-001..006 | Unit tests (JUnit 4/5, TestNG; Jest/Vitest/Mocha): removed/disabled/focused tests, fewer assertions, deleted files; `--strict` blocks any test edit (refactor) |
| `migration` | MIG-001..004 | MOSIP upgrade/rollback and release/revoke pairs, Flyway V/U; released scripts immutable; destructive statements flagged |
| `trace` | TRC-001..007 | AC -> code_refs -> test_ids -> result on HEAD; code_refs inside approved paths; API-facing ACs need a passing component test when system tests are N/A |
| `coding-standards` | STD-001..007 | The module's own lint/format checkers (spotless, checkstyle, eslint, prettier) plus no debug leftovers in the production diff. Blocks IMPLEMENTATION_REVIEW. Does not invent a style guide |
| `robustness` | ROB-001..006 | Empty/swallowed catch, raw `throws Exception`, sleep on the hot path; required `implementation.robustness`; every api AC needs a passing error-path test. Blocks IMPLEMENTATION_REVIEW |
| `requirement-clarity` | REQ-001..006 | Story/bug key, numbered observable ACs, `open_ambiguities == 0`, no TBD/should-work phrasing. Blocks IMPACT_ANALYSIS / TESTCASE_DESIGN. Skipped for non-story types |
| `requirement-fit` | RFT-001..005 | AC observables in the diff / code_refs **and** a changed test file (RFT-005). Blocks IMPLEMENTATION_REVIEW |
| `branch-env` | BEV-001..004 | branch-build env (Q8); released-only host refused (BEV-004). Blocks SYSTEM_TEST_EXECUTION |
| `defect-reports` | DEF-001..003 | APPLICATION/SECURITY defects need a secret-scanned `report_path`; values never printed |
| `adapter-check` | ADP-001 | installed Cursor/Claude/generic files match `adapters/` (`--sync` re-renders) |
| `evals` | EVAL-001..005 | `automated_by` tests exist; `--pack frozen` requires `expected-outcomes/` (`--run` executes them) |
| `cold-start` | CS-001..004 | new-machine kit: frozen pack, adapter --sync/--check, synthetic `--apply` |
| `task` | TSK-001..004 | queue over `tasks/*/task-manifest.json` (`list` / `status` / `resume` / `new`). Not a daemon |
| `usage` | USE-001..008 | API-reported tokens per stage/step; `core/usage-budget.yaml` caps (USE-003/004); packet dumps (USE-007/008). Gate `token_budget` |
| `discover` | DSC-001..010 | Module onboarding: reads poms, tests, CI, DB scripts, deploy, guidance files, API TestRig suites and coding-standard checkers read-only; writes evidence-labelled drafts (`.crewline/development/`, `.crewline/qa/`, profile) and a validator baseline |
| `diff-coverage` | DCV-001..005 | JaCoCo XML or lcov.info (Jest/Vitest) + git diff: share of changed executable production lines executed by tests (default 80%); unreported files and stale reports block |
| `guard` | GRD-001..009 | Platform hooks: deny commit/push/PR without an active task and COMMIT_PR gates; always deny force-push, `--no-verify`, hard reset, stash, branch delete, merge, production deploy; deny raw log dumps and unwrapped test suites (GRD-007); deny Read of dumps / files over `read_max_bytes` (GRD-008); `gh pr create` / `gh pr edit --title|--body` without the `<tracker key>: ` title prefix or a body linking the ticket (GRD-009) |
| `task` | TSK-001..010 | queue + `export`/`import` of manifest, packets and evidence JSON (logs omitted); `activate`/`deactivate` pin the active task; `worktree` creates a task worktree a session can run in (ADR-0009) |
| `manifest --next`, `status` | NXT-001..004, STS-001 | every legal move and each precondition with its state; repair attempts left, open approvals |
| `review record`, `approval add`, `limitation add` | REC-001..002 | write records after validating them; a refused write leaves the manifest as it was |
| `ci-coverage` | CIC-001..003 | test suites from `crewline run` records that no `.github/workflows` file runs |
| `resume` | TSK-009 | writes `<task>/HANDOVER.md` from the manifest and makes the task active |
| `gates refresh` | GAT-001..004 | re-runs the gates a command decides and records the result (`--state`, `--all`, `--rerun`); review and human gates are never touched |

Per-module rules: `<module repo>/.crewline/qa/profile.yaml`. Every rule there cites the repository evidence it came from.

First run against pre-registration (2026-10-27, read-only):
`testcase --scope all` → 1 BLOCK (DEP-001, see module known-issues KI-PR-001) · `expected-diff --base HEAD` → PASS (292/292 cases) ·
`secrets` on resources → PASS · `coverage` → 0 gaps, 11 baselined · `dco HEAD~10..HEAD` → 1 sign-off/author mismatch.
