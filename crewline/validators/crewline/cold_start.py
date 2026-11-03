"""New-machine kit for Crewline itself. Not a MOSIP P5 and not a provisioned cluster.

  python -m crewline cold-start

  CS-001  frozen eval pack (expected-outcomes + nodeids) FAIL                                      BLOCK
  CS-002  adapter --sync/--check FAIL for cursor, claude or generic                                BLOCK
  CS-003  synthetic feature --apply sequence does not refuse then accept the rails                 BLOCK
  CS-004  remaining human steps (evals/cold-start.md)                                              INFO
  CS-005  ci/crewline.yml missing required rails jobs, or live workflow drifted                    BLOCK

A PASS here means this clone can enforce the rails. A real feature on a stranger's machine is
evals/cold-start.md — that still needs a module clone, a local-stack, and no Crewline author in the chat.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from . import adapter_check, evals_run, manifest, pack
from .result import BLOCK, INFO, Result
from .task import skeleton

CORE = Path(__file__).resolve().parents[2]
INSTALL = CORE / "adapters" / "install.py"
PLAYBOOK = CORE / "evals" / "cold-start.md"
CI_SPEC = CORE / "ci" / "crewline.yml"
CI_NEEDLES = ("python -m pytest", "evals --pack frozen", "adapter", "cold-start")


def _write(dir_path: Path, doc: dict, name: str = "task-manifest.json") -> str:
    p = dir_path / name
    p.write_text(json.dumps(doc), encoding="utf-8")
    return str(p)


def _synthetic(res: Result) -> None:
    """Prove --apply refuses IMPACT without clarity or a tracker ticket and REVIEW without fit, then accepts both."""
    d = skeleton("cold-start-synth", "feature")
    d["current_state"] = "ACCEPTANCE_REVIEW"
    d["gates"] = [{"gate": "input_readiness", "status": "PASS"}]
    d["requirements"] = {
        "source_refs": ["MOSIP-0"],
        "acceptance_criteria": [{"id": "AC-1", "text": "POST /x with a missing name returns 400 and error MOSIP-X"}],
        "open_ambiguities": 0,
    }
    with tempfile.TemporaryDirectory(prefix="crewline-cs-") as raw:
        root = Path(raw)
        path = _write(root, d)
        first = manifest.apply(path, "IMPACT_ANALYSIS")
        if first.status != "FAIL" or "requirement_clarity" not in first.to_text() or "MAN-017" not in first.to_text():
            res.add("CS-003", BLOCK, "synthetic IMPACT_ANALYSIS must FAIL until requirement_clarity PASS and a tracker "
                                     "ticket is confirmed (MAN-017)", evidence=first.to_text()[-800:])
            return
        d["tracker"] = {"system": "jira", "key": "MOSIP-0", "found_by": "request", "confirmed_by": "cold-start user",
                        "subtasks": [{"kind": k, "key": f"MOSIP-{i}"} for i, k in
                                     enumerate(manifest.load_workflow()["subtask_kinds"], 1)]}
        d["gates"] += [{"gate": "requirement_clarity", "status": "PASS"},
                       {"gate": "acceptance_readiness", "status": "PASS"}]
        path = _write(root, d, "clarity.json")
        (root / "packets").mkdir(exist_ok=True)
        (root / "packets/ACCEPTANCE_REVIEW.md").write_text("agent: orchestrator\ncontext_id: main\nverdict: PASS\n",
                                                           encoding="utf-8")
        self_review = manifest.transition(path, "IMPACT_ANALYSIS")
        if self_review.status != "FAIL" or "PKT-004" not in self_review.to_text():
            res.add("CS-003", BLOCK, "synthetic IMPACT_ANALYSIS must FAIL while the orchestrator signs ACCEPTANCE_REVIEW (PKT-004)",
                    evidence=self_review.to_text()[-800:])
            return
        (root / "packets/ACCEPTANCE_REVIEW.md").write_text(
            "agent: acceptance-reviewer\ncontext_id: cs-acceptance-1\nverdict: PASS\n", encoding="utf-8")
        second = manifest.apply(path, "IMPACT_ANALYSIS")
        if second.status != "PASS":
            res.add("CS-003", BLOCK, "synthetic IMPACT_ANALYSIS must PASS once clarity gates are recorded",
                    evidence=second.to_text()[-800:])
            return
        d["current_state"] = "UNIT_INTEGRATION_TEST"
        d["gates"] = [{"gate": "deterministic_validation", "status": "PASS"}]
        path = _write(root, d, "review.json")
        third = manifest.apply(path, "IMPLEMENTATION_REVIEW")
        text = third.to_text()
        if third.status != "FAIL" or "requirement_fit" not in text:
            res.add("CS-003", BLOCK, "synthetic IMPLEMENTATION_REVIEW must FAIL until requirement_fit PASS",
                    evidence=text[-800:])
            return
        d["gates"] += [{"gate": "coding_standards", "status": "PASS"},
                       {"gate": "robustness", "status": "PASS"},
                       {"gate": "requirement_fit", "status": "PASS"}]
        path = _write(root, d, "review-ok.json")
        (Path(path).parent / "packets").mkdir(exist_ok=True)
        (Path(path).parent / "packets/IMPLEMENTATION.md").write_text("ACs: AC-1\npaths: src/**\nFAIL findings: none\n",
                                                                    encoding="utf-8")
        ev = Path(path).parent / "evidence"
        ev.mkdir(exist_ok=True)
        (ev / "dependency-security.json").write_text(
            json.dumps({"command": "mvn -B org.owasp:dependency-check-maven:check", "exit_code": 0,
                        "started_at": "2026-10-28T10:00:00Z", "commit": "abc", "worktree_digest": "0", "log": "x.log"}),
            encoding="utf-8")
        fourth = manifest.apply(path, "IMPLEMENTATION_REVIEW")
        if fourth.status != "PASS":
            res.add("CS-003", BLOCK, "synthetic IMPLEMENTATION_REVIEW must PASS once fit/standards/robustness PASS",
                    evidence=fourth.to_text()[-800:])
            return
        # P3: DESIGN -> TESTCASE_DESIGN refused until design_readiness
        d["current_state"] = "DESIGN"
        d["gates"] = [{"gate": "requirement_clarity", "status": "PASS"},
                      {"gate": "acceptance_readiness", "status": "PASS"}]
        path = _write(root, d, "p3.json")
        p3_fail = manifest.apply(path, "TESTCASE_DESIGN")
        if p3_fail.status != "FAIL" or "design_readiness" not in p3_fail.to_text():
            res.add("CS-003", BLOCK, "synthetic TESTCASE_DESIGN must FAIL until design_readiness PASS (P3)",
                    evidence=p3_fail.to_text()[-800:])
            return
        d["gates"].append({"gate": "design_readiness", "status": "PASS"})
        path = _write(root, d, "p3-ok.json")
        (Path(path).parent / "packets").mkdir(exist_ok=True)
        (Path(path).parent / "packets/DESIGN.md").write_text("ACs: AC-1\noptions: A vs B\nrecommendation: A\n",
                                                            encoding="utf-8")
        p3_ok = manifest.apply(path, "TESTCASE_DESIGN")
        if p3_ok.status != "PASS":
            res.add("CS-003", BLOCK, "synthetic TESTCASE_DESIGN must PASS once design_readiness is recorded",
                    evidence=p3_ok.to_text()[-800:])


def run() -> Result:
    res = Result("cold-start", inputs={"pack": pack.active_id(), "playbook": str(PLAYBOOK)})
    frozen = evals_run.run(execute=False, pack="frozen")
    if frozen.status == "FAIL":
        for f in frozen.findings:
            if f.severity == BLOCK:
                res.add("CS-001", BLOCK, f"frozen pack: {f.rule_id} {f.message}", evidence=f.evidence)
    elif frozen.status == "NOT_RUN":
        res.add("CS-001", BLOCK, f"frozen pack not run: {frozen.summary}")
    with tempfile.TemporaryDirectory(prefix="crewline-adp-") as raw:
        target = Path(raw)
        for platform in ("cursor", "claude", "generic"):
            check = adapter_check.run(platform, str(target), sync=True)
            if check.status == "FAIL":
                for f in check.findings:
                    res.add("CS-002", BLOCK, f"{platform}: {f.message}", evidence=platform)
            elif check.status == "NOT_RUN":
                res.add("CS-002", BLOCK, f"{platform}: adapter-check not run: {check.summary}", evidence=platform)
    _synthetic(res)
    if not CI_SPEC.is_file():
        res.add("CS-005", BLOCK, "ci/crewline.yml missing (spec for GitHub Actions)")
    else:
        text = CI_SPEC.read_text(encoding="utf-8")
        missing = [n for n in CI_NEEDLES if n not in text]
        if missing:
            res.add("CS-005", BLOCK, f"ci/crewline.yml missing: {', '.join(missing)}")
        live = CORE.parent / ".github" / "workflows" / "crewline.yml"
        if live.is_file() and live.read_text(encoding="utf-8") != text:
            res.add("CS-005", BLOCK, "live .github/workflows/crewline.yml drifted from ci/crewline.yml")
    res.add("CS-004", INFO,
            "framework kit only. Human P5 (one feature, new machine, every --apply, no Crewline author): "
            + PLAYBOOK.as_posix())
    return res.finish()
