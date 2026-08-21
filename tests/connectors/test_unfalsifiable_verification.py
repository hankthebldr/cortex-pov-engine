"""A verification check that cannot fail cannot pass either.

`verifier._expected_rows_min` used to return the FIRST `purpose == "validation"`
query on the result's TTP card regardless of which detection was being verified.
For seven cards that first entry is an "MTTD Anchor Bookkeeping" query declaring
`expected_rows_min: 0`, and `verify_results` scores `PASS if rows >= want`.
`rows >= 0` is true for every possible tenant response — including a healthy
tenant that ingests nothing, an empty dataset, and a query against a dataset the
customer does not own.

So 14 of the 34 seeded results carrying a verification_xql were UNFALSIFIABLE:
a tenant returning `{"status":"SUCCESS","results":{"data":[]}}` scored a
machine-certified `pass` with `verified_at` stamped. That is the precise claim a
DC would repeat to a customer, manufactured by a query-selection bug.

This is the same defect `assertions.py`'s A-17 rejects at load time — "this
check can never fail and therefore proves nothing". The scenario verification
path simply never had the rule.
"""
from __future__ import annotations

import pytest

from engine.verifier import _MIN_MEANINGFUL_ROWS, _expected_rows_min


class _Result:
    def __init__(self, ttp_ref=None, detection_id=None):
        self.ttp_ref = ttp_ref
        self.detection_id = detection_id


def test_a_zero_floor_is_clamped_not_honoured(monkeypatch):
    """expected_rows_min: 0 would make the check unconditionally true."""
    from engine import ttp_catalog

    monkeypatch.setattr(ttp_catalog.catalog, "raw", lambda ref: {
        "detections": {"xql_queries": [
            {"purpose": "validation", "detection_id": "d-1", "expected_rows_min": 0},
        ]},
    })
    assert _expected_rows_min(_Result("TTP-X", "d-1")) == _MIN_MEANINGFUL_ROWS


def test_the_query_is_matched_on_the_results_own_detection_id(monkeypatch):
    """The bug: 'the first validation query' is not 'this detection's query'."""
    from engine import ttp_catalog

    monkeypatch.setattr(ttp_catalog.catalog, "raw", lambda ref: {
        "detections": {"xql_queries": [
            # An MTTD anchor bookkeeping row — the shape that poisoned 7 cards.
            {"purpose": "validation", "detection_id": "anchor", "expected_rows_min": 0},
            {"purpose": "validation", "detection_id": "real-one", "expected_rows_min": 3},
        ]},
    })
    assert _expected_rows_min(_Result("TTP-X", "real-one")) == 3
    # And the anchor itself still cannot be scored against a floor of 0.
    assert _expected_rows_min(_Result("TTP-X", "anchor")) == _MIN_MEANINGFUL_ROWS


def test_no_matching_query_and_several_candidates_does_not_pick_arbitrarily(monkeypatch):
    """With several validation queries and no id match, 'the first' scores one
    detection against another detection's threshold. Fall back to the floor."""
    from engine import ttp_catalog

    monkeypatch.setattr(ttp_catalog.catalog, "raw", lambda ref: {
        "detections": {"xql_queries": [
            {"purpose": "validation", "detection_id": "a", "expected_rows_min": 7},
            {"purpose": "validation", "detection_id": "b", "expected_rows_min": 9},
        ]},
    })
    assert _expected_rows_min(_Result("TTP-X", "unrelated")) == _MIN_MEANINGFUL_ROWS


def test_a_single_unambiguous_validation_query_is_still_used(monkeypatch):
    """Back-compat: one query, no ambiguity, so the card-level floor applies."""
    from engine import ttp_catalog

    monkeypatch.setattr(ttp_catalog.catalog, "raw", lambda ref: {
        "detections": {"xql_queries": [
            {"purpose": "validation", "expected_rows_min": 5},
        ]},
    })
    assert _expected_rows_min(_Result("TTP-X", "anything")) == 5


@pytest.mark.parametrize("raw", [None, {}, {"detections": {}}, {"detections": {"xql_queries": []}}])
def test_missing_card_data_falls_back_to_the_meaningful_floor(monkeypatch, raw):
    from engine import ttp_catalog

    monkeypatch.setattr(ttp_catalog.catalog, "raw", lambda ref: raw)
    assert _expected_rows_min(_Result("TTP-X", "d")) == _MIN_MEANINGFUL_ROWS


def test_no_corpus_card_can_produce_an_unfalsifiable_floor():
    """Sweep the REAL corpus: no result can end up with a floor below 1."""
    import os

    from engine.ttp_catalog import catalog

    base = os.environ.get("CORTEXSIM_BASE_DIR", ".")
    catalog.load(os.path.join(base, "detection_scanner", "ttps"))
    zero_floors = []
    for ttp_id, raw in catalog.all_raw().items():
        if not isinstance(raw, dict):
            continue
        for q in (raw.get("detections") or {}).get("xql_queries") or []:
            if not isinstance(q, dict) or q.get("purpose") != "validation":
                continue
            got = _expected_rows_min(_Result(ttp_id, q.get("detection_id")))
            if got < _MIN_MEANINGFUL_ROWS:
                zero_floors.append(f"{ttp_id}/{q.get('detection_id')}")
    assert not zero_floors, (
        "these results would score PASS on a tenant returning zero rows:\n  "
        + "\n  ".join(zero_floors)
    )
