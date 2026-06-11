"""GAP-API-008 — runs router prefix (POST /api/runs) + backward-compat alias
(POST /api/run). Both must hit the same launch implementation.
"""
from __future__ import annotations

import asyncio

import pytest


@pytest.fixture
def client(make_client):
    from api.runs import router, compat_router
    return make_client(router, compat_router)


@pytest.fixture
def seeded_scenario(session_factory):
    from models import Scenario

    async def _seed():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id="SIM-EDR-001", name="t", version="1.0", status="active",
                plane="EDR", uc_ref="UCS-EDR-01", uc_name="x", tc_ref="TC-EDR-01",
                tc_name="y", mitre_tactic="TA0006", mitre_tactic_name="Cred",
                mitre_technique="T1003", mitre_technique_name="Dump",
                push_supported=True, pull_supported=True,
                execution_identity={"default": "www-data"},
                steps=[{"id": "step-01", "name": "s", "identity": "www-data",
                        "command": "id", "expected_detections": []}],
            ))
            await db.commit()

    asyncio.run(_seed())
    return "SIM-EDR-001"


def test_launch_via_plural_runs_path(client, seeded_scenario):
    r = client.post("/api/runs", json={"scenario_id": seeded_scenario, "mode": "push"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "push"
    assert "run_id" in body


def test_launch_via_singular_run_alias(client, seeded_scenario):
    """The deprecated POST /api/run still works (backward compat)."""
    r = client.post("/api/run", json={"scenario_id": seeded_scenario, "mode": "push"})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "push"


def test_both_paths_reject_invalid_mode_identically(client, seeded_scenario):
    for path in ("/api/runs", "/api/run"):
        r = client.post(path, json={"scenario_id": seeded_scenario, "mode": "bogus"})
        assert r.status_code == 400, (path, r.text)
        assert r.json()["detail"]["code"] == "INVALID_MODE"


def test_list_runs_still_at_plural_path(client):
    r = client.get("/api/runs")
    assert r.status_code == 200
    assert r.json() == {"runs": [], "total": 0}
