"""Credited evidence is not undone by an uncredited alert, and an attestation
can be withdrawn (RG-RUNS-02, RG-RUNS-03, RG-RUNS-05).

Three defects on c852eac, all reproduced through the real routes.

**RG-RUNS-02.** A Windows push run scoped with ``host=`` read ``detected`` with
an MTTD. Re-importing alerts with ``reevaluate: true`` and no ``host`` then
rewrote the row to ``pending / UNSCOPED_MATCH`` and left ``observed``,
``observed_at`` and the MTTD on it: coverage 0.0 % beside "Average MTTD 5.0s".
An alert that cannot be attributed is not evidence AGAINST an earlier
attributed one.

**RG-RUNS-03.** Since the unscoped rule, ``apply_verdicts`` records an
``Observation`` for a candidate it deliberately does not credit, while
``validate_result`` still read ANY Observation as "an alert matched this row".
So on the documented push flow (unscoped reconcile -> rows pending -> the DC
clicks validate) a mis-click was permanent: the retraction was refused 409
``NOT_AN_ATTESTATION`` with a reason that was false for that row.

**RG-RUNS-05.** ``reconcile_candidates`` exempted a ``delivered_at``-NULL pull
run only when it read ``complete``. An in-place upgrade from v1.0.x leaves
every older run with ``delivered_at`` NULL, so one that executed and ended
``failed`` or ``aborted`` was reported "never delivered: nothing ran on the
target" — asserted as fact, and false.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from tests.api import _reconcile_flow as flow

SHADOW = ("Shadow file read", "T1003", "d-1")


@pytest.fixture
def client(make_client, session_factory):
    flow.seed_scenarios(session_factory)
    return flow.build_client(make_client)


@pytest.fixture(autouse=True)
def _clean():
    flow.reset_orchestrator()
    yield
    flow.reset_orchestrator()


def _row(client, run_id):
    return flow.rows(client, run_id)["step-01"]


def _report(client, run_id) -> str:
    return client.get(f"/api/runs/{run_id}/report", params={"format": "markdown"}).text


# ---------------------------------------------------------------------------
# RG-RUNS-02 — an unscoped batch never downgrades a credited row
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("second_alert", ["al-02-b", "al-02-a"],
                         ids=["a-different-alert", "the-same-alert-again"])
def test_unscoped_reevaluate_leaves_a_scoped_detection_alone(client, second_alert):
    run_id = flow.launch(client, mode="push")
    first = flow.observe(client, run_id, [flow.alert("al-02-a", *SHADOW, "win-01")],
                         host="win-01")
    assert first.json()["observed"] == 1, first.text
    before = _row(client, run_id)
    assert flow.view(before) == (True, "detected", None, True), before

    again = flow.observe(client, run_id,
                         [flow.alert(second_alert, *SHADOW, "win-01", plus=9)],
                         reevaluate=True)

    assert again.status_code == 200, again.text
    after = _row(client, run_id)
    assert flow.view(after) == (True, "detected", None, True), after
    assert after["observed_at"] == before["observed_at"]
    assert after["mttd_seconds"] == before["mttd_seconds"]
    summary = again.json()
    assert summary["observed"] == 1 and summary["coverage_pct"] == 100.0, summary
    assert not summary.get("unscoped_matches"), summary
    # The report never shows an MTTD beside zero coverage.
    assert "| BIOC | 1 | 0 | 1 | 100.0% |" in _report(client, run_id)


def test_summary_coverage_and_mttd_count_agree_after_an_unscoped_reevaluate(client):
    run_id = flow.launch(client, mode="push")
    flow.observe(client, run_id, [flow.alert("al-02-c", *SHADOW, "win-01")], host="win-01")
    flow.observe(client, run_id, [flow.alert("al-02-d", *SHADOW, "win-01", plus=9)],
                 reevaluate=True)
    body = flow.results_body(client, run_id)
    assert body["coverage"]["observed"] == 1
    assert body["mttd"] is not None and body["mttd"]["count"] == body["coverage"]["observed"]


def test_a_pull_detection_survives_the_agent_being_deleted(client):
    flow.register(client, "ag-02", "v2-host")
    run_id = flow.launch(client, agent_id="ag-02")
    flow.collect_task(client, "ag-02")
    done = client.post(f"/api/runs/{run_id}/complete", json={
        "exit_code": 0, "summary": "ok",
        "steps": [{"step_id": "step-01", "exit_code": 0, "termination": "exited"}]})
    assert done.status_code == 200, done.text
    flow.observe(client, run_id, [flow.alert("al-02-p", *SHADOW, "v2-host")])
    assert flow.view(_row(client, run_id))[:2] == (True, "detected")

    assert client.delete("/api/agents/ag-02").status_code == 200
    flow.observe(client, run_id, [flow.alert("al-02-q", *SHADOW, "v2-host", plus=9)],
                 reevaluate=True)

    assert flow.view(_row(client, run_id)) == (True, "detected", None, True)


def test_an_unscoped_candidate_is_still_withheld_on_an_uncredited_row(client):
    """The rule being protected: with no host, a match is a candidate only."""
    run_id = flow.launch(client, mode="push")
    r = flow.observe(client, run_id, [flow.alert("al-02-u", *SHADOW, "win-09")])
    assert r.json()["unscoped_matches"] == 1, r.text
    assert flow.view(_row(client, run_id)) == (False, "pending", "UNSCOPED_MATCH", False)


# ---------------------------------------------------------------------------
# RG-RUNS-03 — an attestation on a row with an uncredited candidate retracts
# ---------------------------------------------------------------------------


def _attest(client, result_id, observed: bool):
    return client.put(f"/api/results/{result_id}/validate", json={"observed": observed})


def test_an_attestation_over_an_unscoped_candidate_can_be_retracted(client):
    run_id = flow.launch(client, mode="push")
    flow.observe(client, run_id, [flow.alert("al-03-a", *SHADOW, "win-09")])
    row = _row(client, run_id)
    assert flow.view(row) == (False, "pending", "UNSCOPED_MATCH", False), row

    assert _attest(client, row["id"], True).status_code == 200
    assert flow.view(_row(client, run_id))[:3] == (True, "detected", None)

    retract = _attest(client, row["id"], False)

    assert retract.status_code == 200, retract.text
    # Back to what the engine had said: a candidate, not a credit, no MTTD.
    assert flow.view(_row(client, run_id)) == (False, "pending", "UNSCOPED_MATCH", False)


def test_retraction_survives_a_second_unscoped_import(client):
    """The old dead end: an unscoped reevaluate after the click left the row
    observed + pending, and the retraction still 409'd."""
    run_id = flow.launch(client, mode="push")
    flow.observe(client, run_id, [flow.alert("al-03-b", *SHADOW, "win-09")])
    rid = _row(client, run_id)["id"]
    _attest(client, rid, True)
    flow.observe(client, run_id, [flow.alert("al-03-b", *SHADOW, "win-09")], reevaluate=True)
    assert flow.view(_row(client, run_id))[:3] == (True, "detected", None)

    assert _attest(client, rid, False).status_code == 200
    assert flow.view(_row(client, run_id)) == (False, "pending", "UNSCOPED_MATCH", False)


