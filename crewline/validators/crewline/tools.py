"""Check local tool capabilities (core/tools.yaml + active pack overlay) and print enable steps.

  TOOL-001  required capability missing (enable steps attached)   BLOCK
  TOOL-002  capability present                                     INFO
  TOOL-003  not probed: its `needed_for` names only other task types  INFO
`--task-type <t>` (default: the active task's type) skips capabilities whose `needed_for` lists task types and not this
one, e.g. helm (release, devops changes) for a dependency-upgrade. Capabilities needed for states or validators are
always probed.
Connector/browser capabilities (`check.adapter`) are reported as NOT_CHECKED: the adapter verifies them by
calling the tool, and must show the same `enable` steps when they are missing.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess

from . import pack
from .manifest import TEMPLATES
from .result import BLOCK, INFO, WARN, Result

ALIASES = {"devops": "release"}          # `needed_for: [devops changes]` -> the release template


def task_types() -> list[str]:
    return sorted(p.stem for p in TEMPLATES.glob("*.yaml"))


def _types_named(needed_for: list) -> set[str] | None:
    """Task types a `needed_for` list is limited to, or None when an entry is a state / validator / anything else."""
    known, named = task_types(), set()
    for entry in needed_for:
        word = re.split(r"[\s(,]", str(entry).strip(), maxsplit=1)[0].lower()
        if word in ALIASES:
            word = ALIASES[word]
            if word is None:
                return None
        hit = [t for t in known if t == word or t.startswith(word + "-")]
        if not hit:
            return None
        named.update(hit)
    return named or None


def _active_type() -> str | None:
    from .workspace import active_manifest
    path, _ = active_manifest()
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("task_type") if path else None
    except (OSError, ValueError):
        return None


def run(only: list[str] | None = None, task_type: str | None = None) -> Result:
    spec = pack.merged_tools()["capabilities"]
    task_type = task_type or _active_type()
    res = Result("tools", inputs={"pack": pack.active_id(), "task_type": task_type or ""})
    for name, cap in spec.items():
        if only and name not in only:
            continue
        limited = _types_named(cap.get("needed_for", []))
        if task_type and not only and limited is not None and task_type not in limited:
            res.add("TOOL-003", INFO, f"{name}: not probed, needed for {', '.join(sorted(limited))} only")
            continue
        check = cap.get("check", {})
        steps = "\n".join(f"  {i}. {s}" for i, s in enumerate(cap.get("enable", []), 1))
        if "adapter" in check:
            res.add("TOOL-000", WARN, f"{name}: NOT_CHECKED here ({check['adapter']}); adapter must verify",
                    remediation=steps)
            continue
        cmd = check.get("command", "")
        if "<" in cmd:                      # needs task parameters (e.g. upstream repo); checked at REPO_READY
            continue
        exe = cmd.split()[0]
        ok = shutil.which(exe) is not None
        out = ""
        if ok:
            p = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            out = (p.stdout + p.stderr).strip()
            ok = p.returncode == 0 and (check.get("expect", "") in out)
        if ok:
            res.add("TOOL-002", INFO, f"{name}: available ({out.splitlines()[0][:60] if out else exe})")
        else:
            res.add("TOOL-001", BLOCK, f"{name}: not available; needed for {', '.join(map(str, cap.get('needed_for', [])))}",
                    remediation="Enable it:\n" + steps)
    return res.finish()
