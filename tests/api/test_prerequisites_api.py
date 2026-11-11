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
    assert body["scenario_id"] == SCENARIO
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
    assert r.json()["scenario_id"] == "SIM-DRAFT-unbound"
    assert [n["code"] for n in r.json()["notes"]] == ["NO_CARD_BINDING"]
    assert client.get("/api/scenarios/SIM-DRAFT-nope/prerequisites").status_code == 404


def test_bad_card_shape_is_structured_500_on_both_routes(client, monkeypatch):
    from engine.ttp_catalog import catalog
    # SIM-MP-001 cites TTP-2026-0047, so the scenario route reads the broken card too.
    bad = {"TTP-2026-0047": {"detections": {"xql_queries": "nope"}}}
    monkeypatch.setattr(catalog, "all_raw", lambda: bad)
    for url in (f"/api/scenarios/{SCENARIO}/prerequisites", "/api/prerequisites/datasets"):
        r = client.get(url)
        assert r.status_code == 500, url
        body = r.json()
        assert body["code"] == "CARD_SHAPE_UNRECOGNISED" and "TTP-2026-0047" in body["detail"], url


def test_health_reports_bad_card_shape_as_degraded(monkeypatch):
    from api import health
    from engine.ttp_catalog import catalog
    monkeypatch.setattr(catalog, "all_raw", lambda: {"TTP-BAD": {"detections": []}})
    comp = health.probe_dataset_requirements()
    assert comp["status"] == "degraded" and comp["code"] == "CARD_SHAPE_UNRECOGNISED"
    assert "TTP-BAD" in comp["detail"] and "Fix:" in comp["detail"]


# --- boot check -------------------------------------------------------------

def _boot(monkeypatch, *, strict, registry, cards=None, load_raises=False):
    from engine import dataset_requirements as dr
    monkeypatch.setattr(settings, "CORTEXSIM_STRICT_DATASETS", strict)
    if load_raises:
        def boom():
            raise DatasetRequirementsError("registry /p is malformed at root: bad")
        monkeypatch.setattr(dr, "load", boom)
    else:
        monkeypatch.setattr(dr, "load", lambda: registry)
    return dr.boot_check_dataset_requirements(cards if cards is not None else {})


def test_boot_malformed_registry_strict_off_logs_and_continues(monkeypatch, caplog):
    import logging
    with caplog.at_level(logging.ERROR):
        _boot(monkeypatch, strict=False, registry=None, load_raises=True)
    assert any("malformed" in r.message and r.levelno == logging.ERROR for r in caplog.records)


def test_boot_malformed_registry_strict_on_raises(monkeypatch):
    with pytest.raises(DatasetRequirementsError, match="malformed"):
        _boot(monkeypatch, strict=True, registry=None, load_raises=True)


def test_boot_unregistered_strict_on_raises(monkeypatch):
    cards = {"TTP-X": {"detections": {"xql_queries": [{"dataset": "mystery_raw"}]}}}
    with pytest.raises(DatasetRequirementsError, match="mystery_raw"):
        _boot(monkeypatch, strict=True, registry={}, cards=cards)


def test_boot_absent_registry_strict_off_does_not_raise(monkeypatch):
    cards = {"TTP-X": {"detections": {"xql_queries": [{"dataset": "mystery_raw"}]}}}
    assert _boot(monkeypatch, strict=False, registry=None, cards=cards) == ["mystery_raw"]


def test_boot_bad_card_shape_strict_off_continues_strict_on_raises(monkeypatch):
    bad = {"TTP-BAD": {"detections": []}}
    _boot(monkeypatch, strict=False, registry={}, cards=bad)
    with pytest.raises(DatasetRequirementsError, match="TTP-BAD"):
        _boot(monkeypatch, strict=True, registry={}, cards=bad)


# --- Task 5: query library -------------------------------------------------

def test_query_library_is_plain_text_and_rejects_unknown_purpose(client):
    r = client.get(f"/api/scenarios/{SCENARIO}/query-library")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert "content-disposition" not in r.headers
    r = client.get(f"/api/scenarios/{SCENARIO}/query-library?purpose=fun")
    assert r.status_code == 422
    assert r.json()["code"] == "PURPOSE_UNRECOGNISED"


@pytest.mark.parametrize("bad", ["", "hunt,", "hunt,hunt", "hunt,,metric"])
def test_query_library_empty_or_duplicate_purpose_is_422(client, bad):
    r = client.get(f"/api/scenarios/{SCENARIO}/query-library", params={"purpose": bad})
    assert r.status_code == 422 and r.json()["code"] == "PURPOSE_UNRECOGNISED"


def test_query_library_unknown_scenario_is_404(client):
    r = client.get("/api/scenarios/SIM-NOPE-999/query-library")
    assert r.status_code == 404 and r.json()["code"] == "SCENARIO_NOT_FOUND"


def test_query_library_real_corpus_validation(client):
    r = client.get(f"/api/scenarios/{SCENARIO}/query-library?purpose=validation")
    assert r.status_code == 200
    assert r.text.startswith(f"# {SCENARIO}")
    assert "(validation)" in r.text.splitlines()[0]
    assert "## " in r.text


def test_query_library_default_header_and_pointer(client):
    r = client.get(f"/api/scenarios/{SCENARIO}/query-library")
    assert r.headers["content-type"] == "text/plain; charset=utf-8"
    assert r.text.splitlines()[0].endswith("query library (hunt, investigation)")
    assert "# The cited cards carry" in r.text and "add ?purpose=" in r.text


def test_query_library_purpose_is_stripped(client):
    r = client.get(f"/api/scenarios/{SCENARIO}/query-library", params={"purpose": " hunt , validation "})
    assert r.status_code == 200
    assert "(hunt, validation)" in r.text.splitlines()[0]


def test_query_library_malformed_card_is_structured_500(client, monkeypatch):
    from engine.ttp_catalog import catalog
    raw = dict(catalog.all_raw())
    for k in raw:
        raw[k] = {"detections": "broken"}
    monkeypatch.setattr(catalog, "all_raw", lambda: raw)
    r = client.get(f"/api/scenarios/{SCENARIO}/query-library")
    assert r.status_code == 500 and r.json()["code"] == "CARD_SHAPE_UNRECOGNISED"
