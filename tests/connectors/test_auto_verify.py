"""Tests for the opt-in auto-verify phase of the reconcile sweep.

The sweep issues XQL against a customer tenant, so every guard here is a
customer-facing cost control, not a nicety: terminal verdicts are never
re-verified, attempts are capped, retries back off, and the whole cycle is
bounded by a query budget with a quota circuit-breaker.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest


def _seed_run(SessionLocal, *, run_id, started_at=None, tc_verdict=None,
              tc_verdict_detail=None, verification_xql="dataset = xdr_data",
              results=1, scenario_id="SIM-EDR-001", with_scenario=True):
    from models import Result, Run, Scenario

    started = started_at or (datetime.utcnow() - timedelta(minutes=10))

    async def _do():
        async with SessionLocal() as db:
            if with_scenario:
                exists = await db.get(Scenario, 1)
                if exists is None:
                    db.add(Scenario(
                        scenario_id=scenario_id, name="X", plane="EDR", version="1.0",
                        status="active", uc_ref="UCS-EDR-01", uc_name="X",
                        tc_ref="TC-EDR-01", tc_name="Y",
                        mitre_tactic="TA0006", mitre_tactic_name="Credential Access",
                        mitre_technique="T1003.001", mitre_technique_name="LSASS",
                        steps=[],
                    ))
            db.add(Run(run_id=run_id, scenario_id=scenario_id, mode="pull",
                       status="complete", started_at=started,
                       tc_verdict=tc_verdict, tc_verdict_detail=tc_verdict_detail))
            for i in range(results):
                db.add(Result(run_id=run_id, step_id=f"step-0{i + 1}", plane="EDR",
                              signal_type="BIOC", expected_detection="d",
                              observed=False,
                              executed_at=started,
                              verification_xql=verification_xql,
                              kpi_verdict="pending" if verification_xql else None))
            await db.commit()

    asyncio.run(_do())


def _runner(rows: int, calls: list):
    async def _run(query: str) -> int:
        calls.append(query)
        return rows
    return _run


def _sweep(SessionLocal, *, calls, rows=1, **kw):
    from connectors.service import auto_verify_once

    async def _do():
        async with SessionLocal() as db:
            return await auto_verify_once(
                db, lookback_seconds=86400, runner=_runner(rows, calls),
                tenant="acme-prod", **kw)

    return asyncio.run(_do())


def test_sweep_verifies_an_open_run(session_factory):
    _seed_run(session_factory, run_id="run-open")
    calls: list[str] = []
    stats = _sweep(session_factory, calls=calls)

    assert stats["runs_considered"] == 1
    assert stats["runs_verified"] == 1
    assert stats["queries_issued"] == 1
    assert calls == ["dataset = xdr_data"]


def test_sweep_skips_a_terminal_verdict(session_factory):
    """Once a run reaches pass/fail/not_applicable it is never re-queried."""
    _seed_run(session_factory, run_id="run-done", tc_verdict="pass")
    calls: list[str] = []
    stats = _sweep(session_factory, calls=calls)

    assert stats["runs_considered"] == 0
    assert calls == []


def test_sweep_respects_the_attempt_cap(session_factory):
    """A detection that has not landed after N sweeps is not going to."""
    _seed_run(session_factory, run_id="run-capped", tc_verdict="pending",
              tc_verdict_detail={"verdict": "pending", "verification": {
                  "attempts": 3, "last_attempt_at": (
                      datetime.utcnow() - timedelta(days=1)).isoformat()}})
    calls: list[str] = []
    stats = _sweep(session_factory, calls=calls, max_attempts=3)

    assert stats["runs_considered"] == 0
    assert calls == []


def test_sweep_respects_backoff(session_factory):
    """A run verified 10 seconds ago must not be re-queried on the next sweep."""
    _seed_run(session_factory, run_id="run-hot", tc_verdict="pending",
              tc_verdict_detail={"verdict": "pending", "verification": {
                  "attempts": 1,
                  "last_attempt_at": (datetime.utcnow() - timedelta(seconds=10)).isoformat()}})
    calls: list[str] = []
    assert _sweep(session_factory, calls=calls, backoff_seconds=600,
                  max_attempts=3)["runs_considered"] == 0
    assert calls == []


def test_sweep_retries_once_backoff_has_elapsed(session_factory):
    _seed_run(session_factory, run_id="run-cold", tc_verdict="pending",
              tc_verdict_detail={"verdict": "pending", "verification": {
                  "attempts": 1,
                  "last_attempt_at": (datetime.utcnow() - timedelta(hours=2)).isoformat()}})
    calls: list[str] = []
    stats = _sweep(session_factory, calls=calls, backoff_seconds=600, max_attempts=3)

    assert stats["runs_considered"] == 1
    assert calls == ["dataset = xdr_data"]


def test_sweep_stops_at_the_query_budget(session_factory):
    """The budget bounds the WHOLE cycle, not each run."""
    for i in range(4):
        _seed_run(session_factory, run_id=f"run-b{i}", results=2)
    calls: list[str] = []
    stats = _sweep(session_factory, calls=calls, max_queries=3)

    assert len(calls) == 3
    assert stats["queries_issued"] == 3


def test_quota_error_stops_the_whole_sweep(session_factory):
    """One XQL_0003 trips the breaker for every remaining run — the customer's
    analysts share that quota."""
    from connectors.service import auto_verify_once
    from integrations.xsiam.exceptions import XsiamQuotaError

    for i in range(3):
        _seed_run(session_factory, run_id=f"run-q{i}")
    calls: list[str] = []

    async def _quota_runner(query: str) -> int:
        calls.append(query)
        raise XsiamQuotaError("XQL_0003")

    async def _do():
        async with session_factory() as db:
            return await auto_verify_once(db, lookback_seconds=86400,
                                          runner=_quota_runner, tenant="acme-prod")

    stats = asyncio.run(_do())
    assert len(calls) == 1
    assert stats["quota_tripped"] is True


def test_sweep_no_ops_without_a_tenant_integration(session_factory):
    """No credential → zero queries, zero noise. Same silence as
    auto_reconcile_once with no configured connector kinds."""
    from connectors.service import auto_verify_once

    _seed_run(session_factory, run_id="run-nocred")

    async def _do():
        async with session_factory() as db:
            return await auto_verify_once(db, lookback_seconds=86400)

    stats = asyncio.run(_do())
    assert stats == {"runs_considered": 0, "runs_verified": 0, "queries_issued": 0,
                     "quota_tripped": False, "tenant": None}


def test_reconcile_sweep_does_not_verify_when_the_flag_is_off(session_factory, monkeypatch):
    """CORTEXSIM_AUTO_VERIFY defaults OFF; the reconcile sweep must not grow an
    outbound XQL phase because reconcile was enabled."""
    from config import settings
    from connectors import service

    monkeypatch.setattr(settings, "CORTEXSIM_AUTO_VERIFY", False)
    called = []

    async def _spy(*a, **kw):
        called.append(kw)
        return {}

    monkeypatch.setattr(service, "auto_verify_once", _spy)
    _seed_run(session_factory, run_id="run-flagoff")

    async def _do():
        async with session_factory() as db:
            return await service.auto_reconcile_once(
                db, lookback_seconds=86400, window_seconds=3600)

    stats = asyncio.run(_do())
    assert called == []
    assert "verify" not in stats


def test_reconcile_sweep_runs_the_verify_phase_when_enabled(session_factory, monkeypatch):
    from config import settings
    from connectors import service

    monkeypatch.setattr(settings, "CORTEXSIM_AUTO_VERIFY", True)
    called = []

    async def _spy(db, **kw):
        called.append(kw)
        return {"runs_considered": 1}

    monkeypatch.setattr(service, "auto_verify_once", _spy)
    _seed_run(session_factory, run_id="run-flagon")

    async def _do():
        async with session_factory() as db:
            return await service.auto_reconcile_once(
                db, lookback_seconds=86400, window_seconds=3600)

    stats = asyncio.run(_do())
    assert called == [{"lookback_seconds": 86400}]
    assert stats["verify"] == {"runs_considered": 1}


def test_verify_phase_failure_does_not_invalidate_reconcile(session_factory, monkeypatch):
    from config import settings
    from connectors import service

    monkeypatch.setattr(settings, "CORTEXSIM_AUTO_VERIFY", True)

    async def _boom(db, **kw):
        raise RuntimeError("tenant on fire")

    monkeypatch.setattr(service, "auto_verify_once", _boom)
    _seed_run(session_factory, run_id="run-boom")

    async def _do():
        async with session_factory() as db:
            return await service.auto_reconcile_once(
                db, lookback_seconds=86400, window_seconds=3600)

    stats = asyncio.run(_do())
    assert stats["runs_reconciled"] == 0
    assert "verify" not in stats


@pytest.mark.parametrize("status,expected", [
    ("running", "run_not_finished"),
    ("pending", "run_not_finished"),
    ("aborted", "run_not_finished"),
    ("complete", None),
    ("failed", None),
    ("staged", None),
])
def test_only_finished_runs_are_verified(status, expected):
    """Verification costs a query against the customer's own XQL quota, so it
    must never fire at a run that is still emitting.

    An in-flight run has not finished producing telemetry, so an empty answer
    means "too early", not "the detection failed" — recording that as a verdict
    is exactly the false red this harness exists to avoid. `staged` IS eligible:
    a push-mode run is terminal the moment the bundle is generated, and querying
    the tenant is the only way to learn what happened on the DC's endpoint.
    """
    from datetime import datetime  # noqa: PLC0415

    from connectors.service import _verify_eligible  # noqa: PLC0415

    class R:
        tc_verdict = None
        tc_verdict_detail = None

    run = R()
    run.status = status
    assert _verify_eligible(run, max_attempts=3, backoff_seconds=600,
                            now=datetime.utcnow()) == expected
