"""The alert-query time window is built in UTC, whatever the host's zone.

``coerce_utc`` fixed the PARSE side of the host-local leak (an alert's offset
converted to host-local time). The REQUEST side had the same leak:
``_build_request`` did ``naive.timestamp()``, which Python reads as LOCAL
time, while every ``since``/``until`` the service hands it is naive UTC
(``Result.executed_at`` / ``datetime.utcnow()``). On a host-run SimCore in
UTC-5 the ``creation_time`` window moved five hours into the future, the run's
alerts fell before ``since``, the tenant returned nothing, and coverage read
0 % with no error. The Docker image runs UTC, which is why nothing caught it.

These tests force a non-UTC zone so they fail on any host, CI included.
"""
from __future__ import annotations

import calendar
import json
import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from connectors.base import ConnectorConfig
from connectors.xsiam import XsiamConnector

pytestmark = pytest.mark.skipif(not hasattr(time, "tzset"), reason="needs time.tzset")


@pytest.fixture(params=["America/Los_Angeles", "Asia/Kolkata"])
def non_utc_host(request, monkeypatch):
    monkeypatch.setenv("TZ", request.param)
    time.tzset()
    assert time.timezone != 0 or time.altzone != 0, "zone did not take effect"
    yield request.param
    monkeypatch.undo()
    time.tzset()


def _epoch_ms(naive_utc: datetime) -> int:
    return calendar.timegm(naive_utc.timetuple()) * 1000


def _window(payload):
    flt = {(f["field"], f["operator"]): f["value"] for f in payload["request_data"]["filters"]}
    return flt[("creation_time", "gte")], flt[("creation_time", "lte")]


def test_naive_utc_window_is_not_shifted_by_the_host_zone(non_utc_host):
    since = datetime(2026, 6, 10, 10, 0, 0)
    until = datetime(2026, 6, 10, 11, 0, 0)
    gte, lte = _window(XsiamConnector()._build_request(since, until, {}))
    assert (gte, lte) == (_epoch_ms(since), _epoch_ms(until)), (
        f"host zone {non_utc_host} shifted the window by "
        f"{(gte - _epoch_ms(since)) / 3_600_000:+.2f} h"
    )


def test_aware_window_is_converted_not_stripped(non_utc_host):
    since = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    until = since + timedelta(hours=1)
    gte, lte = _window(XsiamConnector()._build_request(since, until, {}))
    assert gte == _epoch_ms(datetime(2026, 6, 10, 10, 0, 0))
    assert lte == _epoch_ms(datetime(2026, 6, 10, 11, 0, 0))


def test_pull_asks_the_tenant_for_the_run_window(non_utc_host):
    """End to end through ``pull``: the request the tenant sees brackets the run."""
    executed_at = datetime.utcnow() - timedelta(minutes=10)
    seen = {}

    def fetcher(method, url, headers, body, timeout):
        seen["window"] = _window(json.loads(body))
        return 200, json.dumps({"reply": {"total_count": 0, "result_count": 0,
                                          "alerts": []}})

    cfg = ConnectorConfig(integration_name="t", secret="k" * 40,
                          config={"fqdn": "api-t.xdr.us.paloaltonetworks.com",
                                  "api_key_id": "1"})
    XsiamConnector(fetcher=fetcher).pull(
        cfg, since=executed_at, until=datetime.utcnow() + timedelta(minutes=5))

    alert_ms = _epoch_ms(executed_at + timedelta(seconds=45))
    gte, lte = seen["window"]
    assert gte <= alert_ms <= lte, (
        f"an alert 45 s after execution falls outside the requested window "
        f"on a {os.environ.get('TZ')} host"
    )
