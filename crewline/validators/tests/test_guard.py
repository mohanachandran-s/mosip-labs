"""Platform guard rails (ADR-0007, GRD-001..006)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from crewline import guard, hook, manifest

ROOT = Path(__file__).resolve().parents[2]
HUMAN = {"approver": "mohan", "reason": "r", "scope": "s", "timestamp": "2026-10-28T00:00:00Z"}


def ids(res):
    return {f.rule_id for f in res.findings}


def write_manifest(tmp_path, **over):
    d = {
        "task_id": "t-g", "task_type": "documentation", "risk_tier": "L2", "outcome": "IN_PROGRESS",
        "current_state": "QUALITY_GATE",
        "scope": {"module": "svc", "repositories": [], "approved_paths": ["docs/**", "README.md"]},
        "requirements": {"source_refs": [], "acceptance_criteria": [], "open_ambiguities": 0},
        "testcases": {"canonical_format": "none"},
        "repair": {"attempt_count": 0, "dev_attempt_count": 0, "attempt_history": [], "classification": ""},
        "reviews": {"implementation_review": "PASS", "fresh_context_review": "PASS"},
        "gates": [{"gate": g, "status": "PASS"} for g in
                  ("quality_gate", "deterministic_validation", "change_scope", "secrets", "token_budget")],
        "approvals": [{"action": "commit_pr", **HUMAN}],
        "evidence_refs": [],
        "tracker": {"system": "jira", "key": "MOSIP-1", "confirmed_by": "mohan", "closure": {"status": "declined", "reason": "r"},
                    "subtasks": [{"kind": k, "key": f"MOSIP-{i}", "closure": {"status": "declined", "reason": "r"}}
                                 for i, k in enumerate(("testcase", "automation", "implementation", "testing", "review", "pr"), 2)]},
        "github": {"issue": None, "subtasks": [], "pull_request": None},
        "test_plan": {"environment": "n/a", "environment_kind": "local-stack"},
    }
    d.update(over)
    p = tmp_path / "task-manifest.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    return str(p)


@pytest.mark.parametrize("command,action", [
    ("git commit -s -m MOSIP-1", "commit"),
    ("git commit -sm MOSIP-1", "commit"),
    ("git push origin HEAD", "push"),
    ("gh pr create --title x", "pr"),
    ("gh pr edit 12 --title 'MOSIP-1: x'", "pr_edit"),
    ("gh pr edit 12 --body-file b.md", "pr_edit"),
    ("gh pr edit 12 --add-label bug", "other"),
    ("git push --force origin HEAD", "deny_force"),
    ("git push -f", "deny_force"),
    ("git reset --hard HEAD~1", "deny_destructive"),
    ("git stash", "deny_stash"),
    ("git branch -D tmp", "deny_delete"),
    ("gh pr merge 12", "deny_merge"),
    ("helm upgrade x chart -n production", "deny_prod"),
    ("git commit -s --no-verify -m x", "deny_hooks"),
    ("mvn -B test", "deny_raw_suite"),
    ("python -m crewline run --repo r --out e.json -- mvn -B test", "other"),
    ("cat src/X.java", "other"),
    ("cat target/surefire-reports/TEST-x.xml", "deny_dump"),
    ("cat build.log", "deny_dump"),
    ("Get-Content full-report.html", "deny_dump"),
])
def test_classify_shell(command, action):
    assert guard.classify_shell(command) == action


def test_unsigned_commit_is_blocked():
    res = guard.check_shell("git commit -m skip-signoff")
    assert res.status == "FAIL" and "GRD-005" in ids(res)


def test_commit_without_task_is_blocked():
    res = guard.check_shell("git commit -s -m MOSIP-1", None)
    assert res.status == "FAIL" and "GRD-001" in ids(res)


def test_commit_before_quality_gate_is_blocked(tmp_path):
    m = write_manifest(tmp_path, current_state="IMPLEMENTATION")
    res = guard.check_shell("git commit -s -m MOSIP-1", m)
    assert res.status == "FAIL" and "GRD-003" in ids(res)


def test_signed_commit_at_quality_gate_passes_when_template_gates_pass(tmp_path):
    m = write_manifest(tmp_path)
    assert manifest.transition(m, "COMMIT_PR").status == "PASS"
    assert guard.check_shell("git commit -s -m MOSIP-1 docs", m).status == "PASS"


def test_force_push_always_denied(tmp_path):
    m = write_manifest(tmp_path, current_state="COMMIT_PR",
                       approvals=[{"action": "commit_pr", **HUMAN}, {"action": "push_and_pr", **HUMAN}],
                       gates=[{"gate": g, "status": "PASS"} for g in
                              ("quality_gate", "deterministic_validation", "change_scope", "secrets",
                               "dco", "branch_scope")])
    res = guard.check_shell("git push --force origin HEAD", m)
    assert res.status == "FAIL" and "GRD-002" in ids(res)


def test_write_outside_approved_paths_blocked(tmp_path):
    m = write_manifest(tmp_path, current_state="IMPLEMENTATION")
    res = guard.check_write("src/main/java/X.java", m, cwd=str(tmp_path))
    assert res.status == "FAIL" and "GRD-004" in ids(res)
    assert guard.check_write("docs/guide.md", m, cwd=str(tmp_path)).status == "PASS"


def test_write_outside_cwd_matches_from_the_git_top(tmp_path):
    repo = tmp_path / "repo"
    (repo / "ui-test").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    m = write_manifest(tmp_path, current_state="IMPLEMENTATION",
                       scope={"module": "svc", "repositories": [], "approved_paths": [".crewline/qa/ui-test-context.md"]})
    target = repo / ".crewline" / "qa" / "ui-test-context.md"                   # not created yet
    # the session sits in ui-test/: 0.6.0 matched the bare name and refused an approved path
    assert guard.check_write(str(target), m, cwd=str(repo / "ui-test")).status == "PASS"
    res = guard.check_write(str(repo / ".crewline/other.md"), m, cwd=str(repo / "ui-test"))
    assert res.status == "FAIL" and "GRD-004" in ids(res)
    assert ".crewline/other.md" in res.to_text()


def test_write_without_task_is_allowed():
    assert guard.check_write("src/main/java/X.java", None).status == "PASS"


def test_raw_suite_and_log_dump_are_blocked():
    assert "GRD-007" in ids(guard.check_shell("mvn -B verify"))
    assert "GRD-007" in ids(guard.check_shell("cat api-full-report.html"))
    assert guard.check_shell("python -m crewline run --repo . --out e.json -- mvn -B test").status == "PASS"


def test_read_of_dump_or_huge_file_is_blocked(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("x", encoding="utf-8")
    assert "GRD-007" in ids(guard.check_read(str(log)))
    big = tmp_path / "Huge.java"
    big.write_bytes(b"x" * (guard.READ_MAX_BYTES + 1))
    assert "GRD-008" in ids(guard.check_read(str(big)))
    small = tmp_path / "Svc.java"
    small.write_text("class Svc {}", encoding="utf-8")
    assert guard.check_read(str(small)).status == "PASS"


def test_hook_read_mode_and_detect():
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": {"path": "build.log"}}
    assert hook.detect_mode(payload) == "read"
    out = hook.handle("read", payload)
    assert out["permission"] == "deny" and "GRD-007" in out["agent_message"]


def test_hook_denies_force_push_json():
    out = hook.handle("shell", {"command": "git push --force"})
    assert out["permission"] == "deny"
    assert "GRD-002" in out["agent_message"]


def test_hook_claude_protocol_and_bash_payload():
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_input": {"command": "git push --force origin HEAD"}}
    cursor = hook.handle("shell", payload)
    claude = hook.to_claude(cursor, "PreToolUse")
    assert claude["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "GRD-002" in claude["hookSpecificOutput"]["permissionDecisionReason"]
    assert hook.detect_protocol(payload, False) == "claude"
    assert hook.detect_mode(payload) == "shell"


def test_hook_session_injects_context():
    out = hook.handle("session", {})
    assert "Crewline hooks are active" in out["additional_context"]


def test_git_hook_exit_denies_commit_without_task(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CREWLINE_TASK_MANIFEST", raising=False)
    assert hook.git_exit("git-commit", []) == 1
    assert hook.git_exit("git-push", []) == 1


def test_cli_guard_shell():
    proc = subprocess.run(
        [sys.executable, "-m", "crewline", "--json", "guard", "--shell", "git stash"],
        cwd=str(ROOT / "validators"), capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 1
    assert json.loads(proc.stdout)["status"] == "FAIL"


def _install(tmp_path, platform):
    proc = subprocess.run(
        [sys.executable, str(ROOT / "adapters" / "install.py"), "--platform", platform,
         "--target", str(tmp_path), "--write"],
        capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    return proc


def test_install_cursor_writes_hooks(tmp_path):
    _install(tmp_path, "cursor")
    hooks = json.loads((tmp_path / ".cursor" / "hooks.json").read_text(encoding="utf-8"))
    assert hooks["hooks"]["beforeShellExecution"][0]["failClosed"] is True
    trampoline = (tmp_path / ".cursor" / "hooks" / "crewline_hook.py").read_text(encoding="utf-8")
    assert "{{CORE}}" not in trampoline
    mdc = (tmp_path / ".cursor" / "rules" / "crewline.mdc").read_text(encoding="utf-8")
    assert "alwaysApply: true" in mdc


def test_install_claude_writes_hooks_and_deny_rules(tmp_path):
    _install(tmp_path, "claude")
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))
    deny = settings["permissions"]["deny"]
    assert "Bash(git push --force *)" in deny
    matchers = [g.get("matcher") for g in settings["hooks"]["PreToolUse"]]
    assert "Bash|PowerShell" in matchers and "Write|Edit" in matchers and "Read" in matchers
    skill = (tmp_path / ".claude" / "skills" / "crewline" / "SKILL.md").read_text(encoding="utf-8")
    assert "{{CORE}}" not in skill
    assert "crewline guard" in skill
    trampoline = (tmp_path / ".claude" / "hooks" / "crewline_hook.py").read_text(encoding="utf-8")
    assert "{{CORE}}" not in trampoline


def test_install_sync_rewrites_drift(tmp_path):
    _install(tmp_path, "cursor")
    mdc = tmp_path / ".cursor" / "rules" / "crewline.mdc"
    mdc.write_text("# tampered\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "adapters" / "install.py"), "--platform", "cursor",
         "--target", str(tmp_path), "--sync", "--check"],
        capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "# tampered" not in mdc.read_text(encoding="utf-8")


def test_install_check_detects_drift(tmp_path):
    _install(tmp_path, "cursor")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "adapters" / "install.py"), "--platform", "cursor",
         "--target", str(tmp_path), "--check"],
        capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    mdc = tmp_path / ".cursor" / "rules" / "crewline.mdc"
    mdc.write_text(mdc.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "adapters" / "install.py"), "--platform", "cursor",
         "--target", str(tmp_path), "--check"],
        capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode != 0 and "drift" in (proc.stdout + proc.stderr)


def test_install_generic_writes_git_hooks(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    _install(tmp_path, "generic")
    entry = (tmp_path / ".crewline" / "ENTRY.md").read_text(encoding="utf-8")
    assert "{{CORE}}" not in entry
    assert "crewline guard" in entry
    pre = (tmp_path / ".git" / "hooks" / "pre-commit").read_text(encoding="utf-8")
    assert "git-commit" in pre
    assert "{{HOOK}}" not in pre
    assert (tmp_path / ".crewline" / "hooks" / "crewline_hook.py").is_file()


# --- plugin mode: rails only where .crewline/ exists, tasks in the module clone -------------------------------------
def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def _module(tmp_path, onboarded=True):
    r = tmp_path / "mod"
    r.mkdir()
    _git(tmp_path, "init", "-q", str(r))
    _git(r, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init")
    if onboarded:
        (r / ".crewline").mkdir()
        (r / ".crewline/module.yaml").write_text("module: mod\nroles:\n  development: []\n", encoding="utf-8")
    return r


def _plugin(mode, cwd, command=""):
    payload = {"hook_event_name": "SessionStart" if mode == "session" else "PreToolUse", "cwd": str(cwd)}
    if command:
        payload.update(tool_name="Bash", tool_input={"command": command})
    return hook.handle_plugin(mode, payload)


def test_plugin_hook_leaves_repos_without_crewline_alone(tmp_path, monkeypatch):
    monkeypatch.delenv("CREWLINE_TASKS", raising=False)
    monkeypatch.delenv("CREWLINE_TASK_MANIFEST", raising=False)
    r = _module(tmp_path, onboarded=False)
    assert _plugin("shell", r, "git commit -m x")["permission"] == "allow"
    assert _plugin("shell", r, "git push --force")["permission"] == "allow"        # not our repo: not our rules
    assert _plugin("session", r) == {"additional_context": ""}                    # silent: prints nothing


def test_plugin_hook_enforces_rails_in_onboarded_repo(tmp_path, monkeypatch):
    monkeypatch.delenv("CREWLINE_TASKS", raising=False)
    monkeypatch.delenv("CREWLINE_TASK_MANIFEST", raising=False)
    r = _module(tmp_path)
    out = _plugin("shell", r, "git commit -s -m x")
    assert out["permission"] == "deny" and "GRD-001" in out["agent_message"]
    assert _plugin("shell", r, "git push --force")["permission"] == "deny"
    assert _plugin("shell", r, "git status")["permission"] == "allow"
    ctx = _plugin("session", r)["additional_context"]
    assert "Crewline is ACTIVE" in ctx and "Crewline hooks are active" in ctx and ".crewline-work" in ctx


def test_plugin_hook_off_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("CREWLINE", "off")
    r = _module(tmp_path)
    assert _plugin("shell", r, "git commit -s -m x")["permission"] == "allow"
    assert _plugin("session", r) == {"additional_context": ""}


def test_tasks_live_in_the_main_clone_also_from_a_worktree(tmp_path, monkeypatch):
    from crewline import workspace
    monkeypatch.delenv("CREWLINE_TASKS", raising=False)
    r = _module(tmp_path)
    (r / ".crewline/module.yaml").write_text("module: mod\n", encoding="utf-8")
    _git(r, "add", ".crewline")
    _git(r, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "-m", "onboard")
    wt = r / ".crewline-work/worktrees/T-1"
    _git(r, "worktree", "add", "-q", "-b", "t1", str(wt))
    expected = (r / ".crewline-work/tasks").resolve()
    assert workspace.tasks_root(r).resolve() == expected
    assert workspace.tasks_root(wt).resolve() == expected          # one clone, one task list
    man = expected / "T-1/task-manifest.json"
    man.parent.mkdir(parents=True)
    man.write_text(json.dumps({"task_id": "T-1", "current_state": "IMPLEMENTATION"}), encoding="utf-8")
    assert guard.find_active_manifest(cwd=str(wt)).resolve() == man.resolve()
    assert guard.check_write(str(man), None, str(wt)).status == "PASS"   # the task workspace is always writable
    assert workspace.tasks_root(tmp_path).resolve() == (workspace.CORE / "tasks").resolve()   # no .crewline/: core


def test_cli_hook_passthrough_prints_claude_json(tmp_path):
    r = _module(tmp_path)
    p = subprocess.run([sys.executable, "-m", "crewline", "hook", "--claude", "--plugin", "shell"],
                       input=json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(r),
                                         "tool_input": {"command": "git push --force"}}),
                       capture_output=True, text=True, cwd=str(ROOT / "validators"),
                       env={**__import__("os").environ, "CREWLINE": ""})
    out = json.loads(p.stdout)
    assert p.returncode == 0 and out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_plugin_hooks_cover_bash_powershell_and_writes():
    spec = json.loads((ROOT / "hooks/hooks.json").read_text(encoding="utf-8"))["hooks"]
    pre = {g["matcher"]: g["hooks"][0]["command"] for g in spec["PreToolUse"]}
    assert pre["Bash|PowerShell"].endswith("hook --claude --plugin shell")
    assert pre["Write|Edit"].endswith("hook --claude --plugin write")
    assert pre["Read"].endswith("hook --claude --plugin read")
    assert spec["SessionStart"][0]["hooks"][0]["command"].endswith("hook --claude --plugin session")


def test_session_names_version_and_flags_a_task_started_on_another(tmp_path, monkeypatch):
    from crewline import version
    monkeypatch.delenv("CREWLINE", raising=False)
    monkeypatch.delenv("CREWLINE_TASK_MANIFEST", raising=False)
    monkeypatch.setenv("CREWLINE_TASKS", str(tmp_path / "tasks"))
    r = _module(tmp_path)
    ctx = _plugin("session", r)["additional_context"]
    assert f"Crewline {version.current()}." in ctx and "started on" not in ctx
    man = tmp_path / "tasks/T-1/task-manifest.json"
    man.parent.mkdir(parents=True)
    man.write_text(json.dumps({"task_id": "T-1", "current_state": "IMPLEMENTATION", "crewline_version": "0.0.1"}),
                   encoding="utf-8")
    ctx = _plugin("session", r)["additional_context"]
    assert "started on Crewline 0.0.1" in ctx and "CHANGELOG.md" in ctx


def test_marketplace_catalog_at_repo_root_points_at_the_plugin():
    """Installed from GitHub a relative plugin source resolves against the repository root, so the catalog lives there."""
    repo = ROOT.parent
    cat = json.loads((repo / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
    entry = next(p for p in cat["plugins"] if p["name"] == "crewline")
    assert (repo / entry["source"] / ".claude-plugin/plugin.json").is_file()
    assert not (ROOT / ".claude-plugin/marketplace.json").exists()
    src = json.loads((ROOT / "templates/module-claude-settings.json").read_text(encoding="utf-8"))
    src = src["extraKnownMarketplaces"]["mosip-labs"]["source"]
    assert "path" not in src and set(src["sparsePaths"]) == {".claude-plugin", "crewline"}


def test_installing_the_plugin_enables_it():
    """A local-scope install writes the plugin's default into settings.local.json, which overrides the module's opt-in:
    with defaultEnabled false a new user followed the README and still had Crewline off."""
    assert json.loads((ROOT / ".claude-plugin/plugin.json").read_text(encoding="utf-8")).get("defaultEnabled", True) is True


def test_pr_title_must_start_with_tracker_key(tmp_path):
    """mosip/pre-registration#1089 format: `MOSIP-38854: Handle missing identity name fields`."""
    ready = dict(current_state="COMMIT_PR",
                 approvals=[{"action": "commit_pr", **HUMAN}, {"action": "push_and_pr", **HUMAN}],
                 gates=[{"gate": g, "status": "PASS"} for g in
                        ("quality_gate", "deterministic_validation", "change_scope", "secrets", "dco", "branch_scope")])
    m = write_manifest(tmp_path, **ready)
    for bad in ('gh pr create --title "Fix notification NPE"', "gh pr create --fill",
                'gh pr create --title "Fix MOSIP-1 in notification"', 'gh pr edit 7 --title "fix"'):
        res = guard.check_shell(bad, m)
        assert res.status == "FAIL" and "GRD-009" in ids(res), bad
    for good in ('gh pr create --title "MOSIP-1: Handle missing name" --body "Closes MOSIP-1"',
                 "gh pr create -t 'MOSIP-1 : Negative coverage' -b 'Refs MOSIP-1'", 'gh pr edit 7 --title="MOSIP-1: renamed"'):
        assert "GRD-009" not in ids(guard.check_shell(good, m)), good
    gh = write_manifest(tmp_path, tracker={"system": "github", "key": "inji/pixelpass#185", "confirmed_by": "mohan"}, **ready)
    assert "GRD-009" not in ids(guard.check_shell('gh pr create --title "#185: Python SDK" --body "Closes #185"', gh))
    assert "GRD-009" not in ids(guard.check_shell(
        'gh pr create --title "inji/pixelpass#185: Python SDK" --body "Closes inji/pixelpass#185"', gh))
    waived = write_manifest(tmp_path, tracker={"waived": {"action": "tracker_waiver", **HUMAN}}, **ready)
    assert "GRD-009" not in ids(guard.check_shell('gh pr create --title "anything"', waived))
    assert guard.check_shell('gh pr edit 7 --title "fix"', None).status == "PASS"     # no active task: not a shipping action


