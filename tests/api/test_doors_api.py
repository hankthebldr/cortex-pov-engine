"""Task 5 — the ingestion doors read API.

Each test names the failure it guards: a route that dials out, a bulk map that skips a
scenario, a missing scenario that 200s, a phase-1 tree that claims a door is open, and any
response that implies a tenant was reached.
"""
from __future__ import annotations

import asyncio
import os
import socket
import time

import pytest

from config import settings

SCENARIOS = [
    ("multi_plane", "mp-001-c2-beacon-ngfw-xdr-stitch.yml", "SIM-MP-001"),
    ("edr", None, None),
]


@pytest.fixture
def client(make_client, session_factory):
    from api.doors import router as doors_router
    from api.scenarios import router as scenarios_router
    from engine.scenario_loader import _parse_and_validate, _schema_to_orm_kwargs
    from engine.ttp_catalog import catalog, default_corpus_dir
    from models import Scenario

    catalog.load(default_corpus_dir(settings.CORTEXSIM_BASE_DIR))
    base = os.path.join(settings.CORTEXSIM_BASE_DIR, "scenarios")
    paths = [os.path.join(base, "multi_plane", "mp-001-c2-beacon-ngfw-xdr-stitch.yml")]
    edr = sorted(f for f in os.listdir(os.path.join(base, "edr")) if f.endswith(".yml") and not f.startswith("_"))
    paths.append(os.path.join(base, "edr", edr[0]))

    async def _seed():
        async with session_factory() as s:
            for p in paths:
                schema, err = _parse_and_validate(p)
                assert err is None, err
                s.add(Scenario(**_schema_to_orm_kwargs(schema)))
            await s.commit()

    asyncio.run(_seed())
    c = make_client(doors_router, scenarios_router)  # main.py's mount order
    c.seeded = len(paths)
    return c


