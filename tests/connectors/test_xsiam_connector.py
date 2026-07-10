"""Unit tests for the XSIAM connector with an injected (offline) HTTP fetcher."""
from __future__ import annotations

import json
from datetime import datetime

from connectors import get_connector
from connectors.base import ConnectorConfig
from connectors.xsiam import XsiamConnector

SINCE = datetime(2026, 6, 10, 12, 0, 0)
UNTIL = datetime(2026, 6, 10, 13, 0, 0)


def _cfg(**overrides):
    config = {"fqdn": "api-tenant.xdr.us.paloaltonetworks.com",
              "api_key_id": "42", "auth_mode": "standard"}
    config.update(overrides.pop("config", {}))
    return ConnectorConfig(integration_name="xsiam-prod",
                           config=config, secret="SECRET_KEY", **overrides)


def _tenant_reply(alerts):
    return 200, json.dumps({"reply": {"total_count": len(alerts), "alerts": alerts}})


def test_registered():
    conn = get_connector("xsiam")
    assert isinstance(conn, XsiamConnector)


def test_pull_normalizes_alerts():
    captured = {}

    def fetcher(method, url, headers, body, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json.loads(body)
        return _tenant_reply([
            {
                "alert_id": "1001",
                "name": "LSASS Memory Access",
                "severity": "SEV_040_HIGH",
                "detection_timestamp": int(datetime(2026, 6, 10, 12, 0, 30).timestamp() * 1000),
                "host_name": "win-ws-01",
                "mitre_technique_ids": ["T1003.001"],
            },
        ])

    conn = XsiamConnector(fetcher=fetcher)
    result = conn.pull(_cfg(), since=SINCE, until=UNTIL)

    assert result.ok, result.error
    assert len(result.observations) == 1
    obs = result.observations[0]
    assert obs.external_id == "1001"
    assert obs.severity == "high"
    assert obs.techniques == ["T1003.001"]
    assert obs.host == "win-ws-01"
    # Auth headers present for standard mode.
    assert captured["headers"]["x-xdr-auth-id"] == "42"
    assert captured["headers"]["Authorization"] == "SECRET_KEY"
    # Request carries a creation_time window filter.
    filters = captured["body"]["request_data"]["filters"]
    assert any(f["field"] == "creation_time" for f in filters)


def test_advanced_auth_does_not_leak_key():
    captured = {}

    def fetcher(method, url, headers, body, timeout):
        captured["headers"] = headers
        return _tenant_reply([])

    conn = XsiamConnector(fetcher=fetcher)
    conn.pull(_cfg(config={"auth_mode": "advanced"}), since=SINCE, until=UNTIL)
    h = captured["headers"]
    # In advanced mode the raw key must NOT be the Authorization value.
    assert h["Authorization"] != "SECRET_KEY"
    assert len(h["Authorization"]) == 64  # sha256 hex
    assert "x-xdr-nonce" in h and "x-xdr-timestamp" in h


def test_missing_config_returns_error_not_raise():
    conn = XsiamConnector(fetcher=lambda *a: (200, "{}"))
    cfg = ConnectorConfig(integration_name="x", config={}, secret="k")
    result = conn.pull(cfg, since=SINCE, until=UNTIL)
    assert not result.ok
    assert "fqdn" in (result.error or "")


def test_transport_failure_is_offline_safe():
    def boom(*a):
        raise OSError("network down")

    conn = XsiamConnector(fetcher=boom)
    result = conn.pull(_cfg(), since=SINCE, until=UNTIL)
    assert not result.ok
    assert "network down" in (result.error or "")


def test_http_error_status_surfaced():
    conn = XsiamConnector(fetcher=lambda *a: (403, '{"err":"forbidden"}'))
    result = conn.pull(_cfg(), since=SINCE, until=UNTIL)
    assert not result.ok
    assert "403" in (result.error or "")


def test_technique_extracted_from_name_string():
    def fetcher(*a):
        return _tenant_reply([{
            "alert_id": "2", "name": "Kerberoast",
            "detection_timestamp": int(datetime(2026, 6, 10, 12, 5).timestamp() * 1000),
            "mitre_technique_id_and_name": ["T1558.003 - Kerberoasting"],
        }])

    conn = XsiamConnector(fetcher=fetcher)
    result = conn.pull(_cfg(), since=SINCE, until=UNTIL)
    assert result.observations[0].techniques == ["T1558.003"]
