"""R1: the in-flight step is the FIRST scenario step with no StepOutcome row.

/output writes a row only after a step ends, so the step the beacon died in has
no row yet; blaming the latest ``started_at`` blames the step before it.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from engine.beacon_lost import pick_severed
from engine.host_evidence import apply_host_outcomes

IDS = ["step-01", "step-02", "step-03"]


def _row(sid, term=None):
    return NS(step_id=sid, started_at="t", termination=term, reason=None)


def test_no_rows_blames_the_first_step():
    assert pick_severed([], IDS) == "step-01"


def test_row_for_step_01_only_blames_step_02():
    assert pick_severed([_row("step-01")], IDS) == "step-02"


def test_rows_for_every_step_blames_the_last():
    assert pick_severed([_row(s) for s in IDS], IDS) == "step-03"


def test_first_gap_wins_over_later_rows():
    assert pick_severed([_row("step-01"), _row("step-03")], IDS) == "step-02"


def test_a_witnessed_termination_stands_over_the_first_gap():
    rows = [_row("step-01"), _row("step-02", "anchor_terminated")]
    assert pick_severed(rows, IDS) == "step-02"


def test_no_scenario_steps_is_unclassifiable():
    assert pick_severed([], []) is None


def test_beacon_lost_prevent_result_is_pending_beacon_lost_reason():
    """outcome.py maps beacon_lost on a prevent expectation to pending /
    PREVENTION_UNCONFIRMED_BEACON_LOST; apply_host_outcomes must carry it."""
    res = NS(step_id="step-02", outcome=None, outcome_reason=None, kpi_verdict=None,
             expected_outcome="prevent")
    apply_host_outcomes([res], [_row("step-02", "beacon_lost")], agent_profile_mode="block")
    assert (res.outcome, res.outcome_reason) == ("pending", "PREVENTION_UNCONFIRMED_BEACON_LOST")
