"""Module discovery (Phase 0, task type `module-onboarding`): read a repository and draft its engineering and SDET
context, every fact with the file it came from. Read-only on the repository; writes drafts into the local task workspace.

  python -m crewline discover --repo <module repo> --module <name> [--out <dir>] [--force]

Writes the module's `.crewline/` layout as drafts (default out: <module clone>/.crewline-work/onboarding/, the module's local,
excluded work dir; after the owner's review a documentation task copies `.crewline/` into the module repository):
  .crewline/README.md                      what the folder is, when it was verified, how to refresh it
  .crewline/module.yaml                    index: module, upstream, stack, which role folders exist
  .crewline/development/discovery.json       every fact, machine-readable, with evidence
  .crewline/development/local-context.md     DRAFT: build/test/coverage commands, CI, DB layout, deploy, default approved paths
  .crewline/qa/module-context.md           DRAFT (only when the repo has api-test/): suites, cases, script classes, baseline
  .crewline/qa/profile.draft.yaml          DRAFT validator profile derived from the real YAML (rename to profile.yaml after review)
Existing files are never overwritten without --force. A human (module owner) reviews the drafts before use.

  DSC-001  JaCoCo present but surefire argLine does not include ${argLine}/@{argLine}: coverage will not attach   WARN
  DSC-002  no JaCoCo in any pom: diff-coverage cannot run until it is added                                       WARN
  DSC-003  existing migrations without a rollback/revoke twin (legacy; recorded as baseline, not blocking)        INFO
  DSC-004  API TestRig YAML that does not parse                                                                    WARN
  DSC-005  API TestRig YAML not wired into any suite (candidate for ignore_yaml)                                   INFO
  DSC-006  repository guidance file present (AGENTS.md / CLAUDE.md / CONTRIBUTING.md): it wins where more specific INFO
  DSC-007  draft not written because the file exists (use --force after review)                                    WARN
  DSC-008  a Node project with tests whose coverage reporters do not include lcov (diff-coverage cannot read it)    WARN
  DSC-009  coding-standard checkers found (spotless/checkstyle/eslint/prettier): run before review                    INFO
  DSC-010  no checker found: drafts .crewline/development/standards-proposal.md for the module owner                  INFO
"""
from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .apitestrig import Module, parse_cases
from .gitio import git
from .migration import FORWARD
from .result import INFO, WARN, Result

CORE = Path(__file__).resolve().parents[2]
NS = re.compile(r"^\{.*?\}")
SKIP = {"target", "node_modules", ".git", "api-test"}


def _strip(tag: str) -> str:
    return NS.sub("", tag)


def _line_of(text: str, needle: str) -> int:
    i = text.find(needle)
    return text.count("\n", 0, i) + 1 if i >= 0 else 0


def _canonical_remotes(root: Path) -> list[str]:
    """The remote the module is published from: `upstream` when present (fork model), else `origin`; never a personal fork."""
    named: dict[str, str] = {}
    for line in git(root, "remote", "-v").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            named.setdefault(parts[0], parts[1])
    pick = named.get("upstream") or named.get("origin")
    return [pick] if pick else sorted(set(named.values()))


def _long(root: Path) -> Path:
    """Windows: scan through the \\\\?\\ prefix so files past MAX_PATH (deep test packages in a worktree) stay readable."""
    if os.name != "nt" or str(root).startswith("\\\\?\\"):
        return root
    return Path("\\\\?\\" + str(root.resolve()))


def _rel(root: Path, p: Path) -> str:
    return p.relative_to(root).as_posix()


def _poms(root: Path) -> list[Path]:
    out = []
    for p in root.rglob("pom.xml"):
        if not SKIP & set(p.relative_to(root).parts[:-1]):
            out.append(p)
    return sorted(out, key=lambda p: (len(p.parts), str(p)))


