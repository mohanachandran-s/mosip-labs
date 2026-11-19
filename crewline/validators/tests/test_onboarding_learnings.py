"""Findings from the MAT-ONBOARD session (mosip-automation-tests, DSL TestRig onboarding with Crewline 0.7.2/0.8.0).

Each test names the finding it covers (#n in the session analysis).
"""
import json
import subprocess
import sys
from pathlib import Path

from crewline import discover, evidence, onboarding, records, task, usage, version

HUMAN = {"approver": "mohan", "approver_type": "human", "reason": "r", "scope": "s", "timestamp": "2026-11-19T00:00:00Z"}


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def commit(repo, msg="c"):
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=T", "-c", "user.email=t@example.org", "commit", "-q", "-m", msg)


def ids(res):
    return {f.rule_id for f in res.findings}


def doc(**over):
    d = {
        "task_id": "T", "task_type": "module-onboarding", "risk_tier": "L1", "outcome": "IN_PROGRESS",
        "current_state": "IMPACT_ANALYSIS", "started_at": "2026-11-19T10:00:00Z",
        "scope": {"module": "mat", "repositories": [], "approved_paths": []},
        "requirements": {"source_refs": [], "acceptance_criteria": [], "open_ambiguities": 0},
        "testcases": {"canonical_format": "none"},
        "repair": {"attempt_count": 0, "dev_attempt_count": 0, "attempt_history": [], "classification": ""},
        "reviews": {}, "tracker": {}, "gates": [], "approvals": [], "evidence_refs": [],
        "github": {"issue": None, "subtasks": [], "pull_request": None},
    }
    d.update(over)
    return d


def call(ts, mid, cache_read, inp=100, out=50):
    return json.dumps({"timestamp": ts, "message": {"id": mid, "model": "m", "usage": {
        "input_tokens": inp, "cache_creation_input_tokens": 0, "cache_read_input_tokens": cache_read, "output_tokens": out}}})


# --- #1 token budget: cache reads weighted, waiting on a person not counted ----------------------------------------
def test_1_cache_reads_are_weighted(tmp_path):
    tr = tmp_path / "s.jsonl"
    tr.write_text(call("2026-11-19T10:01:00Z", "a", 5000), encoding="utf-8")       # fresh 150, cache 5000
    m = tmp_path / "task-manifest.json"
    m.write_text(json.dumps(doc(current_state="INTAKE", usage={"budget": {"per_task": 10**6, "per_state": 1000}})))
    res = usage.report(str(m), [str(tr)])
    assert "USE-004" not in ids(res) and res.inputs["overall"]["weighted"] == 650     # 150 + 5000 x 0.1
    m.write_text(json.dumps(doc(current_state="INTAKE",
                                usage={"budget": {"per_task": 10**6, "per_state": 1000, "metric": "total"}})))
    assert "USE-004" in ids(usage.report(str(m), [str(tr)]))                          # the old full-weight count trips


def test_1_waiting_on_a_person_is_not_counted(tmp_path):
    tr = tmp_path / "s.jsonl"
    tr.write_text("\n".join([call("2026-11-19T10:01:00Z", "a", 0, inp=900),            # INTAKE, real work
                             call("2026-11-19T10:03:00Z", "b", 0, inp=5000),           # WAITING_FOR_USER
                             call("2026-11-19T10:06:00Z", "c", 0, inp=5000)]),         # step "wait: ..."
                  encoding="utf-8")
    d = doc(current_state="IMPLEMENTATION", initial_state="INTAKE",
            state_history=[{"from": "INTAKE", "to": "WAITING_FOR_USER", "at": "2026-11-19T10:02:00Z"},
                           {"from": "WAITING_FOR_USER", "to": "IMPLEMENTATION", "at": "2026-11-19T10:04:00Z"}],
            usage_marks=[{"label": "INTAKE->WAITING_FOR_USER", "at": "2026-11-19T10:02:00Z"},
                         {"label": "WAITING_FOR_USER->IMPLEMENTATION", "at": "2026-11-19T10:04:00Z"},
                         {"label": "wait: user installs JDK 25", "at": "2026-11-19T10:05:00Z"}],
            usage={"budget": {"per_task": 2000, "per_state": 2000}})
    m = tmp_path / "task-manifest.json"
    m.write_text(json.dumps(d))
    res = usage.report(str(m), [str(tr)])
    assert not {"USE-003", "USE-004"} & ids(res)
    assert res.inputs["overall"]["waited"] == 10100 and res.inputs["overall"]["budgeted"] == 950
    assert "| waited |" in (tmp_path / "usage.md").read_text(encoding="utf-8")


