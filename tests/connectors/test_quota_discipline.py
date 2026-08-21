"""F-05 / F-06 — every path that costs the customer's tenant is now bounded.

The verify sweep already had a budget, an attempt cap, exponential backoff and a
circuit breaker. All four were fitted to the *cheaper* of the two loops; the
manual endpoint and the auto-reconcile sweep had none. These tests are the
customer-facing cost controls, not hygiene.

The counterweight to all of it: a run that stops being polled must stay
`pending` and say WHY. Never `fail`, and never silence — a DC watching coverage
plateau must be able to tell "the detections are weak" from "CortexSim stopped
asking".
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest


def _seed(SessionLocal, *, run_id, observed=False, detail=None,
          started_at=None, xql=None):
    from models import Result, Run, Scenario

    started = started_at or (datetime.utcnow() - timedelta(minutes=5))

    async def _do():
        async with SessionLocal() as db:
            if await db.get(Scenario, 1) is None:
                db.add(Scenario(scenario_id="SIM-EDR-001", name="X", plane="EDR",
                                version="1.0", status="active", uc_ref="UCS-EDR-01",
                                uc_name="X", tc_ref="TC-EDR-01", tc_name="Y",
                                mitre_tactic="TA0006", mitre_tactic_name="CA",
                                mitre_technique="T1003.001", mitre_technique_name="L",
                                steps=[]))
            db.add(Run(run_id=run_id, scenario_id="SIM-EDR-001", mode="pull",
                       status="complete", started_at=started,
                       tc_verdict_detail=detail))
            db.add(Result(run_id=run_id, step_id="step-01", plane="EDR",
                          signal_type="BIOC", expected_detection="d",
                          observed=observed, executed_at=started,
                          verification_xql=xql,
                          kpi_verdict="pending" if xql else None))
            await db.commit()
    asyncio.run(_do())


async def _put_reconcile_cred(db):
    from security.credentials import CredentialStore
    await CredentialStore(db).put_integration(
        name="acme", kind="xsiam", plaintext_secret="k" * 40,
        config={"fqdn": "api-t.xdr.us.paloaltonetworks.com", "api_key_id": "1"})
    await db.commit()


def _empty_fetcher(counter):
    def fetcher(method, url, headers, body, timeout):
        counter.append(url)
        return 200, json.dumps({"reply": {"alerts": []}})
    return fetcher


# ── F-05: auto-reconcile is bounded ────────────────────────────────────────


def test_auto_reconcile_stops_after_the_attempt_cap(session_factory):
    """Pre-fix: a run whose detection never fires is re-POSTed on EVERY sweep for
    the whole 24 h lookback. At the 300 s default that is 288 tenant calls per
    run per day, forever, with the DC unaware."""
    from connectors.service import auto_reconcile_once

    _seed(session_factory, run_id="run-cap")
    calls: list = []

    async def _sweep():
        async with session_factory() as db:
            await _put_reconcile_cred(db)
            out = []
            for _ in range(8):
                out.append(await auto_reconcile_once(
                    db, lookback_seconds=86400, window_seconds=3600,
                    fetcher=_empty_fetcher(calls),
                    max_attempts=3, backoff_seconds=0))
            return out

    sweeps = asyncio.run(_sweep())
    assert len(calls) == 3, f"expected 3 pulls, the tenant saw {len(calls)}"
    assert sweeps[-1]["skipped"] == {"attempt_cap": 1}


def test_a_capped_out_run_says_why_and_stays_pending(session_factory):
    """The risk of adding a cap: a DC sees coverage plateau and blames the
    customer's detections. A capped-out run must be VISIBLE."""
    from connectors.service import auto_reconcile_once
    from models import Run
    from sqlalchemy import select

    _seed(session_factory, run_id="run-vis")
    calls: list = []

    async def _do():
        async with session_factory() as db:
            await _put_reconcile_cred(db)
            for _ in range(5):
                await auto_reconcile_once(
                    db, lookback_seconds=86400, window_seconds=3600,
                    fetcher=_empty_fetcher(calls), max_attempts=2, backoff_seconds=0)
            run = (await db.execute(select(Run))).scalars().first()
            await db.refresh(run)
            return run

    run = asyncio.run(_do())
    book = (run.tc_verdict_detail or {}).get("reconciliation") or {}
    assert book["attempts"] == 2
    assert "stopped polling after 2 attempts" in book["stopped_reason"]
    assert "did not fail" in book["stopped_reason"]
    assert run.tc_verdict != "fail", "polling stopping is not a detection failure"


