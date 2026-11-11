import time

from mcp_server.envelope import (
    Envelope,
    Evidence,
    Ledger,
    as_of_from_date_header,
    ledger_from_snapshot,
    statement,
)
from mcp_server.errors import McpToolError, TOOL_NOT_BACKED, REMEDIATION


def test_statement_sentences_match_the_spec_table():
    assert statement(basis="authored", queries_issued=0, tenant=None, tenant_verified=0) == (
        "No tenant answered this call. This is authored, not tenant evidence. tenant-verified: 0"
    )
    assert statement(basis="preview", queries_issued=0, tenant=None, tenant_verified=0) == (
        "Preview only. Nothing was sent to the tenant. To execute, a human runs the request in "
        "execute_as_human. tenant-verified: 0"
    )
    assert statement(basis="reachable", queries_issued=1, tenant="deathstar", tenant_verified=0) == (
        "Tenant deathstar answered; this call cost 1 metered operation. This is reachable evidence; "
        "a detection is verified only when a run verdict carries tenant provenance. tenant-verified: 0"
    )
    assert statement(basis="reachable", queries_issued=3, tenant="deathstar", tenant_verified=0).startswith(
        "Tenant deathstar answered; this call cost 3 metered operations. "
    )
    assert statement(
        basis="authored", queries_issued=0, tenant=None, tenant_verified=0,
        tool_id="xsiam.parser.list", not_backed=True,
    ) == (
        "xsiam.parser.list has no backing operation in the XSIAM public API; nothing was queried. "
        "tenant-verified: 0"
    )
    assert statement(
        basis="simcore", queries_issued=0, tenant=None, tenant_verified=0, simcore_unreachable=True,
    ) == "No tenant answered this call. SimCore itself was unreachable. tenant-verified: 0"
    assert statement(basis="simcore", queries_issued=0, tenant=None, tenant_verified=7).endswith(
        "tenant-verified: 7"
    )


def test_ledger_from_snapshot_maps_fields_and_none_stays_none():
    assert ledger_from_snapshot(None) is None
    snap = {
        "tenant": "api-deathstar.xdr.us.paloaltonetworks.com",
        "queries_last_24h": 7,
        "limit": 500,
        "remaining": 493,
        "breaker_open": True,
        "breaker_open_until": "2026-11-02T18:00:00",
        "retry_after_seconds": 12,
        "trips": 1,
    }
    led = ledger_from_snapshot(snap)
    assert led == Ledger(
        key="api-deathstar.xdr.us.paloaltonetworks.com", used_today=7, budget=500,
        remaining=493, breaker_open=True, breaker_until="2026-11-02T18:00:00+00:00",
    )
    snap2 = {**snap, "breaker_open": False, "breaker_open_until": None}
    assert ledger_from_snapshot(snap2).breaker_until is None


def test_as_of_has_offset_with_and_without_date_header(monkeypatch):
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    time.tzset()
    try:
        with_header = as_of_from_date_header("Mon, 02 Nov 2026 18:04:11 GMT")
        assert with_header == "2026-11-02T18:04:11+00:00"
        for bad in (None, "", "not a date"):
            fallback = as_of_from_date_header(bad)
            assert fallback.endswith("+00:00")
    finally:
        monkeypatch.undo()
        time.tzset()


def test_error_structured_content_validates_as_envelope():
    ev = Evidence(basis="authored", statement="x tenant-verified: 0")
    err = McpToolError(
        code=TOOL_NOT_BACKED, error="xsiam.parser.list has no backing operation",
        detail="the XSIAM public API exposes no parser listing; nothing was queried",
        remediation=REMEDIATION[TOOL_NOT_BACKED],
    )
    structured = err.as_structured(ev)
    assert set(structured) == {"error", "code", "detail", "remediation", "evidence"}
    env = Envelope.model_validate(structured)
    assert env.data is None and env.evidence.queries_issued == 0
    assert ev.tenant_verified == 0 and ev.truncated is False and ev.as_of is None

    previewed = McpToolError(code="MCP_WRITE_NOT_EXECUTABLE", error="e", detail="d", data={"p": 1})
    assert previewed.as_structured(ev)["data"] == {"p": 1}


def test_unknown_count_is_stated_as_not_known_and_not_zero():
    text = statement(basis="simcore", queries_issued=None, tenant="deathstar", tenant_verified=0)
    assert text == ("SimCore accepted the request; whether the tenant was called and how many "
                    "operations it issued is not known — this is not zero. tenant-verified: 0")
    # True in both directions: it never claims the tenant was called.
    assert "Tenant deathstar was called" not in text
    ev = Evidence(basis="simcore", queries_issued=None, tenant="deathstar", statement=text)
    assert ev.model_dump()["queries_issued"] is None


def test_unread_tenant_verified_is_never_a_stated_zero():
    text = statement(basis="simcore", queries_issued=0, tenant=None, tenant_verified=None)
    assert text.endswith("tenant-verified: not read")
    assert "tenant-verified: 0" not in text
    ev = Evidence(basis="simcore", tenant_verified=None, statement=text)
    assert ev.tenant_verified is None


def test_a_tenant_that_answered_at_zero_metered_cost_says_so():
    """A results poll reaches the tenant and costs 0 ledger units: the sentence
    says the tenant answered and that the call cost 0, never "No tenant answered"."""
    text = statement(basis="reachable", queries_issued=0, tenant="deathstar", tenant_verified=0,
                     tenant_answered=True)
    assert text == (
        "Tenant deathstar answered; this call cost 0 metered operations. This is reachable evidence; "
        "a detection is verified only when a run verdict carries tenant provenance. tenant-verified: 0")
    assert "No tenant answered" not in text
    unknown = statement(basis="reachable", queries_issued=None, tenant="deathstar", tenant_verified=0,
                        tenant_answered=True)
    assert unknown.startswith("Tenant deathstar answered; how many metered operations this call cost "
                              "is not known — this is not zero. ")
    ev = Evidence(basis="reachable", queries_issued=0, tenant="deathstar", tenant_answered=True,
                  statement=text)
    assert ev.model_dump()["tenant_answered"] is True
    assert Evidence(basis="authored", statement="x").tenant_answered is False
