"""The SOCFW_TRIAGE_MAY_SEAL launch advisory (pure; no tenant, no DB)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from engine.socfw_advisory import (
    SOCFW_RESPONSE_LAYER_UNKNOWN, SOCFW_TRIAGE_MAY_SEAL, STALE_AFTER, triage_advisories)

NOW = datetime(2026, 11, 11, 12, 0, 0)


def _layer(**at):
    auto = {"live": True, "window_hours": 6, "score_threshold": 30,
            "score_off": False, "source": "tenant_config"}
    auto.update(at)
    return {"state": "PRESENT", "auto_triage": auto}


LIVE = _layer()


def _run(types=("BIOC",), paired=True, layer=LIVE, read_at=NOW, now=NOW):
    return triage_advisories(tenant="t", detection_types=set(types), paired=paired,
                             response_layer=layer, read_at=read_at, now=now)


def test_paired_run_with_live_triage_warns():
    out = _run()
    assert [(a["code"], a["level"]) for a in out] == [(SOCFW_TRIAGE_MAY_SEAL, "warn")]
    assert out[0]["tenant"] == "t"
    assert "6" in out[0]["detail"] and "30" in out[0]["detail"]


def test_abioc_or_analytics_scenario_is_info():
    for t in ("ABIOC", "Analytics"):
        out = _run(types=(t,), paired=False)
        assert [(a["code"], a["level"]) for a in out] == [(SOCFW_TRIAGE_MAY_SEAL, "info")]


def test_no_late_detection_and_not_paired_is_silent():
    assert _run(types=("BIOC",), paired=False) == []


def test_triage_not_live_is_silent():
    assert _run(layer=_layer(live=False)) == []


def test_absent_framework_is_silent():
    assert _run(layer={"state": "ABSENT"}) == []


def test_stale_layer_says_unknown_run_preflight():
    out = _run(read_at=NOW - STALE_AFTER - timedelta(minutes=1))
    assert out[0]["code"] == SOCFW_RESPONSE_LAYER_UNKNOWN and "preflight" in out[0]["detail"]


def test_never_probed_is_unknown():
    out = _run(layer=None, read_at=None)
    assert out[0]["code"] == SOCFW_RESPONSE_LAYER_UNKNOWN


def test_unread_or_unknown_state_is_unknown():
    for st in ("UNKNOWN", "PRESENT_UNREAD"):
        assert _run(layer={"state": st})[0]["code"] == SOCFW_RESPONSE_LAYER_UNKNOWN


def test_liveness_undetermined_is_unknown():
    assert _run(layer=_layer(live=None))[0]["code"] == SOCFW_RESPONSE_LAYER_UNKNOWN


def test_malformed_layer_degrades_never_raises():
    out = _run(layer={"state": "PRESENT", "auto_triage": "garbage"})
    assert out[0]["code"] == SOCFW_RESPONSE_LAYER_UNKNOWN
    out = _run(layer={"state": "PRESENT", "auto_triage": {"live": True}}, read_at="not a date")
    assert out[0]["code"] == SOCFW_RESPONSE_LAYER_UNKNOWN


def test_remediation_lists_three_tenant_side_options():
    r = _run()[0]["remediation"]
    assert "star" in r and "!SOCFWConfig command=triage" in r and "dry_run" in r
    assert "docs/lab/2026-11-11-run-alongside-soc-framework.md" in r


def test_score_off_advisory_says_every_aged_case():
    out = _run(layer=_layer(score_off=True, score_threshold=None))
    assert "every New unstarred case older than" in out[0]["detail"]


def test_aware_times_compare_the_same_as_naive():
    aware = NOW.replace(tzinfo=timezone.utc)
    assert [a["code"] for a in _run(read_at=aware, now=aware)] == [SOCFW_TRIAGE_MAY_SEAL]
    assert _run(read_at=aware - timedelta(hours=25), now=aware)[0]["code"] == \
        SOCFW_RESPONSE_LAYER_UNKNOWN


def test_stale_absent_is_unknown_not_silent():
    old = NOW - STALE_AFTER - timedelta(minutes=1)
    out = _run(layer={"state": "ABSENT"}, read_at=old)
    assert out[0]["code"] == SOCFW_RESPONSE_LAYER_UNKNOWN


def test_stale_triage_not_live_is_unknown_not_silent():
    old = NOW - STALE_AFTER - timedelta(minutes=1)
    out = _run(layer=_layer(live=False), read_at=old)
    assert out[0]["code"] == SOCFW_RESPONSE_LAYER_UNKNOWN


def test_fresh_absent_stays_silent():
    assert _run(layer={"state": "ABSENT"}, read_at=NOW - timedelta(hours=1)) == []
