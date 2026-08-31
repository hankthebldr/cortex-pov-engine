"""Tests for the shared reconcile service + opt-in auto-reconcile sweep."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def db_factory(memory_db):
    _get_db, SessionLocal = memory_db
    return SessionLocal


def _seed(SessionLocal, *, run_id, started_at, with_integration=True):
    from models import Result, Run
    from security.credentials import CredentialStore

    async def _seed_async():
        async with SessionLocal() as s:
            s.add(Run(run_id=run_id, scenario_id="SIM-EDR-001", mode="pull",
                      status="complete", started_at=started_at))
            s.add(Result(run_id=run_id, step_id="step-02", plane="EDR",
                         signal_type="BIOC", expected_detection="LSASS dump",
                         observed=False, executed_at=started_at,
                         mitre_technique="T1003.001", detection_id="bioc-lsass"))
            await s.commit()
            if with_integration:
                # Use the store's DEFAULT master key (same as the loop) so the
                # secret round-trips — a mismatched key would surface as a
                # CREDENTIAL_ERROR the loop silently skips.
                store = CredentialStore(s)
                await store.put_integration(
                    name="xsiam-prod", kind="xsiam",
                    plaintext_secret="API_KEY",
                    config={"fqdn": "api-t.xdr.us.paloaltonetworks.com",
                            "api_key_id": "1"})
                await s.commit()
    asyncio.run(_seed_async())


def _fake_fetcher_with_alert(t):
    import json

    def fetcher(method, url, headers, body, timeout):
        return 200, json.dumps({"reply": {"alerts": [{
            "alert_id": "a1", "name": "LSASS Memory Access",
            "severity": "SEV_040_HIGH",
            "detection_timestamp": int(t.timestamp() * 1000),
            "mitre_technique_ids": ["T1003.001"],
        }]}})
    return fetcher


def test_auto_reconcile_validates_recent_run(db_factory):
    from connectors.service import auto_reconcile_once

    t0 = datetime.now(timezone.utc) - timedelta(minutes=10)
    _seed(db_factory, run_id="run-auto-1", started_at=t0)
    fetcher = _fake_fetcher_with_alert(t0 + timedelta(seconds=20))

    async def _run():
        async with db_factory() as db:
            return await auto_reconcile_once(
                db, lookback_seconds=86400, window_seconds=3600, fetcher=fetcher)
    stats = asyncio.run(_run())

    assert stats["runs_considered"] == 1
    assert stats["runs_reconciled"] == 1
    assert stats["newly_matched"] == 1
    assert stats["kinds"] == ["xsiam"]


def test_auto_reconcile_noop_without_integration(db_factory):
    from connectors.service import auto_reconcile_once

    t0 = datetime.now(timezone.utc) - timedelta(minutes=5)
    _seed(db_factory, run_id="run-auto-2", started_at=t0, with_integration=False)

    async def _run():
        async with db_factory() as db:
            return await auto_reconcile_once(db, lookback_seconds=86400, window_seconds=3600)
    stats = asyncio.run(_run())
    assert stats["runs_considered"] == 0
    assert stats["kinds"] == []


def test_auto_reconcile_skips_old_runs(db_factory):
    from connectors.service import auto_reconcile_once

    old = datetime.now(timezone.utc) - timedelta(days=3)
    _seed(db_factory, run_id="run-auto-3", started_at=old)
    fetcher = _fake_fetcher_with_alert(old + timedelta(seconds=20))

    async def _run():
        async with db_factory() as db:
            # lookback 1h → the 3-day-old run is out of scope.
            return await auto_reconcile_once(
                db, lookback_seconds=3600, window_seconds=3600, fetcher=fetcher)
    stats = asyncio.run(_run())
    assert stats["runs_considered"] == 0


def test_reconcile_run_raises_structured_error_on_no_integration(db_factory):
    from connectors.service import ReconcileError, reconcile_run
    from models import Result, Run

    t0 = datetime.now(timezone.utc)
    _seed(db_factory, run_id="run-auto-4", started_at=t0, with_integration=False)

    async def _run():
        async with db_factory() as db:
            from sqlalchemy import select
            run = (await db.execute(select(Run).where(Run.run_id == "run-auto-4"))).scalar_one()
            results = list((await db.execute(
                select(Result).where(Result.run_id == "run-auto-4"))).scalars().all())
            with pytest.raises(ReconcileError) as ei:
                await reconcile_run(db, run, results, connector_kind="xsiam")
            return ei.value
    err = asyncio.run(_run())
    assert err.code == "NO_INTEGRATION"
