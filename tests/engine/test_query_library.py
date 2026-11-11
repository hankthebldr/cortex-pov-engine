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
    cards = {"B": card(q("zeta", "hunt"), q("alpha", "hunt")), "A": card(q("mid", "hunt"))}
    t = render(scen("B", "A"), cards)
    assert t.index("## mid") < t.index("## zeta") < t.index("## alpha")


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


BREAKS = ["\n", "\r", "\v", "\f", "\x85", "\u2028", "\u2029"]


@pytest.mark.parametrize("ch", BREAKS)
@pytest.mark.parametrize("field", ["name", "dataset"])
def test_line_break_in_query_field_is_refused(field, ch):
    bad = q("h", "hunt")
    bad[field] = f"x{ch}dataset = evil"
    with pytest.raises(ValueError, match=rf"card A.*{field}"):
        render(scen("A"), {"A": card(bad)})


@pytest.mark.parametrize("ch", BREAKS)
def test_line_break_in_card_id_is_refused(ch):
    cid = f"A{ch}B"
    with pytest.raises(ValueError, match="card id"):
        render(scen(cid), {cid: card(q("h", "hunt"))})


@pytest.mark.parametrize("ch", BREAKS)
def test_line_break_in_scenario_id_is_refused(ch):
    s = scen("A")
    s["scenario_id"] = f"SIM{ch}X"
    with pytest.raises(ValueError, match="scenario_id"):
        render(s, {"A": card()})


@pytest.mark.parametrize("bad", [None, "Hunt", "fun", ""])
def test_bad_purpose_is_refused(bad):
    with pytest.raises(ValueError, match=r"card A.*purpose"):
        render(scen("A"), {"A": card(q("h", bad))})


def test_missing_purpose_is_refused():
    item = {"name": "h", "dataset": "d", "query": "x"}
    with pytest.raises(ValueError, match=r"card A.*purpose"):
        render(scen("A"), {"A": card(item)})


def test_empty_result_points_at_other_purposes():
    cards = {"A": card(q("v1", "validation"), q("v2", "validation"), q("m", "metric"))}
    t = render(scen("A"), cards)
    assert "# No hunt or investigation query on this scenario's cards." in t
    assert "# The cited cards carry 2 validation, 1 metric queries: add ?purpose=validation,metric" in t


def test_empty_result_without_any_query_has_no_pointer():
    assert "The cited cards carry" not in render(scen("A"), {"A": card()})


def test_nonempty_result_has_no_pointer():
    cards = {"A": card(q("h", "hunt"), q("v", "validation"))}
    assert "The cited cards carry" not in render(scen("A"), cards)


@pytest.mark.parametrize("raw", [
    "str", {"detections": "x"}, {"detections": {"xql_queries": ["x"]}},
    card({"name": 1, "purpose": "hunt"}), card({"dataset": 1, "purpose": "hunt"}),
    card({"purpose": 1}), card({"purpose": "hunt", "query": 1}),
])
def test_shape_errors_are_value_errors(raw):
    with pytest.raises(ValueError):
        render(scen("A"), {"A": raw})


def test_undeclared_dataset_and_unnamed_fallbacks():
    t = render(scen("A"), {"A": card({"purpose": "hunt", "query": "x"})})
    assert "## (unnamed query)" in t and "dataset: (undeclared)" in t


def test_text_none_gets_empty_note():
    t = render(scen("A"), {"A": card({"name": "h", "purpose": "hunt"})})
    assert "# (query text is empty on the card)" in t


def test_single_trailing_newline():
    t = render(scen("A"), {"A": card(q("h", "hunt", "dataset = d\n"))})
    assert t.endswith("dataset = d\n") and not t.endswith("\n\n")
    t2 = render(scen("A"), {"A": card(q("h", "hunt", "dataset = d"))})
    assert t2.endswith("dataset = d\n") and not t2.endswith("\n\n")


def test_empty_and_duplicate_purposes_raise():
    with pytest.raises(ValueError):
        render(scen("A"), {"A": card()}, ())
    with pytest.raises(ValueError):
        render(scen("A"), {"A": card()}, ("hunt", "hunt"))
