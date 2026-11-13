"""SIM-MP-038 shape: Pink (CL-CRI-1147) on today's lab doors.

The first corpus scenario with ``channel: eal`` steps. One agent step resolves
the lure on the beacon host; three eal steps run inside SimCore and POST
collector records. Spec: 2026-11-11-pink-cl-cri-1147-chain-design.md §5-§7.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# Bound at collection, like the other eal tests: tests/scripts/test_lab_readiness.py
# puts scripts/ first on sys.path, where scripts/eal_simulator (the CLI package)
# would shadow core's eal_simulator for an import made later inside a test.
from eal_simulator.registry import get_default_registry

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIO = (REPO_ROOT / "scenarios" / "multi_plane"
            / "mp-038-pink-cl-cri-1147-vishing-passkey-m365-extortion.yml")
CARD = (REPO_ROOT / "detection_scanner" / "ttps"
        / "TTP-2026-0551-pink-cl-cri-1147-vishing-passkey-m365-extortion.json")
INDEX_DIR = REPO_ROOT / "docs" / "uc_tc_mapping" / "_v2.2-source"
TTP = "TTP-2026-0551"

CANARY_USER = "cortexsim-mp-038-pink@cortexsim-canary.invalid"  # = the canary-planted principal
CANARY_TOKEN = "cortexsim-mp-038-pink"
PUBLISHED_UAS = ["Microsoft.Graph.Client/5.62.0", "python-requests/2.28.1", "python-requests/2.33.1"]
LURES = ["passkeyadd.com", "passkeydeploy.com", "deploypasskey.com"]
CONTROL = "test-phishing.testpanw.com"

# Declared rows (spec §7): D1, D3, D5-D10, CR-1, CR-2. D2, D4 and CR-3 are hunts.
DECLARED = {
    "step-01": ["xql-mp-038-dns-security-logged-the-panw-phishing-test-domain",
                "xql-mp-038-endpoint-resolved-a-pink-lure-domain"],
    "step-02": ["xql-mp-038-entra-sign-in-for-the-canary-from-the-pink-access-ip",
                "xql-mp-038-passkey-registered-from-the-sign-in-ip-within-ten-minutes"],
    "step-03": ["xql-mp-038-sharepoint-and-onedrive-download-burst-from-the-pink-ip",
                "xql-mp-038-file-access-through-graph-sdk-and-python-requests-user-agents",
                "CR-MP-0038"],
    "step-04": ["xql-mp-038-internal-fan-out-send-from-the-extortion-ip",
                "xql-mp-038-teams-messages-by-the-canary-from-the-extortion-ip",
                "CR-MP-0038-EXTORT"],
}


@pytest.fixture(scope="module")
def scenario() -> dict[str, Any]:
    return yaml.safe_load(SCENARIO.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def card_queries() -> dict[str, str]:
    """detection_id -> the card's XQL: each xql_queries[].query by its resolver
    slug, and each correlation rule's XQL (after the ``XQL:`` marker in logic)."""
    from engine.ttp_catalog import _slug  # noqa: PLC0415

    card = json.loads(CARD.read_text(encoding="utf-8"))
    out = {_slug(q["name"], "xql"): q["query"] for q in card["detections"]["xql_queries"]}
    for r in card["detections"]["correlation_rules"]:
        out[r["rule_id"]] = r["logic"].split("XQL:", 1)[1].strip("\n")
    return out


@pytest.fixture(scope="module")
def coverage_report():
    path = REPO_ROOT / "detection_scanner" / "scripts" / "coverage_report.py"
    spec = importlib.util.spec_from_file_location("coverage_report", path)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules["coverage_report"] = m
    spec.loader.exec_module(m)
    return m


def _steps(scenario: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["id"]: s for s in scenario["steps"]}


def _declared_rows(scenario: dict[str, Any]) -> list[dict[str, Any]]:
    return [d for s in scenario["steps"] for d in s.get("expected_detections") or []]


def test_loads_under_strict_refs(tmp_path, monkeypatch):
    """The real loader, with the v2.2 index loaded and strict refs on, loads it."""
    from config import settings  # noqa: PLC0415
    from engine import scenario_loader  # noqa: PLC0415
    from engine.uctc_registry import registry  # noqa: PLC0415

    registry.load(str(INDEX_DIR))
    assert len(registry.all_test_cases()) == 266, "the index did not load; strict refs would be vacuous"
    monkeypatch.setattr(settings, "CORTEXSIM_STRICT_REFS", True)
    shutil.copy(SCENARIO, tmp_path / SCENARIO.name)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                 connect_args={"check_same_thread": False}, poolclass=StaticPool)
    session = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _run() -> list[str]:
        from database import Base  # noqa: PLC0415
        import models  # noqa: F401, PLC0415
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        try:
            async with session() as db:
                return await scenario_loader.load_scenarios(str(tmp_path), db)
        finally:
            await engine.dispose()

    assert asyncio.run(_run()) == ["SIM-MP-038"]


