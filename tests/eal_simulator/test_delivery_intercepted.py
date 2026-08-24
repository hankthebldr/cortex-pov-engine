"""COLLECTOR_INTERCEPTED — a 200 carrying an HTML page is not a delivery.

Observed on a live SimCore before this code existed: a collector answering
``200`` with ``Content-Type: text/html`` and a ``<html>…Login required…</html>``
body produced

    {"delivery_verdict": "delivered", "records_attempted": 6,
     "records_delivered": 6, "bytes_delivered": 5370, "failure_codes": {}}

The 302 case *was* caught, and 401/404/timeout all had good remediation — but
modern enterprise egress interception (ZTNA portals, GlobalProtect / Zscaler /
Netskope login interstitials, corporate SSO walls) answers **200 with HTML**,
not 302. It is the more common shape and it was the one that passed.

``POST /api/shelf/stage`` already refuses precisely this condition for staged
tool artifacts — an HTML error page saved as ``linpeas.sh`` has a perfect digest
and runs as a no-op. One codebase; the two guards are now the same rule.
"""
from __future__ import annotations

import pytest

from eal_simulator import delivery


CAPTIVE_PORTAL_BODY = (
    b"<!DOCTYPE html>\n<html><head><title>Authentication Required</title></head>"
    b"<body><h1>Login required</h1></body></html>"
)


# ---------------------------------------------------------------------------
# classify_status
# ---------------------------------------------------------------------------


def test_plain_200_json_is_still_delivered():
    """The regression guard on the guard: a real collector must not be broken."""
    assert delivery.classify_status(
        200, content_type="application/json", body_prefix=b'{"status":"ok"}'
    ) is None


@pytest.mark.parametrize("ctype", [
    "text/html",
    "text/html; charset=utf-8",
    "TEXT/HTML",
    "application/xhtml+xml",
])
def test_200_with_html_content_type_is_intercepted(ctype):
    assert delivery.classify_status(200, content_type=ctype) == \
        delivery.COLLECTOR_INTERCEPTED


def test_200_with_html_body_and_a_lying_content_type_is_intercepted():
    """Some interceptors return 200 + application/json + an HTML body. The body
    is the second, independent signal — either one is sufficient."""
    assert delivery.classify_status(
        200, content_type="application/json", body_prefix=CAPTIVE_PORTAL_BODY,
    ) == delivery.COLLECTOR_INTERCEPTED


def test_204_no_content_from_a_real_collector_is_delivered():
    assert delivery.classify_status(204, content_type="", body_prefix=b"") is None


def test_non_2xx_classification_is_unchanged():
    """Content-type sniffing applies only inside the 2xx branch — a 401 that
    happens to return HTML is still an auth failure, and that is the more
    actionable code."""
    assert delivery.classify_status(
        401, content_type="text/html", body_prefix=CAPTIVE_PORTAL_BODY,
    ) == delivery.COLLECTOR_UNAUTHORIZED
    assert delivery.classify_status(302, content_type="text/html") == \
        delivery.COLLECTOR_REDIRECTED
    assert delivery.classify_status(404) == delivery.COLLECTOR_REJECTED


def test_intercepted_is_in_the_failure_taxonomy_with_remediation():
    assert delivery.COLLECTOR_INTERCEPTED in delivery.FAILURE_CODES
    remediation = delivery.REMEDIATION[delivery.COLLECTOR_INTERCEPTED]
    assert "captive portal" in remediation
    assert "curl -i" in remediation, "the remediation must be runnable, not advice"


# ---------------------------------------------------------------------------
# Ledger + campaign rollup — the numbers a DC actually reads
# ---------------------------------------------------------------------------


def _step(ledger: delivery.DeliveryLedger) -> dict:
    return {"detail": {"delivery": ledger.to_dict()}}


def test_a_campaign_against_a_captive_portal_is_not_delivered():
    ledger = delivery.DeliveryLedger()
    for _ in range(6):
        ledger.record_response(
            200, records=1, wire_bytes=895,
            content_type="text/html; charset=utf-8",
            body_prefix=CAPTIVE_PORTAL_BODY,
        )

    assert ledger.outcome == "error"
    assert ledger.records_delivered == 0
    assert ledger.bytes_delivered == 0
    assert ledger.dominant_failure == delivery.COLLECTOR_INTERCEPTED

    summary = delivery.summarise_step_results([_step(ledger)])
    assert summary["delivery_verdict"] == "not_delivered"
    assert summary["dominant_failure"] == delivery.COLLECTOR_INTERCEPTED
    assert summary["records_delivered"] == 0
    assert "captive portal" in summary["remediation"]


def test_a_real_collector_still_reports_delivered_with_full_evidence():
    ledger = delivery.DeliveryLedger()
    for _ in range(6):
        ledger.record_response(
            200, records=1, wire_bytes=512,
            content_type="application/json", body_prefix=b'{"ok":true}',
        )
    summary = delivery.summarise_step_results([_step(ledger)])
    assert summary["delivery_verdict"] == "delivered"
    assert summary["content_type_evidence"] == "full"
    assert summary["evidence_caveat"] == ""


# ---------------------------------------------------------------------------
# The honesty rule: a caller that supplied no evidence must not be reported as
# if the interception check ran.
# ---------------------------------------------------------------------------


def test_uninspected_deliveries_are_flagged_not_silently_trusted():
    """A call site that has not been wired to pass content-type has proved a
    status code, not an ingest. The rollup says so instead of implying the
    check ran — the same 'never report green for what you did not check' rule
    the health surface follows."""
    ledger = delivery.DeliveryLedger()
    for _ in range(3):
        ledger.record_response(200, records=1, wire_bytes=100)

    assert ledger.content_type_evidence == "absent"
    assert ledger.outcome == "success"  # status-code truth is still reported

    summary = delivery.summarise_step_results([_step(ledger)])
    assert summary["delivery_verdict"] == "delivered"
    assert summary["content_type_evidence"] == "absent"
    assert "content-type was not inspected" in summary["evidence_caveat"]
    assert "curl -i" in summary["evidence_caveat"]


def test_mixed_evidence_reports_partial():
    ledger = delivery.DeliveryLedger()
    ledger.record_response(200, records=1, wire_bytes=100,
                           content_type="application/json")
    ledger.record_response(200, records=1, wire_bytes=100)
    assert ledger.content_type_evidence == "partial"
    summary = delivery.summarise_step_results([_step(ledger)])
    assert summary["content_type_evidence"] == "partial"
    assert summary["evidence_caveat"]


def test_evidence_is_not_applicable_when_nothing_was_delivered():
    ledger = delivery.DeliveryLedger()
    ledger.record_response(401, records=1, wire_bytes=100)
    assert ledger.content_type_evidence == "not_applicable"
    summary = delivery.summarise_step_results([_step(ledger)])
    assert summary["content_type_evidence"] == "not_applicable"
    assert summary["evidence_caveat"] == ""


def test_record_response_stays_backward_compatible():
    """The kwargs are optional so an un-wired call site keeps working. It is
    accounted as uninspected, not as verified."""
    ledger = delivery.DeliveryLedger()
    assert ledger.record_response(200, records=2, wire_bytes=64) is None
    assert ledger.records_delivered == 2
    assert ledger.delivered_uninspected == 1
    assert ledger.delivered_inspected == 0
