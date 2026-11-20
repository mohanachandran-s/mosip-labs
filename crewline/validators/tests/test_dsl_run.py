"""DSL TestRig run support: `crewline service` for the packet creator, and DSL scenario reports in the regression."""
from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

import pytest

from crewline import regression, report, service


@pytest.fixture(autouse=True)
def _registry(tmp_path, monkeypatch):
    monkeypatch.setenv("CREWLINE_SERVICES_DIR", str(tmp_path / "registry"))


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _server(port: int) -> list[str]:
    return [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"]


def _rules(res) -> list[str]:
    return [f.rule_id for f in res.findings]


def test_service_start_health_stop(tmp_path):
    port = _port()
    url = f"http://127.0.0.1:{port}/"
    src = tmp_path / "centralized"
    (src / "config").mkdir(parents=True)
    (src / "config" / "application.properties").write_text("mosip.test.baseurl=\n", encoding="utf-8")
    run_dir = tmp_path / "svc" / "packetcreator"
    out = tmp_path / "evidence" / "service.json"
    res = service.start("pc", str(run_dir), _server(port), url, copy_from=str(src), ready_timeout=1, out=str(out), tick=0.2)
    try:
        assert res.status == "PASS", res.to_text()
        assert "SVC-006" in _rules(res) and out.is_file()
        assert (run_dir / "config" / "application.properties").is_file()        # copied before start
        pid = res.inputs["pid"]
        assert service.alive(pid)
        assert service.health("pc").status == "PASS"
        again = service.start("pc", str(run_dir), _server(port), url, tick=0.2)
        assert "SVC-002" in _rules(again) and again.status == "FAIL"           # one instance per name
    finally:
        stopped = service.stop("pc")
    assert stopped.status == "PASS" and not service.alive(pid)
    assert "SVC-008" in _rules(service.stop("pc"))                              # nothing left to stop
    assert service.health("pc").status == "FAIL"


def test_service_exit_before_ready_shows_masked_log(tmp_path):
    cmd = [sys.executable, "-c", "print('mosip.test.regclient.password=hunter2'); print('Bean creation failed'); raise SystemExit(3)"]
    res = service.start("pc", str(tmp_path / "svc"), cmd, f"http://127.0.0.1:{_port()}/", ready_timeout=1, tick=0.2)
    msg = res.findings[0].message
    assert res.status == "FAIL" and _rules(res) == ["SVC-004"]
    assert "Bean creation failed" in msg and "hunter2" not in msg
    assert not list((tmp_path / "registry").glob("*.json"))


def test_service_not_ready_in_time_is_stopped(tmp_path):
    cmd = [sys.executable, "-c", "import time; time.sleep(60)"]
    res = service.start("pc", str(tmp_path / "svc"), cmd, f"http://127.0.0.1:{_port()}/", ready_timeout=0.03, tick=0.2)
    assert _rules(res) == ["SVC-005"]
    assert not service.alive(res.inputs["pid"])


def test_service_refuses_a_busy_port(tmp_path):
    port = _port()
    busy = subprocess.Popen(_server(port), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        url = f"http://127.0.0.1:{port}/"
        for _ in range(50):
            if service.answers(url):
                break
            __import__("time").sleep(0.1)
        res = service.start("pc", str(tmp_path / "svc"), _server(port), url, ready_timeout=0.1, tick=0.2)
        assert _rules(res) == ["SVC-003"]
    finally:
        busy.kill()
        busy.wait()


def test_service_refuses_a_committable_folder(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text(".crewline-work/\n", encoding="utf-8")
    res = service.start("pc", str(repo / "centralized"), [sys.executable, "-V"], "http://127.0.0.1:1/", tick=0.2)
    assert _rules(res) == ["SVC-001"]
    assert not service._unprotected(repo / ".crewline-work" / "tasks" / "T1" / "services" / "pc")
    assert not service._unprotected(tmp_path / "outside")


NEW = ('<li class="tree-item depth-1 tree-scenario-node scenario-card status-{s}" data-status="{s}" '
       'data-name="{n}" role="none"><a>{n}</a></li>')


def _dsl(folder: Path, epoch: int, statuses: dict[str, str], env: str = "api-internal.dev2-regresion") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = sum(1 for s in statuses.values() if s == "pass")
    f = folder / f"DSL-{env}-run-{epoch}-report_T-{len(statuses)}_P-{p}_KI-0_I-0_S-0_F-0.html"
    f.write_text("<style>.tree-scenario-node .x{}</style><ul>" + "".join(NEW.format(s=s, n=n) for n, s in statuses.items())
                 + "</ul>", encoding="utf-8")
    (folder / f"DSL-{env}-error-{epoch}-report_T-1_P-0_S-0_F-1.html").write_text("x", encoding="utf-8")
    return f


def test_dsl_report_both_layouts(tmp_path):
    rep = tmp_path / "testng-report"
    _dsl(rep, 100, {"Scenario_0": "fail"})
    newest = _dsl(rep, 200, {"Scenario_0": "pass", "Scenario_2": "known", "Scenario_3": "skip", "Scenario_9": "ignored",
                             "Scenario_AFTER_SUITE": "pass"})
    assert report.find_full_report(tmp_path) == newest                       # the newest run, not the -error- one
    assert report.case_status(rep) == {"Scenario_0": "PASS", "Scenario_2": "FAIL", "Scenario_3": "SKIP",
                                       "Scenario_AFTER_SUITE": "PASS"}
    old = tmp_path / "old" / "DSL-api-internal.released-full-run-1759764729458-report_T-3_P-1_KI-1_I-1_S-0_F-0.html"
    old.parent.mkdir()
    old.write_text('<tr class="passedeven"><td><a href="#m1">Scenario_1</a></td></tr>'
                   '<tr class="knownissueeven"><td><a href="#m2">Scenario_2</a></td></tr>'
                   '<tr class="ignoredeven"><td><a href="#m3">Scenario_3</a></td></tr>', encoding="utf-8")
    assert report.case_status(old) == {"Scenario_1": "PASS", "Scenario_2": "FAIL"}
    assert report.dsl_key(old) == "DSL-api-internal.released-full"


def test_dsl_regression_and_environment_pairing(tmp_path):
    _dsl(tmp_path / "base", 100, {"Scenario_0": "pass", "Scenario_2": "pass", "Scenario_5": "known"})
    _dsl(tmp_path / "branch", 200, {"Scenario_0": "pass", "Scenario_2": "known", "Scenario_5": "known"})
    res = regression.run(str(tmp_path / "base"), str(tmp_path / "branch"))
    assert res.status == "FAIL"
    assert [(f.rule_id, f.evidence) for f in res.findings if f.severity == "BLOCK"] == [("REG-001", "Scenario_2")]
    assert any(f.rule_id == "REG-005" and f.evidence == "Scenario_5" for f in res.findings)

    _dsl(tmp_path / "other", 300, {"Scenario_0": "pass", "Scenario_2": "pass", "Scenario_5": "known"},
         env="api-internal.qa-regresion")
    res = regression.run(str(tmp_path / "base"), str(tmp_path / "other"))
    assert [f.rule_id for f in res.findings] == ["REG-009"] and res.status == "FAIL"
