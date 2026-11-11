"""Case-ledger classifier: did the chain stay in one native case, and if not, why."""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from connectors import case_ledger as cl
from connectors.base import ObservedAlert, ObservedIncident
from connectors.socfw import TRIAGE_RESOLVE_COMMENT_PREFIX

T0 = datetime(2026, 11, 11, 10, 0, 0)


def A(eid, minutes=0, sev="high", case=None, status=None, reason=None, comment=None):
    return cl.LedgerAlert(
        external_id=eid, observed_at=T0 + timedelta(minutes=minutes), severity=sev,
        case_id=case, issue_status=status, issue_reason=reason, issue_comment=comment,
    )


def C(cid, status="new", resolved_at=None, comment=None, count=3, starred=False,
      assigned=False, url=None):
    return cl.LedgerCase(
        case_id=cid, status=status, resolve_comment=comment, resolved_at=resolved_at,
        starred=starred, assigned=assigned, xdr_url=url, alert_count=count,
    )


def test_one_case():
    al = [A("1", 0, case="A"), A("2", 1, case="A"), A("3", 2, case="A")]
    assert cl.classify_merge(al, {"A": C("A")}, basis="alert_incident_ids")[0] == "ONE_CASE"


def test_low_only_no_case():
    al = [A("1", sev="low"), A("2", 1, sev="low")]
    assert cl.classify_merge(al, {}, basis="alert_incident_ids")[0] == "LOW_ONLY_NO_CASE"


def test_no_case_yet():
    al = [A("1"), A("2", 1)]
    assert cl.classify_merge(al, {}, basis="alert_incident_ids")[0] == "NO_CASE_YET"


def test_single_medium_is_pending():
    al = [A("1", sev="medium", case="A"), A("2", 1, sev="low")]
    assert cl.classify_merge(al, {"A": C("A")}, basis="alert_incident_ids")[0] == "PENDING"


def test_host_incidents_basis_is_pending():
    al = [A("1", case="A"), A("2", 1, case="A")]
    assert cl.classify_merge(al, {"A": C("A")}, basis="host_incidents")[0] == "PENDING"


def test_missing_case_is_pending_not_one_case():
    al = [A("1", case="A"), A("2", 1, case="A")]
    mc, reason = cl.classify_merge(al, {"B": C("B")}, basis="alert_incident_ids")
    assert mc == "PENDING"
    assert "case A" in reason


def test_split_native():
    al = [A("1", 0, case="A"), A("2", 5, case="B")]
    cases = {"A": C("A"), "B": C("B")}
    assert cl.classify_merge(al, cases, basis="alert_incident_ids")[0] == "SPLIT_NATIVE"


def _split(a_status, a_resolved_min):
    al = [A("1", 0, case="A"), A("2", 5, case="B")]
    cases = {"A": C("A", a_status, resolved_at=T0 + timedelta(minutes=a_resolved_min)),
             "B": C("B")}
    return cl.classify_merge(al, cases, basis="alert_incident_ids")


def test_split_sealed():
    mc, reason = _split("resolved_other", 0)
    assert mc == "SPLIT_SEALED"
    assert "A" in reason and "B" in reason


def test_auto_resolved_is_not_sealed():
    assert _split("resolved_auto_resolve", 0)[0] == "SPLIT_NATIVE"


def test_sealed_after_later_case_is_native():
    assert _split("resolved_other", 10)[0] == "SPLIT_NATIVE"


def test_split_sealed_timing_is_tz_independent():
    old = os.environ.get("TZ")
    os.environ["TZ"] = "America/Los_Angeles"
    time.tzset()
    try:
        first = datetime(2026, 11, 11, 10, 0, 0, tzinfo=timezone.utc)
        second = datetime(2026, 11, 11, 10, 5, 0, tzinfo=timezone.utc)
        resolved_ms = int(datetime(2026, 11, 11, 10, 1, 0, tzinfo=timezone.utc).timestamp() * 1000)
        inc = ObservedIncident(
            incident_id="A", status="resolved_other", alert_count=1,
            raw={"status": "resolved_other", "resolved_timestamp": resolved_ms},
        )
        case_a = cl.ledger_case_from_incident(inc)
        al = [
            cl.LedgerAlert("1", first, "high", "A", None, None, None),
            cl.LedgerAlert("2", second, "high", "B", None, None, None),
        ]
        mc, _ = cl.classify_merge(al, {"A": case_a, "B": C("B")}, basis="alert_incident_ids")
        assert mc == "SPLIT_SEALED"
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


