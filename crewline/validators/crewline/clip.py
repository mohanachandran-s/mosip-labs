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

import re
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


def run_command(command: list[str], cap: int | None = None, cwd: str | None = None) -> Result:
    """Run a command, keep stdout on disk, return CLIP-002. Other noisy bash without vendoring RTK."""
    if not command:
        return Result.not_run("clip", "CLIP-001 no command (put it after --)")
    cap = max_chars() if cap is None else cap
    res = Result("clip", inputs={"command": " ".join(command), "max_chars": cap})
    log = Path(tempfile.mkdtemp(prefix="crewline-clip-")) / "cmd.log"
    try:
        with log.open("w", encoding="utf-8", errors="replace") as fh:
            code = subprocess.run(command, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT, shell=False).returncode
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
