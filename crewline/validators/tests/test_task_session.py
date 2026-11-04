"""Resume without hand-written files (#114), explicit active task (#115), a session inside the task worktree (#116)."""
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from crewline import evidence, gates, guard, handover, hook, manifest, session, systest, task, workspace
from crewline.__main__ import module_profile

HUMAN = {"approver": "mohan", "reason": "r", "scope": "s", "timestamp": "2026-11-04T00:00:00Z"}
TICKET = {"system": "jira", "key": "MOSIP-1", "confirmed_by": "mohan"}


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@example.org", *args],
                          check=True, capture_output=True, text=True).stdout


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("CREWLINE_TASK_MANIFEST", "CREWLINE_TASKS", "CLAUDE_PROJECT_DIR", "CREWLINE"):
        monkeypatch.delenv(name, raising=False)


def make_task(root, tid, **over):
    d = task.skeleton(tid, over.pop("task_type", "feature"))
    d.update(tracker=dict(TICKET), **over)
    p = root / tid / "task-manifest.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d), encoding="utf-8")
    return p


# --- #115 explicit active task -----------------------------------------------------------------------------------
def test_active_task_does_not_follow_the_newest_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CREWLINE_TASKS", str(tmp_path))
    a, b = make_task(tmp_path, "A"), make_task(tmp_path, "B")
    os.utime(a, (1, 1))
    assert guard.find_active_manifest().parent.name == "B"                  # no choice made: the old fallback
    assert task.run("activate", "A").status == "PASS"
    os.utime(b, (time.time() + 60, time.time() + 60))                       # editing B must not steal the rails
    assert workspace.active_manifest() == (a, "active-task")
    listed = task.run("list").to_text()
    assert "* A:" in listed and "[active: active-task]" in listed and "  B:" in listed
    assert task.run("deactivate").status == "PASS"
    assert guard.find_active_manifest().parent.name == "B"


def test_activate_refuses_unknown_and_closed_and_stale_file_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("CREWLINE_TASKS", str(tmp_path))
    make_task(tmp_path, "OPEN")
    closed = make_task(tmp_path, "CLOSED")
    assert task.run("activate", "NOPE").status == "FAIL"
    assert task.run("activate", "OPEN").status == "PASS"
    d = json.loads(closed.read_text(encoding="utf-8")); d["outcome"] = "SUCCESS"
    closed.write_text(json.dumps(d), encoding="utf-8")
    assert task.run("activate", "CLOSED").status == "FAIL"
    (tmp_path / "OPEN" / "task-manifest.json").unlink()
    make_task(tmp_path, "OTHER")
    assert guard.find_active_manifest().parent.name == "OTHER"              # the pinned task is gone


def test_env_manifest_beats_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CREWLINE_TASKS", str(tmp_path))
    make_task(tmp_path, "A")
    b = make_task(tmp_path, "B")
    task.run("activate", "A")
    monkeypatch.setenv("CREWLINE_TASK_MANIFEST", str(b))
    assert workspace.active_manifest() == (b, "env")


def test_every_deny_names_the_active_task(tmp_path):
    m = make_task(tmp_path, "PP-X", scope={"approved_paths": ["docs/**"]}, current_state="IMPLEMENTATION")
    for res in (guard.check_shell("git commit -s -m x", str(m)), guard.check_shell("git push --force", str(m)),
                guard.check_write("src/X.java", str(m), cwd=str(tmp_path)),
                guard.check_read("target/surefire-reports/a.log", str(tmp_path), str(m))):
        assert res.status == "FAIL" and "active task PP-X" in res.to_text()


def test_scratchpad_is_writable_but_a_checkout_in_temp_stays_guarded(tmp_path, monkeypatch):
    temp = tmp_path / "Temp"
    temp.mkdir()
    monkeypatch.setenv("TEMP", str(temp))
    m = make_task(tmp_path, "T", scope={"approved_paths": ["docs/**"]})
    session_dir = tmp_path / "repo"
    git(tmp_path, "init", "-q", str(session_dir))
    assert guard.check_write(str(temp / "claude" / "s" / "notes.py"), str(m), cwd=str(session_dir)).status == "PASS"
    assert guard.check_write(str(session_dir / "src" / "X.java"), str(m), cwd=str(session_dir)).status == "FAIL"
    checkout = temp / "co"
    git(tmp_path, "init", "-q", str(checkout))
    assert guard.check_write(str(checkout / "src" / "X.java"), str(m), cwd=str(session_dir)).status == "FAIL"
    assert guard.check_write(str(temp / "inside.py"), str(m), cwd=str(temp)).status == "FAIL"   # the session's own folder


