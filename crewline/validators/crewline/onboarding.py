"""The two module-onboarding gates, decided from what `crewline discover` and `crewline run` wrote, so nobody types them.

  python -m crewline gates refresh --id <task>          # decides onboarding_discovery / onboarding_baseline with the rest

onboarding_discovery (IMPACT_ANALYSIS):
  ONB-001  no discovery.json from `crewline discover` (looked in the task's onboarding folders)                 BLOCK
  ONB-002  a draft is missing next to discovery.json (local-context.md; qa/ drafts when the repo has api-test)   BLOCK
  ONB-003  discovery read another commit than the task's base (`discover --base`, DSC-012)                       BLOCK
onboarding_baseline (KNOWLEDGE_UPDATE):
  ONB-004  no approval onboarding_review: the module owner has not reviewed the drafts                          BLOCK
  ONB-005  the repo has api-test/ but no reviewed .crewline/qa/profile.yaml                                     BLOCK
  ONB-006  a baseline build record (evidence/build*.json, baseline*.json) failed or was stopped                  BLOCK
  ONB-007  no baseline build record: the drafts' build commands stay Inferred                                    WARN

The onboarding folder is <clone>/.crewline-work/onboarding (discover's default out) or the same folder under a task
worktree; `discover --out` elsewhere is found when it sits in the task folder (<task>/onboarding).
"""
from __future__ import annotations

import json
from pathlib import Path

from . import manifest
from .result import BLOCK, INFO, WARN, Result


def _clone(manifest_path: str) -> Path | None:
    m = Path(manifest_path).resolve()
    return m.parents[3] if len(m.parents) > 3 and m.parents[2].name == ".crewline-work" else None


def _folders(manifest_path: str, doc: dict) -> list[Path]:
    out = [Path(manifest_path).resolve().parent / "onboarding"]
    clone = _clone(manifest_path) or (Path(doc["workspace"]["clone"]) if (doc.get("workspace") or {}).get("clone") else None)
    if clone:
        out.append(clone / ".crewline-work" / "onboarding")
    for w in (doc.get("workspace") or {}).get("worktrees") or []:
        if w.get("path"):
            out.append(Path(w["path"]) / ".crewline-work" / "onboarding")
    for r in (doc.get("scope") or {}).get("repositories") or []:
        if r.get("path"):
            out.append(Path(r["path"]) / ".crewline-work" / "onboarding")
    return list(dict.fromkeys(out))


def _discovery(manifest_path: str, doc: dict) -> tuple[Path | None, dict]:
    found = [f / ".crewline" / "development" / "discovery.json" for f in _folders(manifest_path, doc)]
    found = [p for p in found if p.is_file()]
    if not found:
        return None, {}
    p = max(found, key=lambda x: x.stat().st_mtime)
    try:
        return p, json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return p, {}


def _base_commit(doc: dict) -> str:
    for r in manifest.changed_repos(doc) or (doc.get("scope") or {}).get("repositories") or []:
        c = r.get("baseline_commit") or r.get("base_commit")
        if c:
            return c
    return ""


def discovery_gate(manifest_path: str) -> Result:
    res = Result("onboarding_discovery", inputs={"manifest": manifest_path})
    doc = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    path, facts = _discovery(manifest_path, doc)
    if not path:
        res.add("ONB-001", BLOCK, "no discovery.json: run `crewline discover --repo <clone> --module <name> --base <base>`",
                evidence=", ".join(str(f) for f in _folders(manifest_path, doc)))
        return res.finish()
    res.inputs["discovery"] = str(path)
    root = path.parents[2]
    need = [".crewline/development/local-context.md", ".crewline/module.yaml"]
    if facts.get("api_test"):
        need += [".crewline/qa/module-context.md", ".crewline/qa/profile.draft.yaml"]
    for rel in need:
        if not (root / rel).is_file():
            res.add("ONB-002", BLOCK, f"draft {rel} missing next to {path.name}: re-run discover (--force after review)")
    base = _base_commit(doc)
    head = facts.get("head", "")
    if base and head and not (head.startswith(base) or base.startswith(head)):
        res.add("ONB-003", BLOCK, f"discovery read {facts.get('branch', '?')} @ {head[:10]}, the task base is {base[:10]}: "
                "run discover on a checkout of the base (`--base` names the difference)")
    elif (facts.get("base_diff") or {}).get("base_commit"):
        res.add("ONB-003", BLOCK, f"discover reported HEAD != --base {facts['base_diff']['base']} (DSC-012): re-run on the base")
    if not res.findings:
        res.add("ONB-001", INFO, f"discovery @ {head[:10]} with drafts in {root}")
    return res.finish()


def baseline_gate(manifest_path: str) -> Result:
    res = Result("onboarding_baseline", inputs={"manifest": manifest_path})
    doc = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    approved = [a for a in doc.get("approvals", []) if a.get("action") == "onboarding_review"
                and all(a.get(k) for k in manifest.APPROVAL_FIELDS)]
    if not approved:
        res.add("ONB-004", BLOCK, "no approval onboarding_review: the module owner reviews the drafts "
                "(`crewline approval add --for onboarding_review --approver <who> --reason ... --scope ...`)")
    path, facts = _discovery(manifest_path, doc)
    if facts.get("api_test"):
        root = path.parents[2]
        repos = [Path(r["path"]) for r in (doc.get("scope") or {}).get("repositories") or [] if r.get("path")]
        if not any((p / ".crewline" / "qa" / "profile.yaml").is_file() for p in [root, *repos]):
            res.add("ONB-005", BLOCK, "api-test/ found but no reviewed .crewline/qa/profile.yaml (rename the draft after review)")
    ev = Path(manifest_path).resolve().parent / "evidence"
    records = sorted(set(ev.glob("build*.json")) | set(ev.glob("baseline*.json"))) if ev.is_dir() else []
    if not records:
        res.add("ONB-007", WARN, "no baseline build record: run the build once with `crewline run --out "
                "<task>/evidence/baseline-build.json --stall 10 -- <build>` so the commands are Verified")
    for r in records:
        try:
            rec = json.loads(r.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if rec.get("stopped") or rec.get("exit_code") not in (0, None):
            res.add("ONB-006", BLOCK, f"{r.name}: exit {rec.get('exit_code')}"
                    + (f", stopped ({rec['stopped']})" if rec.get("stopped") else ""), path=str(r))
        else:
            res.add("ONB-006", INFO, f"{r.name}: exit 0 in {rec.get('duration_s', '?')}s", path=str(r))
    return res.finish()
