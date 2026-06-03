"""Direct router tests for /api/scenarios.

Focus is on the new /infra-hints endpoint (catalog-resolved adapter_refs
+ suggested_modules) — the existing list / detail endpoints are
exercised end-to-end by the smoke harness. Here we test the hint
resolver's branches in isolation.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PACKS_DIR = REPO_ROOT / "tools" / "packs"


@pytest.fixture(scope="module", autouse=True)
def _load_catalog():
    """Populate the adapter-catalog singleton for the whole module."""
    from tools.adapter_catalog import catalog  # noqa: PLC0415
    catalog.load(str(PACKS_DIR))
    assert catalog.count() > 0


@pytest.fixture
def client(make_client):
    from api.scenarios import router  # noqa: PLC0415
    return make_client(router)


def _seed_scenario(session_factory, *, scenario_id: str, external_tools: list[dict] | None = None):
    """Insert one Scenario with the given external_tools[] payload."""
    from models import Scenario  # noqa: PLC0415

    async def _do():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id=scenario_id,
                name="Test Scenario",
                plane="EDR",
                version="1.0",
                status="active",
                uc_ref="UCS-EDR-01",
                uc_name="X",
                tc_ref="TC-EDR-01",
                tc_name="Y",
                mitre_tactic="TA0006",
                mitre_tactic_name="Credential Access",
                mitre_technique="T1003.001",
                mitre_technique_name="LSASS Memory",
                steps=[{"id": "step-01", "name": "step1"}],
                external_tools=external_tools or [],
            ))
            await db.commit()

    asyncio.get_event_loop().run_until_complete(_do())


def test_infra_hints_resolves_adapter_refs(client, session_factory):
    """Two real tier-3 adapters → both iac_modules in suggested_modules."""
    _seed_scenario(session_factory, scenario_id="SIM-TEST-001", external_tools=[
        {"name": "mimikatz", "type": "cred-dump", "adapter_ref": "TOOL-MIMIKATZ"},
        {"name": "rubeus",   "type": "kerberos",  "adapter_ref": "TOOL-RUBEUS"},
    ])
    resp = client.get("/api/scenarios/SIM-TEST-001/infra-hints")
    assert resp.status_code == 200
    body = resp.json()
    assert body["scenario_id"] == "SIM-TEST-001"
    assert body["adapter_refs"] == ["TOOL-MIMIKATZ", "TOOL-RUBEUS"]
    assert body["unresolved_refs"] == []
    # Mimikatz → edr, Rubeus → itdr (verified directly from packs/*.yml)
    assert set(body["suggested_modules"]) == {"edr", "itdr"}
    # Each resolved row carries the metadata the UI picker needs
    names = {r["name"] for r in body["resolved_adapters"]}
    assert names == {"Mimikatz", "Rubeus"}
    for row in body["resolved_adapters"]:
        assert row["safety_class"] == "dual-use-lab-only"
        assert row["tier"] == 3


def test_infra_hints_handles_unresolved_refs(client, session_factory):
    """Stale adapter_ref → row in unresolved_refs, not suggested_modules."""
    _seed_scenario(session_factory, scenario_id="SIM-TEST-002", external_tools=[
        {"name": "mimikatz", "adapter_ref": "TOOL-MIMIKATZ"},
        {"name": "ghost",    "adapter_ref": "TOOL-DOES-NOT-EXIST"},
    ])
    body = client.get("/api/scenarios/SIM-TEST-002/infra-hints").json()
    assert body["adapter_refs"] == ["TOOL-MIMIKATZ", "TOOL-DOES-NOT-EXIST"]
    assert body["unresolved_refs"] == ["TOOL-DOES-NOT-EXIST"]
    assert body["suggested_modules"] == ["edr"]
    # The unresolved ref is NOT in resolved_adapters
    assert all(r["adapter_ref"] != "TOOL-DOES-NOT-EXIST" for r in body["resolved_adapters"])


def test_infra_hints_skips_tier4_adapters(client, session_factory):
    """Tier-4 adapters (no iac_module) resolve but contribute zero modules."""
    _seed_scenario(session_factory, scenario_id="SIM-TEST-003", external_tools=[
        {"name": "nmap", "adapter_ref": "TOOL-NMAP"},
    ])
    body = client.get("/api/scenarios/SIM-TEST-003/infra-hints").json()
    assert body["adapter_refs"] == ["TOOL-NMAP"]
    assert body["unresolved_refs"] == []
    assert body["suggested_modules"] == []
    # The resolved row carries iac_module=None
    assert body["resolved_adapters"][0]["iac_module"] is None


def test_infra_hints_skips_entries_without_adapter_ref(client, session_factory):
    """Legacy external_tools[] entries without adapter_ref are ignored."""
    _seed_scenario(session_factory, scenario_id="SIM-TEST-004", external_tools=[
        {"name": "legacy-script", "type": "script"},  # no adapter_ref
        {"name": "rubeus", "adapter_ref": "TOOL-RUBEUS"},
    ])
    body = client.get("/api/scenarios/SIM-TEST-004/infra-hints").json()
    assert body["adapter_refs"] == ["TOOL-RUBEUS"]
    assert body["suggested_modules"] == ["itdr"]


def test_infra_hints_empty_external_tools(client, session_factory):
    """Scenario with no external_tools[] returns empty hints, not 500."""
    _seed_scenario(session_factory, scenario_id="SIM-TEST-005", external_tools=[])
    body = client.get("/api/scenarios/SIM-TEST-005/infra-hints").json()
    assert body == {
        "scenario_id":       "SIM-TEST-005",
        "plane":             "EDR",
        "adapter_refs":      [],
        "resolved_adapters": [],
        "unresolved_refs":   [],
        "suggested_modules": [],
    }


def test_infra_hints_unknown_scenario_404(client):
    resp = client.get("/api/scenarios/SIM-DOES-NOT-EXIST/infra-hints")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "SCENARIO_NOT_FOUND"


def test_infra_hints_dedupes_repeated_modules(client, session_factory):
    """Two adapters with the same iac_module → suggested_modules contains it once."""
    _seed_scenario(session_factory, scenario_id="SIM-TEST-006", external_tools=[
        {"name": "rubeus",     "adapter_ref": "TOOL-RUBEUS"},      # itdr
        {"name": "bloodhound", "adapter_ref": "TOOL-BLOODHOUND"},  # itdr
    ])
    body = client.get("/api/scenarios/SIM-TEST-006/infra-hints").json()
    assert body["suggested_modules"] == ["itdr"]


# ---------------------------------------------------------------------------
# ttp_ref filter on the list endpoint (issue #56)
# ---------------------------------------------------------------------------


def _seed_with_ttp_ref(session_factory, *, scenario_id: str, ttp_ref: str | None,
                       plane: str = "EDR"):
    """Insert one Scenario whose first step's expected_detections cites
    ``ttp_ref`` (or has no ttp_ref if None)."""
    from models import Scenario  # noqa: PLC0415

    det = {"plane": plane, "type": "BIOC", "description": "x"}
    if ttp_ref:
        det["ttp_ref"] = ttp_ref

    async def _do():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id=scenario_id, name=scenario_id, plane=plane,
                version="1.0", status="active",
                uc_ref="UCS-X", uc_name="x", tc_ref="TC-X", tc_name="x",
                mitre_tactic="TA0006", mitre_tactic_name="x",
                mitre_technique="T1003", mitre_technique_name="x",
                steps=[{"id": "step-01", "name": "s", "expected_detections": [det]}],
                external_tools=[],
            ))
            await db.commit()

    asyncio.get_event_loop().run_until_complete(_do())


def test_list_scenarios_ttp_ref_filter_returns_only_citers(client, session_factory):
    _seed_with_ttp_ref(session_factory, scenario_id="SIM-A", ttp_ref="TTP-2026-0004")
    _seed_with_ttp_ref(session_factory, scenario_id="SIM-B", ttp_ref="TTP-2026-0002")
    _seed_with_ttp_ref(session_factory, scenario_id="SIM-C", ttp_ref=None)

    resp = client.get("/api/scenarios?ttp_ref=TTP-2026-0004")
    assert resp.status_code == 200
    body = resp.json()
    ids = [s["scenario_id"] for s in body["scenarios"]]
    assert ids == ["SIM-A"]
    assert body["total"] == 1


def test_list_scenarios_ttp_ref_empty_when_no_citers(client, session_factory):
    _seed_with_ttp_ref(session_factory, scenario_id="SIM-A", ttp_ref="TTP-2026-0002")
    resp = client.get("/api/scenarios?ttp_ref=TTP-2026-NOPE")
    assert resp.status_code == 200
    assert resp.json() == {"scenarios": [], "total": 0}


def test_list_scenarios_ttp_ref_composes_with_plane(client, session_factory):
    """ttp_ref AND plane — only scenarios matching both filters return."""
    _seed_with_ttp_ref(session_factory, scenario_id="SIM-EDR", ttp_ref="TTP-2026-0004", plane="EDR")
    _seed_with_ttp_ref(session_factory, scenario_id="SIM-ITDR", ttp_ref="TTP-2026-0004", plane="ITDR")

    body = client.get("/api/scenarios?ttp_ref=TTP-2026-0004&plane=ITDR").json()
    ids = [s["scenario_id"] for s in body["scenarios"]]
    assert ids == ["SIM-ITDR"]


def test_list_scenarios_no_ttp_ref_filter_returns_all(client, session_factory):
    """Sanity: the filter is opt-in. Without it, scenarios without ttp_ref still appear."""
    _seed_with_ttp_ref(session_factory, scenario_id="SIM-X", ttp_ref=None)
    body = client.get("/api/scenarios").json()
    ids = [s["scenario_id"] for s in body["scenarios"]]
    assert "SIM-X" in ids
