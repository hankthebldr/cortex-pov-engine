"""Preflight — the check a DC runs BEFORE the customer is watching.

Each stage is driven by a transport scripted to fail exactly one thing, because
a preflight that aborts on the first failure tells the DC one fact when they
need the whole picture.

**Read the last test in this file.** A green preflight against a MockTransport
proves the stage LOGIC, not the tenant. That distinction is the difference
between this being useful and it being the next "4368 tests passed while the
feature did not exist".
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from connectors import preflight as pf

_CFG = {"fqdn": "api-t.xdr.us.paloaltonetworks.com", "api_key_id": "1"}


def _stage(report, sid):
    return next(s for s in report.stages if s.id == sid)


def _ids(report):
    return [s.id for s in report.stages]


# ── reconcile-credential preflight (kind="xsiam") ──────────────────────────


def test_a_working_reconcile_credential_reads_ready():
    def fetcher(method, url, headers, body, timeout):
        return 200, json.dumps({"reply": {"alerts": []}})

    r = pf.preflight_reconcile(_CFG, "key", fetcher=fetcher)
    assert r.overall == pf.READY
    assert _ids(r) == ["config", "dns_tls", "auth", "scope_alerts"]
    assert r.capabilities_confirmed == ["read_alerts"]
    assert r.queries_issued == 1, "one cheap call, not a harvest"


def test_a_bad_tenant_url_is_caught_before_any_request_is_made():
    """The API key must not leave the process to learn the URL is wrong."""
    sent: list = []

    def fetcher(method, url, headers, body, timeout):  # pragma: no cover
        sent.append(url)
        return 200, "{}"

    r = pf.preflight_reconcile({"fqdn": "http://10.0.0.5", "api_key_id": "1"},
                               "key", fetcher=fetcher)
    assert r.overall == pf.BLOCKED
    assert _stage(r, "config").code == pf.PF_CONFIG_INVALID
    assert sent == []
    assert r.queries_issued == 0
    assert "did NOT leave" in _stage(r, "config").remediation or \
           "NOT sent anywhere" in _stage(r, "config").remediation


def test_an_unreachable_tenant_marks_later_stages_skipped_not_ok():
    """An ABSENT stage reads as fine. An explicit `skipped` reads as unknown.
    Those are different answers and the DC needs the second one."""
    from connectors.base import ConnectorError

    def fetcher(method, url, headers, body, timeout):
        raise ConnectorError("transport error reaching https://api-t...: no DNS")

    r = pf.preflight_reconcile(_CFG, "key", fetcher=fetcher)
    assert _stage(r, "dns_tls").code == pf.PF_UNREACHABLE
    assert _stage(r, "auth").status == pf.SKIPPED
    assert _stage(r, "scope_alerts").status == pf.SKIPPED
    assert r.overall == pf.BLOCKED


def test_a_401_distinguishes_unauthorized_from_unreachable():
    """"Unreachable" and "unauthorized" send the DC to two different places."""
    def fetcher(method, url, headers, body, timeout):
        return 401, "unauthorized"

    r = pf.preflight_reconcile(_CFG, "key", fetcher=fetcher)
    assert _stage(r, "dns_tls").status == pf.OK, "the tenant answered — it is reachable"
    assert _stage(r, "auth").code == pf.PF_AUTH_REJECTED
    assert r.capabilities_denied == ["read_alerts"]


def test_a_403_is_authorized_but_not_entitled():
    """The third state: the key is valid, its ROLE is not. This is the single
    most common first-POV failure and nothing used to observe it."""
    def fetcher(method, url, headers, body, timeout):
        return 403, "forbidden"

    r = pf.preflight_reconcile(_CFG, "key", fetcher=fetcher)
    assert _stage(r, "auth").status == pf.OK
    assert _stage(r, "scope_alerts").code == pf.PF_ALERTS_FORBIDDEN
    assert "`pending`" in _stage(r, "scope_alerts").remediation


def test_a_429_is_degraded_not_blocked():
    def fetcher(method, url, headers, body, timeout):
        return 429, "slow down"

    r = pf.preflight_reconcile(_CFG, "key", fetcher=fetcher)
    assert _stage(r, "scope_alerts").code == pf.PF_QUOTA_EXHAUSTED
    assert r.overall == pf.DEGRADED


def test_missing_config_never_reports_ready():
    r = pf.preflight_reconcile({}, "key")
    assert r.overall == pf.BLOCKED
    assert _stage(r, "config").code == pf.PF_CONFIG_INVALID
    assert r.queries_issued == 0


# ── tenant-credential preflight (kind="xsiam_tenant") ──────────────────────


def _client(handler):
    from integrations.xsiam.client import XsiamClient
    from integrations.xsiam.config import XsiamTenantConfig

    cfg = XsiamTenantConfig(base_url="https://api-t.xdr.us.paloaltonetworks.com",
                            region="us", auth_mode="standard", api_key_id="1")
    return XsiamClient(cfg, "key", transport=httpx.MockTransport(handler))


def _tenant(*, health=200, xql_status=200, rows=None, dataset_ok=True, now=None):
    rows = rows if rows is not None else [{"_time": _iso(now)}]

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p.endswith("healthcheck"):
            return httpx.Response(health, json={"reply": {"status": "ok"}})
        if p.endswith("start_xql_query"):
            body = json.loads(request.content or b"{}")
            q = (body.get("request_data") or {}).get("query", "")
            if not dataset_ok and "missing_ds" in q:
                return httpx.Response(200, json={"reply": {
                    "err_code": "XQL_2001", "err_msg": "unknown dataset"}})
            return httpx.Response(xql_status, json={"reply": "q1"})
        return httpx.Response(200, json={"reply": {
            "status": "SUCCESS", "results": {"data": rows}}})
    return handler


def _iso(now=None):
    """Epoch-ms for a NAIVE-UTC datetime.

    Not ``dt.timestamp()``: that interprets a naive datetime as HOST-LOCAL time,
    so under TZ=America/Los_Angeles this helper manufactured a 7 h skew and the
    clock stage correctly reported it. Caught by running the suite under a
    non-UTC clock — which is the entire reason the TZ matrix exists.
    """
    dt = now or datetime.utcnow()
    return int((dt.replace(tzinfo=timezone.utc)).timestamp() * 1000)


def test_a_healthy_tenant_confirms_xql_and_a_trustworthy_clock():
    r = asyncio.run(pf.preflight_tenant(_client(_tenant()), integration_name="acme"))
    assert _stage(r, "auth").code == pf.PF_OK
    assert _stage(r, "scope_xql").code == pf.PF_OK
    assert _stage(r, "clock").code == pf.PF_OK
    assert "xql" in r.capabilities_confirmed
    assert r.queries_issued >= 3


def test_xql_forbidden_is_reported_with_its_verdict_consequence():
    """"403" is a status. "Tier-2 verification and all assertions will resolve
    `pending`; reconcile and MTTD still work" is remediation."""
    r = asyncio.run(pf.preflight_tenant(_client(_tenant(xql_status=403)),
                                        integration_name="acme"))
    stage = _stage(r, "scope_xql")
    assert stage.code == pf.PF_XQL_FORBIDDEN
    assert "`pending`" in stage.remediation
    assert "Reconcile and MTTD still work" in stage.remediation
    assert r.capabilities_denied == ["xql"]
    assert _stage(r, "datasets").status == pf.SKIPPED


