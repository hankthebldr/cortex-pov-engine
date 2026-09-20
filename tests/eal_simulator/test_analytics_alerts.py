"""Tests for the vendor analytics-alert profile registry and the detector
readiness gate.

Why this file exists, in one sentence: the vendor documents *preconditions*
(activation / training / test / dedup periods and a Required Data OR-set) but
does NOT document predicates or thresholds, so the only honest thing CortexSim
can assert about an analytics detector is whether it could possibly be ARMED —
and a detector that is not armed produces silence that a POV report would
otherwise read as "Cortex missed it".

Three rules bind this module, all from CLAUDE.md:

  * **Documented is not derived.** Activation and training periods are
    transcribed verbatim; the composition of the two into a single gate is OUR
    inference and is labelled as such. The two must never be reported as one.
  * **Unknown is degraded, not ok.** An operator who has not declared when the
    tenant began carrying a data source gets ``unknown`` — never ``armed``.
  * **A typo must raise, not vanish.** An unknown alert slug, or a detector
    bound to an alert whose Required Data does not include the emitter's own
    data source, raises. The tolerant alternative greens the test and keeps the
    bug.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from eal_simulator.analytics_alerts import (
    ALERT_PROFILES,
    AlertProfile,
    UnknownAlertError,
    get_alert,
    readiness_for,
)
from eal_simulator.analytics_catalogue import get_source


UTC_NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


class TestRegistry:
    def test_every_profile_required_data_resolves_in_the_catalogue(self):
        # A Required Data key that is not a real catalogue source would make the
        # emitter-to-alert join silently unsatisfiable.
        for profile in ALERT_PROFILES:
            assert profile.required_data, f"{profile.slug} declares no Required Data"
            for key in profile.required_data:
                get_source(key)  # raises UnknownDataSourceError on a typo

    def test_unknown_slug_raises_rather_than_returning_none(self):
        with pytest.raises(UnknownAlertError) as exc:
            get_alert("port-scan-detected")  # our old invented name
        assert "port-scan-detected" in str(exc.value)

    def test_slugs_are_unique(self):
        slugs = [p.slug for p in ALERT_PROFILES]
        assert len(slugs) == len(set(slugs))

    def test_port_scan_profile_is_transcribed_verbatim(self):
        p = get_alert("port-scan")
        assert p.name == "Port Scan"
        assert p.severity == "Informational"
        assert p.activation_days == 14
        assert p.training_days == 30
        assert p.test_period == "1 Hour"
        assert p.dedup_period == "1 Day"
        assert set(p.required_data) == {
            "pan_firewall_traffic_logs",
            "third_party_firewalls",
        }
        assert p.doc_url.endswith("/alerts-by-name/port-scan.md")

    def test_profiles_carry_provenance(self):
        # A transcribed claim with no source and no date cannot be re-checked
        # when the vendor page changes.
        for profile in ALERT_PROFILES:
            assert profile.doc_url.startswith("https://")
            assert profile.transcribed_on


class TestReadiness:
    def _profile(self) -> AlertProfile:
        return get_alert("port-scan")

    def test_undeclared_onboarding_is_unknown_never_armed(self):
        r = readiness_for(self._profile(), "third_party_firewalls", None, now=UTC_NOW)
        assert r.state == "unknown"
        assert r.armed is False
        assert r.code == "DETECTOR_READINESS_UNKNOWN"

    def test_recently_onboarded_source_is_not_armed_and_says_how_long_is_left(self):
        onboarded = UTC_NOW - timedelta(days=10)
        r = readiness_for(self._profile(), "third_party_firewalls", onboarded, now=UTC_NOW)
        assert r.state == "not_armed"
        assert r.armed is False
        assert r.code == "DETECTOR_NOT_ARMED"
        # gate is max(activation=14, training=30) = 30; 30 - 10 = 20 days left
        assert r.days_of_data == 10
        assert r.days_remaining == 20

    def test_source_carried_longer_than_the_gate_is_armed(self):
        onboarded = UTC_NOW - timedelta(days=31)
        r = readiness_for(self._profile(), "third_party_firewalls", onboarded, now=UTC_NOW)
        assert r.state == "armed"
        assert r.armed is True
        assert r.days_remaining == 0

    def test_boundary_exactly_at_the_gate_is_armed(self):
        onboarded = UTC_NOW - timedelta(days=30)
        r = readiness_for(self._profile(), "third_party_firewalls", onboarded, now=UTC_NOW)
        assert r.state == "armed"

    def test_gate_keeps_documented_and_inferred_as_separate_fields(self):
        r = readiness_for(self._profile(), "third_party_firewalls", None, now=UTC_NOW)
        # Documented, quotable verbatim.
        assert r.activation_days == 14
        assert r.training_days == 30
        # Our composition of the two — labelled, never conflated with the above.
        assert r.gate_days == 30
        assert r.gate_basis == "inferred:max(activation,training)"

    def test_source_outside_the_alerts_required_data_raises(self):
        # Port Scan's Required Data is firewall traffic; an identity source
        # cannot satisfy it. Returning a soft verdict would let an emitter claim
        # an alert it can never fire.
        with pytest.raises(ValueError) as exc:
            readiness_for(self._profile(), "okta", None, now=UTC_NOW)
        assert "okta" in str(exc.value)
        assert "port-scan" in str(exc.value)

    def test_naive_onboarding_datetime_is_rejected_not_silently_assumed_utc(self):
        # MTTD-style clock bugs start here: a naive timestamp read as local time
        # shifts the gate by the DC's UTC offset.
        with pytest.raises(ValueError):
            readiness_for(
                self._profile(),
                "third_party_firewalls",
                datetime(2026, 8, 1, 0, 0),  # naive
                now=UTC_NOW,
            )
