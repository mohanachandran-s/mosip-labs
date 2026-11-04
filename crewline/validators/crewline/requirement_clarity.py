"""Requirement-clarity gate: do not start design or tests on a vague story or bug.

  REQ-001  code/test task has no acceptance criteria                                              BLOCK
  REQ-002  an AC is too short or uses a non-observable phrase (should work, TBD, as expected, …) BLOCK
  REQ-003  requirements.open_ambiguities is not 0                                                 BLOCK
  REQ-004  no story/bug identity (story_keys, source_refs or tracker.key)                         BLOCK
  REQ-005  requirements.untrusted_input with a resource limit no AC states (resource_limits)           BLOCK
  REQ-006  the ACs read like parsing untrusted input, and requirements.untrusted_input is unanswered   BLOCK

IMPACT_ANALYSIS and TESTCASE_DESIGN are refused until this PASSes. The agent must not invent
business rules to fill a gap (WAITING_FOR_USER with one focused question instead).

  python -m crewline requirement-clarity --manifest <task-manifest.json>
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .result import BLOCK, Result

STORY_TASKS = {"feature", "bug-fix", "test-automation", "database-migration"}
MIN_LEN = 24
VAGUE = re.compile(
    r"\b(tbd|todo|tbc|as expected|as discussed|as agreed|should work|works fine|"
    r"handle it|appropriately|accordingly|somehow|and so on|to be confirmed|"
    r"not sure|unclear|maybe|later)\b"
    r"|\betc\.?(?:\s|$)",
    re.I,
)


RESOURCE_LIMITS = ("input_size", "nesting_depth", "memory", "time", "error_behaviour")
UNTRUSTED = re.compile(r"\b(pars(e|es|er|ing)|decod(e|es|er|ing)|deseriali[sz]\w*|decompress\w*|untrusted|upload\w*|qr|cbor|zip|xml)\b", re.I)


def run(manifest_path: str) -> Result:
    res = Result("requirement-clarity", inputs={"manifest": manifest_path})
    try:
        doc = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return Result.not_run("requirement-clarity", str(e))
    task_type = doc.get("task_type") or "test-automation"
    if task_type not in STORY_TASKS:
        return res.finish()
    req = doc.get("requirements") or {}
    acs = req.get("acceptance_criteria") or []
    keys = [k for k in (req.get("story_keys") or []) if str(k).strip()]
    refs = [s for s in (req.get("source_refs") or []) if str(s).strip()]
    if (doc.get("tracker") or {}).get("key"):                # the confirmed GitHub issue / Jira item (MAN-017)
        refs.append(doc["tracker"]["key"])
    if not acs:
        res.add("REQ-001", BLOCK, "no acceptance criteria: do not guess the story/bug; ask the owner",
                remediation="requirement-analyst writes AC-1..n with an observable outcome; open_ambiguities until then")
    n_amb = req.get("open_ambiguities")
    if n_amb is None or int(n_amb) != 0:
        res.add("REQ-003", BLOCK, f"open_ambiguities={n_amb!r}: IMPACT_ANALYSIS / TESTCASE_DESIGN stay blocked "
                "(WAITING_FOR_USER with one focused question whose answer changes an expected result)")
    if not keys and not refs:
        res.add("REQ-004", BLOCK, "no story/bug identity (requirements.story_keys, source_refs or tracker.key)",
                remediation="record the Jira/GitHub key before analysis; do not invent the requirement")
    for ac in acs:
        aid = ac.get("id", "?")
        text = " ".join(str(ac.get("text") or "").split())
        if len(text) < MIN_LEN or VAGUE.search(text):
            res.add("REQ-002", BLOCK, f"{aid}: AC is not testable ({text[:80]!r})",
                    evidence=aid, remediation="state actor, observable outcome (status/field/error code/persisted state); "
                    "no 'should work', 'as expected', 'TBD', 'etc.'")
    text = " ".join(str(ac.get("text") or "") for ac in acs)
    if req.get("untrusted_input"):
        stated = {lim for ac in acs for lim in ac.get("resource_limits") or []}
        missing = [lim for lim in RESOURCE_LIMITS if lim not in stated]
        if missing:
            res.add("REQ-005", BLOCK, f"the change parses untrusted input and no AC states: {', '.join(missing)}",
                    remediation="add nfr ACs with the limit, what happens past it, and `resource_limits`; "
                                "a limit the change cannot reach is still stated, with why")
    elif req.get("untrusted_input") is None and UNTRUSTED.search(text):
        res.add("REQ-006", BLOCK, f"the ACs mention {UNTRUSTED.search(text).group(0)!r}: does this parse input it does not control? "
                                  "answer requirements.untrusted_input (true or false)",
                remediation="true needs resource-limit ACs and a threat review before test design")
    res.inputs["acceptance_criteria"] = len(acs)
    return res.finish()
