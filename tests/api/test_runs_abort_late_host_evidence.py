"""An aborted run keeps the host's word on which steps ran (RG-RUNS-01).

Observed on c852eac, through the real routes. A DC aborts a delivered
three-step pull run during step-02. The beacon kills the step and POSTs
``/complete`` with what happened on the host:

    step-01 exited · step-02 aborted · step-03 not_run / UPSTREAM_FAILED

SimCore answered ``409 RUN_ALREADY_TERMINAL`` and applied **nothing** from that
request, steps[] included. Every Result stayed at ``outcome`` NULL, which
everywhere else means "pending, awaiting reconcile". One alert from the same
host that matched step-03's expectation then read

    step-03  observed=True  outcome=detected  mttd=5.0 s

for a step that never ran, on a run whose status is ``aborted``. The comment in
``abort_run`` said a delivered run is left alone *because* "the beacon's own
/complete steps[] says which steps ran" — and that was the evidence being
thrown away.

The fix is narrow: a late ``/complete`` on a run that is ``aborted`` AND was
delivered still records ``steps[]`` as host evidence and projects it onto the
Results (``not_run`` -> ``not_reached`` / ``not_executed``, which
``apply_verdicts`` never credits). Status, ``completed_at``, the summary, the
terminal SSE frame and the score are untouched, and the answer is still 409 —
the signal a retrying beacon stops on.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from tests.api import _reconcile_flow as flow

HOST = "rg01-host"
AGENT = "ag-rg01"

#: What agent/beacon/step_outcome.go reports after an operator abort in step-02
#: (recordAborted + markRemaining).
BEACON_COMPLETE_AFTER_ABORT = {
    "exit_code": 130, "summary": "aborted by operator",
    "steps": [
        {"step_id": "step-01", "exit_code": 0, "termination": "exited"},
        {"step_id": "step-02", "exit_code": 130, "termination": "aborted"},
        {"step_id": "step-03", "termination": "not_run", "reason": "UPSTREAM_FAILED"},
    ],
}


@pytest.fixture
def client(make_client):
    return flow.build_client(make_client)


@pytest.fixture(autouse=True)
def _clean():
    flow.reset_orchestrator()
    yield
    flow.reset_orchestrator()


def _run_row(session_factory, run_id: str):
    from models import Run

    async def _go():
        async with session_factory() as db:
            return (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one()

    return asyncio.run(_go())


def _abort_mid_step_two(client, session_factory) -> str:
    """Launch, deliver, start step-01, abort — the state the beacon then reports on."""
    flow.seed_scenarios(session_factory)
    flow.register(client, AGENT, HOST)
    run_id = flow.launch(client, agent_id=AGENT, scenario_id=flow.THREE_STEP)
    flow.collect_task(client, AGENT)
    r = client.post(f"/api/runs/{run_id}/output",
                    json={"step_id": "step-01", "output": "one\n"})
    assert r.status_code == 200, r.text
    a = client.post(f"/api/runs/{run_id}/abort")
    assert a.status_code == 200 and a.json()["status"] == "aborted", a.text
    return run_id


def test_a_step_the_abort_prevented_is_never_credited_an_alert(client, session_factory):
    run_id = _abort_mid_step_two(client, session_factory)

    late = client.post(f"/api/runs/{run_id}/complete", json=BEACON_COMPLETE_AFTER_ABORT)
    assert late.status_code == 409, late.text

    # The host said step-03 never ran. That is now on its Result.
    step3 = flow.rows(client, run_id)["step-03"]
    assert (step3["outcome"], step3["outcome_reason"]) == ("not_reached", "UPSTREAM_FAILED")

    # One alert, from the run's own host, matching ONLY step-03's expectation.
    o = flow.observe(client, run_id, [
        flow.alert("al-rg01-3", "Ransomware file encryption", "T1486", "d-3", HOST)])
    assert o.status_code == 200, o.text
    assert o.json()["observed"] == 0, o.json()

    step3 = flow.rows(client, run_id)["step-03"]
    assert flow.view(step3) == (False, "not_reached", "UPSTREAM_FAILED", False), step3
    assert flow.results_body(client, run_id)["mttd"] is None


def test_the_step_that_was_running_stays_creditable(client, session_factory):
    """step-02 DID start; an alert it raised before the kill is real evidence."""
    run_id = _abort_mid_step_two(client, session_factory)
    client.post(f"/api/runs/{run_id}/complete", json=BEACON_COMPLETE_AFTER_ABORT)

    o = flow.observe(client, run_id, [
        flow.alert("al-rg01-2", "Suspicious shell spawn", "T1059", "d-2", HOST)])
    assert o.status_code == 200, o.text
    got = flow.rows(client, run_id)
    assert flow.view(got["step-02"])[:2] == (True, "detected"), got["step-02"]
    assert got["step-03"]["outcome"] == "not_reached"


def test_the_late_complete_changes_nothing_but_the_host_evidence(client, session_factory):
    run_id = _abort_mid_step_two(client, session_factory)
    before = _run_row(session_factory, run_id)

    late = client.post(f"/api/runs/{run_id}/complete", json=BEACON_COMPLETE_AFTER_ABORT)

    assert late.status_code == 409, late.text
    detail = late.json()["detail"]
    assert detail["code"] == "RUN_ALREADY_TERMINAL"
    assert detail["detail"]["status"] == "aborted"
    # The reply says what WAS kept, so "nothing applied" is no longer claimed.
    assert detail["detail"]["host_evidence_recorded"] == 3

    after = _run_row(session_factory, run_id)
    assert after.status == "aborted"
    assert after.completed_at == before.completed_at
    assert after.tc_verdict == before.tc_verdict
    assert "COMPLETION SUMMARY" not in (after.output or "")
    assert (after.output or "") == (before.output or "")

    outcomes = {s["step_id"]: s["termination"]
                for s in client.get(f"/api/runs/{run_id}").json()["step_outcomes"]}
    assert outcomes == {"step-01": "exited", "step-02": "aborted", "step-03": "not_run"}


def test_a_retried_late_complete_is_idempotent(client, session_factory):
    run_id = _abort_mid_step_two(client, session_factory)
    first = client.post(f"/api/runs/{run_id}/complete", json=BEACON_COMPLETE_AFTER_ABORT)
    again = client.post(f"/api/runs/{run_id}/complete", json=BEACON_COMPLETE_AFTER_ABORT)
    assert first.status_code == again.status_code == 409
    got = flow.rows(client, run_id)
    assert got["step-03"]["outcome"] == "not_reached"
    assert got["step-01"]["outcome"] is None


def test_a_finished_run_still_takes_nothing_from_a_late_complete(client, session_factory):
    """The widening is for ``aborted`` only: a completed run's record is closed."""
    flow.seed_scenarios(session_factory)
    flow.register(client, AGENT, HOST)
    run_id = flow.launch(client, agent_id=AGENT, scenario_id=flow.THREE_STEP)
    flow.collect_task(client, AGENT)
    ok = client.post(f"/api/runs/{run_id}/complete", json={
        "exit_code": 0, "summary": "ok", "steps": [
            {"step_id": s, "exit_code": 0, "termination": "exited"}
            for s in ("step-01", "step-02", "step-03")]})
    assert ok.status_code == 200, ok.text

    late = client.post(f"/api/runs/{run_id}/complete", json=BEACON_COMPLETE_AFTER_ABORT)

    assert late.status_code == 409
    assert late.json()["detail"]["detail"]["host_evidence_recorded"] == 0
    assert flow.rows(client, run_id)["step-03"]["outcome"] is None
    outcomes = {s["step_id"]: s["termination"]
                for s in client.get(f"/api/runs/{run_id}").json()["step_outcomes"]}
    assert outcomes["step-03"] == "exited"


