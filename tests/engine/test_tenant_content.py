"""engine.tenant_content — what a scenario needs to exist in the TENANT.

The first half asserts ``spec/tenant_content_cases.json``, the SAME fixture the
Composer's ``composerContent.test.js`` asserts against the browser derivation,
so the canvas and the export cannot disagree about a rule's status.

The second half reads the real corpus. Its tripwire is the honest number this
feature exists to show: no correlation rule in the library is deployable.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from engine import tenant_content as tc

REPO = Path(__file__).resolve().parents[2]
CASES = json.loads((REPO / "spec" / "tenant_content_cases.json").read_text(encoding="utf-8"))
CARDS_DIR = REPO / "detection_scanner" / "ttps"


def _ids(cases, key="label"):
    return [str(c.get(key))[:60] for c in cases]


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES["ownership"], ids=_ids(CASES["ownership"], "type"))
def test_ownership(case):
    if case.get("error"):
        with pytest.raises(tc.TenantContentError):
            tc.ownership_of(case["type"])
    else:
        assert tc.ownership_of(case["type"]) == case["ownership"]


@pytest.mark.parametrize("case", CASES["classify"], ids=_ids(CASES["classify"]))
def test_classify_rule(case):
    if case.get("error"):
        with pytest.raises(tc.TenantContentError):
            tc.classify_rule(case["rule"])
    else:
        assert tc.classify_rule(case["rule"]) == case["status"]


@pytest.mark.parametrize("case", CASES["logic"], ids=_ids(CASES["logic"]))
def test_parse_logic(case):
    if case.get("error"):
        with pytest.raises(tc.TenantContentError):
            tc.parse_logic(case["logic"])
    else:
        assert tc.parse_logic(case["logic"]) == {"kind": case["kind"], "terms": case["terms"]}


def _project(content: dict) -> dict:
    """The fixture pins a subset of each rule; project the derivation onto it."""
    return {
        "cited_cards": content["cited_cards"],
        "cards_missing": content["cards_missing"],
        "no_card_binding": content["no_card_binding"],
        "rules": [
            {
                "rule_id": r["rule_id"],
                "ttp_ref": r["ttp_ref"],
                "name": r["name"],
                "content_status": r["content_status"],
                "binding": r["binding"],
                "logic_kind": r["logic_kind"],
                "declared_by": r["declared_by"],
                "inputs": [
                    {k: i[k] for k in ("kind", "name", "state", "resolved_kind", "steps")}
                    for i in r["inputs"]
                ],
                "feeders": r["feeders"],
                "anchor_step": r["anchor_step"],
                "edge_kind": r["edge_kind"],
            }
            for r in content["rules"]
        ],
        "unbound": [
            {"step": u["step"], "index": u["index"], "reason": u["reason"]}
            for u in content["unbound"]
        ],
        "summary": content["summary"],
    }


@pytest.mark.parametrize("case", CASES["scenarios"], ids=_ids(CASES["scenarios"]))
def test_derive_against_fixture(case):
    assert _project(tc.derive(case["steps"], case["cards"])) == case["expected"]


def test_derive_keeps_the_reason_a_rule_is_unreadable():
    case = CASES["scenarios"][0]
    odd = tc.derive(case["steps"], case["cards"])["rules"][3]
    assert odd["content_status"] == "unreadable"
    assert "severity" in odd["content_error"]


def test_derive_does_not_mutate_its_inputs():
    case = CASES["scenarios"][0]
    before = json.dumps([case["steps"], case["cards"]], sort_keys=True)
    tc.derive(case["steps"], case["cards"])
    assert json.dumps([case["steps"], case["cards"]], sort_keys=True) == before


def test_slug_is_the_catalogs_own():
    """One slugger. The fixture's slug cases pin the browser copy to this one."""
    from engine.ttp_catalog import _slug

    for case in CASES["slugs"]:
        assert _slug(case["name"], case["prefix"]) == case["slug"]


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def _manifest_case():
    case = CASES["scenarios"][0]
    scenario = {"scenario_id": "SIM-T-001", "name": "Fixture chain", "steps": case["steps"]}
    return scenario, case["cards"]


def test_manifest_states_the_two_things_it_never_claims():
    scenario, cards = _manifest_case()
    m = tc.build_manifest(scenario, cards, catalog_loaded=True)
    assert m["tenant_verified"] == 0
    assert m["write_path"] is False
    assert m["catalog_loaded"] is True
    assert m["scenario_id"] == "SIM-T-001"


def test_manifest_invents_nothing_for_a_stub():
    scenario, cards = _manifest_case()
    m = tc.build_manifest(scenario, cards, catalog_loaded=True)
    stub = m["rules"][0]
    assert stub["content_status"] == "intent-only"
    assert stub["deploy"] is None
    assert "severity" not in stub
    assert "description" not in stub
    # The logic line is exported verbatim, as the authoring note it is.
    assert stub["logic"] == "XQL(Q One) AND BIOC(B One)"


def test_manifest_counts_agree_with_the_rules_it_lists():
    scenario, cards = _manifest_case()
    m = tc.build_manifest(scenario, cards, catalog_loaded=True)
    c = m["summary"]["correlation"]
    assert c["total"] == len(m["rules"]) == 5
    assert c["deployable"] == 0
    assert c["intent_only"] + c["unreadable"] + c["deployable"] == c["total"]
    assert c["declared"] + c["card_only"] == c["total"]
    assert c["unbound"] == len(m["unbound"]) == 3


