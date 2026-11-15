"""derive(): one scenario's prerequisites from the dataset requirement registry."""
from __future__ import annotations

import copy

import pytest

from engine.dataset_requirements import parse_registry
from engine.prerequisites import derive
from tests.engine._dataset_requirements_fixtures import GOOD


def scen(*refs):
    return {"scenario_id": "SIM-T-001", "steps": [{"id": f"s{i}", "expected_detections": [{"ttp_ref": r}]}
                                         for i, r in enumerate(refs)]}


def card(*datasets):
    return {"detections": {"xql_queries": [{"name": f"q{i}", "dataset": d, "query": "x"}
                                            for i, d in enumerate(datasets)]}}


REG = parse_registry(GOOD, "p")


def codes(out):
    return [n["code"] for n in out["notes"]]


def test_one_row_per_dataset_listing_every_card():
    out = derive(scen("A", "B", "C"), {k: card("panw_ngfw_traffic_raw") for k in "ABC"}, REG)
    assert [d["dataset"] for d in out["datasets"]] == ["panw_ngfw_traffic_raw"]
    assert out["datasets"][0]["cards"] == ["A", "B", "C"]


def test_no_card_binding_is_a_note_not_an_empty_answer():
    out = derive({"scenario_id": "SIM-T-002", "steps": [{"id": "s", "expected_detections": []}]}, {}, REG)
    assert [n["code"] for n in out["notes"]] == ["NO_CARD_BINDING"]


def test_unregistered_dataset_is_listed_and_flagged():
    out = derive(scen("A"), {"A": card("mystery_raw")}, REG)
    assert out["datasets"][0] == {"dataset": "mystery_raw", "registered": False, "cards": ["A"],
                                  "read_by": ["xql_queries"]}
    assert out["summary"]["unregistered"] == 1
    assert "UNREGISTERED_DATASET" in codes(out)


def test_query_without_dataset_is_reported():
    out = derive(scen("A"), {"A": card(None)}, REG)
    assert codes(out) == ["DATASET_UNDECLARED"]


def test_missing_card_is_reported():
    assert "CARD_NOT_FOUND" in codes(derive(scen("ZZ"), {}, REG))


def test_absent_registry_is_degraded():
    out = derive(scen("A"), {"A": card("panw_ngfw_traffic_raw")}, None)
    assert out["registry_loaded"] is False and out["datasets"][0]["registered"] is False
    assert out["datasets"][0] == {"dataset": "panw_ngfw_traffic_raw", "registered": False,
                                  "cards": ["A"], "read_by": ["xql_queries"]}
    assert "REGISTRY_ABSENT" in codes(out)
    assert "UNREGISTERED_DATASET" in codes(out)
    assert out["summary"]["unregistered"] == 1


def test_never_claims_verification():
    out = derive(scen("A"), {"A": card("panw_ngfw_traffic_raw")}, REG)
    assert out["tenant_verified"] == 0 and out["write_path"] is False


def test_unrecognised_scenario_shape_raises():
    with pytest.raises(ValueError):
        derive({"scenario_id": "x", "steps": "nope"}, {}, REG)


# --- beyond the brief ---

def test_registered_row_carries_registry_states_verbatim():
    out = derive(scen("A"), {"A": card("panw_ngfw_traffic_raw")}, REG)
    row = out["datasets"][0]
    req = REG["panw_ngfw_traffic_raw"]
    assert row["registered"] is True and row["produced_by"] == req.produced_by
    assert row["cortex_side"] == [
        {"kind": c.kind, "name": c.name, "state": c.verified.state} for c in req.cortex_side]
    assert row["source_side"] == [
        {"product": s.product, "requirement": s.requirement, "detail": s.detail,
         "state": s.verified.state} for s in req.source_side]
    assert row["can_be_emulated_by"] == list(req.emulated_by)
    assert row["read_by"] == ["xql_queries"]
    assert out["registry_loaded"] is True and out["scenario_id"] == "SIM-T-001"


def test_summary_counts_confirmed_and_documentation_rows():
    data = copy.deepcopy(GOOD)
    entry = data["panw_ngfw_traffic_raw"]
    entry["cortex_side"][0]["verified"] = {"by": "hr", "on": "2026-11-12", "how": "saw it"}
    reg = parse_registry(data, "p")
    out = derive(scen("A"), {"A": card("panw_ngfw_traffic_raw", "mystery_raw")}, reg)
    n_cortex, n_source = len(entry["cortex_side"]), len(entry["source_side"])
    assert out["summary"] == {
        "datasets": 2, "unregistered": 1,
        "confirmed_rows": 1, "documentation_rows": n_cortex + n_source - 1}