def test_four_steps_one_agent_three_eal(scenario):
    steps = scenario["steps"]
    assert [s["id"] for s in steps] == ["step-01", "step-02", "step-03", "step-04"]
    by_id = _steps(scenario)

    s1 = by_id["step-01"]
    assert s1.get("channel", "agent") == "agent" and "eal" not in s1
    assert s1["identity"] == "direct"
    lines = [ln.strip() for ln in s1["command"].splitlines() if ln.strip()]
    assert lines[0].startswith(': "${LAB_RESOLVER:?')
    # Each lookup is a bare line, 40 s after the one before: back-to-back steps
    # lost telemetry in the 2026-11-11 persistence-grouping lab run.
    want_lines: list[str] = []
    for d in [*LURES, CONTROL]:
        want_lines += ["sleep 40"] if want_lines else []
        want_lines.append(f"dig @${{LAB_RESOLVER}} {d} A +short")
    assert lines[1:] == want_lines

    want = {
        "step-02": ("idp_signin_emulator", {
            "provider": "microsoft", "event_pattern": "vishing_passkey_enrollment",
            "source_ip": "172.93.100.252", "event_time_offset_seconds": -1500}),
        "step-03": ("m365_activity_emitter", {
            "event_pattern": "storage_exfiltration", "source_ip": "172.93.100.252",
            "user_agents": PUBLISHED_UAS, "storage_targets": ["sharepoint", "onedrive"],
            "event_time_offset_seconds": -1200}),
        "step-04": ("m365_activity_emitter", {
            "event_pattern": "internal_extortion_notice", "source_ip": "96.232.20.66",
            "event_time_offset_seconds": -300}),
    }
    for sid, (plugin, params) in want.items():
        st = by_id[sid]
        assert st["channel"] == "eal" and st["eal"]["plugin"] == plugin, sid
        assert st["eal"]["params"] == {
            **params, "target_user": CANARY_USER, "canary_token": CANARY_TOKEN}, sid
        assert st["command"].strip(), f"{sid}: StepSchema requires a command"


def test_eal_params_validate_against_their_plugins(scenario):
    """The loader does not validate eal.params, so a bad pattern is caught here.
    Then each step is rendered: a dry run never builds records, so validation
    alone proves nothing about rendering."""
    reg = get_default_registry()
    for st in scenario["steps"]:
        if st.get("channel") != "eal":
            continue
        cls = reg.get(st["eal"]["plugin"])
        params = cls.Meta.params_model(**st["eal"]["params"],
                                       collector_url="https://collector.lab.invalid/x")
        records = cls().records_for(params, sim_run_id="run-sim-mp-038-shape", iteration=0)
        assert len(records) >= 1, st["id"]
        assert all(r.get("cortexsim_canary") == CANARY_TOKEN for r in records), st["id"]


def test_nine_indicators_eight_in_band(scenario):
    """Spec §6: ind-04 (the phishing host) is out of band, the other eight are
    in band. (Spec §3 goal 2 says "seven"; the §6 table and §12 decide eight.)"""
    inds = scenario["indicators"]
    assert [i["id"] for i in inds] == [f"ind-0{n}" for n in range(1, 10)]
    out_of_band = [i["id"] for i in inds if i.get("in_band", True) is False]
    assert out_of_band == ["ind-04"]
    assert sum(1 for i in inds if i.get("in_band", True) is not False) == 8


def test_every_declared_detection_has_verification_xql(scenario, card_queries):
    by_step = {s["id"]: [d["detection_id"] for d in s.get("expected_detections") or []]
               for s in scenario["steps"]}
    assert by_step == DECLARED
    rows = _declared_rows(scenario)
    assert len(rows) == 10
    for d in rows:
        assert d["ttp_ref"] == TTP
        q = d.get("verification_xql")
        assert isinstance(q, str) and q.strip(), d["detection_id"]
        assert q.rstrip("\n") == card_queries[d["detection_id"]], (
            f"{d['detection_id']}: verification_xql is not the card's query verbatim")
    assert [d["detection_id"] for d in rows if d["type"] == "Correlation"] == [
        "CR-MP-0038", "CR-MP-0038-EXTORT"]


def test_no_verification_query_reads_relative_to_now(scenario):
    for d in _declared_rows(scenario):
        assert "current_time(" not in d["verification_xql"], d["detection_id"]


def test_no_kpi_no_threshold_push_off(scenario):
    assert "primary_kpi" not in scenario
    assert "threshold" not in scenario
    assert scenario["push_supported"] is False
    assert scenario["pull_supported"] is True


def test_no_marker_only_step(scenario, coverage_report):
    assert [s["id"] for s in scenario["steps"] if coverage_report.is_marker_only_step(s)] == []


def test_published_user_agents_only_in_step_03_params(scenario):
    """ind-07..09 go into step-03's record bodies only: never a command, never
    another param (``user_agent`` would set SimCore's own HTTP header)."""
    def walk(node: Any, path: str):
        if isinstance(node, dict):
            for k, v in node.items():
                yield from walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                yield from walk(v, f"{path}[{i}]")
        elif isinstance(node, str):
            yield path, node

    hits = []
    for n, st in enumerate(scenario["steps"]):
        surface = {k: st.get(k) for k in ("command", "eal", "platform_variants") if k in st}
        for path, text in walk(surface, f"steps[{n}]"):
            if any(ua in text for ua in PUBLISHED_UAS):
                hits.append(path)
    for path, text in walk((scenario.get("cleanup") or {}), "cleanup"):
        if any(ua in text for ua in PUBLISHED_UAS):
            hits.append(path)
    assert hits == [f"steps[2].eal.params.user_agents[{i}]" for i in range(3)]
