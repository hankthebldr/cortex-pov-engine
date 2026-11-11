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
from engine.dataset_requirements import card_datasets, load
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
