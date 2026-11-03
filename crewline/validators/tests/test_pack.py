"""Product pack: MOSIP overlay vs generic core. Tests use CREWLINE_PACK; they never write packs/ACTIVE."""
from pathlib import Path

import pytest
import yaml

from crewline import pack, tools
from crewline.__main__ import main
from crewline.result import EXIT

CORE = Path(__file__).resolve().parents[2]
MOSIP_CAPS = {
    "java21", "maven", "test_credentials", "browser_upload",
    "coverage_cli", "module_build", "branch_test_env",
}   # jira_connector is core (tracker ticket at INPUT_VALIDATION); the MOSIP pack overrides it with its site


def test_default_pack_is_mosip():
    assert pack.DEFAULT_PACK == "mosip"
    assert (CORE / "packs" / "ACTIVE").read_text(encoding="utf-8").splitlines()[0].strip() == "mosip"
    assert pack.load("mosip")["sdet"]["testrig"] is True
    assert pack.load("generic")["sdet"]["testrig"] is False
    assert {p.name for p in (CORE / "knowledge" / "mosip").glob("*.md")}  # still referenced, not moved
    assert all((CORE / rel).is_dir() for rel in pack.load("mosip")["knowledge"])


def test_core_tools_have_no_mosip_stack():
    spec = yaml.safe_load((CORE / "core" / "tools.yaml").read_text(encoding="utf-8"))
    caps = spec["capabilities"]
    assert "git" in caps and "python" in caps and "permission_gated" in spec
    assert MOSIP_CAPS.isdisjoint(caps)
    assert "mosip" not in str(caps["jira_connector"]).lower()


def test_mosip_merge_adds_stack_and_keeps_enable_steps(monkeypatch):
    monkeypatch.delenv("CREWLINE_PACK", raising=False)
    merged = pack.merged_tools()["capabilities"]
    assert MOSIP_CAPS <= set(merged)
    assert all(c.get("enable") and c.get("check") for c in merged.values())
    res = tools.run(["git", "browser_upload"])
    assert res.inputs["pack"] == "mosip"
    assert any(f.rule_id == "TOOL-000" and "Claude in Chrome" in (f.remediation or "") for f in res.findings)


def test_generic_pack_hides_mosip_tools(monkeypatch):
    monkeypatch.setenv("CREWLINE_PACK", "generic")
    merged = pack.merged_tools()["capabilities"]
    assert "git" in merged and MOSIP_CAPS.isdisjoint(merged)
    assert pack.uses_testrig() is False and pack.uses_coverage_cli() is False
    res = tools.run(["browser_upload"])
    assert res.status == "PASS" and not res.findings  # unknown name is skipped, not MOSIP-checked


def test_unknown_pack_is_not_run(monkeypatch):
    monkeypatch.setenv("CREWLINE_PACK", "no-such-pack")
    with pytest.raises(FileNotFoundError):
        pack.load()
    assert main(["tools"]) == EXIT["NOT_RUN"]


def test_testrig_clis_not_run_on_generic(monkeypatch):
    monkeypatch.setenv("CREWLINE_PACK", "generic")
    for cmd in (
        ["testcase", "--module-root", "."],
        ["expected-diff", "--module-root", ".", "--base", "HEAD"],
        ["scope", "--module-root", ".", "--ref", "HEAD"],
        ["coverage", "--commons-dir", ".", "--module-root", ".", "--module-code", "x"],
    ):
        assert main(cmd) == EXIT["NOT_RUN"], cmd


def test_pack_cli_list_does_not_mutate_active(monkeypatch):
    monkeypatch.delenv("CREWLINE_PACK", raising=False)
    before = (CORE / "packs" / "ACTIVE").read_text(encoding="utf-8")
    assert main(["pack", "--list"]) == 0
    assert main(["pack"]) == 0
    assert (CORE / "packs" / "ACTIVE").read_text(encoding="utf-8") == before


def test_set_active_writes_tmp_not_repo(tmp_path, monkeypatch):
    monkeypatch.delenv("CREWLINE_PACK", raising=False)
    monkeypatch.setattr(pack, "ACTIVE_FILE", tmp_path / "ACTIVE")
    pack.set_active("generic")
    assert (tmp_path / "ACTIVE").read_text(encoding="utf-8").strip() == "generic"
    assert pack.active_id() == "generic"
    with pytest.raises(FileNotFoundError):
        pack.set_active("nope")
