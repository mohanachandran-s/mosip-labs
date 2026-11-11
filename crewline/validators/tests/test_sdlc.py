"""Acceptance tests for the full-SDLC controls (Blueprint v2.1 §8, §9, §11; evals EV-20..EV-36)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from crewline import branch_env, change_scope, coding_standards, defect_reports, diff_coverage, discover, evals_run, evidence, manifest, migration, regression, requirement_clarity, requirement_fit, robustness, test_integrity, traceability, usage

READY = [{"gate": g, "status": "PASS"} for g in ("plan_readiness", "repo_ready")]
HUMAN = {"approver": "mohan", "reason": "r", "scope": "s", "timestamp": "2026-10-28T00:00:00Z"}


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def commit_all(repo, msg="c"):
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=T", "-c", "user.email=t@example.org", "commit", "-q", "-s", "-m", msg)
    return git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture
def svc(tmp_path):
    """A tiny Java service repo with a unit test and MOSIP-style DB scripts."""
    r = tmp_path / "svc"
    (r / "src/main/java/x").mkdir(parents=True)
    (r / "src/test/java/x").mkdir(parents=True)
    (r / "db_upgrade_scripts/mosip_x/sql").mkdir(parents=True)
    (r / "src/main/java/x/Svc.java").write_text("class Svc { int f() { return 1; } }\n")
    (r / "src/test/java/x/SvcTest.java").write_text(
        "class SvcTest {\n  @Test\n  void returnsOne() { assertEquals(1, new Svc().f()); }\n"
        "  @Test\n  void notTwo() { assertNotEquals(2, new Svc().f()); }\n}\n")
    (r / "db_upgrade_scripts/mosip_x/sql/1.0_to_1.1_upgrade.sql").write_text("ALTER TABLE a ADD COLUMN b int;\n")
    (r / "db_upgrade_scripts/mosip_x/sql/1.0_to_1.1_rollback.sql").write_text("ALTER TABLE a DROP COLUMN b;\n")
    git(tmp_path, "init", "-q", str(r))
    commit_all(r, "base")
    return r


def doc(**over):
    d = {
        "task_id": "t2", "task_type": "feature", "risk_tier": "L2", "outcome": "IN_PROGRESS", "current_state": "IMPACT_ANALYSIS",
        "scope": {"module": "svc", "repositories": [], "approved_paths": ["src/main/java/x/**", "src/test/java/x/**"]},
        "requirements": {"source_refs": [], "acceptance_criteria": [], "open_ambiguities": 0},
        "testcases": {"canonical_format": "none"},
        "repair": {"attempt_count": 0, "dev_attempt_count": 0, "attempt_history": [], "classification": ""},
        "reviews": {"implementation_review": "PASS", "fresh_context_review": "PASS"},
        "tracker": {"system": "jira", "key": "MOSIP-1", "confirmed_by": "mohan", "closure": {"status": "declined", "reason": "r"},
                    "subtasks": [{"kind": k, "key": f"MOSIP-{i}", "closure": {"status": "declined", "reason": "r"}}
                                 for i, k in enumerate(("testcase", "automation", "implementation", "testing", "review", "pr"), 2)]},
        "gates": [], "approvals": [], "evidence_refs": [], "github": {"issue": None, "subtasks": [], "pull_request": None},
        "test_plan": {"environment": "local docker-compose", "environment_kind": "local-stack"},
    }
    d.update(over)
    return d


def write(tmp_path, d, name="task-manifest.json"):
    p = tmp_path / name
    p.write_text(json.dumps(d))
    return str(p)


def ids(res):
    return {f.rule_id for f in res.findings}


def specialist(tmp_path, state, agent="acceptance-reviewer"):
    """packets/<STATE>.md written by the specialist that did the state (PKT-004)."""
    (tmp_path / "packets").mkdir(exist_ok=True)
    (tmp_path / "packets" / f"{state}.md").write_text(f"agent: {agent}\ncontext_id: ctx-{state.lower()}\nverdict: PASS\n")


def regression_record(tmp_path, candidate="branch-report", status="PASS", name="full-regression.json", baseline="base-report", **inputs):
    """evidence/full-regression*.json as `crewline regression --out` writes it (REG-007)."""
    from crewline import systest
    if not (tmp_path / ".git").is_dir():                         # reports live in a checkout, as on a real branch
        git(tmp_path, "init", "-q")
        (tmp_path / ".git/info/exclude").write_text("*.json\nevidence/\npackets/\nevidence-old/\n")
        git(tmp_path, "-c", "user.name=T", "-c", "user.email=t@example.org", "commit", "-q", "--allow-empty", "-m", "base")
    (tmp_path / "evidence").mkdir(exist_ok=True)
    for side in (baseline, candidate):
        (tmp_path / side).parent.mkdir(parents=True, exist_ok=True)
        if not (tmp_path / side).exists():
            (tmp_path / side).write_text(json.dumps([{"name": side}]))
    fields = {"baseline": str(tmp_path / baseline), "candidate": str(tmp_path / candidate),
              "baseline_sha256": systest.report_sha256(tmp_path / baseline),
              "candidate_sha256": systest.report_sha256(tmp_path / candidate),
              "commit": systest.repo_head(tmp_path / candidate), "worktree_digest": systest.repo_digest(tmp_path / candidate)}
    fields.update(inputs)
    (tmp_path / "evidence" / name).write_text(json.dumps(
        {"validator": "full-regression", "status": status, "summary": "", "findings": [], "inputs": fields}))


# --- one orchestrator, templated paths ------------------------------------------------------------
@pytest.mark.parametrize("task_type,src,expected", [
    ("feature", "IMPACT_ANALYSIS", ["DESIGN"]),
    ("test-automation", "REQUIREMENT_ANALYSIS", ["ACCEPTANCE_REVIEW"]),        # unclear story cannot skip AC review
    ("bug-fix", "REQUIREMENT_ANALYSIS", ["ACCEPTANCE_REVIEW"]),
    ("test-automation", "IMPACT_ANALYSIS", ["TESTCASE_DESIGN"]),               # the SDET path skips DESIGN
    ("release", "IMPACT_ANALYSIS", ["RELEASE_READINESS"]),
    ("incident", "IMPACT_ANALYSIS", ["KNOWLEDGE_UPDATE"]),                     # read-only: no code states at all
    ("test-automation", "DETERMINISTIC_VALIDATION", ["IMPLEMENTATION", "IMPLEMENTATION_REVIEW"]),
    ("test-automation", "FRESH_CONTEXT_REVIEW", ["IMPLEMENTATION", "TESTCASE_DESIGN", "SYSTEM_TEST_EXECUTION"]),
])
def test_template_projection(task_type, src, expected):
    ctx = manifest.Ctx({"task_type": task_type})
    assert ctx.next_states(src) == expected


def test_incident_cannot_reach_implementation(tmp_path):
    res = manifest.transition(write(tmp_path, doc(task_type="incident", risk_tier="L0")), "DESIGN")
    assert "TRN-001" in ids(res)


def test_unknown_type_and_lowered_tier(tmp_path):
    assert "MAN-013" in ids(manifest.check(write(tmp_path, doc(task_type="yolo"))))
    assert "MAN-013" in ids(manifest.check(write(tmp_path, doc(task_type="database-migration", risk_tier="L2"))))


def test_state_off_the_template_path(tmp_path):
    assert "MAN-002" in ids(manifest.check(write(tmp_path, doc(task_type="incident", risk_tier="L0", current_state="IMPLEMENTATION"))))


# --- design approval, independent oracle, reproduction --------------------------------------------
def test_feature_tests_need_design_readiness(tmp_path):
    d = doc(current_state="DESIGN",
            gates=[{"gate": "requirement_clarity", "status": "PASS"},
                   {"gate": "acceptance_readiness", "status": "PASS"}])
    res = manifest.transition(write(tmp_path, d), "TESTCASE_DESIGN")
    assert res.status == "FAIL" and "design_readiness" in res.to_text()
    d["gates"].append({"gate": "design_readiness", "status": "PASS"})
    res = manifest.transition(write(tmp_path, d), "TESTCASE_DESIGN")
    assert res.status == "FAIL" and "PKT-001" in ids(res)
    (tmp_path / "packets").mkdir(exist_ok=True)
    (tmp_path / "packets/DESIGN.md").write_text("ACs: AC-1\noptions: A vs B\nrecommendation: A\n", encoding="utf-8")
    assert manifest.transition(write(tmp_path, d), "TESTCASE_DESIGN").status == "PASS"


def test_feature_plan_needs_design_approval_and_tests_first(tmp_path):
    d = doc(current_state="TESTCASE_REVIEW", gates=[{"gate": "design_readiness", "status": "PASS"}])
    specialist(tmp_path, "TESTCASE_REVIEW", "testcase-reviewer")
    res = manifest.transition(write(tmp_path, d), "IMPLEMENTATION_PLAN")
    msg = " ".join(f.message for f in res.findings)
    assert res.status == "FAIL" and "testcase_readiness" in msg and "'design'" in msg
    d["gates"].append({"gate": "testcase_readiness", "status": "PASS"})
    d["approvals"].append({"action": "design", **HUMAN})
    assert manifest.transition(write(tmp_path, d), "IMPLEMENTATION_PLAN").status == "PASS"


def test_bug_fix_needs_reproduction_first(tmp_path):
    d = doc(task_type="bug-fix", current_state="TESTCASE_REVIEW")
    specialist(tmp_path, "TESTCASE_REVIEW", "testcase-reviewer")
    assert "reproduction" in " ".join(f.message for f in manifest.transition(write(tmp_path, d), "IMPLEMENTATION_PLAN").findings)
    d["gates"].append({"gate": "reproduction", "status": "PASS"})
    res = manifest.transition(write(tmp_path, d), "IMPLEMENTATION_PLAN")
    assert res.status == "FAIL" and "REP-001" in ids(res)
    ev = tmp_path / "evidence"
    ev.mkdir()
    passing = {"command": "mvn -B test -Dtest=Repro", "exit_code": 0, "started_at": "2026-10-28T10:00:00Z",
               "commit": "abc", "worktree_digest": "0", "log": "x.log"}
    (ev / "reproduction.json").write_text(json.dumps(passing))
    assert "REP-002" in ids(manifest.transition(write(tmp_path, d), "IMPLEMENTATION_PLAN"))
    passing["exit_code"] = 1
    (ev / "reproduction.json").write_text(json.dumps(passing))
    assert manifest.transition(write(tmp_path, d), "IMPLEMENTATION_PLAN").status == "PASS"


def test_leaving_system_test_needs_run_record(tmp_path):
    d = doc(current_state="SYSTEM_TEST_EXECUTION",
            gates=[{"gate": "branch_environment", "status": "PASS"}])
    res = manifest.transition(write(tmp_path, d), "QUALITY_GATE")
    assert res.status == "FAIL" and "SYS-001" in ids(res)
    ev = tmp_path / "evidence"
    ev.mkdir()
    (ev / "system-tests.json").write_text(json.dumps(
        {"command": "mvn -B test", "exit_code": 0, "started_at": "2026-10-28T10:00:00Z",
         "commit": "abc", "worktree_digest": "0", "log": "x.log", "environment": "http://127.0.0.1:8080", "cwd": str(tmp_path)}))
    assert manifest.transition(write(tmp_path, d), "QUALITY_GATE").status == "PASS"


def ui_module(tmp_path):
    """A module repo whose .crewline/module.yaml declares api-test, ui-test and a UI source project."""
    repo = tmp_path / "mod"
    (repo / ".crewline").mkdir(parents=True)
    (repo / ".crewline/module.yaml").write_text(
        "module: m\nstack:\n  node_projects: [web-ui]\n  ui_test_projects: [ui-test]\n  api_test: true\n")
    return repo


def run_record(cwd, command, exit_code=0):
    return {"command": command, "cwd": str(cwd), "exit_code": exit_code, "started_at": "2026-11-02T10:00:00Z",
            "commit": "abc", "worktree_digest": "0", "log": "x.log"}


def test_system_tests_must_run_the_module_suites(tmp_path):
    repo = ui_module(tmp_path)
    d = doc(current_state="SYSTEM_TEST_EXECUTION", gates=[{"gate": "branch_environment", "status": "PASS"}],
            scope={"module": "m", "repositories": [{"name": "m", "path": str(repo)}], "approved_paths": ["web-ui/src/**"]})
    ev = tmp_path / "evidence"
    ev.mkdir()
    # a jest run of the UI source, or a hand-made check, is not a system test (the INJIVERIFY-2464 hole)
    (ev / "system-tests.json").write_text(json.dumps(run_record(repo / "web-ui", "npx jest")))
    res = manifest.transition(write(tmp_path, d), "QUALITY_GATE")
    assert res.status == "FAIL" and "SYS-002" in ids(res)
    # the API suite alone does not cover a UI change
    (ev / "system-tests.json").write_text(json.dumps(run_record(repo / "api-test", "java -jar target/apitest.jar")))
    res = manifest.transition(write(tmp_path, d), "QUALITY_GATE")
    assert res.status == "FAIL" and "SYS-003" in ids(res)
    # naming the UI rig in a command does not run it: jest with a ui-test pattern, or an echo from the repo root
    (ev / "system-tests-ui.json").write_text(json.dumps(run_record(repo / "web-ui", "npx jest --testPathPattern=ui-test")))
    assert "SYS-003" in ids(manifest.transition(write(tmp_path, d), "QUALITY_GATE"))
    (ev / "system-tests-ui.json").write_text(json.dumps(run_record(repo, "cmd /c echo ui-test")))
    assert "SYS-003" in ids(manifest.transition(write(tmp_path, d), "QUALITY_GATE"))
    # a hand-written record (no crewline run fields) is refused
    (ev / "system-tests-ui.json").write_text(json.dumps({"command": "mvn test", "cwd": str(repo / "ui-test"), "exit_code": 0}))
    assert "SYS-001" in ids(manifest.transition(write(tmp_path, d), "QUALITY_GATE"))
    # a crewline run inside the UI rig; with suffixed files only (no system-tests.json)
    (ev / "system-tests.json").rename(ev / "system-tests-api.json")
    (ev / "system-tests-ui.json").write_text(json.dumps(
        run_record(repo / "ui-test", "mvn -DskipTests=false -Dsurefire.suiteXmlFiles=testNgXmlFiles/TestNg.xml test", exit_code=1)))
    res = manifest.transition(write(tmp_path, d), "QUALITY_GATE")
    assert res.status == "PASS" and "SYS-005" in ids(res)                       # failures warn: triage them as defects
    # a UI project at the repo root (discover writes ".") touches every change
    (repo / ".crewline/module.yaml").write_text("module: m\nstack:\n  node_projects: [.]\n  ui_test_projects: [ui-test]\n  api_test: true\n")
    d["scope"]["approved_paths"] = ["src/components/Upload.tsx"]
    (ev / "system-tests-ui.json").unlink()
    assert "SYS-003" in ids(manifest.transition(write(tmp_path, d), "QUALITY_GATE"))
    # a backend-only change needs no UI run
    (repo / ".crewline/module.yaml").write_text("module: m\nstack:\n  node_projects: [web-ui]\n  ui_test_projects: [ui-test]\n  api_test: true\n")
    d["scope"]["approved_paths"] = ["svc/src/**"]
    assert manifest.transition(write(tmp_path, d), "QUALITY_GATE").status == "PASS"


def test_system_tests_unchecked_without_declared_suites(tmp_path):
    d = doc(current_state="SYSTEM_TEST_EXECUTION", gates=[{"gate": "branch_environment", "status": "PASS"}])
    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence/system-tests.json").write_text(json.dumps(run_record(tmp_path, "pytest -q")))
    res = manifest.transition(write(tmp_path, d), "QUALITY_GATE")
    assert res.status == "PASS" and "SYS-004" in ids(res)


def test_commit_needs_a_system_suite_regression_record(tmp_path):
    repo = ui_module(tmp_path)
    gates = [{"gate": g, "status": "PASS"} for g in ["quality_gate", "full_regression", "secrets"] + manifest.load_template("bug-fix")["required_gates"]]
    d = doc(task_type="bug-fix", current_state="QUALITY_GATE", gates=gates, approvals=[{"action": "commit_pr", **HUMAN}],
            scope={"module": "m", "repositories": [{"name": "m", "path": str(repo)}], "approved_paths": ["web-ui/src/**"]})
    res = manifest.transition(write(tmp_path, d), "COMMIT_PR")
    assert res.status == "FAIL" and "REG-007" in ids(res)                       # the stamp alone
    regression_record(tmp_path, candidate="web-ui/coverage")                     # unit-suite regression only
    res = manifest.transition(write(tmp_path, d), "COMMIT_PR")
    assert res.status == "FAIL" and "REG-008" in ids(res)
    # only the candidate (branch) report counts: a system-suite baseline with a unit-suite candidate is not one
    regression_record(tmp_path, candidate="web-ui/coverage", baseline="mod/ui-test/target/cucumber.json")
    assert "REG-008" in ids(manifest.transition(write(tmp_path, d), "COMMIT_PR"))
    ui = "mod/ui-test/target/cucumber.json"
    regression_record(tmp_path, candidate=ui, status="FAIL", name="full-regression-ui.json")
    res = manifest.transition(write(tmp_path, d), "COMMIT_PR")
    assert res.status == "FAIL" and "REG-007" in ids(res)                       # a break blocks
    # a hand-written record (no hashes / commit), or one comparing a report with itself, is refused
    (tmp_path / "evidence/full-regression-ui.json").write_text(json.dumps(
        {"validator": "full-regression", "status": "PASS", "inputs": {"candidate": str(repo / "ui-test/target/cucumber.json")}}))
    res = manifest.transition(write(tmp_path, d), "COMMIT_PR")
    assert "REG-007" in ids(res) and "REG-008" in ids(res)
    regression_record(tmp_path, candidate=ui, name="full-regression-ui.json", candidate_sha256="b" * 64)
    assert "REG-007" in ids(manifest.transition(write(tmp_path, d), "COMMIT_PR"))
    regression_record(tmp_path, candidate=ui, baseline=ui, name="full-regression-ui.json")          # report vs itself
    assert "REG-007" in ids(manifest.transition(write(tmp_path, d), "COMMIT_PR"))
    regression_record(tmp_path, candidate=ui, name="full-regression-ui.json", commit="")
    assert "REG-007" in ids(manifest.transition(write(tmp_path, d), "COMMIT_PR"))
    regression_record(tmp_path, candidate=ui, name="full-regression-ui.json")
    assert manifest.transition(write(tmp_path, d), "COMMIT_PR").status == "PASS"
    # stale: the code changed after the run (uncommitted), or the report changed
    (repo / "web-ui").mkdir(exist_ok=True)
    (repo / "web-ui/App.tsx").write_text("export const x = 2;\n")
    res = manifest.transition(write(tmp_path, d), "COMMIT_PR")
    assert "REG-007" in ids(res) and "uncommitted" in res.to_text()
    regression_record(tmp_path, candidate=ui, name="full-regression-ui.json")
    (tmp_path / ui).write_text("[]")
    res = manifest.transition(write(tmp_path, d), "COMMIT_PR")
    assert "REG-007" in ids(res) and "report changed" in res.to_text()
    regression_record(tmp_path, candidate=ui, name="full-regression-ui.json")
    assert "REG-007" in ids(manifest.transition(write(tmp_path, d), "COMMIT_PR"))   # every record must be fresh
    regression_record(tmp_path, candidate="web-ui/coverage", baseline="mod/ui-test/target/cucumber.json")
    assert manifest.transition(write(tmp_path, d), "COMMIT_PR").status == "PASS"
    # documentation tasks never need one
    d2 = doc(task_type="documentation", current_state="QUALITY_GATE", approvals=[{"action": "commit_pr", **HUMAN}],
             gates=[{"gate": g, "status": "PASS"} for g in ["quality_gate"] + manifest.load_template("documentation")["required_gates"]])
    (tmp_path / "evidence").rename(tmp_path / "evidence-old")
    assert "REG-007" not in ids(manifest.transition(write(tmp_path, d2), "COMMIT_PR"))


def test_specialist_states_need_the_specialists_packet(tmp_path):
    d = doc(current_state="ACCEPTANCE_REVIEW", gates=[{"gate": "input_readiness", "status": "PASS"},
                                                      {"gate": "requirement_clarity", "status": "PASS"},
                                                      {"gate": "acceptance_readiness", "status": "PASS"}])
    res = manifest.transition(write(tmp_path, d), "IMPACT_ANALYSIS")
    assert res.status == "FAIL" and "PKT-001" in ids(res)                       # passed in seconds, no work on disk
    specialist(tmp_path, "ACCEPTANCE_REVIEW", agent="crewline-orchestrator")
    res = manifest.transition(write(tmp_path, d), "IMPACT_ANALYSIS")
    assert res.status == "FAIL" and "PKT-004" in ids(res)                       # the orchestrator reviewing itself
    for made_up in ("me", "claude", "crewline:orchestrator"):
        specialist(tmp_path, "ACCEPTANCE_REVIEW", agent=made_up)
        assert "PKT-004" in ids(manifest.transition(write(tmp_path, d), "IMPACT_ANALYSIS")), made_up
    for real in ("acceptance-reviewer", "crewline-acceptance-reviewer", "crewline:product:acceptance-reviewer"):
        specialist(tmp_path, "ACCEPTANCE_REVIEW", agent=real)
        assert manifest.transition(write(tmp_path, d), "IMPACT_ANALYSIS").status == "PASS", real
    # non-code task types keep 0.6.0 behaviour
    d = doc(task_type="documentation", current_state="IMPACT_ANALYSIS")
    assert "PKT-001" not in ids(manifest.transition(write(tmp_path / "packets", d), "IMPLEMENTATION_PLAN"))


def test_regression_reads_cucumber_json(tmp_path):
    def feature(results):
        steps = [{"name": f"s{i}", "result": {"status": s}} for i, s in enumerate(results)]
        return {"name": "Upload", "elements": [
            {"type": "background", "steps": [{"name": "open", "result": {"status": "passed"}}]},
            {"type": "scenario", "id": "upload;svg-template-error", "name": "SVG template error", "steps": steps},
            {"type": "scenario", "id": "upload;png", "name": "png", "steps": [{"name": "s", "result": {"status": "passed"}}]}]}
    base, cand = tmp_path / "base", tmp_path / "cand"
    base.mkdir(); cand.mkdir()
    (base / "cucumber.json").write_text(json.dumps([feature(["passed", "passed"])]))
    (cand / "cucumber.json").write_text(json.dumps([feature(["passed", "failed"])]))
    res = regression.run(str(base), str(cand))
    assert res.status == "FAIL" and "REG-001" in ids(res)
    assert any("Upload#upload;svg-template-error" in f.message for f in res.findings)
    (cand / "cucumber.json").write_text(json.dumps([feature(["passed", "undefined"])]))
    assert "REG-003" in ids(regression.run(str(base), str(cand / "cucumber.json")))
    (cand / "cucumber.json").write_text(json.dumps([feature(["passed", "passed"])]))
    res = regression.run(str(base), str(cand))
    assert res.status == "PASS" and res.inputs["candidate_counts"]["PASS"] == 2


def test_regression_out_writes_the_record(tmp_path):
    for side in ("b", "c"):
        (tmp_path / side).mkdir()
        (tmp_path / side / "cucumber.json").write_text(json.dumps(
            [{"name": "F", "elements": [{"id": "f;a", "steps": [{"result": {"status": "passed"}}]}]}]))
    out = tmp_path / "task/evidence/full-regression-ui.json"
    r = subprocess.run([sys.executable, "-m", "crewline", "regression", "--baseline", str(tmp_path / "b"),
                        "--candidate", str(tmp_path / "c"), "--out", str(out)], capture_output=True, text=True,
                       cwd=Path(__file__).resolve().parents[1])
    assert r.returncode == 0, r.stdout + r.stderr
    rec = json.loads(out.read_text())
    assert rec["validator"] == "full-regression" and rec["status"] == "PASS"
    assert rec["inputs"]["candidate"] == str((tmp_path / "c").resolve())
    assert len(rec["inputs"]["candidate_sha256"]) == 64 and "commit" in rec["inputs"]


def test_implementation_needs_approved_paths(tmp_path):
    d = doc(current_state="REPO_READY", gates=READY, scope={"module": "svc", "repositories": [], "approved_paths": []})
    res = manifest.transition(write(tmp_path, d), "IMPLEMENTATION")
    assert res.status == "FAIL" and "approved_paths" in res.to_text()
    d["scope"]["approved_paths"] = ["src/main/java/x/**"]
    assert manifest.transition(write(tmp_path, d), "IMPLEMENTATION").status == "PASS"


def test_sdet_path_does_not_need_design_gate(tmp_path):
    d = doc(task_type="test-automation", current_state="TESTCASE_REVIEW", gates=[{"gate": "testcase_readiness", "status": "PASS"}])
    specialist(tmp_path, "TESTCASE_REVIEW", "testcase-reviewer")
    assert manifest.transition(write(tmp_path, d), "IMPLEMENTATION_PLAN").status == "PASS"


# --- two repair loops, failure routing ------------------------------------------------------------
def attempts(n, track):
    return [{"attempt": i, "track": track, "hypothesis": "h", "changed_files": ["A.java"], "outcome": "STILL_FAILING"}
            for i in range(1, n + 1)]


def test_code_under_change_goes_back_to_the_developer(tmp_path):
    d = doc(current_state="FAILURE_TRIAGE", repair={"attempt_count": 3, "dev_attempt_count": 1,
            "attempt_history": attempts(3, "sdet") + attempts(1, "dev"), "classification": "CODE_UNDER_CHANGE"}, gates=READY)
    assert manifest.transition(write(tmp_path, d), "IMPLEMENTATION").status == "PASS"   # SDET cap does not apply


def test_dev_cap_forces_escalation(tmp_path):
    d = doc(current_state="FAILURE_TRIAGE", repair={"attempt_count": 0, "dev_attempt_count": 3,
            "attempt_history": attempts(3, "dev"), "classification": "CODE_UNDER_CHANGE"})
    path = write(tmp_path, d)
    assert {"TRN-002", "TRN-003"} <= ids(manifest.transition(path, "IMPLEMENTATION"))
    assert manifest.transition(path, "S_ESCALATE").status == "PASS"


@pytest.mark.parametrize("cls", ["APPLICATION", "SECURITY", "SPECIFICATION"])
def test_out_of_scope_failures_escalate(tmp_path, cls):
    d = doc(current_state="FAILURE_TRIAGE", repair={"attempt_count": 0, "dev_attempt_count": 0, "attempt_history": [], "classification": cls})
    assert "TRN-003" in ids(manifest.transition(write(tmp_path, d), "IMPLEMENTATION"))


def test_environment_retry_is_not_a_repair(tmp_path):
    d = doc(current_state="FAILURE_TRIAGE", repair={"attempt_count": 0, "dev_attempt_count": 0, "attempt_history": [], "classification": "ENVIRONMENT"},
            gates=[{"gate": "branch_environment", "status": "PASS"}])
    path = write(tmp_path, d)
    assert manifest.transition(path, "SYSTEM_TEST_EXECUTION").status == "PASS"
    assert manifest.transition(path, "IMPLEMENTATION").status == "FAIL"


def test_dev_counter_must_match_history(tmp_path):
    d = doc(repair={"attempt_count": 0, "dev_attempt_count": 0, "attempt_history": attempts(1, "dev")})
    assert "MAN-003" in ids(manifest.check(write(tmp_path, d)))


# --- no self-approval, no self-review --------------------------------------------------------------
def test_agent_cannot_approve(tmp_path):
    d = doc(approvals=[{"action": "commit_pr", **HUMAN, "approver": "software-developer"},
                       {"action": "design", **HUMAN, "approver_type": "agent"}])
    assert [f.rule_id for f in manifest.check(write(tmp_path, d)).findings].count("MAN-010") == 2


def test_author_cannot_review_and_reviews_need_two_contexts(tmp_path):
    d = doc(implementation={"authors": ["dev#ctx1"]},
            reviews={"implementation_review": "PASS", "fresh_context_review": "PASS",
                     "reviewers": {"implementation_review": "dev#ctx1", "fresh_context_review": "dev#ctx1"}})
    assert [f.rule_id for f in manifest.check(write(tmp_path, d)).findings].count("MAN-011") == 3


# --- required gates, PR comments, release -----------------------------------------------------------
def test_commit_needs_every_template_gate(tmp_path):
    base = ["quality_gate", "full_regression", "secrets"]
    d = doc(current_state="QUALITY_GATE", gates=[{"gate": g, "status": "PASS"} for g in base],
            approvals=[{"action": "commit_pr", **HUMAN}])
    regression_record(tmp_path)
    res = manifest.transition(write(tmp_path, d), "COMMIT_PR")
    missing = {f.message.split()[2] for f in res.findings if f.rule_id == "MAN-012"}
    assert {"design_readiness", "unit_tests", "test_integrity", "traceability", "change_scope"} <= missing
    d["gates"] = [{"gate": g, "status": "PASS"} for g in base + manifest.load_template("feature")["required_gates"]]
    for g in d["gates"]:
        if g["gate"] == "change_scope":
            g["status"] = "FAIL"
            break
    d["exceptions"] = [{"action": "gate_exception", "gate": "change_scope", **HUMAN}]
    assert manifest.transition(write(tmp_path, d), "COMMIT_PR").status == "PASS"   # ... with an attributed exception
    d["gates"][base.index("secrets")]["status"] = "FAIL"
    d["exceptions"].append({"action": "gate_exception", "gate": "secrets", **HUMAN})
    assert manifest.transition(write(tmp_path, d), "COMMIT_PR").status == "FAIL"   # secrets is never waivable


def test_documentation_path_has_no_regression_or_second_review(tmp_path):
    ctx = manifest.Ctx({"task_type": "documentation"})
    assert ctx.next_states("IMPACT_ANALYSIS") == ["IMPLEMENTATION_PLAN"]          # no DESIGN / TESTCASE_* states
    assert ctx.next_states("IMPLEMENTATION_REVIEW") == ["IMPLEMENTATION", "QUALITY_GATE"]
    d = doc(task_type="documentation", current_state="IMPLEMENTATION_REVIEW",
            reviews={"implementation_review": "PASS", "fresh_context_review": "NOT_STARTED"},
            gates=[{"gate": "deterministic_validation", "status": "PASS"}])
    assert manifest.transition(write(tmp_path, d), "QUALITY_GATE").status == "PASS"  # FRESH_CONTEXT_REVIEW not on path
    d["reviews"]["implementation_review"] = "NOT_STARTED"
    assert manifest.transition(write(tmp_path, d), "QUALITY_GATE").status == "FAIL"  # review 1 is still required
    d.update(current_state="QUALITY_GATE", approvals=[{"action": "commit_pr", **HUMAN}],
             gates=[{"gate": g, "status": "PASS"} for g in ("quality_gate", "deterministic_validation", "change_scope")])
    res = manifest.transition(write(tmp_path, d), "COMMIT_PR")
    assert "full_regression" not in " ".join(f.message for f in res.findings)    # not_applicable_gates, with rationale
    assert "secrets" in " ".join(f.message for f in res.findings)                # never waivable, never skipped
    d["gates"].append({"gate": "secrets", "status": "PASS"})
    d["gates"].append({"gate": "token_budget", "status": "PASS"})
    assert manifest.transition(write(tmp_path, d), "COMMIT_PR").status == "PASS"
    d["task_type"] = "feature"                                                   # other types still need regression
    assert "full_regression" in " ".join(f.message for f in manifest.transition(write(tmp_path, d), "COMMIT_PR").findings)


def test_pr_closeout_loops_on_valid_comments(tmp_path):
    d = doc(current_state="PR_CLOSEOUT", pr={"valid_comments_open": 1, "unresolved_threads": 0, "ci_status": "PASS", "monitoring": "app", "title": "MOSIP-1: x"}, gates=READY + [{"gate": "release_readiness", "status": "PASS"}])
    path = write(tmp_path, d)
    assert manifest.transition(path, "RELEASE_READINESS").status == "FAIL"
    assert manifest.transition(path, "IMPLEMENTATION").status == "PASS"
    d["pr"]["valid_comments_open"] = 0
    assert manifest.transition(write(tmp_path, d), "RELEASE_READINESS").status == "PASS"


def test_production_deploy_is_refused(tmp_path):
    d = doc(task_type="release", risk_tier="L3", current_state="RELEASE_READINESS",
            gates=[{"gate": "release_readiness", "status": "PASS"}], approvals=[{"action": "deploy", **HUMAN}],
            release={"environment": "prod", "environment_class": "production", "rollback_plan_ref": "r.md"})
    assert manifest.transition(write(tmp_path, d), "AUTHORIZED_DEPLOYMENT").status == "FAIL"
    d["release"].update(environment="released", environment_class="non-production")
    assert manifest.transition(write(tmp_path, d), "AUTHORIZED_DEPLOYMENT").status == "PASS"
    d["release"]["rollback_plan_ref"] = ""
    assert manifest.transition(write(tmp_path, d), "AUTHORIZED_DEPLOYMENT").status == "FAIL"


# --- change scope -----------------------------------------------------------------------------------
def test_change_scope(svc, tmp_path):
    (svc / "src/main/java/x/Svc.java").write_text("class Svc { int f() { return 1; } int g() { return 2; } }\n")
    d = doc()
    m = write(tmp_path, d)
    assert change_scope.run(str(svc), "HEAD", m).status == "PASS"
    (svc / ".github/workflows").mkdir(parents=True)
    (svc / ".github/workflows/ci.yml").write_text("on: push\n")
    (svc / "README.md").write_text("x\n")
    res = change_scope.run(str(svc), "HEAD", m)
    assert res.status == "FAIL" and {"SCP-001", "SCP-002"} <= ids(res)
    d["approvals"] = [{"action": "scope_change", **HUMAN, "scope": ".github/workflows/ci.yml README.md"}]
    assert change_scope.run(str(svc), "HEAD", write(tmp_path, d)).status == "PASS"
    d["scope"]["approved_paths"] = []
    assert change_scope.run(str(svc), "HEAD", write(tmp_path, d)).status == "NOT_RUN"


# --- unit-test integrity ------------------------------------------------------------------------------
@pytest.mark.parametrize("new_body,rule", [
    ("class SvcTest {\n  @Test\n  void returnsOne() { assertEquals(1, new Svc().f()); }\n}\n", "TIN-001"),
    ("class SvcTest {\n  @Disabled\n  @Test\n  void returnsOne() { assertEquals(1, new Svc().f()); }\n"
     "  @Test\n  void notTwo() { assertNotEquals(2, new Svc().f()); }\n}\n", "TIN-002"),
    ("class SvcTest {\n  @Test\n  void returnsOne() { new Svc().f(); }\n"
     "  @Test\n  void notTwo() { assertNotEquals(2, new Svc().f()); }\n}\n", "TIN-003"),
])
def test_unit_test_weakening_blocks(svc, new_body, rule):
    (svc / "src/test/java/x/SvcTest.java").write_text(new_body)
    res = test_integrity.run(str(svc), "HEAD")
    assert res.status == "FAIL" and rule in ids(res)


def test_unit_test_deletion_blocks_and_approval_covers(svc, tmp_path):
    (svc / "src/test/java/x/SvcTest.java").unlink()
    assert "TIN-004" in ids(test_integrity.run(str(svc), "HEAD"))
    appr = tmp_path / "approvals.yaml"
    appr.write_text("approvals:\n  - rule_id: TIN-004\n    target: src/test/java/x/SvcTest.java\n"
                    "    approver: mohan\n    reason: MOSIP-1 feature removed\n    timestamp: t\n")
    assert test_integrity.run(str(svc), "HEAD", str(appr)).status == "PASS"


def test_adding_tests_passes_and_strict_blocks_edits(svc):
    p = svc / "src/test/java/x/SvcTest.java"
    p.write_text(p.read_text().replace("}\n}\n", "}\n  @Test\n  void three() { assertTrue(true); }\n}\n"))
    assert test_integrity.run(str(svc), "HEAD").status == "PASS"
    assert "TIN-005" in ids(test_integrity.run(str(svc), "HEAD", strict=True))   # refactor: no test edits at all


# --- migration pairing -----------------------------------------------------------------------------------
def test_migration_pairing(svc):
    sql = svc / "db_upgrade_scripts/mosip_x/sql"
    (sql / "1.1_to_1.2_upgrade.sql").write_text("ALTER TABLE a ADD COLUMN c int;\n")
    res = migration.run(str(svc), "HEAD")
    assert res.status == "FAIL" and "MIG-001" in ids(res)
    (sql / "1.1_to_1.2_rollback.sql").write_text("ALTER TABLE a DROP COLUMN c;\n")
    assert migration.run(str(svc), "HEAD").status == "PASS"
    (sql / "1.0_to_1.1_upgrade.sql").write_text("ALTER TABLE a ADD COLUMN b bigint;\n")    # editing a released script
    assert "MIG-002" in ids(migration.run(str(svc), "HEAD"))


def test_destructive_forward_script_warns(svc):
    sql = svc / "db_upgrade_scripts/mosip_x/sql"
    (sql / "1.1_to_1.2_upgrade.sql").write_text("DROP TABLE old_audit;\n")
    (sql / "1.1_to_1.2_revoke.sql").write_text("CREATE TABLE old_audit(id int);\n")
    res = migration.run(str(svc), "HEAD")
    assert res.status == "PASS" and "MIG-004" in ids(res)


# --- traceability ------------------------------------------------------------------------------------------
def test_traceability(tmp_path):
    acs = [{"id": "AC-1", "text": "t", "code_refs": ["src/main/java/x/Svc.java"], "test_ids": ["x.SvcTest#returnsOne"]},
           {"id": "AC-2", "text": "t", "test_ids": ["TC_x_A_01"]},
           {"id": "AC-3", "text": "t", "code_refs": ["pom.xml"]}]
    d = doc(requirements={"source_refs": [], "acceptance_criteria": acs, "open_ambiguities": 0},
            test_results={"x.SvcTest#returnsOne": "PASS"})
    res = traceability.run(write(tmp_path, d))
    found = [(f.rule_id, f.evidence or f.path) for f in res.findings]
    assert ("TRC-002", "AC-2") in found and ("TRC-003", "TC_x_A_01") in found
    assert ("TRC-001", "AC-3") in found and ("TRC-005", "pom.xml") in found
    assert "TRC-006" in ids(traceability.run(write(tmp_path, doc())))


# --- evidence the model cannot fabricate --------------------------------------------------------------------
def test_run_captures_and_evidence_goes_stale(svc, tmp_path):
    out = tmp_path / "ev/unit.json"
    res = evidence.capture(str(svc), str(out), [sys.executable, "-c", "print('ok')"])
    assert res.status == "PASS" and evidence.run(str(out), str(svc)).status == "PASS"
    (svc / "src/main/java/x/Svc.java").write_text("class Svc { int f() { return 2; } }\n")
    assert "EVD-002" in ids(evidence.run(str(out), str(svc)))                  # code changed after the run
    bad = evidence.capture(str(svc), str(tmp_path / "ev/fail.json"), [sys.executable, "-c", "raise SystemExit(3)"])
    assert "EVD-003" in ids(bad)
    rec = json.loads(out.read_text()); rec.pop("worktree_digest"); out.write_text(json.dumps(rec))
    assert "EVD-001" in ids(evidence.run(str(out), str(svc)))
    assert "CLIP-002" in ids(res) and "do not Read it" in next(f.message for f in res.findings if f.rule_id == "CLIP-002")


def test_clip_keeps_failures_and_caps(tmp_path):
    from crewline import clip
    log = tmp_path / "mvn.log"
    noise = "\n".join(f"Downloading foo-{i}.jar" for i in range(200))
    log.write_text(noise + "\n[ERROR] compilation failure\nTests run: 3, Failures: 1\nBUILD FAILURE\n", encoding="utf-8")
    res = clip.run(str(log), 800)
    assert res.status == "PASS" and "CLIP-002" in ids(res)
    msg = next(f.message for f in res.findings if f.rule_id == "CLIP-002")
    assert "BUILD FAILURE" in msg and "Tests run:" in msg and "[ERROR]" in msg
    assert len(msg) <= 800
    assert clip.run(str(tmp_path / "missing.log")).status == "NOT_RUN"
    cmd = clip.run_command([sys.executable, "-c", "print('ok')"])
    assert cmd.status == "PASS" and "CLIP-002" in ids(cmd)


# --- JUnit regression -----------------------------------------------------------------------------------------
def _surefire(path, cases):
    path.mkdir(parents=True, exist_ok=True)
    body = "".join(f'<testcase classname="x.SvcTest" name="{n}">' + ("<failure/>" if s == "FAIL" else "") + "</testcase>"
                   for n, s in cases.items())
    (path / "TEST-x.SvcTest.xml").write_text(f'<testsuite name="x.SvcTest">{body}</testsuite>')
    return str(path)


def test_junit_regression(tmp_path):
    base = _surefire(tmp_path / "b/target/surefire-reports", {"a": "PASS", "b": "PASS", "c": "FAIL"})
    cand = _surefire(tmp_path / "c/target/surefire-reports", {"a": "PASS", "b": "FAIL", "c": "PASS", "d": "PASS"})
    res = regression.run(str(tmp_path / "b"), str(tmp_path / "c"))
    assert res.status == "FAIL" and "REG-001" in ids(res) and "REG-006" in ids(res)   # b broke, c fixed
    assert regression.run(str(tmp_path / "b"), str(tmp_path / "c"), ["x.SvcTest#b"]).status == "PASS"


# --- token usage per stage and step -------------------------------------------------------------------------------
def test_usage_by_stage_and_step(tmp_path):
    def rec(ts, mid, i, cw, cr, o):
        return json.dumps({"timestamp": ts, "message": {"id": mid, "model": "m", "usage": {
            "input_tokens": i, "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr, "output_tokens": o}}})
    tr = tmp_path / "s.jsonl"
    tr.write_text("\n".join([
        rec("2026-10-28T09:00:00Z", "a", 1, 1, 1, 1),            # before the task: ignored
        rec("2026-10-28T10:01:00Z", "b", 2, 10, 100, 5),         # INTAKE, step 1
        rec("2026-10-28T10:01:00Z", "b", 2, 10, 100, 5),         # duplicate streaming record: counted once
        rec("2026-10-28T10:06:00Z", "c", 1, 20, 200, 7),         # REQUIREMENT_ANALYSIS, step 2
        "not json"]))
    sub = tmp_path / "s" / "subagents"; sub.mkdir(parents=True)
    (sub / "agent-1.jsonl").write_text(rec("2026-10-28T10:07:00Z", "d", 0, 5, 50, 3))
    d = doc(started_at="2026-10-28T10:00:00Z", initial_state="INTAKE",
            state_history=[{"from": "INTAKE", "to": "REQUIREMENT_ANALYSIS", "at": "2026-10-28T10:05:00Z"}],
            usage_marks=[{"label": "read story", "at": "2026-10-28T10:00:30Z"}, {"label": "write ACs", "at": "2026-10-28T10:05:30Z"}])
    m = write(tmp_path, d)
    res = usage.report(m, [str(tr)])
    data = json.loads((tmp_path / "usage.json").read_text())
    assert res.status == "PASS" and data["overall"]["calls"] == 3
    assert data["by_stage"]["INTAKE"]["fresh"] == 17 and data["by_stage"]["REQUIREMENT_ANALYSIS"]["calls"] == 2
    assert data["by_step"]["write ACs"]["total"] == 228 + 58 and "| read story |" in (tmp_path / "usage.md").read_text()
    assert usage.mark(m, "next step").status == "PASS"
    assert json.loads(Path(m).read_text())["usage_marks"][-1]["label"] == "next step"
    assert "USE-002" in ids(usage.report(write(tmp_path, doc(), "m2.json"), [str(tr)]))


def test_ci_spec_has_rails_jobs():
    spec = Path(__file__).resolve().parents[2] / "ci" / "crewline.yml"
    text = spec.read_text(encoding="utf-8")
    for needle in ("python -m pytest", "evals --pack frozen", "adapter", "cold-start"):
        assert needle in text, needle
    live = spec.parents[2] / ".github" / "workflows" / "crewline.yml"
    if live.is_file():
        assert live.read_text(encoding="utf-8") == text


def test_usage_requires_mark_per_transition(tmp_path):
    d = doc(started_at="2026-10-28T10:00:00Z",
            state_history=[{"from": "INTAKE", "to": "INPUT_VALIDATION", "at": "2026-10-28T10:01:00Z"}])
    assert "USE-009" in ids(usage.report(write(tmp_path, d), []))


def test_usage_budget_blocks_when_over_cap(tmp_path):
    rec = json.dumps({"timestamp": "2026-10-28T10:01:00Z", "message": {"id": "b", "model": "m", "usage": {
        "input_tokens": 100, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 9000, "output_tokens": 50}}})
    tr = tmp_path / "s.jsonl"
    tr.write_text(rec, encoding="utf-8")
    d = doc(started_at="2026-10-28T10:00:00Z", initial_state="IMPLEMENTATION_REVIEW",
            usage={"budget": {"per_task": 100, "per_state": 50}})
    found = ids(usage.report(write(tmp_path, d), [str(tr)]))
    assert {"USE-003", "USE-004"} <= found


def _usage_rec(ts, mid, cr, o=10):
    return json.dumps({"timestamp": ts, "message": {"id": mid, "model": "m", "usage": {
        "input_tokens": 1, "cache_creation_input_tokens": 0, "cache_read_input_tokens": cr, "output_tokens": o}}})


def test_usage_discovers_claude_transcripts(tmp_path, monkeypatch):
    clone = tmp_path / "repo" / "keymanager"
    task = clone / ".crewline-work" / "tasks" / "T-1"
    task.mkdir(parents=True)
    m = write(task, doc(started_at="2026-10-28T10:00:00Z"), "task-manifest.json")
    cfg = tmp_path / "cfg"
    real, base = usage._slug, usage._slug(clone.resolve())
    assert real(Path("D:/Mohan/Api_Automation/keymanager")) in ("D--Mohan-Api-Automation-keymanager",
                                                                "-D-Mohan-Api-Automation-keymanager")
    monkeypatch.setattr(usage, "_slug", lambda p: real(p).replace(base, "K"))   # Windows MAX_PATH in tmp
    slug = usage._slug(clone.resolve())
    main = cfg / "projects" / slug / "sess.jsonl"
    main.parent.mkdir(parents=True)
    main.write_text(_usage_rec("2026-10-28T10:01:00Z", "a", 100), encoding="utf-8")
    wt = cfg / "projects" / usage._slug(clone.resolve() / ".crewline-work" / "worktrees" / "T-1") / "s2.jsonl"
    wt.parent.mkdir(parents=True)
    wt.write_text(_usage_rec("2026-10-28T10:02:00Z", "b", 100), encoding="utf-8")
    other = cfg / "projects" / (slug + "-other") / "s3.jsonl"           # a different repo with a longer name
    other.parent.mkdir(parents=True)
    other.write_text(_usage_rec("2026-10-28T10:03:00Z", "c", 100), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    res = usage.report(m, [])
    assert res.inputs["overall"]["calls"] == 2 and res.inputs["discovered"]


def test_usage_blind_meter_blocks(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "none"))
    monkeypatch.setenv("CLAUDECODE", "1")
    m = write(tmp_path, doc(started_at="2026-10-28T10:00:00Z"))
    assert "USE-010" in ids(usage.report(m, []))
    tr = tmp_path / "old.jsonl"
    tr.write_text(_usage_rec("2026-10-27T10:00:00Z", "a", 1), encoding="utf-8")   # before the task
    res = usage.report(m, [str(tr)])
    assert "USE-001" in ids(res) and res.status != "PASS"
    monkeypatch.delenv("CLAUDECODE")
    assert "USE-006" in ids(usage.report(m, []))


def test_usage_per_subagent_and_context_caps(tmp_path):
    tr = tmp_path / "s.jsonl"
    tr.write_text(_usage_rec("2026-10-28T10:01:00Z", "a", 500), encoding="utf-8")
    sub = tmp_path / "s" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-x.jsonl").write_text("\n".join(_usage_rec(f"2026-10-28T10:0{i}:00Z", f"x{i}", 900) for i in range(2, 5)),
                                       encoding="utf-8")
    d = doc(started_at="2026-10-28T10:00:00Z",
            usage={"budget": {"per_subagent": 1000, "context_warn": 800}})
    m = write(tmp_path, d)
    res = usage.report(m, [str(tr)])
    msgs = [f.message for f in res.findings if f.rule_id in ("USE-011", "USE-012")]
    assert any("agent-x" in s for s in msgs) and not any(s.startswith("main") for s in msgs)
    data = json.loads((tmp_path / "usage.json").read_text())
    assert data["by_agent"]["agent-x"]["peak_context"] == 901 and "By agent" in (tmp_path / "usage.md").read_text()


def test_usage_packet_dump_blocks(tmp_path):
    d = doc(started_at="2026-10-28T10:00:00Z")
    m = write(tmp_path, d)
    pkt = tmp_path / "packets"
    pkt.mkdir()
    (pkt / "IMPLEMENTATION.md").write_text("Carried over from the SDET Architect\n" + ("x" * 500), encoding="utf-8")
    assert "USE-008" in ids(usage.report(m, []))


def test_pr_closeout_needs_monitoring_and_green_ci(tmp_path):
    d = doc(current_state="PR_CLOSEOUT", gates=READY + [{"gate": "release_readiness", "status": "PASS"}],
            pr={"valid_comments_open": 0, "unresolved_threads": 1, "ci_status": "PENDING", "monitoring": "none", "title": "MOSIP-1: x"})
    msg = " ".join(f.message for f in manifest.transition(write(tmp_path, d), "RELEASE_READINESS").findings)
    assert "pr.ci_status" in msg and "pr.monitoring" in msg and "pr.unresolved_threads" in msg
    d["pr"].update(ci_status="FAIL", monitoring="app")
    path = write(tmp_path, d)
    assert manifest.transition(path, "RELEASE_READINESS").status == "FAIL"
    assert manifest.transition(path, "FAILURE_TRIAGE").status == "PASS"         # red CI is triaged
    d.update(current_state="FAILURE_TRIAGE", repair={"attempt_count": 0, "dev_attempt_count": 0, "attempt_history": [],
             "classification": "ENVIRONMENT"})
    assert manifest.transition(write(tmp_path, d), "PR_CLOSEOUT").status == "PASS"   # flaky infra: re-run, no attempt
    d.update(current_state="PR_CLOSEOUT", repair={"attempt_count": 0, "dev_attempt_count": 0, "attempt_history": [], "classification": ""})
    d["pr"]["ci_status"] = "PASS"
    assert manifest.transition(write(tmp_path, d), "RELEASE_READINESS").status == "FAIL"   # a thread is still open
    d["pr"]["unresolved_threads"] = 0
    assert manifest.transition(write(tmp_path, d), "RELEASE_READINESS").status == "PASS"


# --- diff coverage + component tests ------------------------------------------------------------------------------
def _jacoco(path, lines):
    """lines: {line: (missed_instr, covered_instr)} for x/Svc.java"""
    body = "".join(f'<line nr="{n}" mi="{mi}" ci="{ci}" mb="0" cb="0"/>' for n, (mi, ci) in lines.items())
    path.mkdir(parents=True, exist_ok=True)
    (path / "jacoco.xml").write_text(f'<report name="r"><package name="x"><sourcefile name="Svc.java">{body}'
                                     f'</sourcefile></package></report>')
    return str(path)


def test_diff_coverage(svc, tmp_path):
    import os, time
    (svc / "src/main/java/x/Svc.java").write_text(
        "class Svc {\n int f() { return 1; }\n int g(int a) {\n  if (a < 0) return -1;\n  return a;\n }\n}\n")
    time.sleep(0.05)
    rep = _jacoco(tmp_path / "jac", {2: (0, 2), 4: (3, 0), 5: (0, 2)})       # line 4 (the error path) never ran
    res = diff_coverage.run(str(svc), "HEAD", rep, 80)
    assert res.status == "FAIL" and {"DCV-001", "DCV-003"} <= ids(res) and res.inputs["diff_coverage"] == 66.7
    rep = _jacoco(tmp_path / "jac2", {2: (0, 2), 4: (0, 3), 5: (0, 2)})
    assert diff_coverage.run(str(svc), "HEAD", rep, 80).status == "PASS"
    later = time.time() + 60
    os.utime(svc / "src/main/java/x/Svc.java", (later, later))            # source edited after the run
    assert "DCV-004" in ids(diff_coverage.run(str(svc), "HEAD", rep, 80))
    (svc / "src/main/java/x/Other.java").write_text("class Other { int h() { return 3; } }\n")
    assert "DCV-002" in ids(diff_coverage.run(str(svc), "HEAD", rep, 80))
    assert diff_coverage.run(str(svc), "HEAD", str(tmp_path / "none"), 80).status == "NOT_RUN"


def test_component_test_required_when_system_tests_na(tmp_path):
    ac = {"id": "AC-1", "text": "missing name field -> notification still sent, no 500", "api": True,
          "code_refs": ["src/main/java/x/Svc.java"], "test_ids": ["x.SvcTest#a"], "system_test_ids": ["TC_x_Notify_01"]}
    d = doc(requirements={"source_refs": [], "acceptance_criteria": [ac], "open_ambiguities": 0},
            test_results={"x.SvcTest#a": "PASS", "TC_x_Notify_01": "DEFERRED"},
            exceptions=[{"action": "gate_exception", "gate": "system_tests", **HUMAN}],      # deferred to the post-merge run
            gates=[{"gate": "system_tests", "status": "NOT_APPLICABLE"}])
    assert "TRC-007" in ids(traceability.run(write(tmp_path, d)))
    ac["component_test_ids"] = ["x.SvcControllerTest#missingName"]
    d["test_results"]["x.SvcControllerTest#missingName"] = "PASS"
    assert traceability.run(write(tmp_path, d)).status == "PASS"
    d["gates"][0]["status"] = "PASS"; ac.pop("component_test_ids"); d["exceptions"] = []   # real system tests ran
    d["test_results"]["TC_x_Notify_01"] = "PASS"
    assert "TRC-007" not in ids(traceability.run(write(tmp_path, d)))


# --- module onboarding --------------------------------------------------------------------------------------------
def _module_repo(tmp_path, keep_agent=True):
    r = tmp_path / "newmod"
    (r / "svc/src/main/java/x").mkdir(parents=True)
    (r / "svc/src/test/java/x").mkdir(parents=True)
    arg = "${argLine} --enable-preview" if keep_agent else "--enable-preview"
    (r / "pom.xml").write_text(f"""<project xmlns="http://maven.apache.org/POM/4.0.0"><artifactId>parent</artifactId>
