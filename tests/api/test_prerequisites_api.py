"""Task 4 — prerequisites routes, the dataset_requirements health component and the
strict boot check.

Each test names the failure it guards: an absent registry must read as degraded,
never as "no requirements"; a malformed one must raise a structured 500 rather
than a 200 that hides it; the strict flag must refuse boot on an unregistered
dataset; nothing here may open a socket.
"""
from __future__ import annotations

import asyncio
import os
import socket

import pytest

from config import settings
from engine.dataset_requirements import (
    DatasetRequirementsError,
    check_datasets_registered,
    load as registry_load,
)

SCENARIO = "SIM-MP-001"


@pytest.fixture
def client(make_client, session_factory):
    """Prerequisites router + scenarios router (mirroring main.py's order), with the
    real card corpus loaded and one real scenario seeded into the in-memory DB."""
    from api.prerequisites import router as prereq_router
    from api.scenarios import router as scenarios_router
    from engine.scenario_loader import _parse_and_validate, _schema_to_orm_kwargs
    from engine.ttp_catalog import catalog, default_corpus_dir
    from models import Scenario

    catalog.load(default_corpus_dir(settings.CORTEXSIM_BASE_DIR))
    path = os.path.join(settings.CORTEXSIM_BASE_DIR, "scenarios", "multi_plane",
                        "mp-001-c2-beacon-ngfw-xdr-stitch.yml")
    schema, err = _parse_and_validate(path)
    assert err is None, err

    async def _seed():
        async with session_factory() as s:
            s.add(Scenario(**_schema_to_orm_kwargs(schema)))
            await s.commit()

    asyncio.run(_seed())
    return make_client(prereq_router, scenarios_router)


def _malformed(monkeypatch):
    def boom():
        raise DatasetRequirementsError("dataset requirement registry X is malformed at root: bad")
    return boom


def test_scenario_prerequisites_ok(client):
    r = client.get(f"/api/scenarios/{SCENARIO}/prerequisites")
    body = r.json()
    assert r.status_code == 200 and body["registry_loaded"] is True
    assert body["tenant_verified"] == 0 and body["write_path"] is False
    assert any(d.get("produced_by") == "strata-ngfw" for d in body["datasets"])


def test_unknown_scenario_is_structured_404(client):
    r = client.get("/api/scenarios/SIM-NOPE-999/prerequisites")
    assert r.status_code == 404 and r.json()["code"] == "SCENARIO_NOT_FOUND"
    assert set(r.json()) >= {"error", "code", "detail"}


def test_registry_listing_counts_and_has_no_unregistered(client):
    body = client.get("/api/prerequisites/datasets").json()
    assert body["registry_loaded"] is True
    assert body["count"] == len(body["datasets"]) > 0 and body["unregistered"] == []
    assert all(isinstance(d["cards"], list) and d["cards"] == sorted(d["cards"])
               for d in body["datasets"])
    assert any(d["cards"] for d in body["datasets"]), "no dataset joined to any card"


def test_absent_registry_is_200_degraded(client, monkeypatch):
    monkeypatch.setattr("api.prerequisites.load", lambda: None)
    body = client.get(f"/api/scenarios/{SCENARIO}/prerequisites").json()
    assert body["registry_loaded"] is False
    assert any(n["code"] == "REGISTRY_ABSENT" for n in body["notes"])


def test_absent_registry_listing_shape(client, monkeypatch):
    from engine.dataset_requirements import card_datasets
    from engine.ttp_catalog import catalog

    monkeypatch.setattr("api.prerequisites.load", lambda: None)
    monkeypatch.setattr("api.prerequisites.registry_source",
                        lambda: {"path": "/x/dataset_requirements.yml", "source": "absent"})
    r = client.get("/api/prerequisites/datasets")
    body = r.json()
    assert r.status_code == 200
    assert body["registry_loaded"] is False and body["count"] == 0 and body["datasets"] == []
    assert body["unregistered"] == sorted(card_datasets(catalog.all_raw()))
    assert body["unregistered"], "an absent registry must list every card dataset, not none"


def test_malformed_registry_is_structured_500_on_both_routes(client, monkeypatch):
    monkeypatch.setattr("api.prerequisites.load", _malformed(monkeypatch))
    monkeypatch.setattr("api.prerequisites.registry_source", _malformed(monkeypatch))
    for url in (f"/api/scenarios/{SCENARIO}/prerequisites", "/api/prerequisites/datasets"):
        r = client.get(url)
        assert r.status_code == 500, url
        body = r.json()
        assert body["code"] == "DATASET_REGISTRY_MALFORMED" and body["error"] and body["detail"], url


