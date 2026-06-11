"""Tests for the MITRE ATLAS coverage endpoint (GAP-9, AI-plane threats)."""
from __future__ import annotations

import asyncio

import pytest


@pytest.fixture
def client(make_client):
    from api.mitre import router
    return make_client(router)


def _seed_airs_scenario(SessionLocal):
    """Seed an AIRS scenario that references an ATLAS-mapped card."""
    from models import Scenario

    async def _seed():
        async with SessionLocal() as s:
            s.add(Scenario(
                scenario_id="SIM-AIRS-001", name="Direct Prompt Injection",
                version="1.0", plane="AIRS", status="active",
                uc_ref="UCS-AIRS-01", tc_ref="TC-AIRS-01",
                uc_name="LLM Runtime Defense", tc_name="Direct Prompt Injection",
                mitre_tactic="TA0001", mitre_tactic_name="Initial Access",
                mitre_technique="T1656", mitre_technique_name="Impersonation",
                steps=[{
                    "id": "step-02", "mitre_technique": "T1656",
                    "expected_detections": [
                        {"plane": "AIRS", "type": "BIOC", "ttp_ref": "TTP-2026-0012",
                         "detection_id": "x"},
                    ],
                }],
            ))
            await s.commit()
    asyncio.run(_seed())


@pytest.fixture(autouse=True)
def _ensure_catalog_loaded():
    # The catalog is a process singleton; ensure it's populated for the join.
    from engine.ttp_catalog import catalog
    import os
    base = os.environ.get("CORTEXSIM_BASE_DIR", os.getcwd())
    if not catalog._raw_by_ttp:  # noqa: SLF001
        catalog.load(os.path.join(base, "detection_scanner", "ttps"))


def test_atlas_coverage_empty_when_no_scenarios(client):
    r = client.get("/api/mitre/atlas/coverage")
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["total_atlas_techniques"] == 0
    assert body["atlas_techniques"] == []


def test_atlas_coverage_surfaces_card_mappings(client, session_factory):
    _seed_airs_scenario(session_factory)
    r = client.get("/api/mitre/atlas/coverage")
    assert r.status_code == 200
    body = r.json()
    ids = {t["atlas_id"] for t in body["atlas_techniques"]}
    # TTP-2026-0012 maps to LLM Prompt Injection (Direct) + LLM Jailbreak.
    assert "AML.T0051.000" in ids
    assert "AML.T0054" in ids
    # Each surfaced technique names the scenario + plane that reaches it.
    pi = next(t for t in body["atlas_techniques"] if t["atlas_id"] == "AML.T0051.000")
    assert "SIM-AIRS-001" in pi["scenarios"]
    assert "AIRS" in pi["planes"]
    assert "TTP-2026-0012" in pi["card_refs"]
    # Grouped by ATLAS tactic.
    tactics = {b["atlas_tactic"] for b in body["by_tactic"]}
    assert "Initial Access" in tactics
    assert body["summary"]["planes_covered"] == ["AIRS"]
