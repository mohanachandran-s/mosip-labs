"""Digest a command log so the agent never loads the raw file (RTK-shaped, Crewline-owned).

RTK (https://github.com/rtk-ai/rtk) is an optional shell hook that compresses *other* bash.
Crewline does not vendor it. Builds and tests already go through `crewline run` (full log on disk);
`clip` is what the agent reads. Inspect a log with:

  python -m crewline clip --file <record.log>
  python -m crewline clip --file -                 # stdin
  python -m crewline clip -- <command>             # run, keep the log, print the digest (noisier bash without RTK)
  python -m crewline run ... -- <command>          # prints the same digest (CLIP-002)

  CLIP-001  file missing / unreadable              NOT_RUN
  CLIP-002  digest (always; never the raw log)     INFO
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml

from .result import INFO, Result

CORE = Path(__file__).resolve().parents[2]
DEFAULT_CAP = 4000
HIT = re.compile(
    r"(?i)(BUILD FAILURE|BUILD SUCCESS|Tests run:|FAILURE!|\[ERROR\]|\[FATAL\]|"
    r"Caused by:|Exception\b|ERROR\s|FAIL(ED|URE)?\b|Assertions?:|"
    r"uniqueIdentifier|Commit Id|Branch Name)"
)


def max_chars() -> int:
    try:
        raw = yaml.safe_load((CORE / "core/usage-budget.yaml").read_text(encoding="utf-8")) or {}
        return int(raw.get("clip_max_chars", DEFAULT_CAP))
    except (OSError, ValueError, yaml.YAMLError):
        return DEFAULT_CAP


def _trim(line: str, width: int = 160) -> str:
    line = line.replace("\t", " ").rstrip()
    return line if len(line) <= width else line[: width - 3] + "..."


def digest_text(text: str, path: str = "", cap: int | None = None) -> str:
    cap = DEFAULT_CAP if cap is None else max(200, cap)
    lines = text.splitlines()
    hits = [(i + 1, _trim(line)) for i, line in enumerate(lines) if HIT.search(line)][:20]
    head_n, tail_n = 8, 16
    head = [_trim(x) for x in lines[:head_n]]
    tail = [_trim(x) for x in lines[-tail_n:]] if len(lines) > head_n else []
    if tail and head and tail[0] == head[-1] and len(lines) <= head_n + tail_n:
        tail = []
    out = [
        f"CLIP path={path or '-'} bytes={len(text.encode('utf-8'))} lines={len(lines)} cap={cap}",
        "hits:" if hits else "hits: (none)",
        *[f"  L{n} {line}" for n, line in hits],
        "head:",
        *[f"  {line}" for line in head],
    ]
    if tail:
        out.append("tail:")
        out.extend(f"  {line}" for line in tail)
    out.append("full log on disk; do not Read it")
    body = "\n".join(out)
    if len(body) <= cap:
        return body
    return body[: cap - 16].rstrip() + "\n...truncated"


def child_env(overrides: list[str] | None = None) -> dict[str, str]:
    """The environment for a command Crewline runs: ours, plus `KEY=VALUE` overrides (e.g. JAVA_HOME=<jdk>; its bin/
    goes first on PATH so `java` and `mvn` use it). MSYS_NO_PATHCONV=1 keeps Git Bash from rewriting `upstream/develop:x`
    style arguments into Windows paths."""
    env = dict(os.environ)
    env.setdefault("MSYS_NO_PATHCONV", "1")
    for pair in overrides or []:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise ValueError(f"--env {pair!r}: expected KEY=VALUE")
        env[key] = value
        if key == "JAVA_HOME":
            env["PATH"] = str(Path(value) / "bin") + os.pathsep + env.get("PATH", "")
    return env


def resolve_argv(command: list[str], env: dict[str, str] | None = None) -> list[str]:
    """argv with its executable resolved through PATH/PATHEXT: on Windows `mvn` is `mvn.cmd`, which a shell-less
    subprocess cannot start by itself (WinError 2)."""
    if not command:
        return command
    path = (env or os.environ).get("PATH")
    exe = shutil.which(command[0], path=path)
    return [exe, *command[1:]] if exe else list(command)


def java_version(env: dict[str, str] | None = None) -> str:
    """First line of `java -version` for the JDK the command will use ('' when there is no java)."""
    env = env or dict(os.environ)
    home = env.get("JAVA_HOME")
    java = shutil.which("java", path=str(Path(home) / "bin")) if home else None
    java = java or shutil.which("java", path=env.get("PATH"))
    if not java:
        return ""
    try:
        p = subprocess.run([java, "-version"], capture_output=True, text=True, env=env, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    out = (p.stderr or p.stdout).strip().splitlines()
    return out[0] if out else ""


def run_command(command: list[str], cap: int | None = None, cwd: str | None = None) -> Result:
    """Run a command, keep stdout on disk, return CLIP-002. Other noisy bash without vendoring RTK."""
    if not command:
        return Result.not_run("clip", "CLIP-001 no command (put it after --)")
    cap = max_chars() if cap is None else cap
    res = Result("clip", inputs={"command": " ".join(command), "max_chars": cap})
    log = Path(tempfile.mkdtemp(prefix="crewline-clip-")) / "cmd.log"
    try:
        with log.open("w", encoding="utf-8", errors="replace") as fh:
            env = child_env()
            code = subprocess.run(resolve_argv(command, env), cwd=cwd, stdout=fh, stderr=subprocess.STDOUT,
                                  shell=False, env=env).returncode
    except OSError as e:
        return Result.not_run("clip", f"CLIP-001 could not start {command[0]}: {e}")
    res.inputs.update(exit_code=code, log=str(log))
    text = log.read_text(encoding="utf-8", errors="replace")
    res.add("CLIP-002", INFO, digest_text(text, str(log), cap), path=str(log))
    return res.finish()


def run(file: str, cap: int | None = None) -> Result:
    cap = max_chars() if cap is None else cap
    res = Result("clip", inputs={"file": file, "max_chars": cap})
    if file == "-":
        import sys
        text = sys.stdin.read()
        path = "-"
    else:
        p = Path(file)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return Result.not_run("clip", f"CLIP-001 cannot read {file}: {e}", file=file)
        path = str(p)
    digest = digest_text(text, path, cap)
    res.add("CLIP-002", INFO, digest, path=path)
    return res.finish()
