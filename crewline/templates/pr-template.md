<!-- PR title (mandatory, GRD-009 / MAN-017): `{{tracker.key}}: {{summary}}`, e.g. `MOSIP-38854: Handle missing identity name fields in NotificationService` -->
## {{type}}: {{summary}}

Closes / Refs {{tracker.url}} <!-- the task's tracker ticket (MAN-017); omit only when tracker.waived -->

### Scope
- Task type: {{feature | bug-fix | refactor | ...}} · Module: {{...}} · Ticket: {{tracker.key}} {{tracker.url | waived: reason}}
- Files: {{n}}, all inside the approved paths ({{globs}})

### Acceptance criteria -> code -> tests
| AC | Code | Tests | Result |
|---|---|---|---|

### Design
{{link to design / ADR}} · Contract changes: {{none | additive | breaking + consumer plan}} · DB: {{none | upgrade + rollback scripts}}

### Evidence (environment: {{env}}, running build {{commit}})
- Base: `{{upstream_repo}}` `{{base_branch}}` @ `{{base_sha}}` (only {{STORY}} commits)
- Build + unit tests: `{{command}}` -> exit 0 (record {{path}})
- Test integrity: no removed/disabled tests, no weakened assertions {{or approvals}}
- System tests: {{p/f/s}} · Full regression base vs branch: **new breaks {{0}}**
- Reviews: code review {{PASS}}, fresh-context {{PASS}}, security {{PASS | N/A}}
- Linked PRs (dependencies): {{none | owner/repo#N}}

### Rollback
{{revert commit | feature flag | rollback script}}

### Known limitations
- {{known-failing tests with bug keys}}

Signed-off commits: yes (DCO)