def _pom_facts(root: Path, pom: Path) -> dict:
    text = pom.read_text(encoding="utf-8", errors="replace")
    tree = ET.fromstring(text)
    props = {}
    for el in tree.iter():
        if _strip(el.tag) == "properties":
            props = {_strip(c.tag): (c.text or "").strip() for c in el}
            break
    artifacts = {(_strip(e.tag), (e.text or "").strip()) for e in tree.iter() if _strip(e.tag) == "artifactId"}
    names = {a for _, a in artifacts}
    modules = [(m.text or "").strip() for m in tree.iter() if _strip(m.tag) == "module"]
    arg_lines = [(a.text or "") for a in tree.iter() if _strip(a.tag) == "argLine"]
    rel = _rel(root, pom)
    ui_markers = ("selenium", "cucumber", "browserstack", "playwright", "appium")
    is_ui_test = any(m in text.lower() for m in ui_markers) and "spring-boot-starter-web" not in names
    return {
        "path": rel,
        "kind": "ui-test" if is_ui_test else "service",
        "artifact": next((c.text for c in tree if _strip(c.tag) == "artifactId"), None),
        "modules": modules,
        "java_version": props.get("java.version") or props.get("maven.compiler.source") or props.get("maven.compiler.release"),
        "java_version_ref": f"{rel}:{_line_of(text, '<java.version>')}" if "<java.version>" in text else rel,
        "jacoco": "jacoco-maven-plugin" in names,
        "jacoco_ref": f"{rel}:{_line_of(text, 'jacoco-maven-plugin')}" if "jacoco-maven-plugin" in names else "",
        "surefire_argline": [a.strip().split()[0] if a.strip() else "" for a in arg_lines],
        "surefire_argline_keeps_agent": (not arg_lines) or any("${argLine}" in a or "@{argLine}" in a for a in arg_lines),
        "test_libs": sorted(n for n in names if n in {"junit", "junit-jupiter", "junit-jupiter-api", "junit-vintage-engine",
                                                    "testng", "mockito-core", "mockito-inline", "spring-boot-starter-test",
                                                    "powermock-module-junit4"}),
    }


def _java_tests(root: Path, svc_dir: Path) -> dict:
    files = [p for p in svc_dir.rglob("*.java") if "src/test/" in p.as_posix() and "target" not in p.parts]
    counts = Counter()
    example = ""
    for f in files:
        t = f.read_text(encoding="utf-8", errors="replace")
        if "MockMvc" in t:
            counts["mockmvc"] += 1
            if not example and "Controller" in f.name:
                example = _rel(root, f)
        if "@WebMvcTest" in t:
            counts["webmvctest"] += 1
        if "@SpringBootTest" in t:
            counts["springboottest"] += 1
    main = [p for p in svc_dir.rglob("*.java") if "src/main/" in p.as_posix() and "target" not in p.parts]
    return {"test_files": len(files), "main_files": len(main), **counts, "component_test_example": example}


TEST_FILE_JS = re.compile(r"(__tests__/.*|\.(test|spec))\.[cm]?[jt]sx?$")


def _node_projects(root: Path) -> list[dict]:
    """package.json projects (not node_modules): scripts, test stack, coverage reporters, test-file count."""
    out = []
    for pkg in sorted(root.rglob("package.json")):
        parts = pkg.relative_to(root).parts
        if "node_modules" in parts or len(parts) > 4:
            continue
        try:
            d = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
        except ValueError:
            continue
        deps = {**d.get("dependencies", {}), **d.get("devDependencies", {})}
        pdir = pkg.parent
        jest_cfg = next((p for p in (pdir / "jest.config.js", pdir / "jest.config.ts", pdir / "jest.config.cjs") if p.exists()), None)
        cfg_text = jest_cfg.read_text(encoding="utf-8", errors="replace") if jest_cfg else json.dumps(d.get("jest") or {})
        m = re.search(r"coverageReporters\s*[:=]\s*\[([^\]]*)\]", cfg_text) or re.search(r'"coverageReporters":\s*\[([^\]]*)\]', cfg_text)
        reporters = re.findall(r"[\"']([\w-]+)[\"']", m.group(1)) if m else ["clover", "json", "lcov", "text"]  # Jest defaults
        tests = [p for p in pdir.rglob("*") if p.is_file() and "node_modules" not in p.parts
                 and TEST_FILE_JS.search(p.relative_to(pdir).as_posix())]
        runner = "jest" if "jest" in deps else "vitest" if "vitest" in deps else "react-scripts" if "react-scripts" in deps else ""
        nvmrc = (pdir / ".nvmrc").read_text().strip() if (pdir / ".nvmrc").exists() else ""
        out.append({
            "path": _rel(root, pdir) or ".", "name": d.get("name"), "node": (d.get("engines") or {}).get("node") or nvmrc,
            "scripts": d.get("scripts", {}), "runner": runner,
            "libs": sorted(k for k in deps if any(x in k for x in ("jest", "vitest", "testing-library", "cypress", "playwright",
                                                                    "eslint", "typescript", "react-scripts", "webpack", "vite"))),
            "lockfile": next((f for f in ("package-lock.json", "yarn.lock", "pnpm-lock.yaml") if (pdir / f).exists()), ""),
            "jest_config": _rel(root, jest_cfg) if jest_cfg else "", "coverage_reporters": reporters,
            "lcov": "lcov" in reporters, "test_files": len(tests),
        })
    return out


