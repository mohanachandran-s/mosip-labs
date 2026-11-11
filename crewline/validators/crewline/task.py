"""Thin task queue over tasks/*/task-manifest.json. Not a daemon. `start` is a conductor only.

  python -m crewline task list
  python -m crewline task status --id <id>
  python -m crewline task resume --id <id>
  python -m crewline task new --type feature --id <id>
  python -m crewline task start --id <id> [--type feature]   # conductor: next --to, not the crew
  python -m crewline start --id <id> [--type feature]        # same
  python -m crewline task export --id <id> --out <dir-or-zip>
  python -m crewline task import --from <dir-or-zip> [--id <id>]

  TSK-001  unknown task id                                                                          BLOCK
  TSK-002  new: id exists, invalid, or unknown type                                                 BLOCK
  TSK-003  resume: WAITING_FOR_USER / S_ESCALATE without resume_state                               BLOCK
  TSK-004  INFO listing / next-step (never walks the crew)                                          INFO
  TSK-005  export/import: missing id, dest, or archive                                              BLOCK
  TSK-006  export/import done (manifest + packets + evidence JSON; logs stay behind)                INFO
  TSK-007  start (conductor): next --to and whether that transition would pass; never applies       INFO
  TSK-008  activate / deactivate: pin the active task in .crewline-work/active-task
  TSK-009  crewline resume: hand-over generated from the manifest (handover.py)
  TSK-010  worktree: create the task worktree and enable the plugin in it; context is found, not copied (ADR-0009)
  TSK-011  new: a --repo is not a git checkout, or no clone at all (the task would land in the plugin cache)   BLOCK

Workspace: CREWLINE_TASKS=<dir>, else `new --repo <clone>`, else <clone>/.crewline-work/tasks/ in a repository with .crewline/, else <core>/tasks/
(see workspace.py).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import gitio, manifest, version, workspace
from .result import BLOCK, INFO, WARN, Result

CORE = Path(__file__).resolve().parents[2]
ID_RX = re.compile(r"^[A-Za-z0-9._-]+$")
KNOWN_TYPES = (
    "feature", "bug-fix", "refactor", "dependency-upgrade", "database-migration",
    "incident", "release", "test-automation", "module-onboarding", "documentation",
)


def tasks_root() -> Path:
    return workspace.tasks_root()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def skeleton(task_id: str, task_type: str) -> dict:
    tpl = manifest.load_template(task_type) or {}
    return {
        "task_id": task_id,
        "crewline_version": version.current(),
        "task_type": task_type,
        "risk_tier": tpl.get("risk_tier") or "L2",
        "outcome": "IN_PROGRESS",
        "current_state": "INTAKE",
        "initial_state": "INTAKE",
        "started_at": now_iso(),
        "scope": {"module": "", "repositories": [], "approved_paths": []},
        "requirements": {"source_refs": [], "acceptance_criteria": [], "open_ambiguities": 0},
        "testcases": {"canonical_format": "none"},
        "repair": {"attempt_count": 0, "dev_attempt_count": 0, "attempt_history": [], "classification": ""},
        "reviews": {"implementation_review": "NOT_STARTED", "fresh_context_review": "NOT_STARTED"},
        "gates": [],
        "approvals": [],
        "evidence_refs": [],
        "tracker": {},                      # INPUT_VALIDATION: linked, created on approval, or waived (MAN-017)
        "github": {"issue": None, "subtasks": [], "pull_request": None},
    }


def _load(task_id: str) -> tuple[Path | None, dict | None, str]:
    path = tasks_root() / task_id / "task-manifest.json"
    if not path.is_file():
        return path, None, f"no manifest at {path}"
    try:
        return path, json.loads(path.read_text(encoding="utf-8")), ""
    except (OSError, ValueError) as e:
        return path, None, str(e)


def _iter() -> list[tuple[str, Path, dict]]:
    root = tasks_root()
    if not root.is_dir():
        return []
    found: list[tuple[str, Path, dict]] = []
    for child in sorted(root.iterdir()):
        man = child / "task-manifest.json"
        if not man.is_file():
            continue
        try:
            doc = json.loads(man.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        found.append((child.name, man, doc))
    return found


def _next_state(doc: dict) -> str | None:
    src = doc.get("current_state")
    if src in ("WAITING_FOR_USER", "S_ESCALATE"):
        return doc.get("resume_state")
    ctx = manifest.Ctx(doc)
    nxt = ctx.next_states(src or "")
    forward = [n for n in nxt if ctx.idx(n) > ctx.idx(src or "")]     # a loop-back is not the way on
    return (forward or nxt or [None])[0]


def _budget_note(doc: dict) -> str:
    usage = Path(tasks_root() / doc.get("task_id", "") / "usage.json")
    if usage.is_file():
        try:
            data = json.loads(usage.read_text(encoding="utf-8"))
            overall = data.get("overall") or {}
            cap = (data.get("budget") or {}).get("per_task")
            return f"tokens {overall.get('total', 0)}/{cap}"
        except (OSError, ValueError):
            pass
    return "tokens unmetered (run crewline usage, or Cursor fire-break)"


def _portable_files(folder: Path) -> list[Path]:
    """Manifest, packets, evidence JSON, usage — not logs or HTML reports."""
    keep: list[Path] = []
    for p in folder.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() in {".log", ".html"}:
            continue
        keep.append(p)
    return keep


def _export(task_id: str, dest: str, res: Result) -> Result:
    path, doc, err = _load(task_id)
    if doc is None:
        res.add("TSK-001", BLOCK, err)
        return res.finish()
    src = path.parent
    out = Path(dest)
    if out.suffix.lower() == ".zip":
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in _portable_files(src):
                zf.write(f, f.relative_to(src).as_posix())
        res.add("TSK-006", INFO, f"exported {task_id} -> {out} (logs omitted)")
    else:
        dest_dir = out / task_id if out.exists() or out.suffix == "" else out
        dest_dir.mkdir(parents=True, exist_ok=True)
        for f in _portable_files(src):
            target = dest_dir / f.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
        res.add("TSK-006", INFO, f"exported {task_id} -> {dest_dir} (logs omitted)")
    return res.finish()


def _import(archive: str, task_id: str | None, res: Result) -> Result:
    src = Path(archive)
    if not src.exists():
        res.add("TSK-005", BLOCK, f"no archive at {src}")
        return res.finish()
    dest_id = task_id or (src.stem if src.suffix.lower() == ".zip" else src.name)
    dest = tasks_root() / dest_id
    if dest.exists():
        res.add("TSK-002", BLOCK, f"{dest} already exists")
        return res.finish()
    dest.mkdir(parents=True)
    if src.suffix.lower() == ".zip":
        with zipfile.ZipFile(src) as zf:
            zf.extractall(dest)
    else:
        for f in _portable_files(src):
            target = dest / f.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
    if not (dest / "task-manifest.json").is_file():
        shutil.rmtree(dest, ignore_errors=True)
        res.add("TSK-005", BLOCK, "archive has no task-manifest.json")
        return res.finish()
    res.add("TSK-006", INFO, f"imported {dest_id} -> {dest}. Resume: python -m crewline task resume --id {dest_id}")
    res.inputs["manifest"] = str(dest / "task-manifest.json")
    return res.finish()


def _enable_plugin(tree: Path, ctx: Path | None) -> None:
    """Project settings of the worktree enable Crewline the way the module does; the base branch may not (ADR-0009)."""
    wanted: dict = {"enabledPlugins": {"crewline@mosip-labs": True}}
    source = ctx.parent / ".claude" / "settings.json" if ctx else None
    if source and source.is_file():
        try:
            mod = json.loads(source.read_text(encoding="utf-8"))
            wanted = {k: mod[k] for k in ("enabledPlugins", "extraKnownMarketplaces") if k in mod} or wanted
        except ValueError:
            pass
    tracked = tree / ".claude" / "settings.json"
    if tracked.is_file():
        try:
            if "crewline@mosip-labs" in (json.loads(tracked.read_text(encoding="utf-8")).get("enabledPlugins") or {}):
                return                                 # the base branch carries it: nothing to add
        except ValueError:
            pass
    local = tree / ".claude" / "settings.local.json"
    try:
        have = json.loads(local.read_text(encoding="utf-8")) if local.is_file() else {}
    except ValueError:
        have = {}
    for key, val in wanted.items():
        have[key] = {**val, **(have.get(key) or {})}   # the user's own settings win
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(json.dumps(have, indent=2) + "\n", encoding="utf-8")


def _exclude(clone: Path, patterns: list[str]) -> None:
    """.git/info/exclude is shared by all worktrees: only paths that are never committed, so never `.crewline/`."""
    info = Path(gitio.git(clone, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()) / "info" / "exclude"
    have = info.read_text(encoding="utf-8").splitlines() if info.is_file() else []
    missing = [p for p in patterns if p not in have]
    if missing:
        info.parent.mkdir(parents=True, exist_ok=True)
        info.write_text("\n".join(have + missing) + "\n", encoding="utf-8")


def _worktree(task_id: str | None, base: str | None, branch: str | None, res: Result) -> Result:
    if not task_id:
        res.add("TSK-001", BLOCK, "pass --id")
        return res.finish()
    path, doc, err = _load(task_id)
    if doc is None:
        res.add("TSK-001", BLOCK, err)
        return res.finish()
    wsp = doc.setdefault("workspace", {"checkout_mode": "worktree"})
    clone = Path(wsp.get("clone") or tasks_root().parent.parent)
    tree = clone / ".crewline-work" / "worktrees" / task_id
    if not tree.is_dir():
        if not base or not branch:
            res.add("TSK-002", BLOCK, f"{tree} does not exist: pass --base <upstream ref> --branch <name>")
            return res.finish()
        if not any(a.get("action") == "base_branch" and all(a.get(k) for k in manifest.APPROVAL_FIELDS)
                   for a in doc.get("approvals", [])):
            res.add("TSK-002", BLOCK, "ask the upstream base branch first: no approval base_branch on the task")
            return res.finish()
        try:
            gitio.git(clone, "worktree", "add", str(tree), "-b", branch, base)
        except RuntimeError as e:
            res.add("TSK-002", BLOCK, f"git worktree add failed: {e}")
            return res.finish()
        wsp.setdefault("worktrees", []).append({"path": tree.as_posix(), "branch": branch, "purpose": "task"})
        wsp["open_worktrees"] = wsp.get("open_worktrees", 0) + 1
        wsp["clone"] = clone.as_posix()
        repo = next(iter(manifest.changed_repos(doc)), None)
        if repo is not None:
            repo.update(path=tree.as_posix(), branch=branch)
            repo.setdefault("baseline_commit", gitio.git(clone, "rev-parse", base).strip())
    _exclude(clone, [".crewline-work/", ".claude/settings.local.json"])
    ctx = workspace.context_dir(tree, doc)
    if ctx and ctx.parent != tree:
        wsp["context_dir"] = ctx.as_posix()
    _enable_plugin(tree, ctx)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    res.inputs.update(worktree=tree.as_posix(), context=str(ctx or ""))
    if ctx is None:
        res.add("TSK-010", WARN, "no .crewline/ found in the clone or its worktrees: onboard the module first")
    res.add("TSK-010", INFO, f"open the session in {tree.as_posix()}; it finds task {task_id} and context "
                             f"{ctx.as_posix() if ctx else '(none)'} by itself")
    return res.finish()


def run(action: str, task_id: str | None = None, task_type: str | None = None,
        out: str | None = None, archive: str | None = None, base: str | None = None, branch: str | None = None,
        repos: list[str] | None = None) -> Result:
    res = Result("task", inputs={"action": action, "root": str(tasks_root())})
    if action == "list":
        rows = _iter()
        res.inputs["count"] = len(rows)
        if not rows:
            res.add("TSK-004", INFO, f"no tasks under {tasks_root()} — `crewline task new --type feature --id <id>`")
            return res.finish()
        active, why = workspace.active_manifest()
        res.inputs["active"] = active.parent.name if active else ""
        for tid, path, doc in rows:
            here = active is not None and path.resolve() == active.resolve()
            res.add("TSK-004", INFO,
                    f"{'* ' if here else '  '}{tid}: {doc.get('task_type')} {doc.get('current_state')} {doc.get('outcome')} "
                    f"{_budget_note(doc)}" + (f" [active: {why}]" if here else ""), evidence=str(path))
        return res.finish()
    if action == "activate":
        if not task_id:
            res.add("TSK-001", BLOCK, "pass --id")
            return res.finish()
        path, doc, err = _load(task_id)
        if doc is None:
            res.add("TSK-001", BLOCK, err)
            return res.finish()
        if doc.get("current_state") in workspace.TERMINAL_STATES or doc.get("outcome") in workspace.TERMINAL_OUTCOMES:
            res.add("TSK-008", BLOCK, f"{task_id} is closed ({doc.get('current_state')}, {doc.get('outcome')})")
            return res.finish()
        workspace.active_file().write_text(task_id + "\n", encoding="utf-8")
        res.add("TSK-008", INFO, f"{task_id} is the active task ({workspace.active_file()}); a session inside another "
                                 "task's worktree still follows that worktree")
        return res.finish()
    if action == "deactivate":
        workspace.active_file().unlink(missing_ok=True)
        res.add("TSK-008", INFO, "no task pinned; the rails fall back to the newest open manifest")
        return res.finish()
    if action == "worktree":
        return _worktree(task_id, base, branch, res)
    if action == "new":
        if not task_id or not ID_RX.match(task_id):
            res.add("TSK-002", BLOCK, "pass --id with letters, digits, dot, underscore or hyphen")
            return res.finish()
        if task_type not in KNOWN_TYPES:
            res.add("TSK-002", BLOCK, f"unknown type {task_type!r}: {', '.join(KNOWN_TYPES)}")
            return res.finish()
        clones: list[Path] = []
        for r in repos or []:
            top = gitio.git(Path(r), "rev-parse", "--show-toplevel", check=False).strip() if Path(r).is_dir() else ""
            if not top:
                res.add("TSK-011", BLOCK, f"--repo {r} is not a git checkout")
                return res.finish()
            clones.append(workspace.main_clone(Path(top)))
        root = (Path(os.environ["CREWLINE_TASKS"]) if os.environ.get("CREWLINE_TASKS")
                else clones[0] / ".crewline-work" / "tasks" if clones else tasks_root())
        if not clones and root.resolve() == (CORE / "tasks").resolve() and "cache" in CORE.parts:
            res.add("TSK-011", BLOCK, "not inside a module clone: the task would land in the plugin cache and be lost on "
                    "update. Pass --repo <clone> (repeat: first = primary, rest = dependencies) or run from the clone")
            return res.finish()
        dest = root / task_id / "task-manifest.json"
        if dest.exists():
            res.add("TSK-002", BLOCK, f"{dest} already exists")
            return res.finish()
        doc = skeleton(task_id, task_type)
        if not clones and root.parent.name == ".crewline-work":          # tasks_root() picked the enclosing clone
            top = gitio.git(root.parent.parent, "rev-parse", "--show-toplevel", check=False).strip()
            if top:
                _exclude(Path(top), [".crewline-work/"])
        if clones:
            doc["scope"]["module"] = clones[0].name
            doc["scope"]["repositories"] = [{"name": c.name, "path": c.as_posix(), "role": "primary" if i == 0 else "dependency"}
                                            for i, c in enumerate(clones)]
            for c in clones:
                _exclude(c, [".crewline-work/"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        res.add("TSK-004", INFO, f"created {dest} at INTAKE. Conductor: `python -m crewline start --id {task_id}`. "
                f"Drive states with `python -m crewline manifest {dest} --to <STATE> [--apply]`.")
        res.inputs["manifest"] = str(dest)
        return res.finish()
    if action in {"status", "resume"}:
        if not task_id:
            res.add("TSK-001", BLOCK, "pass --id")
            return res.finish()
        path, doc, err = _load(task_id)
        if doc is None:
            res.add("TSK-001", BLOCK, err)
            return res.finish()
        res.inputs.update(manifest=str(path), state=doc.get("current_state"), outcome=doc.get("outcome"),
                          task_type=doc.get("task_type"))
        pending = [g.get("gate") for g in doc.get("gates") or [] if g.get("status") not in ("PASS", "NOT_APPLICABLE")]
        nxt = _next_state(doc)
        if action == "status":
            res.add("TSK-004", INFO,
                    f"{task_id}: {doc.get('task_type')} {doc.get('current_state')} {doc.get('outcome')}; "
                    f"next={nxt or 'none'}; pending_gates={pending or 'none'}; {_budget_note(doc)}")
            return res.finish()
        src = doc.get("current_state")
        if src in ("WAITING_FOR_USER", "S_ESCALATE") and not doc.get("resume_state"):
            res.add("TSK-003", BLOCK, f"{src} needs manifest.resume_state before resume")
            return res.finish()
        if not nxt:
            res.add("TSK-004", INFO, f"{task_id} has no successor from {src} (terminal or blocked)")
            return res.finish()
        res.add("TSK-004", INFO,
                f"resume {task_id} -> {nxt}. Check, then apply if exit 0:\n"
                f"  python -m crewline manifest {path} --to {nxt}\n"
                f"  python -m crewline manifest {path} --to {nxt} --apply\n"
                "This does not walk the crew.")
        return res.finish()
    if action == "start":
        if not task_id:
            res.add("TSK-001", BLOCK, "pass --id")
            return res.finish()
        path, doc, err = _load(task_id)
        if doc is None:
            if task_type:
                created = run("new", task_id, task_type)
                if created.status != "PASS":
                    return created
                path, doc, err = _load(task_id)
            if doc is None:
                res.add("TSK-001", BLOCK, err)
                return res.finish()
        nxt = _next_state(doc)
        if not nxt:
            res.add("TSK-007", INFO, f"{task_id} has no successor from {doc.get('current_state')} (terminal or blocked)")
            return res.finish()
        check = manifest.transition(str(path), nxt)
        packet_hint = ""
        from .packets import PACKET_FOR, packet_path
        need = PACKET_FOR.get(nxt)
        if need:
            p = packet_path(str(path), need)
            packet_hint = f" packet packets/{need}.md: {'present' if p.is_file() else 'MISSING'}"
        res.add("TSK-007", INFO,
                f"start {task_id} is a conductor, not the crew. Next:\n"
                f"  python -m crewline manifest {path} --to {nxt}\n"
                f"  python -m crewline manifest {path} --to {nxt} --apply   # only if the check is PASS\n"
                f"check={check.status}{packet_hint}. Play the specialist for {nxt}; do not skip --apply.")
        if check.status == "FAIL":
            for f in check.findings:
                if f.severity == "BLOCK":
                    res.add(f.rule_id, BLOCK, f.message, path=f.path, evidence=f.evidence)
        return res.finish()
    if action == "export":
        if not task_id:
            res.add("TSK-001", BLOCK, "pass --id")
            return res.finish()
        if not out:
            res.add("TSK-005", BLOCK, "pass --out <dir-or-zip>")
            return res.finish()
        return _export(task_id, out, res)
    if action == "import":
        if not archive:
            res.add("TSK-005", BLOCK, "pass --from <dir-or-zip>")
            return res.finish()
        return _import(archive, task_id, res)
    res.add("TSK-002", BLOCK, f"unknown action {action!r}: list, status, resume, new, start, export, import, activate, deactivate, worktree")
    return res.finish()
