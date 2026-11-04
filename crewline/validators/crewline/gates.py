"""`crewline gates refresh`: re-run every gate a command decides and record what it returned on the task manifest.

Replaces per-task scripts (PP-PYSDK's repair4_gates.py set gates to PASS by hand). A status here only ever comes from a run:
  GAT-001  a validator gate: re-run on the task's repository (validators read the manifest for repo and base)
  GAT-002  a gate is not PASS after the refresh                                                            BLOCK
  GAT-003  not refreshed: a review, human or environment-bound gate; says who or what produces it           INFO
  GAT-004  the task has no repository path or base to run against                                           BLOCK

Evidence gates (build, unit_tests, component_tests, dependency_security, system_tests) re-check their `crewline run` records,
so a record made before the repair is stale. `--rerun` executes the recorded command again first; never for system tests,
which use a shared environment. Each run is stored at <task>/evidence/gates/<gate>.json.
A gate recorded NOT_APPLICABLE stays as it is: that is a decision, not a result.
"""
from __future__ import annotations

import json
import os
import shlex
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import (branch, branch_env, change_scope, ci_coverage, coding_standards, dco, defect_reports, evidence, manifest, migration,
               requirement_clarity, requirement_fit, robustness, secrets_scan, test_integrity, traceability)
from .gitio import changed_files, head
from .result import BLOCK, INFO, Result

REPO_LESS = {
    "requirement_clarity": requirement_clarity.run,
    "branch_environment": branch_env.run,
    "defect_reports": defect_reports.run,
    "traceability": traceability.run,
    "ci_coverage": ci_coverage.run,
}
RECORDS = {
    "build": "build*.json",
    "unit_tests": "unit*.json",
    "component_tests": "component*.json",
    "dependency_security": "dependency-security*.json",
    "system_tests": "system-tests*.json",
}
RERUN = {"build", "unit_tests", "component_tests", "dependency_security"}
ON_COMMAND = {
    "diff_coverage": "crewline diff-coverage --repo <dir> --base <base> --jacoco <report>; it needs the coverage report path",
    "full_regression": "crewline regression --baseline <base report> --candidate <branch report> --out <task>/evidence/full-regression.json",
    "token_budget": "crewline usage --manifest <m> --transcript <session.jsonl>",
    "pr_ci": "gh pr checks <n> --repo <upstream_repo>",
}


class Repo:
    def __init__(self, manifest_path: str, doc: dict, entry: dict):
        self.manifest, self.doc, self.entry = manifest_path, doc, entry
        self.task_dir = Path(manifest_path).parent
        self.name = entry.get("name", "")
        self.path = entry.get("path") or next((w.get("path") for w in (doc.get("workspace") or {}).get("worktrees") or []
                                               if w.get("purpose") == "task" and not w.get("removed_at")), "")
        self.base = entry.get("baseline_commit") or entry.get("base_commit") or entry.get("base_ref") or ""


def _test_integrity(r: Repo) -> Result:
    approvals = r.task_dir / "approvals.yaml"
    return test_integrity.run(r.path, r.base, str(approvals) if approvals.is_file() else None,
                              r.doc.get("task_type") == "refactor")


def _coding_standards(r: Repo) -> Result:
    records = sorted(str(p) for p in (r.task_dir / "evidence").glob("coding-standards*.json"))
    return coding_standards.run(r.path, r.base, r.manifest, records)


def _secrets(r: Repo) -> Result:
    files = [f for f, st in changed_files(r.path, r.base).items() if st != "D"]
    return secrets_scan.run(files, r.path)


def _branch_scope(r: Repo) -> Result:
    remote, target = r.entry.get("upstream_remote") or "upstream", r.entry.get("base_branch")
    if not target:
        return Result.not_run("branch-scope", "scope.repositories[].base_branch is not recorded")
    return branch.run(r.path, f"{remote}/{target}", r.base, branch.manifest_keys(r.manifest), r.entry.get("allow_commits") or [])