# --- #2 run --stall --------------------------------------------------------------------------------------------------
def _repo(tmp_path):
    r = tmp_path / "r"
    r.mkdir()
    git(r, "init", "-q")
    (r / "a.txt").write_text("x\n")
    commit(r)
    return r


def test_2_stall_stops_an_idle_build(tmp_path):
    r = _repo(tmp_path)
    cmd = [sys.executable, "-c", "print('Tomcat started', flush=True); import time; time.sleep(60)"]
    res = evidence.capture(str(r), str(tmp_path / "ev" / "build.json"), cmd, stall_min=0.03)
    rec = json.loads((tmp_path / "ev" / "build.json").read_text())
    assert "EVD-005" in ids(res) and rec["stopped"].startswith("--stall") and rec["duration_s"] < 30


def test_2_stall_leaves_a_talking_build_alone(tmp_path):
    r = _repo(tmp_path)
    cmd = [sys.executable, "-c", "import time\nfor i in range(8):\n    print(i, flush=True); time.sleep(0.4)"]
    res = evidence.capture(str(r), str(tmp_path / "ev" / "build.json"), cmd, stall_min=0.05)
    assert res.status == "PASS" and "stopped" not in json.loads((tmp_path / "ev" / "build.json").read_text())


# --- #5 discover heuristics -------------------------------------------------------------------------------------------
POM_ROOT = """<project><artifactId>root</artifactId><packaging>pom</packaging>
<modules><module>lib</module><module>rig</module></modules>
<build><pluginManagement><plugins><plugin><groupId>org.jacoco</groupId><artifactId>jacoco-maven-plugin</artifactId>
</plugin></plugins></pluginManagement>
<plugins><plugin><artifactId>maven-compiler-plugin</artifactId><configuration><argLine>-Xmx1g</argLine></configuration>
</plugin></plugins></build></project>"""
POM_LIB = """<project><artifactId>mosipTestDataProvider</artifactId><dependencies>
<dependency><groupId>io.cucumber</groupId><artifactId>cucumber-java</artifactId></dependency></dependencies></project>"""
POM_RIG = "<project><artifactId>ivv-orchestrator</artifactId></project>"


def _dsl_repo(tmp_path):
    r = tmp_path / "mat"
    for rel, text in {"pom.xml": POM_ROOT, "lib/pom.xml": POM_LIB, "rig/pom.xml": POM_RIG,
                      "lib/src/main/java/x/Lib.java": "class Lib {}",
                      "rig/src/main/java/io/mosip/testrig/dslrig/TestRunner.java": "class TestRunner {}",
                      "dg/src/main/resources/ScenarioSheet.csv": "a,b\n"}.items():
        (r / rel).parent.mkdir(parents=True, exist_ok=True)
        (r / rel).write_text(text, encoding="utf-8")
    git(tmp_path, "init", "-q", str(r))
    commit(r, "base")
    git(r, "branch", "release-1.6.x")
    (r / "AGENTS.md").write_text("guidance on develop only\n")
    commit(r, "develop: guidance")
    # an older task worktree of the same repo must not be scanned
    stale = r / ".crewline-work" / "worktrees" / "OLD" / "svc"
    stale.mkdir(parents=True)
    (stale / "pom.xml").write_text("<project><artifactId>stale</artifactId></project>")
    return r


def test_5_discover_heuristics_on_a_dsl_repo(tmp_path):
    r = _dsl_repo(tmp_path)
    out = tmp_path / "ctx"
    res = discover.run(str(r), "mat", str(out), base="release-1.6.x")
    facts = json.loads((out / ".crewline/development/discovery.json").read_text())
    found = ids(res)
    assert "DSC-001" not in found                                       # compiler-plugin argLine is not surefire's
    assert "pluginManagement" in next(f.message for f in res.findings if f.rule_id == "DSC-002")
    assert facts["ui_test_projects"] == []                              # cucumber alone drives no browser
    assert [s["path"] for s in facts["system_test_projects"]] == ["rig"] and "DSC-011" in found
    assert "rig" not in facts["services"] and "lib" in facts["services"]
    assert not any(".crewline-work" in p["path"] for p in facts["maven"]["poms"])
    d12 = next(f.message for f in res.findings if f.rule_id == "DSC-012")
    assert "release-1.6.x" in d12 and "AGENTS.md" in d12
    assert "System-test rig" in (out / ".crewline/development/local-context.md").read_text()


