"""The collector emitters must present the XSIAM HTTP collector's API key bare.

The XSIAM 3.x page "Set up an HTTP log collector to receive logs" sends the key
as ``Authorization: {api_key}`` with no scheme prefix. The emitters used to
default ``auth_scheme`` to ``"Bearer"``, so a campaign that only supplied a token
sent ``Authorization: Bearer <key>`` and every POST was refused (401).

Subjects are discovered from the plugin registry so a new collector emitter is
covered without editing this file.
"""

from __future__ import annotations

import asyncio

import pytest

from eal_simulator.analytics_emitter import AnalyticsEmitterParams
from eal_simulator.registry import get_default_registry

_COLLECTOR = "https://api-tenant.example.invalid/logs/v1/event"

# Plugins whose default event pattern needs extra fields to validate. Unrelated
# to auth; listed so every plugin can be built with its defaults otherwise.
_REQUIRED_EXTRAS = {
    "ngfw_threat_emitter": {"file_sha256": "0" * 64, "file_name": "sample.exe"},
}

_EMITTERS = sorted(
    (cls for cls in get_default_registry()
     if issubclass(cls.Meta.params_model, AnalyticsEmitterParams)),
    key=lambda cls: cls.Meta.name,
)


def test_discovery_finds_the_collector_emitters():
    # Fourteen emitters inherit the collector auth fields today. A smaller
    # number means discovery broke and the tests below would pass on nothing.
    assert len(_EMITTERS) >= 14, [cls.Meta.name for cls in _EMITTERS]


def test_base_default_scheme_is_empty():
    assert AnalyticsEmitterParams.model_fields["auth_scheme"].default == ""


@pytest.mark.parametrize("plugin_cls", _EMITTERS, ids=lambda c: c.Meta.name)
def test_token_alone_is_sent_bare(plugin_cls):
    params = plugin_cls.Meta.params_model.model_validate({
        "collector_url": _COLLECTOR, "auth_token": "xsiam-api-key",
        **_REQUIRED_EXTRAS.get(plugin_cls.Meta.name, {}),
    })
    assert params.auth_scheme == ""
    plugin = plugin_cls()
    plugin._verify_tls = False
    client = plugin._build_client(params)
    try:
        assert client.headers["authorization"] == "xsiam-api-key"
    finally:
        asyncio.run(client.aclose())


@pytest.mark.parametrize("plugin_cls", _EMITTERS[:1], ids=lambda c: c.Meta.name)
def test_explicit_bearer_still_honoured(plugin_cls):
    # Other collectors (a Cribl or nginx forwarder) may want a scheme; the
    # field stays configurable.
    params = plugin_cls.Meta.params_model.model_validate(
        {"collector_url": _COLLECTOR, "auth_token": "t", "auth_scheme": "Bearer"}
    )
    plugin = plugin_cls()
    plugin._verify_tls = False
    client = plugin._build_client(params)
    try:
        assert client.headers["authorization"] == "Bearer t"
    finally:
        asyncio.run(client.aclose())