# --- #116 a session inside the task worktree --------------------------------------------------------------------
@pytest.fixture
def clone(tmp_path):
    """A clone whose base branch has no .crewline/; the context lives on the onboarding worktree, like inji/pixelpass."""
    c = tmp_path / "pixelpass"
    git(tmp_path, "init", "-q", "-b", "develop", str(c))
    (c / "src").mkdir()
    (c / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    git(c, "add", "-A"); git(c, "commit", "-q", "-s", "-m", "base")
    (c / ".git" / "info" / "exclude").write_text(".crewline-work/\n", encoding="utf-8")
    onboard = c / ".crewline-work" / "worktrees" / "PP-ONBOARD"
    git(c, "worktree", "add", "-q", str(onboard), "-b", "onboarding")
    (onboard / ".crewline" / "qa").mkdir(parents=True)
    (onboard / ".crewline" / "qa" / "profile.yaml").write_text("rules: {}\n", encoding="utf-8")
    (onboard / ".crewline" / "module.yaml").write_text(
        "module: pixelpass\nroles: {development: [], qa: []}\nstack: {system_test_projects: [systest]}\n", encoding="utf-8")
    (onboard / ".claude").mkdir()
    (onboard / ".claude" / "settings.json").write_text(json.dumps({
        "enabledPlugins": {"crewline@mosip-labs": True},
        "extraKnownMarketplaces": {"mosip-labs": {"source": {"source": "github", "repo": "mosip/mosip-labs"}}}}), encoding="utf-8")
    (c / ".crewline-work" / "tasks").mkdir(parents=True)
    return c


def in_clone(c, monkeypatch, tid="PP-Y", approved=True):
    monkeypatch.chdir(c)
    over = {"approvals": [{"action": "base_branch", **HUMAN}]} if approved else {}
    return make_task(c / ".crewline-work" / "tasks", tid,
                     scope={"module": "pixelpass", "repositories": [{"name": "pixelpass", "role": "primary"}],
                            "approved_paths": ["src/**"]}, **over)


def test_task_worktree_is_a_full_crewline_session(clone, monkeypatch):
    m = in_clone(clone, monkeypatch)
    other = make_task(clone / ".crewline-work" / "tasks", "PP-OTHER", scope={"approved_paths": ["docs/**"]})
    res = task.run("worktree", "PP-Y", base="develop", branch="feature/y")
    assert res.status == "PASS", res.to_text()
    tree = clone / ".crewline-work" / "worktrees" / "PP-Y"
    assert not (tree / ".crewline").exists()                                 # context is found, never copied
    assert git(tree, "status", "--porcelain").strip() == ""                  # nothing a `git add -A` could leak
    settings = json.loads((tree / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    assert settings["enabledPlugins"]["crewline@mosip-labs"] is True and "mosip-labs" in settings["extraKnownMarketplaces"]
    assert ".crewline/" not in (clone / ".git" / "info" / "exclude").read_text(encoding="utf-8")   # would hide new context files

    doc = json.loads(m.read_text(encoding="utf-8"))
    assert doc["workspace"]["open_worktrees"] == 1 and doc["workspace"]["context_dir"].endswith("PP-ONBOARD/.crewline")
    assert doc["scope"]["repositories"][0]["branch"] == "feature/y" and doc["scope"]["repositories"][0]["baseline_commit"]

    os.utime(other, (time.time() + 60, time.time() + 60))
    assert workspace.module_top(tree) is not None
    assert workspace.tasks_root(tree).resolve() == (clone / ".crewline-work" / "tasks").resolve()
    found, why = workspace.active_manifest(cwd=tree)
    assert found.parent.name == "PP-Y" and why == "worktree"                 # the newest manifest does not steal it

    text = session.context(str(tree), env={})
    assert "ACTIVE" in text and "PP-ONBOARD/.crewline" in text and "development" in text
    assert systest.module_stack(json.loads(m.read_text(encoding="utf-8")))["system"]    # SYS-002 still sees the suites
    assert module_profile(str(tree), None).endswith("profile.yaml")

    payload = {"cwd": str(tree), "tool_input": {"file_path": str(tree / "docs" / "x.md")}}
    deny = hook.handle_plugin("write", payload)
    assert deny["permission"] == "deny" and "active task PP-Y" in deny["agent_message"]
    assert hook.handle_plugin("write", {"cwd": str(tree), "tool_input": {"file_path": str(tree / "src" / "a.py")}})["permission"] == "allow"
    shell = hook.handle_plugin("shell", {"cwd": str(tree), "tool_input": {"command": "git commit -s -m x"}})
    assert shell["permission"] == "deny" and "GRD-003" in shell["agent_message"]
    started = hook.handle_plugin("session", {"cwd": str(tree)})["additional_context"]
    assert "chosen by worktree" in started


def test_worktree_needs_the_base_branch_approval_and_keeps_user_settings(clone, monkeypatch):
    in_clone(clone, monkeypatch, approved=False)
    res = task.run("worktree", "PP-Y", base="develop", branch="feature/y")
    assert res.status == "FAIL" and "base_branch" in res.to_text()
    assert not (clone / ".crewline-work" / "worktrees" / "PP-Y").exists()
    assert "--base" in task.run("worktree", "PP-Y").to_text()

    m = in_clone(clone, monkeypatch, "PP-Z")
    assert task.run("worktree", "PP-Z", base="develop", branch="feature/z").status == "PASS"
    local = clone / ".crewline-work" / "worktrees" / "PP-Z" / ".claude" / "settings.local.json"
    local.write_text(json.dumps({"enabledPlugins": {"crewline@mosip-labs": False}, "other": 1}), encoding="utf-8")
    assert task.run("worktree", "PP-Z").status == "PASS"                     # re-run on an existing folder: idempotent
    kept = json.loads(local.read_text(encoding="utf-8"))
    assert kept["enabledPlugins"]["crewline@mosip-labs"] is False and kept["other"] == 1   # the user's opt-out wins
    assert json.loads(m.read_text(encoding="utf-8"))["workspace"]["open_worktrees"] == 1


def test_a_plain_clone_without_crewline_stays_inactive(tmp_path, monkeypatch):
    c = tmp_path / "plain"
    git(tmp_path, "init", "-q", str(c))
    assert workspace.module_top(c) is None and session.context(str(c), env={}) is None
    assert hook.handle_plugin("shell", {"cwd": str(c), "tool_input": {"command": "git push"}})["permission"] == "allow"


# --- #114 resume and gates refresh ------------------------------------------------------------------------------
@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "svc"
    git(tmp_path, "init", "-q", str(r))
    (r / "src").mkdir()
    (r / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    git(r, "add", "-A"); git(r, "commit", "-q", "-s", "-m", "base")
    return r


def wired(tmp_path, repo, **over):
    (tmp_path / "tasks").mkdir(exist_ok=True)
    base = git(repo, "rev-parse", "HEAD").strip()
    return make_task(tmp_path / "tasks", "T1", **{
        "current_state": "DETERMINISTIC_VALIDATION",
        "scope": {"module": "svc", "approved_paths": ["src/**"],
                  "repositories": [{"name": "svc", "role": "primary", "path": str(repo), "baseline_commit": base,
                                    "branch": "b", "upstream_repo": "o/svc", "base_branch": "develop"}]},
        **over})


def gate(path, name):
    return next((g for g in json.loads(Path(path).read_text(encoding="utf-8"))["gates"] if g["gate"] == name), None)


def test_gates_refresh_records_what_the_validators_return(tmp_path, repo):
    m = wired(tmp_path, repo)
    res = gates.refresh(str(m))
    change = gate(m, "change_scope")                                        # QUALITY_GATE: not this state
    assert change is None
    ti = gate(m, "test_integrity")
    assert ti["status"] == "PASS" and ti["checked_at_commit"] == git(repo, "rev-parse", "HEAD").strip()
    assert (m.parent / ti["evidence_refs"][0]).is_file()
    assert gate(m, "build")["status"] == "NOT_RUN"                          # no record: never PASS
    assert res.status == "FAIL" and "GAT-002" in {f.rule_id for f in res.findings}

    (repo / "src" / "b.py").write_text('password = "hunter2hunter2hunter2"\n', encoding="utf-8")
    d = json.loads(m.read_text(encoding="utf-8"))
    d["gates"] = [{"gate": "change_scope", "status": "PASS"}, {"gate": "implementation_review", "status": "PASS"}]
    d["scope"]["approved_paths"] = ["docs/**"]
    m.write_text(json.dumps(d), encoding="utf-8")
    out = gates.refresh(str(m), every=True)
    assert gate(m, "change_scope")["status"] == "FAIL"                      # a hand-set PASS does not survive
    assert gate(m, "implementation_review")["status"] == "PASS"             # review gates are never touched
    assert "implementation_review: not refreshed" in out.to_text()


def test_gates_refresh_keeps_not_applicable_and_reports_missing_repo(tmp_path, repo):
    m = wired(tmp_path, repo, gates=[{"gate": "build", "status": "NOT_APPLICABLE", "rationale": "docs only"}])
    assert "stays NOT_APPLICABLE" in gates.refresh(str(m)).to_text()
    assert gate(m, "build")["status"] == "NOT_APPLICABLE"
    (tmp_path / "other").mkdir()
    bare = make_task(tmp_path / "other", "T2", current_state="DETERMINISTIC_VALIDATION")
    res = gates.refresh(str(bare))
    assert "GAT-004" in {f.rule_id for f in res.findings} and gate(bare, "test_integrity")["status"] == "NOT_RUN"


def test_gates_refresh_sees_stale_evidence_and_rerun_fixes_it(tmp_path, repo):
    m = wired(tmp_path, repo)
    record = m.parent / "evidence" / "build.json"
    record.parent.mkdir()
    assert evidence.capture(str(repo), str(record), ["python", "--version"]).status == "PASS"
    gates.refresh(str(m))
    assert gate(m, "build")["status"] == "PASS"
    (repo / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")          # the repair
    gates.refresh(str(m))
    assert gate(m, "build")["status"] == "FAIL" and "EVD-002" in gate(m, "build")["rationale"]
    gates.refresh(str(m), rerun=True)
    assert gate(m, "build")["status"] == "PASS"


def test_resume_writes_the_handover_in_one_command(tmp_path, repo, monkeypatch):
    monkeypatch.setenv("CREWLINE_TASKS", str(tmp_path / "tasks"))
    m = wired(tmp_path, repo, current_state="WAITING_FOR_USER", resume_state="DETERMINISTIC_VALIDATION",
              repair={"attempt_count": 1, "dev_attempt_count": 2, "attempt_history": [], "classification": "AUTOMATION"},
              gates=[{"gate": "robustness", "status": "FAIL"}, {"gate": "secrets", "status": "PASS", "checked_at_commit": "old"}],
              approvals=[{"action": "design", **HUMAN}], pr={"url": "https://x/pull/1", "ci_status": "FAIL"})
    make_task(tmp_path / "tasks", "NEWER")
    res = handover.run("T1")
    assert res.status == "PASS"
    text = (m.parent / "HANDOVER.md").read_text(encoding="utf-8")
    for want in ("WAITING_FOR_USER", "resumes to **DETERMINISTIC_VALIDATION**", "SDET 1 of 3", "developer 2 of 3", "robustness FAIL",
                 "PASS on another commit", "secrets", "approvals not used yet: design", "https://x/pull/1", "MOSIP-1",
                 "crewline gates refresh --id T1", f"crewline manifest {m} --to DETERMINISTIC_VALIDATION"):
        assert want in text, want
    assert workspace.active_manifest() == (m, "active-task")
    assert handover.run("MISSING").status == "FAIL"
    assert handover.run(None).inputs["manifest"] == str(m)                  # no id: the active task


def test_resume_names_the_forward_move_not_a_loop_back(tmp_path, repo, monkeypatch):
    monkeypatch.setenv("CREWLINE_TASKS", str(tmp_path / "tasks"))
    m = wired(tmp_path, repo, current_state="PR_CLOSEOUT")
    handover.run("T1")
    text = (m.parent / "HANDOVER.md").read_text(encoding="utf-8")
    assert "next: **RELEASE_READINESS** (also legal: IMPLEMENTATION, FAILURE_TRIAGE)" in text
