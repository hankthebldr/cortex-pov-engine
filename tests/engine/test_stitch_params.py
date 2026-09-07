"""Phase 3b — the binding -> EAL plugin params adapter.

The adapter is deliberately narrow: the analytics emitters generate their record
fields internally, so the only cross-channel entity it can honestly inject is the
identity principal, planted as ``canary_token``. It injects nothing for a network
plugin (whose source is SimCore, not the endpoint) and nothing for a
non-token-shaped account.
"""
from __future__ import annotations

import pytest

from eal_simulator import get_default_registry
from eal_simulator.stitch_params import stitch_binding_to_eal_params
from engine.stitch_context import StitchBinding


@pytest.fixture(scope="module")
def registry():
    return get_default_registry()


def test_analytics_emitter_gets_canary_token_from_account(registry):
    ngfw = registry.get("ngfw_eal_emitter")
    binding = StitchBinding(account="csim-abc123def")
    out = stitch_binding_to_eal_params(binding, ngfw)
    assert out == {"canary_token": "csim-abc123def"}


def test_all_analytics_emitters_accept_the_injected_token(registry):
    # Every analytics-family plugin must actually validate the injected token.
    binding = StitchBinding(account="csim-shared01")
    for name in [
        "ngfw_eal_emitter", "cloud_audit_emitter", "azure_audit_emitter",
        "k8s_audit_emitter", "m365_activity_emitter", "ad_windows_emitter",
        "idp_signin_emulator", "cloud_storage_compute_emitter",
    ]:
        plugin = registry.get(name)
        out = stitch_binding_to_eal_params(binding, plugin)
        assert out.get("canary_token") == "csim-shared01", name


def test_network_egress_plugin_gets_nothing(registry):
    # c2_http_beacon POSTs from SimCore's own process — no shared endpoint
    # entity is injectable, so the adapter stays out of its params.
    c2 = registry.get("c2_http_beacon")
    binding = StitchBinding(account="csim-abc123def")
    assert stitch_binding_to_eal_params(binding, c2) == {}


def test_none_binding_yields_empty(registry):
    ngfw = registry.get("ngfw_eal_emitter")
    assert stitch_binding_to_eal_params(None, ngfw) == {}


def test_non_token_shaped_account_is_skipped_not_coerced(registry):
    # A literal username with spaces / uppercase can't be a canary token; the
    # adapter skips it rather than coercing an invalid value into the params.
    ngfw = registry.get("ngfw_eal_emitter")
    for bad in ["Alice Smith", "DOMAIN\\svc", "svc@corp.example", ""]:
        binding = StitchBinding(account=bad)
        assert stitch_binding_to_eal_params(binding, ngfw) == {}, bad


def test_no_account_yields_empty(registry):
    ngfw = registry.get("ngfw_eal_emitter")
    assert stitch_binding_to_eal_params(StitchBinding(), ngfw) == {}
