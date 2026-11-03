"""Acceptance tests carried over from the SDET Architect Assistant (task_type test-automation).

Each test builds a throw-away git repo shaped like an API TestRig module, so they run anywhere
with git + Python and never touch a real MOSIP repository.
"""
import json
import subprocess
import textwrap
from pathlib import Path

import pytest

from crewline import dco, expected_diff, manifest, secrets_scan, testcase
from crewline.apitestrig import Module

PROFILE = {
    "module_code": "DEMO",
    "resources_root": "src/main/resources",
    "suite_files": ["testNgXmlFiles/demoSuite.xml"],
    "testcase_rules": {"case_name_prefix": "Demo_", "unique_identifier_pattern": r"^TC_(?i:demo)_[A-Za-z0-9]+_\d+$"},
}
SUITE = """<suite name="s">
  <test name="Create"><parameter name="ymlFile" value="demo/Create/Create.yml"/>
    <parameter name="idKeyName" value="appId"/><classes><class name="x.SimplePostForAutoGenId"/></classes></test>
  <test name="Get"><parameter name="ymlFile" value="demo/Get/Get.yml"/>
    <classes><class name="x.GetWithParam"/></classes></test>
</suite>"""
CREATE = """Create:
   Demo_Create_All_Valid_Smoke_sid:
      endPoint: /v1/create
      uniqueIdentifier: TC_demo_Create_01
      role: batch
      restMethod: post
      inputTemplate: demo/Create/Create
      outputTemplate: demo/Create/CreateResult
      input: '{"name": "a"}'
      output: '{"status": "ACTIVE"}'
"""
GET = """Get:
   Demo_Get_All_Valid_Smoke:
      endPoint: /v1/get
      uniqueIdentifier: TC_demo_Get_01
      role: batch
      restMethod: get
      inputTemplate: demo/Get/Get
      outputTemplate: demo/Get/GetResult
      input: '{"appId": "$ID:Create_All_Valid_Smoke_sid_appId$"}'
      output: '{"appId": "$ID:Create_All_Valid_Smoke_sid_appId$", "status": "ACTIVE"}'
"""


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "demo"
    mod = root / "api-test"
    res = mod / "src/main/resources/demo"
    (res / "Create").mkdir(parents=True)
    (res / "Get").mkdir(parents=True)
    (mod / "testNgXmlFiles").mkdir()
    (mod / "testNgXmlFiles/demoSuite.xml").write_text(SUITE)
    (res / "Create/Create.yml").write_text(CREATE)
    (res / "Create/CreateResult.hbs").write_text('{"status": "{{status}}"}')
    (res / "Get/Get.yml").write_text(GET)
    git(root, "init", "-q")
    git(root, "-c", "user.name=T", "-c", "user.email=t@example.org", "add", ".")
    git(root, "-c", "user.name=T", "-c", "user.email=t@example.org", "commit", "-q", "-s", "-m", "base")
    return root


def module(repo):
    return Module(repo / "api-test", PROFILE)


def edit(repo, rel, old, new):
    p = repo / "api-test/src/main/resources" / rel
    p.write_text(p.read_text().replace(old, new))


# --- testcase conventions --------------------------------------------------
def test_clean_module_passes(repo):
    assert testcase.run(module(repo)).status == "PASS"


def test_unresolvable_id_token_blocks(repo):
    edit(repo, "demo/Get/Get.yml", "$ID:Create_All_Valid_Smoke_sid_appId$", "$ID:Create_Typo_sid_appId$")
    res = testcase.run(module(repo))
    assert res.status == "FAIL" and {f.rule_id for f in res.findings} == {"DEP-001"}


def test_duplicate_unique_identifier_blocks(repo):
    edit(repo, "demo/Get/Get.yml", "TC_demo_Get_01", "tc_DEMO_create_01")      # case-insensitive clash
    assert "NAME-004" in {f.rule_id for f in testcase.run(module(repo)).findings}


def test_bad_prefix_and_id_pattern_block(repo):
    edit(repo, "demo/Get/Get.yml", "Demo_Get_All_Valid_Smoke:", "Get_All_Valid_Smoke:")
    edit(repo, "demo/Get/Get.yml", "TC_demo_Get_01", "GET-01")
    ids = {f.rule_id for f in testcase.run(module(repo)).findings}
    assert {"NAME-001", "NAME-002"} <= ids


