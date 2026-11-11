"""The SOCFW_TRIAGE_MAY_SEAL launch advisory (pure; no tenant, no DB)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from engine.socfw_advisory import (
    SOCFW_RESPONSE_LAYER_UNKNOWN, SOCFW_TRIAGE_MAY_SEAL, STALE_AFTER, triage_advisories)

NOW = datetime(2026, 11, 11, 12, 0, 0)


def _layer(**at):
    auto = {"live": True, "window_hours": 6, "score_threshold": 30,
            "score_off": False, "live_source": "xql_evidence",
            "window_source": "tenant_config", "threshold_source": "tenant_config"}
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
    # Pinned to the framework at d222ba87: SOCFWConfig's triage command takes
    # score_threshold / window_hours / apply and dry-runs unless apply=yes
    # (SOCFWConfig.yml:16-17,165,169,216); Auto Triage's own dry run is the
    # dry_run argument of SOCAutoTriageScoreFilter, the JOB's close task
    # (SOCAutoTriageScoreFilter.yml:65-70). Auto Triage skips starred cases.
    r = _run()[0]["remediation"]
    assert "star the POV's cases (Auto Triage skips starred cases)" in r
    assert "!SOCFWConfig command=triage window_hours=<n> apply=yes" in r
    assert "dry_run=true" in r and "JOB - Auto Triage V3" in r
    assert "command=triage (dry_run)" not in r  # an option that does not exist
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


# ── I-1: PRESENT_UNREAD with XQL-proven live triage, level of UNKNOWN, never-probed ──

def _unread_live(**at):
    auto = {"live": True, "window_hours": None, "score_threshold": None,
            "score_off": False}
    auto.update(at)
    return {"state": "PRESENT_UNREAD", "auto_triage": auto}


def test_present_unread_with_xql_proven_live_on_paired_run_warns():
    # Production auto_triage_closed rows prove triage live even when the lists
    # cannot be read (spec 4.1); the advisory must fire, not say "run preflight".
    out = _run(layer=_unread_live())
    assert [(a["code"], a["level"]) for a in out] == [(SOCFW_TRIAGE_MAY_SEAL, "warn")]
    d = out[0]["detail"]
    assert "not read" in d and "None" not in d


def test_present_unread_live_on_late_scenario_is_info():
    out = _run(types=("ABIOC",), paired=False, layer=_unread_live())
    assert [(a["code"], a["level"]) for a in out] == [(SOCFW_TRIAGE_MAY_SEAL, "info")]


def test_unknown_takes_the_trigger_level():
    old = NOW - STALE_AFTER - timedelta(minutes=1)
    assert [(a["code"], a["level"]) for a in _run(read_at=old)] == \
        [(SOCFW_RESPONSE_LAYER_UNKNOWN, "warn")]
    assert [(a["code"], a["level"]) for a in _run(types=("Analytics",), paired=False,
                                                  read_at=old)] == \
        [(SOCFW_RESPONSE_LAYER_UNKNOWN, "info")]


def test_never_probed_tenant_says_no_probe_stored_not_older_than_24h():
    d = _run(layer=None, read_at=None)[0]["detail"]
    assert "no SOC Framework probe is stored for this tenant — run preflight" in d
    assert "older than 24 hours" not in d


def test_liveness_undetermined_does_not_promise_preflight_will_settle_it():
    for st in ("PRESENT", "PRESENT_UNREAD"):
        out = _run(layer={"state": st, "auto_triage": {"live": None}})
        assert out[0]["code"] == SOCFW_RESPONSE_LAYER_UNKNOWN
        assert "possibly live" in out[0]["detail"]


# ── I-3: the advisory names values it did not read ──

def test_may_seal_detail_says_which_values_were_not_read():
    out = _run(layer=_layer(live_source="mode_inferred", window_source="shipped_default",
                            threshold_source="shipped_default", score_threshold=40))
    d = out[0]["detail"]
    assert "inferred from mode, not read" in d
    assert d.count("shipped default, not read") == 2


def test_may_seal_detail_read_values_carry_no_caveat():
    d = _run()[0]["detail"]
    assert "not read" not in d and "6 h" in d and "<= 30" in d
