"""F-03 — one timestamp coercer, correct under a non-UTC host clock.

These tests set TZ explicitly. That is not decoration: the local-time bug was
invisible under UTC, the Docker image runs UTC, and that is exactly why it
survived to production. A test file for F-03 that does not set TZ proves
nothing.
"""
from __future__ import annotations

import importlib
import os
import time
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def in_tz():
    """Run a callable with the process clock pinned to a given zone."""
    original = os.environ.get("TZ")

    def _run(tz_name, fn):
        os.environ["TZ"] = tz_name
        time.tzset()
        try:
            return fn()
        finally:
            if original is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original
            time.tzset()

    return _run


# Truth: 2026-06-10T12:00:30+02:00 is 10:00:30 UTC.
_OFFSET_ISO = "2026-06-10T12:00:30+02:00"
_TRUE_UTC = datetime(2026, 6, 10, 10, 0, 30)


@pytest.mark.parametrize("tz", ["UTC", "America/Los_Angeles", "Asia/Tokyo"])
def test_offset_is_converted_to_utc_not_stripped_and_not_localised(in_tz, tz):
    """Three answers were possible; only one is right.

    * ``astimezone(tz=None)`` (the old base.py) gave 03:00:30 in LA — SEVEN
      HOURS BEFORE ``executed_at``, so the matcher window never contained the
      observation and coverage read 0 % with no error anywhere.
    * ``replace(tzinfo=None)`` (the old xsiam.py) gave 12:00:30 — a 2 h MTTD
      error, in the direction that fails a threshold.
    """
    from connectors.base import coerce_utc

    got = in_tz(tz, lambda: coerce_utc(_OFFSET_ISO))
    assert got == _TRUE_UTC, f"under TZ={tz}"


@pytest.mark.parametrize("tz", ["UTC", "America/Los_Angeles", "Asia/Tokyo"])
def test_epoch_millis_are_utc_under_any_host_timezone(in_tz, tz):
    from connectors.base import coerce_utc

    # 2024-06-01T00:00:00Z
    got = in_tz(tz, lambda: coerce_utc(1717200000000))
    assert got == datetime(2024, 6, 1, 0, 0, 0), f"under TZ={tz}"


@pytest.mark.parametrize("tz", ["UTC", "Asia/Tokyo"])
def test_epoch_seconds_are_utc_under_any_host_timezone(in_tz, tz):
    from connectors.base import coerce_utc

    assert in_tz(tz, lambda: coerce_utc(1717200000)) == datetime(2024, 6, 1, 0, 0, 0)


def test_z_suffix_and_naive_forms():
    from connectors.base import coerce_utc

    assert coerce_utc("2026-06-10T10:00:30Z") == _TRUE_UTC
    assert coerce_utc("2026-06-10T10:00:30") == _TRUE_UTC
    assert coerce_utc(datetime(2026, 6, 10, 12, 0, 30, tzinfo=timezone(timedelta(hours=2)))) == _TRUE_UTC


def test_quoted_epoch_millis_are_not_read_as_iso():
    """A tenant export that quotes its timestamps used to fall through to
    utcnow(). Numeric strings are epochs."""
    from connectors.base import coerce_utc

    assert coerce_utc("1717200000000") == datetime(2024, 6, 1, 0, 0, 0)


@pytest.mark.parametrize("bad", ["2026/06/10 12:00:30", "n/a", "", None, {}, True])
def test_an_unreadable_timestamp_is_none_never_now(bad):
    """The old fallback was ``datetime.utcnow()``. That made
    ``mttd_seconds = now - executed_at`` — minutes or hours — which score_run
    compared against a ``<= 300s`` threshold and returned **fail**. A schema
    change in Cortex was reported to the customer as their detection being slow.
    """
    from connectors.base import coerce_utc

    assert coerce_utc(bad) is None


def test_from_dict_drops_an_undateable_observation_and_from_dicts_counts_it():
    from connectors.base import ObservedAlert

    assert ObservedAlert.from_dict({"observed_at": "n/a", "name": "x"}) is None

    alerts, dropped = ObservedAlert.from_dicts([
        {"observed_at": "2026-06-10T10:00:30Z", "name": "good"},
        {"observed_at": "not-a-time", "name": "bad"},
        {"name": "no-timestamp-at-all"},
    ])
    assert [a.name for a in alerts] == ["good"]
    assert dropped == 2, "a dropped observation must be counted, not absorbed"


def test_connector_drops_undateable_alerts_rather_than_dating_them_to_now():
    """The tenant-side half of the same rule."""
    from connectors.base import ConnectorConfig
    from connectors.xsiam import XsiamConnector

    body = {"reply": {"alerts": [
        {"alert_id": "1", "detection_timestamp": 1717200000000, "name": "good"},
        {"alert_id": "2", "detection_timestamp": "who knows", "name": "bad"},
    ]}}

    import json as _json

    def fetcher(method, url, headers, body_bytes, timeout):
        return 200, _json.dumps(body)

    pull = XsiamConnector(fetcher=fetcher).pull(
        ConnectorConfig("t", {"fqdn": "api-t.xdr.us.paloaltonetworks.com",
                              "api_key_id": "1"}, "k"),
        since=datetime(2024, 1, 1), until=datetime(2030, 1, 1))
    assert pull.ok
    assert [o.external_id for o in pull.observations] == ["1"]
    assert pull.dropped == 1
    assert pull.detail["unparseable_timestamps"] == 1


def test_exactly_one_coercer_exists():
    """PART 7 item 6. Two coercers is how nine hours went missing."""
    import pathlib

    core = pathlib.Path(__file__).resolve().parents[2] / "core"
    for banned in ("_ms_to_dt", "_coerce_dt"):
        hits = [str(p) for p in core.rglob("*.py")
                if "__pycache__" not in str(p) and f"def {banned}" in p.read_text()]
        assert hits == [], f"{banned} is back in {hits}; use connectors.base.coerce_utc"


def test_coerce_utc_is_importable_from_the_one_place_only():
    base = importlib.import_module("connectors.base")
    xsiam = importlib.import_module("connectors.xsiam")
    assert xsiam.coerce_utc is base.coerce_utc
