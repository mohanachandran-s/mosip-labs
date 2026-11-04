"""Does CI run the test suites this task ran? A suite only the author runs protects nobody after the merge.

  CIC-001  a unit or component suite recorded with `crewline run` is run by no CI workflow             BLOCK
  CIC-002  a system suite is run by no CI workflow (they often need a deployed build)                  WARN
  CIC-003  no test suite among the task's `crewline run` records                                       INFO

Compares the records' commands with `.github/workflows/*.yml` of the task's repository, not with PR check names: a check name
does not say what it ran. A heuristic: tool family plus project folder in the same workflow file. A wrong BLOCK costs one
attributed exception (gate `ci_coverage`, with its follow-up, MAN-019); that is how a known gap is accepted.

  python -m crewline ci-coverage --manifest <task-manifest.json>
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import manifest
from .result import BLOCK, INFO, WARN, Result

FAMILIES = {   # what a suite run looks like in a record -> what running it looks like in a workflow
    "pytest": (r"\bpytest\b|\btox\b|\bnox\b|\bunittest\b", r"\bpytest\b|\btox\b|\bnox\b|\bunittest\b"),
    "node tests": (r"\bjest\b|\bvitest\b|\bmocha\b|\b(npm|pnpm|yarn)(\.cmd)?\s+(run\s+)?(t|test)\b",
                   r"\bjest\b|\bvitest\b|\bmocha\b|\b(npm|pnpm|yarn)\s+(run\s+)?(t|test)\b"),
    "maven": (r"\bmvnw?(\.cmd)?\b.*\b(test|verify|install)\b", r"\bmvnw?\b|maven"),
    "gradle": (r"\bgradlew?(\.bat)?\b.*\b(test|check|build)\b", r"\bgradlew?\b|gradle-"),
    "go test": (r"\bgo\s+test\b", r"\bgo\s+test\b"),
}
NOT_SUITES = ("dependency-security", "coding-standards", "reproduction", "full-regression", "build")


def _family(command: str) -> str | None:
    for name, (recorded, _) in FAMILIES.items():
        if re.search(recorded, command, re.I):
            return name
    return None


def _project(rec: dict, root: Path) -> str | None:
    """The suite's folder in the repository; None for a run elsewhere (a base worktree, the task folder)."""
    try:
        return Path(rec.get("cwd") or root).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def _in_ci(family: str, project: str, workflows: list[str]) -> bool:
    tool = FAMILIES[family][1]
    where = rf"(?<![\w-]){re.escape(project)}(?![\w-])"
    return any(re.search(tool, text, re.I) and (project == "." or re.search(where, text)) for text in workflows)


def run(manifest_path: str) -> Result:
    res = Result("ci-coverage", inputs={"manifest": manifest_path})
    try:
        doc = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return Result.not_run("ci-coverage", str(e))
    repo = next((r.get("path") for r in manifest.changed_repos(doc) if r.get("path")), "")
    if not repo or not Path(repo).is_dir():
        return Result.not_run("ci-coverage", "no repository checkout on the manifest (scope.repositories[].path)")
    root = Path(repo)
    workflows = [p.read_text(encoding="utf-8", errors="replace") for p in sorted((root / ".github" / "workflows").glob("*.y*ml"))]
    accepted = next((e.get("approver") for e in doc.get("exceptions") or []
                     if e.get("gate") == "ci_coverage" and all(e.get(k) for k in manifest.APPROVAL_FIELDS)), None)
    seen: set[tuple[str, str]] = set()
    records = sorted((Path(manifest_path).parent / "evidence").glob("*.json"), key=lambda r: (r.name.startswith("system-tests"), r.name))
    for p in records:                     # a suite run as both unit and system tests counts as unit: that is the stricter rule
        if p.name.startswith(NOT_SUITES):
            continue
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        family = _family(str(rec.get("command") or "")) if isinstance(rec, dict) else None
        if not family:
            continue
        project = _project(rec, root)
        if project is None or (family, project) in seen:
            continue
        seen.add((family, project))
        if _in_ci(family, project, workflows):
            continue
        what = f"{family} in {project}/ ({p.name})"
        if p.name.startswith("system-tests"):
            res.add("CIC-002", WARN, f"system suite {what} is run by no CI workflow")
        else:
            f = res.add("CIC-001", BLOCK, f"{what} is run by no CI workflow: it protects nothing after the merge",
                        remediation="add the suite to CI, or a human records an exception on gate ci_coverage with its follow-up")
            f.approved_by = accepted
    if not seen:
        res.add("CIC-003", INFO, "no test suite among the task's crewline run records")
    res.inputs["suites"] = sorted(f"{a} in {b}" for a, b in seen)
    return res.finish()
