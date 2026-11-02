"""Residual R5: a DC's manual validation is a visible, bounded attestation.

``PUT /api/results/{id}/validate`` used to set only ``observed`` / ``observed_at``.
Once a reconcile had written an ``outcome``, "Mark detected" on a ``pending`` or
``missed`` row kept rendering Pending/Missed while ``/api/results`` counted it a
hit -- the DC's click looked ignored. The attestation now writes the outcome, and
is refused (409) where it would be a false claim: a step that never ran, or a
two-witness ``prevented`` row.
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


def _seed(session_factory, *, outcome, reason=None, observed=False, expected="detect"):
    from models import Result, Run

    executed = datetime.utcnow() - timedelta(minutes=30)

    async def _go():
        async with session_factory() as db:
            if (await db.execute(select(Run).where(Run.run_id == "att-run"))).scalar_one_or_none() is None:
                db.add(Run(run_id="att-run", scenario_id="SIM-EDR-001", mode="pull",
                           status="complete", started_at=executed))
            r = Result(run_id="att-run", step_id="step-01", plane="EDR", signal_type="BIOC",
                       expected_detection="d", executed_at=executed, expected_outcome=expected,
                       outcome=outcome, outcome_reason=reason, observed=observed,
                       observed_at=executed + timedelta(seconds=40) if observed else None)
            db.add(r)
            await db.commit()
            return r.id

    return asyncio.run(_go())


def _row(session_factory, rid):
    from models import Result

    async def _go():
        async with session_factory() as db:
            return (await db.execute(select(Result).where(Result.id == rid))).scalar_one()

    return asyncio.run(_go())


@pytest.mark.parametrize("outcome,reason", [
    (None, None),
    ("pending", "PREVENTION_UNCONFIRMED_NO_ALERT"),
    ("missed", None),
])
def test_mark_detected_writes_the_outcome(client, session_factory, outcome, reason):
    rid = _seed(session_factory, outcome=outcome, reason=reason)
    r = client.put(f"/api/results/{rid}/validate", json={"observed": True})
    assert r.status_code == 200, r.text
    assert (r.json()["outcome"], r.json()["outcome_reason"], r.json()["observed"]) == (
        "detected", None, True)
    row = _row(session_factory, rid)
    assert (row.outcome, row.observed) == ("detected", True)
    assert row.observed_at is not None  # the attestation provenance: no matched alert


@pytest.mark.parametrize("outcome,reason,code", [
    ("not_reached", "UPSTREAM_SEVERED", "RESULT_STEP_NOT_RUN"),
    ("not_executed", "TOOL_ABSENT", "RESULT_STEP_NOT_RUN"),
])
def test_cannot_attest_a_step_that_never_ran(client, session_factory, outcome, reason, code):
    rid = _seed(session_factory, outcome=outcome, reason=reason)
    for observed in (True, False):
        r = client.put(f"/api/results/{rid}/validate", json={"observed": observed})
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == code
    row = _row(session_factory, rid)
    assert (row.outcome, row.outcome_reason, row.observed) == (outcome, reason, False)


def test_an_attestation_never_downgrades_a_two_witness_prevented(client, session_factory):
    rid = _seed(session_factory, outcome="prevented", observed=True, expected="prevent")
    r = client.put(f"/api/results/{rid}/validate", json={"observed": False})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "RESULT_PREVENTED"
    assert (_row(session_factory, rid).outcome, _row(session_factory, rid).observed) == (
        "prevented", True)


def test_mark_detected_on_an_unobserved_prevented_row_is_refused(client, session_factory):
    rid = _seed(session_factory, outcome="prevented", observed=False, expected="prevent")
    r = client.put(f"/api/results/{rid}/validate", json={"observed": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "RESULT_PREVENTED"


def test_saving_a_note_on_a_prevented_row_is_not_an_attestation(client, session_factory):
    # The console's "save notes" re-asserts observed=true. On a prevented row
    # that changes nothing but the note.
    rid = _seed(session_factory, outcome="prevented", observed=True, expected="prevent")
    r = client.put(f"/api/results/{rid}/validate", json={"observed": True, "notes": "BTP"})
    assert r.status_code == 200, r.text
    row = _row(session_factory, rid)
    assert (row.outcome, row.observed, row.notes) == ("prevented", True, "BTP")


# ── Round 3 R5b — observed=false only retracts an operator attestation ────

def _add_observation(session_factory, rid):
    """The provenance an engine match leaves: an Observation row for the result."""
    from models import Observation

    async def _go():
        async with session_factory() as db:
            db.add(Observation(run_id="att-run", result_id=rid, source="xsiam",
                               external_id="alert-1", name="matched alert"))
            await db.commit()
    asyncio.run(_go())


def test_retracting_an_engine_detection_is_refused_and_changes_nothing(client, session_factory):
    """Round 4 minor: an engine-matched row carries no attestation to retract.
    409 NOT_AN_ATTESTATION, and the matched alert's MTTD survives."""
    rid = _seed(session_factory, outcome="detected", observed=True)
    _add_observation(session_factory, rid)
    before = _row(session_factory, rid)
    r = client.put(f"/api/results/{rid}/validate", json={"observed": False})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "NOT_AN_ATTESTATION"
    after = _row(session_factory, rid)
    assert (after.outcome, after.observed, after.observed_at) == (
        "detected", True, before.observed_at)


def test_retracting_an_attestation_reverts_it_to_unjudged(client, session_factory):
    rid = _seed(session_factory, outcome="pending", reason="EVIDENCE_INCOMPLETE")
    client.put(f"/api/results/{rid}/validate", json={"observed": True})
    r = client.put(f"/api/results/{rid}/validate", json={"observed": False})
    assert r.status_code == 200, r.text
    assert (r.json()["outcome"], r.json()["outcome_reason"], r.json()["observed"],
            r.json()["observed_at"]) == (None, None, False, None)


@pytest.mark.parametrize("outcome,reason", [
    ("missed", None), ("pending", "EVIDENCE_INCOMPLETE"), (None, None)])
def test_a_retraction_never_writes_missed_or_moves_an_engine_outcome(
        client, session_factory, outcome, reason):
    rid = _seed(session_factory, outcome=outcome, reason=reason)
    r = client.put(f"/api/results/{rid}/validate", json={"observed": False})
    assert r.status_code == 200, r.text
    assert (r.json()["outcome"], r.json()["outcome_reason"]) == (outcome, reason)


def test_attested_row_counts_once_and_renders_detected(client, session_factory):
    rid = _seed(session_factory, outcome="pending", reason="PREVENTION_UNCONFIRMED_NO_ALERT")
    client.put(f"/api/results/{rid}/validate", json={"observed": True})
    cov = client.get("/api/results/att-run").json()["coverage"]
    assert (cov["observed_in_denominator"], cov["denominator"]) == (1, 1)


def test_findings_name_not_reached_rows_as_severed_not_pending():
    """Round 4 minor: a not_reached row is never "pending DC validation"."""
    from engine.report_generator import _findings_paragraph

    text = _findings_paragraph([
        {"outcome": "detected", "observed": True},
        {"outcome": "not_reached", "outcome_reason": "UPSTREAM_SEVERED", "observed": False},
        {"outcome": "not_executed", "outcome_reason": "TOOL_ABSENT", "observed": False},
        {"outcome": None, "observed": False},
    ])
    assert text == ("1 alert(s) fired as expected; 1 not reached (chain severed); "
                    "1 not executed; 1 pending DC validation.")