def test_broken_yaml_untouched_warns_touched_blocks(repo):
    bad = repo / "api-test/src/main/resources/demo/Old/Old.yml"
    bad.parent.mkdir(parents=True)
    bad.write_text("Old:\n   Demo_Old:\n\t  description: tab\n")
    git(repo, "-c", "user.name=T", "-c", "user.email=t@example.org", "add", ".")
    git(repo, "-c", "user.name=T", "-c", "user.email=t@example.org", "commit", "-qsm", "upstream broken file")
    edit(repo, "demo/Create/Create.yml", '"name": "a"', '"name": "b"')
    res = testcase.run(module(repo), scope="changed", base="HEAD")
    assert res.status == "PASS" and [f.severity for f in res.findings if f.rule_id == "YAML-PARSE"] == ["WARN"]
    assert expected_diff.run(module(repo), "HEAD").status == "PASS"
    bad.write_text("Old:\n   Demo_Old:\n\t  description: still broken, now touched\n")
    assert testcase.run(module(repo), scope="changed", base="HEAD").status == "FAIL"


def test_changed_scope_ignores_untouched_debt(repo):
    edit(repo, "demo/Get/Get.yml", "Demo_Get_All_Valid_Smoke:", "Get_All_Valid_Smoke:")
    git(repo, "-c", "user.name=T", "-c", "user.email=t@example.org", "commit", "-qam", "debt", "-s")
    edit(repo, "demo/Create/Create.yml", '"name": "a"', '"name": "b"')
    res = testcase.run(module(repo), scope="changed", base="HEAD")
    assert res.status == "PASS"           # pre-existing NAME-001 debt is not in scope


# --- expected-result diff (scenario: "Expected result changed") ------------
def test_no_change_passes(repo):
    assert expected_diff.run(module(repo), "HEAD").status == "PASS"


@pytest.mark.parametrize("old,new,rule", [
    ('"status": "ACTIVE"}', '"status": "INACTIVE"}', "EXP-001"),
    ('"status": "ACTIVE"}', '"status": "$IGNORE$"}', "EXP-004"),
    ("restMethod: post", "restMethod: post\n      checkErrorsOnlyInResponse: true", "EXP-003"),
    ("TC_demo_Create_01", "TC_demo_Create_99", "EXP-006"),
    ("outputTemplate: demo/Create/CreateResult", "outputTemplate: demo/Create/Other", "EXP-002"),
])
def test_weakening_changes_block(repo, old, new, rule):
    edit(repo, "demo/Create/Create.yml", old, new)
    res = expected_diff.run(module(repo), "HEAD")
    assert res.status == "FAIL" and rule in {f.rule_id for f in res.findings}


def test_case_removal_and_result_template_block(repo):
    (repo / "api-test/src/main/resources/demo/Get/Get.yml").write_text("Get:\n")
    (repo / "api-test/src/main/resources/demo/Create/CreateResult.hbs").write_text("{}")
    ids = {f.rule_id for f in expected_diff.run(module(repo), "HEAD").findings}
    assert {"EXP-005", "EXP-007"} <= ids


def test_approved_change_passes(repo, tmp_path):
    edit(repo, "demo/Create/Create.yml", '"status": "ACTIVE"}', '"status": "INACTIVE"}')
    appr = tmp_path / "approvals.yaml"
    appr.write_text(textwrap.dedent("""\
        approvals:
          - rule_id: EXP-001
            target: demo/Create/Create.yml::Demo_Create_All_Valid_Smoke_sid
            approver: po.lead
            reason: MOSIP-1 changes default status
            timestamp: 2026-10-27T10:00:00Z
        """))
    res = expected_diff.run(module(repo), "HEAD", str(appr))
    assert res.status == "PASS" and res.findings[0].approved_by == "po.lead"


def test_superseded_approval_does_not_count(repo, tmp_path):
    edit(repo, "demo/Create/Create.yml", '"status": "ACTIVE"}', '"status": "INACTIVE"}')
    appr = tmp_path / "approvals.yaml"
    appr.write_text(textwrap.dedent("""\
        approvals:
          - rule_id: EXP-001
            target: demo/Create/Create.yml::Demo_Create_All_Valid_Smoke_sid
            approver: po.lead
            reason: x
            status: "SUPERSEDED: wrong analysis"
            timestamp: 2026-10-27T10:00:00Z
        """))
    assert expected_diff.run(module(repo), "HEAD", str(appr)).status == "FAIL"