def test_triage_comment_is_socfw_auto_triage():
    c = C("A", "resolved_other", resolved_at=T0, comment=TRIAGE_RESOLVE_COMMENT_PREFIX + " score 12")
    assert cl.case_closed_by(c) == "socfw_auto_triage"
    assert cl.case_sealed(c) is True


def test_unknown_status_raises():
    inc = ObservedIncident(incident_id="A", status="resolved_mystery", raw={})
    with pytest.raises(cl.CaseShapeError):
        cl.ledger_case_from_incident(inc)


def test_bare_resolved_is_other_resolved():
    inc = ObservedIncident(incident_id="A", status="resolved", raw={})
    assert cl.case_closed_by(cl.ledger_case_from_incident(inc)) == "other_resolved"


def test_unparseable_resolved_timestamp_raises():
    inc = ObservedIncident(incident_id="A", status="resolved_other",
                           raw={"resolved_timestamp": "not-a-time"})
    with pytest.raises(cl.CaseShapeError):
        cl.ledger_case_from_incident(inc)


def test_incident_fields_read():
    inc = ObservedIncident(
        incident_id="A", status="new", alert_count=4,
        raw={"starred": True, "assigned_user_mail": "a@b.c", "xdr_url": "https://x/1",
             "resolve_comment": None, "resolved_timestamp": None},
    )
    c = cl.ledger_case_from_incident(inc)
    assert (c.starred, c.assigned, c.xdr_url, c.alert_count, c.resolved_at) == (
        True, True, "https://x/1", 4, None)


def test_issue_unread_when_status_missing():
    obs = ObservedAlert(source="xsiam", observed_at=T0, external_id="9", severity="high")
    la = cl.ledger_alert(obs, None)
    assert la.issue_status is None
    assert cl.issue_closed_by(la) == "unread"


def test_shared_with_other_runs():
    al = [A("1", case="A"), A("2", 1, case="A")]
    led = cl.build_case_ledger(al, {"A": C("A", count=5)}, basis="alert_incident_ids", read_at=T0)
    assert led["cases"][0]["issues_other"] == 3
    assert led["shared_with_other_runs"] is True


def test_auto_resolved_after_issue_close_is_flagged():
    st, rs = "Resolved", "Resolved - False Positive"
    al = [A("1", case="A", status=st, reason=rs), A("2", 1, case="A", status=st, reason=rs)]
    c = C("A", "resolved_auto_resolve", resolved_at=T0 + timedelta(minutes=3), count=2)
    led = cl.build_case_ledger(al, {"A": c}, basis="alert_incident_ids", read_at=T0)
    assert led["cases"][0]["issues_closed_first"] is True


def test_pair_merge_spans_both_runs():
    r1 = cl.build_case_ledger(
        [A("1", 0, case="A"), A("2", 1, case="A")],
        {"A": C("A", "resolved_other", resolved_at=T0 + timedelta(minutes=2), count=2)},
        basis="alert_incident_ids", read_at=T0)
    r2 = cl.build_case_ledger(
        [A("3", 5, case="B"), A("4", 6, case="B")], {"B": C("B", count=2)},
        basis="alert_incident_ids", read_at=T0)
    assert cl.pair_merge(r1, r2)["merge_class"] == "SPLIT_SEALED"
    assert cl.pair_merge(r1, None)["merge_class"] == "PENDING"


