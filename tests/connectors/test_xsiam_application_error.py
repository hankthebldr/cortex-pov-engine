"""XSIAM signals application errors INSIDE a 200. Those are not zero alerts.

`{"reply": {"err_code": 401, "err_msg": "..."}}` arrives with HTTP 200 and no
`alerts` key, so `reply.get("alerts", reply.get("data", []))` returned `[]` —
indistinguishable from a successful query that observed nothing. A permissions
problem, a wrong tenant or an expired key therefore became "the customer's stack
detected nothing", which is the manufactured false negative in its most damaging
form: it is the number that goes in the report.

The connector must RAISE so the caller degrades to `pending`.
"""
from __future__ import annotations

import json

import pytest

from connectors.base import ConnectorError
from connectors.xsiam import XsiamConnector


def _conn():
    return XsiamConnector.__new__(XsiamConnector)


@pytest.mark.parametrize("body", [
    {"reply": {"err_code": 401, "err_msg": "Unauthorized"}},
    {"reply": {"err_code": 403, "err_msg": "forbidden: missing scope"}},
    {"reply": {"err_msg": "internal error"}},
    {"err_code": 500, "err_msg": "server error"},
])
def test_an_application_error_raises_rather_than_reading_as_zero_alerts(body):
    with pytest.raises(ConnectorError) as exc:
        _conn()._parse_alerts(json.dumps(body))
    msg = str(exc.value)
    assert "XSIAM_APPLICATION_ERROR" in msg
    # It must say plainly that this is not an absence of alerts, because that is
    # the misreading the whole guard exists to prevent.
    assert "NOT an absence of alerts" in msg


def test_a_dict_where_a_list_belongs_is_refused_not_counted():
    """`len({"data": []}) == 1` is how a zero-row answer became one row."""
    with pytest.raises(ConnectorError) as exc:
        _conn()._parse_alerts(json.dumps({"reply": {"alerts": {"data": []}}}))
    assert "XSIAM_ENVELOPE_UNRECOGNISED" in str(exc.value)


def test_a_genuine_empty_result_is_still_zero_alerts():
    """The guard must not turn a real 'nothing fired' into an error — that would
    be the opposite failure, hiding a true detection miss behind an exception."""
    alerts, dropped = _conn()._parse_alerts(json.dumps({"reply": {"alerts": []}}))
    assert alerts == [] and dropped == 0