# --- #3 tracker waive -------------------------------------------------------------------------------------------------
def test_3_tracker_waive_command(tmp_path):
    m = tmp_path / "task-manifest.json"
    m.write_text(json.dumps(doc()))
    assert "MAN-010" in ids(records.tracker_waive(str(m), "agent:orchestrator", "r", "s"))
    res = records.tracker_waive(str(m), "mohan", "onboarding needs no ticket", "MAT-ONBOARD")
    w = json.loads(m.read_text())["tracker"]["waived"]
    assert res.status == "PASS" and w["action"] == "tracker_waiver" and w["approver"] == "mohan"
    records.tracker_waive(str(m), "mohan", "no subtasks", "MAT-ONBOARD", subtasks=True)
    assert json.loads(m.read_text())["tracker"]["subtasks_waived"]["action"] == "subtasks_waiver"
    m.write_text(json.dumps(doc(tracker={"system": "jira", "key": "MOSIP-9", "confirmed_by": "mohan"})))
    assert records.tracker_waive(str(m), "mohan", "r", "s").status != "PASS"


# --- #4 onboarding gates decided by `gates refresh` -----------------------------------------------------------------------
def test_4_onboarding_gates(tmp_path):
    r = _dsl_repo(tmp_path)
    tdir = r / ".crewline-work" / "tasks" / "T"
    tdir.mkdir(parents=True)
    m = tdir / "task-manifest.json"
    m.write_text(json.dumps(doc()))
    assert "ONB-001" in ids(onboarding.discovery_gate(str(m)))
    discover.run(str(r), "mat")                                          # default out: <clone>/.crewline-work/onboarding
    assert onboarding.discovery_gate(str(m)).status == "PASS"
    base = git(r, "rev-parse", "release-1.6.x").strip()
    m.write_text(json.dumps(doc(scope={"module": "mat", "approved_paths": [],
                                       "repositories": [{"name": "mat", "path": str(r), "baseline_commit": base}]})))
    assert "ONB-003" in ids(onboarding.discovery_gate(str(m)))          # discovery read develop, base is release-1.6.x

    m.write_text(json.dumps(doc()))
    res = onboarding.baseline_gate(str(m))
    assert {"ONB-004", "ONB-007"} <= ids(res) and res.status != "PASS"
    m.write_text(json.dumps(doc(approvals=[{"action": "onboarding_review", **HUMAN}])))
    (tdir / "evidence").mkdir()
    (tdir / "evidence" / "baseline-build.json").write_text(json.dumps({"exit_code": 0, "duration_s": 91}))
    assert onboarding.baseline_gate(str(m)).status == "PASS"
    (tdir / "evidence" / "build-jdk21.json").write_text(json.dumps({"exit_code": 1, "stopped": "--stall: log idle"}))
    assert "ONB-006" in {f.rule_id for f in onboarding.baseline_gate(str(m)).findings if f.severity == "BLOCK"}


# --- #6 task close, status after DONE --------------------------------------------------------------------------------------
def test_6_task_close_and_status_after_done(tmp_path, monkeypatch):
    r = _repo(tmp_path)
    tasks = r / ".crewline-work" / "tasks"
    tree = r / ".crewline-work" / "worktrees" / "T"
    git(r, "worktree", "add", "-q", str(tree), "-b", "t-branch")
    (tasks / "T").mkdir(parents=True)
    m = tasks / "T" / "task-manifest.json"
    m.write_text(json.dumps(doc(current_state="DONE", outcome="SUCCESS", workspace={
        "clone": r.as_posix(), "open_worktrees": 1,
        "worktrees": [{"path": tree.as_posix(), "branch": "t-branch", "purpose": "task"}]})))
    monkeypatch.setenv("CREWLINE_TASKS", str(tasks))
    assert "TSK-012" in ids(task.run("close", "T"))                       # no report yet
    report = tasks / "T" / "final-report.md"
    report.write_text("# done\n")
    res = task.run("close", "T", report=str(report))
    d = json.loads(m.read_text())
    assert res.status == "PASS" and d["final_report_ref"] == "final-report.md"
    assert not tree.exists() and d["workspace"]["open_worktrees"] == 0 and d["workspace"]["worktrees"][0]["removed_at"]
    st = records.status(str(m), closed=True)
    assert "STS-002" in ids(st) and "final-report.md" in st.findings[0].message


# --- #7 stale plugin copy -------------------------------------------------------------------------------------------------------
def test_7_stale_cached_copy_warns(tmp_path):
    for v in ("0.7.2", "0.8.0"):
        p = tmp_path / "plugins" / "cache" / "mosip-labs" / "crewline" / v / ".claude-plugin" / "plugin.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({"version": v}))
    old = tmp_path / "plugins" / "cache" / "mosip-labs" / "crewline" / "0.7.2"
    new = old.parent / "0.8.0"
    assert "0.8.0" in version.stale_install(old) and version.stale_install(new) == ""
    assert version.stale_install(Path(__file__).resolve().parents[2]) == ""   # a source checkout never warns
