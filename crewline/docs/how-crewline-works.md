# How Crewline works

Crewline runs engineering work through one gated workflow. Specialist agents do the work, deterministic validators decide what a
command can decide, and people approve what leaves the machine. It is active in repositories that carry a `.crewline/` folder.

## What it takes as input
- **The request:** a story, bug or task. Every task links a GitHub issue or Jira item first; if there is none, Crewline drafts one and
  creates it only when you approve, or you waive it with a reason.
- **Requirements:** the story, the design document (or an approved exception), and the OpenAPI contract where an API exists.
- **The module's `.crewline/` folder:** how this repository builds and tests, default paths, known issues, its test rig. It reaches
  the agents at the start of a session and in a small packet per state. A repository's own `CLAUDE.md` or `AGENTS.md` wins where it is more specific.
- **The code that actually runs:** agents read the source and dependencies rather than assume.

## Who acts at each stage

| Stage | Who acts | What comes out | Human part |
|---|---|---|---|
| Intake, input check | orchestrator | task type, risk tier, ticket | confirm or create the ticket |
| Requirements | requirement-analyst | numbered acceptance criteria (ACs), ambiguity list | answer open questions |
| Acceptance review | acceptance-reviewer | testable ACs, contradictions removed | |
| Impact, design | impact-analyst, solution-architect, plus API, database, UX and security specialists where relevant | impact map, options, ADR | approve material design |
| Test design and review | sdet-architect, testcase-designer, testcase-reviewer | tests from the ACs, before code exists | |
| Plan, repo ready | orchestrator | approved file paths, subtasks, branch | choose the base branch |
| Implementation | software-developer, database-engineer | code and unit tests, inside the approved paths | approve a change of scope |
| Validation and unit tests | validators | gate results with evidence | |
| Two code reviews | code-reviewer, then fresh-context-reviewer in a new context | findings | |
| System tests | sdet-architect, api-automation, ui-automation | the module's own suites on the branch build | name the environment |
| Quality gate, commit, PR | orchestrator, security-engineer | signed-off commit, PR | approve commit, push and PR |
| PR closeout | code-reviewer, failure-analyst | CI green, every comment answered | |
| Release, deployment | release-manager, devops-engineer | readiness, rollback plan | sign off; a person runs production |
| Knowledge, report | knowledge-curator, orchestrator | reviewed knowledge update, final report | approve the ticket closure comment |

Each task type walks the part of this path it needs (`core/workflows/`). Incidents and module onboarding take short paths.

## Where ACs, tests and reviews come from
- **ACs:** the requirement-analyst writes them from your story; it asks rather than inventing a business rule. Vague ACs are refused by a validator (`requirement-clarity`).
- **Test cases:** the SDET designs them from the ACs and the design, before the code exists, so the developer cannot shape them to fit the code. API test cases stay in the existing TestRig YAML and coverage matrix.
- **Agent-written tests are reviewed by:** testcase-reviewer (clarity, duplicates, expected results, AC mapping), then both code reviews. `test-integrity` and `expected-diff` block a removed or weakened test or a changed expected result without a person's approval.
- **Independence is checked, not promised:** a reviewer cannot be an author, the two reviews must run in different contexts, and an agent cannot be an approver (MAN-010, MAN-011).

## What the validators enforce
| Enforced by a command | Not enforced |
|---|---|
| State order, required gates, single-use human approvals | Whether a design is good: reviewers and you judge that |
| Every AC traced to code, tests and results on the branch | Correctness beyond what tests and reviews cover |
| Coverage of changed lines, secrets, DCO sign-off, changed files inside approved paths | Business truth of an AC you approved |
| Three repair attempts per track, then escalation | The content of a hand-written evidence record, beyond its required fields |
| Unit, system and regression evidence from `crewline run` | |

**Coding standards:** Crewline does not invent a style guide. It runs the checkers a repository already has (Spotless, Checkstyle, ESLint, Prettier) and requires a passing record. In a repository with none, only debug leftovers are checked. Onboarding then proposes a checker as a separate PR for the module owner, with a mode that checks only the lines a change touches.

## What a person must approve
Commit, push and PR. Creating or waiving a ticket and subtasks, and closure comments. Material design. A change outside the approved paths. A changed expected result. Filing an application bug. Waivers, each with a follow-up or an accepted reason. Leaving an escalation. Any deployment, which is non-production only.

Disabled for everyone: production deployment, force-push, merging a PR, deleting branches.

## Where to go next
[README](../README.md) for install and day-to-day use · [roles](agents-by-role.md) · `core/workflow.yaml` for the exact states and guards.
