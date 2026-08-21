"""Cross-projection status-parity test (TC-06).

Three consumers each classify a seeded ``Result`` row into
``detected | missed | pending``:

* ``report_generator._status_from_result``  (report / detection-matrix face)
* ``efficacy_scorecard._status``            (CISO scorecard face)
* ``detection_storyline._entry_status``     (live presenter timeline face)

They are documented to *never disagree* — the seeded-Result denominator is one
truth projected three ways — but each was only unit-tested in isolation, so a
fix to one (e.g. handling ``observed_at`` set *without* ``observed`` =
reviewed-but-unfired) could silently diverge one face from the others.

This module feeds ONE shared row-set — a detected row, a reviewed-unfired row
(``observed_at`` present, ``observed`` false → a real ``missed`` gap), and a
pending row (neither) — through all three real classifiers and asserts every
one yields the SAME ``{detected, missed, pending}`` tally.

The classifiers are the actual subjects (imported, never re-implemented); the
only local logic is a vocabulary map from ``report_generator``'s
``ENABLED | DISABLED | PENDING`` labels onto the canonical triple.
"""
from __future__ import annotations

from collections import Counter

import pytest

from engine.detection_storyline import _entry_status
from engine.efficacy_scorecard import _status as scorecard_status
from engine.report_generator import _status_from_result


# ---------------------------------------------------------------------------
# The one shared row-set — the three canonical states, one row each.
# Rows are shaped like ``Result.to_dict()`` (only the status-relevant keys).
# ---------------------------------------------------------------------------

DETECTED_ROW = {
    "id": 1,
    "step_id": "step-01",
    "observed": True,
    "observed_at": "2026-07-11T12:00:05+00:00",
    "mttd_seconds": 5.0,
}
# Reviewed-but-unfired: the DC checked and the detection did NOT fire. An
# ``observed_at`` WITHOUT ``observed`` is the divergence trap TC-06 guards — it
# must read as ``missed`` in all three faces, never a catch.
REVIEWED_UNFIRED_ROW = {
    "id": 2,
    "step_id": "step-02",
    "observed": False,
    "observed_at": "2026-07-11T12:00:09+00:00",
    "mttd_seconds": None,
}
PENDING_ROW = {
    "id": 3,
    "step_id": "step-03",
    "observed": False,
    "observed_at": None,
    "mttd_seconds": None,
}

SHARED_ROWS = [DETECTED_ROW, REVIEWED_UNFIRED_ROW, PENDING_ROW]

# The expected projection of the shared row-set — the golden every face must hit.
EXPECTED_COUNTS = {"detected": 1, "missed": 1, "pending": 1}

# ``detection_storyline._entry_status`` is the only run-status-aware classifier:
# an un-reviewed detection is ``pending`` until the run reaches a terminal state.
# Feed a NON-terminal status so the pending row stays pending — the state the
# other two (which have no run-status concept) always assume for that row.
_NON_TERMINAL_RUN_STATUS = "running"

# ``report_generator`` speaks ENABLED/DISABLED/PENDING; translate to canonical.
_REPORT_LABELS = {"ENABLED": "detected", "DISABLED": "missed", "PENDING": "pending"}


def _report_status(row: dict) -> str:
    label = _status_from_result(bool(row.get("observed")), bool(row.get("observed_at")))
    return _REPORT_LABELS[label]


def _scorecard_status(row: dict) -> str:
    return scorecard_status(row)


def _storyline_status(row: dict) -> str:
    return _entry_status(row, _NON_TERMINAL_RUN_STATUS)


# (name, classifier) — each classifier is a real subject, output normalized to
# the ``detected | missed | pending`` triple.
CLASSIFIERS = [
    ("report_generator._status_from_result", _report_status),
    ("efficacy_scorecard._status", _scorecard_status),
    ("detection_storyline._entry_status", _storyline_status),
]


@pytest.mark.parametrize("name,classifier", CLASSIFIERS, ids=[c[0] for c in CLASSIFIERS])
def test_each_projection_matches_golden_counts(name, classifier):
    """Each of the three faces projects the shared row-set to the same tally."""
    counts = Counter(classifier(row) for row in SHARED_ROWS)
    assert dict(counts) == EXPECTED_COUNTS, (
        f"{name} diverged: {dict(counts)} != {EXPECTED_COUNTS}"
    )


def test_all_three_projections_are_mutually_equal():
    """Belt-and-suspenders: compute all three tallies and assert they are equal
    to each other (not merely each equal to the golden) — the direct statement
    of the 'three projections never diverge' invariant."""
    tallies = {
        name: dict(Counter(classifier(row) for row in SHARED_ROWS))
        for name, classifier in CLASSIFIERS
    }
    distinct = {tuple(sorted(t.items())) for t in tallies.values()}
    assert len(distinct) == 1, f"projections diverged: {tallies}"


@pytest.mark.parametrize(
    "row,expected",
    [
        (DETECTED_ROW, "detected"),
        (REVIEWED_UNFIRED_ROW, "missed"),
        (PENDING_ROW, "pending"),
    ],
    ids=["detected", "reviewed-unfired", "pending"],
)
def test_every_classifier_agrees_per_row(row, expected):
    """Row-wise parity: for each row, all three classifiers return the same
    canonical status (so a divergence is pinned to the exact row + face)."""
    verdicts = {name: classifier(row) for name, classifier in CLASSIFIERS}
    assert set(verdicts.values()) == {expected}, (
        f"row {row.get('id')} expected all '{expected}', got {verdicts}"
    )
