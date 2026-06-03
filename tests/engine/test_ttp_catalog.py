"""Tests for the Phase 1 TTP catalog and scenario-to-card bridge.

Covers:

* TTP catalog loads the in-tree corpus and exposes resolvable detection
  cards by ``(ttp_ref, detection_id)``.
* Slug synthesis is stable for the four card kinds (bioc / xql /
  correlation / ioc).
* Scenario steps that carry ``ttp_ref + detection_id`` resolve against the
  catalog so the orchestrator can copy logic onto Result rows.
* Dangling references log warnings rather than failing scenario load.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TTPS_DIR = REPO_ROOT / "detection_scanner" / "ttps"
SCENARIOS_DIR = REPO_ROOT / "scenarios"


@pytest.fixture
def catalog():
    """Fresh catalog loaded from the in-tree corpus."""
    from engine.ttp_catalog import TtpCatalog  # noqa: PLC0415

    cat = TtpCatalog()
    cat.load(str(TTPS_DIR))
    return cat


def test_catalog_loads_inrepo_corpus(catalog):
    """At minimum the committed TTP files must index. Issue #58 promoted
    three AI Access drafts (0008/0009/0010) — assert they're indexed
    AND in active status so the browser surfaces them without a
    status filter."""
    entries = catalog.all_entries()
    assert len(entries) >= 6, f"expected ≥6 TTP entries, got {len(entries)}"

    by_ref = {e.ttp_ref for e in entries}
    expected = {
        "TTP-2026-0001",
        "TTP-2026-0002",
        "TTP-2026-0003",
        "TTP-2026-0004",
        "TTP-2026-0005",
        "TTP-2026-0006",
    }
    assert expected.issubset(by_ref)


def test_catalog_indexes_promoted_aiacc_drafts(catalog):
    """Regression for issue #58 — 0008/0009/0010 promoted out of
    _drafts/ to active. Verifies both the entry is reachable AND the
    summary isn't the auto-generated placeholder."""
    for ttp_id in ("TTP-2026-0008", "TTP-2026-0009", "TTP-2026-0010"):
        entry = catalog.get_entry(ttp_id)
        assert entry is not None, f"{ttp_id} not loaded — still in _drafts/?"
        assert entry.status == "active", (
            f"{ttp_id} is status={entry.status}; promotion didn't land"
        )
        # The raw dict surfaces the customer-grade summary the API
        # serves via /api/ttps/{id}.
        raw = catalog.raw(ttp_id)
        assert raw is not None
        summary = raw.get("identity", {}).get("summary", "")
        assert "Auto-generated draft" not in summary, (
            f"{ttp_id} still has the auto-generated draft summary — "
            "promotion didn't include a real human edit"
        )
        assert "Requires human" not in summary
        assert len(summary) > 80, (
            f"{ttp_id} summary is too short ({len(summary)}ch) to be "
            "customer-grade"
        )


def test_catalog_resolves_known_bioc(catalog):
    """A BIOC name from TTP-2026-0002 must resolve via its slugged id."""
    card = catalog.find(
        "TTP-2026-0002",
        "bioc-lsass-handle-open-with-sensitive-access-rights",
    )
    assert card is not None
    assert card.kind == "bioc"
    assert card.severity == "critical"
    assert card.logic and "lsass.exe" in card.logic.lower()


def test_catalog_resolves_correlation_by_rule_id(catalog):
    """Correlation rules expose their ``rule_id`` verbatim as the detection_id."""
    card = catalog.find("TTP-2026-0004", "CR-CRED-0003")
    assert card is not None
    assert card.kind == "correlation"


def test_catalog_returns_none_for_missing_pair(catalog):
    assert catalog.find("TTP-2026-9999", "bioc-nope") is None
    assert catalog.find(None, None) is None
    assert catalog.find("TTP-2026-0002", None) is None


def test_catalog_handles_missing_dir(tmp_path):
    """Empty/missing corpus dir loads zero cards without raising."""
    from engine.ttp_catalog import TtpCatalog  # noqa: PLC0415

    cat = TtpCatalog()
    n = cat.load(str(tmp_path / "does-not-exist"))
    assert n == 0
    assert cat.all_entries() == []


def test_score_weights_aggregated_per_use_case(catalog):
    """expected_score_weight sums are surfaced per use_case_id."""
    entry = catalog.get_entry("TTP-2026-0002")
    assert entry is not None
    assert "UC-RANSOM-002" in entry.score_weights
    # The TTP-2026-0002 entry weights sum to 0.6 + 0.2 + 0.2 = 1.0.
    assert abs(entry.score_weights["UC-RANSOM-002"] - 1.0) < 1e-6


def test_scenario_bridge_round_trip():
    """The mp-002 scenario step we backfilled must resolve every ttp_ref."""
    from engine.ttp_catalog import TtpCatalog  # noqa: PLC0415

    cat = TtpCatalog()
    cat.load(str(TTPS_DIR))

    raw = yaml.safe_load(
        (SCENARIOS_DIR / "multi_plane" / "mp-002-kerberoast-lateral-smb.yml").read_text(
            encoding="utf-8"
        )
    )
    bridged = 0
    unresolved = []
    for step in raw["steps"]:
        for det in step.get("expected_detections", []):
            ref = det.get("ttp_ref")
            did = det.get("detection_id")
            if not ref or not did:
                continue
            card = cat.find(ref, did)
            if card is None:
                unresolved.append((step["id"], ref, did))
            else:
                bridged += 1
    assert not unresolved, f"unresolved TTP refs in mp-002: {unresolved}"
    assert bridged >= 3, "expected at least 3 bridged detections in mp-002 step-04"


def test_scenario_loader_warns_on_dangling_ref(tmp_path, caplog):
    """A scenario with an unresolved ttp_ref logs a warning but does NOT raise."""
    from engine.scenario_loader import ScenarioSchema, _warn_dangling_ttp_refs  # noqa: PLC0415

    raw = {
        "scenario_id": "SIM-EDR-999",
        "name": "fixture",
        "version": "1.0",
        "status": "active",
        "plane": "EDR",
        "detection_types": ["BIOC"],
        "uc_ref": "UCS-EDR-99",
        "tc_ref": "TC-EDR-99",
        "uc_name": "fixture",
        "tc_name": "fixture",
        "mitre_tactic": "TA0006",
        "mitre_tactic_name": "Credential Access",
        "mitre_technique": "T1003",
        "mitre_technique_name": "OS Credential Dumping",
        "execution_identity": {"default": "root", "options": ["root"]},
        "push_supported": True,
        "pull_supported": True,
        "steps": [
            {
                "id": "step-01",
                "name": "fixture",
                "command": "echo hi",
                "identity": "root",
                "mitre_technique": "T1003",
                "expected_detections": [
                    {
                        "plane": "EDR",
                        "type": "BIOC",
                        "description": "fixture",
                        "ttp_ref": "TTP-2099-9999",
                        "detection_id": "bioc-does-not-exist",
                    }
                ],
            }
        ],
    }
    schema = ScenarioSchema.model_validate(raw)
    with caplog.at_level(logging.WARNING, logger="cortexsim.loader"):
        _warn_dangling_ttp_refs(schema, "fixture.yml")
    assert any("unresolved TTP card" in rec.message for rec in caplog.records)
