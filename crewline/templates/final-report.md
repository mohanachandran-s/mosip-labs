# Crewline task report - {{task_id}}

**Outcome:** SUCCESS | PARTIAL | BLOCKED | ESCALATED | FAILED. {{one-sentence reason}}

| | |
|---|---|
| Task type / risk tier | {{feature}} / {{L2}} (template core/workflows/{{feature}}.yaml) |
| Story | {{MOSIP-XXXXX}} |
| Module / track | {{pre-registration}} / {{SERVICE + API}} |
| Upstream / base -> head | {{upstream_repo}} `{{base_branch}}` @ {{base_sha}} -> {{head_sha}} (branch {{branch}} on {{origin}}) |
| Linked repos | {{none | repo: branch -> PR, version_link}} |
| Authors / reviewers | {{authors}} / review 1 {{ctx}}, review 2 {{ctx}} (independent) |

## Traceability
| AC | Code refs | Test ids (unit / TestRig / matrix row) | Result on HEAD |
|---|---|---|---|
| AC-1 | | | PASS |

Change scope: {{n}} files, all inside approved paths | scope_change approvals: {{none | list}}

## Gates
| Gate | Status | Evidence |
|---|---|---|
| requirement_clarity / acceptance_readiness / requirement_fit | | |
| design_readiness (+ approval design) | | |
| testcase_readiness / reproduction | | |
| build | | `crewline run` record |
| deterministic_validation / coding_standards / robustness / test_integrity / migration_pairing | | |
| unit_tests | | |
| implementation_review / fresh_context_review | | |
| system_tests / branch_environment | | report header commit/branch; env kind |
| defect_reports | | secret-scanned report_path |
| full_regression (base vs branch) | | new breaks: |
| traceability / change_scope / secrets / dependency_security | | |
| dco / branch_scope | | |
| release_readiness / post_deployment | | |

## Execution
`{{exact command}}`. Exit {{code}}. Env {{env}} (runs build {{commit}}). Passed/failed/skipped {{p}}/{{f}}/{{s}}. Record: {{path}}

## Repair history
SDET loop: {{attempts}} · Development loop: {{attempts}} · Escalations: {{reason or none}}

## GitHub / Jira
Ticket {{tracker.system}} {{tracker.key}} ({{linked | created by Crewline on approval | waived: reason}}) · closure comment {{posted link | declined: reason}} · PR {{link}} · review comments {{valid fixed / not valid replied}} · Bugs: {{key}} with {{error report, excerpt}}

## Release
{{NOT_APPLICABLE | readiness, rollback plan, environment, deployment record, post-deployment checks}}

## Token usage
{{overall calls / fresh / total tokens}} vs `core/usage-budget.yaml` — per stage and per step in {{usage.md}}
(`crewline usage`; API-reported, not estimated). Over budget is FAIL, not a footnote.

## Knowledge
{{entries added/updated with status}}, or why none was warranted.

## Residual risks and next steps
- {{unresolved failures, Inferred assumptions, approvals still required}}