def test_a_tenant_without_the_xql_capability_is_not_reported_as_a_403():
    """An authorisation CortexSim WITHHELD is a different fact from one the
    tenant refused, and it must not be blamed on the customer's API key."""
    r = asyncio.run(pf.preflight_tenant(_client(_tenant()), integration_name="acme",
                                        xql_authorised=False))
    assert _stage(r, "scope_xql").code == pf.PF_XQL_NOT_AUTHORISED
    assert r.queries_issued == 1, "it must not spend a query to report our own policy"


def test_a_403_on_healthcheck_is_degraded_not_fatal():
    """/healthcheck is licence-gated on some tenants; XQL is the authoritative
    scope check. Reporting the whole tenant dead on one endpoint is wrong."""
    r = asyncio.run(pf.preflight_tenant(_client(_tenant(health=403)),
                                        integration_name="acme"))
    assert _stage(r, "auth").status == pf.DEGRADED
    assert _stage(r, "scope_xql").code == pf.PF_OK


def test_a_401_blocks_and_skips_the_rest():
    r = asyncio.run(pf.preflight_tenant(_client(_tenant(health=401)),
                                        integration_name="acme"))
    assert _stage(r, "auth").code == pf.PF_AUTH_REJECTED
    assert [s.status for s in r.stages[1:]] == [pf.SKIPPED] * 3
    assert r.overall == pf.BLOCKED


