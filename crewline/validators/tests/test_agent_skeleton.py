"""Agents on `skeleton: 2` (docs/agent-skeleton.md) keep the sections that make them executable, in order."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

AGENTS = Path(__file__).resolve().parents[2] / "agents"
SECTIONS = ["Inputs", "Entry checks", "Produces", "Procedure", "When something fails", "Stop and ask the user",
            "Done when", "Example", "Must not", "Output contract"]
MIGRATED = {"dsl-automation", "api-automation", "failure-analyst", "impact-analyst", "software-developer", "sdet-architect",
            "requirement-analyst", "testcase-designer", "testcase-reviewer", "code-reviewer", "fresh-context-reviewer",
            "defect-analyst"}


def _skeleton2() -> list[Path]:
    return [p for p in sorted(AGENTS.rglob("*.md")) if re.search(r"^skeleton:\s*2\s*$", p.read_text(encoding="utf-8"), re.M)]


def test_migrated_agents_declare_skeleton_2():
    assert MIGRATED <= {p.stem for p in _skeleton2()}


@pytest.mark.parametrize("path", _skeleton2(), ids=lambda p: p.stem)
def test_sections_present_in_order(path):
    text = path.read_text(encoding="utf-8")
    heads = [re.sub(r"\s*\(.*$", "", h).strip() for h in re.findall(r"^## (.+)$", text, re.M)]
    found = [h for h in heads if h in SECTIONS]
    assert found == SECTIONS, f"{path.name}: sections {found}"


@pytest.mark.parametrize("path", _skeleton2(), ids=lambda p: p.stem)
def test_tables_and_commands_are_concrete(path):
    text = path.read_text(encoding="utf-8")

    def body(name: str) -> str:
        m = re.search(rf"^## {re.escape(name)}.*?$(.*?)(?=^## )", text, re.M | re.S)
        return m.group(1) if m else ""

    assert "| Check | How |" in body("Entry checks"), "entry checks must be a Check | How table"
    assert "| What you see | Do |" in body("When something fails"), "failures must be a What you see | Do table"
    assert body("When something fails").count("\n|") >= 5, "at least three failure rows"
    assert "crewline " in body("Done when"), "done must be a crewline command or record, not an opinion"
    assert re.search(r"`crewline [a-z-]+", body("Procedure")), "the procedure names the commands it runs"