def test_auto_reconcile_backs_off_between_attempts(session_factory):
    from connectors.service import auto_reconcile_once

    _seed(session_factory, run_id="run-backoff")
    calls: list = []

    async def _do():
        async with session_factory() as db:
            await _put_reconcile_cred(db)
            first = await auto_reconcile_once(
                db, lookback_seconds=86400, window_seconds=3600,
                fetcher=_empty_fetcher(calls), max_attempts=6, backoff_seconds=900)
            second = await auto_reconcile_once(
                db, lookback_seconds=86400, window_seconds=3600,
                fetcher=_empty_fetcher(calls), max_attempts=6, backoff_seconds=900)
            return first, second

    _first, second = asyncio.run(_do())
    assert len(calls) == 1, "the second sweep must respect the backoff"
    assert second["skipped"] == {"backoff": 1}


def test_auto_reconcile_honours_a_per_sweep_pull_budget(session_factory):
    from connectors.service import auto_reconcile_once

    for i in range(10):
        _seed(session_factory, run_id=f"run-{i}")
    calls: list = []

    async def _do():
        async with session_factory() as db:
            await _put_reconcile_cred(db)
            return await auto_reconcile_once(
                db, lookback_seconds=86400, window_seconds=3600,
                fetcher=_empty_fetcher(calls), max_pulls=4)

    stats = asyncio.run(_do())
    assert len(calls) == 4
    assert stats["pulls_issued"] == 4
    assert stats["skipped"]["sweep_budget_spent"] >= 1


def test_a_429_from_the_tenant_stops_the_whole_sweep(session_factory):
    """The customer's analysts share this quota. Walking the remaining runs into
    the same wall is the behaviour a revoked API key is made of."""
    from connectors.service import auto_reconcile_once

    for i in range(6):
        _seed(session_factory, run_id=f"run-q{i}")
    calls: list = []

    def fetcher(method, url, headers, body, timeout):
        calls.append(url)
        return 429, "rate limited"

    async def _do():
        async with session_factory() as db:
            await _put_reconcile_cred(db)
            return await auto_reconcile_once(
                db, lookback_seconds=86400, window_seconds=3600, fetcher=fetcher)

    stats = asyncio.run(_do())
    assert len(calls) == 1, f"the sweep kept going after a 429 ({len(calls)} calls)"
    assert stats["quota_tripped"] is True


def test_a_sweep_that_matched_nothing_is_logged_at_warning(session_factory, caplog):
    """F-11's production half. `auto_reconcile_once` logged every skip at DEBUG,
    so a zero-coverage reconcile was indistinguishable from an idle sweep."""
    import logging

    from connectors.service import auto_reconcile_once

    _seed(session_factory, run_id="run-quiet")
    calls: list = []

    async def _do():
        async with session_factory() as db:
            await _put_reconcile_cred(db)
            return await auto_reconcile_once(
                db, lookback_seconds=86400, window_seconds=3600,
                fetcher=_empty_fetcher(calls))

    with caplog.at_level(logging.WARNING, logger="cortexsim.connectors.service"):
        asyncio.run(_do())
    assert any("matched 0 detections" in r.getMessage() for r in caplog.records), (
        "a sweep that considered runs and matched nothing must be a WARNING, "
        "not a DEBUG line nobody sees")


def test_an_already_observed_run_is_never_pulled_again(session_factory):
    from connectors.service import auto_reconcile_once

    _seed(session_factory, run_id="run-done", observed=True)
    calls: list = []

    async def _do():
        async with session_factory() as db:
            await _put_reconcile_cred(db)
            return await auto_reconcile_once(
                db, lookback_seconds=86400, window_seconds=3600,
                fetcher=_empty_fetcher(calls))

    stats = asyncio.run(_do())
    assert calls == [] and stats["runs_considered"] == 0


def test_reconcile_bookkeeping_survives_a_tier_one_rescore(session_factory):
    """Tier 1 rebuilds tc_verdict_detail wholesale and runs on every completion.
    If it drops the reconcile block, `attempts` resets to 0 every time and the
    cap never bites — and `stopped_reason` disappears."""
    from connectors.service import auto_reconcile_once, score_run_for_run
    from models import Run
    from sqlalchemy import select

    _seed(session_factory, run_id="run-carry")
    calls: list = []

    async def _do():
        async with session_factory() as db:
            await _put_reconcile_cred(db)
            await auto_reconcile_once(db, lookback_seconds=86400, window_seconds=3600,
                                      fetcher=_empty_fetcher(calls))
            run = (await db.execute(select(Run))).scalars().first()
            await score_run_for_run(db, run, source="complete")
            await db.refresh(run)
            return run.tc_verdict_detail

    detail = asyncio.run(_do())
    assert (detail.get("reconciliation") or {}).get("attempts") == 1


