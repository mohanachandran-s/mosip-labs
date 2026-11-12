"""Regression decisions (waive / fix / defer to a bug), kept run reports, run timeouts, guard false positives and
credential merging. Evidence: KM-COV-001 (keymanager coverage matrices, session 5ee71ce9) and the user's requests."""
import json
import os
import subprocess
import sys
from pathlib import Path

from crewline import __main__ as cli
from crewline import creds, evidence, guard, manifest, regression_decision
from crewline.result import Result

HUMAN = {"approver": "mohan", "reason": "r", "scope": "s", "timestamp": "2026-11-12T00:00:00Z"}


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def make_repo(path: Path, files: dict[str, str] | None = None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    for name, text in (files or {"README.md": "x\n"}).items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text, encoding="utf-8")
    git(path, "add", "-A")
    git(path, "-c", "user.name=T", "-c", "user.email=t@example.org", "commit", "-q", "-m", "c")
    return path


def doc(**over):
    d = {
        "task_id": "t", "task_type": "feature", "risk_tier": "L2", "outcome": "IN_PROGRESS", "current_state": "QUALITY_GATE",
        "scope": {"module": "svc", "repositories": [], "approved_paths": ["src/**"]},
        "requirements": {"source_refs": [], "acceptance_criteria": [], "open_ambiguities": 0},
        "testcases": {"canonical_format": "none"},
        "repair": {"attempt_count": 0, "dev_attempt_count": 0, "attempt_history": [], "classification": ""},
        "reviews": {"implementation_review": "PASS", "fresh_context_review": "PASS"},
        "tracker": {"system": "jira", "key": "MOSIP-1", "confirmed_by": "mohan",
                    "subtasks": [{"kind": k, "key": f"MOSIP-{i}"} for i, k in enumerate(
                        ("testcase", "automation", "implementation", "testing", "review", "pr"), 2)]},
        "gates": [], "approvals": [], "evidence_refs": [], "github": {"issue": None, "subtasks": [], "pull_request": None},
        "test_plan": {"environment": "http://127.0.0.1:8080", "environment_kind": "local-stack"},
    }
    d.update(over)
    return d


def write(tmp_path, d, name="task-manifest.json"):
    p = tmp_path / name
    p.write_text(json.dumps(d), encoding="utf-8")
    return str(p)


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def ids(res):
    return {f.rule_id for f in res.findings}


def _report(folder: Path, passed, failed=()):
    body = "<h3>A — Passed</h3>" + "".join(f"<td>{u}</td>" for u in passed)
    if failed:
        body += "<h3>A — Failed</h3>" + "".join(f"<td>{u}</td>" for u in failed)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "mosip-x-keymanager-eng-2026-11-11_14-44-full-report_T-1.html").write_text(
        f"<html><body>{body}</body></html>", encoding="utf-8")
    return str(folder)


# --- waive: no code change, or the user's explicit waiver ------------------------------------------------------------

def test_waive_without_code_change_is_recorded_as_no_code_change(tmp_path):
    repo = make_repo(tmp_path / "km", {"src/A.java": "class A {}\n"})
    base = git(repo, "rev-parse", "HEAD").strip()
    (repo / "api-test/src/main/resources/coverage").mkdir(parents=True)
    (repo / "api-test/src/main/resources/coverage/POST_x.md").write_text("| row |\n", encoding="utf-8")
    (repo / "README.md").write_text("docs\n", encoding="utf-8")
    m = write(tmp_path, doc())
    res = regression_decision.run(m, "waive", "mohan", "matrix-only change", str(repo), base)
    assert res.status == "PASS" and res.inputs["kind"] == "no_code_change"
    exc = load(m)["exceptions"][0]
    assert exc["gate"] == "full_regression" and exc["kind"] == "no_code_change" and exc["no_follow_up"]
    assert manifest.schema_errors(load(m)) == []
    d = load(m)
    d["gates"] = [{"gate": g, "status": "PASS"} for g in ("quality_gate", "secrets")]
    assert not any("full_regression" in f.message for f in manifest.transition(write(tmp_path, d), "COMMIT_PR").findings
                   if f.rule_id in ("REG-007", "TRN-002"))


def test_waive_with_code_change_is_a_user_waiver(tmp_path):
    repo = make_repo(tmp_path / "km", {"src/A.java": "class A {}\n"})
    base = git(repo, "rev-parse", "HEAD").strip()
    (repo / "src/A.java").write_text("class A { int x; }\n", encoding="utf-8")
    m = write(tmp_path, doc())
    res = regression_decision.run(m, "waive", "mohan", "user: skip, env down", str(repo), base, follow_up="MOSIP-9")
    assert res.inputs["kind"] == "user_waiver" and "RGD-004" in ids(res)
    assert load(m)["exceptions"][0]["follow_up"] == "MOSIP-9"


def test_decisions_need_a_human_and_a_reason(tmp_path):
    m = write(tmp_path, doc())
    assert "RGD-001" in ids(regression_decision.run(m, "waive", "orchestrator", "x"))
    assert "RGD-001" in ids(regression_decision.run(m, "fix", "mohan", ""))