def test_health_reports_absent_registry_as_degraded(client, monkeypatch):
    from api import health
    monkeypatch.setattr("api.health.dataset_requirements_load", lambda: None)
    comp = health.probe_dataset_requirements()
    assert comp["status"] == "degraded" and comp["code"] == "DATASET_REGISTRY_ABSENT"
    assert "Fix:" in comp["detail"]


def test_health_reports_malformed_registry_as_degraded(monkeypatch):
    from api import health
    monkeypatch.setattr("api.health.dataset_requirements_load", _malformed(monkeypatch))
    comp = health.probe_dataset_requirements()
    assert comp["status"] == "degraded" and comp["code"] == "DATASET_REGISTRY_MALFORMED"
    assert comp["path"] and "Fix:" in comp["detail"]


def test_health_reports_unregistered_datasets_as_degraded(client, monkeypatch):
    from api import health
    from engine import dataset_requirements as dr
    from engine.ttp_catalog import catalog

    real = dr.load()
    trimmed = dict(real)
    dropped = sorted(dr.card_datasets(catalog.all_raw()))[0]
    trimmed.pop(dropped)
    monkeypatch.setattr("api.health.dataset_requirements_load", lambda: trimmed)
    comp = health.probe_dataset_requirements()
    assert comp["status"] == "degraded" and comp["code"] == "DATASETS_UNREGISTERED"
    assert dropped in comp["unregistered"]


def test_health_ok_when_loaded_and_all_registered(client):
    from api import health
    comp = health.probe_dataset_requirements()
    assert comp["status"] == "ok", comp


def test_component_is_registered_in_health_payload():
    from api import health
    assert "dataset_requirements" in health.COMPONENT_KEYS
    body = asyncio.run(health.health_payload())
    assert "dataset_requirements" in body["components"]


def test_health_endpoint_names_the_component_when_absent(client, monkeypatch):
    from api import health
    monkeypatch.setattr("api.health.dataset_requirements_load", lambda: None)
    body = asyncio.run(health.health_payload())
    assert "dataset_requirements" in body["degraded_components"]
    assert body["components"]["dataset_requirements"]["code"] == "DATASET_REGISTRY_ABSENT"


def test_no_outbound_call(client, monkeypatch):
    monkeypatch.setattr(socket.socket, "connect",
                        lambda *a, **k: pytest.fail("outbound call"))
    assert client.get(f"/api/scenarios/{SCENARIO}/prerequisites").status_code == 200
    assert client.get("/api/prerequisites/datasets").status_code == 200


def test_strict_flag_refuses_boot_on_unregistered_dataset(monkeypatch):
    monkeypatch.setattr(settings, "CORTEXSIM_STRICT_DATASETS", True)
    with pytest.raises(DatasetRequirementsError, match="mystery_raw"):
        check_datasets_registered({"mystery_raw": {"TTP-X"}}, {})


def test_non_strict_returns_unregistered_without_raising(monkeypatch):
    monkeypatch.setattr(settings, "CORTEXSIM_STRICT_DATASETS", False)
    assert check_datasets_registered({"b": {"T"}, "a": {"T"}}, {}) == ["a", "b"]
    assert check_datasets_registered({"a": {"T"}}, None) == ["a"]


def test_strict_flag_passes_when_all_registered(monkeypatch):
    monkeypatch.setattr(settings, "CORTEXSIM_STRICT_DATASETS", True)
    reg = registry_load()
    from engine.dataset_requirements import card_datasets
    from engine.ttp_catalog import catalog, default_corpus_dir
    catalog.load(default_corpus_dir(settings.CORTEXSIM_BASE_DIR))
    assert check_datasets_registered(card_datasets(catalog.all_raw()), reg) == []


def test_strict_setting_defaults_off():
    from config import Settings
    assert Settings.model_fields["CORTEXSIM_STRICT_DATASETS"].default is False


def test_draft_id_style_resolves_and_reports_no_card_binding(client, session_factory):
    """Drafts live in the same table under SIM-DRAFT-<slug>; an unbound draft is a legal
    saved state and must answer NO_CARD_BINDING, not 404 and not an empty 'all clear'."""
    from models import Scenario

    async def _seed():
        async with session_factory() as s:
            s.add(Scenario(scenario_id="SIM-DRAFT-unbound", name="x", version="1", status="draft",
                           plane="EDR", detection_types=[], steps=[], uc_ref="", tc_ref="", uc_name="", tc_name="", mitre_tactic_name="", mitre_technique_name="",
                           mitre_tactic="", mitre_technique="", execution_identity={}, cleanup=[]))
            await s.commit()

    asyncio.run(_seed())
    r = client.get("/api/scenarios/SIM-DRAFT-unbound/prerequisites")
    assert r.status_code == 200
    assert [n["code"] for n in r.json()["notes"]] == ["NO_CARD_BINDING"]
    assert client.get("/api/scenarios/SIM-DRAFT-nope/prerequisites").status_code == 404
