"""Preflight and record commands (#117), non-Java defaults (#118), threat review (#119), standards (#120), CI gaps and waiver follow-ups (#121)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from crewline import ci_coverage, coding_standards, diff_coverage, discover, evidence, manifest, records, requirement_clarity, requirement_fit
from crewline.coding_standards import is_test_path
from crewline.task import skeleton

HUMAN = {"approver": "mohan", "reason": "r", "scope": "s", "timestamp": "2026-11-04T00:00:00Z"}
AC = "POST /x with a missing name returns 400 and error MOSIP-X"


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@example.org", *args],
                          check=True, capture_output=True, text=True).stdout


def ids(res):
    return {f.rule_id for f in res.findings}


def task(tmp_path, **over):
    d = skeleton("T", over.pop("task_type", "feature"))
    d["tracker"] = {"system": "jira", "key": "MOSIP-1", "confirmed_by": "mohan", "closure": {"status": "declined", "reason": "r"},
                    "subtasks": [{"kind": k, "key": f"MOSIP-{i}", "closure": {"status": "declined", "reason": "r"}}
                                 for i, k in enumerate(("testcase", "automation", "implementation", "testing", "review", "pr"), 2)]}
    d["requirements"] = {"source_refs": ["MOSIP-1"], "acceptance_criteria": [{"id": "AC-1", "text": AC}], "open_ambiguities": 0}
    d.update(over)
    p = tmp_path / "task-manifest.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    return p


@pytest.fixture
def py_repo(tmp_path):
    r = tmp_path / "pixelpass"
    git(tmp_path, "init", "-q", str(r))
    (r / "python" / "src" / "pp").mkdir(parents=True)
    (r / "python" / "tests").mkdir()
    (r / "python" / "src" / "pp" / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    git(r, "add", "-A"); git(r, "commit", "-q", "-s", "-m", "base")
    return r


# --- #118 ---------------------------------------------------------------------------------------------------------
def test_python_paths_count_as_tests():
    for p in ("tests/test_a.py", "python/tests/test_a.py", "python/test_b.py", "pkg/a_test.py", "python/tests/conftest.py", "x/a_test.go", "src/test/kotlin/ATest.kt"):
        assert is_test_path(p), p
    for p in ("python/src/pp/a.py", "src/main/java/A.java", "latest/x.py", "src/contest.py"):
        assert not is_test_path(p), p


def cobertura(path, filename, covered=True):
    path.write_text(f'<coverage><packages><package><classes><class filename="{filename}"><lines>'
                    f'<line number="2" hits="{1 if covered else 0}"/><line number="3" hits="1"/>'
                    '</lines></class></classes></package></packages></coverage>', encoding="utf-8")


def test_diff_coverage_measures_python_with_no_flags(py_repo, tmp_path):
    (py_repo / "python" / "src" / "pp" / "a.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    (py_repo / "python" / "tests" / "test_a.py").write_text("def test_f():\n    pass\n", encoding="utf-8")
    report = tmp_path / "coverage.xml"
    cobertura(report, "pp/a.py")                                             # relative to coverage's source root
    res = diff_coverage.run(str(py_repo), "HEAD", str(report))
    assert res.status == "PASS" and res.inputs["changed_executable_lines"] == 1 and "100.0%" in res.summary
    assert "python/tests/test_a.py" not in res.inputs["files"]               # tests are not production lines
    cobertura(report, "pp/a.py", covered=False)
    assert "DCV-001" in ids(diff_coverage.run(str(py_repo), "HEAD", str(report)))
    cobertura(report, "pp/other.py")
    assert "DCV-002" in ids(diff_coverage.run(str(py_repo), "HEAD", str(report)))   # 0/0 on PP-PYSDK; now a block


def test_zero_lines_measured_is_never_a_silent_pass(py_repo, tmp_path):
    report = tmp_path / "coverage.xml"
    cobertura(report, "pp/a.py")
    (py_repo / "python" / "src" / "pp" / "a.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    bad = diff_coverage.run(str(py_repo), "HEAD", str(report), include="*src/main/java/*.java")
    assert bad.status == "FAIL" and "DCV-005" in ids(bad)                    # the old default, on this diff
    git(py_repo, "checkout", "-q", "--", ".")
    (py_repo / "README.md").write_text("docs\n", encoding="utf-8")
    only_docs = diff_coverage.run(str(py_repo), "HEAD", str(report))
    assert only_docs.status == "PASS" and "DCV-005" in ids(only_docs) and "nothing measured" in only_docs.summary
    (py_repo / "python" / "src" / "pp" / "a.py").write_text("# comment\ndef f():\n    return 1\n", encoding="utf-8")
    report.write_text('<coverage><packages><package><classes><class filename="pp/a.py"><lines><line number="9" hits="1"/>'
                      '</lines></class></classes></package></packages></coverage>', encoding="utf-8")
    assert "DCV-005" in ids(diff_coverage.run(str(py_repo), "HEAD", str(report)))


def test_requirement_fit_sees_python_tests(py_repo, tmp_path):
    (py_repo / "python" / "src" / "pp" / "a.py").write_text('E = "MOSIP-X"  # 400\n', encoding="utf-8")
    m = task(tmp_path, requirements={"source_refs": [], "open_ambiguities": 0, "acceptance_criteria": [
        {"id": "AC-1", "text": "POST /x returns 400 and error MOSIP-X", "code_refs": ["python/src/pp/a.py"]}]})
    assert "RFT-005" in ids(requirement_fit.run(str(py_repo), "HEAD", str(m)))
    (py_repo / "python" / "tests" / "test_a.py").write_text('def test_a():\n    assert "MOSIP-X" and 400 and "POST /x"\n', encoding="utf-8")
    res = requirement_fit.run(str(py_repo), "HEAD", str(m))
    assert "RFT-005" not in ids(res), res.to_text()


# --- #117 ---------------------------------------------------------------------------------------------------------
def test_next_lists_every_precondition_without_a_rejected_attempt(tmp_path):
    m = task(tmp_path, current_state="IMPLEMENTATION_REVIEW",
             reviews={"implementation_review": "WAIVED", "fresh_context_review": "NOT_STARTED"},
             gates=[{"gate": "plan_readiness", "status": "PASS"}, {"gate": "repo_ready", "status": "PASS"}],
             scope={"module": "m", "repositories": [], "approved_paths": ["src/**"]},
             test_plan={"environment": "local", "environment_kind": "local-stack"})
    res = manifest.next_report(str(m))
    assert res.status == "PASS"                                              # a report, not a refusal
    text = res.to_text()
    assert "IMPLEMENTATION_REVIEW -> FRESH_CONTEXT_REVIEW: not ready" in text
    assert "NOT reviews.implementation_review eq 'PASS'" in text             # WAIVED is not accepted: said up front
    assert "NOT gate deterministic_validation eq 'PASS'" in text
    assert "NOT packets/FRESH_CONTEXT_REVIEW.md" in text
    assert "IMPLEMENTATION_REVIEW -> IMPLEMENTATION: READY" in text and res.inputs["ready"] == ["IMPLEMENTATION"]
    assert manifest.transition(str(m), "IMPLEMENTATION").status == "PASS"    # what it calls ready really passes


def test_next_says_ok_for_what_holds_and_reports_forced_escalation(tmp_path):
    m = task(tmp_path, current_state="INPUT_VALIDATION", gates=[{"gate": "input_readiness", "status": "PASS"}])
    text = manifest.next_report(str(m)).to_text()
    assert "ok  gate input_readiness eq 'PASS'" in text and "ok  tracker ticket or waiver" in text and ": READY" in text
    n = task(tmp_path, current_state="FAILURE_TRIAGE", repair={"attempt_count": 0, "dev_attempt_count": 3, "classification": "CODE_UNDER_CHANGE",
             "attempt_history": [{"attempt": i, "track": "dev", "hypothesis": "h", "changed_files": [], "outcome": "o"} for i in (1, 2, 3)]})
    res = manifest.next_report(str(n))
    assert "only S_ESCALATE" in res.to_text() and res.inputs["ready"] == []


def test_status_never_offers_a_fourth_repair(tmp_path):
    m = task(tmp_path, current_state="FAILURE_TRIAGE", approvals=[{"action": "design", **HUMAN}, {"action": "x", **HUMAN, "consumed_by": "y"}],
             repair={"attempt_count": 1, "dev_attempt_count": 3, "classification": "CODE_UNDER_CHANGE", "attempt_history": []})
    text = records.status(str(m)).to_text()
    assert "SDET 1 used, 2 left" in text and "developer 3 used, 0 left" in text
    assert "S_ESCALATE only (forced)" in text and "do not offer another developer repair" in text
    assert "open approvals: design" in text and "x" not in text.split("open approvals:")[1].split("\n")[0]


def test_review_record_refuses_what_the_schema_or_independence_forbid(tmp_path):
    m = task(tmp_path, implementation={"authors": ["dev-1"]})
    before = m.read_text(encoding="utf-8")
    res = records.review_record(str(m), "implementation_review", "FAIL", "rev-1")
    assert "REC-001" in ids(res) and "FINDINGS" in res.to_text()
    assert "MAN-011" in ids(records.review_record(str(m), "implementation_review", "PASS", "dev-1"))
    assert "REC-001" in ids(records.review_record(str(m), "nope", "PASS", "rev-1"))
    assert "REC-001" in ids(records.review_record(str(m), "implementation_review", "PASS"))
    assert m.read_text(encoding="utf-8") == before                           # refused means nothing written
    assert records.review_record(str(m), "implementation_review", "PASS", "rev-1").status == "PASS"
    d = json.loads(m.read_text(encoding="utf-8"))
    assert d["reviews"]["implementation_review"] == "PASS" and d["reviews"]["reviewers"] == {"implementation_review": "rev-1"}
    assert "MAN-011" in ids(records.review_record(str(m), "fresh_context_review", "PASS", "rev-1"))
    assert manifest.check(str(m)).status == "PASS"


def test_approval_and_limitation_records(tmp_path):
    m = task(tmp_path)
    assert "MAN-010" in ids(records.approval_add(str(m), "commit_pr", "orchestrator", "r", "s"))
    assert "REC-001" in ids(records.approval_add(str(m), "gate_exception", "mohan", "r", "s", exception=True))
    assert records.approval_add(str(m), "commit_pr", "mohan", "said yes", "this commit").status == "PASS"
    assert records.approval_add(str(m), "gate_exception", "mohan", "r", "s", gate="diff_coverage", exception=True, follow_up="MOSIP-9").status == "PASS"
    assert "REC-001" in ids(records.limitation_add(str(m), "KL-1"))
    first = records.limitation_add(str(m), "KL-1", "deep nesting fails", ["a.py:3"])
    assert first.status == "PASS" and "MAN-019" in first.to_text()
    assert records.limitation_add(str(m), "KL-1", follow_up="MOSIP-10").status == "PASS"
    d = json.loads(m.read_text(encoding="utf-8"))
    assert d["approvals"][0]["approver_type"] == "human" and d["approvals"][0]["timestamp"]
    assert d["exceptions"][0]["follow_up"] == "MOSIP-9"
    assert d["known_limitations"] == [{"id": "KL-1", "text": "deep nesting fails", "refs": ["a.py:3"], "follow_up": "MOSIP-10"}]
    assert manifest.check(str(m)).status == "PASS"


# --- #119 ---------------------------------------------------------------------------------------------------------
def test_untrusted_input_needs_limit_acs(tmp_path):
    m = task(tmp_path, requirements={"source_refs": ["K"], "open_ambiguities": 0, "acceptance_criteria": [
        {"id": "AC-1", "text": "Decoding a CBOR QR payload returns the mapped identity data"}]})
    res = requirement_clarity.run(str(m))
    assert "REQ-006" in ids(res) and "untrusted_input" in res.to_text()     # asked, not guessed
    d = json.loads(m.read_text(encoding="utf-8"))
    d["requirements"]["untrusted_input"] = False
    m.write_text(json.dumps(d), encoding="utf-8")
    assert requirement_clarity.run(str(m)).status == "PASS"
    d["requirements"]["untrusted_input"] = True
    m.write_text(json.dumps(d), encoding="utf-8")
    res = requirement_clarity.run(str(m))
    assert "REQ-005" in ids(res) and "nesting_depth" in res.to_text()
    d["requirements"]["acceptance_criteria"].append({"id": "AC-2", "nfr": True, "text": "A payload past 1 MB or 64 levels deep returns a typed error within 1 s using under 50 MB",
                                                     "resource_limits": ["input_size", "nesting_depth", "memory", "time", "error_behaviour"]})
    m.write_text(json.dumps(d), encoding="utf-8")
    assert requirement_clarity.run(str(m)).status == "PASS"
    assert manifest.check(str(m)).status == "PASS"


def test_a_plain_api_ac_is_not_asked_about_untrusted_input(tmp_path):
    assert "REQ-006" not in ids(requirement_clarity.run(str(task(tmp_path))))


def test_threat_review_comes_before_test_design(tmp_path):
    gates = [{"gate": g, "status": "PASS"} for g in ("requirement_clarity", "acceptance_readiness", "design_readiness", "testcase_readiness")]
    req = {"source_refs": ["K"], "open_ambiguities": 0, "untrusted_input": True, "acceptance_criteria": []}
    m = task(tmp_path, current_state="DESIGN", requirements=req, gates=gates)
    (tmp_path / "packets").mkdir()
    (tmp_path / "packets" / "DESIGN.md").write_text("options and recommendation\n", encoding="utf-8")
    res = manifest.transition(str(m), "TESTCASE_DESIGN")
    assert "PKT-005" in ids(res)
    packet = tmp_path / "packets" / "THREAT_REVIEW.md"
    packet.write_text("agent: software-developer\ncontext_id: c1\n", encoding="utf-8")
    assert "PKT-005" in ids(manifest.transition(str(m), "TESTCASE_DESIGN"))  # only the security-engineer's counts
    packet.write_text("agent: security-engineer\ncontext_id: sec-1\nfindings: depth limit\n", encoding="utf-8")
    assert "PKT-005" not in ids(manifest.transition(str(m), "TESTCASE_DESIGN"))
    packet.unlink()
    d = json.loads(m.read_text(encoding="utf-8"))
    d["requirements"]["untrusted_input"] = False
    m.write_text(json.dumps(d), encoding="utf-8")
    assert "PKT-005" not in ids(manifest.transition(str(m), "TESTCASE_DESIGN"))


# --- #120 ---------------------------------------------------------------------------------------------------------
@pytest.fixture
def linted(tmp_path):
    r = tmp_path / "ui"
    git(tmp_path, "init", "-q", str(r))
    (r / "package.json").write_text(json.dumps({"scripts": {"lint": "eslint ."}}), encoding="utf-8")
    (r / "src").mkdir()
    (r / "src" / "a.js").write_text("var a = 1\nvar b = 2\nvar c = 3\n", encoding="utf-8")
    git(r, "add", "-A"); git(r, "commit", "-q", "-s", "-m", "base")
    return r


def lint_record(repo, tmp_path, output):
    script = tmp_path / "lint.py"
    script.write_text(f"import sys\nprint({output!r})\nsys.exit(1)\n", encoding="utf-8")
    rec = tmp_path / "lint.json"
    evidence.capture(str(repo), str(rec), [sys.executable, str(script), "npm", "run", "lint"])
    return str(rec)


def test_changed_lines_mode_blocks_only_new_violations(linted, tmp_path):
    (linted / "src" / "a.js").write_text("var a = 1\nvar b = 22\nvar c = 3\n", encoding="utf-8")
    m = task(tmp_path)
    old = lint_record(linted, tmp_path, "src/a.js:3:1: existing")
    assert "STD-002" in ids(coding_standards.run(str(linted), "HEAD", str(m), [old]))   # default: unchanged, any failure blocks
    res = coding_standards.run(str(linted), "HEAD", str(m), [old], changed_lines=True)
    assert res.status == "PASS" and "STD-007" in ids(res)
    new = lint_record(linted, tmp_path, f"{(linted / 'src' / 'a.js').as_posix()}:2:1: new one\nsrc/a.js:3:1: existing")
    res = coding_standards.run(str(linted), "HEAD", str(m), [new], changed_lines=True)
    assert res.status == "FAIL" and "STD-006" in ids(res) and "src/a.js:2" in res.to_text()
    unmapped = lint_record(linted, tmp_path, "5 problems")
    assert "STD-002" in ids(coding_standards.run(str(linted), "HEAD", str(m), [unmapped], changed_lines=True))  # cannot map: still blocks


def test_changed_lines_mode_from_module_yaml(linted, tmp_path):
    (linted / ".crewline").mkdir()
    (linted / ".crewline" / "module.yaml").write_text("module: ui\nstandards: {mode: changed-lines}\n", encoding="utf-8")
    (linted / "src" / "a.js").write_text("var a = 1\nvar b = 22\nvar c = 3\n", encoding="utf-8")
    rec = lint_record(linted, tmp_path, "src/a.js:3:1: existing")
    assert coding_standards.run(str(linted), "HEAD", str(task(tmp_path)), [rec]).status == "PASS"


def test_no_checker_says_so_and_onboarding_proposes_one(py_repo, tmp_path):
    res = coding_standards.run(str(py_repo), "HEAD", str(task(tmp_path)))
    assert "no repo standard; only debug leftovers checked" in res.to_text()
    (py_repo / "python" / "pyproject.toml").write_text("[project]\nname='pp'\n", encoding="utf-8")
    git(py_repo, "add", "-A"); git(py_repo, "commit", "-q", "-s", "-m", "p")
    out = tmp_path / "drafts"
    found = discover.run(str(py_repo), "pixelpass", str(out))
    assert "DSC-010" in ids(found)
    text = (out / ".crewline" / "development" / "standards-proposal.md").read_text(encoding="utf-8")
    assert "ruff" in text and "changed-lines" in text and "Maven" not in text
    assert not (py_repo / ".crewline").exists()                              # a draft, never written into the repo


# --- #121 ---------------------------------------------------------------------------------------------------------
def final_task(tmp_path, repo=None, **over):
    scope = {"module": "m", "approved_paths": ["**"], "repositories": [{"name": "m", "role": "primary", "path": str(repo)}] if repo else []}
    return task(tmp_path, current_state="KNOWLEDGE_UPDATE", scope=scope, **over)


def test_waived_findings_need_a_follow_up_before_final_report(tmp_path):
    m = final_task(tmp_path, known_limitations=[{"id": "KL-1", "text": "t"}, {"id": "KL-2", "text": "t", "follow_up": "MOSIP-5"},
                                                {"id": "KL-3", "text": "t", "no_follow_up": "accepted: tool is internal"}],
                   exceptions=[{"action": "gate_exception", "gate": "diff_coverage", **HUMAN}],
                   approvals=[{"action": "gate_exception", "gate": "token_budget", **HUMAN},
                              {"action": "gate_exception", "gate": "secrets", **HUMAN, "follow_up": "MOSIP-6"}])
    res = manifest.transition(str(m), "FINAL_REPORT")
    msgs = [f.message for f in res.findings if f.rule_id == "MAN-019"]
    assert len(msgs) == 2 and "KL-1" in msgs[0] and "diff_coverage" in msgs[1]   # token budget is not a finding that ships
    d = json.loads(m.read_text(encoding="utf-8"))
    d["known_limitations"][0]["follow_up"] = "MOSIP-7"
    d["exceptions"][0]["no_follow_up"] = "accepted"
    m.write_text(json.dumps(d), encoding="utf-8")
    assert "MAN-019" not in ids(manifest.transition(str(m), "FINAL_REPORT"))
    assert "MAN-019" not in ids(manifest.transition(str(task(tmp_path, current_state="IMPACT_ANALYSIS",
                                                             known_limitations=[{"id": "K", "text": "t"}])), "DESIGN"))  # only at the end


def record_suite(tmp_path, repo, name, command, cwd=None):
    (tmp_path / "evidence").mkdir(exist_ok=True)
    (tmp_path / "evidence" / name).write_text(json.dumps({"command": command, "cwd": str(cwd or repo)}), encoding="utf-8")


def workflow(repo, text):
    (repo / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    (repo / ".github" / "workflows" / "ci.yml").write_text(text, encoding="utf-8")


def test_a_suite_no_ci_runs_is_refused_unless_accepted(py_repo, tmp_path):
    m = final_task(tmp_path, py_repo)
    assert "CIC-003" in ids(ci_coverage.run(str(m)))
    record_suite(tmp_path, py_repo, "unit-py313.json", "python -m pytest -q", py_repo / "python")
    record_suite(tmp_path, py_repo, "system-tests-wheel.json", "python -m pytest systest", py_repo / "python")
    record_suite(tmp_path, py_repo, "dependency-security.json", "pip-audit --strict")
    record_suite(tmp_path, py_repo, "base-run.json", "npm test", tmp_path)   # a run outside the repository is no suite of it
    res = ci_coverage.run(str(m))
    assert res.status == "FAIL" and "CIC-001" in ids(res) and "pytest in python/" in res.to_text()
    assert "CIC-001" in ids(manifest.transition(str(m), "FINAL_REPORT"))
    assert "CIC-001" not in ids(manifest.transition(str(m), "KNOWLEDGE_UPDATE"))

    workflow(py_repo, "jobs:\n  js:\n    steps:\n      - run: npm test\n        working-directory: js\n")
    assert "CIC-001" in ids(ci_coverage.run(str(m)))                         # CI exists, not for this suite
    workflow(py_repo, "jobs:\n  py:\n    steps:\n      - run: pytest -q\n        working-directory: python\n")
    assert ci_coverage.run(str(m)).status == "PASS"
    workflow(py_repo, "jobs:\n  py:\n    steps:\n      - run: pytest -q\n        working-directory: python-old\n")
    assert "CIC-001" in ids(ci_coverage.run(str(m)))                         # another folder is not this one

    d = json.loads(m.read_text(encoding="utf-8"))
    d["exceptions"] = [{"action": "gate_exception", "gate": "ci_coverage", **HUMAN, "follow_up": "MOSIP-8"}]
    m.write_text(json.dumps(d), encoding="utf-8")
    res = manifest.transition(str(m), "FINAL_REPORT")
    assert "CIC-001" not in {f.rule_id for f in res.findings if not f.approved_by} and "MAN-019" not in ids(res)


def test_system_suite_outside_ci_warns_and_a_task_with_no_code_is_not_asked(py_repo, tmp_path):
    m = final_task(tmp_path, py_repo)
    record_suite(tmp_path, py_repo, "system-tests.json", "mvn -B verify", py_repo)
    res = ci_coverage.run(str(m))
    assert res.status == "PASS" and "CIC-002" in ids(res)
    inc = task(tmp_path, task_type="incident", risk_tier="L0", current_state="KNOWLEDGE_UPDATE")
    assert "CIC-001" not in ids(manifest.transition(str(inc), "FINAL_REPORT"))