# --- fix: one approval before the repair loop ---------------------------------------------------------------------

def test_repair_after_regression_breaks_needs_one_fix_approval(tmp_path):
    d = doc(gates=[{"gate": "full_regression", "status": "FAIL"}])
    m = write(tmp_path, d)
    assert "TRN-006" in ids(manifest.transition(m, "IMPLEMENTATION"))
    assert regression_decision.run(m, "fix", "mohan", "fix the KER-KMS-002 breaks").status == "PASS"
    assert "TRN-006" not in ids(manifest.transition(m, "IMPLEMENTATION"))
    d = load(m)
    d["gates"] = [{"gate": "full_regression", "status": "PASS"}]
    assert "TRN-006" not in ids(manifest.transition(write(tmp_path, d, "p.json"), "IMPLEMENTATION"))   # PASS: no question


def test_moving_into_repair_consumes_the_fix_approval(tmp_path, monkeypatch):
    m = write(tmp_path, doc(gates=[{"gate": "full_regression", "status": "FAIL"}]))
    regression_decision.run(m, "fix", "mohan", "fix")
    monkeypatch.setattr(manifest, "transition", lambda *_: Result("manifest-transition").finish())
    assert manifest.apply(m, "IMPLEMENTATION").status == "PASS"
    assert load(m)["approvals"][0]["consumed_by"].startswith("QUALITY_GATE->IMPLEMENTATION@")


# --- defer: raise a bug, one approval covers every break -----------------------------------------------------------

def test_defer_to_bug_covers_every_break_with_one_approval(tmp_path):
    base = _report(tmp_path / "base", ["TC_KM_A_01", "TC_KM_A_02", "TC_KM_A_03", "TC_KM_A_04"])
    branch = _report(tmp_path / "branch", ["TC_KM_A_01"], ["TC_KM_A_02", "TC_KM_A_03", "TC_KM_A_04"])
    out = tmp_path / "evidence" / "full-regression.json"
    assert cli.main(["regression", "--baseline", base, "--candidate", branch, "--out", str(out)]) == 1
    m = write(tmp_path, doc())
    assert "RGD-003" in ids(regression_decision.run(m, "defer", "mohan", "r", bug="MOSIP-77", record=str(out),
                                                    cls="CODE_UNDER_CHANGE"))
    assert "RGD-002" in ids(regression_decision.run(m, "defer", "mohan", "r", bug="not a key", record=str(out)))
    res = regression_decision.run(m, "defer", "mohan", "KER-KMS-002 on released; raised MOSIP-77", bug="MOSIP-77",
                                  record=str(out), cls="ENVIRONMENT")
    assert res.status == "PASS" and res.inputs["test_ids"] == ["TC_KM_A_02", "TC_KM_A_03", "TC_KM_A_04"]
    d = load(m)
    assert len(d["exceptions"]) == 1 and d["exceptions"][0]["bug"] == "MOSIP-77"           # one approval, not three
    assert d["defects"][0]["status"] == "raised" and d["defects"][0]["class"] == "ENVIRONMENT"
    assert manifest.schema_errors(d) == []
    assert cli.main(["regression", "--baseline", base, "--candidate", branch, "--manifest", m, "--out", str(out)]) == 0
    rescored = load(out)
    assert rescored["status"] == "PASS"
    assert all(f["approved_by"] == "mohan (MOSIP-77)" for f in rescored["findings"] if f["rule_id"] == "REG-001")


def test_a_per_test_deferral_does_not_waive_the_whole_gate(tmp_path):
    d = doc(gates=[{"gate": "full_regression", "status": "FAIL"}],
            exceptions=[{"action": "regression_deferred", "gate": "full_regression", **HUMAN, "bug": "MOSIP-77",
                         "test_ids": ["TC_KM_A_02"], "follow_up": "MOSIP-77"}])
    ctx = manifest.Ctx(d)
    res = Result("x")
    manifest._required_gates(ctx, res)
    assert any("full_regression" in f.message for f in res.findings)


# --- reports kept with the task; runs that hang are stopped -----------------------------------------------------

def _py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_run_keeps_the_testng_reports_it_wrote(tmp_path):
    repo = make_repo(tmp_path / "km")
    rig = repo / "api-test"
    (rig / "testng-report").mkdir(parents=True)
    (rig / "testng-report" / "old-full-report.html").write_text("old", encoding="utf-8")
    os.utime(rig / "testng-report" / "old-full-report.html", (1, 1))        # from an earlier run: not kept
    write_report = ("import pathlib; p=pathlib.Path('testng-report'); "
                    "(p/'mosip-km-eng-2026-11-12_10-00-full-report_T-1.html').write_text('r'); "
                    "(p/'emailable-report.html').write_text('e')")
    out = tmp_path / "task" / "evidence" / "system-tests.json"
    res = evidence.capture(str(repo), str(out), _py(write_report), cwd=str(rig))
    assert res.status == "PASS", res.to_text()
    rec = load(out)
    kept = sorted(Path(k).name for k in rec["reports"])
    assert kept == ["emailable-report.html", "mosip-km-eng-2026-11-12_10-00-full-report_T-1.html"]
    assert all(Path(k).is_relative_to(out.parent / "reports" / "system-tests") for k in rec["reports"])