REPO_RUNNERS = {
    "coding_standards": _coding_standards,
    "robustness": lambda r: robustness.run(r.path, r.base, r.manifest),
    "requirement_fit": lambda r: requirement_fit.run(r.path, r.base, r.manifest),
    "change_scope": lambda r: change_scope.run(r.path, r.base, r.manifest),
    "test_integrity": _test_integrity,
    "migration_pairing": lambda r: migration.run(r.path, r.base),
    "secrets": _secrets,
    "dco": lambda r: dco.run(r.path, f"{r.base}..HEAD"),
    "branch_scope": _branch_scope,
}
PER_REPO = {"dco", "branch_scope"}                # gate names MAN-007 expects per repository: `dco:<name>`


def _records(gate: str, r: Repo, rerun: bool) -> Result:
    folder = r.task_dir / "evidence"
    found = sorted(p for p in folder.glob(RECORDS[gate]) if p.is_file())
    if not found:
        return Result.not_run(gate, f"no {folder.name}/{RECORDS[gate]}: record it with crewline run --repo {r.path} --out "
                                    f"<task>/evidence/{RECORDS[gate].replace('*', '')} -- <command>")
    res = Result(gate, inputs={"records": [str(p) for p in found]})
    for p in found:
        one = _rerun(p, r) if rerun and gate in RERUN else None
        if one is None or one.status == "PASS":
            one = evidence.run(str(p), r.path)
        res.findings += one.findings
        if one.status == "NOT_RUN":
            res.status = "NOT_RUN"
            res.summary = one.summary
    return res.finish()


def _rerun(record: Path, r: Repo) -> Result:
    rec = json.loads(record.read_text(encoding="utf-8"))
    command = shlex.split(rec.get("command") or "", posix=os.name != "nt")
    if not command:
        return Result.not_run("run", f"{record.name} has no command")
    return evidence.capture(r.path, str(record), command, rec.get("report"), rec.get("cwd"))


def _deterministic(results: dict[str, Result], r: Repo) -> Result:
    """The composite: secrets, plus the TestRig checks for a module with api-test/ (they need its profile and commons)."""
    res = Result("deterministic_validation", inputs={"repo": r.path})
    if (Path(r.path) / "api-test").is_dir():
        return Result.not_run("deterministic_validation", "module has api-test/: run crewline testcase, expected-diff and "
                                                          "coverage (quality-gates.yaml), they need its profile and commons")
    for gate, one in results.items():
        if gate in ("secrets", "test_integrity", "coding_standards", "robustness", "requirement_fit", "migration_pairing"):
            res.findings += [f for f in one.findings if f.severity == BLOCK]
            if one.status == "NOT_RUN":
                res.status = "NOT_RUN"
                res.summary = f"{gate}: {one.summary}"
    return res.finish()


def _select(ctx: manifest.Ctx, state: str | None, every: bool) -> list[str]:
    spec = yaml.safe_load(manifest.GATES.read_text(encoding="utf-8"))["gates"]
    recorded = {g.get("gate") for g in ctx.doc.get("gates", [])}
    needed = recorded | set(ctx.tpl.get("required_gates", []))
    out = []
    for name, g in spec.items():
        if name in ctx.tpl.get("not_applicable_gates", {}) and name not in ctx.tpl.get("required_gates", []):
            continue
        if g.get("state") not in ctx.path or name not in needed:
            continue
        if every or g.get("state") == state:
            out.append(name)
    return out


def _worst(results: list[Result]) -> Result:
    for status in ("NOT_RUN", "FAIL"):
        for one in results:
            if one.status == status:
                return one
    return results[0]


