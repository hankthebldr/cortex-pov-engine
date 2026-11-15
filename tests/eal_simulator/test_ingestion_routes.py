"""Tests for the ingestion-route registry.

The registry answers the question the readiness gate could not: can a record
reach the detector at all? The vendor documents that built-in analytics run only
on normalised logs, that a custom HTTP collector dataset is normalised only once
an XDM rule maps it, and that the Cribl generic UUID gets no out-of-the-box
analytics. A route that cannot feed analytics must never read as one that can.
"""
from __future__ import annotations

import dataclasses

import pytest

from eal_simulator.analytics_catalogue import DATA_SOURCE_CATALOGUE
from eal_simulator.ingestion_routes import (
    COLLECTOR_CRIBL,
    COLLECTOR_HTTP,
    COLLECTOR_UNDECLARED,
    COLLECTOR_UNKNOWN,
    GENERIC_ROUTES,
    INGESTION_ONLY_SOURCES,
    ROUTES,
    IngestionRoute,
    _validate_route,
    pipe_for_collector_url,
    resolve_route,
    routes_for,
)

_GENERIC_UUID = "af01292940d7426594d3d3e55ae17ee0"


def _route(**overrides) -> IngestionRoute:
    base = dict(
        pipe="cribl_dedicated", tenant_dataset="okta_sso_raw", ootb_analytics="yes",
        dataset_evidence="documented", analytics_evidence="documented",
        doc_url="https://example.invalid/page",
        cribl_source_uuid="5faf4c1fdb8443d9920d6a54815432c1",
    )
    base.update(overrides)
    return IngestionRoute(**base)


class TestRegistryShape:
    def test_every_catalogue_source_has_an_entry_even_if_empty(self):
        # An unlisted source would read as "no route problem" rather than as
        # "no route known". Empty tuples are the honest answer, and they exist.
        assert set(ROUTES) == {s.key for s in DATA_SOURCE_CATALOGUE}
        assert ROUTES["duo"] == ()

    def test_a_cribl_uuid_always_names_the_same_dataset(self):
        seen: dict[str, str] = {}
        for routes in ROUTES.values():
            for r in routes:
                if r.cribl_source_uuid:
                    assert seen.setdefault(r.cribl_source_uuid, r.tenant_dataset) == r.tenant_dataset

    def test_no_dedicated_route_uses_the_generic_uuid(self):
        # The generic UUID gets no OOTB analytics; a dedicated route carrying it
        # would claim analytics the vendor says do not run.
        for routes in ROUTES.values():
            for r in routes:
                if r.pipe == "cribl_dedicated":
                    assert r.cribl_source_uuid != _GENERIC_UUID

    def test_generic_cribl_route_is_pinned_to_no_analytics(self):
        r = GENERIC_ROUTES["cribl_generic"]
        assert r.cribl_source_uuid == _GENERIC_UUID
        assert r.ootb_analytics == "no"
        assert r.analytics_evidence == "documented"

    def test_http_collector_needs_xdm_before_analytics(self):
        r = GENERIC_ROUTES["http_collector"]
        assert r.ootb_analytics == "requires_xdm"
        assert r.tenant_dataset == "<vendor>_<product>_raw"

    def test_logsim_only_facts_are_never_labelled_documented(self):
        reported = [
            r for routes in ROUTES.values() for r in routes
            if "phreezone/LogSimv2" in r.doc_url
        ]
        assert reported, "expected the LogSimv2-reported firewall datasets"
        assert all(r.dataset_evidence == "reported" for r in reported)

    def test_ingestion_only_sources_stay_outside_the_analytics_catalogue(self):
        catalogue = {s.key for s in DATA_SOURCE_CATALOGUE}
        keys = {s.key for s in INGESTION_ONLY_SOURCES}
        assert keys == {"proofpoint_tap", "infoblox_nios", "apache_httpd", "vmware_esxi"}
        assert not keys & catalogue
        for src in INGESTION_ONLY_SOURCES:
            assert all(r.ootb_analytics == "no" for r in src.routes)


class TestValidation:
    def test_unknown_pipe_raises(self):
        with pytest.raises(ValueError, match="unknown pipe"):
            _validate_route(_route(pipe="smoke_signal"), "t")

    def test_malformed_uuid_raises(self):
        with pytest.raises(ValueError, match="malformed Cribl"):
            _validate_route(_route(cribl_source_uuid="5faf-not-hex"), "t")

    def test_cribl_route_without_uuid_raises(self):
        with pytest.raises(ValueError, match="source UUID"):
            _validate_route(_route(cribl_source_uuid=None), "t")

    def test_inferred_analytics_claim_without_a_reason_raises(self):
        with pytest.raises(ValueError, match="must say why"):
            _validate_route(_route(analytics_evidence="inferred", note=""), "t")

    def test_evidence_outside_the_vocabulary_raises(self):
        with pytest.raises(ValueError, match="dataset_evidence"):
            _validate_route(_route(dataset_evidence="probably"), "t")

    def test_routes_are_frozen(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            routes_for("okta")[0].ootb_analytics = "no"  # type: ignore[misc]


class TestResolution:
    def test_source_specific_route_wins_over_generic(self):
        r = resolve_route("okta", "cribl_dedicated")
        assert r is not None and r.tenant_dataset == "okta_sso_raw"

    def test_generic_route_fills_in_for_any_source(self):
        r = resolve_route("duo", "http_collector")
        assert r is GENERIC_ROUTES["http_collector"]

    def test_no_documented_way_in_is_none_not_a_guess(self):
        assert resolve_route("duo", "cribl_dedicated") is None
        assert resolve_route("third_party_firewalls", "broker_wec") is None

    def test_unknown_pipe_raises(self):
        with pytest.raises(ValueError):
            resolve_route("okta", "carrier_pigeon")

    def test_unknown_source_raises(self):
        with pytest.raises(ValueError):
            routes_for("okta_typo")


class TestCollectorUrlClassification:
    @pytest.mark.parametrize("url,expected", [
        ("https://api-tenant.xdr.us.paloaltonetworks.com/logs/v1/event", COLLECTOR_HTTP),
        ("https://api-tenant.xdr.us.paloaltonetworks.com/logs/v1/event/", COLLECTOR_HTTP),
        ("https://api-tenant.xdr.us.paloaltonetworks.com/logs/v1/integrated/cribl/data",
         COLLECTOR_CRIBL),
        ("http://broker.lab:8088/ingest", COLLECTOR_UNKNOWN),
        ("", COLLECTOR_UNDECLARED),
        (None, COLLECTOR_UNDECLARED),
    ])
    def test_url_maps_to_the_door_it_hits(self, url, expected):
        assert pipe_for_collector_url(url) == expected
