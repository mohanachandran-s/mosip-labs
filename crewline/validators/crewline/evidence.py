"""Execution evidence that the model cannot fabricate.

`run` executes the command itself and writes the record; `evidence` re-checks a record against the repository
before a gate uses it.

  python -m crewline run --repo <dir> --out <task>/evidence/unit.json [--report <path>] [--env JAVA_HOME=<jdk>]
                         [--timeout <minutes>] [--fail-on <regex>] -- mvn -B test
  python -m crewline evidence --record <task>/evidence/unit.json --repo <dir>

Record: {command, cwd, exit_code, started_at, duration_s, commit, worktree_digest, log, report, reports, java_version, env,
         stopped}
The executable is resolved through PATH/PATHEXT (`mvn` -> `mvn.cmd` on Windows); `--env KEY=VALUE` sets a variable for
this run only (JAVA_HOME also leads PATH); `java_version` records which JDK the run used.

Reports are kept with the task: `--report` and every report the run wrote (TestRig `testng-report/`, Surefire/Failsafe
`TEST-*.xml`, `cucumber.json`, newer than the start, under the run's folder) are copied to `<out dir>/reports/<out stem>/`
and listed in `reports`, so they outlive the worktree and can be looked at later.
`--timeout` stops a run after N minutes, `--fail-on` as soon as the log matches the regex (e.g. a 401 at TestRig login,
which otherwise hangs the suite); the record says `stopped` and why.

  EVD-001  record misses a required field                                        BLOCK
  EVD-002  commit is not the repository HEAD, or the uncommitted state changed   BLOCK (stale evidence)
  EVD-003  exit_code is not 0                                                    BLOCK
  EVD-004  log or report path in the record does not exist (a kept copy counts)  BLOCK
  EVD-005  run stopped by --timeout or --fail-on                                 BLOCK
  EVD-006  reports kept in <out dir>/reports/<out stem>/                          INFO
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from . import clip
from .gitio import head, worktree_digest
from .result import BLOCK, INFO, Result

REQUIRED = ("command", "exit_code", "started_at", "commit", "worktree_digest", "log")
REPORT_GLOBS = ("**/testng-report/*.html", "**/testng-report/*.xml", "**/surefire-reports/TEST-*.xml",
                "**/failsafe-reports/TEST-*.xml", "**/cucumber.json", "**/target/cucumber*.json")
MAX_REPORT_FILES = 200


def _new_reports(root: Path, since: float) -> list[Path]:
    """Report files the run wrote under `root` (modified after it started); node_modules and .git are skipped."""
    found: dict[Path, None] = {}
    for pattern in REPORT_GLOBS:
        for f in root.glob(pattern):
            parts = set(f.parts)
            if "node_modules" in parts or ".git" in parts or ".crewline-work" in parts:
                continue
            try:
                if f.is_file() and f.stat().st_mtime >= since - 1:
                    found[f] = None
            except OSError:
                continue
            if len(found) >= MAX_REPORT_FILES:
                return list(found)
    return list(found)


def keep_reports(out: Path, cwd: Path, since: float, report: str | None) -> list[str]:
    """Copy the run's reports to <out dir>/reports/<out stem>/ (layout kept) and return the copies."""
    dest = out.resolve().parent / "reports" / out.stem
    sources: list[tuple[Path, Path]] = []
    if report and Path(report).exists():
        r = Path(report).resolve()
        files = [r] if r.is_file() else [f for f in r.rglob("*") if f.is_file()][:MAX_REPORT_FILES]
        sources += [(f, f.relative_to(r.parent)) for f in files]
    for f in _new_reports(cwd, since):
        try:
            sources.append((f, f.resolve().relative_to(cwd.resolve())))
        except ValueError:
            sources.append((f, Path(f.name)))
    if not sources:
        return []
    if dest.exists():
        shutil.rmtree(dest)
    kept: list[str] = []
    for src, rel in sources:
        target = dest / rel
        if str(target) in kept:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        kept.append(str(target))
    return kept


def _watch(argv: list[str], cwd: str, env: dict, log: Path, timeout_min: float | None,
           fail_on: str | None) -> tuple[int, str]:
    """Run with the log on disk; stop on timeout or when the log matches `fail_on`. Returns (exit code, why stopped)."""
    pattern = re.compile(fail_on) if fail_on else None
    deadline = time.monotonic() + timeout_min * 60 if timeout_min else None
    with log.open("w", encoding="utf-8", errors="replace") as fh:
        proc = subprocess.Popen(argv, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT, shell=False, env=env)
        if not pattern and not deadline:
            return proc.wait(), ""
        pos, why = 0, ""
        while proc.poll() is None:
            time.sleep(1)
            if pattern:
                with log.open("rb") as rd:
                    rd.seek(max(pos - 4096, 0))          # overlap, so a match split across reads is still seen
                    chunk = rd.read()
                    pos = rd.tell()
                m = pattern.search(chunk.decode("utf-8", errors="replace"))
                if m:
                    why = f"--fail-on matched: {m.group(0)[:120]}"
            if not why and deadline and time.monotonic() > deadline:
                why = f"--timeout {timeout_min:g} min"
            if why:
                _kill(proc)
                break
        return proc.wait(), why


