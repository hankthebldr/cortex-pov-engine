"""A run that never executed can never be credited a detection (OPS-01).

Before this, every path that ended a run WITHOUT executing it left the run's
seeded Results at ``outcome`` NULL — which everywhere else in the engine means
"pending, awaiting reconcile". The next alert that matched on technique inside
the window was credited to the run, with an MTTD, although nothing ran:

    rehydrate() fails an orphan ("RUN FAILED ON RESTART — queued task was lost")
    -> DC pastes the tenant's alerts into POST /observations
    -> newly_matched: 1, mttd 720 s, coverage 100 %

on an endpoint that never received the task. That is a coverage claim the run
cannot support, in the artifact a DC shows a customer.

Two defences, each tested on its own:

1. **Stamping.** The terminating paths stamp ``not_executed`` with a reason
   (``TASK_LOST_ON_RESTART``, ``LAUNCH_REFUSED``, ``BEACON_LOST_UNCLASSIFIED``,
   ``ABORTED_BEFORE_DELIVERY``). ``apply_verdicts`` never credits such a row.
2. **Candidate exclusion (defence in depth).** A pull run whose task no beacon
   ever collected (``delivered_at`` NULL) is not a reconcile candidate at all —
   manual ``/observations``, credential-backed ``/reconcile`` (which then makes
   no tenant call) and the auto sweep — even if some future path forgets to
   stamp it.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, select

T0 = datetime(2026, 11, 5, 12, 0, 0)
HOST = "win-lab-07"


@pytest.fixture
def client(session_factory):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.connectors import router, runs_reconcile_router
    from api.runs import router as runs_router
    from database import get_db

    async def _get_db():
        async with session_factory() as s:
            yield s

    app = FastAPI()
    for r in (router, runs_reconcile_router, runs_router):
        app.include_router(r, prefix="/api")
    app.dependency_overrides[get_db] = _get_db
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_orchestrator():
    from engine.orchestrator import orchestrator

    orchestrator._aborted.clear()
    orchestrator._queue.clear()
    yield
    orchestrator._aborted.clear()
    orchestrator._queue.clear()


async def _seed_scenario_and_agent(db):
    from models import Agent, Scenario

    db.add(Scenario(
        scenario_id="SIM-EDR-950", name="T", version="1.0", status="active", plane="EDR",
        uc_ref="UCS-EDR-01", uc_name="x", tc_ref="TC-EDR-01", tc_name="y",
        mitre_tactic="TA0006", mitre_tactic_name="Credential Access",
        mitre_technique="T1003.001", mitre_technique_name="LSASS Memory",
        execution_identity={"default": "root"}, push_supported=True, pull_supported=True,
        steps=[{"id": "step-01", "name": "dump", "identity": "root", "command": "id",
                "mitre_technique": "T1003.001",
                "expected_detections": [{
                    "plane": "EDR", "type": "BIOC",
                    "description": "LSASS memory access",
                    "detection_id": "bioc-lsass-access"}]}],
    ))
    db.add(Agent(agent_id="agent-7", hostname=HOST, os="linux",
                 capabilities=["shell", "identity-harness"], interpreters=[],
                 registered_at=T0, last_seen=datetime.utcnow(), status="online"))
    await db.commit()


def _launch(session_factory) -> str:
    """A real pull launch, executed_at pinned to T0 for a deterministic MTTD."""
    from engine.orchestrator import orchestrator
    from models import Result, Run

    async def _go():
        async with session_factory() as db:
            await _seed_scenario_and_agent(db)
            res = await orchestrator.launch(scenario_id="SIM-EDR-950", mode="pull",
                                            db=db, target_agent_id="agent-7")
            assert res.success, res.error
            for r in (await db.execute(select(Result))).scalars().all():
                r.executed_at = T0
            run = (await db.execute(select(Run))).scalar_one()
            run.started_at = T0
            await db.commit()
            return res.run_id

    return asyncio.run(_go())


def _alert(seconds_after=720, host=HOST):
    return {"external_id": "alert-lsass-1", "name": "LSASS memory access",
            "technique": "T1003.001", "detection_id": "bioc-lsass-access",
            "host": host,
            "observed_at": (T0 + timedelta(seconds=seconds_after)).isoformat() + "Z"}


def _results(session_factory):
    from models import Result

    async def _go():
        async with session_factory() as db:
            return (await db.execute(select(Result))).scalars().all()

    return asyncio.run(_go())


def _lose_task_and_restart(session_factory):
    from engine.orchestrator import Orchestrator
    from models import QueuedTask

    async def _go():
        async with session_factory() as db:
            await db.execute(delete(QueuedTask))
            await db.commit()
            return await Orchestrator().rehydrate(db)

    return asyncio.run(_go())


# ---------------------------------------------------------------------------
# 1. The reported defect: an orphan after restart, then a pasted alert
# ---------------------------------------------------------------------------


def test_orphan_failed_on_restart_is_never_credited_an_alert(client, session_factory):
    run_id = _launch(session_factory)
    stats = _lose_task_and_restart(session_factory)
    assert stats["failed_orphans"] == 1

    r = client.post(f"/api/runs/{run_id}/observations",
                    json={"observations": [_alert()]})
    assert r.status_code == 200, r.text
    body = r.json()
    # Base cc26ddf: newly_matched 1, mttd avg 720.0 — a detection credited to a
    # run that never reached the endpoint.
    assert body["newly_matched"] == 0, body
    assert body["observed"] == 0
    assert body["mttd"]["count"] == 0
    assert body["denominator"] == 0
    assert body["excluded"]["not_executed"] == 1

    (row,) = _results(session_factory)
    assert (row.outcome, row.outcome_reason) == ("not_executed", "TASK_LOST_ON_RESTART")
    assert row.observed_at is None and row.mttd_seconds is None


# ---------------------------------------------------------------------------
# 2. Defence in depth: never-delivered pull runs are not candidates at all
# ---------------------------------------------------------------------------


def test_undelivered_pull_run_is_not_a_manual_reconcile_candidate(client, session_factory):
    """Still queued (nobody stamped it — it has not ended), so only the
    candidate exclusion stands between the alert and a credited detection."""
    run_id = _launch(session_factory)
    (row,) = _results(session_factory)
    assert row.outcome is None  # unstamped: the run is still owed

    r = client.post(f"/api/runs/{run_id}/observations",
                    json={"observations": [_alert()]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["newly_matched"] == 0, body
    assert body["never_delivered"] is True
    assert "never collected" in body["warning"]
    (row,) = _results(session_factory)
    assert row.observed is False and row.observed_at is None


def test_delivered_run_is_still_matched_normally(client, session_factory):
    """The exclusion keys on delivery, not on mode: once the beacon collects
    the task, the same alert is evidence again."""
    from engine.orchestrator import orchestrator

    run_id = _launch(session_factory)

    async def _deliver():
        async with session_factory() as db:
            assert await orchestrator.dequeue_for_agent("agent-7", db) is not None

    asyncio.run(_deliver())
    r = client.post(f"/api/runs/{run_id}/observations",
                    json={"observations": [_alert(seconds_after=30)]})
    body = r.json()
    assert body["newly_matched"] == 1, body
    assert body["mttd"]["avg_seconds"] == 30.0


def test_a_complete_pull_run_from_before_the_delivery_column_stays_a_candidate():
    """``complete`` is only ever written by evidence that the run executed (a
    beacon's /complete, the classified beacon-lost path, an in-process
    dispatch). A ``complete`` pull row with ``delivered_at`` NULL therefore
    predates that column's migration and must stay reconcilable."""
    from types import SimpleNamespace

    from connectors.service import reconcile_candidates

    rows = [SimpleNamespace(step_id="step-01")]
    legacy = SimpleNamespace(mode="pull", delivered_at=None, status="complete",
                             channel_dispatch=None)
    assert reconcile_candidates(legacy, rows) == (rows, False)
    for status in ("pending", "running", "failed", "aborted"):
        queued = SimpleNamespace(mode="pull", delivered_at=None, status=status,
                                 channel_dispatch=None)
        assert reconcile_candidates(queued, rows) == ([], True), status
    push = SimpleNamespace(mode="push", delivered_at=None, status="staged",
                           channel_dispatch=None)
    assert reconcile_candidates(push, rows) == (rows, False)


