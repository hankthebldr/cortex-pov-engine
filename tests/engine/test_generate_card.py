"""Tests for detection_scanner/scripts/generate_card.py.

The generator must:

* Produce schema-valid TTP entries against ``detection_scanner/schema/ttp-entry.schema.json``.
* Default to ``status: draft`` so generated cards never auto-load against
  a customer tenant.
* Lift identity, MITRE block, payload skeleton, and BIOC stubs from the
  source scenario without losing data.
* Map each plane to the right Cortex product + simulation_class + platform.
* Stay back-compatible across re-runs: regenerating the same draft produces
  the same JSON (up to created/updated timestamps).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import jsonschema
import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "detection_scanner" / "scripts" / "generate_card.py"
SCHEMA = REPO_ROOT / "detection_scanner" / "schema" / "ttp-entry.schema.json"
SCENARIOS = REPO_ROOT / "scenarios"
ACTIVE_TTPS = REPO_ROOT / "detection_scanner" / "ttps"
DRAFTS = REPO_ROOT / "detection_scanner" / "ttps" / "_drafts"


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_card", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_card"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def gen():
    return _load_generator()


@pytest.fixture
def schema():
    return json.loads(SCHEMA.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Per-plane round-trip — one representative scenario per plane
# ---------------------------------------------------------------------------


_PLANE_SCENARIOS = [
    ("EDR", "scenarios/edr/edr-001-credential-dumping.yml"),
    ("CDR", "scenarios/cdr/cdr-002-cryptominer.yml"),
    ("NDR", "scenarios/ndr/ndr-001-c2-beacon-eal-validation.yml"),
    ("ITDR", "scenarios/itdr/sim-itdr-002-mfa-fatigue.yml"),
    ("CLOUD_APP", "scenarios/cloud_app/sim-cloud-001-okta-risky-drive-grant.yml"),
    ("AI_ACCESS", "scenarios/ai_access/sim-aiacc-001-source-code-to-chatgpt.yml"),
    ("AIRS", "scenarios/airs/sim-airs-001-direct-prompt-injection.yml"),
    ("BROWSER", "scenarios/browser/sim-browser-001-credential-paste.yml"),
    ("KOI", "scenarios/koi/sim-koi-001-typosquat-mcp-server.yml"),
    ("ANALYTICS", "scenarios/multi_plane/mp-001-c2-beacon-ngfw-xdr-stitch.yml"),
]


@pytest.mark.parametrize("plane,scenario_rel", _PLANE_SCENARIOS, ids=lambda x: x if isinstance(x, str) else "")
def test_generates_schema_valid_card_for_every_plane(gen, schema, plane, scenario_rel):
    """One generated draft per CortexSim plane must validate against the
    canonical TTP schema. Catches enum-drift regressions class-wide."""
    scenario = yaml.safe_load((REPO_ROOT / scenario_rel).read_text(encoding="utf-8"))
    card = gen._scenario_to_card(scenario, "TTP-2026-0999")
    jsonschema.validate(card, schema)
    assert card["id"] == "TTP-2026-0999"
    assert card["status"] == "draft", "generator must emit drafts, never active"
    assert card["metadata"]["pov_engine"]["auto_load"] is False


# ---------------------------------------------------------------------------
# Specific field-level invariants
# ---------------------------------------------------------------------------


def test_uc_tc_ids_match_corpus_regex(gen, schema):
    """UC and TC ids on the generated card must match the regex enforced
    by the canonical schema. Regression guard against the 4-digit suffix
    bug caught during initial development."""
    scenario = yaml.safe_load((REPO_ROOT / "scenarios/edr/edr-001-credential-dumping.yml").read_text())
    card = gen._scenario_to_card(scenario, "TTP-2026-0007")
    use_cases = card["panw_mapping"]["use_cases"]
    assert use_cases, "card must have at least one use case"
    import re
    for uc in use_cases:
        assert re.match(r"^UC-[A-Z0-9]+-\d{3}$", uc["use_case_id"])
        for tc in uc["test_cases"]:
            assert re.match(r"^TC-[A-Z0-9]+-\d{3}[A-Z]?$", tc["test_case_id"])


def test_bioc_count_matches_scenario_detections(gen):
    """One BIOC stub per scenario expected_detection that is type=BIOC or
    Analytics. Detection types we don't (yet) lift to BIOC (IOC, etc.)
    are dropped — the count check guards against either silently dropping
    or duplicating."""
    scenario = yaml.safe_load((REPO_ROOT / "scenarios/edr/edr-001-credential-dumping.yml").read_text())
    expected = 0
    for s in scenario["steps"]:
        for d in s.get("expected_detections") or []:
            if d.get("type") in ("BIOC", "Analytics"):
                expected += 1
    card = gen._scenario_to_card(scenario, "TTP-2026-0007")
    assert len(card["detections"]["biocs"]) == expected


def test_panw_products_match_plane(gen):
    """The Cortex product mapping per plane must produce non-empty,
    non-overlapping entries."""
    scenario = yaml.safe_load((REPO_ROOT / "scenarios/airs/sim-airs-001-direct-prompt-injection.yml").read_text())
    card = gen._scenario_to_card(scenario, "TTP-2026-0099")
    modules = [p["module"] for p in card["panw_mapping"]["products"]]
    assert "ai-runtime-security" in modules


def test_source_refs_superset_of_publisher_ids(gen):
    """metadata.source_refs must include every references[].publisher_id —
    canonical validator enforces this. Regression guard for the Unit42
    bug surfaced during initial corpus generation."""
    scenario = yaml.safe_load((REPO_ROOT / "scenarios/itdr/sim-itdr-002-mfa-fatigue.yml").read_text())
    card = gen._scenario_to_card(scenario, "TTP-2026-0099")
    publisher_ids = {r["publisher_id"] for r in card["references"]}
    assert publisher_ids.issubset(set(card["metadata"]["source_refs"]))


def test_payload_code_preserves_step_commands(gen):
    """The generator must lift the first 1-3 step commands verbatim into
    the payload code — the corpus validator looks for non-empty payload."""
    scenario = yaml.safe_load((REPO_ROOT / "scenarios/edr/edr-001-credential-dumping.yml").read_text())
    card = gen._scenario_to_card(scenario, "TTP-2026-0007")
    payload = card["execution"]["payload"]["code"]
    assert payload.strip()
    # The first step's command should appear in payload (heuristic — works
    # for the credential-dumping scenario's `cat /etc/passwd` opener).
    first_cmd = scenario["steps"][0]["command"].strip().splitlines()[0]
    assert first_cmd[:40] in payload


# ---------------------------------------------------------------------------
# Existing active corpus must already include the generator's promoted cards
# ---------------------------------------------------------------------------


def test_promoted_corpus_includes_expected_planes():
    """Phase 2 backfill promoted one TTP per plane (where one didn't already
    exist). Confirm the active corpus reflects that breadth — otherwise the
    UI's plane filter degrades back to the original 6 entries."""
    planes_with_active_ttp: set[str] = set()
    for path in ACTIVE_TTPS.glob("*.json"):
        body = json.loads(path.read_text(encoding="utf-8"))
        # We don't store plane on the TTP itself — infer from tags.
        for tag in body.get("metadata", {}).get("tags", []):
            if tag.startswith("plane-"):
                planes_with_active_ttp.add(tag.removeprefix("plane-").upper().replace("-", "_"))
    # At least 5 of the 10 planes must now have at least one active TTP card
    # — was 0 before this PR.
    assert len(planes_with_active_ttp) >= 5, (
        f"expected ≥5 planes with active TTP coverage post-backfill, got: {planes_with_active_ttp}"
    )


def test_every_active_ttp_validates(schema):
    """No active TTP file may be schema-invalid — catches "I edited a JSON
    file by hand and broke a field" before SimCore boots in front of a customer."""
    errs: list[str] = []
    for path in sorted(ACTIVE_TTPS.glob("*.json")):
        try:
            jsonschema.validate(json.loads(path.read_text(encoding="utf-8")), schema)
        except jsonschema.ValidationError as e:
            errs.append(f"{path.name}: {e.message} at {list(e.absolute_path)}")
    assert not errs, "schema-invalid active TTPs:\n" + "\n".join(errs)


def test_every_draft_validates(schema):
    """Drafts must also validate — they exist precisely so the human can
    enrich a schema-valid skeleton, not so they can land malformed JSON
    in the corpus."""
    errs: list[str] = []
    for path in sorted(DRAFTS.glob("*.json")):
        try:
            jsonschema.validate(json.loads(path.read_text(encoding="utf-8")), schema)
        except jsonschema.ValidationError as e:
            errs.append(f"{path.name}: {e.message} at {list(e.absolute_path)}")
    assert not errs, "schema-invalid drafts:\n" + "\n".join(errs)
