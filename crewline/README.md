# Crewline

**New here, or showing this to someone?** Read the one page: [How Crewline works](docs/how-crewline-works.md).

*A full engineering crew, one line of work.*

One controlled, evidence-backed engineering workflow for MOSIP across the whole SDLC: requirements, design,
independent tests, code, reviews, system tests, a signed-off PR, release readiness and post-deployment checks.
It is run by one orchestrator with specialist agents, deterministic gates and human approvals.

> **The model proposes; repositories preserve verified knowledge; deterministic controls enforce rules; humans approve.**

Cursor and Claude already write code. Crewline is not a better coder — it is the MOSIP flight checklist and the locks on
commit/push, so a chat cannot pass for a done SDLC. [Why Crewline on Cursor or Claude](#why-crewline-on-cursor-or-claude).

**How you start it:** describe the work in **Cursor** or **Claude Code**. Use the **terminal** for setup, validators and git — `crewline start` is a conductor (next `--to`), not a crew. Details: [How you start it](#how-you-start-it-cursor-claude-or-terminal).

| | |
|---|---|
| Version | `0.8.0` (see `VERSION`) |
| Design | `../AI_Engineering_Agent_Full_SDLC_Implementation_Blueprint_v2.1.html` (authoritative) and `../AI_Engineering_Agent_Full_SDLC_Updated_Plan_v1.3.html` |
| Lineage | Copied from the SDET Architect Assistant 0.2.0. Its proven workflow is the `test-automation` task type and the `agents/qa/` specialty |
| Pilots | **PILOT-001** (test automation): [mosip/pre-registration#1088](https://github.com/mosip/pre-registration/pull/1088) · **PILOT-002** (bug fix MOSIP-38854): [mosip/pre-registration#1089](https://github.com/mosip/pre-registration/pull/1089) |
| Health | 203 validator acceptance tests; evaluation scenarios EV-01..59 (`python -m crewline evals --pack frozen`) |

---

## Why Crewline on Cursor or Claude

You do not need Crewline to *generate* a patch. You need it if the patch has to survive MOSIP review: tests designed from
the story, a fork from upstream, DCO, coverage, and a PR you can defend.

Without it, the same model in Cursor or Claude will typically write code first, invent tests that match that code, commit
from local `develop`, skip a full suite, review its own diff in the same chat, and call it a PR. That is fast. It is also
how PILOT-001’s first branch failed (wrong base, extra commits, DCO, no regression).

| Cursor / Claude alone | Cursor / Claude **with Crewline** |
|---|---|
| One generalist that invents a process each session | One fixed MOSIP path: story → ACs → design → **tests before code** → implementation → two reviews → system tests → gated PR |
| The model says “tests passed” | `crewline run` / `evidence`: a check that did not run is `NOT_RUN`, never `PASS` |
| Tests written after (or from) the code | Independent oracle: SDET designs from ACs; weakening, skipping or deleting a test is blocked |
| Commit / push when the agent feels ready | Hooks deny commit / push / PR until QUALITY_GATE, DCO, scope and secrets pass |
| Same chat reviews its own work | No self-review, no self-approval; second review in a **new** context |
| Branch from whatever is local | Fork from **upstream** after you name the base branch |
| Own breakage filed as a product bug, or a test deleted to go green | `CODE_UNDER_CHANGE` vs `APPLICATION`; repair cap 3; then escalate |
| MOSIP TestRig, CoverageCli and DCO optional | Those are the artifacts. Crewline does not invent a parallel format |

**What it does not claim:** a stronger model, a faster first draft, or a replacement for GitHub CI. It is slower on
purpose. You still answer questions and approve design, commit, push and PR.

**Bottom line:** Cursor and Claude are the engine. Crewline is the MOSIP process plus locks on shipping. If “the agent
opened a PR and it looks fine” is enough, this is overhead. If the goal is *story → test design → automation → bug
raise/fix → a signed-off PR*, it is the difference between a chat and an engineering workflow.

---

## Core vs MOSIP pack

Crewline is a **gated SDLC** (workflow, gates, hooks) with a **MOSIP plugin** as the default pack — not a
language-agnostic coding agent. Keep that default if the selling point is MOSIP engineering. To use the same rails on
another product, swap the pack; do not fork the workflow.

| Layer | What it is | Lives in |
|---|---|---|
| **Core** | State machine, quality gates, permissions, `crewline guard` hooks, adapters, evidence | `core/`, `agents/`, `validators/`, `adapters/` |
| **MOSIP pack** (default) | TestRig, CoverageCli, application map, Java 21 / Maven / Jira / branch-env tools | `packs/mosip/` + `knowledge/mosip`, `knowledge/frameworks` |
| **Generic pack** | No TestRig, no MOSIP map; module `.crewline/` names the stack | `packs/generic/` |
| **Module context** | Build, tests, CI, default paths for *this* repo | `<module repo>/.crewline/` |

```bat
python -m crewline pack              REM show active (committed default: mosip)
python -m crewline pack --list
python -m crewline pack --set generic
set CREWLINE_PACK=generic            REM process override; tests use this, never rewrite ACTIVE
```

```bash
python -m crewline pack --set generic
CREWLINE_PACK=generic python -m crewline tools
```

A new product pack is a copy of `packs/generic/`: set `id`, point `knowledge:` at that product’s map, overlay stack
tools in `tools.yaml`. MOSIP knowledge dirs are **referenced**, not moved (links stay valid). TestRig CLIs
(`testcase`, `expected-diff`, `scope`, `coverage`) return `NOT_RUN` when the pack has `sdet.testrig` /
`coverage_cli` false; fill the same gates with the repo’s own suite (`crewline run`, `regression`, `trace`).
See `knowledge/decisions/ADR-0008-core-vs-product-pack.md`.

---

## Turn Crewline on for a module (no separate clone of Crewline)

Crewline ships as a **Claude Code plugin** from this repository. A module turns it on by committing one settings file; anyone who
clones that module and trusts the folder in Claude Code is offered the plugin, and its SessionStart hook activates the workflow
only in repositories that carry a `.crewline/` folder.

1. **Module (once, by its owner):** commit `.claude/settings.json` with the content of
   [`templates/module-claude-settings.json`](templates/module-claude-settings.json) (merge if the file exists), next to the
   module's reviewed `.crewline/` folder.
2. **Developer:** clone the module and open Claude Code in it (trust the folder). Claude Code does not offer the install
   by itself, so install once:
   - `/plugin` -> Discover -> `crewline` -> Install (if `mosip-labs` is not listed under Marketplaces:
     `/plugin marketplace add mosip/mosip-labs`), or in a terminal `claude plugin install crewline@mosip-labs`;
   - scope: *user* to have it for every module on this machine (outside repositories with `.crewline/` it says nothing and
     allows everything), or *local* for this clone only.
   Restart Claude Code, then just ask *"Add feature X"*: the session starts with *"Crewline is ACTIVE for this repository"*
   and follows the workflow.
3. **Any repository by hand:** `/plugin marketplace add mosip/mosip-labs`, then `/plugin install crewline@mosip-labs`. The
   marketplace catalog is `.claude-plugin/marketplace.json` at the root of mosip-labs; the plugin is this folder.

**On / off per repository, locally:**

| Want | Do |
|---|---|
| Off for me in one module that has it on | `.claude/settings.local.json` in that clone: `{"enabledPlugins": {"crewline@mosip-labs": false}}` (git-ignored, overrides the committed setting) |
| On for me in a repo that does not commit it | nothing to do once installed: it is active wherever `.crewline/` exists |
| Off for one session only | start Claude Code with `CREWLINE=off` in the environment |
| Check | the first lines of a session say whether Crewline is ACTIVE; `/plugin` lists it as enabled or disabled |

**Work stays with the module:** task records go to `<clone>/.crewline-work/tasks/<task-id>/` and worktrees to
`<clone>/.crewline-work/worktrees/<task-id>`, excluded from git through `.git/info/exclude`. Validators run as `crewline <command>`
(the plugin's `bin/` is on the PATH while it is enabled).

**Updates:** Claude Code keeps an installed copy of Crewline and replaces it only when the plugin version changes.
- Turn on auto-update for the `mosip-labs` marketplace in `/plugin` (Marketplaces), or update by hand:
  `/plugin marketplace update mosip-labs`, then update `crewline` in `/plugin`. A running session keeps its version;
  the next session uses the new one (the session context names it: "Crewline x.y.z").
- Every shipped change raises the version (CI runs `crewline version --base`); [CHANGELOG.md](CHANGELOG.md) says what changed.
- A task records the version it started on (`crewline_version`). On a newer Crewline, `crewline manifest` warns (MAN-016)
  and the session context points at the CHANGELOG entries in between; the task's records are untouched.

**Rails come with the plugin (ADR-0007):** in a repository with `.crewline/`, the plugin's PreToolUse hooks (Bash, PowerShell,
Write/Edit) run `crewline guard`: `git commit` / `git push` / `gh pr create` are denied until the active task's gates pass,
and force-push, `--no-verify`, hard reset, stash, branch delete, PR merge and production deploy are always denied. In other
repositories the hooks allow everything; `CREWLINE=off` turns them off for one session.

## Turn Crewline on for a module in Cursor

Cursor has no plugin marketplace, so the module carries a small bootstrap that does the plugin's job. Commit the three
files of [`templates/module-cursor/`](templates/module-cursor/) into the module:

| Template | Goes to | What it does |
|---|---|---|
| `crewline.py` | `.cursor/crewline.py` | caches Crewline under `~/.crewline/` (sparse fetch of mosip-labs `develop`, refreshed at most once a day), runs the hooks and validators, prints the live rule |
| `hooks.json` | `.cursor/hooks.json` (merge with existing hooks) | sessionStart, beforeShellExecution and preToolUse (Write/Edit) through `python .cursor/crewline.py hook ...`, fail-closed |
| `crewline.mdc` | `.cursor/rules/crewline.mdc` | always-on rule: run `python .cursor/crewline.py rule` and follow it |

A developer clones the module and opens it in Cursor; the first session fetches Crewline (needs `git` and Python with
PyYAML) and says *"Crewline is ACTIVE for this repository"*. The rails are the same as in Claude Code, and so are the task
records in `.crewline-work/`.

| Want | Do |
|---|---|
| Off for one session | `CREWLINE=off` |
| Off for this clone | `.cursor/crewline.local.json`: `{"enabled": false}` (kept out of git through `.git/info/exclude`) |
| Test a Crewline branch | same file: `{"source": "https://github.com/<fork>/mosip-labs.git", "ref": "<branch>"}` |
| Update now | `CREWLINE_REFRESH_SECONDS=0 python .cursor/crewline.py where` |

Offline or without git the hooks allow everything and the session says Crewline is unavailable. If the cached Crewline
fails to run (e.g. PyYAML missing), shell and write actions ask the user instead of being blocked.

## Quick start for a new user

1. **Get Crewline** (one time): copy or clone this folder, then run the setup script from inside `crewline/`:

   ```bat
   scripts\setup.bat cursor D:\work\mosip
   ```

   ```bash
   chmod +x scripts/setup.sh
   ./scripts/setup.sh claude ~/work/mosip
   ```

   That installs Python deps, runs the validator tests, prints missing tools (Git, JDK 21, Maven, `gh`, …) with
   numbered enable steps from `core/tools.yaml` plus the active pack (default MOSIP: JDK 21, Maven, …), and writes
   the Cursor or Claude adapter into the folder where you keep MOSIP module clones. Use `generic` instead of
   `cursor`/`claude` for git hooks only.
   `scripts\setup.bat` with no arguments does the Python + tests + tool check and stops (adapter later).
2. **Have the module cloned with two remotes:** `origin` = your fork, `upstream` = the official repo
   (`git remote add upstream https://github.com/<org>/<repo>.git`).
3. **Start the crew in Cursor or Claude Code** (not in the terminal): open that folder and describe the work in one
   message (templates below). If the module has no `<module repo>/.crewline/` yet, the agent onboards it first
   (`module-onboarding`). See [How you start it](#how-you-start-it-cursor-claude-or-terminal).
4. **Answer what it asks**: open questions, the upstream base branch, design approval, and each commit / push / PR / Jira action.
   Watch `.crewline-work/tasks/<id>/` (in the module clone) for the manifest, evidence, `usage.md` and the final report.

**Request templates**
- New change: `Feature for inji-verify: <story key> <what the user should be able to do>. Acceptance: <...>.`
- Bug: `Fix <story key> in <module>: <symptom, how to reproduce>.`
- **Add to an existing PR:** `Feature for inji-verify: <story key> <description>. Push it to the existing PR
  inji/inji-verify#2460 (do not open a new PR).` - the agent then works on that PR's branch (orchestrator step 4b).

## Contents
- [Why Crewline on Cursor or Claude](#why-crewline-on-cursor-or-claude)
- [How you start it (Cursor, Claude or terminal)](#how-you-start-it-cursor-claude-or-terminal)
1. [What it is (and is not)](#1-what-it-is-and-is-not)
2. [Architecture](#2-architecture)
3. [The workflow](#3-the-workflow)
4. [Task types](#4-task-types)
5. [Agents](#5-agents)
6. [Quality gates](#6-quality-gates)
7. [Testing model](#7-testing-model)
8. [Failures, repairs and escalation](#8-failures-repairs-and-escalation)
9. [Git, PR and review comments](#9-git-pr-and-review-comments)
10. [Governance: tiers, approvals, independence](#10-governance-tiers-approvals-independence)
11. [Validators (`crewline`)](#11-validators-crewline)
12. [Evidence and token usage](#12-evidence-and-token-usage)
13. [Task manifest](#13-task-manifest)
14. [Knowledge and module context](#14-knowledge-and-module-context) · [How agents find and share context](#141-how-agents-find-and-share-context)
15. [Adapters and installation](#15-adapters-and-installation)
16. [Running a task, step by step](#16-running-a-task-step-by-step) · [Use it for your module](#use-it-for-your-module)
17. [Repository layout](#17-repository-layout)
18. [Pilots and lessons](#18-pilots-and-lessons)
19. [Status, limits and open questions](#19-status-limits-and-open-questions)

---

## How you start it (Cursor, Claude or terminal)

Crewline is two layers. They are not started the same way.

| Layer | What it is | Where it runs |
|---|---|---|
| **The crew** | Orchestrator + specialist agents (requirements, design, tests, code, reviews, PR) | Inside an **AI coding tool** that loads the adapter |
| **The rails** | `python -m crewline …` validators, `crewline guard`, git hooks | **Terminal** and hooks that fire on commit / push / PR |

There is **no** command that walks Story → tests → PR by itself. `python -m crewline start --id` only prints the next `manifest --to` (conductor). You start the crew by talking to Cursor or Claude Code. The terminal sets up the machine and enforces the gates.

```mermaid
flowchart LR
  T["1 · Terminal once<br/>pip · pytest · crewline tools<br/>install.py --platform …"]
  A["2 · Cursor or Claude Code<br/>describe the story or bug"]
  H["3 · You approve<br/>base branch · design · commit · push · PR"]
  R["4 · Rails<br/>guard + git hooks refuse a skip"]
  T --> A --> H --> R
```

| You open | Starts the crew? | What you do | What is enforced |
|---|---|---|---|
| **Cursor** | **Yes** | Open the folder that has the module clones. One message with the story or bug (templates in Quick start). The installed rule (`.cursor/rules/crewline.mdc`) is the orchestrator. Cursor has no subagents: each specialist contract is played in turn; fresh-context review is a **new chat**. | Fail-closed hooks: commit / push / `gh pr create` denied until COMMIT_PR gates pass. Force-push, `--no-verify`, hard reset, stash, branch delete, PR merge, production deploy always denied. |
| **Claude Code** | **Yes** | Same request in that folder. The skill (`.claude/skills/crewline/`) is the orchestrator; specialists are `crewline-*` subagents. Fresh-context review is a **new subagent**. | Same `crewline guard` policy via `.claude/settings.json` `PreToolUse` + `permissions.deny`. Subagent tool allow-lists still apply. |
| **Another AI tool** | **Yes, if it loads the entry** | Install `--platform generic` (or paste `.crewline/ENTRY.md`). Describe the work the same way. | Git `pre-commit` / `commit-msg` / `pre-push` call `crewline guard`. They cannot see `--no-verify` (git skips hooks); never use it. Prefer Cursor or Claude hooks as well when the tool has them. |
| **Terminal** | **No** | One-time setup and on-demand checks: `pip install`, `pytest`, `python -m crewline tools`, `cold-start`, `task list/status/resume/new/start`, `manifest`, `guard`, `run` / `evidence`. You can also `git commit` yourself. | Validators and git hooks still run. `crewline start` / `task start` is a conductor over `tasks/*/task-manifest.json`; it does not walk the crew. |

**Typical path for a MOSIP story**


1. **Terminal (once per machine / workspace):** from `crewline/`, run `scripts\setup.bat cursor D:\work\mosip`
   or `./scripts/setup.sh claude ~/work/mosip` (Python deps, validator tests, `crewline tools`, adapter).
   Then `python -m crewline cold-start` must PASS. The script does not start the crew.
2. **Cursor or Claude Code:** `Fix MOSIP-38854 in pre-registration: <symptom>.` (or a feature / test-automation template).
3. **You:** answer ambiguities; name the upstream base branch; approve design, commit, push and PR when asked.
4. **Rails:** if the model skips `crewline manifest`, the hook or git hook still refuses the commit or push.

Install **one** platform adapter on the workspace you actually use (Cursor *or* Claude). Add `--platform generic` as well if you want git hooks in the module repo so a commit from any tool (or a human) hits the same rails.

---

## 1. What it is (and is not)

**It is** a repository of rules, contracts and scripts that any AI coding tool (Claude Code, Cursor, others) follows. A task
(a story, a bug, an upgrade) goes through a fixed state machine. At every step it:
- delegates to a specialist agent;
- runs deterministic validators;
- asks a human for every consequential action;
- records evidence that cannot be typed in by the model.

**It is not** a stronger Cursor/Claude, a faster first draft, a code generator that commits on its own, a second
testcase format, or a replacement for the team's CI. See [Why Crewline on Cursor or Claude](#why-crewline-on-cursor-or-claude).
It drives the tools the team already has:
- API TestRig YAML and the coverage matrix (`CoverageCli`);
- Maven, JUnit and JaCoCo;
- the GitHub CI workflows, CodeQL and DCO;
- Jira.

**Design rules that shape everything:**
- **One orchestrator** owns state, permissions, evidence, retries and the final status. Specialists only propose.
- **Deterministic checks decide.** If a script can check a rule, the script decides. A check that didn't run is `NOT_RUN`, never `PASS`.
- **Humans approve.** Approvals are recorded, attributed, single-use, and never granted by an agent.
- **Nothing outside the plan.** A task may change only the file paths its plan approved.

---

## 2. Architecture

### 2.1 Layers

```mermaid
flowchart TB
    U["Engineer / Product owner<br/>asks · answers · approves"]
    ADP["Platform adapter<br/>Claude Code skill + crewline-* subagents · Cursor rule · generic ENTRY.md"]
    subgraph CORE["Crewline core (this repository)"]
        direction LR
        O["Orchestrator<br/>task manifest · state · approvals · retries"]
        POL["Policy<br/>workflow.yaml + 9 templates<br/>quality gates · permissions · escalation · tools"]
        AG["Specialist agents by role<br/>PO · UI/UX · architect · dev · QA/SDET · review<br/>security · devops · SRE · knowledge (25)"]
        KN["Knowledge<br/>frameworks · MOSIP · ADRs<br/>each module's own .crewline/ (by role)"]
    end
    V["Deterministic layer: crewline<br/>manifest · evidence · scope · test integrity · diff coverage<br/>trace · regression · branch · DCO · secrets · usage"]
    EXT["Team tooling<br/>MOSIP repos (worktrees) · Maven · JUnit · JaCoCo · API TestRig · CoverageCli"]
    OUT["Outward actions, each after a human approval<br/>commit · push to fork · PR to upstream · Jira"]
    T["tasks/&lt;id&gt;/<br/>manifest · evidence · usage · reports"]

    U <--> ADP <--> O
    O --- POL
    O <-->|"minimal context packet / agent-response"| AG
    AG --- KN
    O <-->|"every transition and gate: PASS · FAIL · NOT_RUN"| V
    AG --> EXT
    V --> EXT
    O --> OUT
    O --> T
```

### 2.2 Component boundaries

| Component | Owns | Must not |
|---|---|---|
| Adapter | Tool syntax, context loading, tool bindings | Hold workflow policy |
| Orchestrator | State transitions, delegation, gates, approvals, retries, manifest | Invent results; bypass validators |
| Specialist agent | Bounded analysis or implementation in its states | Move state; approve; act outside approved paths |
| Validators | Machine-checkable rules, machine-readable results | Decide ambiguous product intent |
| Knowledge | Versioned, evidence-labelled facts (Verified / Inferred / Unknown) | Present an unverified claim as fact |
| Human | Answers, approvals, exceptions, production actions | — |

### 2.3 One task, end to end

```mermaid
sequenceDiagram
    autonumber
    actor H as Human
    participant O as Orchestrator
    participant S as Specialists
    participant V as crewline
    participant GH as GitHub / Jira
    H->>O: request (story, bug, upgrade ...)
    O->>V: tools (S0) - missing tool means stop with enable steps
    O->>O: INTAKE: task_type + risk tier, create manifest
    O->>S: requirements, AC review, impact, design
    S-->>O: ACs, impact map, design (approval if material)
    O->>S: SDET designs tests from ACs (before code)
    O->>H: base branch? (fork model)
    H-->>O: approval base_branch
    O->>S: developer implements in worktree (approved paths only)
    O->>V: build · test-integrity · change-scope · secrets · diff-coverage · regression · trace
    O->>S: code review (ctx A), then fresh-context review (ctx B)
    O->>V: manifest --to COMMIT_PR (every template gate PASS)
    O->>H: approve commit? / push + PR?
    H-->>O: approvals (single-use)
    O->>GH: signed-off commit, push to fork, PR to upstream
    GH-->>O: CI events and review comments (app PR monitor)
    O->>O: PR_CLOSEOUT loop until CI green and no valid comment open
    O->>H: final report + token usage per stage and step
```

---

## 3. The workflow

`core/workflow.yaml` is the single state machine (28 states). States are declared in **main-line order**.
`crewline manifest <m> --to <STATE> [--apply]` is the only way state moves: it refuses illegal edges and unmet guards,
and records every transition with a timestamp.

```mermaid
flowchart TB
    P1["<b>1 · Intake and requirements</b><br/>S0_BOOTSTRAP_CHECK → INTAKE → INPUT_VALIDATION (tracker ticket)<br/>→ REQUIREMENT_ANALYSIS ⇄ ACCEPTANCE_REVIEW"]
    P2["<b>2 · Analysis, design, tests first</b><br/>IMPACT_ANALYSIS → DESIGN<br/>→ TESTCASE_DESIGN ⇄ TESTCASE_REVIEW"]
    P3["<b>3 · Plan and branch</b><br/>IMPLEMENTATION_PLAN → GITHUB_PLANNING → REPO_READY<br/>(approved paths · base branch asked)"]
    P4["<b>4 · Build, test, review</b><br/>IMPLEMENTATION → DETERMINISTIC_VALIDATION → UNIT_INTEGRATION_TEST<br/>→ IMPLEMENTATION_REVIEW → FRESH_CONTEXT_REVIEW → SYSTEM_TEST_EXECUTION"]
    FT{{"FAILURE_TRIAGE<br/>classify · route · cap 3 per loop"}}
    P5["<b>5 · Gate, commit, PR</b><br/>QUALITY_GATE → COMMIT_PR → PR_CLOSEOUT<br/>(approvals · CI and review watch)"]
    P6["<b>6 · Release and close</b><br/>RELEASE_READINESS → AUTHORIZED_DEPLOYMENT → POST_DEPLOYMENT_VALIDATION<br/>→ KNOWLEDGE_UPDATE → FINAL_REPORT → DONE"]
    ESC(["WAITING_FOR_USER · S_ESCALATE · BLOCKED<br/>reachable from any state"])

    P1 --> P2 --> P3 --> P4 --> P5 --> P6
    P4 -->|"test failure"| FT
    P5 -->|"red CI"| FT
    FT -.->|"repair → IMPLEMENTATION"| P4
    P5 -.->|"gate failed / valid review comment → IMPLEMENTATION"| P4
    FT -->|"application · security · cap reached"| ESC
```

Dashed arrows are loop-backs. From any state the orchestrator can also go to:
- `WAITING_FOR_USER`, which resumes only to `resume_state`;
- `S_ESCALATE`, which is left only with a recorded human `escalation_decision`;
- `BLOCKED`.

**Guards** (evaluated against the task manifest):

| Form | Meaning |
|---|---|
| `{gate: X, eq: PASS}` | gate X recorded as PASS (skipped when X's state is not on the template path) |
| `{path: a.b, eq / ne / lt / gte / in: v}` | manifest field comparison |
| `{approval: action}` | an unconsumed, attributed, human approval exists; a transition that uses it **consumes** it |
| `{any_of: [...]}`, `{all_of: [...]}` | combinations, e.g. the two repair loops |

**Important guards:**
- **Ambiguities:** `IMPACT_ANALYSIS` and `TESTCASE_DESIGN` need `open_ambiguities == 0` **and** gates `requirement_clarity` + `acceptance_readiness` PASS (TBD / should-work ACs fail REQ-002).
- **Requirement fit:** `IMPLEMENTATION_REVIEW` needs `requirement_fit` PASS (AC observables in the diff).
- **Branch env:** `SYSTEM_TEST_EXECUTION` needs `branch_environment` PASS (Q8: the env runs this branch, not released).
- **Base branch:** `REPO_READY` needs approval `base_branch`, asked before any branch exists.
- **Commit:** `COMMIT_PR` needs `quality_gate`, `full_regression`, `secrets`, approval `commit_pr`, and every `required_gates` entry of the template (MAN-012).
- **Push and PR:** `COMMIT_PR -> PR_CLOSEOUT` (where push and PR happen) needs `dco`, `branch_scope`, `change_scope` and approval `push_and_pr`.
- **PR closeout:** `PR_CLOSEOUT` moves forward only with `pr.ci_status == PASS`, `pr.valid_comments_open == 0` and `pr.monitoring` set.
- **Deployment:** `AUTHORIZED_DEPLOYMENT` needs `release_readiness`, `environment_class == non-production` and approval `deploy`.

---

## 4. Task types

Each of the 9 task types is a **template**: a projection of the one graph, never a second orchestrator (ADR-0004). `path` lists the
states the type walks. An edge into a skipped state collapses forward along the main line; a loop-back into a skipped
state is dropped. Templates add guards and list `required_gates`.

| Type | Tier | States | What is special | Required gates |
|---|---|---:|---|---|
| `feature` | L2 | 26 | AC review; design + approval `design`; tests from ACs before code; release readiness | design, testcase, build, unit, diff coverage, component, deterministic, coding standards, robustness, test integrity, traceability, system tests, full regression, secrets, dependency security, change scope |
| `bug-fix` | L2 | 23 | a test that **fails on the base** first (`reproduction`); minimal fix | reproduction, build, unit, diff coverage, component, deterministic, coding standards, test integrity, traceability, full regression, secrets, change scope |
| `refactor` | L2 | 21 | strict test integrity: no test may change | design, build, unit, diff coverage, component, deterministic, coding standards, test integrity, full regression, secrets, change scope |
| `dependency-upgrade` | L2 | 21 | analysis of the **resolved** version; dependency security | design, build, unit, deterministic, test integrity, dependency security, full regression, secrets, change scope |
| `database-migration` | L3 | 26 | paired upgrade/rollback; approval `migration_review`; disposable-DB staged validation | design, testcase, migration pairing, build, unit, diff coverage, component, deterministic, test integrity, system tests, full regression, secrets, change scope, release readiness |
| `incident` | L0 | 7 | read-only: telemetry, timeline, hypotheses, postmortem; a fix becomes a new bug-fix task | secrets |
| `release` | L3 | 10 | readiness, rollback plan, non-production deploy only, post-deployment checks | release readiness, post deployment |
| `test-automation` | L2 | 22 | the PILOT-001 SDET path (matrix rows + TestRig YAML) | testcase, deterministic, test integrity, traceability, system tests, full regression, secrets, change scope |
| `module-onboarding` | L1 | 7 | Phase 0 for a new module: read-only discovery, evidence-labelled drafts, validator baseline, owner approval `onboarding_review` | onboarding discovery, onboarding baseline, secrets |
| `documentation` | L2 | 16 | docs-only change in a module repo (e.g. publishing reviewed `.crewline/development/`/`.crewline/qa/`); one independent review; `full_regression` not applicable (template `not_applicable_gates`) | deterministic, secrets, change scope |

A request that mixes types takes the stricter template or is split into linked tasks. The risk tier comes from the template
and can only be raised (MAN-013).

---

## 5. Agents

Contracts live in `agents/` (each has inputs, outputs, procedure, must-not rules and a permission profile). Every agent returns a
`templates/agent-response.schema.json` block:
- `task_id`, `agent`, `context_id`, `status`, `summary`;
- `evidence_refs`, `assumptions`, `unresolved_questions`;
- `proposed_changes`, `risks`, `recommended_next_state`.

Agents are grouped by the role they play in a team (`agents/<role>/`; full map with states in `docs/agents-by-role.md`).
Folders are for people: the workflow and the installer find agents by `name:`.

| Role (folder) | Agent | States | Produces |
|---|---|---|---|
| Orchestration (`agents/`) | orchestrator | all | manifest, delegations, approvals, final report |
| **Product Owner / BA** (`product/`) | requirement-analyst | INPUT_VALIDATION, REQUIREMENT_ANALYSIS | numbered ACs (incl. NFRs), ambiguity register |
| | acceptance-reviewer | ACCEPTANCE_REVIEW | testability / contradiction findings |
| **UI / UX** (`ui-ux/`) | ux-designer | DESIGN, TESTCASE_DESIGN (UI), IMPLEMENTATION_REVIEW (UI) | screen states, copy + translation keys, accessibility checklist, testable UI ACs |
| **Architect** (`architecture/`) | impact-analyst | IMPACT_ANALYSIS | code/API/DB/ops impact, proposed `approved_paths`, linked repos |
| | solution-architect | DESIGN | design doc, options, ADR, rollback approach |
| | api-contract-designer | DESIGN, IMPLEMENTATION_REVIEW | contract diff, additive vs breaking verdict |
| **Developer** (`development/`) | software-developer | IMPLEMENTATION, FAILURE_TRIAGE (CODE_UNDER_CHANGE) | scoped diff, unit + component tests, evidence |
| | database-engineer | DESIGN, IMPLEMENTATION | paired migrations, staged-validation plan |
| **QA / SDET** (`qa/`) | sdet-architect (lead) | TESTCASE_DESIGN/REVIEW, SYSTEM_TEST_EXECUTION, FAILURE_TRIAGE (AUTOMATION) | test strategy per AC, delegates below |
| | testcase-designer · testcase-reviewer | TESTCASE_DESIGN · TESTCASE_REVIEW | coverage-matrix rows, TestRig YAML design |
| | api-automation · ui-automation · dsl-automation | IMPLEMENTATION | TestRig YAML, templates, Suite.xml wiring |
| | failure-analyst · defect-analyst | FAILURE_TRIAGE / S_ESCALATE | classification with evidence; bug drafts with secret-scanned run reports |
| **Reviewer** (`review/`) | code-reviewer | IMPLEMENTATION_REVIEW, PR_CLOSEOUT | review 1; PR comment validity |
| | fresh-context-reviewer | FRESH_CONTEXT_REVIEW | review 2 in a new context, source-only packet |
| **Security** (`security/`) | security-engineer | DETERMINISTIC_VALIDATION, IMPLEMENTATION_REVIEW | severity-rated findings, dependency verdict |
| **DevOps / Release** (`devops/`) | devops-engineer | IMPLEMENTATION, RELEASE_READINESS, AUTHORIZED_DEPLOYMENT | validated CI/Helm/Docker changes, deploy record |
| | release-manager | RELEASE_READINESS | release notes, compatibility, rollback plan |
| **SRE / Operations** (`operations/`) | sre-analyst | incident path, POST_DEPLOYMENT_VALIDATION | timeline, hypotheses, postmortem draft |
| **Knowledge** (`knowledge/`) | knowledge-curator | KNOWLEDGE_UPDATE | evidence-labelled knowledge deltas |
| | module-onboarder | IMPACT_ANALYSIS, KNOWLEDGE_UPDATE (onboarding) | module context drafts, validator profile, baseline |

---

## 6. Quality gates

`core/quality-gates.yaml` defines the gates. Each gate's status goes in the manifest as `PASS | FAIL | BLOCKED | NOT_RUN | NOT_APPLICABLE`,
with rationale, evidence and the commit it was checked at. `enforced_by: deterministic` means a command decides, not an agent.

| Stage | Gates |
|---|---|
| Onboarding | `onboarding_discovery`, `onboarding_baseline` |
| Requirements / design | `input_readiness`, `acceptance_readiness`, `requirement_clarity`, `design_readiness`, `testcase_readiness`, `reproduction`, `plan_readiness`, `repo_ready` |
| Build / static | `build`, `deterministic_validation`, `coding_standards`, `robustness`, `requirement_fit`, `test_integrity`, `migration_pairing`, `dependency_security` |
| Developer testing | `unit_tests`, `diff_coverage`, `component_tests` |
| Reviews | `implementation_review`, `fresh_context_review` |
| System / pre-commit | `system_tests`, `branch_environment`, `full_regression`, `traceability`, `change_scope`, `secrets`, `defect_reports`, `quality_gate` |
| Git / PR | `dco`, `branch_scope`, `pr_ci`, `pr_comments` |
| Release / ops | `release_readiness`, `post_deployment`, `knowledge_update` |

**Exceptions** need `gate`, `approver`, `reason`, `scope` and `timestamp`. Never waivable: `secrets`, `test_integrity` without
approval, the repair caps, and production deployment.

---

## 7. Testing model

```mermaid
flowchart TB
    AC["Acceptance criteria<br/>(numbered, api: true when a caller can observe them)"]
    AC -->|"designed BEFORE code (independent oracle)"| TD["SDET test design"]
    subgraph DEV["Developer testing - UNIT_INTEGRATION_TEST"]
        UT["Unit tests<br/>JUnit + Mockito"]
        CT["Component tests at the HTTP boundary<br/>MockMvc / @WebMvcTest"]
        COV["diff-coverage<br/>JaCoCo: at least 80% of changed lines"]
    end
    subgraph SYS["System testing - SYSTEM_TEST_EXECUTION"]
        TR["API / UI / DSL TestRig<br/>against an env running the BRANCH build"]
    end
    REG["full_regression<br/>full suite base vs branch: 0 new breaks"]
    TD --> UT
    TD --> CT
    TD --> TR
    UT --> COV
    CT --> COV
    COV --> REG
    TR --> REG
    REG --> TRC["trace: AC to code to test id to PASS"]
```

| Layer | Who | Gate / validator | Rule |
|---|---|---|---|
| Reproduction (bug fix) | SDET + developer | `reproduction` | the new test fails on the base for the reported reason, then passes on the branch |
| Unit | developer | `unit_tests`, `crewline regression` (surefire XML) | targeted while iterating; changed modules' full suite base vs branch before commit |
| Coverage of the change | developer | `diff_coverage`, `crewline diff-coverage` | at least 80% of changed executable production lines executed; no unreported file; report newer than the code |
| Component (HTTP boundary) | developer | `component_tests`, `crewline trace` TRC-007 | when system tests are not applicable, every `api: true` AC needs a passing MockMvc / `@WebMvcTest` test asserting what the caller sees |
| Test integrity | all | `test_integrity`, `crewline test-integrity` + `expected-diff` | no removed/disabled tests, no fewer assertions, no weakened TestRig expectations without approval |
| System (API/UI/DSL) | SDET | `system_tests` | only counts when the environment runs the branch build; report header must show the branch commit |
| Regression | all | `full_regression` | full suite on base and branch, same environment, sequential; any PASS to FAIL blocks |
| Traceability | orchestrator | `traceability`, `crewline trace` | every AC maps to code refs and tests with a recorded result |
| Coding standards | developer | `coding_standards`, `crewline coding-standards` | repo's own lint/format checkers with a fresh `crewline run` record; no debug leftovers in the production diff; **before** review |
| Robustness | developer | `robustness`, `crewline robustness` | no empty/swallowed catch, no raw `throws Exception`, no sleep on the hot path; reuse the module handler; every api AC has a passing error-path test; **before** review |
| Requirement fit | developer | `requirement_fit`, `crewline requirement-fit` | AC status/error/route/quoted value appears in the diff **and** a changed test; `code_refs` were touched; **before** review |
| Branch env | SDET | `branch_environment`, `crewline branch-env` | system tests against an env that runs **this** branch (`local-stack` or `branch-deployed-env`); `api-internal.released` is BEV-004 |
| Defect reports | SDET | `defect_reports`, `crewline defect-reports` | APPLICATION/SECURITY defects carry a secret-scanned `report_path` |

**Scope rule:** run targeted tests while iterating. Before committing, run the full suite on base and branch. If only new tests
were added, those tests plus their producers are enough (`crewline scope` for TestRig).

---

### 7.1 SDET in every flow

Every flow that changes product code runs through the SDET role: new behaviour gets automated system tests, and the existing
TestRig suites prove nothing broke, before the PR. Steps are included or skipped per task type, never silently.

```mermaid
flowchart TB
    S["Story / bug"] --> PO["PO: requirements + AC review<br/>(api / ui flags per AC)"]
    PO --> UX["UI/UX: screens, states, copy keys, accessibility<br/>(when a UI changes)"]
    UX --> AR["Architect: impact + design (approval)"]
    AR --> TD["SDET: test case design from ACs<br/>+ test plan: where the branch build runs"]
    TD --> DEV["Dev: code + unit + component tests"]
    TD --> AUTO["SDET: automate missing cases<br/>(TestRig API / UI rig)"]
    DEV --> REV["Reviews: code review + fresh-context review"]
    AUTO --> REV
    REV --> RUN["SDET: TestRig run on the branch build<br/>new cases + existing regression (base vs branch)"]
    RUN -->|"all pass"| QG["Quality gate: every required gate has evidence"]
    RUN -->|"failure"| TRI{"Triage: fails on base too?"}
    TRI -->|"no: our change"| FIX["Bug fixing: developer loop (max 3)"]
    TRI -->|"yes: pre-existing"| BUG["Bug raising: Jira bug + run report<br/>(or deferred with a reason)"]
    TRI -->|"test / data / env"| AFIX["SDET fixes the test or re-runs"]
    FIX --> RUN
    AFIX --> RUN
    BUG --> QG
    QG --> PR["Commit + PR (approvals) -> CI + review watch"]
```

| Step | feature | bug-fix | refactor | dependency-upgrade | database-migration | test-automation | documentation | incident | release |
|---|---|---|---|---|---|---|---|---|---|
| PO: requirements / AC review | yes | yes | — | — | yes | yes | — | — | — |
| UI/UX spec | when UI changes | when UI changes | — | — | — | — | — | — | — |
| Design + approval | yes | — | yes | yes | yes (+ migration review) | — | — | — | — |
| SDET test design from ACs | yes (before code) | yes (reproduction first) | regression selection | regression selection | yes | yes | — | — | — |
| Test plan (branch environment) | required | required | required | required | required | required | — | — | — |
| Dev: code + unit + component tests | yes | yes | yes | yes | yes | — | docs only | — | — |
| SDET: automate missing system cases | every api/ui AC (TRC-008) | the reproduced symptom | — | — | every api/ui AC | the whole task | — | — | — |
| TestRig run on the branch build (new + regression) | required | required | required | required | required | required | — | — | smoke after deploy |
| Bug raising / bug fixing before PR | MAN-014 | MAN-014 | MAN-014 | MAN-014 | MAN-014 | MAN-014 | — | read-only | — |

**Enforced by:**
- **No code before a test plan:** IMPLEMENTATION is refused until `test_plan.environment` says where the branch build runs.
- **Every user-facing criterion is automated:** an `api` or `ui` acceptance criterion without a system-level automated test is blocked (TRC-008). One without a result on the branch build is blocked too (TRC-009).
- **System tests and regression can't be quietly skipped:** they can't be marked "not applicable" in code flows (MAN-015). The only way to skip them is a human-approved deferral to a named environment and date; the post-merge run then becomes a follow-up task.
- **No PR with an open defect:** the PR is refused while a defect caused by the change is open, or while a pre-existing one hasn't been raised (MAN-014).
- **No design or code without a ticket:** nothing moves past INPUT_VALIDATION, in any task type, until a GitHub issue or Jira item is linked and the user confirmed it, Crewline created one on the user's approval (`create_tracker_item`), or the user waived it with a reason (MAN-017, `tracker_guard`). Tasks already past INPUT_VALIDATION are caught at their next move. DONE needs the closure comment on the ticket posted on approval (`tracker_comment`) or declined.
- **Subtasks under the ticket:** past GITHUB_PLANNING, one subtask per kind the template lists (default testcase, implementation, testing, review, pr; GitHub sub-issue or Jira sub-task), linked or created on approval `create_tracker_subtasks`, or waived by the user (MAN-018). Each gets a closure comment (posted on approval or declined) before DONE.
- **Waivers get a follow-up:** by FINAL_REPORT every known limitation and gate exception links a ticket or task, or a reason the user accepted (MAN-019). A test suite no CI workflow runs is refused on the same move unless accepted (`ci-coverage`).
- **Untrusted input:** a change that parses input it does not control needs ACs for input size, nesting depth, memory, time and error behaviour (REQ-005), and a threat review by the security-engineer before test design (PKT-005).
- **PR links the ticket:** the title starts with the key and the body links the ticket; the hook refuses `gh pr create` / `gh pr edit --title|--body` otherwise (GRD-009).
- **Where it can run:** an environment where CI deploys the PR image, or a local stack (the module's docker-compose or a local service run). MOSIP pack: options in `packs/mosip/tools.yaml` `branch_test_env`. Generic pack: the environment named in `test_plan.environment` and the module's own suite.

## 8. Failures, repairs and escalation

```mermaid
flowchart TB
    F["Failing test or red CI check"] --> FP["Capture evidence<br/>fingerprint = test id | assertion | status"]
    FP --> BASE{"Fails on the BASE too?"}
    BASE -->|"no, and the path runs through the task's diff"| CUC["CODE_UNDER_CHANGE"]
    BASE -->|"yes"| APP["APPLICATION<br/>(pre-existing defect)"]
    FP --> AUT["AUTOMATION<br/>(test code / YAML / wiring)"]
    FP --> ENV["ENVIRONMENT / TEST_DATA"]
    FP --> SPEC["SPECIFICATION / SECURITY"]
    CUC --> DL["Developer loop<br/>max 3 attempts (dev_attempt_count)"]
    AUT --> SL["SDET loop<br/>max 3 attempts (attempt_count)"]
    ENV --> RERUN["Re-run (not an attempt)"]
    APP --> ESC["S_ESCALATE<br/>bug draft with secret-scanned run report"]
    SPEC --> ESC
    DL -->|"cap reached"| ESC
    SL -->|"cap reached"| ESC
    ESC --> HD["Human escalation_decision<br/>names the resume state"]
```

- **Attempts:** one attempt is one distinct, evidence-based change followed by validation. Re-running unchanged code does not count. Counters reset only for a different fingerprint or a human-approved new investigation (ADR-0005).
- **Escalate immediately:** a defect outside the task's scope, a security concern, or a fix that could only weaken a test.
- **Bugs:** drafted, never filed without explicit confirmation. Each carries the generated run report, secret-scanned. Raw full reports embed environment configuration and are never published.

---

## 9. Git, PR and review comments

**Which task is active:** the rails judge one task at a time. Most specific first: `CREWLINE_TASK_MANIFEST`, the task whose
worktree the session runs in, `crewline task activate --id <id>`, then the newest open manifest. `crewline task list` marks it,
and every guard deny names it. Session scratchpads and temp folders are outside the rails (not a checkout in a temp folder).

**Pausing and resuming:** `crewline resume <id>` in the new session prints everything it needs, and `crewline gates refresh`
replaces per-task gate scripts. Neither uses the model, which is the main saving on mechanical work. For what remains, the
specialists running validators and recording evidence can use a cheaper model through the `model:` field of their agent file
(not set by default, because no evaluation has measured it yet). Token use per state is in `usage.md`, and
`core/usage-budget.yaml` warns at `warn_ratio` before a state goes over its cap.

**Fork model** (ADR-0003, `crewline branch`):
1. Ask the **upstream** base branch before any branch exists (approval `base_branch`).
2. Cut a worktree and branch from that upstream ref exactly; never from local or `origin`.
3. Carry only the task's commits. Origin- or local-only changes go in only on explicit request.
4. Sign every commit (`git commit -s`, DCO) and put the tracker key in the subject (Jira `PROJ-1` or GitHub `#N`;
   `crewline branch --manifest <m>` reads `tracker.key` + `requirements.story_keys`, BR-002).
   The PR title starts with the key: `<KEY>: <summary>` (e.g. `MOSIP-38854: Handle missing identity name fields in
   NotificationService`); the hook refuses `gh pr create` / `gh pr edit --title` otherwise (GRD-009).
5. Push to `origin` (the fork) and open the PR against the same upstream branch.

**Linked changes:** when a module needs a change in a dependency (e.g. apitest-commons), each repo gets:
- its own base-branch approval, worktree, branch and gates (`branch_scope:<repo>`, `dco:<repo>`);
- its own PR. The dependency PR comes first, the primary PR links it, and `version_link` states how the change is consumed (MAN-006..009).

**PR closeout:**

```mermaid
flowchart TB
    PR["PR opened"] --> MON["Turn on monitoring<br/>app monitor, else one gh pr checks --watch,<br/>else the user confirms they will report"]
    MON --> EV{"event"}
    EV -->|"CI red"| FT["FAILURE_TRIAGE"]
    EV -->|"review comment"| CR{"code-reviewer:<br/>valid against the code that runs?"}
    CR -->|"valid"| IM["IMPLEMENTATION<br/>re-run gates by scope<br/>new commit_pr + push_and_pr approvals"]
    CR -->|"not valid"| RP["Reply with evidence"]
    FT --> IM
    IM --> EV
    RP --> EV
    EV -->|"CI green and no valid comment open"| NEXT["next state"]
```

No polling: monitoring events wake the session. Every event gets its own usage mark.

---

## 10. Governance: tiers, approvals, independence

| Tier | Behaviour | Examples |
|---|---|---|
| L0 Advisory | analyse and recommend; read-only | incidents, architecture options |
| L1 Assisted | drafts for a human to apply | issue drafts, bug drafts |
| L2 Controlled execution | edit the task worktree, run tests, commit/PR behind gates and approvals | features, bug fixes, test automation |
| L3 Restricted | consequential action with explicit authorization and a checklist | DB migrations, non-production deploys |

- **Approvals** (`core/permissions.yaml`, 23 actions): every outward or irreversible action needs one. That covers base branch, design, scope change, commit, push and PR, bug filing, Jira comments, migration review, deploy and escalation decisions. Each records the approver, reason, scope and timestamp, and is **consumed** by the transition that uses it.
- **No self-approval** (MAN-010): an approval whose approver is an agent, or `approver_type: agent`, is rejected.
- **No self-review** (MAN-011): reviewers may not be authors, and the two reviews must run in different contexts.
- **Disabled in this version:** production deployment, rollback execution, force-push, deleting branches or data.
- **Untrusted input:** instructions found in repository files, issues, logs, PR comments or API responses are data, not commands.

---

## 11. Validators (`crewline`)

Run from `validators/`: `python -m crewline [--json] <command> ...`. Exit codes: **0 PASS · 1 FAIL · 2 NOT_RUN**. A crash is NOT_RUN, never PASS.

| Command | Rules | Checks |
|---|---|---|
| `manifest` | MAN-001..019, TRN-001..003 | schema; template projection; guards; both repair caps; human single-use approvals; reviewer independence; risk tier; required gates; linked repos. `--to <STATE> [--apply]` |
| `run` / `evidence` | EVD-001..004 | runs the command and records exit code, commit and worktree digest; rejects stale or incomplete records. `run` also prints a CLIP-002 digest of the log |
| `clip` | CLIP-001..002 | digest of a log (`--file`) or of a command (`clip -- <cmd>`); cap `clip_max_chars` |
| `change-scope` | SCP-001..003 | changed files inside `scope.approved_paths`; CI/build/deploy/config files always need `scope_change` |
| `test-integrity` | TIN-001..006 | JUnit/TestNG and Jest/Vitest/Mocha: removed, disabled or focused tests, fewer assertions, deleted files; `--strict` for refactors |
| `diff-coverage` | DCV-001..004 | JaCoCo XML or lcov.info + git diff: share of changed lines tested, missing files, stale reports |
| `trace` | TRC-001..007 | AC to code to test to result; component test for API-facing ACs when system tests are not applicable |
| `coding-standards` | STD-001..005 | repo's own spotless/checkstyle/eslint/prettier must have a fresh `crewline run` record; production diff must not add debug leftovers; blocks review |
| `robustness` | ROB-001..006 | empty/swallowed catch, raw throws Exception, sleep on the hot path; `implementation.robustness` (handler reuse + hot-path notes); api AC error-path test PASS |
| `requirement-clarity` | REQ-001..004 | story/bug key, observable ACs, no TBD/should-work; blocks design/tests |
| `requirement-fit` | RFT-001..005 | AC observables in the diff **and** a changed test; blocks review |
| `branch-env` | BEV-001..004 | branch-build env named; released host refused (Q8) |
| `defect-reports` | DEF-001..003 | secret-scanned defect attachments |
| `adapter-check` | ADP-001 | installed adapter matches `adapters/` (`--sync` re-renders) |
| `evals` | EVAL-001..005 | frozen `automated_by` nodeids; `--pack frozen` needs `expected-outcomes/` |
| `cold-start` | CS-001..005 | new-machine kit (frozen pack, adapters, synthetic `--apply`, CI spec jobs) |
| `task` | TSK-001..007 | queue over `tasks/*/`; `export`/`import` portable memory (no logs); `start` is a conductor (next `--to`); not a daemon |
| `migration` | MIG-001..004 | upgrade/rollback and release/revoke pairs; released scripts immutable; destructive statements flagged |
| `regression` | REG-001..006 | base vs branch from TestRig full reports or surefire XML; pass-to-fail and failing new tests block |
| `branch` · `dco` | BR-001..005 · DCO-001..002 | fork model; signed-off commits |
| `secrets` | SEC-001..005 | gitleaks when present; values never printed |
| `tools` | TOOL-000..002 | capabilities from `core/tools.yaml` + active pack overlay; numbered enable steps |
| `pack` | PCK-001..002 | show / list / `--set` product pack (`mosip` default, `generic` for a non-MOSIP repo) |
| `usage` | USE-001..012 | token usage per task/stage/step **and** budget stop (`token_budget` gate); packet size; `--apply` stamps a mark (USE-009) |
| `discover` | DSC-001..009 | module onboarding: reads poms, tests, CI, DB scripts, deploy, guidance, TestRig suites and coding-standard checkers read-only; writes evidence-labelled drafts and a baseline |
| `guard` | GRD-001..009 | commit/push/PR gated; **GRD-009** PR title not prefixed `<tracker key>: ` or body not linking the ticket; force-push / `--no-verify` / prod always denied; **GRD-007** raw log dump or suite not via `crewline run`; **GRD-008** Read of a dump or file over `read_max_bytes` |
| `manifest --next` / `status` | NXT-001..004, STS-001 | every legal move and each precondition with its state; repair attempts left, open approvals. Read these before offering the user a choice |
| `review record` / `approval add` / `limitation add` | REC-001..002 | write records through a command that validates first; never hand-edit the manifest |
| `ci-coverage` | CIC-001..003 | test suites the task ran that no CI workflow runs (checked on the move to FINAL_REPORT) |
| `resume` | TSK-009 | `crewline resume [<id>]`: writes `HANDOVER.md` from the manifest (state, open findings, attempts, next commands) and makes the task active; run it instead of writing a hand-over |
| `gates refresh` | GAT-001..004 | re-runs the gates a command decides and records the result; `--all` after a code change, `--rerun` runs recorded builds and tests again. Review and human gates are never touched |
| `task activate` / `worktree` | TSK-008, TSK-010 | pin the active task in `.crewline-work/active-task`; create a task worktree a session can run in (ADR-0009) |
| `testcase` · `expected-diff` · `scope` · `coverage` | NAME/DEP · EXP-001..009 · SCOPE · COV | API TestRig: IDs, `$ID:` chains, weakened expectations, targeted vs full scope, CoverageCli |

---

## 12. Evidence and token usage

**Evidence the model cannot fabricate.** Builds and tests run through the recorder:

```bash
python -m crewline run --repo <worktree> --out .crewline-work/tasks/<id>/evidence/unit.json -- mvn -B -pl <module> -am test
python -m crewline evidence --record .crewline-work/tasks/<id>/evidence/unit.json --repo <worktree>   # PASS only if still current
```

**Token budget is a stop, not a report** (`core/usage-budget.yaml`, gate `token_budget`, principle 36). PILOT-001 spent
about 3M tokens per review round and 6.7M waiting on background runs. `crewline usage` FAILs when the **task** or a
**state** exceeds the cap (USE-003/004), or one **subagent** exceeds `per_subagent` (USE-011). KM-COV-001 spent 94M
against an 8M cap because the meter was never given a transcript and passed on 0 calls; now it finds the Claude
transcript itself and a blind meter on Claude BLOCKs (USE-001/010). A call whose context passes `context_warn` warns
(USE-012): `/compact` the orchestrator or resume in a new session; give a subagent a smaller batch. That is WAITING_FOR_USER: continue with an attributed exception, split the
task, or abort. Specialist packets under `tasks/<id>/packets/` over `packet_max_chars` or that dump core/reports also
FAIL (USE-007/008). Cursor often has no API transcript: USE-006, and the adapter **token fire-break** is the control
(do not re-read core after INTAKE, do not paste reports, do not poll). `crewline clip` is the bash-output reducer
(CLIP-002 on every `crewline run`); RTK is optional for other shell and is not a gate.

The transcript records the API-reported usage of every model call. `crewline usage`
attributes each call:
- to a **stage**, using the manifest's `started_at` and `state_history`;
- to a **step**, using `usage_marks`;
- including subagent transcripts, de-duplicated by message id.

```bash
python -m crewline usage --manifest .crewline-work/tasks/<id>/task-manifest.json --mark "IMPLEMENTATION: minimal fix"   # start a step
python -m crewline usage --manifest .crewline-work/tasks/<id>/task-manifest.json                                       # .crewline-work/tasks/<id>/usage.md
python -m crewline usage --manifest .crewline-work/tasks/<id>/task-manifest.json --transcript <session>.jsonl            # explicit transcript
```

| Column | Meaning |
|---|---|
| fresh | input + cache write + output: tokens new to the model in that call |
| cache read | conversation re-read from cache; usually the largest, cheap per token |
| total | everything |

Without `--transcript` or `usage_transcripts`, the meter reads every `~/.claude/projects/<slug>/*.jsonl` (or
`$CLAUDE_CONFIG_DIR`) for the clone and its `.crewline-work` worktrees written since `started_at`, so a task that spans
sessions is covered. `usage.md` also has a **By agent** table (main = orchestrator) with each one's peak context.

---

## 13. Task manifest

Each task has its own workspace, `<clone>/.crewline-work/tasks/<id>/`, inside the module clone but excluded from git and never committed. It holds `task-manifest.json`, `evidence/`, `usage.md`,
`final-report.md` and `pr-body.md`. The schema is in `templates/task-manifest.schema.json`. Main fields:

| Field | Purpose |
|---|---|
| `task_type`, `risk_tier` | selects the template; tier only rises |
| `current_state`, `resume_state`, `state_history` | where the task is; every applied transition with time and note |
| `tracker` | the GitHub issue / Jira item: `system`, `key`, `url`, `found_by`, `created_by_crewline`, `confirmed_by`, `linked_at`, or `waived` (approval record); `closure` comment status (MAN-017); `subtasks[]` (kind, key, url, closure) or `subtasks_waived` (MAN-018) |
| `scope.repositories[]`, `scope.approved_paths` | repos (role, upstream, base branch, PR, `depends_on`, `version_link`) and the file globs the plan allows |
| `requirements.acceptance_criteria[]` | `id`, `text`, `api`, `code_refs`, `test_ids`, `component_test_ids`, `matrix_row_ids` |
| `gates[]` | status, rationale, evidence, `checked_at_commit` |
| `approvals[]`, `exceptions[]` | human, attributed, single-use (`consumed_by`) |
| `implementation.authors`, `reviews.reviewers` | independence checks |
| `repair` | `classification`, `attempt_count` (SDET), `dev_attempt_count` (developer), `attempt_history[]` with `track` |
| `test_results` | test id to latest result on HEAD |
| `pr` | `url`, `ci_status`, `monitoring`, `valid_comments_open`, `events` |
| `release` | `environment`, `environment_class`, `rollback_plan_ref` |
| `started_at`, `usage_marks`, `usage_transcripts` | token accounting |

---

## 14. Knowledge and module context

There is no vector index, embedding store or “agent memory” service. Shareable memory is **files in git** (team) and
**files on disk in the task workspace** (one clone, one task). A new chat starts empty; it reloads those files.

- **Global** (`core/`): principles, workflow, policy — product-neutral. **Pack** (`packs/<id>/`): which knowledge dirs and extra tools load. MOSIP pack points at **Frameworks** (`knowledge/frameworks/`: API TestRig, apitest-commons, UI and DSL rigs) and **MOSIP** (`knowledge/mosip/`: application map, dependencies, glossary). Generic pack loads neither.
- **Decisions** (`knowledge/decisions/`): ADR-0001..0008, an ADR template, and the open Phase 0 questions.
- **Module context lives in each module repository**, never in Crewline: `<module repo>/.crewline/`, one folder per role:

  ```
  <module repo>/.crewline/
  ├── README.md          what this is, when it was verified, how to refresh it
  ├── module.yaml        index: module, upstream, stack, which role folders exist
  ├── development/       local-context.md: build, unit and coverage commands, CI, DB layout, default approved paths
  ├── qa/                module-context.md, known-issues.md (KI-*), profile.yaml (TestRig validator rules)
  └── product/ ui-ux/ architecture/ devops/ security/ operations/   added when the module has something to say
  ```

  The `module-onboarding` task drafts it in a local task workspace; after the owner's review a documentation task opens a
  PR that adds it to the module repository. Validators read the profile from `<module repo>/.crewline/qa/profile.yaml`
  by default (`--profile` overrides).
- **Evidence labels:** every claim is Verified (file:line, command, report), Inferred (with reasoning) or Unknown. Secrets and raw logs are never stored.

### 14.1 How agents find and share context

```mermaid
flowchart LR
    CODE["Module source + git"]
    SEARCH["Read · Grep · Glob · git"]
    DISC["crewline discover<br/>onboarding only"]
    MOD[".crewline/ in the module repo<br/>team-shared, committed"]
    PACK["Pack knowledge/ + ADRs<br/>in Crewline, committed"]
    TASK[".crewline-work/tasks/id/<br/>manifest · packets · evidence<br/>local, not git"]
    DEV["software-developer"]
    QA["sdet-architect"]
    TEAM["Next human or next chat"]

    CODE --> SEARCH
    DISC --> MOD
    SEARCH --> DEV
    SEARCH --> QA
    MOD --> DEV
    MOD --> QA
    PACK --> DEV
    PACK --> QA
    DEV -->|"writes code_refs, test_ids,<br/>evidence paths, packet"| TASK
    TASK -->|"same files, no chat dump"| QA
    MOD --> TEAM
    PACK --> TEAM
    TASK -.->|"only if they share the clone"| TEAM
```

#### Which tool searches the code

The agent contract lists the tools. Crewline does not ship a code-search product. On Cursor and Claude that is the
platform’s own **Read**, **Grep**, **Glob** and **Bash** (`git grep`, `git show`, `git diff`, `git log -S`). The
developer and SDET contracts are `Read, Grep, Glob, Bash, Edit, Write`. Reviewers are read-only (`Read, Grep, Glob, Bash`).

| Step | Tool | What it is for |
|---|---|---|
| 1. Orient | Read `.crewline/development/local-context.md` (and `.crewline/qa/` for test work) | default packages, build, test, CI, approved-path globs — **not** the whole repository (principle 10) |
| 2. Find files | Glob (`**/*Service.java`) | names and layout |
| 3. Find symbols / strings | Grep / `git grep` | call sites, error codes, existing helpers (commons-first, principle 1) |
| 4. Read what you will change | Read at **file:line**, at the version the build resolves (principle 4) | the code that actually runs, including `-sources.jar` when the fact is in a dependency |
| 5. Compare | `git diff` / `git show` on the task worktree | the change, not a chat summary of it |
| 6. First time on a module | `python -m crewline discover --repo <module> --module <name>` | read-only scan of poms, tests, CI, DB scripts, TestRig suites; writes **drafts** of `.crewline/` |
| 7. Run a bulky command | `crewline run --repo <dir> --out <record> -- <command>` then `crewline clip --file <record.log>` | full stdout stays on disk; the agent reads a CLIP-002 digest (cap `clip_max_chars`) |

That last step is the Crewline equivalent of [RTK](https://github.com/rtk-ai/rtk) (Rust Token Killer: a shell hook that
compresses bash output before the model sees it). Crewline does **not** vendor or require RTK. Builds and tests already
go through `crewline run`; `clip` is what you read. Install RTK yourself if you want the same compression on *other*
bash (`git status`, `ls`, `rg`) — it is optional and outside the gates.

This search / clip / hand-off loop is also in the adapters (`adapters/cursor/crewline.mdc`, `skills/crewline/SKILL.md`,
`adapters/generic/ENTRY.md`) and in the orchestrator, software-developer and sdet-architect contracts, so it is agent
behaviour, not only this README.

After INTAKE the orchestrator does **not** re-read `principles.md` or every agent file. Each specialist gets one contract,
the module `.crewline/` slice it needs, the manifest, and FAIL findings only (principle 10 / 36, USE-007/008).

#### Three memories (what is shared, what is not)

| Layer | Where | Who sees it | What belongs there |
|---|---|---|---|
| **Team memory** | `<module>/.crewline/` committed on the module’s default branch | every clone, every new chat (SessionStart / Cursor bootstrap says “Crewline is ACTIVE” and points here) | how *this* repo builds, tests, deploys; QA profile; known issues; default approved paths |
| **Product memory** | Crewline `knowledge/` dirs named by the active pack + `knowledge/decisions/` | every Crewline install on that pack | application map, TestRig / commons facts, ADRs. Module facts do **not** live here |
| **Task memory** | `<clone>/.crewline-work/tasks/<id>/` (gitignored via `.git/info/exclude`) | people who have that clone, or who are handed the folder | `task-manifest.json` (ACs, `code_refs`, `test_ids`, `test_plan`, gates, evidence paths), `packets/<state>.md` (≤ `packet_max_chars`), `evidence/`, `usage.md` |

**Never** treated as shareable memory: the chat transcript, the implementer’s reasoning, a pasted TestRig/surefire/raw
log, secrets, or a re-paste of `core/`. Fresh-context review is given a **source-only** packet (story, ACs, design,
diff, validator output, evidence paths) and a new `context_id`.

A teammate who clones the module and opens a new Cursor/Claude session gets team + product memory automatically. They
do **not** get another person’s task workspace unless that person copies `.crewline-work/tasks/<id>/` or they continue
on the same machine. The durable hand-off between people is the **PR + the committed `.crewline/` + the story**; the
durable hand-off between agents on one task is the **manifest and the packet files**.

#### How a developer shares context with agentic QA

SDET designs tests from the ACs **before** the code exists, so QA is not waiting on a dump from the developer. The
developer then writes pointers the SDET and the gates can consume without re-reading the chat.

| When | What the developer writes | What the SDET / gates read |
|---|---|---|
| IMPLEMENTATION | `requirements.acceptance_criteria[].code_refs` — repo-relative `file[:symbol]` that implements the AC | `crewline requirement-fit` / `trace`; SDET at SYSTEM_TEST_EXECUTION |
| IMPLEMENTATION | `test_ids` / `component_test_ids` — unit and HTTP-boundary tests the developer added | `trace` (TRC-007 when system tests are N/A) |
| IMPLEMENTATION | `implementation.robustness` (handler reuse, hot-path notes) and `error_paths` for every `api: true` AC | `crewline robustness` |
| IMPLEMENTATION | `crewline run` evidence paths (build, unit, coverage, linter) | `evidence`, `diff-coverage`, `coding-standards` |
| each state | orchestrator writes `tasks/<id>/packets/<state>.md` — ACs, approved paths, FAIL rules only | the next specialist, including SDET. Cap: `packet_max_chars` in `core/usage-budget.yaml` |

The SDET already has, from earlier states: approved ACs, the design, `test_plan`, matrix rows / TestRig ids
(`system_test_ids`), and `.crewline/qa/` (suites, `profile.yaml`, known issues). At SYSTEM_TEST_EXECUTION it reads
those plus the worktree and the evidence paths. It does **not** receive the developer’s notes. CODE_UNDER_CHANGE
failures go back through the same manifest (`repair`, `test_results`), not a Slack/chat paste.

If a human developer (not the agent) needs the SDET to pick up a change: put the same fields on the manifest, leave
the evidence records in `.crewline-work/tasks/<id>/evidence/`, and point the next session at that task id
(`python -m crewline task resume --id <id>`). Do not paste logs into the chat.

#### Maintaining the shareable part

| Event | Who | What to do |
|---|---|---|
| New module | `module-onboarder` + owner | `crewline discover` → owner reviews drafts → documentation PR commits `.crewline/` to the **module** repo |
| Build / test / CI command changed | module owner (or a documentation task) | update `.crewline/development/local-context.md` and `discovery.json`; keep the Verified commit |
| TestRig layout or `$ID` extras changed | SDET + owner | update `.crewline/qa/module-context.md` and `profile.yaml` |
| Task produced a durable fact | `knowledge-curator` at KNOWLEDGE_UPDATE | small delta in pack `knowledge/` **or** `.crewline/qa/` (known issue, not a log). Prefer a link to the module’s own `CLAUDE.md` / `coverage/AGENTS.md` over a copy. Mark superseded entries; do not delete history. Status Verified/Inferred/Unknown + `last_verified` commit |
| Stale `.crewline/` | next agent | repository guidance wins where it disagrees; fix the draft, do not override the repo |

Refresh rule: if a command in `local-context.md` no longer runs, the file is wrong — do not keep a “tribal” chat note
instead. Secrets, tokens, personal data and raw reports never go in, either layer.

---

## 15. Adapters and installation

The adapter is only tool syntax. Policy stays in this repository. **Which adapter you install decides how you start the crew**
([How you start it](#how-you-start-it-cursor-claude-or-terminal)): Cursor and Claude Code start it from a chat; the terminal
never does; generic is for any other AI tool plus git hooks.

| Tool | File | Installs to |
|---|---|---|
| Claude Code (plugin, preferred) | `.claude-plugin/`, `skills/`, `agents/`, `hooks/` (SessionStart + guard), `bin/` | enabled per module, see "Turn Crewline on for a module" |
| Claude Code (manual) | `skills/crewline/SKILL.md` + `crewline-*` subagents + fail-closed hooks | `<workspace>/.claude/skills/`, `.claude/agents/`, `.claude/settings.json`, `.claude/hooks/` |
| Cursor (module bootstrap, preferred) | `templates/module-cursor/` | committed in the module's `.cursor/`, see "Turn Crewline on for a module in Cursor" |
| Cursor (manual) | `adapters/cursor/crewline.mdc` + fail-closed hooks (`crewline guard`) | `<workspace>/.cursor/rules/` and `<workspace>/.cursor/hooks.json` |
| Any other | `adapters/generic/ENTRY.md` + git `pre-commit` / `commit-msg` / `pre-push` | `<workspace>/.crewline/` and `<workspace>/.git/hooks/` |

```bash
# Preferred: one script (deps + tests + tool check + adapter)
scripts\setup.bat cursor D:\work\mosip          # Windows
./scripts/setup.sh claude ~/work/mosip          # Linux / macOS / Git Bash
./scripts/setup.sh generic /path/to/module-repo

# The same steps by hand
pip install -r validators/requirements.txt
cd validators && python -m pytest -q tests
python -m crewline tools
python ../adapters/install.py --platform cursor --target <workspace> --write
python ../adapters/install.py --platform claude --target <workspace> --write
python ../adapters/install.py --platform generic --target <workspace> --write
python adapters/install.py --platform cursor --target <workspace> --sync --check   # after an upgrade
```

**Prerequisites** (`python -m crewline tools` — core + active pack, each capability has numbered enable steps):
- **Core:** Git, Python 3.11+, GitHub CLI, Docker/Helm (release), a dependency scanner, a disposable DB, a non-production environment, PR monitoring.
- **MOSIP pack (default):** Java 21, Maven 3.9, Jira connector and Claude in Chrome (report attach), CoverageCli, branch TestRig env.

---

## 16. Running a task, step by step

Start this in **Cursor or Claude Code**, not with a terminal command ([How you start it](#how-you-start-it-cursor-claude-or-terminal)).

1. **Ask:** "fix MOSIP-12345", "add API tests for story X", "upgrade kernel-core to 1.3.0".
2. **Intake:** the orchestrator checks tools, classifies the task, creates `.crewline-work/tasks/<id>/task-manifest.json` and starts the usage marks.
3. **Questions:** it asks for anything ambiguous and for the **upstream base branch**.
4. **Design** (feature, migration): you approve material design choices.
5. **Tests first:** tests are designed from the ACs. A bug fix gets a failing reproduction.
6. **Build:** the developer implements in a worktree, inside the approved paths, with unit, component and coverage evidence.
7. **Reviews:** two independent reviews in different contexts.
8. **Quality gate:** every required gate must have evidence before the commit.
9. **Commit, push, PR:** each needs your approval. The PR is watched until CI is green and no valid comment is open.
10. **Report:** knowledge update and final report, with token usage per stage and step.

### Use it for your module

Everything in `core/`, `agents/` and most validators is **module**-independent. MOSIP TestRig / CoverageCli / the
application map are the **pack**, not core — switch with `python -m crewline pack --set generic` for a non-MOSIP
repo ([Core vs MOSIP pack](#core-vs-mosip-pack)). What a module needs is its **context**
(`<module repo>/.crewline/`) and, for MOSIP API tests, its **validator profile** (`<module repo>/.crewline/qa/profile.yaml`).
The `module-onboarding` task creates both:

```bash
cd validators
python -m crewline discover --repo <path to a clean clone of the module> --module <module>   # read-only on the repo
```

It writes evidence-labelled drafts:
- `<module repo>/.crewline/development/discovery.json` and `local-context.md`: Maven parent, Java version, test libraries, JaCoCo and whether the surefire `argLine` keeps the agent, CI workflows and reusable workflows, DB script layout and unpaired migrations, Helm charts and Dockerfiles, guidance files, per-service test counts and component-test styles, default approved paths;
- `<module repo>/.crewline/qa/module-context.md` and `profile.draft.yaml` (when the repo has `api-test/`): suites, case counts, case-name prefix, `uniqueIdentifier` pattern with match rate, duplicates, unwired YAML, script classes, `idKeyName`s, and a `testcase` baseline run with the draft profile.

Then the `module-onboarder` agent (or you):
1. reads every guidance file it reports (`AGENTS.md`, `CLAUDE.md`, `coverage/AGENTS.md` ...): they win where more specific;
2. runs the inferred build, unit and coverage commands once through `crewline run` and marks them Verified;
3. completes the profile (`extra_id_fields_by_class` from the DEP-001 baseline), copies it to `<module repo>/.crewline/qa/profile.yaml`, re-runs the baseline;
4. records remaining findings as baseline debt (`.crewline/qa/known-issues.md`), so later tasks validate only what they change;
5. gets the module owner's approval (`onboarding_review`).

After that, any task type works for the module. Copying `.crewline/` into the module repository is a
separate task with its own branch and PR.

**Tried on modules it had never seen (2026-10-29, read-only):**

| Module | Found | Draft profile |
|---|---|---|
| keymanager | parent `kernel/pom.xml`, Java 21, JaCoCo attached, 3 Maven modules, 18 MockMvc tests, AGENTS.md | prefix `KeyManager_`, 37/37 IDs match, baseline 0 findings |
| resident-services | JaCoCo present but the parent surefire `argLine` drops `${argLine}` (coverage likely not attaching), 1 YAML that does not parse, 12 unwired YAML | 1,216 cases |
| id-repository | 7 Maven modules, AGENTS.md, 7 unwired YAML | 792 cases |
| pre-registration (control) | matches the hand-written profile: prefix, ID pattern, the same 3 ignored YAML | 6 DEP-001 until `extra_id_fields_by_class` is filled, as in the hand-written profile |

---

## 17. Repository layout

```
crewline/
├── README.md · VERSION
├── core/
│   ├── principles.md            39 rules (18 from the SDET pilot, 21 for the full SDLC)
│   ├── workflow.yaml            the state machine, guards, forced escalation, linked changes
│   ├── workflows/               feature · bug-fix · refactor · dependency-upgrade · database-migration · incident · release · test-automation · module-onboarding · documentation
│   ├── quality-gates.yaml       gates (clarity, fit, branch-env, defect-reports included)
│   ├── permissions.yaml         tiers L0-L3, profiles, 23 actions, agent profiles
│   ├── escalation-policy.yaml   two repair loops, failure classes
│   └── tools.yaml               core capabilities (git, python, gh, docker, …); pack overlays the rest
├── packs/                       ACTIVE (default mosip) · mosip/ · generic/   product plugin (tools + knowledge refs)
├── agents/                      orchestrator + 24 specialists in role folders (see docs/agents-by-role.md):
│   ├── product/ ui-ux/ architecture/ development/ qa/ (SDET lead + 7)
│   └── review/ security/ devops/ operations/ knowledge/
├── validators/
│   ├── crewline/                validator modules (see section 11)
│   └── tests/                   test_validators.py · test_sdlc.py · test_guard.py · test_pack.py · test_setup_scripts.py
├── templates/                   task-manifest + agent-response schemas, PR, issue, subtask, escalation, final report
├── .claude-plugin/              plugin.json (marketplace catalog: mosip-labs root .claude-plugin/marketplace.json)
├── skills/crewline/SKILL.md     the Crewline skill
├── hooks/hooks.json             plugin hooks: SessionStart (module detection) + PreToolUse guard
├── bin/                         crewline, crewline.cmd (validators on PATH)
├── docs/agents-by-role.md       role map
├── adapters/                    claude/ (settings hooks for manual installs) · cursor/ (rule + hooks) · generic/ (ENTRY + git hooks) · hooks/crewline_hook.py · install.py
├── scripts/                     setup.bat · setup.sh  (one-time prereqs + optional adapter install)
├── knowledge/                   decisions/ (ADR-0001..0008) · frameworks/ · mosip/ · known-issues/
├── evals/                       task-scenarios/scenarios.yaml (EV-01..59) · frozen-pack.yaml · cold-start.md · q8-local-stack.md · expected-outcomes/
├── ci/                          GitHub workflow spec (a maintainer with workflow scope copies it to `.github/workflows/crewline.yml`)
└── tasks/                       task workspaces for a Crewline clone (git-ignored); plugin users: <clone>/.crewline-work/tasks/
```

---

## 18. Pilots and lessons

| | PILOT-001 (SDET Architect Assistant) | PILOT-002 (this agent) |
|---|---|---|
| Task | test automation, MOSIP-17633 | bug fix, MOSIP-38854 (NPE in `NotificationService`) |
| Result | [#1088](https://github.com/mosip/pre-registration/pull/1088): 6 TestRig cases, 0 new breaks, bug MOSIP-45714 filed | [#1089](https://github.com/mosip/pre-registration/pull/1089): reproduction 5 tests failing on develop; full suite base 280 / branch 290 with 0 new breaks; diff coverage 100%; 4 component tests |
| Rules it produced | fork model, full suite before commit, single-use approvals, review-comment handling, bug evidence, missing-tool guidance, linked changes | PR watching, diff coverage, component tests when system tests can't run, usage per stage and step |

**PILOT-002 lessons:**
- **Independent reviews paid off.** They found two real behaviour gaps the tests did not:
  - a request's own name was being dropped;
  - the pre-registration-id fallback could not be reached over HTTP.
- **API-level tests found what unit tests could not reach.**
- **Token cost** (first stretch, before the PR follow-ups):
  - each review round cost about 3M total tokens;
  - waiting on background runs cost about 6.7M;
  - a fresh session halved the cost per call.

  Two ways to cut it: settle behaviour questions during requirements, and wake on completion instead of checking.

---

## 19. Status, limits and open questions

| Phase | Status |
|---|---|
| P0 discovery, P1 shared core, P2 SDET slice | done |
| P3 requirements/design, P4 development | P3 gate built (`design_readiness` + `packets/DESIGN.md` before leaving DESIGN); P4 piloted (PILOT-002). Human design walk still `evals/cold-start.md` |
| P5 integrated feature, P6 security/DevOps, P7 release/SRE, P8 scale | P5 kit: `cold-start`. Human walk not piloted. P6: scanner record (DEPSEC-001). P7: `ui: true` ACs fail TRC-008; leaving SYSTEM_TEST needs `evidence/system-tests.json` (SYS-001). P8: `crewline start` is a conductor |

**Open questions** (`knowledge/decisions/open-questions.md`):
- **Q8 cluster:** laptop `local-stack` is gated; a branch image on a shared namespace is a **platform-team** request (`evals/q8-local-stack.md` has the template). Crewline does not provision a cluster.

**Known limits (not closable in this repo):**
- Production actions stay **disabled**.
- **P5** still needs a stranger on a new machine (`evals/cold-start.md`). The kit and the P3 design gate are automated; the walk is not.
- **P7** UI/DSL: contracts only. A `ui: true` AC without `system_test_ids` already fails TRC-008.
- `crewline start` is a conductor (next `--to`). It does not invoke a model.
- Frozen pack + two-model check is rails-only (`evals --pack frozen --run` on Cursor and on Claude).
- Cursor has no API transcript (USE-006). `manifest --apply` stamps a usage_mark; missing marks fail USE-009.
- GitHub Actions for this repo is the spec at `ci/crewline.yml`. A maintainer with workflow scope copies it to `.github/workflows/`.
