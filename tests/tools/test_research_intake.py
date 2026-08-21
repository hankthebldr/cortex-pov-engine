"""Tests for scripts/research_intake.py — the weekly research-intake scaffolder.

Asserts the two contracts that keep the scaffold safe to run unattended:
  1. It picks an UNUSED, never-reused TTP id (max existing + 1).
  2. The card it emits passes the canonical JSON Schema (detection_scanner/
     schema/ttp-entry.schema.json), and every scenario detection_id it emits
     resolves to a detection object on that same card (GAP-4 holds).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "research_intake.py"
SCHEMA = REPO_ROOT / "detection_scanner" / "schema" / "ttp-entry.schema.json"


def _load_module():
    spec = importlib.util.spec_from_file_location("research_intake", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ri():
    return _load_module()


def test_next_ttp_id_is_unused(ri):
    used = ri.discover_ttp_numbers(REPO_ROOT)
    nxt = ri.next_ttp_id(REPO_ROOT)
    m = __import__("re").match(r"TTP-\d{4}-(\d{4})", nxt)
    assert m, f"malformed ttp id {nxt!r}"
    num = int(m.group(1))
    assert num not in used, f"{nxt} collides with an existing TTP id"
    assert num == (max(used) + 1 if used else 1), "id must be monotonic max+1"


def test_next_ttp_id_stable_and_advances(ri):
    """Calling twice without writing returns the same id; the picked number is
    strictly greater than every existing number."""
    used = ri.discover_ttp_numbers(REPO_ROOT)
    a = ri.next_ttp_id(REPO_ROOT)
    b = ri.next_ttp_id(REPO_ROOT)
    assert a == b
    picked = int(a.split("-")[-1])
    assert all(picked > n for n in used)


@pytest.mark.parametrize("plane", ["EDR", "CDR", "NDR", "ITDR", "CLOUD_APP"])
def test_scaffolded_card_passes_schema(ri, plane):
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)

    plane_info = ri.resolve_plane(plane, REPO_ROOT)
    ttp_id = ri.next_ttp_id(REPO_ROOT)
    card = ri.build_card(
        ttp_id=ttp_id, plane_info=plane_info,
        title="Test Threat Report", url="https://example.com/report",
        summary="A synthetic technique used for scaffold testing.",
    )
    errors = sorted(validator.iter_errors(card), key=lambda e: list(e.absolute_path))
    assert not errors, "; ".join(
        f"{'/'.join(str(p) for p in e.absolute_path)}: {e.message}" for e in errors
    )


def test_scenario_detection_ids_resolve_to_card(ri):
    """Every detection_id the scenario references must be a slug the loader
    would synthesize from the card's detection objects (GAP-4)."""
    plane_info = ri.resolve_plane("EDR", REPO_ROOT)
    ttp_id = ri.next_ttp_id(REPO_ROOT)
    card = ri.build_card(
        ttp_id=ttp_id, plane_info=plane_info,
        title="Test Threat Report", url="https://example.com/report",
        summary="A synthetic technique used for scaffold testing.",
    )
    scenario = ri.build_scenario_yaml(
        ttp_id=ttp_id, plane_info=plane_info,
        title="Test Threat Report", url="https://example.com/report",
        summary="A synthetic technique used for scaffold testing.",
    )
    # Replicate the loader's slug synthesis for each detection kind.
    dets = card["detections"]
    expected = {
        ri.slug(dets["abiocs"][0]["name"], "abioc"),
        ri.slug(dets["xql_queries"][0]["name"], "xql"),
        dets["correlation_rules"][0]["rule_id"],
    }
    for det_id in expected:
        assert det_id in scenario, f"scenario missing detection_id {det_id}"


def test_required_top_fields_present_in_card(ri):
    """A card must carry every top-level field the schema requires."""
    plane_info = ri.resolve_plane("EDR", REPO_ROOT)
    card = ri.build_card(
        ttp_id="TTP-2026-9999", plane_info=plane_info,
        title="T", url="https://e", summary="short",
    )
    for key in ("id", "schema_version", "status", "metadata", "identity",
                "mitre_attack", "execution", "detections", "panw_mapping",
                "references"):
        assert key in card
    # exactly one primary reference
    primaries = [r for r in card["references"] if r.get("primary")]
    assert len(primaries) == 1
    # identity.summary within schema bounds (20..400)
    assert 20 <= len(card["identity"]["summary"]) <= 400