def test_an_engine_matched_row_still_refuses_a_retraction(client):
    """The protection the 409 exists for is unchanged."""
    run_id = flow.launch(client, mode="push")
    flow.observe(client, run_id, [flow.alert("al-03-c", *SHADOW, "win-01")], host="win-01")
    row = _row(client, run_id)

    refused = _attest(client, row["id"], False)

    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "NOT_AN_ATTESTATION"
    assert _row(client, run_id) == row


def test_a_scoped_match_after_the_click_is_engine_evidence(client):
    """Candidate -> attested -> the same alert credited with a host: the row is
    now the engine's (the alert's time, not the click's), so no retraction."""
    run_id = flow.launch(client, mode="push")
    alert = flow.alert("al-03-d", *SHADOW, "win-09")
    flow.observe(client, run_id, [alert])
    rid = _row(client, run_id)["id"]
    _attest(client, rid, True)

    flow.observe(client, run_id, [alert], host="win-09", reevaluate=True)

    assert flow.view(_row(client, run_id)) == (True, "detected", None, True)
    assert _attest(client, rid, False).status_code == 409


def test_a_plain_attestation_still_retracts_to_unjudged(client):
    run_id = flow.launch(client, mode="push")
    rid = _row(client, run_id)["id"]
    _attest(client, rid, True)
    assert _attest(client, rid, False).status_code == 200
    assert flow.view(_row(client, run_id)) == (False, None, None, False)


