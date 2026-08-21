"""The GET /api/scenarios list returns a SUMMARY projection.

The full corpus serialises to ~1.25 MB, 65% of which is ``steps[]`` the list
view never renders. These tests pin both halves of the contract: what the list
drops, and what it must keep because the console reads it off a list-derived
scenario (grepped from ``ui/src`` — FilterPalette / useScenarioFilter /
ScenarioGrid / StackCoverageView / AppConsole).
"""
from __future__ import annotations

import asyncio

import pytest


@pytest.fixture
def client(make_client):
    from api.scenarios import router  # noqa: PLC0415
    return make_client(router)


FAT_STEP = {
    "id": "step-01",
    "name": "Dump LSASS",
    "mitre_technique": "T1003.001",
    "identity": "www-data",
    "platforms": ["windows"],
    "command": "x" * 4096,
    "platform_variants": {"linux": "y" * 2048},
    "causality": {"parent_step": None},
    "expected_detections": [{
        "type": "BIOC",
        "plane": "EDR",
        "ttp_ref": "TTP-2026-0004",
        "detection_id": "bioc-lsass-dump",
        "description": "z" * 2048,
        "verification_xql": "dataset = xdr_data | limit 1",
    }],
}


def _seed(session_factory, *, scenario_id="SIM-EDR-001"):
    from models import Scenario

    async def _do():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id=scenario_id, name="Credential Dumping", plane="EDR",
                version="1.0", status="active",
                uc_ref="UCS-EDR-01", uc_name="X", tc_ref="TC-EDR-01", tc_name="Y",
                mitre_tactic="TA0006", mitre_tactic_name="Credential Access",
                mitre_technique="T1003.001", mitre_technique_name="LSASS Memory",
                steps=[FAT_STEP],
                cleanup={"commands": ["rm -f /tmp/dump"]},
                external_tools=[{"name": "mimikatz", "adapter_ref": "TOOL-MIMIKATZ"}],
                success_criteria="q" * 4096,
            ))
            await db.commit()

    asyncio.run(_do())
    return scenario_id


def test_list_drops_the_heavy_unrendered_fields(client, session_factory):
    _seed(session_factory)
    body = client.get("/api/scenarios").json()
    row = body["scenarios"][0]

    assert body["projection"] == "summary"
    for dropped in ("cleanup", "external_tools", "success_criteria"):
        assert dropped not in row, f"{dropped} is not rendered by any list view"

    step = row["steps"][0]
    for dropped in ("command", "platform_variants", "causality", "identity"):
        assert dropped not in step
    det = step["expected_detections"][0]
    for dropped in ("description", "verification_xql", "detection_id", "ttp_ref"):
        assert dropped not in det


def test_list_keeps_every_field_the_console_reads(client, session_factory):
    """Regression guard for the fields grepped out of ui/src. Removing any one
    of these silently empties a filter facet or a card badge."""
    _seed(session_factory)
    row = client.get("/api/scenarios").json()["scenarios"][0]

    # Card / facet scalars
    for kept in ("scenario_id", "name", "plane", "mitre_tactic", "mitre_technique",
                 "detection_types", "tags", "execution_identity",
                 "additional_techniques", "threat_report", "uc_ref", "tc_ref"):
        assert kept in row, f"{kept} is read off the LIST response"

    # AppConsole renders `steps.length`
    assert len(row["steps"]) == 1
    step = row["steps"][0]
    # FilterPalette / useScenarioFilter / ScenarioGrid technique facets
    assert step["mitre_technique"] == "T1003.001"
    # detection-type facet + cross-plane badge
    det = step["expected_detections"][0]
    assert det["type"] == "BIOC"
    assert det["plane"] == "EDR"


def test_list_projection_is_materially_smaller(client, session_factory):
    """The whole point. A step whose command/description dominate must not
    reach the list."""
    _seed(session_factory)
    listed = client.get("/api/scenarios").content
    detailed = client.get("/api/scenarios/SIM-EDR-001").content

    assert len(listed) < len(detailed) / 4


def test_detail_endpoint_still_returns_the_full_document(client, session_factory):
    """Nothing was removed from the corpus — only from the list projection."""
    _seed(session_factory)
    doc = client.get("/api/scenarios/SIM-EDR-001").json()

    assert doc["cleanup"] == {"commands": ["rm -f /tmp/dump"]}
    assert doc["external_tools"][0]["adapter_ref"] == "TOOL-MIMIKATZ"
    assert doc["success_criteria"].startswith("q")
    assert doc["steps"][0]["command"] == "x" * 4096
    assert doc["steps"][0]["expected_detections"][0]["ttp_ref"] == "TTP-2026-0004"


def test_ttp_ref_filter_still_matches_on_the_full_steps(client, session_factory):
    """The ttp_ref filter reads steps[].expected_detections[].ttp_ref — it runs
    against the ORM rows, so trimming the RESPONSE must not break it."""
    _seed(session_factory)
    assert len(client.get("/api/scenarios?ttp_ref=TTP-2026-0004").json()["scenarios"]) == 1
    assert client.get("/api/scenarios?ttp_ref=TTP-2026-9999").json()["scenarios"] == []


def test_list_tolerates_a_scenario_with_no_steps(client, session_factory):
    from models import Scenario

    async def _do():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id="SIM-EMPTY-001", name="Empty", plane="EDR",
                version="1.0", status="active",
                uc_ref="UCS-EDR-01", uc_name="X", tc_ref="TC-EDR-01", tc_name="Y",
                mitre_tactic="TA0006", mitre_tactic_name="CA",
                mitre_technique="T1003.001", mitre_technique_name="LSASS",
                steps=None,
            ))
            await db.commit()

    asyncio.run(_do())
    row = client.get("/api/scenarios").json()["scenarios"][0]
    assert row["steps"] == []
