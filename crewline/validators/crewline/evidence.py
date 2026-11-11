"""Execution evidence that the model cannot fabricate.

`run` executes the command itself and writes the record; `evidence` re-checks a record against the repository
before a gate uses it.

  python -m crewline run --repo <dir> --out <task>/evidence/unit.json [--report <path>] [--env JAVA_HOME=<jdk>] -- mvn -B test
  python -m crewline evidence --record <task>/evidence/unit.json --repo <dir>

Record: {command, cwd, exit_code, started_at, duration_s, commit, worktree_digest, log, report, java_version, env}
The executable is resolved through PATH/PATHEXT (`mvn` -> `mvn.cmd` on Windows); `--env KEY=VALUE` sets a variable for
this run only (JAVA_HOME also leads PATH); `java_version` records which JDK the run used.

  EVD-001  record misses a required field                                        BLOCK
  EVD-002  commit is not the repository HEAD, or the uncommitted state changed   BLOCK (stale evidence)
  EVD-003  exit_code is not 0                                                    BLOCK
  EVD-004  log or report path in the record does not exist                       BLOCK
"""
from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from . import clip
from .gitio import head, worktree_digest
from .result import BLOCK, INFO, Result

REQUIRED = ("command", "exit_code", "started_at", "commit", "worktree_digest", "log")


def capture(repo: str, out: str, command: list[str], report: str | None = None, cwd: str | None = None,
            env: list[str] | None = None) -> Result:
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
    t0 = time.monotonic()
    try:
        with log.open("w", encoding="utf-8", errors="replace") as fh:
            rec["exit_code"] = subprocess.run(clip.resolve_argv(command, child), cwd=rec["cwd"], stdout=fh,
                                              stderr=subprocess.STDOUT, shell=False, env=child).returncode
    except OSError as e:
        return Result.not_run("run", f"could not start {command[0]}: {e}")
    rec["duration_s"] = round(time.monotonic() - t0, 1)
    if report:
        rec["report"] = str(Path(report).resolve())
    outp.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
    res.inputs.update(record=str(outp), exit_code=rec["exit_code"])
    try:
        res.add("CLIP-002", INFO, clip.digest_text(log.read_text(encoding="utf-8", errors="replace"), str(log)),
                path=str(log))
    except OSError:
        pass
    if rec["exit_code"] != 0:
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
    for key in ("log", "report"):
        if rec.get(key) and not Path(rec[key]).exists():
            res.add("EVD-004", BLOCK, f"{key} {rec[key]} does not exist")
    return res.finish()
