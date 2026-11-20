# Agent definition skeleton (`skeleton: 2`)

A role description ("implement approved cases, then run them") tells an agent *what* to do, not *how* or *when it is
finished*. Agents that only had that guessed run commands, retried unchanged code and stopped at "looks done". An
agent with `skeleton: 2` in its front matter carries these sections, in this order, and
`validators/tests/test_agent_skeleton.py` keeps them there:

| Section | What it holds |
|---|---|
| `## Inputs` | what the agent reads, and where it comes from (manifest field, packet, knowledge file, the user) |
| `## Entry checks` | a table `Check \| How`: each a command or a field to read, and what it means when it fails. Stop with NEEDS_INPUT or BLOCKED on the first failure, before any work |
| `## Produces` | files, records (`<task>/evidence/*.json`) and manifest fields the agent leaves behind |
| `## Procedure` | numbered steps, each naming the command it runs (`crewline ...`, `mvn ...`) or the file it edits |
| `## When something fails` | a table `What you see \| Do`: rule ids and log signatures, each with one action |
| `## Stop and ask the user` | the points where the agent must hand the decision to a person |
| `## Done when` | commands that must exit 0 (or records that must be PASS); "done" is never the agent's opinion |
| `## Example` | one real run from a pilot task, short, with the ids and results it produced |
| `## Must not` | the hard prohibitions |
| `## Output contract` | the agent-response block |

Rules for writing one:
- Cite a command with its real flags (check `python -m crewline <cmd> --help`), a rule by its id, a file by its path.
- Put framework detail in `knowledge/frameworks/*.md` and module detail in the module's `.crewline/`; the agent points
  to them instead of copying them.
- The example is a real run (task id, ids, counts). Leave it out rather than invent one; say which pilot it came from.

- An agent with a read-only profile cannot run `crewline`: its Procedure names the command the orchestrator runs for
  it, and its Done when is that command's result.

Agents on skeleton 2: requirement-analyst, impact-analyst, testcase-designer,
testcase-reviewer, software-developer, api-automation, dsl-automation, failure-analyst, defect-analyst, code-reviewer,
fresh-context-reviewer, sdet-architect. The others keep the 0.8 layout until they are rewritten the same way.