def _register_integration(session_factory):
    from security.credentials import CredentialStore

    async def _go():
        async with session_factory() as db:
            await CredentialStore(db).put_integration(
                name="xsiam-lab", kind="xsiam", plaintext_secret="API_KEY",
                config={"fqdn": "api-t.xdr.us.paloaltonetworks.com", "api_key_id": "1"})
            await db.commit()

    asyncio.run(_go())


def _counting_fetcher(calls: list):
    def fetcher(method, url, headers, body, timeout):
        calls.append(url)
        return 200, json.dumps({"reply": {"alerts": [{
            "alert_id": "a1", "name": "LSASS memory access",
            "host_name": HOST, "severity": "SEV_040_HIGH",
            "detection_timestamp": int((T0 + timedelta(seconds=60)).timestamp() * 1000),
            "mitre_technique_ids": ["T1003.001"],
        }]}})
    return fetcher


def test_undelivered_pull_run_reconcile_makes_no_tenant_call(session_factory):
    from connectors.service import reconcile_run
    from models import Result, Run

    run_id = _launch(session_factory)
    _register_integration(session_factory)
    calls: list = []

    async def _go():
        async with session_factory() as db:
            run = (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one()
            results = list((await db.execute(select(Result))).scalars().all())
            return await reconcile_run(db, run, results, connector_kind="xsiam",
                                       fetcher=_counting_fetcher(calls))

    out = asyncio.run(_go())
    assert calls == []  # no metered call spent on a run that never ran
    assert out.newly_matched == 0
    assert out.summary["never_delivered"] is True


def test_auto_sweep_skips_never_delivered_pull_runs(session_factory):
    from connectors.service import auto_reconcile_once

    _launch(session_factory)
    _register_integration(session_factory)
    calls: list = []

    async def _go():
        async with session_factory() as db:
            return await auto_reconcile_once(
                db, lookback_seconds=10 ** 9, window_seconds=3600,
                fetcher=_counting_fetcher(calls))

    stats = asyncio.run(_go())
    assert calls == []
    assert stats["runs_considered"] == 0
    assert stats["skipped"].get("never_delivered") == 1, stats


# ---------------------------------------------------------------------------
# 3. The other terminating paths stamp too
# ---------------------------------------------------------------------------


def test_abort_before_delivery_stamps_not_executed(client, session_factory):
    run_id = _launch(session_factory)
    r = client.post(f"/api/runs/{run_id}/abort")
    assert r.status_code == 200 and r.json()["status"] == "aborted"
    (row,) = _results(session_factory)
    assert (row.outcome, row.outcome_reason) == ("not_executed", "ABORTED_BEFORE_DELIVERY")


def test_abort_after_delivery_does_not_claim_nothing_ran(client, session_factory):
    from engine.orchestrator import orchestrator

    run_id = _launch(session_factory)

    async def _deliver():
        async with session_factory() as db:
            await orchestrator.dequeue_for_agent("agent-7", db)

    asyncio.run(_deliver())
    client.post(f"/api/runs/{run_id}/abort")
    (row,) = _results(session_factory)
    assert row.outcome is None  # the beacon's own /complete steps[] owns this row


def test_unclassifiable_beacon_lost_run_stamps_not_executed(session_factory):
    """beacon_lost's unclassified branch: delivered, agent offline, and nothing
    to say which step the beacon died in (no scenario row, rows carry no
    step id). The run is failed and its rows can never be credited."""
    from engine.beacon_lost import terminalise_lost_runs
    from models import Agent, Result, Run

    now = datetime.utcnow()

    async def _go():
        async with session_factory() as db:
            db.add(Agent(agent_id="gone-1", hostname="h", os="linux", capabilities=[],
                         interpreters=[], registered_at=now - timedelta(hours=1),
                         last_seen=now - timedelta(hours=1), status="offline"))
            db.add(Run(run_id="lost-1", scenario_id="SIM-NOT-LOADED", mode="pull",
                       target="gone-1", status="running", open_tasks=1,
                       started_at=now - timedelta(hours=1),
                       delivered_at=now - timedelta(hours=1)))
            db.add(Result(run_id="lost-1", step_id=None, plane="EDR", signal_type="BIOC",
                          expected_detection="d", executed_at=now - timedelta(hours=1)))
            await db.commit()
            done = await terminalise_lost_runs(db, "gone-1", now, after_seconds=180)
            run = (await db.execute(select(Run))).scalar_one()
            row = (await db.execute(select(Result))).scalar_one()
            return done, run, row

    done, run, row = asyncio.run(_go())
    assert done == ["lost-1"]
    assert run.status == "failed"
    assert (row.outcome, row.outcome_reason) == ("not_executed", "BEACON_LOST_UNCLASSIFIED")