def _migrations(root: Path) -> dict:
    out = {"dirs": [], "forward": 0, "unpaired": []}
    for d in ("db_upgrade_scripts", "db_release_scripts", "db_scripts"):
        if (root / d).is_dir():
            out["dirs"].append(d)
    for p in sorted(root.rglob("*.sql")):
        rel = "/" + _rel(root, p)
        if "target" in p.parts:
            continue
        for rx, twins in FORWARD:
            m = rx.match(rel)
            if m:
                out["forward"] += 1
                if not any((root / t.lstrip("/")).exists() for t in twins(m)):
                    out["unpaired"].append(rel.lstrip("/"))
                break
    return out


def _api_test(root: Path) -> dict | None:
    at = root / "api-test"
    res = at / "src/main/resources"
    if not res.is_dir():
        return None
    suites = []
    for s in sorted((at / "testNgXmlFiles").glob("*.xml")) if (at / "testNgXmlFiles").is_dir() else []:
        try:
            tests = [t for t in ET.parse(s).getroot().iter("test")]
        except ET.ParseError:
            continue
        if any(p.get("name") == "ymlFile" for t in tests for p in t.findall("parameter")):
            suites.append(_rel(at, s))
    exclude = [d.name for d in res.iterdir() if d.is_dir() and d.name in ("config", "dbFiles", "coverage")]
    profile = {"resources_root": "src/main/resources", "suite_files": suites, "exclude_dirs": exclude}
    mod = Module(at, profile)
    # module-local script classes: literal ID fields they store, and whether they store without a `_sid` check
    # The gate is the case-name token checked just before the call (`contains("_pos")`, `contains("_sid")` ...), or none.
    extra_fields, gates, ungated, from_input = {}, {}, [], {}
    per_case = re.compile(r"\.has\(\s*\"(_?[A-Za-z]*[Ii]d[Kk]ey[A-Za-z]*)\"\s*\)")
    call = re.compile(r"\bwriteAutoGeneratedId\s*\(\s*([^,]+?),\s*(?:\"([A-Za-z0-9_]+)\")?")
    for src in sorted((at / "src/main/java").rglob("*.java")) if (at / "src/main/java").is_dir() else []:
        lines = src.read_text(encoding="utf-8", errors="replace").splitlines()
        k = per_case.search("\n".join(lines))
        if k and any(call.search(l) for l in lines):
            from_input[src.stem] = k.group(1)
        for i, line in enumerate(lines):
            m = call.search(line)
            if not m or "void writeAutoGeneratedId" in line:
                continue
            window = " ".join(lines[max(0, i - 4):i + 1]).lower()
            g = re.search(r"contains\(\s*\"(_[a-z0-9]+)\"\s*\)", window)
            gate = g.group(1) if g else None
            if m.group(2):
                extra_fields.setdefault(src.stem, set()).add(m.group(2))
                if gate and gate != "_sid":
                    gates[src.stem] = gate
            if gate is None:
                ungated.append(src.stem)
    used = {t.script_class.rsplit(".", 1)[-1] for t in mod.suite_tests()}
    profile_rules_extra = {"extra_id_fields_by_class": {k: sorted(v) for k, v in extra_fields.items() if k in used},
                           "extra_id_fields_gate": {k: v for k, v in gates.items() if k in used},
                           "capture_without_sid": sorted({c for c in ungated if c in used}),
                           "id_key_from_input": {k: v for k, v in from_input.items() if k in used}}
    wired = {t.yml.strip("/") for t in mod.suite_tests() if t.yml}
    classes = Counter(t.script_class.rsplit(".", 1)[-1] for t in mod.suite_tests())
    cases, errors = [], {}
    for rel in mod.yaml_files():
        try:
            cases += parse_cases(mod.read(rel), rel)
        except yaml.YAMLError as e:
            errors[rel] = str(e).splitlines()[0]
    unwired = sorted(y for y in mod.yaml_files() if y not in wired and y not in errors)
    uids = [c.uid for c in cases if c.uid]
    tok = Counter(u.split("_")[1] for u in uids if u.startswith("TC_") and u.count("_") >= 3)
    prefix = Counter(c.name.split("_")[0] + "_" for c in cases)
    module_tok = tok.most_common(1)[0][0] if tok else ""
    # the narrowest observed convention that covers >= 95% of IDs; what it leaves out is real naming debt
    candidates = [rf"^TC_(?i:{re.escape(module_tok)})_[A-Za-z0-9]+_\d+$",
                  rf"^TC_(?i:{re.escape(module_tok)})_[A-Za-z0-9_]+_\d+$",
                  r"^TC_[A-Za-z0-9]+_[A-Za-z0-9_]+_\d+$"] if module_tok else []
    scored = [(sum(1 for u in uids if re.match(p, u)), p) for p in candidates]
    pattern, matching = next(((p, n) for n, p in scored if uids and n >= 0.95 * len(uids)),
                             max(((p, n) for n, p in scored), key=lambda x: x[1], default=("", 0)))
    # case-name families: every prefix carrying >= 5% of the cases is a convention; rarer ones are deviations
    families = [p for p, n in prefix.most_common() if cases and n >= 0.05 * len(cases)]
    dupes = [u for u, n in Counter(u.lower() for u in uids).items() if n > 1]
    return {
        "root": "api-test", "suite_files": suites, "yaml_files": len(mod.yaml_files()), "cases": len(cases),
        "with_unique_identifier": len(uids), "parse_errors": errors, "unwired_yaml": unwired,
        "script_classes": dict(classes.most_common()), "id_key_names": sorted({k for t in mod.suite_tests() for k in t.id_key_names}),
        "case_name_prefix": prefix.most_common(1)[0] if prefix else None,
        "case_name_families": {p: prefix[p] for p in families},
        "case_name_deviations": {p: n for p, n in prefix.items() if p not in families},
        "uid_module_token": tok.most_common(3), "uid_pattern": pattern, "uid_pattern_matches": matching,
        "duplicate_uids": dupes, "coverage_matrix": (res / "coverage").is_dir(),
        "profile": {"module_code": module_tok.upper(), **profile, "ignore_yaml": unwired,
                    "testcase_rules": {"case_name_prefix": (families[0] if len(families) == 1 else families) if families else "",
                                       "unique_identifier_pattern": pattern, **profile_rules_extra,
                                       "severity": {"NAME-003": "WARN"}}},
        "id_capture": profile_rules_extra,
    }


