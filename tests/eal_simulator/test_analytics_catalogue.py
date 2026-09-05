"""Tests for the analytics data-source catalogue + coverage reporter, and the
spine's negative-control contract.

The coverage reporter is a named deliverable: it must report against the
vendor's 34-source catalogue (gaps included), keep authored and proven as two
numbers (tenant-verified is 0), and REFUSE to hide an emitter's typo'd source
key rather than silently drop it.
"""
from __future__ import annotations

import asyncio

import pytest

from eal_simulator import get_default_registry, PluginRegistry
from eal_simulator.analytics_catalogue import (
    ADDRESSABLE_SOURCES,
    DATA_SOURCE_CATALOGUE,
    TOTAL_SOURCES,
    UnknownDataSourceError,
    coverage_report,
    family_manifests,
    get_source,
)
from eal_simulator.analytics_emitter import (
    AnalyticsLogEmitter,
    NegativeControlEmitterParams,
)
from eal_simulator.base import SimulationContext


class TestCatalogue:
    def test_thirty_four_sources(self):
        assert TOTAL_SOURCES == 34
        assert len(DATA_SOURCE_CATALOGUE) == 34

    def test_addressable_excludes_xdr_agent_and_unspecified(self):
        # Two XDR Agent sources + Unspecified are non-addressable.
        assert ADDRESSABLE_SOURCES == 31
        non = {s.key for s in DATA_SOURCE_CATALOGUE if not s.addressable}
        assert non == {"xdr_agent", "xdr_agent_xth", "unspecified"}

    def test_keys_unique(self):
        keys = [s.key for s in DATA_SOURCE_CATALOGUE]
        assert len(keys) == len(set(keys))

    def test_get_source_unknown_raises(self):
        with pytest.raises(UnknownDataSourceError):
            get_source("totally_made_up_source")


class TestCoverageReport:
    def test_all_sources_present_including_gaps(self):
        rep = coverage_report(get_default_registry())
        assert len(rep["sources"]) == TOTAL_SOURCES
        states = {s["state"] for s in rep["sources"]}
        # A gap must be visible as a gap, never omitted.
        assert "gap" in states

    def test_counts_add_up(self):
        c = coverage_report(get_default_registry())["counts"]
        assert c["total"] == TOTAL_SOURCES
        assert c["covered"] + c["partial"] + c["gap"] + c["not_addressable"] == c["total"]
        assert c["addressable"] == c["covered"] + c["partial"] + c["gap"]

    def test_third_party_buckets_now_covered_with_negative_control(self):
        rep = coverage_report(get_default_registry())
        by_key = {s["key"]: s for s in rep["sources"]}
        for key in ("third_party_firewalls", "third_party_vpns", "third_party_alerts"):
            assert by_key[key]["state"] == "covered"
            assert by_key[key]["authored"] is True
            assert by_key[key]["has_negative_control"] is True

    def test_authored_is_not_proven(self):
        c = coverage_report(get_default_registry())["counts"]
        # Authored can be >0; proven is 0 unconditionally (tenant-verified is 0).
        assert c["authored"] > 0
        assert c["proven"] == 0

    def test_every_covered_row_is_unproven(self):
        rep = coverage_report(get_default_registry())
        assert all(s["proven"] is False for s in rep["sources"])

    def test_banner_states_authored_not_proven(self):
        rep = coverage_report(get_default_registry())
        assert "Authored is not proven" in rep["authored_not_proven"]
        assert "tenant-verified is 0" in rep["authored_not_proven"]

    def test_unknown_source_key_raises_not_dropped(self):
        # An emitter that declares a bogus source key must FAIL the report, not
        # silently vanish from coverage (tolerance hides bugs).
        class _BogusParams(NegativeControlEmitterParams):
            pass

        class _BogusEmitter(AnalyticsLogEmitter):
            class Meta:
                name = "bogus_source_emitter"
                data_sources = ["this_source_does_not_exist"]
                datasets = ["bogus_raw"]
                params_model = _BogusParams

            def build_events(self, params, *, sim_run_id, iteration):
                return []

        reg = PluginRegistry()
        reg.register(_BogusEmitter)
        with pytest.raises(UnknownDataSourceError):
            coverage_report(reg)


class TestFamilyManifests:
    def test_new_emitters_listed_with_full_and_partial(self):
        manifests = family_manifests(get_default_registry())
        by_name = {m["name"]: m for m in manifests}
        assert "third_party_firewall_emitter" in by_name
        # idp_signin_emulator declares two partial sources.
        idp = by_name["idp_signin_emulator"]
        partial_keys = {s["key"] for s in idp["sources"] if s["coverage"] == "partial"}
        assert partial_keys == {"azuread", "google_workspace_authentication"}


class TestNegativeControlContract:
    """The spine's records_for must dispatch to the negative-control builder and
    must REFUSE a negative control on an emitter that does not support one."""

    def _ctx(self, params):
        async def _emit(_):
            return None
        return SimulationContext(
            campaign_id="c", run_id="r", step_id="s",
            simulation_run_id="cortexsim-x", dry_run=True,
            target_allowlist=[], emit_event=_emit, params=params,
        )

    def test_unsupported_emitter_refuses_negative_control(self):
        class _NoNegParams(NegativeControlEmitterParams):
            pass

        class _NoNegEmitter(AnalyticsLogEmitter):
            supports_negative_control = False

            class Meta:
                name = "no_neg_emitter"
                data_sources = ["third_party_alerts"]
                datasets = ["x_raw"]
                params_model = _NoNegParams

            def build_events(self, params, *, sim_run_id, iteration):
                return [{"dataset": "x_raw", "cortexsim_run_id": sim_run_id}]

        emitter = _NoNegEmitter()
        params = _NoNegParams.model_validate({
            "collector_url": "https://c.invalid/e", "negative_control": True,
        })
        with pytest.raises(ValueError, match="does not support a negative control"):
            emitter.records_for(params, sim_run_id="x", iteration=1)

    def test_supported_emitter_dispatches_to_negative_builder(self):
        from eal_simulator.plugins.third_party_firewall_emitter import (
            ThirdPartyFirewallEmitter, ThirdPartyFirewallParams,
        )
        emitter = ThirdPartyFirewallEmitter()
        neg = ThirdPartyFirewallParams.model_validate({
            "collector_url": "https://c.invalid/e", "event_pattern": "port_scan",
            "negative_control": True,
        })
        recs = emitter.records_for(neg, sim_run_id="x", iteration=1)
        assert all(r.get("negative_control") for r in recs)
        assert len(recs) == 2