def test_wrong_paths_are_not_run_never_pass(repo):
    m = Module(repo / "api-test", {**PROFILE, "resources_root": "src/main/resources/demo/Create"})
    m.resources_git_prefix = lambda: "nowhere"          # simulate a base listing that finds nothing
    assert expected_diff.run(m, "HEAD").status == "NOT_RUN"


# --- DCO (scenario: "Missing DCO sign-off") --------------------------------
def test_dco(repo):
    assert dco.run(str(repo), "HEAD~0").status == "PASS"
    edit(repo, "demo/Create/Create.yml", '"a"', '"c"')
    git(repo, "-c", "user.name=T", "-c", "user.email=t@example.org", "commit", "-qam", "unsigned")
    res = dco.run(str(repo), "HEAD~1..HEAD")
    assert res.status == "FAIL" and res.findings[0].rule_id == "DCO-001"


# --- secrets (scenario: "Secret detected") ---------------------------------
def test_secret_blocks_and_is_never_echoed(tmp_path):
    secret = "Sup3rS3cretValue!"
    (tmp_path / "prereg.properties").write_text(f"keycloak_client_secret={secret}\nadmin_password=\nx=$TIMESTAMP$\n")
    res = secrets_scan.run([str(tmp_path)], str(tmp_path), use_gitleaks=False)
    assert res.status == "FAIL" and len(res.findings) == 1
    assert secret not in res.to_json() and secret not in res.to_text()


# --- manifest / state machine ----------------------------------------------
def base_manifest(**over):
    doc = {
        "task_id": "t1", "outcome": "IN_PROGRESS", "current_state": "FAILURE_TRIAGE",
        "scope": {"module": "prereg", "repositories": [], "approved_paths": ["api-test/**"]},
        "requirements": {"source_refs": [], "acceptance_criteria": [], "open_ambiguities": 0},
        "testcases": {"canonical_format": "existing-api-testrig-yaml"},
        "repair": {"attempt_count": 0, "attempt_history": [], "classification": "AUTOMATION"},
        "reviews": {"implementation_review": "PASS", "fresh_context_review": "PASS"},
        "gates": [{"gate": g, "status": "PASS"} for g in ("testcase_readiness", "plan_readiness", "repo_ready")],
        "tracker": {"system": "jira", "key": "MOSIP-1", "confirmed_by": "mohan", "closure": {"status": "declined", "reason": "r"},
                    "subtasks": [{"kind": k, "key": f"MOSIP-{i}", "closure": {"status": "declined", "reason": "r"}}
                                 for i, k in enumerate(("testcase", "automation", "implementation", "testing", "review", "pr"), 2)]},
        "approvals": [], "evidence_refs": [], "github": {"issue": None, "subtasks": [], "pull_request": None},
        "test_plan": {"environment": "released", "environment_kind": "branch-deployed-env"},
    }
    doc.update(over)
    return doc


def write(tmp_path, doc):
    p = tmp_path / "task-manifest.json"
    p.write_text(json.dumps(doc))
    return str(p)


def attempts(n):
    return [{"attempt": i, "hypothesis": "h", "changed_files": ["a.yml"], "outcome": "STILL_FAILING"} for i in range(1, n + 1)]


def test_repair_allowed_below_cap(tmp_path):
    doc = base_manifest(repair={"attempt_count": 2, "attempt_history": attempts(2), "classification": "AUTOMATION"})
    assert manifest.transition(write(tmp_path, doc), "IMPLEMENTATION").status == "PASS"


def test_fourth_attempt_is_refused(tmp_path):
    """Scenario: three unsuccessful automation fixes -> no 4th attempt, escalation only."""
    doc = base_manifest(repair={"attempt_count": 3, "attempt_history": attempts(3), "classification": "AUTOMATION"})
    path = write(tmp_path, doc)
    res = manifest.transition(path, "IMPLEMENTATION")
    assert res.status == "FAIL" and {"TRN-002", "TRN-003"} <= {f.rule_id for f in res.findings}
    assert manifest.transition(path, "S_ESCALATE").status == "PASS"


def test_application_defect_escalates_immediately(tmp_path):
    doc = base_manifest(repair={"attempt_count": 0, "attempt_history": [], "classification": "APPLICATION"})
    res = manifest.transition(write(tmp_path, doc), "IMPLEMENTATION")
    assert "TRN-003" in {f.rule_id for f in res.findings}


