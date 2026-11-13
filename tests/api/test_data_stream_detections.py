"""GET /api/eal/data-streams/{source_key}/detections: which scenario detections read a
catalogue source's dataset, the suggested names, and the route verdict.

A source no card reads must read degraded, never ok; edited names must re-run the
verdict; the lab flag is null until a collector host is given.
"""
from __future__ import annotations

import asyncio
import os

import pytest

import config
from config import settings

SCENARIOS = (
    ("multi_plane", "mp-001-c2-beacon-ngfw-xdr-stitch.yml"),
    ("itdr", "sim-itdr-001-impossible-travel.yml"),
)


@pytest.fixture
def client(make_client, session_factory):
    from api.eal import router as eal_router
    from engine.scenario_loader import _parse_and_validate, _schema_to_orm_kwargs
    from engine.ttp_catalog import catalog, default_corpus_dir
    from models import Scenario

    catalog.load(default_corpus_dir(settings.CORTEXSIM_BASE_DIR))

    async def _seed():
        async with session_factory() as s:
            for folder, name in SCENARIOS:
                schema, err = _parse_and_validate(
                    os.path.join(settings.CORTEXSIM_BASE_DIR, "scenarios", folder, name))
                assert err is None, err
                s.add(Scenario(**_schema_to_orm_kwargs(schema)))
            await s.commit()

    asyncio.run(_seed())
    return make_client(eal_router)


def test_okta_lists_its_detections_with_types_and_a_disputed_verdict(client):
    body = client.get("/api/eal/data-streams/okta/detections").json()
    assert body["card_dataset"] == "okta_sso" and body["verdict"]["verdict"] == "name_disputed"
    assert body["count"] == len(body["detections"]) > 0
    assert body["status"] == "ok" and body["code"] is None
    assert all({"scenario_id", "step_id", "detection_id", "type", "ttp_ref"} <= d.keys()
               for d in body["detections"])
    assert {d["scenario_id"] for d in body["detections"]} == {"SIM-ITDR-001"}
    assert body["cards"] and all(c["kinds"] for c in body["cards"])


def test_a_source_no_card_reads_is_degraded_not_ok(client):
    body = client.get("/api/eal/data-streams/duo/detections").json()
    assert (body["count"], body["status"], body["code"]) == (0, "degraded", "NO_DETECTION_READS_DATASET")
    assert body["verdict"]["verdict"] == "matches"


def test_edited_names_rerun_the_verdict(client):
    body = client.get("/api/eal/data-streams/duo/detections?vendor=duo&product=logs").json()
    assert body["verdict"]["verdict"] == "name_differs"


def test_names_are_stripped_like_the_target_put(client):
    padded = client.get("/api/eal/data-streams/duo/detections?vendor=%20duo%20&product=%20logs%20").json()
    plain = client.get("/api/eal/data-streams/duo/detections?vendor=duo&product=logs").json()
    assert padded["verdict"] == plain["verdict"]


def test_lab_flag_needs_a_host(client, monkeypatch):
    monkeypatch.setattr(config.settings, "CORTEXSIM_DOOR_PROBE_TENANTS", "lab.xdr.example.com",
                        raising=False)
    url = "/api/eal/data-streams/okta/detections"
    assert client.get(url).json()["lab_tenant"] is None
    assert client.get(url + "?collector_host=lab.xdr.example.com").json()["lab_tenant"] is True
    assert client.get(url + "?collector_host=other.example.com").json()["lab_tenant"] is False


def test_test_channel_is_not_applicable(client):
    body = client.get("/api/eal/data-streams/test_channel/detections").json()
    assert (body["status"], body["code"], body["count"]) == ("not_applicable", "TEST_CHANNEL", 0)
    assert body["verdict"]["verdict"] == "test_channel"
    assert body["prefill"] == {"vendor": "cortexsim", "product": "door_http"}


def test_unknown_key_is_a_structured_404(client):
    r = client.get("/api/eal/data-streams/nope/detections")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "DATA_SOURCE_UNKNOWN"
    assert set(r.json()["detail"]) >= {"error", "code", "detail"}