def test_datasets_and_cards_are_sorted_and_distinct():
    sc = scen("B", "A", "B")
    out = derive(sc, {"A": card("zz_raw", "aa_raw"), "B": card("aa_raw")}, REG)
    assert out["cards"] == ["A", "B"]
    assert [d["dataset"] for d in out["datasets"]] == ["aa_raw", "zz_raw"]
    assert out["datasets"][0]["cards"] == ["A", "B"]


def test_undeclared_note_names_card_and_query_and_blank_counts():
    out = derive(scen("A"), {"A": card("  ")}, REG)
    (note,) = out["notes"]
    assert note["code"] == "DATASET_UNDECLARED"
    assert "A" in note["detail"] and "q0" in note["detail"]
    missing_key = {"detections": {"xql_queries": [{"name": "nokey", "query": "x"}]}}
    assert codes(derive(scen("A"), {"A": missing_key}, REG)) == ["DATASET_UNDECLARED"]


def test_one_note_per_missing_card_and_per_unregistered_dataset():
    out = derive(scen("X", "Y"), {}, REG)
    assert codes(out) == ["CARD_NOT_FOUND", "CARD_NOT_FOUND"]
    out = derive(scen("A"), {"A": card("m1_raw", "m2_raw")}, REG)
    assert codes(out).count("UNREGISTERED_DATASET") == 2


def test_blank_and_missing_ttp_refs_are_not_cards():
    sc = {"scenario_id": "S", "steps": [{"id": "a", "expected_detections": [
        {"ttp_ref": ""}, {"ttp_ref": None}, {}, {"ttp_ref": "  "}]}]}
    out = derive(sc, {}, REG)
    assert out["cards"] == []
    # The all-unbound case carries NO_CARD_BINDING AND one DETECTION_UNBOUND per entry.
    assert codes(out) == ["NO_CARD_BINDING"] + ["DETECTION_UNBOUND"] * 4


def test_card_without_queries_is_flagged_not_silent():
    out = derive(scen("A"), {"A": {"detections": {}}}, REG)
    assert out["datasets"] == []
    assert codes(out) == ["CARD_DECLARES_NO_QUERY"]
    assert "A" in out["notes"][0]["detail"]


def test_one_no_query_note_naming_the_right_card():
    out = derive(scen("A", "B"), {"A": card("panw_ngfw_traffic_raw"), "B": {"detections": {}}}, REG)
    notes = [n for n in out["notes"] if n["code"] == "CARD_DECLARES_NO_QUERY"]
    assert len(notes) == 1
    assert "card B " in notes[0]["detail"] and "card A " not in notes[0]["detail"]


def test_does_not_mutate_inputs():
    sc, cards = scen("A"), {"A": card("panw_ngfw_traffic_raw")}
    before = copy.deepcopy((sc, cards))
    derive(sc, cards, REG)
    assert (sc, cards) == before


@pytest.mark.parametrize("scenario, cards", [
    ({"scenario_id": "x", "steps": [["not a dict"]]}, {}),
    ({"scenario_id": "x", "steps": ["s"]}, {}),
    ({"scenario_id": "x", "steps": [{"id": "s", "expected_detections": "nope"}]}, {}),
    ({"scenario_id": "x", "steps": [{"id": "s", "expected_detections": ["nope"]}]}, {}),
    (scen("A"), {"A": {"detections": []}}),
    (scen("A"), {"A": {"detections": {"xql_queries": "nope"}}}),
    (scen("A"), {"A": {"detections": {"xql_queries": ["nope"]}}}),
    (scen("A"), {"A": {"detections": {"xql_queries": [{"name": "q", "dataset": 5}]}}}),
    (scen("A"), {"A": "not a dict"}),
])
def test_other_unrecognised_shapes_raise(scenario, cards):
    with pytest.raises(ValueError):
        derive(scenario, cards, REG)


def test_derive_reads_the_string_scenario_id_not_the_integer_pk():
    sc = {"id": 136, "scenario_id": "SIM-MP-001", "steps": []}
    out = derive(sc, {}, REG)
    assert out["scenario_id"] == "SIM-MP-001"
    assert any("SIM-MP-001" in n["detail"] for n in out["notes"] if n["code"] == "NO_CARD_BINDING")


@pytest.mark.parametrize("sc", [
    {"id": "SIM-X", "steps": []},
    {"id": 5, "scenario_id": 136, "steps": []},
    {"scenario_id": "   ", "steps": []},
    {"scenario_id": None, "steps": []},
])
def test_derive_requires_a_non_blank_string_scenario_id(sc):
    with pytest.raises(ValueError, match="scenario_id"):
        derive(sc, {}, REG)


# --- item A: datasets read by BIOC / ABIOC / correlation logic -------------------------

