"""Task 5 — query library rendering. Pure: dicts in, text out."""
from __future__ import annotations

import pytest

from engine.query_library import render


def scen(*cards):
    return {"scenario_id": "SIM-T-001", "steps": [
        {"expected_detections": [{"ttp_ref": c} for c in cards]}]}


def card(*qs):
    return {"detections": {"xql_queries": list(qs)}}


def q(name, purpose, query="dataset = d", dataset="d"):
    return {"name": name, "purpose": purpose, "dataset": dataset, "query": query}


def test_only_requested_purposes():
    cards = {"A": card(q("h", "hunt"), q("v", "validation", "dataset = d | limit 1"))}
    text = render(scen("A"), cards)
    assert "## h" in text and "## v" not in text


def test_empty_result_says_so():
    assert "# No hunt or investigation query" in render(scen("A"), {"A": card()})


def test_empty_result_names_requested_purposes():
    t = render(scen("A"), {"A": card()}, ("validation",))
    assert "# No validation query on this scenario's cards." in t
    assert "# No hunt" not in t


def test_query_text_is_verbatim():
    qt = "dataset = d\n| filter a = \"b\"\n|   comp count() as n"
    assert qt + "\n" in render(scen("A"), {"A": card(q("h", "hunt", qt))})


def test_header_uses_string_id_and_requested_order():
    t = render(scen("A"), {"A": card()}, ("metric", "hunt"))
    assert t.splitlines()[0] == "# SIM-T-001 — query library (metric, hunt)"
    assert "not run against any tenant" in t.splitlines()[1]


def test_cards_in_id_order_queries_in_card_order():
    cards = {"B": card(q("b1", "hunt"), q("b2", "hunt")), "A": card(q("a1", "hunt"))}
    t = render(scen("B", "A"), cards)
    assert t.index("## a1") < t.index("## b1") < t.index("## b2")


def test_block_metadata_line():
    t = render(scen("A"), {"A": card(q("h", "hunt", dataset="panw_ngfw_traffic_raw"))})
    assert "# card: A   purpose: hunt   dataset: panw_ngfw_traffic_raw" in t


def test_missing_card_is_named_not_dropped():
    t = render(scen("A", "GONE"), {"A": card(q("h", "hunt"))})
    assert "# card GONE: not found in the card catalog" in t
    assert "## h" in t


def test_only_missing_card_still_reads_as_no_query():
    t = render(scen("GONE"), {})
    assert "# card GONE: not found in the card catalog" in t
    assert "# No hunt or investigation query" in t


def test_blank_query_text_gets_block_with_note():
    t = render(scen("A"), {"A": card(q("h", "hunt", "   "))})
    assert "## h" in t and "# (query text is empty on the card)" in t


def test_malformed_shapes_raise():
    with pytest.raises(ValueError):
        render(scen("A"), {"A": {"detections": {"xql_queries": "nope"}}})
    with pytest.raises(ValueError):
        render({"scenario_id": "X", "steps": "nope"}, {})


def test_unknown_purpose_raises():
    with pytest.raises(ValueError):
        render(scen("A"), {"A": card()}, ("fun",))
