"""API tests for the connector framework: discovery, manual ingest, reconcile."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def client(make_client):
    from api.connectors import router, runs_reconcile_router
    return make_client(router, runs_reconcile_router)


def _seed_run_with_results(SessionLocal, run_id="run-conn-1"):
    from models import Result, Run

    async def _seed():
        async with SessionLocal() as s:
            t0 = datetime(2026, 6, 10, 12, 0, 0)
            s.add(Run(run_id=run_id, scenario_id="SIM-EDR-001", mode="pull",
                      status="complete", started_at=t0))
            s.add(Result(run_id=run_id, step_id="step-02", plane="EDR",
                         signal_type="BIOC",
                         expected_detection="Credential file access shadow read",
                         observed=False, executed_at=t0,
                         mitre_technique="T1003.001",
                         detection_id="bioc-edr-001-shadow-read"))
            s.add(Result(run_id=run_id, step_id="step-03", plane="EDR",
                         signal_type="XQL",
                         expected_detection="SSH key harvesting search",
                         observed=False, executed_at=t0,
                         mitre_technique="T1552.004",
                         detection_id="xql-edr-001-ssh-key"))
            await s.commit()
    asyncio.run(_seed())
    return run_id


def test_list_connectors_shows_xsiam_unconfigured(client):
    r = client.get("/api/connectors")
    assert r.status_code == 200
    kinds = {c["kind"]: c for c in r.json()["connectors"]}
    assert "xsiam" in kinds
    assert kinds["xsiam"]["configured"] is False
    assert kinds["xsiam"]["integrations"] == []


def test_manual_observation_ingest_auto_validates(client, session_factory):
    run_id = _seed_run_with_results(session_factory)
    # One observation that matches the first result on technique within window.
    obs = {
        "external_id": "alert-1", "name": "Shadow file credential read",
        "technique": "T1003.001",
        "observed_at": (datetime(2026, 6, 10, 12, 0, 30)).isoformat(),
        "severity": "high",
    }
    r = client.post(f"/api/runs/{run_id}/observations",
                    json={"observations": [obs]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ingested"] == 1
    assert body["newly_matched"] == 1
    assert body["observed"] == 1
    assert body["total_expected"] == 2
    assert body["coverage_pct"] == 50.0
    # MTTD computed from the real alert timestamp (30s).
    assert body["mttd"]["avg_seconds"] == 30.0
    assert body["verdicts"][0]["mttd_seconds"] == 30.0


def test_manual_ingest_unrelated_alert_no_match(client, session_factory):
    run_id = _seed_run_with_results(session_factory, run_id="run-conn-2")
    obs = {"external_id": "x", "name": "unrelated", "technique": "T9999",
           "observed_at": datetime(2026, 6, 10, 12, 0, 10).isoformat()}
    r = client.post(f"/api/runs/{run_id}/observations", json={"observations": [obs]})
    body = r.json()
    assert body["newly_matched"] == 0
    assert body["observed"] == 0


def test_observations_unknown_run_404(client):
    r = client.post("/api/runs/does-not-exist/observations",
                    json={"observations": []})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "RUN_NOT_FOUND"


def test_reconcile_unknown_connector_404(client, session_factory):
    run_id = _seed_run_with_results(session_factory, run_id="run-conn-3")
    r = client.post(f"/api/runs/{run_id}/reconcile?connector=bogus")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "CONNECTOR_NOT_FOUND"


def test_reconcile_no_integration_400(client, session_factory):
    run_id = _seed_run_with_results(session_factory, run_id="run-conn-4")
    r = client.post(f"/api/runs/{run_id}/reconcile?connector=xsiam")
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "NO_INTEGRATION"


def test_manual_ingest_publishes_result_observed_sse(session_factory):
    """The manual-import measurement-loop publish site (apply_verdicts, reached
    through the real ingest_observations handler) must fan a ``result.observed``
    SSE frame out to the run-scoped bus for each matched verdict.

    This is a DISTINCT publish site from the PUT /validate handler (covered by
    tests/api/test_results_publish.py). The body-only tests above never
    subscribe to the bus, so the reconcile/observations -> SSE contract is
    otherwise unasserted.

    ``asyncio.Queue`` subscriptions are event-loop-bound, so we cannot drive
    this through TestClient (its handler runs on a different loop than the one
    the test subscribes on). Instead we seed, subscribe, call the handler, and
    drain — all inside a single ``asyncio.run`` — mirroring
    test_results_publish.py::test_validate_publishes_result_observed_event.
    """
    run_id = _seed_run_with_results(session_factory, run_id="run-conn-sse")
    t0 = datetime(2026, 6, 10, 12, 0, 0)

    async def scenario():
        from sqlalchemy import select

        from api.connectors import ObservationsBody, ingest_observations
        from events import event_bus
        from models import Result

        # Resolve the id of the result that SHOULD match (step-02, T1003.001).
        async with session_factory() as db:
            res = await db.execute(
                select(Result).where(
                    Result.run_id == run_id,
                    Result.mitre_technique == "T1003.001",
                )
            )
            matched_result_id = res.scalars().first().id

        obs = {
            "external_id": "alert-1",
            "name": "Shadow file credential read",
            "technique": "T1003.001",
            "observed_at": (t0 + timedelta(seconds=30)).isoformat(),
            "severity": "high",
        }

        # Subscribe on THIS loop, BEFORE invoking the handler.
        q = event_bus.subscribe(run_id)
        try:
            async with session_factory() as db:
                summary = await ingest_observations(
                    run_id=run_id,
                    body=ObservationsBody(observations=[obs]),
                    db=db,
                )

            evt = await asyncio.wait_for(q.get(), timeout=2.0)

            # Exactly ONE frame — the second seeded result (T1552.004) does not
            # match the single observation, so no further frame is published.
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(q.get(), timeout=0.25)
        finally:
            event_bus.unsubscribe(run_id, q)

        assert evt["type"] == "result.observed"
        assert evt["run_id"] == run_id
        assert evt["data"]["result_id"] == matched_result_id
        assert evt["data"]["observed"] is True
        assert evt["data"]["plane"] == "EDR"
        # MTTD computed from executed_at (t0) vs observed_at (t0+30s).
        assert evt["data"]["mttd_seconds"] == 30.0
        assert evt["data"]["source"] == "manual-import"
        assert isinstance(evt["data"]["matched_on"], list)
        assert "technique" in evt["data"]["matched_on"]

        # Tie the SSE frame to the persisted mutation.
        assert summary["newly_matched"] == 1
        assert summary["observed"] == 1

    asyncio.run(scenario())