def test_bioc_only_card_yields_its_dataset_not_only_a_note():
    cards = {"A": {"detections": {"xql_queries": [], "biocs": [{"name": "b", "logic": "dataset = esxi_syslog"}]}}}
    out = derive(scen("A"), cards, REG)
    assert [d["dataset"] for d in out["datasets"]] == ["esxi_syslog"]
    assert out["datasets"][0]["read_by"] == ["biocs"]
    (note,) = [n for n in out["notes"] if n["code"] == "CARD_DECLARES_NO_QUERY"]
    assert "cannot be derived" not in note["detail"]


def test_card_with_no_query_and_no_anchor_still_says_cannot_be_derived():
    out = derive(scen("A"), {"A": {"detections": {"biocs": [{"name": "b", "logic": "filter a = 1"}]}}}, REG)
    assert out["datasets"] == []
    (note,) = out["notes"]
    assert note["code"] == "CARD_DECLARES_NO_QUERY" and "cannot be derived" in note["detail"]


def test_read_by_is_sorted_and_on_every_row():
    cards = {"A": {"detections": {
        "xql_queries": [{"name": "q", "dataset": "panw_ngfw_traffic_raw", "query": "x"}],
        "correlation_rules": [{"name": "c", "logic": "preset = panw_ngfw_traffic_raw"}],
        "abiocs": [{"name": "a", "logic": "dataset = mystery_raw"}]}}}
    out = derive(scen("A"), cards, REG)
    rows = {d["dataset"]: d for d in out["datasets"]}
    assert rows["panw_ngfw_traffic_raw"]["read_by"] == ["correlation_rules", "xql_queries"]
    assert rows["mystery_raw"] == {"dataset": "mystery_raw", "registered": False,
                                   "cards": ["A"], "read_by": ["abiocs"]}


def test_real_sim_aiacc_001_needs_the_agent_dataset():
    from config import settings
    from engine.dataset_requirements import load
    from engine.ttp_catalog import catalog, default_corpus_dir
    from tests.engine._corpus_scenarios import scenario_by_id

    load.cache_clear()
    catalog.load(default_corpus_dir(settings.CORTEXSIM_BASE_DIR))
    out = derive(scenario_by_id("SIM-AIACC-001"), catalog.all_raw(), load())
    rows = {d["dataset"]: d for d in out["datasets"]}
    assert "xdr_data" in rows and "biocs" in rows["xdr_data"]["read_by"]
    assert rows["xdr_data"]["registered"] is True


def test_real_sim_mp_013_lists_esxi_syslog():
    from config import settings
    from engine.dataset_requirements import load
    from engine.ttp_catalog import catalog, default_corpus_dir
    from tests.engine._corpus_scenarios import scenario_by_id

    load.cache_clear()
    catalog.load(default_corpus_dir(settings.CORTEXSIM_BASE_DIR))
    out = derive(scenario_by_id("SIM-MP-013"), catalog.all_raw(), load())
    rows = {d["dataset"]: d for d in out["datasets"]}
    assert rows["esxi_syslog"]["registered"] is True
    assert rows["esxi_syslog"]["produced_by"] == "vmware-esxi"
    assert out["summary"]["unregistered"] == 0


# --- item B: unbound expected detections are never skipped silently ------------------

def test_mixed_bound_and_unbound_emits_one_note_per_unbound_entry():
    sc = {"scenario_id": "S", "steps": [
        {"id": "step-01", "expected_detections": [
            {"ttp_ref": "A"}, {"type": "Analytics", "plane": "CDR", "description": "x"}]},
        {"id": "step-02"},  # no expected_detections key: allowed, empty
        {"id": "step-03", "expected_detections": [{"ttp_ref": ""}]},
    ]}
    out = derive(sc, {"A": card("panw_ngfw_traffic_raw")}, REG)
    assert out["cards"] == ["A"]
    unbound = [n for n in out["notes"] if n["code"] == "DETECTION_UNBOUND"]
    assert len(unbound) == 2
    assert "step step-01 expected_detections[1]" in unbound[0]["detail"]
    assert "type Analytics" in unbound[0]["detail"] and "plane CDR" in unbound[0]["detail"]
    assert "step step-03 expected_detections[0]" in unbound[1]["detail"]
    assert "NO_CARD_BINDING" not in codes(out)


def test_real_sim_cdr_027_carries_detection_unbound():
    from config import settings
    from engine.dataset_requirements import load
    from engine.ttp_catalog import catalog, default_corpus_dir
    from tests.engine._corpus_scenarios import scenario_by_id

    load.cache_clear()
    catalog.load(default_corpus_dir(settings.CORTEXSIM_BASE_DIR))
    out = derive(scenario_by_id("SIM-CDR-027"), catalog.all_raw(), load())
    assert out["cards"], "SIM-CDR-027 has bound detections too"
    assert "DETECTION_UNBOUND" in codes(out)


def test_every_emitted_code_is_in_note_codes():
    from engine.prerequisites import NOTE_CODES
    assert len(set(NOTE_CODES)) == len(NOTE_CODES) == 7