# ── F-06: the manual /verify path is bounded too ───────────────────────────


def _fake_runner(calls):
    """A tenant that answers but errors — the run stays `pending` forever.

    That is precisely the shape the attempt cap exists for: a settled verdict is
    already protected by the terminal check, so a runner returning rows would
    test the wrong guard.
    """
    from integrations.xsiam.exceptions import XsiamQueryError

    async def _run(q):
        calls.append(q)
        raise XsiamQueryError("dataset not found")
    return _run


def test_manual_verify_honours_the_attempt_cap(session_factory, monkeypatch):
    """Pre-fix `_verify_eligible` was called ONLY from the sweep, so
    `POST /verify` re-queried a stuck run on every request with a brand-new
    50-query budget and no delay."""
    from config import settings
    from connectors.service import verify_run_now
    from models import Run
    from sqlalchemy import select

    monkeypatch.setattr(settings, "CORTEXSIM_AUTO_VERIFY_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(settings, "CORTEXSIM_AUTO_VERIFY_BACKOFF_SECONDS", 0)
    _seed(session_factory, run_id="run-manual", xql="dataset = xdr_data")
    calls: list = []

    async def _do():
        async with session_factory() as db:
            run = (await db.execute(select(Run))).scalars().first()
            out = []
            for _ in range(5):
                out.append(await verify_run_now(db, run, runner=_fake_runner(calls),
                                                tenant="acme"))
            return out

    outcomes = asyncio.run(_do())
    assert len(calls) == 2, f"the tenant saw {len(calls)} queries, cap is 2"
    assert outcomes[-1].reason == "attempt_cap"
    assert outcomes[-1].tc_verdict == "pending"
    assert outcomes[-1].remediation and "not\n" not in outcomes[-1].remediation
    assert "`pending`, not" in outcomes[-1].remediation


def test_manual_verify_reports_a_retry_after_while_backing_off(session_factory, monkeypatch):
    from config import settings
    from connectors.service import verify_run_now
    from models import Run
    from sqlalchemy import select

    monkeypatch.setattr(settings, "CORTEXSIM_AUTO_VERIFY_MAX_ATTEMPTS", 5)
    monkeypatch.setattr(settings, "CORTEXSIM_AUTO_VERIFY_BACKOFF_SECONDS", 600)
    _seed(session_factory, run_id="run-wait", xql="dataset = xdr_data")
    calls: list = []

    async def _do():
        async with session_factory() as db:
            run = (await db.execute(select(Run))).scalars().first()
            await verify_run_now(db, run, runner=_fake_runner(calls), tenant="acme")
            return await verify_run_now(db, run, runner=_fake_runner(calls), tenant="acme")

    out = asyncio.run(_do())
    assert len(calls) == 1
    assert out.reason == "backoff"
    assert 0 < (out.retry_after_seconds or 0) <= 600


def test_force_bypasses_the_terminal_check_but_not_the_attempt_cap(
        session_factory, monkeypatch):
    """`force=true`'s documented job is re-verifying a TERMINAL verdict. It is
    not an exemption from the quota control, which protects the customer rather
    than CortexSim's idempotence."""
    from config import settings
    from connectors.service import verify_run_now
    from models import Run
    from sqlalchemy import select

    monkeypatch.setattr(settings, "CORTEXSIM_AUTO_VERIFY_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(settings, "CORTEXSIM_AUTO_VERIFY_BACKOFF_SECONDS", 0)
    _seed(session_factory, run_id="run-force", xql="dataset = xdr_data")
    calls: list = []

    async def _do():
        async with session_factory() as db:
            run = (await db.execute(select(Run))).scalars().first()
            await verify_run_now(db, run, runner=_fake_runner(calls), tenant="acme")
            return await verify_run_now(db, run, runner=_fake_runner(calls),
                                        tenant="acme", force=True)

    out = asyncio.run(_do())
    assert len(calls) == 1
    assert out.reason == "attempt_cap"


# ── the cross-call ledger ──────────────────────────────────────────────────


def _real_runner(handler, kind="count"):
    """The PRODUCTION runner over a scripted tenant.

    Deliberately not a stub: the ledger is charged inside the runner precisely so
    that callers which never reach ``_budgeted`` — assertion runs build a runner
    directly — are bounded too. A stub would prove the wrapper, not the path.
    """
    import httpx

    from engine.verifier import xsiam_query_runner, xsiam_rows_runner
    from integrations.xsiam.client import XsiamClient
    from integrations.xsiam.config import XsiamTenantConfig

    cfg = XsiamTenantConfig(base_url="https://api-acme.xdr.us.paloaltonetworks.com",
                            region="us", auth_mode="standard", api_key_id="1")
    client = XsiamClient(cfg, "key", transport=httpx.MockTransport(handler))
    build = xsiam_query_runner if kind == "count" else xsiam_rows_runner
    return build(client, {"relativeTime": 1000})


def _ok_tenant(calls):
    import httpx

    def handler(request):
        calls.append(str(request.url))
        if request.url.path.endswith("start_xql_query"):
            return httpx.Response(200, json={"reply": "q1"})
        return httpx.Response(200, json={"reply": {
            "status": "SUCCESS", "results": {"data": [{"a": 1}]}}})
    return handler


def test_the_daily_ledger_bounds_every_path_together(monkeypatch):
    """The per-sweep budget is per INVOCATION. Without an outer bound, N calls
    buy N x 50 queries — and `POST /verify` built a fresh budget every time."""
    from connectors.service import _budgeted, _new_budget
    from integrations.xsiam.ledger import DailyBudgetExhausted, ledger

    monkeypatch.setenv("CORTEXSIM_XSIAM_MAX_QUERIES_PER_DAY", "3")
    ledger.reset()
    calls: list = []
    runner = _real_runner(_ok_tenant(calls))

    async def _drive():
        for _ in range(6):
            budget = _new_budget(50)          # a FRESH budget each time
            budget["tenant"] = "acme"
            try:
                await _budgeted(runner, budget)("q")
            except DailyBudgetExhausted:
                pass

    asyncio.run(_drive())
    starts = [c for c in calls if "start_xql_query" in c]
    assert len(starts) == 3, f"the ledger let {len(starts)} queries through, limit 3"
    ledger.reset()


def test_assertion_runs_are_budgeted_even_though_they_never_touch_budgeted():
    """`/api/assertions/{id}/run` builds a rows-runner directly and never reaches
    connectors.service._budgeted. Before the charge moved into the runner that
    was a third, entirely unbounded path onto the same metered resource."""
    from integrations.xsiam.ledger import QuotaBreakerOpen, ledger

    ledger.reset()
    calls: list = []
    runner = _real_runner(_ok_tenant(calls), kind="rows")

    ledger.trip("api-acme.xdr.us.paloaltonetworks.com", cooldown_seconds=900)
    with pytest.raises(QuotaBreakerOpen):
        asyncio.run(runner("dataset = xdr_data"))
    assert calls == [], "an open breaker must stop the assertion path too"
    ledger.reset()


def test_a_tenant_quota_error_trips_the_breaker_from_the_runner():
    """The trip must happen where the query happens, so every caller trips it."""
    import httpx

    from integrations.xsiam.exceptions import XsiamQuotaError
    from integrations.xsiam.ledger import ledger

    ledger.reset()

    def handler(request):
        return httpx.Response(200, json={"reply": {
            "err_code": "XQL_0003", "err_msg": "Daily quota exceeded"}})

    with pytest.raises(XsiamQuotaError):
        asyncio.run(_real_runner(handler)("q"))
    snap = ledger.snapshot("api-acme.xdr.us.paloaltonetworks.com", limit=500)
    assert snap["breaker_open"] is True
    ledger.reset()


def test_the_breaker_is_process_wide_and_reports_a_retry_after(monkeypatch):
    from integrations.xsiam.ledger import QuotaBreakerOpen, ledger

    ledger.reset()
    ledger.trip("acme", cooldown_seconds=900)
    with pytest.raises(QuotaBreakerOpen) as exc:
        ledger.check("acme", limit=500)
    assert 0 < exc.value.retry_after_seconds <= 900

    snap = ledger.snapshot("acme", limit=500)
    assert snap["breaker_open"] is True and snap["trips"] == 1
    # A different tenant is unaffected — the breaker is per tenant, not global.
    ledger.check("other", limit=500)
    ledger.reset()


def test_the_ledger_prunes_a_rolling_day():
    from integrations.xsiam.ledger import ledger

    ledger.reset()
    old = datetime.utcnow() - timedelta(days=2)
    for _ in range(5):
        ledger.charge("acme", limit=500, now=old)
    assert ledger.snapshot("acme", limit=500)["queries_last_24h"] == 0
    ledger.reset()


def test_the_http_multiplier_is_documented_not_silent():
    """One budget unit is one runner() call = 1 start + up to 21 polls. Being
    22x over the stated budget silently is how an API key gets revoked mid-POV."""
    from connectors import tuning

    assert tuning.HTTP_CALLS_PER_QUERY_WORST_CASE >= 20