def test_dataset_probing_is_opt_in_and_priced_per_dataset():
    """12 datasets is 12 XQL queries against the customer's shared quota."""
    r = asyncio.run(pf.preflight_tenant(_client(_tenant()), integration_name="acme"))
    assert _stage(r, "datasets").code == pf.PF_NOT_PROBED
    baseline = r.queries_issued

    r2 = asyncio.run(pf.preflight_tenant(
        _client(_tenant()), integration_name="acme",
        datasets=["xdr_data", "panw_ngfw_traffic_raw"]))
    assert r2.queries_issued == baseline + 2


def test_a_missing_dataset_is_degraded_and_named():
    """F-15: zero rows from a dataset the tenant does not ingest is NOT a failed
    detection, and preflight is where that becomes knowable in advance."""
    r = asyncio.run(pf.preflight_tenant(
        _client(_tenant(dataset_ok=False)), integration_name="acme",
        datasets=["xdr_data", "missing_ds"]))
    stage = _stage(r, "datasets")
    assert stage.code == pf.PF_DATASET_MISSING
    assert stage.extra["missing"] == ["missing_ds"]
    assert stage.extra["present"] == ["xdr_data"]
    assert "`pending`, not `fail`" in stage.remediation
    assert r.overall == pf.DEGRADED


def test_clock_skew_is_measured_because_mttd_depends_on_it():
    """MTTD is (tenant alert time) - (SimCore executed_at): two clocks. This
    codebase already lost nine hours to timestamp handling once."""
    skewed = datetime.utcnow() - timedelta(minutes=9)
    r = asyncio.run(pf.preflight_tenant(
        _client(_tenant(rows=[{"_time": _iso(skewed)}])), integration_name="acme"))
    stage = _stage(r, "clock")
    assert stage.code == pf.PF_CLOCK_SKEW
    assert stage.extra["skew_seconds"] > 500
    assert "MTTD" in stage.remediation


def test_a_tenant_with_no_recent_event_reports_unknown_not_ok():
    r = asyncio.run(pf.preflight_tenant(_client(_tenant(rows=[])),
                                        integration_name="acme"))
    assert _stage(r, "clock").status == pf.SKIPPED


def test_every_non_ok_stage_carries_a_remediation():
    """A code with no remediation sends the DC to guess. Enforced, not hoped."""
    reports = [
        pf.preflight_reconcile({"fqdn": "http://x", "api_key_id": "1"}, "k"),
        pf.preflight_reconcile(_CFG, "k",
                               fetcher=lambda *a: (403, "forbidden")),
        asyncio.run(pf.preflight_tenant(_client(_tenant(xql_status=403)),
                                        integration_name="a")),
        asyncio.run(pf.preflight_tenant(_client(_tenant(dataset_ok=False)),
                                        integration_name="a",
                                        datasets=["missing_ds"])),
    ]
    for report in reports:
        for stage in report.stages:
            if stage.status in (pf.DEGRADED, pf.BLOCKED):
                assert stage.remediation, f"{stage.id}/{stage.code} has none"


def test_preflight_never_touches_a_run_or_a_verdict(session_factory):
    """Diagnosis that changes the measurement is not diagnosis."""
    from models import Result, Run
    from sqlalchemy import select

    async def _do():
        async with session_factory() as db:
            db.add(Run(run_id="r1", scenario_id="S", mode="pull", status="complete",
                       started_at=datetime.utcnow(), tc_verdict="pending"))
            await db.commit()
            pf.preflight_reconcile(_CFG, "k", fetcher=lambda *a: (401, "no"))
            await asyncio.sleep(0)
            run = (await db.execute(select(Run))).scalars().first()
            results = list((await db.execute(select(Result))).scalars().all())
            return run.tc_verdict, results

    verdict, results = asyncio.run(_do())
    assert verdict == "pending" and results == []


def test_the_report_states_what_it_does_and_does_not_prove():
    """The payload itself says a mocked run was not answered by a tenant, so a
    green preflight cannot be quoted in a handoff as "connection validated"."""
    r = pf.preflight_reconcile(_CFG, "k",
                               fetcher=lambda *a: (200, json.dumps({"reply": {"alerts": []}})))
    body = r.to_dict()
    assert body["queries_issued"] == 1
    assert "queries_issued=0 was not answered by a tenant" in body["proves"]

    blocked = pf.preflight_reconcile({"fqdn": "http://x", "api_key_id": "1"}, "k")
    assert blocked.to_dict()["queries_issued"] == 0
