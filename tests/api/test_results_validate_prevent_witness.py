"""Round 3 R5c: a prevention is never hand-marked.

``PUT /validate {observed:true}`` on a row whose ``expected_outcome`` is
``prevent`` (any mode) is refused: a manual attestation cannot stand in for the
alert witness. Re-asserting on an already-observed prevent row only updates the
note (the console's save-notes call).
"""
from __future__ import annotations

import pytest

from tests.api.test_results_validate_attestation import _row, _seed, client  # noqa: F401


@pytest.mark.parametrize("outcome,reason", [
    (None, None), ("pending", "PREVENTION_UNCONFIRMED_NO_ALERT"), ("missed", None)])
def test_mark_detected_on_a_prevent_expectation_is_refused(client, session_factory,
                                                           outcome, reason):
    rid = _seed(session_factory, outcome=outcome, reason=reason, expected="prevent")
    r = client.put(f"/api/results/{rid}/validate", json={"observed": True})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "ATTESTATION_NOT_A_WITNESS"
    row = _row(session_factory, rid)
    assert (row.outcome, row.observed) == (outcome, False)


def test_saving_a_note_on_an_observed_prevent_row_is_not_an_attestation(client, session_factory):
    rid = _seed(session_factory, outcome="pending",
                reason="PREVENTION_ALERT_WITHOUT_TERMINATION", observed=True, expected="prevent")
    r = client.put(f"/api/results/{rid}/validate", json={"observed": True, "notes": "seen"})
    assert r.status_code == 200, r.text
    row = _row(session_factory, rid)
    assert (row.outcome, row.outcome_reason, row.notes) == (
        "pending", "PREVENTION_ALERT_WITHOUT_TERMINATION", "seen")
