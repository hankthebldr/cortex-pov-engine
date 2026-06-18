"""Tests for the GAP-ADAPT-02 honest adapter-wiring coverage endpoint."""
from __future__ import annotations

import asyncio
import os

import pytest


@pytest.fixture
def client(make_client):
    from api.tools import router
    return make_client(router)


@pytest.fixture(autouse=True)
def _ensure_adapter_catalog():
    from tools.adapter_catalog import catalog
    base = os.environ.get("CORTEXSIM_BASE_DIR", os.getcwd())
    if catalog.count() == 0:
        catalog.load(os.path.join(base, "tools", "packs"))


def _seed_scenario_with_adapter(SessionLocal, adapter_ref="TOOL-IMPACKET"):
    from models import Scenario

    async def _seed():
        async with SessionLocal() as s:
            s.add(Scenario(
                scenario_id="SIM-X-001", name="x", version="1.0", plane="ITDR",
                status="active", uc_ref="U", tc_ref="T", uc_name="u", tc_name="t",
                mitre_tactic="TA0006", mitre_tactic_name="Credential Access",
                mitre_technique="T1558", mitre_technique_name="Kerberos",
                external_tools=[{"name": "impacket", "adapter_ref": adapter_ref}],
            ))
            await s.commit()
    asyncio.run(_seed())


def test_coverage_classifies_three_ways(client, session_factory):
    _seed_scenario_with_adapter(session_factory)
    r = client.get("/api/tools/adapters/coverage")
    assert r.status_code == 200
    body = r.json()
    s = body["summary"]
    # Every adapter is classified exactly once.
    assert s["wired"] + s["reference_only"] + s["candidates"] == body["total"]
    # The seeded scenario wires TOOL-IMPACKET.
    assert "TOOL-IMPACKET" in body["wired"]
    # Reference-only is non-empty (tier-5 / c2 packs exist in the catalog).
    assert s["reference_only"] > 0
    # Wireable denominator excludes reference-only.
    assert s["wireable_total"] == s["wired"] + s["candidates"]


def test_reference_only_excludes_c2_and_tier5(client, session_factory):
    _seed_scenario_with_adapter(session_factory)
    body = client.get("/api/tools/adapters/coverage").json()
    from tools.adapter_catalog import catalog
    ref_ids = set(body["reference_only"])
    cand_ids = {c["adapter_id"] for c in body["candidates"]}
    for a in catalog.all():
        if a.adapter_id in ref_ids:
            assert a.tier == 5 or a.safety_class == "c2-framework"
        if a.adapter_id in cand_ids:
            # candidates are never reference-only
            assert a.tier != 5 and a.safety_class != "c2-framework"


def test_coverage_route_not_shadowed_by_adapter_id(client):
    # /adapters/coverage must resolve to the coverage handler, not be parsed as
    # an adapter_id by /adapters/{adapter_id}.
    r = client.get("/api/tools/adapters/coverage")
    assert r.status_code == 200
    assert "summary" in r.json()
