"""Guards for the TTP catalog's derived-view memoisation.

``card_techniques`` / ``card_atlas_techniques`` / ``card_detection_kind_counts``
are pure functions of a corpus that is frozen once loaded, yet the coverage
heatmap asked for all three on every scenario/card pair on every request. They
are memoised now; these tests pin that the memo is (a) actually used, (b)
dropped on reload, and (c) not aliased into callers' hands.
"""
from __future__ import annotations

import json

import pytest

from engine.ttp_catalog import TtpCatalog

CARD = {
    "id": "TTP-TEST-0001",
    "status": "active",
    "identity": {"name": "Test Card"},
    "mitre_attack": {
        "techniques": [
            {"technique_id": "T1003", "subtechnique_id": "T1003.001",
             "name": "LSASS Memory", "tactic_ids": ["TA0006"],
             "tactic_names": ["Credential Access"]},
            {"technique_id": "T1021", "name": "Remote Services",
             "tactic_ids": ["TA0008"], "tactic_names": ["Lateral Movement"]},
        ],
        "atlas_techniques": [
            {"atlas_id": "AML.T0051.000", "name": "Direct Prompt Injection",
             "atlas_tactic": "ML Attack Staging"},
        ],
    },
    "detections": {
        "biocs": [{"name": "b1"}, {"name": "b2"}],
        "xql_queries": [{"name": "q1"}],
        "correlation_rules": [{"name": "c1"}],
        "iocs": [{"ioc_type": "domain", "value": "evil.test"}],
        "analytics_modules": [{"name": "a1"}],
        "abiocs": [{"name": "ab1"}],
        "modeling_rules": [{"name": "m1"}],
    },
}


@pytest.fixture
def corpus(tmp_path):
    (tmp_path / "card.json").write_text(json.dumps(CARD))
    return tmp_path


@pytest.fixture
def cat(corpus):
    c = TtpCatalog()
    c.load(str(corpus))
    return c


def test_derived_views_match_a_cold_derivation(cat):
    """Memoised output must equal what a fresh derivation produces."""
    assert cat.card_techniques("TTP-TEST-0001") == cat._derive_techniques("TTP-TEST-0001")
    assert cat.card_atlas_techniques("TTP-TEST-0001") == \
        cat._derive_atlas_techniques("TTP-TEST-0001")
    assert cat.card_detection_kind_counts("TTP-TEST-0001") == \
        cat._derive_kind_counts("TTP-TEST-0001")


def test_technique_ids_prefer_the_subtechnique(cat):
    techs = cat.card_techniques("TTP-TEST-0001")
    assert [t["technique"] for t in techs] == ["T1003.001", "T1021"]
    assert techs[0]["tactic_id"] == "TA0006"
    assert techs[0]["tactic_name"] == "Credential Access"


def test_kind_counts_cover_every_detection_family(cat):
    assert cat.card_detection_kind_counts("TTP-TEST-0001") == {
        "bioc": 2, "xql": 1, "correlation": 1, "ioc": 1,
        "analytics": 1, "abioc": 1, "modeling": 1,
    }


def test_derivation_runs_once_per_card(cat, monkeypatch):
    """Repeated lookups hit the memo instead of re-parsing the raw JSON."""
    calls = {"tech": 0, "atlas": 0, "kinds": 0}
    real_tech = cat._derive_techniques
    real_atlas = cat._derive_atlas_techniques
    real_kinds = cat._derive_kind_counts

    monkeypatch.setattr(cat, "_derive_techniques",
                        lambda r: (calls.__setitem__("tech", calls["tech"] + 1),
                                   real_tech(r))[1])
    monkeypatch.setattr(cat, "_derive_atlas_techniques",
                        lambda r: (calls.__setitem__("atlas", calls["atlas"] + 1),
                                   real_atlas(r))[1])
    monkeypatch.setattr(cat, "_derive_kind_counts",
                        lambda r: (calls.__setitem__("kinds", calls["kinds"] + 1),
                                   real_kinds(r))[1])

    for _ in range(25):
        cat.card_techniques("TTP-TEST-0001")
        cat.card_atlas_techniques("TTP-TEST-0001")
        cat.card_detection_kind_counts("TTP-TEST-0001")

    assert calls == {"tech": 1, "atlas": 1, "kinds": 1}


def test_unknown_card_returns_empty_and_is_memoised(cat):
    """A miss must stay a miss — and must not re-scan the corpus each time."""
    assert cat.card_techniques("TTP-NOPE") == []
    assert cat.card_atlas_techniques("TTP-NOPE") == []
    assert cat.card_detection_kind_counts("TTP-NOPE") == {
        "bioc": 0, "xql": 0, "correlation": 0, "ioc": 0,
        "analytics": 0, "abioc": 0, "modeling": 0,
    }
    assert "TTP-NOPE" in cat._techniques_memo


def test_callers_cannot_corrupt_the_memo(cat):
    """Returned containers are copies — a caller mutating one must not poison
    the catalog for every later request in the process."""
    techs = cat.card_techniques("TTP-TEST-0001")
    techs.append({"technique": "T9999"})
    assert len(cat.card_techniques("TTP-TEST-0001")) == 2

    atlas = cat.card_atlas_techniques("TTP-TEST-0001")
    atlas.clear()
    assert len(cat.card_atlas_techniques("TTP-TEST-0001")) == 1

    counts = cat.card_detection_kind_counts("TTP-TEST-0001")
    counts["bioc"] = 999
    assert cat.card_detection_kind_counts("TTP-TEST-0001")["bioc"] == 2


def test_reload_drops_stale_memo_entries(cat, corpus):
    """A reloaded corpus must not serve the previous corpus's derivations."""
    assert cat.card_techniques("TTP-TEST-0001")[0]["technique"] == "T1003.001"

    mutated = json.loads(json.dumps(CARD))
    mutated["mitre_attack"]["techniques"] = [
        {"technique_id": "T1486", "name": "Data Encrypted for Impact",
         "tactic_ids": ["TA0040"], "tactic_names": ["Impact"]},
    ]
    mutated["detections"]["biocs"] = []
    (corpus / "card.json").write_text(json.dumps(mutated))

    cat.load(str(corpus))

    assert [t["technique"] for t in cat.card_techniques("TTP-TEST-0001")] == ["T1486"]
    assert cat.card_detection_kind_counts("TTP-TEST-0001")["bioc"] == 0
