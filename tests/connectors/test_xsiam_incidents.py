"""Sprint 2b/2d — incidents read, alert incident ids, configurable paths."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from connectors.base import ConnectorConfig
from connectors.xsiam import XsiamConnector

T0 = datetime(2026, 6, 10, 12, 0, 0)
T1 = T0 + timedelta(hours=1)


def _cfg(**config):
    base = {"fqdn": "api-t.xdr.us.paloaltonetworks.com", "api_key_id": "1"}
    base.update(config)
    return ConnectorConfig(integration_name="x", config=base, secret="k")


def test_pull_incidents_parses_rows_and_hosts():
    calls = []

    def fetcher(method, url, headers, body, timeout):
        calls.append((url, json.loads(body)["request_data"]))
        return 200, json.dumps({"reply": {"total_count": 2, "result_count": 2, "incidents": [
            {"incident_id": "104", "incident_name": "Credential dumping on web-01",
             "creation_time": int((T0 + timedelta(minutes=5)).timestamp() * 1000),
             "alert_count": 6, "hosts": ["web-01:aef3", "db-02:77aa"], "severity": "high"},
            {"incident_id": "105", "creation_time": int((T0 + timedelta(minutes=9)).timestamp() * 1000),
             "alert_count": 1, "hosts": []},
        ]}})

    r = XsiamConnector(fetcher=fetcher).pull_incidents(_cfg(), since=T0, until=T1)
    assert r.ok, r.error
    assert calls[0][0].endswith("/public_api/v1/incidents/get_incidents")
    assert [f["field"] for f in calls[0][1]["filters"]] == ["creation_time", "creation_time"]
    assert [i.incident_id for i in r.incidents] == ["104", "105"]
    assert r.incidents[0].alert_count == 6
    assert r.incidents[0].hosts == ["web-01", "db-02"]     # the ':<id>' suffix is stripped
    assert r.detail["truncated"] is False


def test_pull_incidents_application_error_inside_200_is_not_zero_incidents():
    def fetcher(*a):
        return 200, json.dumps({"reply": {"err_code": 500, "err_msg": "nope"}})

    r = XsiamConnector(fetcher=fetcher).pull_incidents(_cfg(), since=T0, until=T1)
    assert not r.ok and r.code == "XSIAM_PARSE_ERROR"
    assert r.incidents == []


def test_alert_incident_id_is_captured_when_the_tenant_puts_it_on_the_alert():
    def fetcher(method, url, headers, body, timeout):
        return 200, json.dumps({"reply": {"total_count": 2, "alerts": [
            {"alert_id": "1", "detection_timestamp": int(T0.timestamp() * 1000) + 1000, "incident_id": "104"},
            {"alert_id": "2", "detection_timestamp": int(T0.timestamp() * 1000) + 2000, "case_id": 105},
        ]}})

    r = XsiamConnector(fetcher=fetcher).pull(_cfg(), since=T0, until=T1)
    assert [a.incident_id for a in r.observations] == ["104", "105"]
    assert r.observations[0].to_dict()["incident_id"] == "104"


def test_api_paths_are_credential_config_not_class_constants():
    seen = []

    def fetcher(method, url, headers, body, timeout):
        seen.append(url)
        key = "incidents" if "incidents" in url else "alerts"
        return 200, json.dumps({"reply": {"total_count": 0, key: []}})

    conn = XsiamConnector(fetcher=fetcher)
    cfg = _cfg(alerts_path="/public_api/v2/alerts/get_alerts_multi_events",
               incidents_path="/public_api/v2/incidents/get_incidents")
    a = conn.pull(cfg, since=T0, until=T1)
    i = conn.pull_incidents(cfg, since=T0, until=T1)
    assert seen[0].endswith("/public_api/v2/alerts/get_alerts_multi_events")
    assert seen[1].endswith("/public_api/v2/incidents/get_incidents")
    assert a.detail["path"] == "/public_api/v2/alerts/get_alerts_multi_events"
    assert i.detail["path"] == "/public_api/v2/incidents/get_incidents"
    # and the default is still the v1 path
    d = conn.pull(_cfg(), since=T0, until=T1)
    assert d.detail["path"] == "/public_api/v1/alerts/get_alerts_multi_events"
