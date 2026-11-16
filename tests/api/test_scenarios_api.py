"""Direct router tests for /api/scenarios.

The list / detail endpoints are exercised end-to-end by the smoke
harness. Here we test the list filters in isolation.
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

    asyncio.run(_do())


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
    body = resp.json()
    # Assert the payload, not the envelope's full key set — the list response
    # also carries `projection` (see api/scenarios._LIST_OMIT_FIELDS).
    assert body["scenarios"] == []
    assert body["total"] == 0


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