def test_manifest_lists_the_queries_to_run_and_the_content_to_deploy():
    scenario, cards = _manifest_case()
    m = tc.build_manifest(scenario, cards, catalog_loaded=True)
    assert [q["detection_id"] for q in m["queries"]] == ["xql-q-one", "xql-q-two-burst"]
    assert m["queries"][0]["steps"] == ["step-01"]
    assert m["queries"][0]["content"] == {"name": "Q One"}
    assert [b["detection_id"] for b in m["biocs"]] == ["bioc-b-one"]
    assert m["biocs"][0]["steps"] == ["step-02"]


def test_manifest_reports_what_it_could_not_resolve_instead_of_dropping_it():
    scenario, cards = _manifest_case()
    m = tc.build_manifest(scenario, cards, catalog_loaded=True)
    codes = {n["code"] for n in m["notes"]}
    assert "CARD_NOT_FOUND" in codes          # TTP-T-0002 is cited and absent
    assert "UNBOUND_CORRELATION" in codes     # a Correlation expectation with no rule
    assert "RULE_UNREADABLE" in codes
    missing = [n for n in m["notes"] if n["code"] == "CARD_NOT_FOUND"]
    assert missing[0]["detail"].startswith("TTP-T-0002")


def test_manifest_without_a_catalog_is_degraded_not_empty():
    scenario, _ = _manifest_case()
    m = tc.build_manifest(scenario, {}, catalog_loaded=False)
    assert m["catalog_loaded"] is False
    assert m["rules"] == []
    assert [n["code"] for n in m["notes"]] == ["CATALOG_NOT_LOADED"]
    # A reader must not take the zero for "nothing needed".
    assert m["summary"]["correlation"]["total"] is None


def test_manifest_for_a_chain_citing_no_card_says_so():
    case = CASES["scenarios"][1]
    m = tc.build_manifest({"scenario_id": "SIM-T-002", "steps": case["steps"]}, {}, catalog_loaded=True)
    assert "NO_CARD_BINDING" in {n["code"] for n in m["notes"]}
    assert m["rules"] == []


def test_manifest_names_an_unknown_detection_type():
    scenario = {"scenario_id": "SIM-T-003", "steps": [
        {"id": "step-01", "expected_detections": [{"type": "Sigma", "ttp_ref": "TTP-T-0001", "detection_id": "x"}]},
    ]}
    m = tc.build_manifest(scenario, CASES["scenarios"][0]["cards"], catalog_loaded=True)
    assert "UNKNOWN_DETECTION_TYPE" in {n["code"] for n in m["notes"]}


def test_manifest_rejects_a_scenario_whose_steps_are_not_a_list():
    with pytest.raises(tc.TenantContentError):
        tc.build_manifest({"scenario_id": "SIM-T-004", "steps": {"step-01": {}}}, {}, catalog_loaded=True)


# ---------------------------------------------------------------------------
# The real corpus
# ---------------------------------------------------------------------------


def _corpus_cards() -> dict:
    cards = {}
    for path in sorted(CARDS_DIR.glob("*.json")):
        card = json.loads(path.read_text(encoding="utf-8"))
        cards[card["id"]] = card
    return cards


def test_no_correlation_rule_in_the_corpus_is_deployable():
    """TRIPWIRE. Every rule in the library is an authoring note: name, id and a
    logic line, with nothing a tenant can import. The day one becomes deployable
    this test must be edited on purpose, together with the card schema, the
    validator and the coverage report (design spec, phase 3)."""
    status: dict[str, int] = {}
    for card in _corpus_cards().values():
        for rule in (card.get("detections") or {}).get("correlation_rules") or []:
            try:
                s = tc.classify_rule(rule)
            except tc.TenantContentError:
                s = "unreadable"
            status[s] = status.get(s, 0) + 1
    assert status.get("intent-only", 0) > 0
    assert status.get("deployable", 0) == 0
    assert status.get("unreadable", 0) == 0, status


def test_every_corpus_logic_line_parses():
    for cid, card in _corpus_cards().items():
        for rule in (card.get("detections") or {}).get("correlation_rules") or []:
            try:
                tc.parse_logic(rule.get("logic"))
            except tc.TenantContentError as exc:  # pragma: no cover - corpus guard
                pytest.fail(f"{cid} {rule.get('rule_id')}: {exc}")


def test_sim_aiacc_003_needs_a_rule_it_does_not_declare_and_cannot_import():
    """The scenario this work started from. Its steps expect five queries a DC
    runs by hand; the rule that would raise the alert is on the card only."""
    scenario = yaml.safe_load(
        (REPO / "scenarios" / "ai_access" / "sim-aiacc-003-high-volume-gemini.yml").read_text(encoding="utf-8")
    )
    m = tc.build_manifest(scenario, _corpus_cards(), catalog_loaded=True)
    assert [r["rule_id"] for r in m["rules"]] == ["CR-AIACC-0003"]
    rule = m["rules"][0]
    assert rule["content_status"] == "intent-only"
    assert rule["binding"] == "card-only"
    assert rule["logic_kind"] == "composition"
    assert rule["deploy"] is None
    assert [(i["state"], i["steps"]) for i in rule["inputs"]] == [("step", ["step-02"])] * 3
    assert len(m["queries"]) == 5
    assert m["biocs"] == [] and m["iocs"] == []
    assert m["summary"]["correlation"] == {
        "total": 1, "deployable": 0, "intent_only": 1, "unreadable": 0,
        "declared": 0, "card_only": 1, "unbound": 0,
    }
