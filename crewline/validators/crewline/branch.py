"""Branch-scope check: the task branch is cut from the user-chosen upstream base and carries only
the task's own commits (fork -> PR to upstream model).

  BR-001  branch is not based on the recorded upstream base commit           BLOCK
  BR-002  a commit in base..HEAD is not a task commit and not allowed        BLOCK
  BR-003  a merge commit in base..HEAD (pulls in other branches' history)    BLOCK
  BR-004  upstream branch moved past the base commit                         WARN
  BR-005  PR target is not the upstream repo/branch                          BLOCK

A task commit is one whose subject carries one of the task's keys: the tracker key (Jira `INJIVER-1386`, or a GitHub
issue `#185` / `owner/repo#185`) or a story key (`MOSIP-17633`). `--manifest` reads tracker.key and
requirements.story_keys. Anything else must be listed with --allow <sha>, which the orchestrator only passes after the
user explicitly asked for that change to be included.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .result import BLOCK, INFO, WARN, Result


def branch_of(upstream_ref: str) -> str:
    """'upstream/develop', 'refs/remotes/upstream/release/1.2' -> 'develop', 'release/1.2'."""
    ref = upstream_ref.removeprefix("refs/remotes/")
    return ref.split("/", 1)[1] if "/" in ref else ref


def key_pattern(key: str) -> re.Pattern:
    """`MOSIP-1` must not match `MOSIP-17633`; a GitHub key matches its `#N` (any repo prefix) or the full form."""
    m = re.fullmatch(r"(?:[\w.-]+/[\w.-]+)?#(\d+)", key.strip())
    if m:
        return re.compile(rf"(?<![\w#])(?:[\w.-]+/[\w.-]+)?#{m.group(1)}(?!\d)")
    return re.compile(rf"(?<![\w-]){re.escape(key.strip())}(?!\d)")


def manifest_keys(manifest_path: str) -> list[str]:
    doc = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    keys = [(doc.get("tracker") or {}).get("key")] + list((doc.get("requirements") or {}).get("story_keys") or [])
    return [k for k in keys if k]


def _git(repo: str, *args: str) -> str:
    return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout.strip()


def run(repo: str, upstream_ref: str, base: str, story_key: str | list[str], allow: list[str] | None = None,
        pr_repo: str | None = None, pr_base: str | None = None, upstream_repo: str | None = None) -> Result:
    keys = [story_key] if isinstance(story_key, str) else [k for k in story_key if k]
    res = Result("branch-scope", inputs={"repo": repo, "upstream_ref": upstream_ref, "base": base,
                                         "story_key": ", ".join(keys)})
    if not keys:
        return Result.not_run("branch-scope", "no task key: pass --story or --manifest with tracker.key")
    patterns = [key_pattern(k) for k in keys]
    allow = [a.lower() for a in (allow or [])]
    try:
        base_full = _git(repo, "rev-parse", base)
        merge_base = _git(repo, "merge-base", "HEAD", base_full)
        upstream_tip = _git(repo, "rev-parse", upstream_ref)
        log = _git(repo, "log", "--format=%H%x1f%P%x1f%s", f"{base_full}..HEAD")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        return Result.not_run("branch-scope", f"git failed: {getattr(e, 'stderr', e)}")

    if merge_base != base_full:
        res.add("BR-001", BLOCK, f"HEAD is not based on upstream base {base_full[:10]} (merge-base {merge_base[:10]})",
                remediation=f"recreate the branch from {upstream_ref} and cherry-pick only the task commits")
    commits = [line.split("\x1f") for line in log.splitlines() if line]
    for sha, parents, subject in commits:
        if len(parents.split()) > 1:
            res.add("BR-003", BLOCK, f"merge commit {sha[:10]}: {subject[:70]}", evidence=sha)
        elif not any(p.search(subject) for p in patterns) and not any(sha.startswith(a) for a in allow):
            res.add("BR-002", BLOCK, f"foreign commit {sha[:10]}: {subject[:70]}", evidence=sha,
                    remediation="drop it, or include it only after the user explicitly asks (--allow)")
    if upstream_tip != base_full:
        ahead = _git(repo, "rev-list", "--count", f"{base_full}..{upstream_tip}")
        res.add("BR-004", WARN, f"{upstream_ref} has {ahead} commit(s) after the base; rebase before the PR if needed")
    if pr_base is not None and upstream_repo is not None:
        if (pr_repo or "").lower() != upstream_repo.lower() or pr_base != branch_of(upstream_ref):
            res.add("BR-005", BLOCK, f"PR target {pr_repo}:{pr_base} is not the upstream {upstream_repo}:"
                                     f"{branch_of(upstream_ref)}")
    res.inputs["task_commits"] = len(commits)
    if not commits:
        res.add("BR-000", INFO, "no commits on the branch yet")
    return res.finish()