def test_pr_body_must_link_the_ticket(tmp_path):
    ready = dict(current_state="COMMIT_PR",
                 approvals=[{"action": "commit_pr", **HUMAN}, {"action": "push_and_pr", **HUMAN}],
                 gates=[{"gate": g, "status": "PASS"} for g in
                        ("quality_gate", "deterministic_validation", "change_scope", "secrets", "dco", "branch_scope")])
    url = "https://mosip.atlassian.net/browse/MOSIP-1"
    m = write_manifest(tmp_path, tracker={"system": "jira", "key": "MOSIP-1", "url": url, "confirmed_by": "mohan"}, **ready)
    title = '--title "MOSIP-1: x"'
    for bad in (f"gh pr create {title}", f'gh pr create {title} --body "fixes the NPE"', f"gh pr create {title} -F -",
                f"gh pr create {title} --body-file {(tmp_path / 'missing.md').as_posix()}", 'gh pr edit 7 --body "no link"'):
        res = guard.check_shell(bad, m)
        assert "GRD-009" in ids(res) and "body" in res.to_text(), bad
    body = tmp_path / "body.md"
    body.write_text(f"Closes {url}\n\nscope ...", encoding="utf-8")
    for good in (f'gh pr create {title} --body "Refs MOSIP-1"', f"gh pr create {title} --body-file {body.as_posix()}",
                 f"gh pr edit 7 -F {body.as_posix()}", "gh pr edit 7 --add-label bug"):
        assert "GRD-009" not in ids(guard.check_shell(good, m)), good