def test_no_pr_without_gates_and_approval(tmp_path):
    doc = base_manifest(current_state="QUALITY_GATE", repair={"attempt_count": 0, "attempt_history": []})
    res = manifest.transition(write(tmp_path, doc), "COMMIT_PR")
    msgs = " ".join(f.message for f in res.findings)
    assert res.status == "FAIL" and "quality_gate" in msgs and "commit_pr" in msgs


def test_push_needs_dco_on_real_commit_and_approval(tmp_path):
    doc = base_manifest(current_state="COMMIT_PR", repair={"attempt_count": 0, "attempt_history": []})
    res = manifest.transition(write(tmp_path, doc), "PR_CLOSEOUT")
    msgs = " ".join(f.message for f in res.findings)
    assert res.status == "FAIL" and "gate dco" in msgs and "push_and_pr" in msgs and "gate branch_scope" in msgs
    doc["gates"].append({"gate": "dco", "status": "PASS"})
    doc["gates"].append({"gate": "branch_scope", "status": "PASS"})
    doc["gates"].append({"gate": "change_scope", "status": "PASS"})
    doc["approvals"].append({"action": "push_and_pr", "approver": "u", "reason": "r", "scope": "s", "timestamp": "t"})
    assert manifest.transition(write(tmp_path, doc), "PR_CLOSEOUT").status == "PASS"


def test_illegal_edge_and_success_with_failed_gate(tmp_path):
    doc = base_manifest(current_state="IMPLEMENTATION", outcome="SUCCESS",
                        gates=[{"gate": "deterministic_validation", "status": "NOT_RUN"}])
    path = write(tmp_path, doc)
    ids = {f.rule_id for f in manifest.transition(path, "SYSTEM_TEST_EXECUTION").findings}
    assert {"TRN-001", "MAN-004"} <= ids


def test_apply_records_only_legal_transitions(tmp_path):
    doc = base_manifest(repair={"attempt_count": 3, "attempt_history": attempts(3), "classification": "AUTOMATION"})
    path = write(tmp_path, doc)
    assert manifest.apply(path, "IMPLEMENTATION").status == "FAIL"
    assert json.loads(Path(path).read_text())["current_state"] == "FAILURE_TRIAGE"   # unchanged
    assert manifest.apply(path, "S_ESCALATE", "cap reached").status == "PASS"
    after = json.loads(Path(path).read_text())
    assert after["current_state"] == "S_ESCALATE" and after["state_history"][-1]["from"] == "FAILURE_TRIAGE"
    assert any(m.get("label") == "FAILURE_TRIAGE->S_ESCALATE" for m in after.get("usage_marks") or [])


def test_escalation_exit_needs_human_decision(tmp_path):
    doc = base_manifest(current_state="S_ESCALATE", resume_state="IMPLEMENTATION")
    path = write(tmp_path, doc)
    res = manifest.transition(path, "IMPLEMENTATION")
    assert res.status == "FAIL" and "TRN-002" in {f.rule_id for f in res.findings}
    doc["approvals"].append({"action": "escalation_decision", "approver": "po", "reason": "repair approved",
                             "scope": "F2", "timestamp": "2026-10-28T00:00:00Z"})
    path = write(tmp_path, doc)
    assert manifest.transition(path, "IMPLEMENTATION").status == "PASS"
    assert manifest.transition(path, "SYSTEM_TEST_EXECUTION").status == "FAIL"      # only to resume_state


def _commit(repo, name, msg, signoff=True):
    (repo / name).write_text(msg)
    git(repo, "add", name)
    git(repo, "-c", "user.name=T", "-c", "user.email=t@example.org", "commit", "-q", "-m", msg, *(["-s"] if signoff else []))
    return git(repo, "rev-parse", "HEAD").strip()


def test_branch_of_handles_ref_forms():
    from crewline.branch import branch_of
    assert branch_of("upstream/develop") == "develop"
    assert branch_of("refs/remotes/upstream/develop") == "develop"
    assert branch_of("refs/remotes/upstream/release/1.2") == "release/1.2"


