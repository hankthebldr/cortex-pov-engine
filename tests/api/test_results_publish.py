"""Test that PUT /api/results/{id}/validate publishes a result.observed event.

The validate handler must fan a ``result.observed`` bus event out to the
run-scoped stream so the live SSE shows detection confirmations in real time.
We subscribe to the module-level ``event_bus`` for the run before validating,
then assert the published event's shape (type/run_id/data).

The TestClient runs the async handler on its own loop, but ``event_bus``
queues are created on whatever loop calls ``subscribe``. To keep the
subscriber queue and the handler's ``put_nowait`` on the same loop, we drive
the whole flow (seed + subscribe + validate-via-handler + drain) inside one
``asyncio.run`` rather than the TestClient.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest


def test_validate_publishes_result_observed_event(memory_db):
    get_db_override, SessionLocal = memory_db

    async def scenario():
        from api.results import validate_result, ValidateRequest
        from events import event_bus
        from models import Result, Run

        run_id = "pub-run"

        # Seed a run + one unobserved result.
        async with SessionLocal() as db:
            db.add(
                Run(
                    run_id=run_id,
                    scenario_id="SIM-EDR-001",
                    mode="pull",
                    status="running",
                    started_at=datetime.utcnow() - timedelta(seconds=60),
                )
            )
            db.add(
                Result(
                    run_id=run_id,
                    step_id="step-01",
                    step_name="s1",
                    plane="EDR",
                    signal_type="Analytics",
                    expected_detection="d1",
                    observed=False,
                    executed_at=datetime.utcnow() - timedelta(seconds=30),
                )
            )
            await db.commit()
            from sqlalchemy import select
            res = await db.execute(select(Result).where(Result.run_id == run_id))
            result_id = res.scalars().first().id

        # Subscribe to the run-scoped bus on THIS loop.
        q = event_bus.subscribe(run_id)
        try:
            # Call the handler directly with a fresh session.
            async with SessionLocal() as db:
                await validate_result(
                    result_id=result_id,
                    body=ValidateRequest(observed=True, notes="seen"),
                    db=db,
                )

            evt = await asyncio.wait_for(q.get(), timeout=2.0)
        finally:
            event_bus.unsubscribe(run_id, q)

        assert evt["type"] == "result.observed"
        assert evt["run_id"] == run_id
        assert evt["data"]["result_id"] == result_id
        assert evt["data"]["observed"] is True
        assert evt["data"]["plane"] == "EDR"
        # mttd_seconds is computed (executed_at ~30s before observed_at).
        assert evt["data"]["mttd_seconds"] is not None
        assert evt["data"]["mttd_seconds"] > 0

    asyncio.run(scenario())
