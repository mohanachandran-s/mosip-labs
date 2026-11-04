"""Coding-standards gate: the repository's own checkers run before a human reviews format.

Review back-and-forth on import order, Checkstyle, Prettier and leftover debug prints is a process
failure. This validator does not invent a style guide (principle 1): it finds the tools the module
already has, demands a fresh `crewline run` record for each, and blocks debug leftovers in the
production diff. `IMPLEMENTATION_REVIEW` is refused until the gate PASSes.

  STD-001  a checker exists (spotless / checkstyle / eslint / …) but has no matching evidence record   BLOCK
  STD-002  the evidence record for a checker is not PASS (failed, stale, or incomplete)               BLOCK
  STD-003  production diff adds debug leftovers (System.out, console.log, printStackTrace, debugger)  BLOCK
  STD-004  no repo standard: only debug leftovers are checked (onboarding proposes a checker)          INFO
  STD-005  a config file exists (checkstyle.xml, .prettierrc, …) but no plugin/script to run it       WARN
  STD-006  changed-lines mode: the checker reports a violation on a line this change touched          BLOCK
  STD-007  changed-lines mode: the checker failed only on lines this change did not touch             INFO

Changed-lines mode (`--changed-lines`, or `standards: {mode: changed-lines}` in `.crewline/module.yaml`) lets a repo adopt a
checker without fixing its history first. The checker's log has to name `path:line`; a log it cannot map still blocks (STD-002).

  python -m crewline coding-standards --repo <dir> --base <ref> --manifest <m> [--evidence <record.json>]...
Evidence paths also come from `implementation.standards_evidence` on the manifest.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from . import evidence as evidence_mod
from . import workspace
from .change_scope import _match
from .gitio import changed_files, git, top
from .result import BLOCK, INFO, WARN, Result

MAVEN_GOALS = {
    "spotless-maven-plugin": "spotless:check",
    "maven-checkstyle-plugin": "checkstyle:check",
    "formatter-maven-plugin": "formatter:validate",
    "impsort-maven-plugin": "impsort:check",
    "spring-javaformat-maven-plugin": "spring-javaformat:validate",
}
NODE_SCRIPTS = ("lint", "lint:check", "format:check", "prettier:check", "eslint")
CONFIG_HINTS = (
    "checkstyle.xml", "google_checks.xml", ".prettierrc", ".prettierrc.json", ".prettierrc.js",
    "eslint.config.js", "eslint.config.mjs", ".eslintrc.json", ".eslintrc.cjs", ".eslintrc.js",
    ".editorconfig", ".clang-format", "ruff.toml",
)
SKIP_DIR = {"target", "node_modules", ".git"}
TEST_GLOBS = [
    "**/src/test/**", "**/src/tests/**", "**/__tests__/**",
    "**/*Test.java", "**/*Tests.java", "**/*IT.java",
    "**/*.spec.ts", "**/*.spec.tsx", "**/*.spec.js",
    "**/*.test.ts", "**/*.test.tsx", "**/*.test.js",
    "**/tests/**", "**/test/**", "**/test_*.py", "**/*_test.py", "**/conftest.py",
    "**/*Test.kt", "**/*Tests.kt", "**/*_test.go",
    "**/api-test/**", "**/apitest/**",
]
DEBUG_RX = re.compile(
    r"System\.out\.print|System\.err\.print|\.printStackTrace\s*\("
    r"|console\.(log|debug|info)\s*\(|\bdebugger\s*;"
)
ARTIFACT = re.compile(r"<artifactId>\s*([^<]+)\s*</artifactId>")


def _posix(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def is_test_path(path: str) -> bool:
    p = _posix(path)
    return any(_match(p, g) for g in TEST_GLOBS)


def detect(repo: str | Path) -> dict:
    """Runnable checkers + config files. Does not invent a command the repo does not already declare."""
    root = top(repo)
    tools: list[dict] = []
    for pom in sorted(root.rglob("pom.xml")):
        if SKIP_DIR & set(pom.relative_to(root).parts[:-1]):
            continue
        text = pom.read_text(encoding="utf-8", errors="replace")
        names = {m.group(1).strip() for m in ARTIFACT.finditer(text)}
        rel = pom.relative_to(root).as_posix()
        for plugin, goal in MAVEN_GOALS.items():
            if plugin in names:
                tools.append({"id": plugin, "kind": "maven", "marker": goal,
                              "command": ["mvn", "-B", "-f", rel, goal], "evidence": rel})
    for pkg in sorted(root.rglob("package.json")):
        if SKIP_DIR & set(pkg.relative_to(root).parts[:-1]):
            continue
        try:
            data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
        except ValueError:
            continue
        scripts = data.get("scripts") or {}
        rel = pkg.parent.relative_to(root).as_posix() or "."
        chosen = next((k for k in NODE_SCRIPTS if k in scripts), None)
        if chosen:
            tools.append({"id": f"npm:{chosen}", "kind": "node", "marker": f"npm run {chosen}",
                          "command": ["npm", "run", chosen], "evidence": pkg.relative_to(root).as_posix(),
                          "cwd": rel})
        elif "eslint" in {**data.get("dependencies", {}), **data.get("devDependencies", {})}:
            tools.append({"id": "eslint", "kind": "node", "marker": "eslint",
                          "command": ["npx", "eslint", "."], "evidence": pkg.relative_to(root).as_posix(),
                          "cwd": rel})
    configs = [p.relative_to(root).as_posix() for name in CONFIG_HINTS
               for p in [root / name] if p.is_file()]
    return {"tools": tools, "config_files": configs}


def _added_lines(repo: Path, base: str, path: str) -> list[tuple[int, str]]:
    out = git(repo, "diff", "-U0", base, "--", path, check=False)
    lines: list[tuple[int, str]] = []
    new_no = 0
    for line in out.splitlines():
        if line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            new_no = int(m.group(1)) if m else 0
            continue
        if line.startswith("+") and not line.startswith("+++"):
            lines.append((new_no, line[1:]))
            new_no += 1
        elif line.startswith("-") and not line.startswith("---"):
            continue
    return lines


def _covers(record_cmd: str, tool: dict) -> bool:
    return tool["marker"] in (record_cmd or "")


def _load_records(paths: list[str]) -> list[tuple[str, dict]]:
    out = []
    for p in paths:
        try:
            out.append((p, json.loads(Path(p).read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            out.append((p, {}))
    return out


LOCATION = re.compile(r"((?:[A-Za-z]:)?[\w./\\-]+\.\w{1,6})[:(\[]+(\d+)")


def _changed_mode(root: Path) -> bool:
    ctx = workspace.context_dir(root)
    try:
        mod = yaml.safe_load((ctx / "module.yaml").read_text(encoding="utf-8")) if ctx else None
    except (OSError, yaml.YAMLError):
        return False
    return ((mod or {}).get("standards") or {}).get("mode") == "changed-lines" if isinstance(mod, dict) else False


def _on_changed_lines(record: str, root: Path, base: str, changed: dict[str, str]) -> tuple[list[str], int] | None:
    """(violations on lines this change touched, violations elsewhere); None when the log names no path:line."""
    try:
        log = Path(json.loads(Path(record).read_text(encoding="utf-8")).get("log", ""))
        text = log.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None
    found = LOCATION.findall(text)
    if not found:
        return None
    touched: dict[str, set[int]] = {}
    for path, kind in changed.items():
        if kind != "D":
            touched[path] = {n for n, _ in _added_lines(root, base, path)}
            if not touched[path] and kind == "A" and (root / path).is_file():
                touched[path] = set(range(1, len((root / path).read_text(encoding="utf-8", errors="replace").splitlines()) + 1))
    hits, other = [], 0
    for where, line in found:
        norm = where.replace("\\", "/")
        path = next((p for p in touched if norm == p or norm.endswith("/" + p)), None)
        if path and int(line) in touched[path]:
            hits.append(f"{path}:{line}")
        else:
            other += 1
    return sorted(set(hits)), other


def run(repo: str, base: str, manifest_path: str, evidence_paths: list[str] | None = None, changed_lines: bool = False) -> Result:
    res = Result("coding-standards", inputs={"repo": repo, "base": base, "manifest": manifest_path})
    try:
        doc = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        root = top(repo)
        git(root, "rev-parse", "--verify", f"{base}^{{commit}}")
        changed = changed_files(repo, base)
    except (OSError, ValueError, RuntimeError) as e:
        return Result.not_run("coding-standards", str(e))

    disc = (workspace.context_dir(root) or root / ".crewline") / "development/discovery.json"
    found = detect(repo)
    if disc.is_file():
        try:
            extra = (json.loads(disc.read_text(encoding="utf-8")).get("standards") or {}).get("tools") or []
            seen = {t["id"] for t in found["tools"]}
            for t in extra:
                if t.get("id") not in seen:
                    found["tools"].append(t)
        except ValueError:
            pass
    res.inputs["tools"] = [t["id"] for t in found["tools"]]
    res.inputs["config_files"] = found["config_files"]

    paths = list(evidence_paths or [])
    paths += list((doc.get("implementation") or {}).get("standards_evidence") or [])
    records = _load_records(paths)

    if not found["tools"]:
        res.add("STD-004", INFO, "no repo standard; only debug leftovers checked. Crewline does not invent a style guide: "
                "onboarding proposes a checker for the module owner (.crewline/development/standards-proposal.md)")
        if found["config_files"]:
            res.add("STD-005", WARN, "style config present but no runnable plugin/script: "
                    + ", ".join(found["config_files"]),
                    remediation="wire the team's checker (spotless:check, npm run lint, …) and record it in .crewline/")
    for tool in found["tools"]:
        hit = next((p for p, rec in records if _covers(rec.get("command", ""), tool)), None)
        if not hit:
            cmd = " ".join(tool["command"])
            res.add("STD-001", BLOCK, f"{tool['id']}: no crewline run record for `{cmd}`",
                    path=tool.get("evidence", ""),
                    remediation=f"python -m crewline run --repo {repo} --out <task>/evidence/{tool['id']}.json -- {cmd}")
            continue
        ev = evidence_mod.run(hit, repo)
        only_exit = {f.rule_id for f in ev.findings if f.severity == BLOCK} == {"EVD-003"}
        if ev.status != "PASS" and only_exit and (changed_lines or _changed_mode(root)):
            mapped = _on_changed_lines(hit, root, base, changed)
            if mapped is not None:
                hits, other = mapped
                for where in hits:
                    res.add("STD-006", BLOCK, f"{tool['id']}: violation on a changed line, {where}", path=where.rsplit(":", 1)[0],
                            evidence=hit)
                if not hits:
                    res.add("STD-007", INFO, f"{tool['id']}: {other} existing violation(s), none on a line this change touched")
                continue
        if ev.status != "PASS":
            res.add("STD-002", BLOCK, f"{tool['id']}: evidence {hit} is {ev.status} ({ev.summary})",
                    path=hit, evidence=ev.summary)

    for path, kind in sorted(changed.items()):
        if kind == "D" or is_test_path(path):
            continue
        fp = root / path
        if not fp.is_file():
            continue
        added = _added_lines(root, base, path)
        if not added and kind == "A":
            added = list(enumerate(fp.read_text(encoding="utf-8", errors="replace").splitlines(), 1))
        for lineno, text in added:
            if DEBUG_RX.search(text):
                res.add("STD-003", BLOCK, f"{path}:{lineno} adds debug leftover in production code",
                        path=path, line=lineno, evidence=text.strip()[:120],
                        remediation="remove it; logging goes through the module's existing logger")
    return res.finish()
