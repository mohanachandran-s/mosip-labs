"""Put the user's environment values into a worktree's config for a run, and take them out again, without printing them.

  crewline creds merge   --from <values file> --into <props> [--into <props> ...]
  crewline creds restore --into <props> [--into <props> ...]

`merge` reads `key=value` / `key: value` lines from the values file, sets every key that the target already has and
appends the ones it lacks, marks a tracked target `git update-index --skip-worktree` (so it cannot be committed) and keeps
the original next to it as `<props>.crewline-orig` (also excluded). Its output names keys and counts, never values.
`restore` puts the original back and clears skip-worktree. Never run `merge` on the user's own checkout: use the task's
worktrees.

  CRD-001  values file or target missing / unreadable                                   BLOCK
  CRD-002  target is not inside a git checkout (cannot be protected from a commit)       BLOCK
  CRD-003  restore: no `<props>.crewline-orig` to restore from                            BLOCK
  CRD-004  merged: keys updated / added (names only)                                     INFO
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from .gitio import git
from .result import BLOCK, INFO, Result

LINE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*[=:]\s?(.*?)\s*$")
ORIG = ".crewline-orig"


def parse(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        if line.lstrip().startswith(("#", "!")):
            continue
        m = LINE.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _top(path: Path) -> Path | None:
    out = git(path.parent, "rev-parse", "--show-toplevel", check=False).strip()
    return Path(out) if out else None


def _exclude(top: Path, pattern: str) -> None:
    info = Path(git(top, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()) / "info" / "exclude"
    have = info.read_text(encoding="utf-8").splitlines() if info.is_file() else []
    if pattern not in have:
        info.parent.mkdir(parents=True, exist_ok=True)
        info.write_text("\n".join(have + [pattern]) + "\n", encoding="utf-8")


def merge(source: str, targets: list[str]) -> Result:
    res = Result("creds", inputs={"action": "merge", "targets": targets})
    try:
        values = parse(Path(source).read_text(encoding="utf-8", errors="replace"))
    except OSError as e:
        res.add("CRD-001", BLOCK, f"cannot read the values file: {e.strerror}")
        return res.finish()
    for target in targets:
        p = Path(target)
        if not p.is_file():
            res.add("CRD-001", BLOCK, f"{target} does not exist")
            continue
        top = _top(p)
        if top is None:
            res.add("CRD-002", BLOCK, f"{target} is not in a git checkout: merge into the task worktree's copy")
            continue
        orig = p.with_name(p.name + ORIG)
        if not orig.exists():
            shutil.copy2(p, orig)
        _exclude(top, f"*{ORIG}")
        lines = orig.read_text(encoding="utf-8", errors="replace").splitlines()
        seen, updated = set(), []
        for i, line in enumerate(lines):
            m = LINE.match(line)
            if m and not line.lstrip().startswith(("#", "!")) and m.group(1) in values:
                key = m.group(1)
                sep = "=" if line.split(key, 1)[1].lstrip().startswith("=") else ":"
                lines[i] = f"{key}{sep}{values[key]}" if sep == "=" else f"{key}: {values[key]}"
                seen.add(key)
                updated.append(key)
        added = [k for k in values if k not in seen]
        lines += [f"{k}={values[k]}" for k in added]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        rel = p.resolve().relative_to(top.resolve()).as_posix()
        if git(top, "ls-files", "--error-unmatch", rel, check=False).strip():
            git(top, "update-index", "--skip-worktree", rel)
        res.add("CRD-004", INFO, f"{target}: {len(updated)} key(s) updated, {len(added)} added "
                f"({', '.join(added[:5])}{' ...' if len(added) > 5 else ''}); restore with `crewline creds restore`")
    return res.finish()


def restore(targets: list[str]) -> Result:
    res = Result("creds", inputs={"action": "restore", "targets": targets})
    for target in targets:
        p = Path(target)
        orig = p.with_name(p.name + ORIG)
        if not orig.is_file():
            res.add("CRD-003", BLOCK, f"no {orig.name} next to {target}: nothing to restore")
            continue
        shutil.move(str(orig), str(p))
        top = _top(p)
        if top:
            rel = p.resolve().relative_to(top.resolve()).as_posix()
            if git(top, "ls-files", "--error-unmatch", rel, check=False).strip():
                git(top, "update-index", "--no-skip-worktree", rel)
        res.add("CRD-004", INFO, f"{target}: original restored")
    return res.finish()
