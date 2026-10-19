"""An alert envelope carrying no alert list is unrecognised, not empty.

``_parse_alerts`` read ``reply.get("alerts", reply.get("data", []))``, so a 200
whose JSON body names neither key — ``{"reply": {}}``, a proxy's
``{"status": "login_required"}``, a ``reply`` that is a list or a string — was
read as ZERO alerts. The pull returned ``ok=True``, reconcile marked the
integration verified, preflight reported auth and alert-read scope OK with
"0 row(s)", and coverage read 0 %. "Tolerance hides bugs": an unrecognised
shape must raise, exactly as a dict-where-a-list-belongs already does.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from connectors.base import ConnectorConfig, ConnectorError
from connectors.xsiam import XsiamConnector


@pytest.mark.parametrize("body", [
    {"reply": {}},
    {"reply": {"total_count": 0, "result_count": 0}},
    {"status": "login_required"},
    {},
    {"reply": []},
    {"reply": "ok"},
    [],
])
def test_an_envelope_without_an_alert_list_raises(body):
    conn = XsiamConnector.__new__(XsiamConnector)
    with pytest.raises(ConnectorError) as exc:
        conn._parse_alerts(json.dumps(body))
    assert "XSIAM_ENVELOPE_UNRECOGNISED" in str(exc.value)


@pytest.mark.parametrize("body", [
    {"reply": {"total_count": 0, "result_count": 0, "alerts": []}},
    {"reply": {"data": []}},
    {"alerts": []},
])
def test_a_recognised_empty_envelope_is_still_zero_alerts(body):
    conn = XsiamConnector.__new__(XsiamConnector)
    alerts, dropped, _ = conn._parse_alerts(json.dumps(body))
    assert alerts == [] and dropped == 0


def test_pull_degrades_to_a_parse_error_not_ok_zero():
    cfg = ConnectorConfig(integration_name="t", secret="k" * 40,
                          config={"fqdn": "api-t.xdr.us.paloaltonetworks.com",
                                  "api_key_id": "1"})
    pull = XsiamConnector(fetcher=lambda *a: (200, json.dumps({"reply": {}}))).pull(
        cfg, since=datetime.utcnow() - timedelta(hours=1), until=datetime.utcnow())
    assert pull.ok is False
    assert pull.code == "XSIAM_PARSE_ERROR"
    assert pull.observations == []
