"""The shipped dataset requirement registry is pinned to the card corpus.

Every dataset a card queries has an entry; every entry is a known dataset;
``emulated_by`` names real EAL plugins; and the seed claims no tenant
confirmation it does not have.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from config import settings
from engine.dataset_requirements import DatasetRequirementsError, card_dataset_index, card_datasets, load
from engine.ttp_catalog import catalog, default_corpus_dir

_VALIDATE = Path(__file__).resolve().parents[2] / "detection_scanner" / "scripts" / "validate.py"


def _known_datasets() -> frozenset[str]:
    spec = importlib.util.spec_from_file_location("validate_script", _VALIDATE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["validate_script"] = mod
    spec.loader.exec_module(mod)
    return mod.KNOWN_DATASETS


KNOWN_DATASETS = _known_datasets()


def eal_plugin_names() -> set[str]:
    """The plugin names ``GET /api/eal/plugins`` serves (the default registry's manifest)."""
    from eal_simulator.registry import get_default_registry  # noqa: PLC0415

    names = {m["name"] for m in get_default_registry().manifest()}
    assert names, "EAL plugin registry loaded no plugins"
    return names


@pytest.fixture(scope="module", autouse=True)
def _corpus_and_registry():
    load.cache_clear()
    assert catalog.load(default_corpus_dir(settings.CORTEXSIM_BASE_DIR)) > 0
    assert load() is not None, "spec/dataset_requirements.yml is absent"
    yield
    load.cache_clear()


def test_card_datasets_skips_blank_and_missing():
    raw = {
        "A": {"detections": {"xql_queries": [{"dataset": "xdr_data"}, {"dataset": " "}, {}]}},
        "B": {"detections": {"xql_queries": [{"dataset": "xdr_data"}]}},
        "C": {},
    }
    assert card_datasets(raw) == {"xdr_data": {"A", "B"}}


def test_card_datasets_skips_none_and_whitespace():
    raw = {"A": {"detections": {"xql_queries": [{"dataset": None}, {"dataset": "\t "}]}},
           "B": {"detections": {}}, "C": {"detections": None}}
    assert card_datasets(raw) == {}


@pytest.mark.parametrize("card, needle", [
    ({"detections": []}, r"card X: detections must be a mapping"),
    ({"detections": {"xql_queries": {"dataset": "xdr_data"}}}, r"card X: detections\.xql_queries must be a list"),
    ({"detections": {"xql_queries": [{"dataset": "xdr_data"}, "xdr_data"]}}, r"card X: detections\.xql_queries\[1\] must be a mapping"),
    ({"detections": {"xql_queries": [{"dataset": ["xdr_data"]}]}}, r"card X: detections\.xql_queries\[0\]\.dataset must be a string"),
    ({"detections": {"xql_queries": [{"dataset": 7}]}}, r"card X: detections\.xql_queries\[0\]\.dataset must be a string"),
])
def test_card_datasets_raises_on_unrecognised_shape(card, needle):
    with pytest.raises(DatasetRequirementsError, match=needle):
        card_datasets({"X": card})


def test_real_corpus_reads_without_raising():
    assert len(card_datasets(catalog.all_raw())) > 0


def test_every_card_dataset_is_registered():
    missing = sorted(set(card_datasets(catalog.all_raw())) - set(load()))
    assert missing == [], f"datasets read by cards with no requirement entry: {missing}"


def test_registry_keys_are_known_datasets():
    assert sorted(set(load()) - KNOWN_DATASETS) == []


def test_emulated_by_names_real_plugins():
    names = eal_plugin_names()
    bad = {(k, p) for k, r in load().items() for p in r.emulated_by if p not in names}
    assert bad == set()


def test_seed_claims_no_tenant_confirmation_it_does_not_have():
    confirmed = [k for k, r in load().items()
                 for row in (*r.cortex_side, *r.source_side)
                 if row.verified.state == "confirmed in a tenant"]
    assert confirmed == []   # edit this list deliberately when a DC verifies a row


def test_seed_names_no_cortex_side_object():
    named = [k for k, r in load().items() for c in r.cortex_side if c.name is not None]
    assert named == []   # a name is written only once read in a tenant


def test_ngfw_datasets_require_log_forwarding():
    for k, r in load().items():
        if r.produced_by == "strata-ngfw":
            assert "log_forwarding" in {s.requirement for s in r.source_side}, k


@pytest.mark.parametrize("dataset, requirement", [
    ("panw_ngfw_threat_raw", "threat_profile_attached"),
    ("pan_dns", "dns_security_logged"),
    ("panw_ngfw_globalprotect_raw", "globalprotect_logs"),
])
def test_ngfw_specific_requirements_sit_on_their_datasets(dataset, requirement):
    assert requirement in {s.requirement for s in load()[dataset].source_side}


def test_xdr_data_requires_an_installed_agent():
    row = load()["xdr_data"]
    assert row.produced_by == "cortex-xdr-agent"
    assert "agent_installed" in {s.requirement for s in row.source_side}


# --- item A: datasets read by BIOC / ABIOC / correlation logic -------------------------

def _bioc(logic, name="b"):
    return {"name": name, "logic": logic}


def test_bioc_anchor_alone_yields_the_dataset():
    raw = {"A": {"detections": {"xql_queries": [], "biocs": [_bioc("dataset = esxi_syslog\n| filter x = 1")]}}}
    assert card_datasets(raw) == {"esxi_syslog": {"A"}}


@pytest.mark.parametrize("kind", ["biocs", "abiocs", "correlation_rules"])
def test_each_logic_kind_is_walked_and_recorded(kind):
    raw = {"A": {"detections": {kind: [_bioc("preset = xdr_data | filter a = 1")]}}}
    assert card_dataset_index(raw) == {"xdr_data": {"A": {kind}}}


def test_every_anchor_in_one_logic_is_read_and_kinds_merge():
    logic = "dataset = xdr_data\n| join (dataset=panw_ngfw_traffic_raw | fields a) as n n.a = a"
    raw = {"A": {"detections": {
        "xql_queries": [{"name": "q", "dataset": "xdr_data", "query": "dataset = xdr_data"}],
        "correlation_rules": [_bioc(logic)]}}}
    assert card_dataset_index(raw) == {
        "xdr_data": {"A": {"xql_queries", "correlation_rules"}},
        "panw_ngfw_traffic_raw": {"A": {"correlation_rules"}},
    }


def test_anchor_matches_validate_py_semantics():
    # validate.py: (?:dataset|preset)\s*=\s*NAME, case-insensitive, name lowercased.
    raw = {"A": {"detections": {"biocs": [_bioc("PRESET=XDR_DATA | filter a = 1"),
                                          _bioc("filter x = 1")]}}}
    assert card_datasets(raw) == {"xdr_data": {"A"}}


@pytest.mark.parametrize("card, needle", [
    ({"detections": {"biocs": {"logic": "dataset = x"}}}, r"card X: detections\.biocs must be a list"),
    ({"detections": {"abiocs": ["dataset = x"]}}, r"card X: detections\.abiocs\[0\] must be a mapping"),
    ({"detections": {"correlation_rules": [{"name": "c", "logic": ["dataset = x"]}]}},
     r"card X: detections\.correlation_rules\[0\]\.logic must be a string"),
    ({"detections": {"biocs": [{"name": "b"}]}}, r"card X: detections\.biocs\[0\]\.logic must be a string"),
    ({"detections": {"biocs": [{"name": "b", "logic": 7}]}}, r"card X: detections\.biocs\[0\]\.logic must be a string"),
])
def test_logic_shape_errors_raise(card, needle):
    with pytest.raises(DatasetRequirementsError, match=needle):
        card_datasets({"X": card})


def test_walker_is_the_single_implementation():
    """derive and card_datasets share one walker; prerequisites.py carries no shape rules of its own."""
    import ast
    import engine.prerequisites as prereq
    src = Path(prereq.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "card_queries" not in defined
    assert '"detections"' not in src, "prerequisites.py reads card shapes itself again"


def test_esxi_syslog_is_registered_and_read_by_logic_only():
    index = card_dataset_index(catalog.all_raw())
    assert "esxi_syslog" in load()
    kinds = set().union(*index["esxi_syslog"].values())
    assert kinds and "xql_queries" not in kinds


def test_new_registry_rows_claim_nothing():
    row = load()["esxi_syslog"]
    assert row.produced_by == "vmware-esxi"
    assert all(c.name is None for c in row.cortex_side)
    assert all(v.state == "from documentation"
               for v in (*(c.verified for c in row.cortex_side), *(s.verified for s in row.source_side)))


# --- item E: rows that read as "nothing to configure" --------------------------------

def test_threat_intel_states_its_indicator_feed():
    rows = {(s.product, s.requirement): s for s in load()["threat_intel"].source_side}
    row = rows[("cortex-xsiam", "api_integration")]
    assert "indicator feed" in row.detail and row.verified.state == "from documentation"


def test_asm_findings_states_its_scope():
    rows = {(s.product, s.requirement): s for s in load()["asm_findings"].source_side}
    row = rows[("cortex-xpanse", "asset_scope_configured")]
    assert "scope" in row.detail and row.verified.state == "from documentation"


def test_asset_scope_configured_is_xpanse_only():
    from engine.dataset_requirements import SOURCE_REQUIREMENTS
    assert [p for p, reqs in SOURCE_REQUIREMENTS.items() if "asset_scope_configured" in reqs] == ["cortex-xpanse"]
