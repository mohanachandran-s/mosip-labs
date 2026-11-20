"""Read per-test results from a run, for the regression gate.

Two sources:
- API TestRig Extent "full-report" HTML: cases are grouped under `<Subject> — Passed|Failed|Skipped` headings and
  listed by `uniqueIdentifier`. (testng-results.xml is not used: it labels out-of-scope cases as skips and does not
  carry the uniqueIdentifier.) Response bodies in the report are never returned.
- JUnit / Surefire / Failsafe XML (`TEST-*.xml`, searched recursively): `<classname>#<name>` -> status.
- DSL TestRig scenario report (`DSL-<env>-<level>-run-<epoch ms>-report_T-<n>_P-..._F-<n>.html`, written by the
  ivv-orchestrator's EmailableReport into `testng-report/`): each scenario is an element of class `tree-scenario-node`
  with `data-name="Scenario_<n>"` and `data-status` pass | fail | known | skip. `known` (a known issue) counts as FAIL,
  so a scenario that passed on base and is a known issue on the branch is a new break; `ignored` scenarios (excluded by
  configuration) are left out. Older reports (1.3/1.4) carry a summary row per scenario instead
  (`<tr class="passedeven|failedodd|knownissueeven|skipped..|ignored..">`), read the same way. The `-error-` report (failures only) and the `-report.html` without counts are not read.
- Cucumber JSON (`cucumber.json`, e.g. a UI rig's `target/cucumber.json`): `<feature>#<scenario id>` -> status. A failed
  step or hook fails the scenario; skipped, pending or undefined steps skip it.
"""
from __future__ import annotations

import glob
import json
import html
import re
import xml.etree.ElementTree as ET
from pathlib import Path

HEADING = re.compile(r"^(\S.*) — (Passed|Failed|Skipped)$")
UID = re.compile(r"^TC_[A-Za-z]+_[A-Za-z0-9]+_\d+$")
DSL_RUN = re.compile(r"^(DSL-.+)-run-(\d+)-report_T-\d+.*\.html$")
DSL_NODE = re.compile(r"<[a-z]+\b[^>]*\btree-scenario-node\b[^>]*>", re.I)
DSL_STATUS = {"pass": "PASS", "fail": "FAIL", "known": "FAIL", "skip": "SKIP"}
DSL_ROW = re.compile(r'<tr class="([a-z]+)"><td><a href="#m\d+">(Scenario_[A-Za-z0-9_]+)</a>', re.I)


def dsl_reports(folder: str | Path) -> list[Path]:
    """DSL TestRig run reports in `folder`, oldest first (by the epoch in the name)."""
    p = Path(folder)
    hits = [f for f in p.glob("DSL-*-run-*-report_T-*.html") if DSL_RUN.match(f.name)] if p.is_dir() else []
    return sorted(hits, key=lambda f: int(DSL_RUN.match(f.name).group(2)))


def dsl_key(report: str | Path) -> str | None:
    """`DSL-<env>-<level>` of a DSL run report, None for anything else."""
    m = DSL_RUN.match(Path(report).name)
    return m.group(1) if m else None


def dsl_status(path: str | Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for tag in DSL_NODE.findall(Path(path).read_text(encoding="utf-8", errors="replace")):
        name = re.search(r'\bdata-name="([^"]+)"', tag)
        status = re.search(r'\bdata-status="([^"]+)"', tag)
        if not name or not status:
            continue
        s = status.group(1).lower()
        if s.startswith("ignore"):
            continue
        out.setdefault(html.unescape(name.group(1)), DSL_STATUS.get(s, "SKIP"))
    if not out:                                        # the 1.3/1.4-era layout: a summary row per scenario
        for cls, name in DSL_ROW.findall(Path(path).read_text(encoding="utf-8", errors="replace")):
            s = re.sub(r"(even|odd)$", "", cls.lower())
            if s.startswith("ignore"):
                continue
            out.setdefault(name, {"passed": "PASS", "failed": "FAIL", "knownissue": "FAIL", "skipped": "SKIP"}
                           .get(s, "SKIP"))
    return out


def _lines(text: str) -> list[str]:
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", text)
    return [l.strip() for l in html.unescape(re.sub(r"<[^>]+>", "\n", text)).splitlines() if l.strip()]


def junit_files(path: str | Path) -> list[Path]:
    p = Path(path)
    if p.is_file():
        return [p] if p.suffix == ".xml" else []
    return sorted(p.rglob("TEST-*.xml")) if p.is_dir() else []


def find_full_report(path: str | Path) -> Path:
    p = Path(path)
    if p.is_file():
        return p
    hits = sorted(glob.glob(str(p / "*full-report*.html")))
    if hits:
        return Path(hits[-1])
    for folder in (p, p / "testng-report"):
        dsl = dsl_reports(folder)
        if dsl:
            return dsl[-1]                             # the newest run in a testng-report/ that keeps every run
    for cucumber in (p / "cucumber.json", p / "target" / "cucumber.json"):   # a UI rig's own report, not a deep search
        if cucumber.is_file():
            return cucumber
    junit = junit_files(p)
    if junit:
        return p
    raise FileNotFoundError(f"no *full-report*.html, DSL-*-run-*-report_T-*.html, cucumber.json or TEST-*.xml under {p}")


def cucumber_status(path: str | Path) -> dict[str, str]:
    try:
        features = json.loads(Path(path).read_text(encoding="utf-8"))
    except ValueError as e:
        raise ValueError(f"{Path(path).name}: {e}") from e
    out: dict[str, str] = {}
    for feat in features if isinstance(features, list) else []:
        background: list = []
        for el in feat.get("elements") or []:
            if el.get("type") == "background":       # its steps belong to the scenario that follows
                background = list(el.get("steps") or [])
                continue
            steps = background + list(el.get("before") or []) + list(el.get("steps") or []) + list(el.get("after") or [])
            background = []
            states = {((s.get("result") or {}).get("status") or "").lower() for s in steps}
            if states & {"failed", "ambiguous"}:
                status = "FAIL"
            elif states & {"skipped", "pending", "undefined"} or not steps:
                status = "SKIP"
            else:
                status = "PASS"
            key = f"{feat.get('name', '')}#{el.get('id') or el.get('name', '')}"
            if out.get(key) != "FAIL":
                out[key] = status
    return out


def junit_status(path: str | Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for f in junit_files(path):
        try:
            root = ET.parse(f).getroot()
        except ET.ParseError as e:
            raise ValueError(f"{f.name}: {e}") from e
        for tc in root.iter("testcase"):
            key = f"{tc.get('classname', '')}#{tc.get('name', '')}"
            kids = {c.tag for c in tc}
            status = "FAIL" if kids & {"failure", "error"} else "SKIP" if "skipped" in kids else "PASS"
            if out.get(key) != "FAIL":           # a failed rerun stays failed
                out[key] = status
    return out


def case_status(path: str | Path) -> dict[str, str]:
    """test id -> PASS | FAIL | SKIP (TestRig: first occurrence wins)."""
    report = find_full_report(path)
    if report.suffix == ".json":
        return cucumber_status(report)
    if report.is_dir() or report.suffix == ".xml":
        return junit_status(report)
    if dsl_key(report):
        return dsl_status(report)
    status, out = None, {}
    for line in _lines(report.read_text(encoding="utf-8", errors="replace")):
        m = HEADING.match(line)
        if m:
            status = {"Passed": "PASS", "Failed": "FAIL", "Skipped": "SKIP"}[m.group(2)]
        elif status and UID.match(line) and line not in out:
            out[line] = status
    return out