def test_run_stops_on_fail_on_pattern_and_on_timeout(tmp_path):
    repo = make_repo(tmp_path / "km")
    hang = "import time,sys; print('HTTP 401 Unauthorized at login', flush=True); time.sleep(120)"
    out = tmp_path / "ev" / "sys.json"
    res = evidence.capture(str(repo), str(out), _py(hang), fail_on=r"HTTP 401|NotAuthorizedException")
    assert "EVD-005" in ids(res) and load(out)["stopped"].startswith("--fail-on matched")
    assert load(out)["duration_s"] < 60
    res = evidence.capture(str(repo), str(out), _py("import time; time.sleep(120)"), timeout_min=0.05)
    assert "EVD-005" in ids(res) and "timeout" in load(out)["stopped"]
    assert "EVD-005" in ids(evidence.run(str(out), str(repo)))


# --- guard: no false positives on note text; files outside every checkout are not repository changes -------------------

def test_guard_ignores_git_words_inside_quoted_arguments():
    note = 'crewline manifest t.json --to COMMIT_PR --note "ready to git commit; never git push --force"'
    assert guard.classify_shell(note) == "other"                        # was GRD-005 / GRD-002 in KM-COV-001
    assert guard.classify_shell('git commit -m "x"') == "commit"
    assert guard.classify_shell('bash -c "git push --force"') == "deny_force"   # a nested shell is still code
    assert guard.classify_shell("git commit -s -F - <<'EOF'\nsubject\n\ngit push --force\nEOF") == "commit"


def test_guard_allows_writes_outside_every_checkout(tmp_path):
    repo = make_repo(tmp_path / "km")
    m = write(tmp_path / "km", doc(scope={"module": "km", "repositories": [], "approved_paths": ["src/**"]}))
    memory = tmp_path / "home" / ".claude" / "projects" / "x" / "memory" / "feedback.md"
    memory.parent.mkdir(parents=True)
    assert guard.check_write(str(memory), m, str(repo)).status == "PASS"           # agent memory, not the repo
    assert guard.check_write(str(repo / "pom.xml"), m, str(repo)).status == "FAIL"  # repo file outside scope: still guarded


# --- creds: values merged by key, never printed; original restored -------------------------------------------------

def test_creds_merge_and_restore_without_printing_values(tmp_path):
    wt = make_repo(tmp_path / "wt", {"config/keymanager.properties": "# env\nkeycloak-external-url=\nmosip_admin_client_secret=\n"})
    props = wt / "config" / "keymanager.properties"
    values = tmp_path / "released.txt"
    values.write_text("keycloak-external-url=https://iam.released.example\nmosip_admin_client_secret=s3cr3t\n"
                      "mosip_partner_client_secret: p4ss\n", encoding="utf-8")
    res = creds.merge(str(values), [str(props)])
    assert res.status == "PASS"
    text = props.read_text(encoding="utf-8")
    assert "mosip_admin_client_secret=s3cr3t" in text and "mosip_partner_client_secret=p4ss" in text
    assert "s3cr3t" not in res.to_json() and "p4ss" not in res.to_json()
    assert git(wt, "status", "--porcelain").strip() == ""                 # skip-worktree, original excluded
    assert creds.restore([str(props)]).status == "PASS"
    assert "s3cr3t" not in props.read_text(encoding="utf-8")
    assert git(wt, "status", "--porcelain").strip() == ""


# --- reuse the user's existing base run ---------------------------------------------------------------------------

def test_user_supplied_base_report_saves_the_base_run(tmp_path):
    base = _report(tmp_path / "their-run", ["TC_KM_A_01", "TC_KM_A_02"])          # report dated 2026-11-11
    branch = _report(tmp_path / "branch", ["TC_KM_A_01", "TC_KM_A_02"])
    out = tmp_path / "evidence" / "full-regression.json"
    args = ["regression", "--baseline", base, "--candidate", branch, "--baseline-from-user", "--out", str(out)]
    assert cli.main(args) == 1 and "REG-010" in {f["rule_id"] for f in load(out)["findings"]}   # no approver
    assert cli.main(args + ["--approver", "orchestrator"]) == 1                                  # not an agent
    assert cli.main(args + ["--approver", "mohan", "--max-age-days", "100000"]) == 0
    inp = load(out)["inputs"]
    assert inp["baseline_source"] == "user" and inp["baseline_approver"] == "mohan"
    assert cli.main(args + ["--approver", "mohan", "--max-age-days", "0"]) == 0          # stale base: a warning only
    assert any(f["rule_id"] == "REG-010" and f["severity"] == "WARN" for f in load(out)["findings"])