def refresh(manifest_path: str, state: str | None = None, every: bool = False, rerun: bool = False) -> Result:
    res = Result("gates-refresh", inputs={"manifest": manifest_path})
    doc = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    ctx = manifest.Ctx(doc)
    cur = doc.get("current_state")
    state = state or (doc.get("resume_state") if cur in ("WAITING_FOR_USER", "S_ESCALATE") else cur)
    names = _select(ctx, state, every)
    res.inputs.update(state=state, gates=names)
    repos = [Repo(manifest_path, doc, e) for e in manifest.changed_repos(doc)]
    if not repos:                                  # no entry yet: the worktree recorded on the task is the repository
        repos = [Repo(manifest_path, doc, {})]
    runnable = [r for r in repos if r.path and r.base and Path(r.path).is_dir()]
    if len(runnable) < len(repos) and any(n in REPO_RUNNERS or n in RECORDS or n == "deterministic_validation" for n in names):
        res.add("GAT-004", BLOCK, "scope.repositories[] needs path and baseline_commit (or base_ref) of an existing checkout; "
                                  "repository gates are recorded NOT_RUN")

    recorded = {g.get("gate"): g for g in doc.get("gates", [])}
    fresh: dict[str, tuple[str, str, list[str], str]] = {}
    ran: dict[str, Result] = {}
    out_dir = Path(manifest_path).parent / "evidence" / "gates"
    for name in sorted(names, key=lambda n: n == "deterministic_validation"):
        if (recorded.get(name) or {}).get("status") == "NOT_APPLICABLE":
            res.add("GAT-003", INFO, f"{name}: stays NOT_APPLICABLE ({(recorded[name].get('rationale') or 'no rationale')[:80]})")
            continue
        per_repo: list[tuple[Repo | None, Result]] = []
        if name in REPO_LESS:
            per_repo = [(None, REPO_LESS[name](manifest_path))]
        elif name in REPO_RUNNERS or name in RECORDS or name == "deterministic_validation":
            for r in repos:
                if r not in runnable:
                    per_repo.append((r, Result.not_run(name, "no repository path or base on the manifest")))
                elif name in RECORDS:
                    per_repo.append((r, _records(name, r, rerun)))
                elif name == "deterministic_validation":
                    per_repo.append((r, _deterministic(ran, r)))
                else:
                    per_repo.append((r, _safe(REPO_RUNNERS[name], r, name)))
        else:
            spec = ON_COMMAND.get(name) or _who(name)
            res.add("GAT-003", INFO, f"{name}: not refreshed; {spec}")
            continue
        ran[name] = _worst([one for _, one in per_repo])
        split = name in PER_REPO and len(repos) > 1
        groups = [(f"{name}:{r.name}", [(r, one)]) for r, one in per_repo] if split else [(name, per_repo)]
        for gate, items in groups:
            one = _worst([o for _, o in items])
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / f"{gate.replace(':', '-')}.json"
            out.write_text(one.to_json() + "\n", encoding="utf-8")
            blocks = sorted({f.rule_id for _, o in items for f in o.findings if f.severity == BLOCK and not f.approved_by})
            why = f"{one.summary}" + (f" ({', '.join(blocks)})" if blocks else "") + " [crewline gates refresh]"
            commit = _head(items[0][0]) if items[0][0] else _head(runnable[0] if runnable else None)
            fresh[gate] = (one.status, why, [out.relative_to(Path(manifest_path).parent).as_posix()], commit)
            if one.status != "PASS":
                res.add("GAT-002", BLOCK, f"{gate}: {one.status}: {why}", remediation="fix it, or a human records an exception")
            else:
                res.add("GAT-001", INFO, f"{gate}: PASS")
    _record(manifest_path, fresh)
    if not names:
        res.add("GAT-003", INFO, f"no gate belongs to {state} on this template; --all refreshes every gate a command decides")
    return res.finish()


def _safe(runner, r: Repo, name: str) -> Result:
    try:
        return runner(r)
    except Exception as e:                 # a validator that crashes must never read as PASS
        return Result.not_run(name, f"{type(e).__name__}: {e}")


def _head(r: Repo | None) -> str:
    try:
        return head(r.path) if r and r.path else ""
    except RuntimeError:
        return ""


def _who(name: str) -> str:
    g = yaml.safe_load(manifest.GATES.read_text(encoding="utf-8"))["gates"].get(name) or {}
    if g.get("agent"):
        return f"its result comes from {g['agent']}"
    return f"no single command here decides it ({g.get('enforced_by') or 'a person'}); see core/quality-gates.yaml"


def _record(manifest_path: str, fresh: dict) -> None:
    if not fresh:
        return
    p = Path(manifest_path)
    doc = json.loads(p.read_text(encoding="utf-8"))             # re-read: a validator may have written to the task
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    gates = doc.setdefault("gates", [])
    for gate, (status, why, refs, commit) in fresh.items():
        entry = next((g for g in gates if g.get("gate") == gate), None)
        if entry is None:
            entry = {"gate": gate}
            gates.append(entry)
        entry.update(status=status, rationale=why, evidence_refs=refs, checked_at_commit=commit, checked_at=at)
    p.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
