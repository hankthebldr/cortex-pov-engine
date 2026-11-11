"""The pack's own reason for not being shelf-backed reaches the operator.

Every tier-4 pack must declare ``install.artifact`` or ``install.artifact_exempt
{reason_code, reason, revisit?}`` (TA-13). The exemption was validated at load
and then dropped on the way out: ``payload_shelf.required_artifacts`` built
every ``UnstagedAdapter`` with ONE hard-coded sentence, and
``api.tools._adapter_summary`` carried nothing. Observed on c852eac over the
real catalog:

    unstaged_adapters[] for the 48 exempt tier-4 packs -> 1 distinct `reason`,
    no `reason_code`, no `revisit`

so a DC could not tell ``hydra`` (a distro package: settled, never shelvable)
from ``gobuster`` (blocked on an extractor: backlog) — a decision from an
omission. The same sentence also said a tier-3 pack "fetches this tool from the
public internet at dispatch", which the loader's own TA-15 text contradicts.

Counts are COUNTED from the catalog here, never quoted.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKS_DIR = REPO_ROOT / "tools" / "packs"
GENERIC = "pack declares no install.artifact"


@pytest.fixture(scope="module")
def real_packs():
    from tools.adapter_catalog import AdapterCatalog

    cat = AdapterCatalog()
    cat.load(str(PACKS_DIR))
    assert cat.count() > 0
    return cat


@pytest.fixture
def wired(real_packs, monkeypatch):
    """Point the process-wide catalog at the real corpus for one test."""
    from tools.adapter_catalog import catalog

    monkeypatch.setattr(catalog, "_by_id", dict(real_packs._by_id))
    return catalog


def _exempt(cat):
    return [a for a in cat.all() if a.install.artifact_exempt is not None]


def test_the_corpus_has_exempt_packs_to_check(real_packs):
    """A zero is degraded, not ok: these tests are vacuous without them."""
    assert len(_exempt(real_packs)) >= 10
    assert len({a.install.artifact_exempt.reason_code for a in _exempt(real_packs)}) >= 3


def test_every_exempt_pack_reaches_unstaged_with_its_own_reason(wired):
    from engine.payload_shelf import required_artifacts

    exempt = _exempt(wired)
    _, unstaged = required_artifacts(adapter_refs=[a.adapter_id for a in exempt])
    by_id = {u.adapter_id: u for u in unstaged}

    assert set(by_id) == {a.adapter_id for a in exempt}
    for a in exempt:
        ex, u = a.install.artifact_exempt, by_id[a.adapter_id]
        assert u.reason_code == ex.reason_code, a.adapter_id
        assert u.reason == ex.reason, a.adapter_id
        assert u.revisit == ex.revisit, a.adapter_id
        assert u.tier == 4
        assert GENERIC not in u.reason, a.adapter_id
    # Not one sentence for all of them any more.
    assert len({u.reason for u in unstaged}) > len(exempt) // 2
    assert {u.reason_code for u in unstaged} == {a.install.artifact_exempt.reason_code
                                                 for a in exempt}


def test_backlog_packs_carry_their_revisit_action(wired):
    """The three codes that describe a CortexSim limitation are the backlog;
    each must arrive with the action that would close it (TA-17)."""
    from engine.payload_shelf import required_artifacts
    from tools.adapter_loader import _REVISITABLE_REASON_CODES

    backlog = [a for a in _exempt(wired)
               if a.install.artifact_exempt.reason_code in _REVISITABLE_REASON_CODES]
    assert backlog, "no backlog pack in the corpus — recount before trusting this test"
    _, unstaged = required_artifacts(adapter_refs=[a.adapter_id for a in backlog])
    assert all((u.revisit or "").strip() for u in unstaged)


def test_the_wire_shape_carries_code_reason_and_revisit(wired):
    from engine.payload_shelf import compose

    pick = _exempt(wired)[0]
    plan = compose(scenario={"scenario_id": "SIM-T", "external_tools": [
        {"adapter_ref": pick.adapter_id}]}, consumer="console",
        server_url="http://sim:8888")
    entry = plan.to_dict()["unstaged_adapters"][0]
    ex = pick.install.artifact_exempt
    assert entry == {"adapter_id": pick.adapter_id, "reason_code": ex.reason_code,
                     "reason": ex.reason, "revisit": ex.revisit, "tier": 4}
    assert plan.air_gapped is False


def test_an_unknown_adapter_ref_has_a_machine_code(wired):
    from engine.payload_shelf import required_artifacts

    _, unstaged = required_artifacts(adapter_refs=["TOOL-DOES-NOT-EXIST"])
    assert unstaged[0].reason_code == "ADAPTER_NOT_IN_CATALOG"
    assert unstaged[0].reason.startswith("ADAPTER_NOT_IN_CATALOG")
    assert unstaged[0].tier is None


@pytest.mark.parametrize("tier, code", [(1, "SOURCE_TREE_ON_DISK"), (2, "SOURCE_TREE_ON_DISK"),
                                        (3, "IAC_PROVISIONED"), (5, "EXTERNAL_ONLY")])
def test_a_pack_that_is_not_tier_4_is_not_described_as_an_internet_fetch(wired, tier, code):
    """Only tier 4 fetches its tool at dispatch (adapter_loader TA-15). The old
    sentence said every artifact-less pack does."""
    from engine.payload_shelf import required_artifacts

    packs = [a for a in wired.all() if a.tier == tier]
    if not packs:
        pytest.skip(f"no tier-{tier} pack in the corpus")
    _, unstaged = required_artifacts(adapter_refs=[a.adapter_id for a in packs])
    assert {u.adapter_id for u in unstaged} == {a.adapter_id for a in packs}
    for u in unstaged:
        assert (u.reason_code, u.tier) == (code, tier), u
        assert "public internet" not in u.reason, u.reason
        assert GENERIC not in u.reason


def test_a_staged_pack_is_not_unstaged(wired):
    from engine.payload_shelf import required_artifacts

    staged = [a for a in wired.all() if a.install.artifact is not None]
    assert staged
    requests, unstaged = required_artifacts(adapter_refs=[a.adapter_id for a in staged])
    assert unstaged == [] and len(requests) == len(staged)


# -- GET /api/tools/adapters ------------------------------------------------


@pytest.fixture
def client(wired):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.tools import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


def test_the_adapter_list_says_why_each_tier_4_pack_is_or_is_not_on_the_shelf(client, wired):
    rows = {r["adapter_id"]: r for r in client.get("/api/tools/adapters").json()["adapters"]}
    assert len(rows) == wired.count()

    for a in wired.all():
        row = rows[a.adapter_id]
        ex = a.install.artifact_exempt
        assert row["shelf_backed"] is (a.install.artifact is not None), a.adapter_id
        if ex is None:
            assert row["artifact_exempt"] is None, a.adapter_id
        else:
            assert row["artifact_exempt"] == {"reason_code": ex.reason_code,
                                              "reason": ex.reason,
                                              "revisit": ex.revisit}, a.adapter_id

    tier4 = [r for r in rows.values() if r["tier"] == 4]
    assert tier4 and all(r["shelf_backed"] != bool(r["artifact_exempt"]) for r in tier4), (
        "every tier-4 pack is exactly one of shelf-backed or exempt-with-a-reason")