def test_branch_scope_fork_model(tmp_path):
    """PILOT-001 lesson: branch from the upstream base, carry only task commits, PR to upstream."""
    from crewline import branch
    up = tmp_path / "upstream"; up.mkdir(); git(up, "init", "-q")
    base = _commit(up, "a.txt", "upstream base")
    fork = tmp_path / "fork"
    git(tmp_path, "clone", "-q", str(up), str(fork))
    _commit(fork, "local.txt", "local-only coverage work")             # exists only in the fork/local
    local_tip = git(fork, "rev-parse", "HEAD").strip()
    good = str(fork)
    git(fork, "checkout", "-q", "-b", "task-ok", "origin/HEAD")
    _commit(fork, "t.txt", "MOSIP-1 : task change")
    ok = branch.run(good, "origin/HEAD", base, "MOSIP-1", upstream_repo="mosip/x", pr_repo="mosip/x", pr_base="HEAD")
    assert ok.status == "PASS", ok.to_text()

    git(fork, "checkout", "-q", "-b", "task-bad", local_tip)                # the PILOT-001 mistake
    _commit(fork, "t2.txt", "MOSIP-1 : task change")
    bad = branch.run(good, "origin/HEAD", base, "MOSIP-1")
    assert bad.status == "FAIL" and "BR-002" in {f.rule_id for f in bad.findings}
    assert branch.run(good, "origin/HEAD", base, "MOSIP-1", allow=[local_tip[:10]]).status == "PASS"   # only if asked

    wrong_target = branch.run(good, "origin/HEAD", base, "MOSIP-1", upstream_repo="mosip/x",
                              pr_repo="me/x", pr_base="develop")
    assert "BR-005" in {f.rule_id for f in wrong_target.findings}


def test_repo_ready_needs_user_chosen_base_branch(tmp_path):
    doc = base_manifest(current_state="GITHUB_PLANNING", repair={"attempt_count": 0, "attempt_history": []})
    res = manifest.transition(write(tmp_path, doc), "REPO_READY")
    assert res.status == "FAIL" and "base_branch" in " ".join(f.message for f in res.findings)


def _report(path, groups):
    """Minimal Extent full-report: {(subject, status): [uids]}."""
    body = "".join(f"<h3>{s} — {st}</h3>" + "".join(f"<td>{u}</td><td>desc</td>" for u in uids)
                   for (s, st), uids in groups.items())
    path.mkdir(parents=True, exist_ok=True)
    (path / "mosip-x-full-report_T-1.html").write_text(f"<html><body>{body}</body></html>", encoding="utf-8")
    return str(path)


def test_full_regression_blocks_only_new_breaks(tmp_path):
    from crewline import regression
    base = _report(tmp_path / "base", {("A", "Passed"): ["TC_x_A_01", "TC_x_A_02", "TC_x_A_03"],
                                       ("A", "Failed"): ["TC_x_A_04"]})
    same = _report(tmp_path / "same", {("A", "Passed"): ["TC_x_A_01", "TC_x_A_02", "TC_x_A_03", "TC_x_A_05"],
                                       ("A", "Failed"): ["TC_x_A_04"]})
    ok = regression.run(base, same)
    assert ok.status == "PASS" and "REG-005" in {f.rule_id for f in ok.findings}        # pre-existing failure is not ours
    broke = _report(tmp_path / "broke", {("A", "Passed"): ["TC_x_A_01", "TC_x_A_03"],
                                         ("A", "Failed"): ["TC_x_A_02", "TC_x_A_04", "TC_x_A_06"]})
    res = regression.run(base, broke)
    assert res.status == "FAIL" and {"REG-001", "REG-002"} <= {f.rule_id for f in res.findings}
    assert regression.run(base, broke, ["TC_x_A_02", "TC_x_A_06"]).status == "PASS"     # attributed exceptions only
    assert regression.run(base, str(tmp_path / "nothing-here")).status == "NOT_RUN"


def test_commit_needs_full_regression(tmp_path):
    doc = base_manifest(current_state="QUALITY_GATE", repair={"attempt_count": 0, "attempt_history": []})
    res = manifest.transition(write(tmp_path, doc), "COMMIT_PR")
    assert "gate full_regression" in " ".join(f.message for f in res.findings)


def test_approval_is_single_use(tmp_path):
    doc = base_manifest(current_state="COMMIT_PR", repair={"attempt_count": 0, "attempt_history": []},
                        gates=[{"gate": g, "status": "PASS"} for g in ("dco", "branch_scope", "change_scope")])
    doc["approvals"].append({"action": "push_and_pr", "approver": "u", "reason": "PR #1", "scope": "fork", "timestamp": "t"})
    path = write(tmp_path, doc)
    assert manifest.apply(path, "PR_CLOSEOUT").status == "PASS"
    after = json.loads(Path(path).read_text())
    assert after["approvals"][0]["consumed_by"].startswith("COMMIT_PR->PR_CLOSEOUT")
    after["current_state"] = "COMMIT_PR"                       # a second push/PR attempt
    Path(path).write_text(json.dumps(after))
    assert manifest.transition(path, "PR_CLOSEOUT").status == "FAIL"