def test_round_trip():
    al = [A("1", 0, case="A"), A("2", 5, case="B", sev="medium")]
    cases = {"A": C("A", "resolved_other", resolved_at=T0, comment="c", starred=True, count=2),
             "B": C("B", count=1, url="https://x")}
    led = cl.build_case_ledger(al, cases, basis="alert_incident_ids", read_at=T0)
    back_a, back_c = cl.ledger_from_dict(led)
    assert [(x.external_id, x.observed_at, x.severity, x.case_id) for x in back_a] == [
        (x.external_id, x.observed_at, x.severity, x.case_id) for x in al]
    assert back_c == cases
    assert led["merge_class"] == "SPLIT_SEALED"


def test_sealed_case_without_resolve_time_is_pending():
    al = [A("1", 0, case="A"), A("2", 5, case="B")]
    cases = {"A": C("A", "resolved_other", resolved_at=None), "B": C("B")}
    mc, reason = cl.classify_merge(al, cases, basis="alert_incident_ids")
    assert mc == "PENDING"
    assert "case A" in reason and "unknown" in reason


def test_resolved_without_timestamp_is_null_in_ledger_and_pending():
    inc = ObservedIncident(incident_id="A", status="resolved_other", raw={})
    ca = cl.ledger_case_from_incident(inc)
    assert ca.resolved_at is None
    al = [A("1", 0, case="A"), A("2", 5, case="B")]
    led = cl.build_case_ledger(al, {"A": ca, "B": C("B")}, basis="alert_incident_ids", read_at=T0)
    row = next(c for c in led["cases"] if c["case_id"] == "A")
    assert row["resolved_at"] is None
    assert led["merge_class"] == "PENDING"


@pytest.mark.parametrize("bad", [["new"], {"x": 1}, 5])
def test_non_string_status_raises_shape_error(bad):
    inc = ObservedIncident(incident_id="A", status=None, raw={"status": bad})
    with pytest.raises(cl.CaseShapeError):
        cl.ledger_case_from_incident(inc)
    with pytest.raises(cl.CaseShapeError):
        cl.issue_closed_by(A("1", status=bad))


def test_some_medium_without_case_is_pending():
    al = [A("1", 0, case="A"), A("2", 1, case=None)]
    mc, reason = cl.classify_merge(al, {"A": C("A")}, basis="alert_incident_ids")
    assert mc == "PENDING" and "not in a case" in reason


# ── IP-to-host bridge diagnosis (context only) ─────────────────────────────

def test_bridge_bridged_when_agent_ip_on_endpoint():
    d = cl.bridge_diagnosis(host="web-01", agent_ip="10.0.1.20",
                            endpoint_ips=["10.0.1.20", "fe80::1"])
    assert d == {"host": "web-01", "agent_ip": "10.0.1.20",
                 "endpoint_ips": ["10.0.1.20", "fe80::1"], "code": "BRIDGED"}


def test_bridge_unbridged_when_ip_absent():
    d = cl.bridge_diagnosis(host="web-01", agent_ip="10.0.1.20", endpoint_ips=["10.9.9.9"])
    assert d["code"] == "UNBRIDGED_IP_HOST"
    assert d["endpoint_ips"] == ["10.9.9.9"]


def test_bridge_unread_when_endpoint_not_returned():
    # An endpoint that was not returned (or whose record was unreadable) can
    # never be read as bridged; and an empty list is "the record carries no
    # IP", which is unbridged only if the read succeeded -- here it is a list.
    d = cl.bridge_diagnosis(host="web-01", agent_ip="10.0.1.20", endpoint_ips=None)
    assert d["code"] == "BRIDGE_UNREAD" and d["endpoint_ips"] is None
    assert cl.bridge_diagnosis(host="web-01", agent_ip=None,
                               endpoint_ips=["10.0.1.20"])["code"] == "BRIDGE_UNREAD"
    assert cl.bridge_diagnosis(host=None, agent_ip="10.0.1.20",
                               endpoint_ips=["10.0.1.20"])["code"] == "BRIDGE_UNREAD"