<properties><java.version>21</java.version></properties><modules><module>svc</module></modules>
<build><plugins><plugin><artifactId>maven-surefire-plugin</artifactId><configuration><argLine>{arg}</argLine></configuration></plugin>
<plugin><groupId>org.jacoco</groupId><artifactId>jacoco-maven-plugin</artifactId></plugin></plugins></build></project>""")
    (r / "svc/pom.xml").write_text("<project><artifactId>svc</artifactId><dependencies><dependency><artifactId>junit-jupiter</artifactId>"
                                   "</dependency><dependency><artifactId>mockito-core</artifactId></dependency></dependencies></project>")
    (r / "svc/src/main/java/x/ACtrl.java").write_text("class ACtrl {}")
    (r / "svc/src/test/java/x/AControllerTest.java").write_text("class AControllerTest { MockMvc m; }")
    (r / ".github/workflows").mkdir(parents=True)
    (r / ".github/workflows/push.yml").write_text("jobs:\n  b:\n    uses: mosip/kattu/.github/workflows/maven-build.yml@master\n")
    (r / "db_upgrade_scripts/m/sql").mkdir(parents=True)
    (r / "db_upgrade_scripts/m/sql/1.0_to_1.1_upgrade.sql").write_text("select 1;")
    (r / "AGENTS.md").write_text("rules")
    res = r / "api-test/src/main/resources/newmod"
    (res / "Create").mkdir(parents=True)
    (res / "Unused").mkdir(parents=True)
    (r / "api-test/testNgXmlFiles").mkdir(parents=True)
    (r / "api-test/testNgXmlFiles/newmodSuite.xml").write_text(
        '<suite name="s"><test name="Create"><parameter name="ymlFile" value="newmod/Create/Create.yml"/>'
        '<parameter name="idKeyName" value="id"/><classes><class name="x.SimplePost"/></classes></test></suite>')
    (res / "Create/Create.yml").write_text("Create:\n   Newmod_Create_Valid_Smoke_sid:\n      uniqueIdentifier: TC_newmod_Create_01\n"
                                           "      input: '{}'\n      output: '{}'\n   Newmod_Create_Neg:\n"
                                           "      uniqueIdentifier: TC_newmod_Create_02\n      input: '{}'\n      output: '{}'\n")
    (res / "Unused/Unused.yml").write_text("Unused:\n   Newmod_Unused_X:\n      uniqueIdentifier: TC_newmod_Unused_01\n")
    git(tmp_path, "init", "-q", str(r))
    commit_all(r, "base")
    return r


def test_discover_drafts_context_and_profile(tmp_path):
    r = _module_repo(tmp_path, keep_agent=False)
    before = git(r, "status", "--porcelain")
    out = tmp_path / "ctx"
    res = discover.run(str(r), "newmod", str(out))
    assert res.status == "PASS" and {"DSC-001", "DSC-003", "DSC-005", "DSC-006"} <= ids(res)
    assert git(r, "status", "--porcelain") == before                         # read-only on the module repo
    facts = json.loads((out / ".crewline/development/discovery.json").read_text())
    assert facts["maven"]["parent"] == "pom.xml" and facts["services"]["svc"]["mockmvc"] == 1
    import yaml as _y
    prof = _y.safe_load((out / ".crewline/qa/profile.draft.yaml").read_text())
    assert prof["testcase_rules"]["case_name_prefix"] == "Newmod_"
    assert prof["testcase_rules"]["unique_identifier_pattern"] == r"^TC_(?i:newmod)_[A-Za-z0-9]+_\d+$"
    assert prof["ignore_yaml"] == ["newmod/Unused/Unused.yml"] and prof["module_code"] == "NEWMOD"
    md = (out / ".crewline/development/local-context.md").read_text()
    assert "DRAFT" in md and "coverage will not attach" in md and "mosip/kattu" in md
    assert "DSC-007" in ids(discover.run(str(r), "newmod", str(out)))            # never overwrites without --force
    r2 = _module_repo(tmp_path / "b", keep_agent=True)
    assert "DSC-001" not in ids(discover.run(str(r2), "newmod", str(tmp_path / "ctx2")))


def test_onboarding_path_needs_owner_review(tmp_path):
    assert manifest.Ctx({"task_type": "module-onboarding"}).next_states("IMPACT_ANALYSIS") == ["KNOWLEDGE_UPDATE"]
    d = doc(task_type="module-onboarding", risk_tier="L1", current_state="KNOWLEDGE_UPDATE",
            gates=[{"gate": g, "status": "PASS"} for g in ("onboarding_discovery", "onboarding_baseline", "secrets")])
    assert "onboarding_review" in " ".join(f.message for f in manifest.transition(write(tmp_path, d), "FINAL_REPORT").findings)
    d["approvals"] = [{"action": "onboarding_review", **HUMAN}]
    assert manifest.transition(write(tmp_path, d), "FINAL_REPORT").status == "PASS"


# --- mixed stacks (Java + React/TypeScript), learned from onboarding inji-verify ------------------------------------
def _js_repo(tmp_path):
    r = tmp_path / "web"
    (r / "ui/src/__tests__").mkdir(parents=True)
    (r / "ui/src/App.ts").write_text("export const f = (a: number) => {\n  if (a < 0) return -1;\n  return a;\n};\n")
    (r / "ui/src/__tests__/App.spec.ts").write_text(
        "it('returns a', () => { expect(f(1)).toBe(1); });\nit('negative', () => { expect(f(-1)).toBe(-1); });\n")
    (r / "ui/package.json").write_text('{"name": "ui", "scripts": {"test": "jest --coverage"}, "devDependencies": {"jest": "29"}}')
    (r / "ui/jest.config.js").write_text("module.exports = { coverageReporters: ['html'] };")
    git(tmp_path, "init", "-q", str(r))
    commit_all(r, "base")
    return r


@pytest.mark.parametrize("body,rule", [
    ("it('returns a', () => { expect(f(1)).toBe(1); });\n", "TIN-001"),
    ("it('returns a', () => { expect(f(1)).toBe(1); });\nit.skip('negative', () => { expect(f(-1)).toBe(-1); });\n", "TIN-002"),
    ("it('returns a', () => { expect(f(1)).toBe(1); });\nit('negative', () => { f(-1); });\n", "TIN-003"),
    ("it.only('returns a', () => { expect(f(1)).toBe(1); });\nit('negative', () => { expect(f(-1)).toBe(-1); });\n", "TIN-002"),
])
def test_js_test_integrity(tmp_path, body, rule):
    r = _js_repo(tmp_path)
    (r / "ui/src/__tests__/App.spec.ts").write_text(body)
    res = test_integrity.run(str(r), "HEAD")
    assert res.status == "FAIL" and rule in ids(res)


def test_lcov_diff_coverage(tmp_path):
    import time
    r = _js_repo(tmp_path)
    (r / "ui/src/App.ts").write_text("export const f = (a: number) => {\n  if (a < 0) return -1;\n  if (a > 9) return 9;\n  return a;\n};\n")
    time.sleep(0.05)
    cov = r / "ui/coverage"; cov.mkdir()
    (cov / "lcov.info").write_text("TN:\nSF:src/App.ts\nDA:1,3\nDA:2,3\nDA:3,0\nDA:4,3\nend_of_record\n")
    res = diff_coverage.run(str(r), "HEAD", str(cov), 80, "*ui/src/*")
    assert res.status == "FAIL" and res.inputs["diff_coverage"] == 0.0 and "DCV-003" in ids(res)   # only line 3 changed, not run
    (cov / "lcov.info").write_text("TN:\nSF:src/App.ts\nDA:1,3\nDA:2,3\nDA:3,1\nDA:4,3\nend_of_record\n")
    assert diff_coverage.run(str(r), "HEAD", str(cov), 80, "*ui/src/*").status == "PASS"


def test_discover_mixed_stack_and_conventions(tmp_path):
    r = _module_repo(tmp_path)
    ui = r / "ui-test"; ui.mkdir()
    (ui / "pom.xml").write_text("<project><artifactId>uitest</artifactId><properties><selenium.version>4</selenium.version>"
                                "</properties></project>")
    (r / "web/src/__tests__").mkdir(parents=True)
    (r / "web/package.json").write_text('{"name": "web", "scripts": {"test:ci": "jest --ci"}, "devDependencies": {"jest": "29"}}')
    (r / "web/jest.config.js").write_text("module.exports = { coverageReporters: ['html'] };")
    (r / "web/src/__tests__/a.spec.tsx").write_text("it('x', () => expect(1).toBe(1));")
    y = r / "api-test/src/main/resources/newmod/Create/Create.yml"
    y.write_text(y.read_text() + "".join(
        f"   DC_{i:02d}_dcapi_request:\n      uniqueIdentifier: TC_newmod_Dc_Api_Request_{i:02d}\n" for i in range(1, 5)))
    commit_all(r, "mixed")
    out = tmp_path / "ctx"
    res = discover.run(str(r), "newmod", str(out))
    facts = json.loads((out / ".crewline/development/discovery.json").read_text())
    assert facts["ui_test_projects"] == ["ui-test"] and "ui-test" not in facts["services"]
    assert facts["node"][0]["path"] == "web" and facts["node"][0]["lcov"] is False and "DSC-008" in ids(res)
    import yaml as _y
    rules = _y.safe_load((out / ".crewline/qa/profile.draft.yaml").read_text())["testcase_rules"]
    assert rules["unique_identifier_pattern"] == r"^TC_(?i:newmod)_[A-Za-z0-9_]+_\d+$"     # underscores inside the subject
    assert sorted(rules["case_name_prefix"]) == ["DC_", "Newmod_"]                          # two case families
    assert facts["baseline"]["by_rule"].get("NAME-001", 0) == 0 and facts["baseline"]["by_rule"].get("NAME-002", 0) == 0
    assert "Node / front-end projects" in (out / ".crewline/development/local-context.md").read_text()


def test_ungated_id_capture_from_module_script_class(tmp_path):
    """inji-verify: a module script class that stores IDs without a _sid check produces $ID keys for every case."""
    r = _module_repo(tmp_path)
    js = r / "api-test/src/main/java/x/testscripts"; js.mkdir(parents=True)
    (js / "SimplePost.java").write_text('class SimplePost { void t() { writeAutoGeneratedId(response, "requestId", testCaseName); } }')
    res = r / "api-test/src/main/resources/newmod"
    (res / "Get").mkdir()
    (res / "Get/Get.yml").write_text("Get:\n   Newmod_Get_Valid:\n      uniqueIdentifier: TC_newmod_Get_01\n"
                                     "      input: '{\"r\": \"$ID:Create_Neg_requestId$\"}'\n      output: '{}'\n")
    suite = r / "api-test/testNgXmlFiles/newmodSuite.xml"
    suite.write_text(suite.read_text().replace("</suite>", '<test name="Get"><parameter name="ymlFile" value="newmod/Get/Get.yml"/>'
                                               '<classes><class name="x.GetWithParam"/></classes></test></suite>'))
    commit_all(r, "ungated")
    out = tmp_path / "ctx"
    discover.run(str(r), "newmod", str(out))
    facts = json.loads((out / ".crewline/development/discovery.json").read_text())
    assert facts["api_test"]["id_capture"] == {"extra_id_fields_by_class": {"SimplePost": ["requestId"]},
                                               "extra_id_fields_gate": {}, "capture_without_sid": ["SimplePost"],
                                               "id_key_from_input": {}}
    js2 = r / "api-test/src/main/java/x/testscripts/CreateThing.java"            # prereg-style: gated on another token
    js2.write_text('class CreateThing { void t() {\n if (name.toLowerCase().contains("_pos")) {\n'
                   '  writeAutoGeneratedId(dto.getTestCaseName(), "EMAIL", email);\n } } }')
    suite.write_text(suite.read_text().replace('x.SimplePost"', 'x.CreateThing"'))
    commit_all(r, "gated")
    discover.run(str(r), "newmod", str(out), force=True)
    cap = json.loads((out / ".crewline/development/discovery.json").read_text())["api_test"]["id_capture"]
    assert cap["extra_id_fields_by_class"]["CreateThing"] == ["EMAIL"] and cap["extra_id_fields_gate"] == {"CreateThing": "_pos"}
    assert facts["baseline"]["by_rule"].get("DEP-001", 0) == 0          # Newmod_Create_Neg (no _sid) now produces the key


def test_permission_gated_capabilities_offer_three_choices():
    import yaml as _y
    spec = _y.safe_load((Path(__file__).resolve().parents[2] / "core/tools.yaml").read_text(encoding="utf-8"))
    gated = spec["permission_gated"]
    assert "package_install" in gated
    for name, cap in gated.items():
        assert [o["id"] for o in cap["options"]] == ["user_runs", "allow_rule", "leave_inferred"], name
        assert all(o["steps"] for o in cap["options"]) and cap["risk"]
    rule = " ".join(gated["package_install"]["options"][1]["steps"])
    assert "Bash(npm ci:*)" in rule and "never a rule for the `crewline run` wrapper" in rule


# --- SDET in every code flow ---------------------------------------------------------------------------------------
def test_sdet_required_in_code_flows(tmp_path):
    # no code before a test environment plan
    d = doc(current_state="REPO_READY", gates=READY, test_plan={})
    assert "test_plan.environment" in " ".join(f.message for f in manifest.transition(write(tmp_path, d), "IMPLEMENTATION").findings)
    # a user-facing AC needs an automated system test, with a result on the branch build
    ac = {"id": "AC-1", "text": "t", "ui": True, "code_refs": ["src/main/java/x/Svc.java"], "test_ids": ["x.SvcTest#a"]}
    d = doc(requirements={"source_refs": [], "acceptance_criteria": [ac], "open_ambiguities": 0}, test_results={"x.SvcTest#a": "PASS"})
    assert "TRC-008" in ids(traceability.run(write(tmp_path, d)))
    ac["system_test_ids"] = ["TC_x_Ui_01"]
    assert "TRC-009" in ids(traceability.run(write(tmp_path, d)))
    d["test_results"]["TC_x_Ui_01"] = "DEFERRED"                                 # deferral without a human exception
    assert "TRC-009" in ids(traceability.run(write(tmp_path, d)))
    d["test_results"]["TC_x_Ui_01"] = "PASS"
    assert traceability.run(write(tmp_path, d)).status == "PASS"
    # system tests / regression cannot be silently N/A; open in-scope defects block the PR
    gates = [{"gate": g, "status": "PASS"} for g in ["quality_gate", "full_regression", "secrets"] + manifest.load_template("feature")["required_gates"]]
    for g in gates:
        if g["gate"] == "system_tests":
            g["status"] = "NOT_APPLICABLE"
    d = doc(current_state="QUALITY_GATE", gates=gates, approvals=[{"action": "commit_pr", **HUMAN}],
            defects=[{"id": "D1", "class": "CODE_UNDER_CHANGE", "status": "open", "found_by": "TC_x_Ui_01"},
                     {"id": "D2", "class": "APPLICATION", "status": "open", "found_by": "TC_x_Reg_07"}])
    regression_record(tmp_path)
    found = [f.rule_id for f in manifest.transition(write(tmp_path, d), "COMMIT_PR").findings]
    assert "MAN-015" in found and found.count("MAN-014") == 2
    d["defects"][0]["status"] = "fixed"
    d["defects"][1].update(status="raised", bug="MOSIP-99999")
    d["exceptions"] = [{"action": "gate_exception", "gate": "system_tests", **HUMAN, "scope": "deferred to dev env 2026-11-02"}]
    assert manifest.transition(write(tmp_path, d), "COMMIT_PR").status == "PASS"


# --- module context lives in the module: <repo>/.crewline/<role>/ ---------------------------------------------------
def test_crewline_layout_and_default_profile(tmp_path):
    import yaml as _y
    from crewline.__main__ import main
    r = _module_repo(tmp_path)
    out = tmp_path / "draft"
    discover.run(str(r), "newmod", str(out))
    idx = _y.safe_load((out / ".crewline/module.yaml").read_text())
    assert idx["module"] == "newmod" and idx["roles"]["development"] == "development/local-context.md"
    assert "qa/profile.yaml" in idx["roles"]["qa"] and (out / ".crewline/README.md").exists()
    for f in ("development/local-context.md", "qa/module-context.md", "qa/profile.draft.yaml"):
        assert (out / ".crewline" / f).exists(), f
    # publishing = the reviewed folder copied into the module repo; validators then find the profile on their own
    with pytest.raises(SystemExit) if False else _nullcontext():
        assert main(["testcase", "--module-root", str(r / "api-test"), "--scope", "all"]) == 2      # no profile yet: NOT_RUN
    dest = r / ".crewline/qa"; dest.mkdir(parents=True)
    (dest / "profile.yaml").write_text((out / ".crewline/qa/profile.draft.yaml").read_text())
    assert main(["testcase", "--module-root", str(r / "api-test"), "--scope", "all"]) in (0, 1)       # ran with the module's profile


class _nullcontext:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# --- one clone per repo; no worktree left behind -------------------------------------------------------------------
def test_task_cannot_close_with_open_worktrees(tmp_path):
    d = doc(current_state="FINAL_REPORT", final_report_ref="final-report.md",
            workspace={"checkout_mode": "worktree", "open_worktrees": 1})
    assert "open_worktrees" in " ".join(f.message for f in manifest.transition(write(tmp_path, d), "DONE").findings)
    d["workspace"]["open_worktrees"] = 0
    assert manifest.transition(write(tmp_path, d), "DONE").status == "PASS"
    d["workspace"] = {"checkout_mode": "single"}                                   # user chose branch switching
    assert manifest.transition(write(tmp_path, d), "DONE").status == "PASS"


# --- coding standards before review (not the reviewer's job) -------------------------------------------------------
def _std_record(svc, tmp_path, command, exit_code=0):
    out = tmp_path / "ev/std.json"
    evidence.capture(str(svc), str(out), [sys.executable, "-c", "print('ok')"])
    rec = json.loads(out.read_text(encoding="utf-8"))
    rec["command"] = command
    rec["exit_code"] = exit_code
    out.write_text(json.dumps(rec), encoding="utf-8")
    return str(out)


def _spotless_pom(svc):
    (svc / "pom.xml").write_text(
        "<project><artifactId>x</artifactId><build><plugins>"
        "<plugin><groupId>com.diffplug.spotless</groupId>"
        "<artifactId>spotless-maven-plugin</artifactId></plugin>"
        "</plugins></build></project>", encoding="utf-8")


def test_coding_standards_no_checker_passes(svc, tmp_path):
    res = coding_standards.run(str(svc), "HEAD", write(tmp_path, doc()))
    assert res.status == "PASS" and "STD-004" in ids(res)


def test_coding_standards_debug_in_production_blocks(svc, tmp_path):
    (svc / "src/main/java/x/Svc.java").write_text(
        "class Svc { int f() { System.out.println(1); return 1; } }\n", encoding="utf-8")
    res = coding_standards.run(str(svc), "HEAD", write(tmp_path, doc()))
    assert res.status == "FAIL" and "STD-003" in ids(res)


def test_coding_standards_debug_in_tests_is_allowed(svc, tmp_path):
    p = svc / "src/test/java/x/SvcTest.java"
    p.write_text(p.read_text(encoding="utf-8").replace(
        "assertEquals", "System.out.println(1); assertEquals", 1), encoding="utf-8")
    res = coding_standards.run(str(svc), "HEAD", write(tmp_path, doc()))
    assert "STD-003" not in ids(res)


def test_coding_standards_checker_requires_evidence(svc, tmp_path):
    _spotless_pom(svc)
    res = coding_standards.run(str(svc), "HEAD", write(tmp_path, doc()))
    assert res.status == "FAIL" and "STD-001" in ids(res)


def test_coding_standards_checker_with_fresh_record_passes(svc, tmp_path):
    _spotless_pom(svc)
    rec = _std_record(svc, tmp_path, "mvn -B -f pom.xml spotless:check")
    d = doc(implementation={"standards_evidence": [rec]})
    res = coding_standards.run(str(svc), "HEAD", write(tmp_path, d))
    assert res.status == "PASS" and "STD-001" not in ids(res) and "STD-002" not in ids(res)


def test_coding_standards_failed_record_blocks(svc, tmp_path):
    _spotless_pom(svc)
    rec = _std_record(svc, tmp_path, "mvn -B -f pom.xml spotless:check", exit_code=1)
    d = doc(implementation={"standards_evidence": [rec]})
    assert "STD-002" in ids(coding_standards.run(str(svc), "HEAD", write(tmp_path, d)))


def test_review_refused_until_coding_standards(tmp_path):
    d = doc(current_state="UNIT_INTEGRATION_TEST",
            gates=[{"gate": "deterministic_validation", "status": "PASS"}])
    res = manifest.transition(write(tmp_path, d), "IMPLEMENTATION_REVIEW")
    msg = " ".join(f.message for f in res.findings)
    assert res.status == "FAIL" and "coding_standards" in msg and "robustness" in msg
    d["gates"].append({"gate": "coding_standards", "status": "PASS"})
    assert "robustness" in " ".join(f.message for f in manifest.transition(write(tmp_path, d), "IMPLEMENTATION_REVIEW").findings)
    d["gates"].append({"gate": "robustness", "status": "PASS"})
    assert "requirement_fit" in " ".join(f.message for f in manifest.transition(write(tmp_path, d), "IMPLEMENTATION_REVIEW").findings)
    d["gates"].append({"gate": "requirement_fit", "status": "PASS"})
    (tmp_path / "packets").mkdir(exist_ok=True)
    (tmp_path / "packets/IMPLEMENTATION.md").write_text("ACs: AC-1\npaths: src/**\nFAIL findings: none\n", encoding="utf-8")
    res = manifest.transition(write(tmp_path, d), "IMPLEMENTATION_REVIEW")
    assert res.status == "FAIL" and "DEPSEC-001" in ids(res)
    ev = tmp_path / "evidence"
    ev.mkdir(exist_ok=True)
    (ev / "dependency-security.json").write_text(json.dumps(
        {"command": "mvn -B org.owasp:dependency-check-maven:check", "exit_code": 0,
         "started_at": "2026-10-28T10:00:00Z", "commit": "abc", "worktree_digest": "0", "log": "x.log"}))
    assert manifest.transition(write(tmp_path, d), "IMPLEMENTATION_REVIEW").status == "PASS"


def test_review_refused_without_implementation_packet(tmp_path):
    d = doc(current_state="UNIT_INTEGRATION_TEST",
            gates=[{"gate": g, "status": "PASS"} for g in
                   ("deterministic_validation", "coding_standards", "robustness", "requirement_fit")])
    res = manifest.transition(write(tmp_path, d), "IMPLEMENTATION_REVIEW")
    assert res.status == "FAIL" and "PKT-001" in ids(res)


def test_discover_records_spotless_command(tmp_path):
    r = _module_repo(tmp_path)
    pom = r / "pom.xml"
    pom.write_text(pom.read_text(encoding="utf-8").replace(
        "</plugins>", "<plugin><artifactId>spotless-maven-plugin</artifactId></plugin></plugins>"), encoding="utf-8")
    out = tmp_path / "ctx-std"
    res = discover.run(str(r), "newmod", str(out), force=True)
    assert "DSC-009" in ids(res)
    facts = json.loads((out / ".crewline/development/discovery.json").read_text(encoding="utf-8"))
    assert any(t["id"] == "spotless-maven-plugin" for t in facts["standards"]["tools"])
    assert "spotless:check" in (out / ".crewline/development/local-context.md").read_text(encoding="utf-8")


# --- robustness: exceptions, error paths, hot-path (before review) -----------------------------------------------
ROB = {"exception_reuse": "src/main/java/x/Handler.java:12",
       "hot_path_notes": "reuses Svc.f(); no extra repository round-trip"}


def test_robustness_empty_catch_blocks(svc, tmp_path):
    (svc / "src/main/java/x/Svc.java").write_text(
        "class Svc { int f() { try { return 1; } catch (Exception e) { } } }\n", encoding="utf-8")
    d = doc(implementation={"robustness": ROB})
    assert "ROB-001" in ids(robustness.run(str(svc), "HEAD", write(tmp_path, d)))


def test_robustness_swallowed_exception_blocks(svc, tmp_path):
    (svc / "src/main/java/x/Svc.java").write_text(
        "class Svc { int f() { try { return 1; } catch (Exception e) { log.warn(e); } } }\n", encoding="utf-8")
    d = doc(implementation={"robustness": ROB})
    assert "ROB-002" in ids(robustness.run(str(svc), "HEAD", write(tmp_path, d)))


def test_robustness_specific_exception_rethrow_passes_scan(svc, tmp_path):
    (svc / "src/main/java/x/Svc.java").write_text(
        "class Svc { int f() throws SvcException {\n"
        "  try { return 1; } catch (IOException e) { throw new SvcException(e); } } }\n", encoding="utf-8")
    d = doc(implementation={"robustness": ROB})
    res = robustness.run(str(svc), "HEAD", write(tmp_path, d))
    assert {"ROB-001", "ROB-002", "ROB-003", "ROB-004"}.isdisjoint(ids(res))
    assert res.status == "PASS"


def test_robustness_sleep_and_raw_throws_block(svc, tmp_path):
    (svc / "src/main/java/x/Svc.java").write_text(
        "class Svc { int f() throws Exception { Thread.sleep(50); return 1; } }\n", encoding="utf-8")
    d = doc(implementation={"robustness": ROB})
    found = ids(robustness.run(str(svc), "HEAD", write(tmp_path, d)))
    assert {"ROB-003", "ROB-004"} <= found


def test_robustness_record_required_before_review(svc, tmp_path):
    res = robustness.run(str(svc), "HEAD", write(tmp_path, doc()))
    assert "ROB-006" in ids(res)


def test_robustness_api_error_path_must_pass(svc, tmp_path):
    ac = {"id": "AC-1", "text": "missing name -> 400 MOSIP-X", "api": True,
          "code_refs": ["src/main/java/x/Svc.java"], "test_ids": ["x.SvcTest#returnsOne"],
          "component_test_ids": ["x.SvcControllerTest#missingName"]}
    rob = {**ROB, "error_paths": [{"ac_id": "AC-1", "status": 400, "error_code": "MOSIP-X",
                                   "test_id": "x.SvcControllerTest#missingName"}]}
    d = doc(requirements={"source_refs": ["MOSIP-1"], "acceptance_criteria": [ac], "open_ambiguities": 0},
            implementation={"robustness": rob}, test_results={"x.SvcTest#returnsOne": "PASS"})
    assert "ROB-005" in ids(robustness.run(str(svc), "HEAD", write(tmp_path, d)))
    d["test_results"]["x.SvcControllerTest#missingName"] = "PASS"
    assert "ROB-005" not in ids(robustness.run(str(svc), "HEAD", write(tmp_path, d)))


# --- requirement-clarity: no design/tests on a vague story or bug --------------------------------
CLEAR_AC = {"id": "AC-1", "text": "POST /x with a missing name returns 400 and error MOSIP-X"}


def test_requirement_clarity_blocks_empty_and_unidentified(tmp_path):
    d = doc(tracker={})
    found = ids(requirement_clarity.run(write(tmp_path, d)))
    assert {"REQ-001", "REQ-004"} <= found
    d["requirements"] = {"source_refs": ["MOSIP-1"], "acceptance_criteria": [CLEAR_AC], "open_ambiguities": 0}
    assert requirement_clarity.run(write(tmp_path, d, "ok.json")).status == "PASS"
    d["requirements"]["source_refs"] = []                                       # the confirmed tracker key is identity too
    d["tracker"] = {"system": "github", "key": "inji/pixelpass#185", "confirmed_by": "mohan"}
    assert requirement_clarity.run(write(tmp_path, d, "gh.json")).status == "PASS"


def test_requirement_clarity_blocks_vague_ac_and_open_questions(tmp_path):
    vague = {"id": "AC-1", "text": "the change should work as expected for the operator"}
    d = doc(requirements={"story_keys": ["MOSIP-1"], "acceptance_criteria": [vague], "open_ambiguities": 2})
    found = ids(requirement_clarity.run(write(tmp_path, d)))
    assert {"REQ-002", "REQ-003"} <= found


def test_requirement_clarity_skips_non_story_types(tmp_path):
    d = doc(task_type="refactor")
    assert requirement_clarity.run(write(tmp_path, d)).status == "PASS"


def test_impact_refused_until_requirement_clarity(tmp_path):
    d = doc(current_state="ACCEPTANCE_REVIEW", gates=[{"gate": "input_readiness", "status": "PASS"}])
    specialist(tmp_path, "ACCEPTANCE_REVIEW")
    res = manifest.transition(write(tmp_path, d), "IMPACT_ANALYSIS")
    msg = " ".join(f.message for f in res.findings)
    assert res.status == "FAIL" and "requirement_clarity" in msg and "acceptance_readiness" in msg
    d["gates"] += [{"gate": "requirement_clarity", "status": "PASS"},
                   {"gate": "acceptance_readiness", "status": "PASS"}]
    assert manifest.transition(write(tmp_path, d), "IMPACT_ANALYSIS").status == "PASS"


def test_docs_and_incidents_do_not_need_clarity_gate(tmp_path):
    d = doc(task_type="documentation", current_state="INPUT_VALIDATION",
            gates=[{"gate": "input_readiness", "status": "PASS"}])
    assert manifest.transition(write(tmp_path, d), "IMPACT_ANALYSIS").status == "PASS"
    d = doc(task_type="incident", risk_tier="L0", current_state="INPUT_VALIDATION")
    assert manifest.transition(write(tmp_path, d), "IMPACT_ANALYSIS").status == "PASS"


def test_requirement_clarity_cannot_be_excepted(tmp_path):
    names = list(dict.fromkeys(["quality_gate", "full_regression", "secrets"]
                               + manifest.load_template("feature")["required_gates"]))
    d = doc(current_state="QUALITY_GATE",
            gates=[{"gate": g, "status": "FAIL" if g == "requirement_clarity" else "PASS"} for g in names],
            approvals=[{"action": "commit_pr", **HUMAN}],
            exceptions=[{"action": "gate_exception", "gate": "requirement_clarity", **HUMAN}])
    msg = " ".join(f.message for f in manifest.transition(write(tmp_path, d), "COMMIT_PR").findings)
    assert "requirement_clarity" in msg


# --- requirement-fit: the diff must contain what the AC named -------------------------------------
def test_requirement_fit_blocks_when_diff_misses_ac_tokens(svc, tmp_path):
    ac = {**CLEAR_AC, "code_refs": ["src/main/java/x/Svc.java"]}
    d = doc(requirements={"source_refs": ["MOSIP-1"], "acceptance_criteria": [ac], "open_ambiguities": 0})
    assert "RFT-001" in ids(requirement_fit.run(str(svc), "HEAD", write(tmp_path, d)))
    (svc / "src/main/java/x/Svc.java").write_text(
        "class Svc { int f() { return 400; /* MOSIP-X POST /x */ } }\n", encoding="utf-8")
    (svc / "src/test/java/x/SvcTest.java").write_text(
        "class SvcTest { @Test void missingName() { assertEquals(400, 400); /* MOSIP-X POST /x */ } }\n",
        encoding="utf-8")
    assert requirement_fit.run(str(svc), "HEAD", write(tmp_path, d, "fit-ok.json")).status == "PASS"


def test_requirement_fit_blocks_when_tests_miss_ac_tokens(svc, tmp_path):
    ac = {**CLEAR_AC, "code_refs": ["src/main/java/x/Svc.java"]}
    (svc / "src/main/java/x/Svc.java").write_text(
        "class Svc { int f() { return 400; /* MOSIP-X POST /x */ } }\n", encoding="utf-8")
    d = doc(requirements={"source_refs": ["MOSIP-1"], "acceptance_criteria": [ac], "open_ambiguities": 0})
    assert "RFT-005" in ids(requirement_fit.run(str(svc), "HEAD", write(tmp_path, d)))


def test_requirement_fit_blocks_untouched_code_ref(svc, tmp_path):
    ac = {**CLEAR_AC, "code_refs": ["src/main/java/x/Missing.java"]}
    (svc / "src/main/java/x/Svc.java").write_text(
        "class Svc { int f() { return 400; /* MOSIP-X POST /x */ } }\n", encoding="utf-8")
    d = doc(requirements={"source_refs": ["MOSIP-1"], "acceptance_criteria": [ac], "open_ambiguities": 0})
    assert "RFT-002" in ids(requirement_fit.run(str(svc), "HEAD", write(tmp_path, d)))


def test_requirement_fit_skips_refactor(svc, tmp_path):
    assert requirement_fit.run(str(svc), "HEAD", write(tmp_path, doc(task_type="refactor"))).status == "PASS"


# --- branch env (Q8) and secret-scanned defect reports --------------------------------------------
def test_branch_env_refuses_released_as_branch(tmp_path):
    d = doc(test_plan={"environment": "api-internal.released"})
    assert "BEV-001" in ids(branch_env.run(write(tmp_path, d)))
    d["test_plan"] = {"environment": "https://api-internal.released/v1", "environment_kind": "local-stack"}
    assert "BEV-004" in ids(branch_env.run(write(tmp_path, d, "released-as-branch.json")))
    d["test_plan"] = {"environment": "local docker-compose", "environment_kind": "local-stack"}
    assert branch_env.run(write(tmp_path, d, "env-ok.json")).status == "PASS"


def test_branch_env_deferral_needs_exception(tmp_path):
    d = doc(test_plan={"environment": "later", "environment_kind": "post-merge-deferred"})
    assert "BEV-002" in ids(branch_env.run(write(tmp_path, d)))
    d["exceptions"] = [{"gate": "system_tests", **HUMAN}]
    assert "BEV-002" not in ids(branch_env.run(write(tmp_path, d, "defer.json")))


def test_application_defect_needs_clean_report(tmp_path):
    d = doc(defects=[{"id": "D1", "class": "APPLICATION", "status": "open"}])
    assert "DEF-001" in ids(defect_reports.run(write(tmp_path, d)))
    leak = tmp_path / "full-dump.txt"
    leak.write_text("admin_" + "password" + "=" + "Sup3rS3cretValue!\n", encoding="utf-8")
    d["defects"][0]["report_path"] = str(leak)
    assert "DEF-003" in ids(defect_reports.run(write(tmp_path, d, "leak.json")))
    clean = tmp_path / "error.txt"
    clean.write_text("status=500 MOSIP-X; no config dump\n", encoding="utf-8")
    d["defects"][0]["report_path"] = str(clean)
    assert defect_reports.run(write(tmp_path, d, "clean.json")).status == "PASS"


def test_frozen_eval_pack_nodeids_exist():
    res = evals_run.run(execute=False)
    assert "EVAL-001" not in ids(res), res.to_text()


def test_frozen_pack_has_eight_stories_with_outcomes():
    res = evals_run.run(execute=False, pack="frozen")
    assert res.status == "PASS", res.to_text()
    assert len(res.inputs["frozen_stories"]) == 8
    assert {"EVAL-004", "EVAL-005"}.isdisjoint(ids(res))


def test_cold_start_kit_passes():
    from crewline import cold_start
    res = cold_start.run()
    assert res.status == "PASS", res.to_text()
    assert "CS-004" in ids(res)


def test_task_new_with_repos_and_no_plugin_cache(tmp_path, monkeypatch):
    from crewline import task
    monkeypatch.delenv("CREWLINE_TASKS", raising=False)
    repos = []
    for n in ("commons", "masterdata"):
        r = tmp_path / n
        subprocess.run(["git", "init", "-q", str(r)], check=True)
        repos.append(str(r))
    res = task.run("new", "J25", "dependency-upgrade", repos=repos)
    assert res.status == "PASS", res.to_text()
    m = Path(res.inputs["manifest"])
    assert m.parent.parent == (tmp_path / "commons" / ".crewline-work" / "tasks").resolve() or \
        m.parent.parent.resolve() == (tmp_path / "commons" / ".crewline-work" / "tasks").resolve()
    scope = json.loads(m.read_text(encoding="utf-8"))["scope"]
    assert [r["role"] for r in scope["repositories"]] == ["primary", "dependency"] and scope["module"] == "commons"
    assert ".crewline-work/" in (tmp_path / "masterdata" / ".git" / "info" / "exclude").read_text()
    assert "TSK-011" in ids(task.run("new", "J26", "feature", repos=[str(tmp_path / "nope")]))
    monkeypatch.setattr(task, "CORE", tmp_path / "plugins" / "cache" / "crewline")
    monkeypatch.setattr(task, "tasks_root", lambda: tmp_path / "plugins" / "cache" / "crewline" / "tasks")
    assert "TSK-011" in ids(task.run("new", "J27", "feature"))


def test_task_queue_new_status_resume(tmp_path, monkeypatch):
    from crewline import task
    monkeypatch.setenv("CREWLINE_TASKS", str(tmp_path))
    assert "TSK-002" in ids(task.run("new", "bad id", "feature"))
    assert task.run("new", "t1", "feature").status == "PASS"
    assert "TSK-002" in ids(task.run("new", "t1", "feature"))
    st = task.run("status", "t1")
    assert st.status == "PASS" and "INTAKE" in st.to_text()
    nxt = task.run("resume", "t1")
    assert nxt.status == "PASS" and "INPUT_VALIDATION" in nxt.to_text()
    listed = task.run("list")
    assert listed.status == "PASS" and "t1" in listed.to_text()
    waiting = json.loads((tmp_path / "t1" / "task-manifest.json").read_text(encoding="utf-8"))
    waiting["current_state"] = "WAITING_FOR_USER"
    (tmp_path / "t1" / "task-manifest.json").write_text(json.dumps(waiting), encoding="utf-8")
    assert "TSK-003" in ids(task.run("resume", "t1"))
    (tmp_path / "t1/packets").mkdir()
    (tmp_path / "t1/packets/IMPLEMENTATION.md").write_text("ACs: AC-1\n", encoding="utf-8")
    (tmp_path / "t1/evidence").mkdir()
    (tmp_path / "t1/evidence/unit.json").write_text("{}", encoding="utf-8")
    (tmp_path / "t1/evidence/unit.log").write_text("secret-looking noise\n", encoding="utf-8")
    z = tmp_path / "t1.zip"
    assert task.run("export", "t1", out=str(z)).status == "PASS"
    import zipfile
    names = zipfile.ZipFile(z).namelist()
    assert "task-manifest.json" in names and "packets/IMPLEMENTATION.md" in names
    assert "evidence/unit.json" in names and not any(n.endswith(".log") for n in names)
    assert task.run("import", archive=str(z), task_id="t1-copy").status == "PASS"
    assert (tmp_path / "t1-copy/task-manifest.json").is_file()
    waiting["current_state"] = "INTAKE"
    waiting.pop("resume_state", None)
    (tmp_path / "t1" / "task-manifest.json").write_text(json.dumps(waiting), encoding="utf-8")
    started = task.run("start", "t1")
    assert started.status == "PASS" and "TSK-007" in ids(started) and "INPUT_VALIDATION" in started.to_text()


# --- plugin SessionStart hook: active only where .crewline/ exists ---------------------------------------------------
def test_session_start_activates_only_in_onboarded_repos(tmp_path):
    from crewline import session
    r = tmp_path / "mod"
    r.mkdir()
    git(tmp_path, "init", "-q", str(r))
    msg = session.context(str(r), env={})
    assert msg is None                                                       # enabled, not onboarded: silent
    (r / ".crewline/qa").mkdir(parents=True)
    (r / ".crewline/module.yaml").write_text("module: mod\nroles:\n  development: development/local-context.md\n  qa: []\n")
    sub_dir = r / "src"; sub_dir.mkdir()
    msg = session.context(str(sub_dir), env={})                            # session opened in a subfolder
    assert "ACTIVE" in msg and "module mod" in msg and "development, qa" in msg
    assert session.context(str(r), env={"CREWLINE": "off"}) is None          # one-session opt-out


def test_id_key_from_case_input(tmp_path):
    """pre-registration PreRegPiiRequest: the ID key comes from each case's `input._idKeyName`, not the suite."""
    r = _module_repo(tmp_path)
    js = r / "api-test/src/main/java/x/testscripts"; js.mkdir(parents=True)
    (js / "PiiRequest.java").write_text(
        'class PiiRequest { void t() {\n String k = input.has("_idKeyName") ? input.remove("_idKeyName").toString() : idKeyName;\n'
        ' if (name.contains("_sid")) { writeAutoGeneratedId(first, k, name); } } }')
    res = r / "api-test/src/main/resources/newmod"
    (res / "Pii").mkdir()
    (res / "Pii/Pii.yml").write_text(
        "Pii:\n   Newmod_Pii_Make_Smoke_sid:\n      uniqueIdentifier: TC_newmod_Pii_01\n"
        "      input: \'{\"_idKeyName\": \"applicationId\", \"a\": 1}\'\n      output: \'{}\'\n"
        "   Newmod_Pii_Use:\n      uniqueIdentifier: TC_newmod_Pii_02\n"
        "      input: \'{\"id\": \"$ID:Pii_Make_Smoke_sid_applicationId$\"}\'\n      output: \'{}\'\n")
    suite = r / "api-test/testNgXmlFiles/newmodSuite.xml"
    suite.write_text(suite.read_text().replace("</suite>", '<test name="Pii"><parameter name="ymlFile" value="newmod/Pii/Pii.yml"/>'
                                               '<classes><class name="x.PiiRequest"/></classes></test></suite>'))
    commit_all(r, "per-case id key")
    out = tmp_path / "ctx"
    discover.run(str(r), "newmod", str(out))
    facts = json.loads((out / ".crewline/development/discovery.json").read_text())
    assert facts["api_test"]["id_capture"]["id_key_from_input"] == {"PiiRequest": "_idKeyName"}
    assert facts["baseline"]["by_rule"].get("DEP-001", 0) == 0          # the producer is found through the input field