# ---------------------------------------------------------------------------
# RG-RUNS-05 — a run from before delivered_at that visibly executed
# ---------------------------------------------------------------------------

BEACON_OUTPUT = ("=== STEP 1/1 · step-01 · a ===\nroot:x\n--- exit_code=1 ---\n"
                 "\n--- COMPLETION SUMMARY ---\nExit code: 1\nFAILED\n")


def _seed_legacy(session_factory, run_id: str, status: str, output: str) -> None:
    from models import Result, Run

    async def _go():
        async with session_factory() as db:
            t0 = datetime.utcnow() - timedelta(seconds=30)
            terminal = status in ("failed", "aborted", "complete")
            db.add(Run(run_id=run_id, scenario_id=flow.ONE_STEP, mode="pull",
                       target="ag-05", status=status, started_at=t0,
                       completed_at=t0 + timedelta(seconds=5) if terminal else None,
                       delivered_at=None, output=output))
            db.add(Result(run_id=run_id, step_id="step-01", step_name="a", plane="EDR",
                          signal_type="BIOC", expected_detection="Shadow file read",
                          detection_id="d-1", detection_name="Shadow file read",
                          mitre_technique="T1003", executed_at=t0))
            await db.commit()

    asyncio.run(_go())


def _legacy_alert(tag: str) -> dict:
    return {"external_id": f"al-05-{tag}", "name": "Shadow file read",
            "mitre_technique": "T1003", "detection_id": "d-1", "host": "v5-host",
            "observed_at": (datetime.utcnow() - timedelta(seconds=23)).isoformat() + "Z"}


@pytest.mark.parametrize("status", ["failed", "aborted", "complete"])
def test_a_legacy_run_that_executed_is_reconcilable(client, session_factory, status):
    flow.register(client, "ag-05", "v5-host")
    _seed_legacy(session_factory, f"legacy-{status}", status, BEACON_OUTPUT)

    r = client.post(f"/api/runs/legacy-{status}/observations",
                    json={"observations": [_legacy_alert(status)]})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["observed"] == 1 and "never_delivered" not in body, body
    assert "never delivered" not in (body.get("warning") or "")
    assert flow.view(_row(client, f"legacy-{status}")) == (True, "detected", None, True)


@pytest.mark.parametrize("status, output", [
    ("failed", "\n--- RUN FAILED ON RESTART — queued task was lost ---\n"),
    ("failed", None),
    ("aborted", "\n--- RUN ABORTED BY OPERATOR ---\n"),
    ("running", None),
    ("pending", None),
], ids=["orphan-marker-only", "failed-no-output", "aborted-before-delivery",
        "still-running", "still-queued"])
def test_a_run_with_no_beacon_trace_is_still_never_delivered(client, session_factory,
                                                             status, output):
    """The OPS-01 defence in depth is intact: no beacon frame in the run's own
    record, no credit."""
    flow.register(client, "ag-05", "v5-host")
    _seed_legacy(session_factory, "untouched", status, output)

    r = client.post("/api/runs/untouched/observations",
                    json={"observations": [_legacy_alert("x")]})

    body = r.json()
    assert body["observed"] == 0 and body["never_delivered"] is True, body
    assert flow.view(_row(client, "untouched")) == (False, None, None, False)


def test_beacon_trace_is_read_from_frames_not_from_any_text():
    """A step frame or a completion summary at the start of a line; prose that
    merely mentions them is not a trace."""
    from connectors.service import beacon_reported

    class R:
        output = None

    r = R()
    assert beacon_reported(r) is False
    r.output = "launch note: the beacon prints '=== STEP n/N' per step\n"
    assert beacon_reported(r) is False
    r.output = "!! PLATFORM VARIANT step-01\n=== STEP 1/3 · step-01 · a ===\n"
    assert beacon_reported(r) is True
    r.output = "x\n--- COMPLETION SUMMARY ---\nExit code: 0\n"
    assert beacon_reported(r) is True