def test_an_abort_before_delivery_takes_no_host_evidence(client, session_factory):
    """No beacon collected the task, so nothing calling itself that run's beacon
    has evidence to give; the rows keep ``ABORTED_BEFORE_DELIVERY``."""
    flow.seed_scenarios(session_factory)
    flow.register(client, AGENT, HOST)
    run_id = flow.launch(client, agent_id=AGENT, scenario_id=flow.THREE_STEP)
    assert client.post(f"/api/runs/{run_id}/abort").json()["status"] == "aborted"

    late = client.post(f"/api/runs/{run_id}/complete", json=BEACON_COMPLETE_AFTER_ABORT)

    assert late.status_code == 409
    assert late.json()["detail"]["detail"]["host_evidence_recorded"] == 0
    got = flow.rows(client, run_id)
    assert {r["outcome_reason"] for r in got.values()} == {"ABORTED_BEFORE_DELIVERY"}
    assert client.get(f"/api/runs/{run_id}").json()["step_outcomes"] == []


def test_the_aborted_steps_started_row_takes_the_hosts_termination(client, session_factory):
    """The lab shape (pov-target-01, SimCore at d7f4f29, before this fix).

    A one-step ``sleep 600`` was aborted after ~6 s; the run record showed
    ``exit_code=130`` but the step's ``step_outcomes`` row, created at its
    first ``/output`` (``started_at`` only), kept termination, signal and rc
    NULL, because the beacon's ``/complete`` met 409 and nothing was applied.
    The late ``/complete`` now fills that same row; ``started_at`` survives.
    """
    from models import StepOutcome

    run_id = _abort_mid_step_two(client, session_factory)  # step-01 has a started row
    late = client.post(f"/api/runs/{run_id}/complete", json={
        "exit_code": 130, "summary": "aborted by operator",
        "steps": [
            {"step_id": "step-01", "exit_code": 130, "termination": "aborted"},
            {"step_id": "step-02", "termination": "not_run", "reason": "UPSTREAM_FAILED"},
            {"step_id": "step-03", "termination": "not_run", "reason": "UPSTREAM_FAILED"},
        ],
    })
    assert late.status_code == 409, late.text
    assert late.json()["detail"]["detail"]["host_evidence_recorded"] == 3

    async def _go():
        async with session_factory() as db:
            return (await db.execute(select(StepOutcome).where(
                StepOutcome.run_id == run_id, StepOutcome.step_id == "step-01"))).scalar_one()

    row = asyncio.run(_go())
    assert (row.termination, row.exit_code) == ("aborted", 130), (row.termination, row.exit_code)
    assert row.started_at is not None
