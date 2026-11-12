"""crewline - deterministic validators for the Crewline.

Workflow
  python -m crewline discover      --repo <module repo> --module <name> [--out <dir>] [--force]   # module onboarding
  python -m crewline manifest     <task-manifest.json> [--to <STATE> [--apply --note <text>]]
  python -m crewline tools         [--only <capability>]
  python -m crewline pack          [--list | --set <id>]          # product pack (default mosip)
Development (any repository)
  python -m crewline run           --repo <dir> --out <record.json> [--report <path>] [--cwd <dir>] -- <command...>
  python -m crewline clip          --file <log|-> [--max-chars N]     # digest a log; never load the raw file
  python -m crewline evidence      --record <record.json> --repo <dir>
  python -m crewline change-scope  --repo <dir> --base <ref> --manifest <task-manifest.json>
  python -m crewline test-integrity --repo <dir> --base <ref> [--approvals <yaml>] [--strict]
  python -m crewline migration     --repo <dir> --base <ref>
  python -m crewline trace         <task-manifest.json>
  python -m crewline coding-standards --repo <dir> --base <ref> --manifest <m> [--evidence <record.json>]... [--changed-lines]
  python -m crewline robustness      --repo <dir> --base <ref> --manifest <m>
  python -m crewline requirement-clarity --manifest <task-manifest.json>
  python -m crewline requirement-fit --repo <dir> --base <ref> --manifest <m>
  python -m crewline branch-env        --manifest <m>
  python -m crewline defect-reports    --manifest <m>
  python -m crewline adapter-check     --platform cursor|claude|generic --target <workspace> [--sync]
  python -m crewline evals             [--run] [--pack frozen]
  python -m crewline cold-start        # new-machine kit (frozen pack, adapters, synthetic --apply)
  python -m crewline manifest      <m> --next                   # every legal move, each precondition with its state
  python -m crewline status        [<id>]                       # state, repair attempts left, legal moves, open approvals
  python -m crewline review record --review <name> --result <PASS|FINDINGS|WAIVED> --reviewer <ctx> [--id <id>]
  python -m crewline approval add  --for <action> --approver <person> --reason <r> --scope <s> [--exception --gate <g>] [--follow-up <key>]
  python -m crewline limitation add --lim <KL-id> [--text <t>] [--ref <file:line>] [--follow-up <key> | --no-follow-up <reason>]
  python -m crewline ci-coverage   --manifest <m>               # test suites this task ran that no CI workflow runs
  python -m crewline resume        [<id>]                       # generate HANDOVER.md from the manifest; make the task active
  python -m crewline gates refresh [--id <id>] [--state <S> | --all] [--rerun]   # re-run and re-record gates a command decides
  python -m crewline task              list | status --id <id> | resume --id <id> | new --type <type> --id <id>
                                       | activate --id <id> | deactivate | worktree --id <id> [--base <ref> --branch <name>]
                                       | export --id <id> --out <dir-or-zip> | import --from <dir-or-zip> [--id <id>]
  python -m crewline diff-coverage --repo <dir> --base <ref> --jacoco <jacoco.xml|dir> [--threshold 80]
  python -m crewline usage         --manifest <m> (--mark "<step>" | --transcript <session.jsonl> [--out <json>])
  python -m crewline regression    --baseline <report|surefire dir> --candidate <report|surefire dir> [--allow-fail <id>]
Git / PR
  python -m crewline secrets       <path>... [--base-dir <dir>] [--no-gitleaks]
  python -m crewline dco           --repo <dir> --range <base>..<head>
  python -m crewline branch        --repo <dir> --upstream upstream/<branch> --base <sha> (--story <KEY> | --manifest <m>) [--allow <sha>] [--upstream-repo o/r --pr-repo o/r --pr-base <b>]
  python -m crewline version       [--repo <git root> --base <ref>]   (shipped changes must raise the plugin version)
  python -m crewline guard         --shell "<cmd>" | --write <path> [--manifest <m>] [--cwd <dir>]
                                    (platform hooks call this so commit/push/PR cannot skip the gates)
SDET (API TestRig — MOSIP pack; NOT_RUN when sdet.testrig/coverage_cli is false)
  python -m crewline testcase      --module-root <api-test> [--profile <yaml>] [--base <ref>] [--scope all|changed]
                                    (profile default: <module repo>/.crewline/qa/profile.yaml)
  python -m crewline expected-diff --module-root <api-test> --profile <yaml> --base <ref> [--approvals <yaml>]
  python -m crewline scope         --module-root <api-test> --profile <yaml> --ref <last fully-regressed commit>
  python -m crewline coverage      --commons-dir <dir> --module-root <api-test> --module-code <CODE> [--offline]

Exit code 0 PASS, 1 FAIL, 2 NOT_RUN. Add --json for machine-readable output.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from . import workspace
from . import (adapter_check, branch, branch_env, change_scope, clip, coding_standards, cold_start, coverage, dco, defect_reports, diff_coverage, discover, evals_run, evidence, expected_diff, guard, manifest, migration, pack, regression, requirement_clarity, requirement_fit, robustness, scope,
               secrets_scan, task, test_integrity, testcase, tools, traceability, usage)
from .apitestrig import Module, load_profile
from .result import EXIT, Result


def module_profile(module_root: str, profile: str | None) -> str:
    """The module's own profile unless one is given: <repo top>/.crewline/qa/profile.yaml."""
    if profile:
        return profile
    import subprocess
    top = subprocess.run(["git", "-C", module_root, "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                         encoding="utf-8").stdout.strip()
    ctx = workspace.context_dir(top or module_root) or Path(top or module_root) / ".crewline"
    path = ctx / "qa" / "profile.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found: onboard the module (module-onboarding) or pass --profile")
    return str(path)


def task_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--id", dest="task_id", help="default: the active task"); p.add_argument("--manifest")


def _task_manifest(manifest_path: str | None, task_id: str | None) -> str:
    if manifest_path:
        return manifest_path
    if task_id:
        path = task.tasks_root() / task_id / "task-manifest.json"
        return str(path) if path.is_file() else ""
    return str(workspace.active_manifest()[0] or "")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command: list[str] = []
    if "--" in argv:                          # `run ... -- <command>`: everything after -- is the command
        i = argv.index("--")
        argv, command = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(prog="crewline", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def module_args(p):
        p.add_argument("--module-root", required=True)
        p.add_argument("--profile", help="default: <module repo>/.crewline/qa/profile.yaml")

    def repo_base(p):
        p.add_argument("--repo", required=True)
        p.add_argument("--base", required=True)

    p = sub.add_parser("testcase"); module_args(p)
    p.add_argument("--base"); p.add_argument("--scope", choices=["all", "changed"])
    p = sub.add_parser("expected-diff"); module_args(p)
    p.add_argument("--base", required=True); p.add_argument("--approvals")
    p = sub.add_parser("secrets")
    p.add_argument("paths", nargs="+"); p.add_argument("--base-dir", default="."); p.add_argument("--no-gitleaks", action="store_true")
    p = sub.add_parser("dco")
    p.add_argument("--repo", required=True); p.add_argument("--range", required=True)
    p = sub.add_parser("coverage")
    p.add_argument("--commons-dir", required=True); p.add_argument("--module-root", required=True)
    p.add_argument("--module-code", required=True); p.add_argument("--offline", action="store_true")
    p = sub.add_parser("branch")
    p.add_argument("--repo", required=True); p.add_argument("--upstream", required=True, help="e.g. upstream/develop")
    p.add_argument("--base", required=True, help="upstream base commit recorded at REPO_READY")
    p.add_argument("--story", action="append", default=[], help="task key(s): Jira PROJ-1, GitHub #N or owner/repo#N")
    p.add_argument("--manifest", help="read tracker.key + requirements.story_keys"); p.add_argument("--allow", action="append", default=[])
    p.add_argument("--upstream-repo"); p.add_argument("--pr-repo"); p.add_argument("--pr-base")
    p = sub.add_parser("tools"); p.add_argument("--only", action="append", default=[])
    p.add_argument("--task-type", dest="tools_task_type", help="probe only what this task type needs (default: active task's)")
    p = sub.add_parser("pack")
    p.add_argument("--list", action="store_true", help="list pack ids")
    p.add_argument("--set", dest="set_pack", help="write packs/ACTIVE (committed default is mosip)")
    p = sub.add_parser("scope"); module_args(p)
    p.add_argument("--ref", required=True, help="last commit that passed a full regression")
    p = sub.add_parser("regression")
    p.add_argument("--baseline", required=True, help="TestRig full-report / surefire dir of the base run")
    p.add_argument("--candidate", required=True, help="same, for the branch run")
    p.add_argument("--allow-fail", action="append", default=[], help="test id with an attributed gate exception")
    p.add_argument("--out", help="also write the result JSON here (evidence/full-regression[-<suite>].json, REG-007)")
    p.add_argument("--baseline-from-user", dest="baseline_from_user", action="store_true",
                   help="--baseline is a run the user already has (same env); saves the base run (REG-010)")
    p.add_argument("--approver", dest="reg_approver", help="with --baseline-from-user: who supplied the base report")
    p.add_argument("--max-age-days", dest="max_age_days", type=float, default=14)
    p.add_argument("--manifest", dest="reg_manifest",
                   help="honour the task's regression deferrals (regression-decision --defer) as approved")
    p = sub.add_parser("creds", help="merge the user's env values into a worktree config for a run (keys only), and restore it")
    p.add_argument("creds_action", choices=["merge", "restore"])
    p.add_argument("--from", dest="creds_from", help="for merge: the values file (key=value or key: value)")
    p.add_argument("--into", dest="creds_into", action="append", default=[], required=True,
                   help="the worktree's config file (repeat for base and branch)")
    p = sub.add_parser("regression-decision", help="the user's one decision on the full regression: waive, fix, or defer to a bug")
    p.add_argument("--manifest", required=True)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--waive", dest="rgd_mode", action="store_const", const="waive",
                      help="skip the full regression (no code change, or the user's explicit waiver)")
    mode.add_argument("--fix", dest="rgd_mode", action="store_const", const="fix", help="approve fixing the new breaks")
    mode.add_argument("--defer", dest="rgd_mode", action="store_const", const="defer",
                      help="raise a bug and defer every new break in --record with one approval")
    p.add_argument("--approver", help="the person who decided (never an agent)")
    p.add_argument("--reason")
    p.add_argument("--repo", dest="rgd_repo", help="for --waive: check the diff (no code change)")
    p.add_argument("--base", dest="rgd_base", help="for --waive: the base ref of the diff")
    p.add_argument("--bug", help="for --defer: the Jira key / GitHub issue already raised")
    p.add_argument("--record", help="for --defer: evidence/full-regression*.json with the new breaks")
    p.add_argument("--class", dest="rgd_class", default="APPLICATION", help="for --defer: defect class (not CODE_UNDER_CHANGE)")
    p.add_argument("--follow-up", dest="rgd_follow_up", help="for --waive: ticket that closes what was waived")
    p = sub.add_parser("run")
    p.add_argument("--repo", required=True); p.add_argument("--out", required=True)
    p.add_argument("--report"); p.add_argument("--cwd")
    p.add_argument("--env", action="append", default=[], metavar="KEY=VALUE",
                   help="set for this run only, e.g. JAVA_HOME=<jdk> (its bin/ leads PATH); the record keeps the names")
    p.add_argument("--timeout", type=float, metavar="MINUTES", help="stop the run after this many minutes (EVD-005)")
    p.add_argument("--fail-on", dest="fail_on", metavar="REGEX",
                   help="stop as soon as the log matches, e.g. 'HTTP 401|NotAuthorizedException' for a TestRig login")
    p = sub.add_parser("clip")
    p.add_argument("--file", help="log path, or - for stdin (omit when a command follows --)")
    p.add_argument("--max-chars", type=int, dest="max_chars")
    p = sub.add_parser("evidence")
    p.add_argument("--record", required=True); p.add_argument("--repo", required=True)
    p = sub.add_parser("change-scope"); repo_base(p); p.add_argument("--manifest", required=True)
    p = sub.add_parser("test-integrity"); repo_base(p)
    p.add_argument("--approvals"); p.add_argument("--strict", action="store_true")
    p = sub.add_parser("migration"); repo_base(p)
    p = sub.add_parser("trace"); p.add_argument("manifest")
    p = sub.add_parser("coding-standards"); repo_base(p); p.add_argument("--manifest", required=True)
    p.add_argument("--changed-lines", action="store_true", help="a checker failure counts only on lines this change touched")
    p.add_argument("--evidence", action="append", default=[], help="crewline run record for a checker (repeatable)")
    p = sub.add_parser("robustness"); repo_base(p); p.add_argument("--manifest", required=True)
    p = sub.add_parser("requirement-clarity"); p.add_argument("--manifest", required=True)
    p = sub.add_parser("requirement-fit"); repo_base(p); p.add_argument("--manifest", required=True)
    p = sub.add_parser("branch-env"); p.add_argument("--manifest", required=True)
    p = sub.add_parser("defect-reports"); p.add_argument("--manifest", required=True)
    p = sub.add_parser("adapter-check")
    p.add_argument("--platform", choices=["claude", "cursor", "generic"], required=True)
    p.add_argument("--target", required=True)
    p.add_argument("--sync", action="store_true", help="re-render from adapters/ then check")
    p = sub.add_parser("evals")
    p.add_argument("--run", dest="run_evals", action="store_true")
    p.add_argument("--pack", dest="eval_pack", choices=["frozen"], help="official 8-story pack + expected-outcomes/")
    sub.add_parser("cold-start")
    p = sub.add_parser("start", help="conductor: next --to for a task (not the crew)")
    p.add_argument("--id", dest="task_id", required=True)
    p.add_argument("--type", dest="task_type")
    p = sub.add_parser("task")
    p.add_argument("action", choices=["list", "status", "resume", "new", "start", "export", "import",
                                      "activate", "deactivate", "worktree"])
    p.add_argument("--id", dest="task_id")
    p.add_argument("--base", dest="task_base", help="for worktree: upstream ref to cut the branch from, e.g. upstream/develop")
    p.add_argument("--branch", dest="task_branch", help="for worktree: new branch name")
    p.add_argument("--type", dest="task_type", help="for `new`: feature, bug-fix, ...")
    p.add_argument("--repo", dest="task_repos", action="append", default=[],
                   help="for `new`: a clone the task changes (repeat; first = primary). Fills scope.repositories, "
                        "keeps the task in <first clone>/.crewline-work/tasks")
    p.add_argument("--out", dest="task_out", help="for export: directory or .zip")
    p.add_argument("--from", dest="task_from", help="for import: directory or .zip")
    p = sub.add_parser("status", help="state, repair attempts left, legal moves, open approvals")
    p.add_argument("task_id", nargs="?", help="default: the active task")
    p = sub.add_parser("review", help="review record: write a review result without hand-editing the manifest")
    p.add_argument("action", choices=["record"]); task_arg(p)
    p.add_argument("--review", required=True, help="implementation_review, fresh_context_review or security_review")
    p.add_argument("--result", required=True, help="PASS, FINDINGS, WAIVED (security_review: NOT_APPLICABLE)")
    p.add_argument("--reviewer", help="context id of the reviewer")
    p = sub.add_parser("approval", help="approval add: record what a person decided")
    p.add_argument("action", choices=["add"]); task_arg(p)
    p.add_argument("--for", dest="for_action", required=True, help="the action approved, e.g. commit_pr")
    p.add_argument("--approver", required=True); p.add_argument("--reason", required=True); p.add_argument("--scope", required=True)
    p.add_argument("--exception", action="store_true", help="a gate exception (with --gate)"); p.add_argument("--gate")
    p.add_argument("--follow-up"); p.add_argument("--no-follow-up")
    p = sub.add_parser("limitation", help="limitation add: a known limitation and its follow-up")
    p.add_argument("action", choices=["add"]); task_arg(p)
    p.add_argument("--lim", required=True, help="limitation id, e.g. KL-PYSDK-001"); p.add_argument("--text")
    p.add_argument("--ref", action="append", default=[]); p.add_argument("--follow-up"); p.add_argument("--no-follow-up")
    p = sub.add_parser("ci-coverage", help="test suites this task ran that no CI workflow runs")
    p.add_argument("--manifest", required=True)
    p = sub.add_parser("resume", help="write and print the hand-over of a task from its manifest, and make it active")
    p.add_argument("task_id", nargs="?", help="default: the active task")
    p = sub.add_parser("gates", help="gates refresh: re-run and re-record the gates a command decides")
    p.add_argument("action", choices=["refresh"])
    p.add_argument("--id", dest="task_id", help="default: the active task"); p.add_argument("--manifest")
    p.add_argument("--state", help="default: the task's current state")
    p.add_argument("--all", dest="every", action="store_true", help="every gate a command decides, on any state")
    p.add_argument("--rerun", action="store_true", help="run the recorded build/test commands again first (not system tests)")
    sub.add_parser("session-start", help="SessionStart hook (prints hook JSON)")
    sub.add_parser("hook", help="platform hook: crewline hook [--claude|--cursor] [--plugin] session|shell|write (stdin JSON)")
    sub.add_parser("cursor-rule", help="print the Crewline rule for a module that uses .cursor/crewline.py")
    p = sub.add_parser("discover")
    p.add_argument("--repo", required=True); p.add_argument("--module", required=True)
    p.add_argument("--out"); p.add_argument("--force", action="store_true")
    p = sub.add_parser("diff-coverage"); repo_base(p)
    p.add_argument("--jacoco", required=True, help="jacoco.xml or a directory searched for jacoco.xml")
    p.add_argument("--threshold", type=float, default=80.0); p.add_argument("--include", help="default: every changed source file that is not a test")
    p = sub.add_parser("usage")
    p.add_argument("--manifest", required=True); p.add_argument("--mark", help="start a named step now")
    p.add_argument("--transcript", action="append", default=[]); p.add_argument("--out")
    p = sub.add_parser("version", help="print the Crewline version; with --base, check that shipped changes raise it")
    p.add_argument("--repo", help="git checkout that contains crewline/ (default: this Crewline)")
    p.add_argument("--base", help="base ref of the pull request, e.g. origin/develop")
    p = sub.add_parser("guard")
    p.add_argument("--shell", help="classify and gate a shell command (git commit/push, gh pr create, ...)")
    p.add_argument("--write", dest="write_path", help="gate a file write against scope.approved_paths")
    p.add_argument("--read", dest="read_path", help="gate a Read of a dump file or oversized file")
    p.add_argument("--manifest", help="default: CREWLINE_TASK_MANIFEST or latest tasks/<id>/task-manifest.json")
    p.add_argument("--cwd", help="workspace root for relative write paths")
    p = sub.add_parser("manifest")
    p.add_argument("manifest"); p.add_argument("--to")
    p.add_argument("--next", action="store_true", help="every legal move and each precondition with its current state")
    p.add_argument("--apply", action="store_true", help="record the transition if every guard passes")
    p.add_argument("--note", default="")

    raw = list(sys.argv[1:] if argv is None else argv)
    if raw[:1] == ["cursor-rule"]:                # the live Cursor rule (.cursor/crewline.py rule)
        from . import cursor_rule
        return cursor_rule.run()
    if raw[:1] == ["hook"]:                       # platform hook protocol: stdin JSON -> stdout JSON (hook.py)
        from . import hook
        return hook.main(raw[1:])
    a = ap.parse_args(argv)
    if a.cmd == "session-start":
        from . import session
        return session.run()
    try:
        if a.cmd == "testcase":
            if not pack.uses_testrig():
                res = Result.not_run("testcase", f"pack {pack.active_id()} has sdet.testrig=false")
            else:
                case_scope = a.scope or ("changed" if a.base else "all")
                res = testcase.run(Module(a.module_root, load_profile(module_profile(a.module_root, a.profile))), case_scope, a.base)
        elif a.cmd == "expected-diff":
            if not pack.uses_testrig():
                res = Result.not_run("expected-diff", f"pack {pack.active_id()} has sdet.testrig=false")
            else:
                res = expected_diff.run(Module(a.module_root, load_profile(module_profile(a.module_root, a.profile))), a.base, a.approvals)
        elif a.cmd == "secrets":
            res = secrets_scan.run(a.paths, a.base_dir, not a.no_gitleaks)
        elif a.cmd == "dco":
            res = dco.run(a.repo, a.range)
        elif a.cmd == "branch":
            keys = a.story + (branch.manifest_keys(a.manifest) if a.manifest else [])
            res = branch.run(a.repo, a.upstream, a.base, keys, a.allow, a.pr_repo, a.pr_base, a.upstream_repo)
        elif a.cmd == "tools":
            res = tools.run(a.only, a.tools_task_type)
        elif a.cmd == "pack":
            if a.list:
                res = Result("pack", inputs={"packs": pack.list_ids(), "active": pack.active_id()})
                res.add("PCK-002", "INFO", f"packs: {', '.join(pack.list_ids())}; active={pack.active_id()}")
                res = res.finish()
            else:
                res = pack.run(a.set_pack)
        elif a.cmd == "scope":
            if not pack.uses_testrig():
                res = Result.not_run("scope", f"pack {pack.active_id()} has sdet.testrig=false")
            else:
                res = scope.run(Module(a.module_root, load_profile(module_profile(a.module_root, a.profile))), a.ref)
        elif a.cmd == "regression":
            allow = dict.fromkeys(a.allow_fail, "quality-gate exception")
            if a.reg_manifest:
                from . import regression_decision
                allow.update(regression_decision.deferred_tests(json.loads(Path(a.reg_manifest).read_text(encoding="utf-8"))))
            res = regression.run(a.baseline, a.candidate, allow)
            if a.baseline_from_user and res.status != "NOT_RUN":
                regression.user_baseline(res, a.reg_approver, a.max_age_days)
                res.status, res.summary = "PASS", ""
                res = res.finish()
            if a.out:
                from . import systest
                base_copy, cand_copy = (regression.snapshot(res, a.out) if res.status != "NOT_RUN"   # the base checkout
                                        else (a.baseline, a.candidate))                          # can go now (principle 9)
                res.inputs.update(baseline_sha256=systest.report_sha256(base_copy),
                                  candidate_sha256=systest.report_sha256(cand_copy),
                                  commit=systest.repo_head(a.candidate),     # REG-007 checks these
                                  worktree_digest=systest.repo_digest(a.candidate))
                Path(a.out).parent.mkdir(parents=True, exist_ok=True)
                Path(a.out).write_text(res.to_json(), encoding="utf-8")
        elif a.cmd == "creds":
            from . import creds
            res = (creds.merge(a.creds_from, a.creds_into) if a.creds_action == "merge" and a.creds_from
                   else creds.restore(a.creds_into) if a.creds_action == "restore"
                   else Result.not_run("creds", "merge needs --from <values file>"))
        elif a.cmd == "regression-decision":
            from . import regression_decision
            res = regression_decision.run(a.manifest, a.rgd_mode, a.approver, a.reason, a.rgd_repo, a.rgd_base, a.bug,
                                          a.record, a.rgd_class, a.rgd_follow_up)
        elif a.cmd == "coverage":
            if not pack.uses_coverage_cli():
                res = Result.not_run("coverage", f"pack {pack.active_id()} has sdet.coverage_cli=false")
            else:
                res = coverage.run(a.commons_dir, a.module_root, a.module_code, a.offline)
        elif a.cmd == "run":
            res = evidence.capture(a.repo, a.out, command, a.report, a.cwd, a.env, a.timeout, a.fail_on)
        elif a.cmd == "clip":
            if command:
                res = clip.run_command(command, a.max_chars)
            elif a.file:
                res = clip.run(a.file, a.max_chars)
            else:
                res = Result.not_run("clip", "pass --file <log> or -- <command>")
        elif a.cmd == "evidence":
            res = evidence.run(a.record, a.repo)
        elif a.cmd == "change-scope":
            res = change_scope.run(a.repo, a.base, a.manifest)
        elif a.cmd == "test-integrity":
            res = test_integrity.run(a.repo, a.base, a.approvals, a.strict)
        elif a.cmd == "migration":
            res = migration.run(a.repo, a.base)
        elif a.cmd == "trace":
            res = traceability.run(a.manifest)
        elif a.cmd == "coding-standards":
            res = coding_standards.run(a.repo, a.base, a.manifest, a.evidence, a.changed_lines)
        elif a.cmd == "robustness":
            res = robustness.run(a.repo, a.base, a.manifest)
        elif a.cmd == "requirement-clarity":
            res = requirement_clarity.run(a.manifest)
        elif a.cmd == "requirement-fit":
            res = requirement_fit.run(a.repo, a.base, a.manifest)
        elif a.cmd == "branch-env":
            res = branch_env.run(a.manifest)
        elif a.cmd == "defect-reports":
            res = defect_reports.run(a.manifest)
        elif a.cmd == "adapter-check":
            res = adapter_check.run(a.platform, a.target, a.sync)
        elif a.cmd == "evals":
            res = evals_run.run(a.run_evals, a.eval_pack)
        elif a.cmd == "cold-start":
            res = cold_start.run()
        elif a.cmd == "start":
            res = task.run("start", a.task_id, a.task_type)
        elif a.cmd == "task":
            res = task.run(a.action, a.task_id, a.task_type, getattr(a, "task_out", None), getattr(a, "task_from", None),
                           a.task_base, a.task_branch, a.task_repos)
        elif a.cmd in ("status", "review", "approval", "limitation"):
            from . import records
            path = _task_manifest(getattr(a, "manifest", None), a.task_id)
            if not path:
                res = Result.not_run(a.cmd, "no task manifest: pass --id, or activate a task")
            elif a.cmd == "status":
                res = records.status(path)
            elif a.cmd == "review":
                res = records.review_record(path, a.review, a.result, a.reviewer)
            elif a.cmd == "approval":
                res = records.approval_add(path, a.for_action, a.approver, a.reason, a.scope, a.gate, a.exception, a.follow_up, a.no_follow_up)
            else:
                res = records.limitation_add(path, a.lim, a.text, a.ref, a.follow_up, a.no_follow_up)
        elif a.cmd == "ci-coverage":
            from . import ci_coverage
            res = ci_coverage.run(a.manifest)
        elif a.cmd == "resume":
            from . import handover
            res = handover.run(a.task_id)
        elif a.cmd == "gates":
            from . import gates
            path = a.manifest or (str(task.tasks_root() / a.task_id / "task-manifest.json") if a.task_id
                                  else str(workspace.active_manifest()[0] or ""))
            res = gates.refresh(path, a.state, a.every, a.rerun) if path and Path(path).is_file() \
                else Result.not_run("gates-refresh", f"no task manifest: {path or 'no active task; pass --id'}")
        elif a.cmd == "discover":
            res = discover.run(a.repo, a.module, a.out, a.force)
        elif a.cmd == "diff-coverage":
            res = diff_coverage.run(a.repo, a.base, a.jacoco, a.threshold, a.include)
        elif a.cmd == "usage":
            res = usage.mark(a.manifest, a.mark) if a.mark else usage.report(a.manifest, a.transcript, a.out)
        elif a.cmd == "version":
            from . import version
            res = version.run(a.repo, a.base)
        elif a.cmd == "guard":
            from .guard import find_active_manifest
            man = a.manifest or (str(find_active_manifest(cwd=a.cwd)) if find_active_manifest(cwd=a.cwd) else None)
            if a.shell is not None:
                res = guard.check_shell(a.shell, man)
            elif a.write_path:
                res = guard.check_write(a.write_path, man, a.cwd)
            elif getattr(a, "read_path", None):
                res = guard.check_read(a.read_path, a.cwd)
            else:
                res = Result.not_run("guard", "pass --shell, --write or --read")
        else:
            if a.next:
                res = manifest.next_report(a.manifest)
            elif a.to and a.apply:
                res = manifest.apply(a.manifest, a.to, a.note)
            else:
                res = manifest.transition(a.manifest, a.to) if a.to else manifest.check(a.manifest)
    except Exception as e:  # a validator that crashes must never read as PASS
        res = Result.not_run(a.cmd, f"{type(e).__name__}: {e}")

    out = res.to_json() if a.json else res.to_text()
    sys.stdout.buffer.write((out + "\n").encode("utf-8"))
    return EXIT[res.status]


if __name__ == "__main__":
    sys.exit(main())
