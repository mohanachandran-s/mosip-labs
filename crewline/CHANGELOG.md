# Crewline changelog

Every change that ships to users raises the version in `.claude-plugin/plugin.json` and `VERSION` (CI: `crewline version`),
because Claude Code updates an installed plugin only when that version changes. A task records the version it started on
(`crewline_version`); when a session runs a newer Crewline, the agents read the entries in between.

- **PATCH**: a fix; no rule or gate becomes stricter.
- **MINOR**: a new or stricter rule, gate, validator or agent. Tasks in progress continue; the agent tells the user what
  changed for them.
- **MAJOR**: a change that can break a task in progress (state machine, manifest schema). Finish or restart open tasks.

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