# --- Crewline updates reach machines only through a version bump ---------------------------------------------------
def _crewline_repo(tmp_path, version="0.2.0"):
    from crewline import version as ver
    r = tmp_path / "labs"
    core = r / "crewline"
    (core / ".claude-plugin").mkdir(parents=True)
    (core / "core").mkdir()
    (core / "validators/tests").mkdir(parents=True)

    def set_version(v):
        (core / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "crewline", "version": v}), encoding="utf-8")
        (core / "VERSION").write_text(v + "\n", encoding="utf-8")
    set_version(version)
    (core / "core/workflow.yaml").write_text("states: []\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    commit_all(r, "base")
    git(r, "branch", "base")
    return r, core, set_version, ver


def test_shipped_change_must_raise_the_version(tmp_path):
    r, core, set_version, ver = _crewline_repo(tmp_path)
    (core / "core/workflow.yaml").write_text("states: [A]\n", encoding="utf-8")
    commit_all(r, "rule change")
    res = ver.run(str(r), "base", core)
    assert res.status == "FAIL" and "VER-001" in ids(res)
    set_version("0.2.1")
    commit_all(r, "bump")
    assert ver.run(str(r), "base", core).status == "PASS"


def test_test_only_change_needs_no_bump_and_files_must_agree(tmp_path):
    r, core, set_version, ver = _crewline_repo(tmp_path)
    (core / "validators/tests/test_x.py").write_text("def test_x(): pass\n", encoding="utf-8")
    commit_all(r, "test only")
    assert ver.run(str(r), "base", core).status == "PASS"
    (core / "VERSION").write_text("0.3.0\n", encoding="utf-8")
    assert "VER-002" in ids(ver.run(str(r), "base", core))
    set_version("one")
    assert "VER-003" in ids(ver.run(str(r), None, core))


def test_task_records_version_and_manifest_warns_on_drift(tmp_path, monkeypatch):
    from crewline import task, version as ver
    monkeypatch.setenv("CREWLINE_TASKS", str(tmp_path / "tasks"))
    task.run("new", "T-V1", "feature")
    path = tmp_path / "tasks/T-V1/task-manifest.json"
    d = json.loads(path.read_text(encoding="utf-8"))
    assert d["crewline_version"] == ver.current()
    assert "MAN-016" not in ids(manifest.check(str(path)))
    d["crewline_version"] = "0.0.1"
    path.write_text(json.dumps(d), encoding="utf-8")
    res = manifest.check(str(path))
    drift = [f for f in res.findings if f.rule_id == "MAN-016"]
    assert drift and drift[0].severity == "WARN" and "0.0.1" in drift[0].message


def test_version_file_matches_plugin_manifest():
    from crewline import version as ver
    assert ver.run().status == "PASS"          # this Crewline: VERSION == plugin.json, valid semver


# --- tracker ticket before any design or code (MAN-017) ------------------------------------------------------------
def _intake(**over):
    return doc(**{"current_state": "INPUT_VALIDATION", "tracker": {},
                  "gates": [{"gate": "input_readiness", "status": "PASS"}], **over})


def test_tracker_required_to_leave_input_validation(tmp_path):
    d = _intake()
    res = manifest.transition(write(tmp_path, d), "REQUIREMENT_ANALYSIS")
    assert res.status == "FAIL" and "MAN-017" in ids(res)
    d["tracker"] = {"system": "github", "key": "inji/pixelpass#185", "found_by": "search"}    # found, not yet confirmed
    assert "MAN-017" in ids(manifest.transition(write(tmp_path, d), "REQUIREMENT_ANALYSIS"))
    d["tracker"]["confirmed_by"] = "mohan"
    assert manifest.transition(write(tmp_path, d), "REQUIREMENT_ANALYSIS").status == "PASS"
    w = _intake(tracker={"waived": {"action": "tracker_waiver", **HUMAN}})                     # the user declined, with a reason
    assert manifest.transition(write(tmp_path, w, "w.json"), "REQUIREMENT_ANALYSIS").status == "PASS"
    assert manifest.transition(write(tmp_path, _intake(), "wait.json"), "WAITING_FOR_USER").status == "FAIL"  # needs resume_state
    assert manifest.transition(write(tmp_path, _intake(resume_state="INPUT_VALIDATION"), "wait2.json"),
                               "WAITING_FOR_USER").status == "PASS"                        # asking the user is always allowed


def test_tracker_guard_on_every_template_resume_and_in_flight_task(tmp_path):
    inc = _intake(task_type="incident", risk_tier="L0")
    assert "MAN-017" in ids(manifest.transition(write(tmp_path, inc), "IMPACT_ANALYSIS"))
    resumed = doc(current_state="WAITING_FOR_USER", resume_state="IMPACT_ANALYSIS", tracker={})
    assert "MAN-017" in ids(manifest.transition(write(tmp_path, resumed, "r.json"), "IMPACT_ANALYSIS"))
    legacy = doc(current_state="QUALITY_GATE")                                              # started before 0.7.1
    legacy.pop("tracker")
    assert "MAN-017" in ids(manifest.transition(write(tmp_path, legacy, "l.json"), "COMMIT_PR"))


def test_tracker_record_rules(tmp_path):
    def found(t, approvals=()):
        return ids(manifest.check(write(tmp_path, doc(tracker=t, approvals=list(approvals)), "t.json")))
    assert "MAN-017" in found({"system": "jira", "key": "#12", "confirmed_by": "mohan"})        # not a Jira key
    assert "MAN-017" in found({"system": "github", "key": "PROJ-1", "confirmed_by": "mohan"})   # not a GitHub key
    assert "MAN-017" in found({"key": "PROJ-1", "confirmed_by": "mohan"})                      # no system
    assert "MAN-017" not in found({"system": "github", "key": "#12", "confirmed_by": "mohan"})
    created = {"system": "jira", "key": "INJIVER-9", "confirmed_by": "mohan", "created_by_crewline": True}
    assert "MAN-017" in found(created)                                                         # no create approval
    assert "MAN-017" not in found(created, [{"action": "create_tracker_item", **HUMAN}])
    assert "MAN-017" in found({**created, "waived": {"action": "tracker_waiver", **HUMAN}},
                              [{"action": "create_tracker_item", **HUMAN}])                    # key and waiver
    assert "MAN-010" in found({"system": "jira", "key": "INJIVER-9", "confirmed_by": "orchestrator"})
    assert "MAN-010" in found({"waived": {"action": "tracker_waiver", **HUMAN, "approver": "claude"}})
    assert "MAN-005" in found({"waived": {"action": "tracker_waiver", "approver": "mohan"}})
    posted = {"system": "jira", "key": "INJIVER-9", "confirmed_by": "mohan", "closure": {"status": "posted", "ref": "c1"}}
    assert "MAN-017" in found(posted)                                                          # comment needs approval
    assert "MAN-017" not in found(posted, [{"action": "tracker_comment", **HUMAN}])


def test_done_needs_tracker_closure_comment(tmp_path):
    d = doc(current_state="FINAL_REPORT", final_report_ref="final-report.md", workspace={"checkout_mode": "single"},
            tracker={"system": "jira", "key": "INJIVER-9", "confirmed_by": "mohan", "closure": {"status": "pending"},
                     "subtasks_waived": {"action": "subtasks_waiver", **HUMAN}})
    assert "tracker.closure.status" in manifest.transition(write(tmp_path, d), "DONE").to_text()
    d["tracker"]["closure"] = {"status": "posted", "ref": "https://x/browse/INJIVER-9?focusedCommentId=1"}
    d["approvals"] = [{"action": "tracker_comment", **HUMAN}]
    assert manifest.transition(write(tmp_path, d), "DONE").status == "PASS"


def test_branch_scope_accepts_tracker_keys(tmp_path):
    from crewline.branch import key_pattern, manifest_keys
    assert key_pattern("#185").search("feat: python sdk (#185)")
    assert key_pattern("inji/pixelpass#185").search("INJIVER-1: fix #185")
    assert not key_pattern("#18").search("fix #185")
    assert not key_pattern("MOSIP-1").search("MOSIP-17633: x")                   # was a substring match before 0.7.1
    assert key_pattern("MOSIP-1").search("MOSIP-1: x")
    d = doc(tracker={"system": "github", "key": "inji/pixelpass#185", "confirmed_by": "mohan"},
            requirements={"source_refs": [], "acceptance_criteria": [], "open_ambiguities": 0, "story_keys": ["INJIVER-1386"]})
    assert manifest_keys(write(tmp_path, d)) == ["inji/pixelpass#185", "INJIVER-1386"]


def test_task_new_has_empty_tracker():
    from crewline.task import skeleton
    assert skeleton("x", "feature")["tracker"] == {}


def test_pr_title_recorded_with_key_before_leaving_pr_closeout(tmp_path):
    pr = {"valid_comments_open": 0, "unresolved_threads": 0, "ci_status": "PASS", "monitoring": "app"}
    d = doc(current_state="PR_CLOSEOUT", pr=dict(pr))
    assert "no PR title" in manifest.transition(write(tmp_path, d), "RELEASE_READINESS").to_text()
    d["pr"]["title"] = "Handle missing identity name fields"
    assert "MAN-017" in ids(manifest.check(write(tmp_path, d)))
    d["pr"]["title"] = "MOSIP-1: Handle missing identity name fields"
    assert "MAN-017" not in ids(manifest.transition(write(tmp_path, d), "RELEASE_READINESS"))


# --- subtasks under the ticket (MAN-018) ----------------------------------------------------------------------------
def _sub(kind, n, **extra):
    return {"kind": kind, "key": f"INJIVER-{n}", **extra}


def test_subtasks_required_past_github_planning(tmp_path):
    base = {"system": "jira", "key": "INJIVER-1", "confirmed_by": "mohan"}
    d = doc(current_state="GITHUB_PLANNING", tracker=dict(base), gates=READY,
            approvals=[{"action": "base_branch", **HUMAN}])
    res = manifest.transition(write(tmp_path, d), "REPO_READY")
    assert "MAN-018" in ids(res) and "testcase, implementation, testing, review, pr" in res.to_text()
    d["tracker"]["subtasks"] = [_sub(k, i) for i, k in enumerate(("testcase", "implementation", "testing", "review"), 2)]
    msgs = " ".join(f.message for f in manifest.transition(write(tmp_path, d), "REPO_READY").findings if f.rule_id == "MAN-018")
    assert msgs.startswith("no subtask") and ": pr " in msgs
    d["tracker"]["subtasks"].append(_sub("pr", 9))
    assert manifest.transition(write(tmp_path, d), "REPO_READY").status == "PASS"
    w = doc(current_state="GITHUB_PLANNING", gates=READY, approvals=[{"action": "base_branch", **HUMAN}],
            tracker={**base, "subtasks_waived": {"action": "subtasks_waiver", **HUMAN}})
    assert manifest.transition(write(tmp_path, w, "w.json"), "REPO_READY").status == "PASS"
    ta = doc(task_type="test-automation", current_state="GITHUB_PLANNING", gates=READY,
             approvals=[{"action": "base_branch", **HUMAN}], tracker={**base, "subtasks": d["tracker"]["subtasks"]})
    assert "automation" in manifest.transition(write(tmp_path, ta, "ta.json"), "REPO_READY").to_text()   # template kinds
    inc = doc(task_type="incident", risk_tier="L0", current_state="IMPACT_ANALYSIS", tracker=dict(base))
    assert "MAN-018" not in ids(manifest.transition(write(tmp_path, inc, "inc.json"), "KNOWLEDGE_UPDATE"))  # no GITHUB_PLANNING


def test_subtask_record_rules(tmp_path):
    def found(subs, approvals=(), **extra):
        t = {"system": "jira", "key": "INJIVER-1", "confirmed_by": "mohan", "subtasks": subs, **extra}
        return ids(manifest.check(write(tmp_path, doc(tracker=t, approvals=list(approvals)), "s.json")))
    assert "MAN-018" in found([{"kind": "pr", "key": "#4"}])                                  # GitHub key under a Jira ticket
    assert "MAN-018" in found([_sub("pr", 4, created_by_crewline=True)])                      # no create approval
    assert "MAN-018" not in found([_sub("pr", 4, created_by_crewline=True)], [{"action": "create_tracker_subtasks", **HUMAN}])
    assert "MAN-018" in found([_sub("pr", 4, closure={"status": "posted", "ref": "c"})])      # comment needs approval
    assert "MAN-010" in found([], subtasks_waived={"action": "subtasks_waiver", **HUMAN, "approver": "orchestrator"})
    assert "MAN-005" in found([], subtasks_waived={"action": "subtasks_waiver", "approver": "mohan"})


def test_done_needs_each_subtask_closed(tmp_path):
    subs = [_sub(k, n, closure={"status": "posted", "ref": f"c{n}"}) for n, k in enumerate(("testcase", "implementation",
                                                                                         "testing", "review"), 5)]
    subs[2]["key"] = "INJIVER-3"
    subs.append(_sub("pr", 4, closure={"status": "pending"}))
    d = doc(current_state="FINAL_REPORT", final_report_ref="final-report.md", workspace={"checkout_mode": "single"},
            approvals=[{"action": "tracker_comment", **HUMAN}],
            tracker={"system": "jira", "key": "INJIVER-1", "confirmed_by": "mohan", "subtasks": subs,
                     "closure": {"status": "posted", "ref": "c0"}})
    res = manifest.transition(write(tmp_path, d), "DONE")
    assert "MAN-018" in ids(res) and "INJIVER-4" in res.to_text() and "INJIVER-3" not in res.to_text()
    subs[-1]["closure"] = {"status": "declined", "reason": "PR link is on the parent"}
    assert manifest.transition(write(tmp_path, d), "DONE").status == "PASS"
