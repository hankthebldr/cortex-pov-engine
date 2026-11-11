"""Alert read-back understands the field names the public XSIAM Issues API documents.

The read-back keys predate the docs' issues/cases model. The Issues API object
(https://cortex-docs.paloaltonetworks.com/xsiam-api/issues-apis/issues, response
of ``POST /public_api/v1/issue/search``) carries:

* ``detection.method`` — "Detection source or method that created the issue",
  an enum of codes (``TRAPS``, ``MAGNIFIER``, ``ANALYTICS_BIOC``, ``FW``,
  ``BIOC``, ``IOC``, ``THREAT_INTELLIGENCE``, ``MTH``, ``CORRELATION``, ...);
* ``detection.rule_id`` — the rule that raised it;
* ``case_ids`` — "List of case IDs this issue is associated with";
* ``_insert_time`` — "Issue creation timestamp in epoch milliseconds".

The schema writes ``detection.*`` as flat dotted property names; a nested
``{"detection": {...}}`` object is read too. Before this change:

* ``signal_family("ANALYTICS_BIOC")`` was ``analytics`` (the underscore defeated
  the "analytics bioc" check), so an ABIOC expectation could never match it;
* ``TRAPS`` / ``MAGNIFIER`` named no family at all;
* none of the four keys above was read, so an Issues-shaped record had no rule
  id, no source, no case and — with no other timestamp — was dropped;
* preflight graded such a record ``PF_ALERT_SHAPE_PARTIAL``.

All transports are injected. Nothing here touches a tenant; tenant-verified is 0.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from connectors import preflight as pf
from connectors.base import ObservedAlert
from connectors.matcher import reconcile, signal_family
from connectors.xsiam import XsiamConnector

T0 = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)
T0_MS = int(T0.timestamp() * 1000)
T0_NAIVE = T0.replace(tzinfo=None)


def _conn() -> XsiamConnector:
    return XsiamConnector(fetcher=lambda *a, **k: (200, json.dumps({})))


def _normalize(record: dict):
    return _conn()._normalize_alert(record)


# ── 1. matcher: Issues API detection.method codes ────────────────────────────

@pytest.mark.parametrize("code, family", [
    ("ANALYTICS_BIOC", "abioc"),
    ("BIOC", "bioc"),
    ("IOC", "ioc"),
    ("CORRELATION", "correlation"),
    ("MAGNIFIER", "analytics"),
    ("TRAPS", "prevention"),
])
def test_issues_method_codes_map_onto_families(code, family):
    assert signal_family(code) == family


@pytest.mark.parametrize("value, family", [
    ("XDR Agent", "prevention"),
    ("Prevention", "prevention"),
    ("XDR Analytics BIOC", "abioc"),
    ("ABIOC", "abioc"),
    ("XDR Analytics", "analytics"),
    ("XDR BIOC", "bioc"),
    ("XDR IOC", "ioc"),
    ("Correlation", "correlation"),
    ("XQL", "correlation"),
])
def test_human_readable_sources_still_map(value, family):
    assert signal_family(value) == family


@pytest.mark.parametrize("code", [
    "FW", "THREAT_INTELLIGENCE", "MTH", "PRISMA_CLOUD", "PRISMA_CLOUD_COMPUTE",
    "XPANSE", "IOT", "CREATE_ALERT_PUBLIC_API", "HEALTH", "EMAIL_ATTACHMENT",
    "ATTACK_PATH", "CAS_IAC_SCANNER", "CAS_SECRET_SCANNER", "CIEM_SCANNER",
])
def test_other_documented_codes_stay_unconstrained(code):
    # None is "unconstrained", never a disagreement. A substring collision here
    # would turn an unmapped engine into a known-but-different family and block
    # a real technique/name match.
    assert signal_family(code) is None


class _R:
    def __init__(self, rid, *, signal_type, technique="T1003.001"):
        self.id = rid
        self.mitre_technique = technique
        self.detection_id = None
        self.expected_detection = "LSASS memory access"
        self.signal_type = signal_type
        self.executed_at = T0
        self.observed = False


def _alert(source: str) -> ObservedAlert:
    return ObservedAlert(source="xsiam", observed_at=T0 + timedelta(seconds=30),
                         external_id="I1", techniques=["T1003.001"], alert_source=source)


def test_abioc_expectation_matches_an_analytics_bioc_issue():
    v = reconcile([_R(1, signal_type="ABIOC")], [_alert("ANALYTICS_BIOC")])
    assert v[0].matched
    assert "source" in v[0].matched_on


def test_traps_issue_cannot_credit_a_bioc_expectation():
    # The agent's own prevention module is not a rule family.
    v = reconcile([_R(1, signal_type="BIOC")], [_alert("TRAPS")])
    assert not v[0].matched


def test_magnifier_issue_cannot_credit_a_bioc_expectation():
    v = reconcile([_R(1, signal_type="BIOC")], [_alert("MAGNIFIER")])
    assert not v[0].matched


# ── 2. connector: Issues API keys on the record ──────────────────────────────

def test_rule_id_is_read_from_the_flat_dotted_key():
    a = _normalize({"id": 7, "detection_timestamp": T0_MS, "detection.rule_id": "4012"})
    assert a is not None and a.detection_id == "4012"


def test_rule_id_is_read_from_a_nested_detection_object():
    a = _normalize({"id": 7, "detection_timestamp": T0_MS, "detection": {"rule_id": "4012"}})
    assert a is not None and a.detection_id == "4012"


def test_method_becomes_the_alert_source_flat_and_nested():
    flat = _normalize({"detection_timestamp": T0_MS, "detection.method": "ANALYTICS_BIOC"})
    nested = _normalize({"detection_timestamp": T0_MS, "detection": {"method": "TRAPS"}})
    assert flat.alert_source == "ANALYTICS_BIOC"
    assert nested.alert_source == "TRAPS"


def test_alerts_api_keys_keep_precedence_over_issue_keys():
    a = _normalize({"detection_timestamp": T0_MS,
                    "matching_service_rule_id": "bioc-12", "detection.rule_id": "4012",
                    "source": "XDR BIOC", "detection.method": "CORRELATION"})
    assert a.detection_id == "bioc-12"
    assert a.alert_source == "XDR BIOC"


def test_a_single_case_id_becomes_the_incident_id():
    a = _normalize({"detection_timestamp": T0_MS, "case_ids": [834]})
    assert a.incident_id == "834"


@pytest.mark.parametrize("case_ids", [[3, 42], [], None, "834"])
def test_several_or_no_case_ids_never_pick_one(case_ids):
    # Taking case_ids[0] of several would credit the alert to an arbitrary case
    # and feed that into the correlation-rate basis.
    a = _normalize({"detection_timestamp": T0_MS, "case_ids": case_ids})
    assert a.incident_id is None


def test_incident_id_keeps_precedence_over_case_ids():
    a = _normalize({"detection_timestamp": T0_MS, "incident_id": "9", "case_ids": [834]})
    assert a.incident_id == "9"


def test_insert_time_dates_an_issue_with_no_other_timestamp():
    # Before: an Issues-shaped record had no timestamp the connector read, so
    # it was dropped as unreadable and could never be evidence for anything.
    a = _normalize({"id": 7, "_insert_time": T0_MS})
    assert a is not None and a.observed_at == T0_NAIVE


def test_insert_time_never_outranks_the_existing_timestamps():
    # Which timestamp MTTD uses is open (PM-ALR-02); this change must not move it.
    later = T0_MS + 120_000
    for key in ("detection_timestamp", "creation_time", "local_insert_ts"):
        a = _normalize({key: T0_MS, "_insert_time": later})
        assert a.observed_at == T0_NAIVE, key


def test_a_documented_issue_record_normalizes_end_to_end():
    record = {  # shape of the docs' response example, values ours
        "_insert_time": T0_MS, "id": 12345, "external_id": "abc-12345-def",
        "name": "LSASS Memory Access", "severity": "HIGH",
        "detection.method": "BIOC", "detection.rule_id": "4012",
        "case_ids": [834], "observation_time": T0_MS,
    }
    a = _normalize(record)
    assert a.observed_at == T0_NAIVE
    assert (a.detection_id, a.alert_source, a.incident_id) == ("4012", "BIOC", "834")
    assert signal_family(a.alert_source) == "bioc"


# ── 3. preflight: the alert_shape rung reads the same keys ───────────────────

_CFG = {"fqdn": "api-t.xdr.us.paloaltonetworks.com", "api_key_id": "1"}

_ISSUE_BASE = {"id": 1, "name": "LSASS Memory Access", "_insert_time": 1780000000000,
               "host_name": "web-01", "mitre_technique_id_and_name": ["T1003.001 - LSASS"],
               "action_pretty": "Detected (Reported)"}


def _stage_for(alert: dict):
    def fetcher(method, url, headers, body, timeout):
        return 200, json.dumps({"reply": {"total_count": 1, "alerts": [alert]}})
    r = pf.preflight_reconcile(_CFG, "k", fetcher=fetcher)
    return next(s for s in r.stages if s.id == "alert_shape")


@pytest.mark.parametrize("extra", [
    {"detection.rule_id": "4012"},
    {"detection": {"rule_id": "4012"}},
])
def test_an_issue_rule_id_reads_ok_not_partial(extra):
    s = _stage_for({**_ISSUE_BASE, **extra})
    assert s.status == pf.OK and s.code == pf.PF_OK
    assert s.extra["keys"]["rule_id"] == "detection.rule_id"
    assert s.extra["keys"]["timestamp"] == "_insert_time"


def test_preflight_and_connector_agree_on_case_ids():
    one = _stage_for({**_ISSUE_BASE, "detection.rule_id": "4", "case_ids": [834]})
    many = _stage_for({**_ISSUE_BASE, "detection.rule_id": "4", "case_ids": [3, 42]})
    assert one.extra["keys"]["incident"] == "case_ids"
    assert one.extra["correlation_basis"] == "alert_incident_ids"
    assert many.extra["keys"]["incident"] is None
    assert many.extra["correlation_basis"] == "host_incidents"


# ── 4. lock-step: every key preflight credits, the connector actually reads ──

#: A value per key that the connector should turn into a populated field.
_SAMPLE = {
    "mitre_technique_ids": ["T1003.001"], "mitre_technique_id_and_name": ["T1003.001 - x"],
    "mitre_techniques": ["T1003.001"], "hosts": ["web-01"], "case_ids": [834],
}

#: Role → what the normalised alert must carry when only that role's key is set.
_ROLE_FIELD = {
    "timestamp": lambda a: a.observed_at,
    "technique": lambda a: a.techniques,
    "name": lambda a: a.name,
    "host": lambda a: a.host,
    "source": lambda a: a.alert_source,
    "rule_id": lambda a: a.detection_id,
    "action": lambda a: a.action_pretty or a.action,
    "incident": lambda a: a.incident_id,
}


@pytest.mark.parametrize("role, key", [
    (role, key) for role, keys in pf.ALERT_SHAPE_KEYS.items() for key in keys
])
def test_every_preflight_key_is_read_by_the_connector(role, key):
    # A preflight that credits a key the connector ignores green-lights a
    # tenant whose alerts then match on nothing — the drift item 3 fixed.
    record = {} if role == "timestamp" else {"detection_timestamp": T0_MS}
    record[key] = _SAMPLE.get(key, T0_MS if role == "timestamp" else "v-1")
    a = _normalize(record)
    assert a is not None, f"{key} left the record undated"
    assert _ROLE_FIELD[role](a), f"preflight credits {key!r} for {role}; the connector ignores it"
    assert set(_ROLE_FIELD) == set(pf.ALERT_SHAPE_KEYS)