def test_list_doors_zero_outbound(client, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a read route opened a socket")
    monkeypatch.setattr(socket.socket, "connect", boom)
    r = client.get("/api/doors")
    assert r.status_code == 200
    body = r.json()
    assert body["outbound_calls"] == 0
    from engine.ingestion_doors import DOORS
    assert {d["door_id"] for d in body["doors"]} == {d.id for d in DOORS}
    row = body["doors"][0]
    assert set(row) == {"key", "door_id", "label", "scope", "state", "basis", "rung",
                        "last_conclusive_at", "age_seconds", "wizard", "detections_needing"}
    assert any(d["detections_needing"] for d in body["doors"])


def test_readiness_bulk_keys_every_loaded_scenario(client):
    r = client.get("/api/doors/readiness")
    body = r.json()
    assert len(body["scenarios"]) == client.seeded
    assert "SIM-MP-001" in body["scenarios"]
    assert body["agent_scoped_evaluated"] is False
    assert client.get("/api/doors/readiness?agent_id=a1").json()["agent_scoped_evaluated"] is True
    for sid, row in body["scenarios"].items():
        assert row["scenario_id"] == sid
        assert {"route_blocked", "unevaluated", "not_open", "state"} <= set(row)
        assert "closed" not in row  # renamed: the list means "not in the open set", not a closed state


def test_scenario_doors_per_detection(client):
    r = client.get("/api/scenarios/SIM-MP-001/doors")
    body = r.json()
    assert r.status_code == 200
    assert body["readiness"]["scenario_id"] == "SIM-MP-001"
    assert body["detections"]
    d = body["detections"][0]
    assert set(d) == {"step_id", "detection_id", "requirement"}
    assert {"all_of", "route", "underived"} <= set(d["requirement"])


def test_scenario_doors_404(client):
    r = client.get("/api/scenarios/SIM-NOPE-999/doors")
    assert r.status_code == 404
    assert r.json() == {"error": "Scenario not found", "code": "SCENARIO_NOT_FOUND",
                        "detail": "scenario_id='SIM-NOPE-999'"}


def test_phase1_nothing_open(client):
    doors = client.get("/api/doors").json()["doors"]
    assert {d["state"] for d in doors} == {"unknown"}
    assert {d["rung"] for d in doors} <= {"authored", "configured"}
    scen = client.get("/api/doors/readiness").json()["scenarios"]
    assert {v["state"] for v in scen.values()} <= {"needs_doors", "not_evaluated"}


def test_tenant_verified_zero(client):
    for path in ("/api/doors", "/api/doors/readiness", "/api/scenarios/SIM-MP-001/doors"):
        assert client.get(path).json()["tenant_verified"] == 0


def test_absent_registry_is_degraded_not_empty(client, monkeypatch):
    monkeypatch.setattr("api.doors.load", lambda: None)
    d = client.get("/api/doors").json()
    assert d["registry_loaded"] is False
    assert all(row["detections_needing"] is None for row in d["doors"])
    r = client.get("/api/doors/readiness").json()
    assert r["registry_loaded"] is False
    assert {v["state"] for v in r["scenarios"].values()} == {"not_evaluated"}
    s = client.get("/api/scenarios/SIM-MP-001/doors").json()
    assert s["registry_loaded"] is False and s["code"] == "REGISTRY_ABSENT"
    assert s["readiness"]["state"] == "not_evaluated" and s["detections"] == []


def test_malformed_registry_is_a_structured_500(client, monkeypatch):
    from engine.dataset_requirements import DatasetRequirementsError

    def boom():
        raise DatasetRequirementsError("bad root")
    monkeypatch.setattr("api.doors.load", boom)
    r = client.get("/api/doors")
    assert r.status_code == 500 and r.json()["code"] == "DATASET_REGISTRY_MALFORMED"


def test_bulk_endpoints_are_fast(client):
    t = time.monotonic()
    client.get("/api/doors")
    client.get("/api/doors/readiness")
    assert time.monotonic() - t < 2.0


def test_configured_unchecked_door_is_unknown_state_configured_rung(client, monkeypatch):
    async def facts(db):
        return {"saas_okta": "configured"}
    monkeypatch.setattr("api.doors.door_state.configured_facts", facts)
    row = next(d for d in client.get("/api/doors").json()["doors"] if d["door_id"] == "saas_okta")
    assert row["state"] == "unknown" and row["rung"] == "configured"


def test_open_event_without_configured_fact_reads_open(client, session_factory):
    from engine import door_state

    async def _rec():
        async with session_factory() as s:
            await door_state.record(s, door_key="saas_okta", door_id="saas_okta", state="open",
                                    basis="attested", rung="reachable", evidence={}, attested_by="t")
    asyncio.run(_rec())
    row = next(d for d in client.get("/api/doors").json()["doors"] if d["door_id"] == "saas_okta")
    assert row["state"] == "open" and row["basis"] == "attested"


def test_detections_needing_excludes_composer_drafts(client, session_factory):
    """A Composer draft is not corpus coverage: it must not inflate any door's count."""
    from engine.scenario_loader import _parse_and_validate, _schema_to_orm_kwargs
    from models import Scenario

    before = {d["key"]: d["detections_needing"] for d in client.get("/api/doors").json()["doors"]}
    assert before["xdr_agent"] > 0

    async def _seed_draft():
        p = os.path.join(settings.CORTEXSIM_BASE_DIR, "scenarios", "multi_plane",
                         "mp-001-c2-beacon-ngfw-xdr-stitch.yml")
        schema, err = _parse_and_validate(p)
        assert err is None, err
        kw = _schema_to_orm_kwargs(schema)
        kw.update(scenario_id="SIM-DRAFT-001", status="draft")
        async with session_factory() as s:
            s.add(Scenario(**kw))
            await s.commit()

    asyncio.run(_seed_draft())
    after = {d["key"]: d["detections_needing"] for d in client.get("/api/doors").json()["doors"]}
    assert after == before
