"""Efficiency findings from MOSIP-45750 (Java 21 -> 25 upgrade run with Crewline 0.7.2).

Each test names the finding it covers (#n in the task's crewline-optimizations.md).
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

from crewline import __main__ as cli
from crewline.result import Result
from crewline import branch_env, clip, evidence, manifest, regression, robustness, systest, task, tools, workspace

WIN = os.name == "nt"
HUMAN = {"approver": "mohan", "reason": "r", "scope": "s", "timestamp": "2026-11-11T00:00:00Z"}


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    (path / "README.md").write_text("x\n", encoding="utf-8")
    git(path, "add", "-A")
    git(path, "-c", "user.name=T", "-c", "user.email=t@example.org", "commit", "-q", "-m", "c")
    return path


def script(folder: Path, name: str, body_win: str, body_posix: str) -> Path:
    """An executable that only PATH/PATHEXT resolution can start without a shell (`name` -> `name.cmd` on Windows)."""
    folder.mkdir(parents=True, exist_ok=True)
    if WIN:
        p = folder / f"{name}.cmd"
        p.write_text(f"@echo off\r\n{body_win}\r\n", encoding="utf-8")
    else:
        p = folder / name
        p.write_text(f"#!/bin/sh\n{body_posix}\n", encoding="utf-8")
        p.chmod(0o755)
    return p


def doc(**over):
    d = {
        "task_id": "t", "task_type": "feature", "risk_tier": "L2", "outcome": "IN_PROGRESS", "current_state": "IMPACT_ANALYSIS",
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


def ids(res):
    return {f.rule_id for f in res.findings}


# --- #3: a task never lands in the plugin cache when cwd is a git clone ---------------------------------------------

def test_3_tasks_root_falls_back_to_the_enclosing_git_clone(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "admin-services")             # no .crewline/: not onboarded
    (repo / "api-test").mkdir()
    monkeypatch.delenv("CREWLINE_TASKS", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    assert workspace.tasks_root(repo / "api-test").resolve() == (repo / ".crewline-work" / "tasks").resolve()
    assert workspace.tasks_root(tmp_path / "not-a-repo") == workspace.CORE / "tasks"   # outside git: unchanged


def test_3_task_new_in_a_plain_clone_keeps_the_task_there_and_excludes_it(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "svc")
    monkeypatch.delenv("CREWLINE_TASKS", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.delenv("CREWLINE_TASK_MANIFEST", raising=False)
    monkeypatch.chdir(repo)
    res = task.run("new", "UPG-1", "dependency-upgrade")
    assert res.status == "PASS", res.to_text()
    assert (repo / ".crewline-work" / "tasks" / "UPG-1" / "task-manifest.json").is_file()
    assert ".crewline-work/" in (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8").split()
    assert ".crewline-work" not in git(repo, "status", "--porcelain")


# --- #13 / #15 / #6: crewline run on Windows, with a chosen JDK ----------------------------------------------------

def test_13_run_resolves_the_executable_through_pathext(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "repo")
    bin_dir = tmp_path / "bin"
    script(bin_dir, "fakemvn", "echo BUILD OK", "echo BUILD OK")
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    res = evidence.capture(str(repo), str(tmp_path / "ev" / "build.json"), ["fakemvn", "-B", "verify"])
    assert res.status == "PASS", res.to_text()                 # was NOT_RUN: WinError 2 for a .cmd
    rec = json.loads((tmp_path / "ev" / "build.json").read_text(encoding="utf-8"))
    assert rec["exit_code"] == 0 and "BUILD OK" in Path(rec["log"]).read_text(encoding="utf-8")


def test_15_run_env_java_home_is_used_and_recorded(tmp_path):
    repo = make_repo(tmp_path / "repo")
    jdk = tmp_path / "jdk-25"
    script(jdk / "bin", "java", 'echo openjdk version "25.0.1" 2025-10-21 1>&2',
           'echo \'openjdk version "25.0.1" 2025-10-21\' 1>&2')
    out = tmp_path / "ev" / "unit.json"
    res = evidence.capture(str(repo), str(out), ["java", "-version"], env=[f"JAVA_HOME={jdk}", "SECRET_TOKEN=s3cr3t"])
    assert res.status == "PASS", res.to_text()
    rec = json.loads(out.read_text(encoding="utf-8"))
    assert rec["java_version"].startswith('openjdk version "25.0.1"')     # which JDK made the evidence
    assert rec["env"] == ["JAVA_HOME", "SECRET_TOKEN"]                     # names only, never values
    assert "s3cr3t" not in out.read_text(encoding="utf-8")
    assert '"25.0.1"' in Path(rec["log"]).read_text(encoding="utf-8")       # the command ran on that JDK too


def test_6_child_env_disables_msys_path_conversion_and_rejects_bad_pairs():
    assert clip.child_env()["MSYS_NO_PATHCONV"] == "1"
    try:
        clip.child_env(["NOEQUALS"])
    except ValueError as e:
        assert "KEY=VALUE" in str(e)
    else:
        raise AssertionError("a pair without = must be refused")


# --- #4 / #16 / #14: schema first, every error with its JSON path ---------------------------------------------------

def test_4_16_manifest_reports_every_schema_error_with_json_path(tmp_path):
    bad = doc(scope={"module": "svc", "repositories": ["admin-services"]},
              test_plan={"environment_kind": "released"},
              repair={"attempt_count": 1, "attempt_history": [{"attempt": 1, "track": "dev"}]})
    res = manifest.check(write(tmp_path, bad))                # used to crash: 'str' object has no attribute 'get'
    msgs = [f.message for f in res.findings if f.rule_id == "MAN-001"]
    assert res.status == "FAIL"
    assert any(m.startswith("$.scope.repositories[0]:") for m in msgs)
    assert any(m.startswith("$.test_plan.environment_kind:") and "local-stack" in m for m in msgs)   # allowed values
    assert any(m.startswith("$.repair.attempt_history[0]:") for m in msgs)
    assert len(msgs) >= 3                                       # all at once, not a few per round-trip
    t = manifest.transition(write(tmp_path, bad), "DESIGN")
    assert t.status == "FAIL" and "MAN-001" in ids(t)


def test_14_robustness_names_a_mistyped_field_instead_of_crashing(tmp_path):
    repo = make_repo(tmp_path / "repo")
    m = write(tmp_path, doc(task_type="dependency-upgrade", implementation={"robustness": "reused GlobalExceptionHandler"}))
    res = robustness.run(str(repo), "HEAD", m)
    assert res.status == "FAIL"
    assert any(f.rule_id == "MAN-001" and f.message.startswith("$.implementation.robustness:") for f in res.findings)


# --- #18 / #9: BEV-004 can be excepted; a test-rig-only change runs against a released env ---------------------------

def test_18_bev004_honours_an_attributed_branch_environment_exception(tmp_path):
    plan = {"environment_kind": "local-stack", "environment": "https://api-internal.released.mosip.net"}
    assert branch_env.run(write(tmp_path, doc(test_plan=plan))).status == "FAIL"
    res = branch_env.run(write(tmp_path, doc(test_plan=plan, exceptions=[{"gate": "branch_environment", **HUMAN}])))
    assert res.status == "PASS"
    bev = [f for f in res.findings if f.rule_id == "BEV-004"]
    assert bev and bev[0].approved_by == "mohan" and res.inputs["exception"]["approver"] == "mohan"
    agent = {"gate": "branch_environment", **HUMAN, "reason": ""}                 # unattributed: no effect
    assert branch_env.run(write(tmp_path, doc(test_plan=plan, exceptions=[agent]))).status == "FAIL"


def test_9_local_test_rig_kind_for_a_test_rig_only_change(tmp_path):
    plan = {"environment_kind": "local-test-rig", "environment": "https://api-internal.released.mosip.net"}
    rig = {"changed_files": ["admin-services/api-test/pom.xml", "apitest-commons/pom.xml"]}
    res = branch_env.run(write(tmp_path, doc(task_type="dependency-upgrade", test_plan=plan, implementation=rig)))
    assert res.status == "PASS", res.to_text()
    prod = {"changed_files": ["api-test/pom.xml", "admin-service/src/main/java/A.java"]}
    res = branch_env.run(write(tmp_path, doc(task_type="dependency-upgrade", test_plan=plan, implementation=prod)))
    assert "BEV-005" in ids(res) and res.status == "FAIL"
    assert "BEV-005" in ids(branch_env.run(write(tmp_path, doc(test_plan=plan))))   # nothing recorded: refused
    assert manifest.schema_errors(doc(test_plan=plan)) == []


# --- #21 / #22: per-language pairing; compared reports kept with the task ------------------------------------------

def _rig_report(folder: Path, lang: str, passed: list[str], failed: list[str] = (), stamp="2026-11-11_14-44"):
    body = "<h3>A — Passed</h3>" + "".join(f"<td>{u}</td>" for u in passed)
    body += "<h3>A — Failed</h3>" + "".join(f"<td>{u}</td>" for u in failed) if failed else ""
    folder.mkdir(parents=True, exist_ok=True)
    name = f"mosip-api-internal.released-masterdata-{lang}-{stamp}-full-report_T-2_P-2_S-0_F-0.html"
    (folder / name).write_text(f"<html><body>{body}</body></html>", encoding="utf-8")


def test_21_regression_pairs_every_language_report(tmp_path):
    base, branch = tmp_path / "base", tmp_path / "branch"
    for lang in ("eng", "ara", "fra"):
        _rig_report(base, lang, ["TC_md_A_01", "TC_md_A_02"])
    _rig_report(branch, "eng", ["TC_md_A_01", "TC_md_A_02"])
    _rig_report(branch, "ara", ["TC_md_A_01"], ["TC_md_A_02"])          # only the Arabic run broke
    _rig_report(branch, "eng", ["TC_md_A_01"], ["TC_md_A_02"], stamp="2026-11-10_09-00")   # older eng run: ignored
    res = regression.run(str(base), str(branch))
    assert [p["key"].rsplit("-", 1)[1] for p in res.inputs["pairs"]] == ["ara", "eng"]
    assert any(f.rule_id == "REG-001" and "-ara] TC_md_A_02" in f.message for f in res.findings)
    assert any(f.rule_id == "REG-009" and f.evidence.endswith("-fra") for f in res.findings)
    assert res.status == "FAIL"


def test_22_regression_out_copies_reports_so_the_base_checkout_can_go(tmp_path):
    repo = make_repo(tmp_path / "wt")                              # the branch worktree: its HEAD is the commit
    base, branch = tmp_path / "base-wt" / "testng-report", repo / "api-test" / "testng-report"
    for lang in ("eng", "hin"):
        _rig_report(base, lang, ["TC_md_A_01"])
        _rig_report(branch, lang, ["TC_md_A_01", "TC_md_A_02"])     # a new case passes on the branch
    out = tmp_path / "task" / "evidence" / "full-regression.json"
    assert cli.main(["regression", "--baseline", str(base), "--candidate", str(branch), "--out", str(out)]) == 0
    rec = json.loads(out.read_text(encoding="utf-8"))
    inp = rec["inputs"]
    copies = out.parent / "reports" / "full-regression"
    assert Path(inp["baseline_copy"]) == copies / "base" and len(list((copies / "base").iterdir())) == 2
    assert Path(inp["candidate_copy"]) == copies / "branch"
    shutil.rmtree(tmp_path / "base-wt")                            # principle 9: remove the base checkout
    assert systest.stale_reason(inp) == ""                         # REG-007 still accepts the record


# --- #1: tools probes only what the task type needs ---------------------------------------------------------------

def test_1_tools_skips_capabilities_for_other_task_types(monkeypatch):
    assert tools._types_named(["release", "devops changes"]) == {"release"}
    assert tools._types_named(["REPO_READY", "COMMIT_PR"]) is None             # a state: always probed
    spec = {"capabilities": {
        "helm": {"needed_for": ["release", "devops changes"], "check": {"command": "definitely-not-helm version"}},
        "git": {"needed_for": ["REPO_READY", "COMMIT_PR"], "check": {"command": "git --version", "expect": "git"}},
    }}
    monkeypatch.setattr(tools.pack, "merged_tools", lambda: spec)
    res = tools.run(task_type="dependency-upgrade")
    assert res.status == "PASS", res.to_text()                                 # helm no longer BLOCKs (TOOL-001)
    assert ids(res) == {"TOOL-003", "TOOL-002"}
    assert tools.run(task_type="release").status == "FAIL"                     # a release still needs helm
    assert tools.run(only=["helm"], task_type="dependency-upgrade").status == "FAIL"   # asked for by name: probed


# --- #10 / #23: gates checked when their own state ends; no triage without a failure --------------------------------

def test_10_leaving_a_state_checks_the_gate_it_owns(tmp_path):
    d = doc(current_state="IMPLEMENTATION_PLAN", gates=[{"gate": "design_readiness", "status": "PASS"}])
    res = manifest.transition(write(tmp_path, d), "GITHUB_PLANNING")
    assert any(f.rule_id == "TRN-004" and "plan_readiness" in f.message for f in res.findings), res.to_text()
    d["gates"].append({"gate": "plan_readiness", "status": "PASS"})
    assert "TRN-004" not in ids(manifest.transition(write(tmp_path, d), "GITHUB_PLANNING"))


def test_23_no_failure_triage_when_full_regression_passed(tmp_path, monkeypatch):
    d = doc(current_state="SYSTEM_TEST_EXECUTION", gates=[{"gate": "full_regression", "status": "PASS"}])
    assert "TRN-005" in ids(manifest.transition(write(tmp_path, d), "FAILURE_TRIAGE"))
    d["gates"] = [{"gate": "full_regression", "status": "FAIL"}]
    assert "TRN-005" not in ids(manifest.transition(write(tmp_path, d), "FAILURE_TRIAGE"))
    d["repair"]["classification"] = "CODE_UNDER_CHANGE"                  # stale, from an earlier build repair
    m = write(tmp_path, d)
    monkeypatch.setattr(manifest, "transition", lambda *_: Result("manifest-transition").finish())   # guards: other tests
    assert manifest.apply(m, "FAILURE_TRIAGE").status == "PASS"
    assert json.loads(Path(m).read_text(encoding="utf-8"))["repair"]["classification"] == ""    # each triage classifies anew