def test_scope_targeted_for_added_cases_full_otherwise(repo):
    from crewline import scope
    p = repo / "api-test/src/main/resources/demo/Get/Get.yml"
    p.write_text(p.read_text() + "".join(f"{line}\n" for line in [
        "   Demo_Get_NoAuth_Neg:",
        "      endPoint: /v1/get",
        "      uniqueIdentifier: TC_demo_Get_02",
        "      role: noauth",
        "      restMethod: get",
        "      inputTemplate: demo/Get/Get",
        "      outputTemplate: demo/Get/GetResult",
        "      input: '{\"appId\": \"$ID:Create_All_Valid_Smoke_sid_appId$\"}'",
        "      output: '{\"responseCode\": \"401\"}'"]))
    res = scope.run(module(repo), "HEAD")
    assert res.inputs["required_scope"] == "TARGETED"
    assert res.inputs["run_cases"] == ["TC_demo_Create_01", "TC_demo_Get_02"]      # new case + its producer
    edit(repo, "demo/Create/Create.yml", '"status": "ACTIVE"', '"status": "NEW"')
    assert scope.run(module(repo), "HEAD").inputs["required_scope"] == "FULL"


def test_tools_manifest_has_enable_steps_and_checker_runs(monkeypatch):
    monkeypatch.delenv("CREWLINE_PACK", raising=False)
    from crewline import pack, tools
    spec = pack.merged_tools()["capabilities"]
    assert all(c.get("enable") and c.get("check") for c in spec.values())
    res = tools.run(["git", "browser_upload"])
    assert res.status in ("PASS", "FAIL") and {f.rule_id for f in res.findings} <= {"TOOL-000", "TOOL-001", "TOOL-002"}
    assert any(f.rule_id == "TOOL-000" and "Claude in Chrome" in f.remediation for f in res.findings)


def test_linked_change_module_plus_commons(tmp_path):
    """A module change that needs an apitest-commons change: per-repo approvals, gates, PR order, version link."""
    repos = [
        {"name": "pre-registration", "path": "p", "role": "primary", "upstream_repo": "mosip/pre-registration",
         "branch": "sdet/X", "depends_on": ["mosip-functional-tests"], "pull_request": "https://x/pr/2"},
        {"name": "mosip-functional-tests", "path": "c", "role": "dependency", "upstream_repo": "mosip/mosip-functional-tests",
         "branch": "sdet/X-commons"}]
    doc = base_manifest(current_state="COMMIT_PR", repair={"attempt_count": 0, "attempt_history": []},
                        scope={"module": "prereg", "repositories": repos},
                        gates=[{"gate": g, "status": "PASS"} for g in ("dco", "branch_scope", "change_scope")])
    doc["approvals"] += [{"action": "base_branch", "approver": "u", "reason": "r", "scope": "mosip/pre-registration develop", "timestamp": "t"},
                         {"action": "push_and_pr", "approver": "u", "reason": "r", "scope": "s", "timestamp": "t"}]
    ids = {f.rule_id for f in manifest.transition(write(tmp_path, doc), "PR_CLOSEOUT").findings}
    assert {"MAN-006", "MAN-007", "MAN-008", "MAN-009"} <= ids          # commons not approved/gated/PR'd/linked
    doc["approvals"].append({"action": "base_branch", "approver": "u", "reason": "r", "scope": "mosip/mosip-functional-tests develop", "timestamp": "t"})
    repos[1].update(version_link="pom apitest-commons 1.6.0 -> 1.6.1-SNAPSHOT", pull_request="https://x/pr/1")
    doc["gates"] += [{"gate": f"{g}:{n}", "status": "PASS"} for n in ("pre-registration", "mosip-functional-tests") for g in ("branch_scope", "dco")]
    assert manifest.transition(write(tmp_path, doc), "PR_CLOSEOUT").status == "PASS"


def test_attempt_counter_must_match_history(tmp_path):
    doc = base_manifest(repair={"attempt_count": 1, "attempt_history": attempts(2)})
    assert "MAN-003" in {f.rule_id for f in manifest.check(write(tmp_path, doc)).findings}