def _kill(proc: subprocess.Popen) -> None:
    """Stop the process and its children (a TestRig JVM started by mvn.cmd outlives a plain kill on Windows)."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=30)
        else:
            proc.kill()
    except (OSError, subprocess.TimeoutExpired):
        proc.kill()


def capture(repo: str, out: str, command: list[str], report: str | None = None, cwd: str | None = None,
            env: list[str] | None = None, timeout_min: float | None = None, fail_on: str | None = None) -> Result:
    res = Result("run", inputs={"repo": repo, "command": " ".join(command)})
    if not command:
        return Result.not_run("run", "no command given (put it after --)")
    try:
        child = clip.child_env(env)
    except ValueError as e:
        return Result.not_run("run", str(e))
    outp = Path(out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    log = outp.with_suffix(".log")
    rec = {"command": " ".join(command), "cwd": str(Path(cwd or repo).resolve()),
           "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "commit": head(repo), "worktree_digest": worktree_digest(repo), "log": str(log.resolve())}
    if env:
        rec["env"] = sorted(p.partition("=")[0] for p in env)       # names only: values may be secrets
    jv = clip.java_version(child)
    if jv:
        rec["java_version"] = jv
    t0, wall0 = time.monotonic(), time.time()
    try:
        rec["exit_code"], stopped = _watch(clip.resolve_argv(command, child), rec["cwd"], child, log, timeout_min, fail_on)
    except OSError as e:
        return Result.not_run("run", f"could not start {command[0]}: {e}")
    except re.error as e:
        return Result.not_run("run", f"--fail-on is not a valid regex: {e}")
    rec["duration_s"] = round(time.monotonic() - t0, 1)
    if stopped:
        rec["stopped"] = stopped
    if report:
        rec["report"] = str(Path(report).resolve())
    kept = keep_reports(outp, Path(rec["cwd"]), wall0, report)
    if kept:
        rec["reports"] = kept
        res.add("EVD-006", INFO, f"{len(kept)} report file(s) kept in {outp.resolve().parent / 'reports' / outp.stem}")
    outp.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
    res.inputs.update(record=str(outp), exit_code=rec["exit_code"])
    try:
        res.add("CLIP-002", INFO, clip.digest_text(log.read_text(encoding="utf-8", errors="replace"), str(log)),
                path=str(log))
    except OSError:
        pass
    if stopped:
        res.add("EVD-005", BLOCK, f"stopped: {stopped} (log {log})")
    elif rec["exit_code"] != 0:
        res.add("EVD-003", BLOCK, f"exit code {rec['exit_code']} (log {log})")
    return res.finish()


def run(record: str, repo: str) -> Result:
    res = Result("evidence", inputs={"record": record, "repo": repo})
    try:
        rec = json.loads(Path(record).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return Result.not_run("evidence", f"cannot read record: {e}")
    missing = [k for k in REQUIRED if rec.get(k) in (None, "")]
    if missing:
        res.add("EVD-001", BLOCK, f"record misses {', '.join(missing)} (produce it with `crewline run`)")
    try:
        if rec.get("commit") and rec["commit"] != head(repo):
            res.add("EVD-002", BLOCK, f"recorded at {rec['commit'][:10]}, HEAD is {head(repo)[:10]}: re-run on HEAD")
        elif rec.get("worktree_digest") and rec["worktree_digest"] != worktree_digest(repo):
            res.add("EVD-002", BLOCK, "uncommitted changes differ from what was tested: re-run")
    except RuntimeError as e:
        return Result.not_run("evidence", str(e))
    if rec.get("exit_code") not in (None, 0):
        res.add("EVD-003", BLOCK, f"exit code {rec['exit_code']}")
    if rec.get("stopped"):
        res.add("EVD-005", BLOCK, f"the run was stopped: {rec['stopped']}")
    for key in ("log", "report"):
        kept = key == "report" and any(Path(k).exists() for k in rec.get("reports") or [])
        if rec.get(key) and not Path(rec[key]).exists() and not kept:
            res.add("EVD-004", BLOCK, f"{key} {rec[key]} does not exist")
    return res.finish()
