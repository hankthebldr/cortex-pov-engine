"""panos_ngfw credential kind: validated at PUT time, key never listed."""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def cred_client(make_client):
    from api.credentials import router
    return make_client(router)


def _put(c, config, secret="K" * 40):
    return c.put("/api/credentials/integrations", json={
        "name": "fw1", "kind": "panos_ngfw", "plaintext_secret": secret, "config": config})


def test_bad_host_rejected(cred_client):
    r = _put(cred_client, {"host": "https://x"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "INTEGRATION_CONFIG_INVALID"


def test_missing_host_rejected(cred_client):
    assert _put(cred_client, {}).status_code == 400


def test_valid_host_stores_defaulted_config(cred_client):
    r = _put(cred_client, {"host": "192.0.2.1"})
    assert r.status_code == 200
    assert r.json()["config"]["verify_tls"] is True
    got = cred_client.get("/api/credentials/integrations/fw1").json()
    assert got["config"]["verify_tls"] is True and got["config"]["vsys"] == "vsys1"


def test_backing_secret_not_listed(cred_client):
    _put(cred_client, {"host": "192.0.2.1"}, secret="SUPER-PANOS-KEY-ZZZZ")
    body = cred_client.get("/api/credentials/secrets").json()
    blob = json.dumps(body)
    assert body["secrets"] == [] and "ZZZZ" not in blob and "__integration__" not in blob


def test_type_hint_forced_for_panos(cred_client, session_factory):
    import asyncio
    r = cred_client.put("/api/credentials/integrations", json={
        "name": "fw1", "kind": "panos_ngfw", "plaintext_secret": "K" * 40,
        "secret_type_hint": "generic", "config": {"host": "192.0.2.1"}})
    assert r.status_code == 200

    async def _hint():
        from security import CredentialStore
        async with session_factory() as s:
            row = await CredentialStore(s)._get_secret_row("__integration__/fw1")
            return row.type_hint, None
    hint, tail = asyncio.run(_hint())
    assert hint == "panos_api_key_credential" and tail is None
    assert cred_client.get("/api/credentials/secrets/__integration__/fw1").status_code == 404


# ── re-registration by pasted key (controller ruling, Task 8 review) ─────────
# Same rule as keygen: per-agent overrides describe the path through ONE
# firewall, so they survive a re-PUT to the same host and are cleared (and
# reported) when the host changes. Probes and zones are lab intent and stay.

_OVERRIDES = {
    "observed_sources": {"agent-a": "10.0.0.5"},
    "agent_addresses": {"agent-a": ["10.0.0.5"]},
    "probe_destinations": [{"ip": "203.0.113.9", "port": 443, "protocol": "tcp",
                            "application": "ssl"}],
    "inside_zone": "trust",
    "outside_zone": "untrust",
}


def test_first_panos_put_reports_overrides_not_cleared(cred_client):
    r = _put(cred_client, {"host": "192.0.2.1"})
    assert r.status_code == 200
    assert r.json()["overrides_cleared"] is False


def test_re_put_same_host_keeps_overrides(cred_client):
    assert _put(cred_client, {"host": "192.0.2.1", **_OVERRIDES}).status_code == 200
    r = _put(cred_client, {"host": "192.0.2.1", "forwarding_path": "syslog"})
    assert r.status_code == 200
    assert r.json()["overrides_cleared"] is False
    cfg = cred_client.get("/api/credentials/integrations/fw1").json()["config"]
    assert cfg["observed_sources"] == {"agent-a": "10.0.0.5"}
    assert cfg["agent_addresses"] == {"agent-a": ["10.0.0.5"]}
    assert cfg["probe_destinations"][0]["ip"] == "203.0.113.9"
    assert cfg["inside_zone"] == "trust" and cfg["outside_zone"] == "untrust"
    assert cfg["forwarding_path"] == "syslog"


def test_re_put_same_host_after_normalisation_keeps_overrides(cred_client):
    assert _put(cred_client, {"host": "fw.lab.example", **_OVERRIDES}).status_code == 200
    r = _put(cred_client, {"host": "FW.LAB.EXAMPLE"})
    assert r.status_code == 200
    assert r.json()["overrides_cleared"] is False
    cfg = cred_client.get("/api/credentials/integrations/fw1").json()["config"]
    assert cfg["observed_sources"] == {"agent-a": "10.0.0.5"}


def test_re_put_new_host_clears_source_overrides_keeps_probes(cred_client):
    assert _put(cred_client, {"host": "192.0.2.1", **_OVERRIDES}).status_code == 200
    r = _put(cred_client, {"host": "192.0.2.99"})
    assert r.status_code == 200
    assert r.json()["overrides_cleared"] is True
    cfg = cred_client.get("/api/credentials/integrations/fw1").json()["config"]
    assert cfg["observed_sources"] == {} and cfg["agent_addresses"] == {}
    assert cfg["probe_destinations"][0]["ip"] == "203.0.113.9"
    assert cfg["inside_zone"] == "trust"


def test_re_put_new_host_without_overrides_reports_false(cred_client):
    assert _put(cred_client, {"host": "192.0.2.1"}).status_code == 200
    r = _put(cred_client, {"host": "192.0.2.99"})
    assert r.json()["overrides_cleared"] is False


def test_explicit_config_in_body_wins_over_stored(cred_client):
    assert _put(cred_client, {"host": "192.0.2.1", **_OVERRIDES}).status_code == 200
    r = _put(cred_client, {"host": "192.0.2.1", "observed_sources": {}})
    assert r.status_code == 200
    cfg = cred_client.get("/api/credentials/integrations/fw1").json()["config"]
    assert cfg["observed_sources"] == {}
    assert cfg["agent_addresses"] == {"agent-a": ["10.0.0.5"]}


def test_non_panos_put_is_unchanged(cred_client):
    body = {"name": "other", "kind": "generic_thing", "plaintext_secret": "S" * 20,
            "config": {"a": 1}}
    assert cred_client.put("/api/credentials/integrations", json=body).status_code == 200
    body["config"] = {"b": 2}
    r = cred_client.put("/api/credentials/integrations", json=body)
    assert r.status_code == 200
    assert "overrides_cleared" not in r.json()
    assert cred_client.get("/api/credentials/integrations/other").json()["config"] == {"b": 2}


# ── final review M3: the whole operator-entered config survives a re-PUT ─────


def test_re_put_keeps_device_name_routing_instance_vsys_and_path_when_not_sent(cred_client):
    assert _put(cred_client, {"host": "192.0.2.1", "device_name": "pa-440.lab",
                              "routing_instance": "vr-lab", "vsys": "vsys2",
                              "forwarding_path": "syslog"}).status_code == 200
    r = _put(cred_client, {"host": "192.0.2.1"})
    assert r.status_code == 200
    cfg = cred_client.get("/api/credentials/integrations/fw1").json()["config"]
    assert cfg["device_name"] == "pa-440.lab"
    assert cfg["routing_instance"] == "vr-lab"
    assert cfg["vsys"] == "vsys2"
    assert cfg["forwarding_path"] == "syslog"


def test_re_put_device_name_and_routing_instance_survive_a_host_change(cred_client):
    assert _put(cred_client, {"host": "192.0.2.1", "device_name": "pa-440.lab",
                              "routing_instance": "vr-lab"}).status_code == 200
    assert _put(cred_client, {"host": "192.0.2.99"}).status_code == 200
    cfg = cred_client.get("/api/credentials/integrations/fw1").json()["config"]
    assert cfg["device_name"] == "pa-440.lab" and cfg["routing_instance"] == "vr-lab"


def test_re_put_sent_vsys_and_path_win_over_stored(cred_client):
    assert _put(cred_client, {"host": "192.0.2.1", "vsys": "vsys2",
                              "forwarding_path": "syslog"}).status_code == 200
    assert _put(cred_client, {"host": "192.0.2.1", "vsys": "vsys1",
                              "forwarding_path": "auto"}).status_code == 200
    cfg = cred_client.get("/api/credentials/integrations/fw1").json()["config"]
    assert cfg["vsys"] == "vsys1" and cfg["forwarding_path"] == "auto"


def test_panos_put_onto_another_kind_is_409_and_leaves_the_row(cred_client):
    other = {"name": "fw1", "kind": "generic_thing", "plaintext_secret": "S" * 20,
             "config": {"a": 1}}
    assert cred_client.put("/api/credentials/integrations", json=other).status_code == 200
    r = _put(cred_client, {"host": "192.0.2.1"})
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["code"] == "INTEGRATION_KIND_CONFLICT"
    assert d["error"] and "generic_thing" in d["detail"]
    got = cred_client.get("/api/credentials/integrations/fw1").json()
    assert got["kind"] == "generic_thing" and got["config"] == {"a": 1}
