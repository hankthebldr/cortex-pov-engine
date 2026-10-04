"""PUT /api/results/{id}/validate must not move an existing observed_at.

``mttd_seconds`` is ``observed_at - executed_at`` — the only KPI the engine
measures natively, and the number in the POV report's MTTD table. The validate
handler stamped ``observed_at = utcnow()`` on EVERY ``observed: true`` call,
including on a row that was already observed. The console makes that call in
normal use: ResultsViewer's "save notes" sends ``validate(id, observed, notes)``
for an observed row. So:

* a row auto-validated by /reconcile with the ALERT's real fire time (MTTD
  45 s) had that evidence replaced by the moment a DC typed a note (MTTD of
  minutes or hours), flipping a ``<= 300 s`` threshold from pass to fail on
  the next score;
* a double-click or retry on a manual validation inflated MTTD by the gap.

Re-asserting ``observed: true`` is idempotent: notes update, the timestamp does
not. Un-observing and re-observing is a new observation and does re-stamp.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select


@pytest.fixture
def client(make_client):
    from api.results import router
    return make_client(router)


def _seed_result(session_factory, *, observed_after: float | None):
    from models import Result, Run

    executed = datetime.utcnow() - timedelta(minutes=30)

    async def _go():
        async with session_factory() as db:
            db.add(Run(run_id="mttd-run", scenario_id="SIM-EDR-001", mode="pull",
                       status="complete", started_at=executed))
            r = Result(
                run_id="mttd-run", step_id="step-01", plane="EDR", signal_type="BIOC",
                expected_detection="d", executed_at=executed,
                observed=observed_after is not None,
                observed_at=(executed + timedelta(seconds=observed_after)
                             if observed_after is not None else None),
                notes="auto-validated via reconcile" if observed_after is not None else None,
            )
            db.add(r)
            await db.commit()
            return r.id

    return asyncio.run(_go())


def _row(session_factory, result_id):
    from models import Result

    async def _go():
        async with session_factory() as db:
            return (await db.execute(select(Result).where(Result.id == result_id))).scalar_one()

    return asyncio.run(_go())


def test_saving_a_note_keeps_the_evidence_backed_observed_at(client, session_factory):
    rid = _seed_result(session_factory, observed_after=45.0)

    r = client.put(f"/api/results/{rid}/validate",
                   json={"observed": True, "notes": "confirmed in XSIAM incident 4411"})
    assert r.status_code == 200
    body = r.json()
    assert body["mttd_seconds"] == pytest.approx(45.0), body
    assert body["notes"] == "confirmed in XSIAM incident 4411"
    assert _row(session_factory, rid).mttd_seconds == pytest.approx(45.0)


def test_repeating_a_manual_validation_does_not_inflate_mttd(client, session_factory):
    rid = _seed_result(session_factory, observed_after=None)

    first = client.put(f"/api/results/{rid}/validate", json={"observed": True}).json()
    second = client.put(f"/api/results/{rid}/validate", json={"observed": True}).json()
    assert first["observed_at"] is not None
    assert second["observed_at"] == first["observed_at"]
    assert second["mttd_seconds"] == first["mttd_seconds"]


def test_unobserve_then_observe_is_a_new_observation(client, session_factory):
    rid = _seed_result(session_factory, observed_after=45.0)

    cleared = client.put(f"/api/results/{rid}/validate", json={"observed": False}).json()
    assert cleared["observed_at"] is None and cleared["mttd_seconds"] is None
    again = client.put(f"/api/results/{rid}/validate", json={"observed": True}).json()
    assert again["observed_at"] is not None
    assert again["mttd_seconds"] > 45.0  # re-stamped at click time, not resurrected
