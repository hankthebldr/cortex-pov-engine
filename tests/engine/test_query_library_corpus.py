"""The query library over the real corpus: no card and no scenario may break it.

A card with ``purpose: hunting`` or a line break in a query name only surfaces when a DC
asks for that scenario's queries and gets a 500. This walks every loaded card through the
same validation the route runs, and renders every loaded scenario for all four purposes.
"""
from __future__ import annotations

import pytest

from config import settings
from engine.query_library import ALLOWED_PURPOSES, render, validate_card
from engine.ttp_catalog import catalog, default_corpus_dir
from tests.engine._corpus_scenarios import all_scenario_dicts


def check_corpus(cards: dict[str, dict], scenarios: list[dict]) -> int:
    """Raise on the first card or scenario the query library cannot serve. Returns renders done."""
    for card_id, raw in cards.items():
        validate_card(card_id, raw)
    done = 0
    for sc in scenarios:
        for purpose in ALLOWED_PURPOSES:
            render(sc, cards, (purpose,))
            done += 1
    return done


@pytest.fixture(scope="module")
def corpus():
    n = catalog.load(default_corpus_dir(settings.CORTEXSIM_BASE_DIR))
    assert n > 100, f"only {n} cards loaded; the check would pass vacuously"
    return catalog.all_raw(), all_scenario_dicts()


def test_every_card_and_scenario_renders(corpus):
    cards, scenarios = corpus
    assert check_corpus(cards, scenarios) == len(scenarios) * len(ALLOWED_PURPOSES)


def test_the_check_fails_on_a_card_the_route_would_refuse(corpus):
    cards, scenarios = corpus
    bad_id = sorted(cards)[0]
    bad = dict(cards)
    bad[bad_id] = {"detections": {"xql_queries": [
        {"name": "q", "purpose": "hunting", "dataset": "xdr_data", "query": "dataset = xdr_data"}]}}
    with pytest.raises(ValueError, match=rf"card {bad_id} .*purpose"):
        check_corpus(bad, scenarios)