def collect(repo: str) -> dict:
    root = Path(git(repo, "rev-parse", "--show-toplevel").strip())
    facts: dict = {"repo": str(root), "repo_name": root.name, "head": git(root, "rev-parse", "HEAD").strip(),
                   "branch": git(root, "rev-parse", "--abbrev-ref", "HEAD").strip(),
                   "remotes": _canonical_remotes(root),
                   "discovered_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    if facts["remotes"]:                                   # a worktree folder is not the repository's name
        facts["repo_name"] = re.sub(r"\.git$", "", facts["remotes"][0].rstrip("/").rsplit("/", 1)[-1])
    root = _long(root)
    poms = [_pom_facts(root, p) for p in _poms(root)]
    parents = [p for p in poms if p["modules"]]
    facts["maven"] = {"poms": poms, "parent": parents[0]["path"] if parents else (poms[0]["path"] if poms else None)}
    services = [p for p in poms if not p["modules"] and p["kind"] == "service"]
    facts["services"] = {p["path"].rsplit("/pom.xml", 1)[0] or ".": _java_tests(root, root / p["path"].rsplit("pom.xml", 1)[0])
                         for p in services}
    facts["ui_test_projects"] = [p["path"].rsplit("/pom.xml", 1)[0] for p in poms if p["kind"] == "ui-test"]
    facts["node"] = _node_projects(root)
    wf = root / ".github/workflows"
    facts["ci"] = {"workflows": sorted(f.name for f in wf.glob("*.y*ml")) if wf.is_dir() else [],
                   "uses": sorted({m for f in (wf.glob("*.y*ml") if wf.is_dir() else [])
                                   for m in re.findall(r"uses:\s*([\w./-]+)@", f.read_text(encoding="utf-8", errors="replace"))})}
    facts["db"] = _migrations(root)
    facts["deploy"] = {"helm": sorted(_rel(root, p.parent) for p in root.glob("helm/*/Chart.yaml")),
                       "dockerfiles": sorted(_rel(root, p) for p in root.rglob("Dockerfile") if "target" not in p.parts)}
    facts["guidance"] = [n for n in ("AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md", ".github/CODEOWNERS",
                                     "api-test/src/main/resources/coverage/AGENTS.md",
                                     "api-test/src/main/resources/coverage/README.md") if (root / n).exists()]
    facts["api_test"] = _api_test(root)
    from .coding_standards import detect as detect_standards
    facts["standards"] = detect_standards(root)
    return facts


def _engineering_md(name: str, f: dict) -> str:
    m = f["maven"]
    parent = m["parent"] or "pom.xml"
    pdir = parent.rsplit("/pom.xml", 1)[0] if "/" in parent else "."
    java = sorted({f"{p['java_version']} ({p['path']})" for p in m["poms"] if p["java_version"]})
    jacoco = [p for p in m["poms"] if p["jacoco"]]
    agent_ok = all(p["surefire_argline_keeps_agent"] for p in m["poms"])
    libs = sorted({l for p in m["poms"] for l in p["test_libs"]})
    rows = "\n".join(f"| `{k}` | {v['main_files']} main / {v['test_files']} test | MockMvc {v.get('mockmvc', 0)}, "
                     f"@WebMvcTest {v.get('webmvctest', 0)}, @SpringBootTest {v.get('springboottest', 0)} |"
                     for k, v in f["services"].items() if v["main_files"])
    example = next((v["component_test_example"] for v in f["services"].values() if v["component_test_example"]), "")
    svc_globs = "\n".join(f"- `{k}/src/main/java/**`, `{k}/src/test/java/**`" for k, v in f["services"].items() if v["main_files"])
    db = f["db"]
    return f"""# {name}: local engineering context (DRAFT - generated by `crewline discover`, review before use)

Evidence: `{f['repo_name']}` @ {f['head'][:10]} ({f['branch']}), discovered {f['discovered_at']}. Every row is **Verified** from the
file named unless marked Inferred. A module owner reviews this draft; then copy it into the repository as `.crewline/development/`.

| Item | Value | Evidence |
|---|---|---|
| Remotes | {', '.join(f['remotes']) or 'none'} | `git remote -v` |
| Maven parent | `{parent}` | file |
| Java | {', '.join(java) or 'Unknown'} | {', '.join(p['java_version_ref'] for p in m['poms'] if p['java_version'])[:200] or '-'} |
| Unit-test libraries | {', '.join(libs) or 'none found'} | poms |
| JaCoCo | {'yes' if jacoco else '**no**'}{'' if agent_ok else ' - **surefire argLine drops ${argLine}: coverage will not attach (DSC-001)**'} | {', '.join(p['jacoco_ref'] for p in jacoco)[:200] or '-'} |
| CI workflows | {', '.join(f['ci']['workflows']) or 'none'} | `.github/workflows/` |
| Reusable CI | {', '.join(f['ci']['uses'])[:300] or 'none'} | workflow `uses:` |
| DB scripts | {', '.join(db['dirs']) or 'none'}; {db['forward']} forward scripts, {len(db['unpaired'])} without a rollback twin (legacy) | `crewline migration` conventions |
| Deploy | helm: {', '.join(f['deploy']['helm']) or 'none'}; Dockerfiles: {len(f['deploy']['dockerfiles'])} | files |
| Repository guidance | {', '.join(f['guidance']) or 'none'} | files (they win where more specific) |
| API tests | {'yes: see .crewline/qa/module-context.md' if f['api_test'] else 'no api-test/'} | `api-test/` |

## Services and tests

| Service | Sources | Component-test styles |
|---|---|---|
{rows}

{_node_md(f)}{_ui_md(f)}
## Commands (Inferred from the layout; mark Verified once run)

- Build: `mvn -B -f {parent} -Dmaven.gitcommitid.skip=true -pl <service> -am verify`
- Unit tests: `mvn -B -f {parent} -Dmaven.gitcommitid.skip=true -pl <service> -am test` -> `<service>/target/surefire-reports/TEST-*.xml`
- Coverage: same with `jacoco:report` -> `<service>/target/site/jacoco/jacoco.xml`, then `crewline diff-coverage`
- Component tests: follow {('`' + example.split(pdir + '/', 1)[-1] + '`') if example else 'the module controller tests (none with MockMvc found: Inferred gap)'}
- Evidence: wrap every command in `python -m crewline run --repo <worktree> --out <task>/evidence/<name>.json -- <command>`
- Coding standards: {('; '.join('`' + ' '.join(t['command']) + '`' for t in f.get('standards', {}).get('tools', [])) or 'none found (Crewline will not invent a style guide; debug leftovers in the production diff still block)')} → `crewline coding-standards`

## Default approved paths (the plan narrows them per task)

{svc_globs or '- (no Java services found)'}
- API tests: `api-test/src/main/resources/**`, `api-test/testNgXmlFiles/**`
- DB: {', '.join(f'`{d}/**`' for d in db['dirs']) or 'none'} (database-migration tasks only)
- Sensitive, always `scope_change`: `.github/**`, `helm/**`, `deploy/**`, `**/application*.properties`, Dockerfiles

## Open (owner to confirm)
- Environment that runs a branch build for system tests (Crewline open question Q8).
- Coverage threshold if the team's standard differs from 80%.
"""


def _node_md(f: dict) -> str:
    if not f.get("node"):
        return ""
    rows = []
    for n in f["node"]:
        s = n["scripts"]
        test = next((f"npm run {k}" for k in ("test:ci", "test") if k in s), "")
        rows.append(f"| `{n['path']}` ({n['name']}) | {n['runner'] or '?'}; {n['test_files']} test files | `{test or 'no test script'}` | "
                    f"{'lcov: yes' if n['lcov'] else '**lcov: no** (reporters ' + ', '.join(n['coverage_reporters']) + '; add `--coverageReporters=lcov` for diff-coverage)'} | "
                    f"{n['lockfile'] or 'no lockfile'}; node {n['node'] or 'unspecified'} |")
    return ("\n## Node / front-end projects\n\n| Project | Test runner | Test command | Coverage for diff-coverage | Lockfile / Node |\n"
            "|---|---|---|---|---|\n" + "\n".join(rows) + "\n\n"
            "Commands (Inferred): `npm ci` then the test command; coverage at `<project>/coverage/lcov.info` -> "
            "`crewline diff-coverage --jacoco <project>/coverage --include '*<project>/src/*'`. "
            "Unit-test integrity covers Jest/Vitest files (`crewline test-integrity`).\n")


def _ui_md(f: dict) -> str:
    if not f.get("ui_test_projects"):
        return ""
    return ("\n## UI test projects (SDET specialty: ui-automation)\n\n" + "\n".join(f"- `{p}` (Selenium/Cucumber-style Maven project; "
            "not a service: excluded from unit/coverage gates)" for p in f["ui_test_projects"]) + "\n")


CHECKERS = {   # the usual checker per stack; the proposal only suggests, the module owner decides
    "Maven / Java": ("Spotless (`spotless-maven-plugin`) or Checkstyle (`maven-checkstyle-plugin`)", "mvn -B spotless:check"),
    "JS / TS": ("ESLint and Prettier", "npx eslint --format unix ."),
    "Kotlin": ("ktlint or detekt", "ktlint"),
    "Python": ("ruff", "ruff check --output-format concise ."),
}


def _stacks(root: Path, f: dict) -> list[str]:
    def found(*names: str) -> bool:
        return any(p for n in names for p in root.glob(n) if "node_modules" not in p.parts)
    out = []
    if f["maven"]["poms"]:
        out.append("Maven / Java")
    if f["node"]:
        out.append("JS / TS")
    if found("build.gradle.kts", "*/build.gradle.kts", "*/*.kts"):
        out.append("Kotlin")
    if found("pyproject.toml", "setup.py", "*/pyproject.toml", "*/setup.py"):
        out.append("Python")
    return out


def _standards_proposal(name: str, stacks: list[str]) -> str:
    rows = "\n".join(f"| {s} | {CHECKERS[s][0]} | `{CHECKERS[s][1]}` |" for s in stacks)
    return f"""# Coding-standards proposal: {name} (DRAFT)

No checker was found, so `crewline coding-standards` only blocks debug leftovers in this repository (STD-004). It does not check style.
Crewline does not invent a standard; these are the usual checkers for what this repository contains. The module owner decides.

| Stack | Usual checker | Command for `crewline run` |
|---|---|---|
{rows}

If the owner agrees:
1. Add the checker in its own small PR (a `documentation` or `refactor` task), with no mass reformat.
2. Set `standards: {{mode: changed-lines}}` in `.crewline/module.yaml`, so existing violations do not block new work; only lines a
   change touches do (STD-006). The checker's output has to name `path:line`; the commands above do.
3. Run the command through `crewline run` before IMPLEMENTATION_REVIEW, as for any checker.
"""


def _sdet_md(name: str, f: dict, baseline: dict | None) -> str:
    a = f["api_test"]
    pfx = a["case_name_prefix"]
    return f"""# {name}: API test context (DRAFT - generated by `crewline discover`, review before use)

Evidence: `{f['repo_name']}/api-test` @ {f['head'][:10]}. Profile draft: `.crewline/qa/profile.draft.yaml`.

| Item | Value |
|---|---|
| Suites (with ymlFile tests) | {', '.join(a['suite_files']) or 'none'} |
| YAML files / cases | {a['yaml_files']} / {a['cases']} ({a['with_unique_identifier']} with uniqueIdentifier) |
| Case-name families | {', '.join(f'`{p}` ({n})' for p, n in a['case_name_families'].items()) or '?'} of {a['cases']}; deviations: {', '.join(f'`{p}` ({n})' for p, n in a['case_name_deviations'].items()) or 'none'} |
| uniqueIdentifier pattern | `{a['uid_pattern'] or 'Unknown'}` ({a['uid_pattern_matches']}/{a['with_unique_identifier']} match) |
| Duplicate uniqueIdentifiers | {len(a['duplicate_uids'])} |
| Script classes | {', '.join(f'{k} ({v})' for k, v in list(a['script_classes'].items())[:12])} |
| idKeyName values | {', '.join(a['id_key_names'])[:300] or 'none'} |
| Unwired YAML (ignore_yaml candidates) | {len(a['unwired_yaml'])} |
| YAML that does not parse | {len(a['parse_errors'])} |
| Coverage matrix | {'yes' if a['coverage_matrix'] else 'no'} |
| Baseline (`crewline testcase --scope all` with the draft profile) | {baseline['summary'] if baseline else 'not run'} |

Existing debt in the baseline is recorded, not fixed: later tasks validate only what they change (`--scope changed`).
ID capture found in the module's own script classes (Inferred from source; confirm):
- extra fields by class: {a['id_capture']['extra_id_fields_by_class'] or 'none'}
- extra fields captured on another case-name token than `_sid` (`extra_id_fields_gate`): {a['id_capture']['extra_id_fields_gate'] or 'none'}
- classes that store IDs without a `_sid` check (`capture_without_sid`): {a['id_capture']['capture_without_sid'] or 'none'}
- classes that read the ID key per case from an input field (`id_key_from_input`): {a['id_capture']['id_key_from_input'] or 'none'}
Commons script classes are not scanned: remaining DEP-001 findings may still need entries here.
"""


def run(repo: str, module: str, out: str | None = None, force: bool = False) -> Result:
    res = Result("discover", inputs={"repo": repo, "module": module})
    try:
        facts = collect(repo)
    except (RuntimeError, OSError, ET.ParseError) as e:
        return Result.not_run("discover", str(e))
    target = Path(out) if out else Path(facts["repo"]) / ".crewline-work" / "onboarding"
    poms = facts["maven"]["poms"]
    if any(p["jacoco"] for p in poms) and not all(p["surefire_argline_keeps_agent"] for p in poms):
        res.add("DSC-001", WARN, "surefire argLine does not include ${argLine}: JaCoCo agent will not attach",
                path=next(p["path"] for p in poms if not p["surefire_argline_keeps_agent"]))
    if poms and not any(p["jacoco"] for p in poms):
        res.add("DSC-002", WARN, "no jacoco-maven-plugin in any pom: diff-coverage cannot run until it is added")
    for n in facts["node"]:
        if n["test_files"] and not n["lcov"]:
            res.add("DSC-008", WARN, f"{n['path']}: Jest coverage reporters {n['coverage_reporters']} have no lcov; "
                                     "diff-coverage needs --coverageReporters=lcov", path=n["jest_config"] or n["path"])
    if facts["db"]["unpaired"]:
        res.add("DSC-003", INFO, f"{len(facts['db']['unpaired'])} existing forward migration(s) without a rollback twin (legacy baseline)",
                evidence=", ".join(facts["db"]["unpaired"][:5]))
    for g in facts["guidance"]:
        res.add("DSC-006", INFO, f"{g} present: read it; it wins where it is more specific", path=g)
    baseline = None
    a = facts["api_test"]
    if a:
        for y, err in a["parse_errors"].items():
            res.add("DSC-004", WARN, f"does not parse: {err}", path=f"api-test/src/main/resources/{y}")
        if a["unwired_yaml"]:
            res.add("DSC-005", INFO, f"{len(a['unwired_yaml'])} YAML file(s) not referenced by any suite (ignore_yaml candidates)")
        if a["uid_pattern"]:
            from . import testcase
            r = testcase.run(Module(Path(facts["repo"]) / "api-test", a["profile"]), "all", None)
            baseline = {"status": r.status, "summary": r.summary,
                        "by_rule": dict(Counter(f.rule_id for f in r.findings))}
    facts["baseline"] = baseline
    if facts.get("standards", {}).get("tools"):
        res.add("DSC-009", INFO, "coding-standard checkers: " +
                ", ".join(t["id"] for t in facts["standards"]["tools"]) +
                " — run via crewline run before IMPLEMENTATION_REVIEW")
    stacks = [] if facts.get("standards", {}).get("tools") else _stacks(Path(facts["repo"]), facts)
    if stacks:
        res.add("DSC-010", INFO, f"no coding-standard checker: proposal for {', '.join(stacks)} in .crewline/development/standards-proposal.md")
    public = {k: v for k, v in facts.items() if k != "repo"}   # drafts are published: no local paths
    files = {".crewline/development/discovery.json": json.dumps(public, indent=2, default=str) + "\n",
             ".crewline/development/local-context.md": _engineering_md(module, facts)}
    if stacks:
        files[".crewline/development/standards-proposal.md"] = _standards_proposal(module, stacks)
    if a:
        files[".crewline/qa/module-context.md"] = _sdet_md(module, facts, baseline)
        files[".crewline/qa/profile.draft.yaml"] = ("# DRAFT validator profile generated by crewline discover - review, then rename "
                                                    "to .crewline/qa/profile.yaml\n" + yaml.safe_dump(a["profile"], sort_keys=False))
    roles = {"development": "development/local-context.md"}
    if a:
        roles["qa"] = ["qa/module-context.md", "qa/known-issues.md", "qa/profile.yaml"]
    files[".crewline/module.yaml"] = yaml.safe_dump({
        "module": module, "remotes": facts["remotes"],
        "verified_at": {"commit": facts["head"], "date": facts["discovered_at"]},
        "stack": {"maven_parent": facts["maven"]["parent"], "node_projects": [n["path"] for n in facts["node"]],
                  "ui_test_projects": facts["ui_test_projects"], "api_test": bool(a)},
        "roles": roles}, sort_keys=False)
    files[".crewline/README.md"] = (
        f"# {module}: Crewline context\n\n"
        "Read by the Crewline agents (https://github.com/mosip/mosip-labs/tree/develop/crewline) before they work on this "
        "repository. One folder per role:\n\n"
        "- `development/` - build, test and coverage commands, CI, DB scripts, default approved paths\n"
        "- `qa/` - API/UI test context, known issues, validator profile\n"
        "- other roles (`product/`, `ui-ux/`, `devops/`, `security/`, ...) are added when the module has something to say\n\n"
        f"Verified at {facts['head'][:10]} on {facts['discovered_at'][:10]}. Refresh with the `module-onboarding` task; every "
        "fact cites the file it came from. The repository's own guidance (AGENTS.md, CONTRIBUTING.md) wins.\n")
    written = []
    for rel, text in files.items():
        p = target / rel
        if p.exists() and not force:
            res.add("DSC-007", WARN, f"{p} exists: draft not written (review the difference, then --force)", path=str(p))
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
        written.append(str(p))
    res.inputs.update(written=written, head=facts["head"], services=len(facts["services"]),
                      api_test_cases=a["cases"] if a else 0, baseline=baseline)
    res.finish()
    res.summary = (f"{len(facts['services'])} Maven module(s), {a['cases'] if a else 0} API cases, "
                   f"{len(written)} draft file(s) written; " + res.summary)
    return res
