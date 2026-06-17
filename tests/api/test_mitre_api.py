"""Direct router tests for /api/mitre/coverage.

The endpoint joins Scenario + Run + Result into a per-technique status map
with a tactic grouping suitable for the heatmap UI.  Tests cover:

  - empty repo → empty arrays + zero summary
  - one scenario, no run → status "not_run"
  - one scenario + one run, no observations → "run_not_detected"
  - one scenario + observed result → "detected"
  - per-step technique override picks up extra techniques
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest


@pytest.fixture
def client(make_client):
    from api.mitre import router
    return make_client(router)


def _seed(session_factory, *, observed: bool = False, with_run: bool = False):
    from models import Scenario, Run, Result

    async def _do():
        async with session_factory() as db:
            s = Scenario(
                scenario_id="SIM-EDR-001",
                name="Credential Dumping",
                plane="EDR",
                version="1.0",
                status="active",
                uc_ref="UCS-EDR-01",
                uc_name="X",
                tc_ref="TC-EDR-01",
                tc_name="Y",
                mitre_tactic="TA0006",
                mitre_tactic_name="Credential Access",
                mitre_technique="T1003.008",
                mitre_technique_name="Shadow Dump",
                steps=[
                    {"id": "step-01", "mitre_technique": "T1087.001"},  # per-step override
                    {"id": "step-02", "mitre_technique": "T1003.008"},
                ],
            )
            db.add(s)
            if with_run:
                db.add(
                    Run(
                        run_id="r-1",
                        scenario_id="SIM-EDR-001",
                        mode="push",
                        status="complete",
                        started_at=datetime.utcnow(),
                    )
                )
                db.add(
                    Result(
                        run_id="r-1",
                        step_id="step-02",
                        plane="EDR",
                        signal_type="BIOC",
                        expected_detection="d",
                        observed=observed,
                        observed_at=datetime.utcnow() if observed else None,
                        executed_at=datetime.utcnow(),
                    )
                )
            await db.commit()

    asyncio.run(_do())


def test_coverage_empty(client):
    r = client.get("/api/mitre/coverage")
    assert r.status_code == 200
    body = r.json()
    assert body["techniques"] == []
    assert body["summary"]["total_techniques"] == 0


def test_coverage_scenario_only_is_not_run(client, session_factory):
    _seed(session_factory, with_run=False)
    body = client.get("/api/mitre/coverage").json()
    statuses = {t["technique_id"]: t["status"] for t in body["techniques"]}
    assert statuses["T1003.008"] == "not_run"
    # per-step technique surfaced too
    assert "T1087.001" in statuses


def test_coverage_run_no_detection(client, session_factory):
    _seed(session_factory, with_run=True, observed=False)
    body = client.get("/api/mitre/coverage").json()
    s = next(t for t in body["techniques"] if t["technique_id"] == "T1003.008")
    assert s["status"] == "run_not_detected"
    assert s["total_detections"] == 1
    assert s["observed_detections"] == 0


def test_coverage_detected(client, session_factory):
    _seed(session_factory, with_run=True, observed=True)
    body = client.get("/api/mitre/coverage").json()
    s = next(t for t in body["techniques"] if t["technique_id"] == "T1003.008")
    assert s["status"] == "detected"
    assert s["observed_detections"] == 1
    assert s["coverage_pct"] == 100.0
    assert body["summary"]["detected"] >= 1


def test_coverage_groups_by_tactic(client, session_factory):
    _seed(session_factory, with_run=True, observed=True)
    body = client.get("/api/mitre/coverage").json()
    tactic_ids = [t["tactic_id"] for t in body["by_tactic"]]
    assert "TA0006" in tactic_ids


# ---------------------------------------------------------------------------
# GAP-6 / GAP-5 / GAP-API-009 — card-corpus fusion + additional_techniques +
# explicit/optional status filter. These lock the contract the coverage UI and
# the parallel lanes consume.
# ---------------------------------------------------------------------------


def _seed_with_card_and_additional(session_factory):
    """Seed a scenario that references a real in-tree TTP card via a step's
    ``expected_detections[].ttp_ref`` AND declares an additional_technique."""
    from models import Scenario

    async def _do():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id="SIM-EDR-001",
                name="Credential Dumping",
                plane="EDR",
                version="1.0",
                status="active",
                uc_ref="UCS-EDR-01", uc_name="X",
                tc_ref="TC-EDR-01", tc_name="Y",
                mitre_tactic="TA0006",
                mitre_tactic_name="Credential Access",
                mitre_technique="T1003",
                mitre_technique_name="OS Credential Dumping",
                additional_techniques=[
                    {"technique": "T1552.001", "name": "Unsecured Credentials: Credentials In Files"},
                ],
                steps=[
                    {"id": "step-01", "mitre_technique": "T1003",
                     "expected_detections": [
                         {"plane": "EDR", "type": "BIOC",
                          "description": "d", "ttp_ref": "TTP-2026-0032"},
                     ]},
                ],
            ))
            await db.commit()

    asyncio.run(_do())


def _load_corpus():
    """Load the in-tree TTP corpus into the module-level catalog singleton."""
    import os
    from engine.ttp_catalog import catalog, default_corpus_dir
    base = os.environ["CORTEXSIM_BASE_DIR"]
    catalog.load(default_corpus_dir(base))


def test_coverage_fuses_card_corpus_techniques(client, session_factory):
    """A scenario referencing TTP-2026-0032 should pull in the card's full
    multi-technique surface (e.g. T1087.001) even though no scenario field
    names it directly — that's the GAP-6 fusion."""
    _load_corpus()
    _seed_with_card_and_additional(session_factory)
    body = client.get("/api/mitre/coverage").json()
    by_id = {t["technique_id"]: t for t in body["techniques"]}
    # T1087.001 is in card TTP-2026-0032's mitre_attack.techniques[] but is
    # NOT the scenario primary / step / additional technique.
    assert "T1087.001" in by_id, "card-corpus technique not fused"
    t = by_id["T1087.001"]
    assert t["sources"] == ["card"]
    assert "TTP-2026-0032" in t["card_refs"]
    assert t["in_card_corpus"] is True


def test_coverage_fuses_additional_techniques(client, session_factory):
    """additional_techniques (GAP-5) must reach the heatmap."""
    _load_corpus()
    _seed_with_card_and_additional(session_factory)
    body = client.get("/api/mitre/coverage").json()
    by_id = {t["technique_id"]: t for t in body["techniques"]}
    assert "T1552.001" in by_id
    assert "additional" in by_id["T1552.001"]["sources"]


def test_coverage_surfaces_correlation_detection_kind(client, session_factory):
    """Card detection-kind tally must surface correlation backing (GAP-2)."""
    _load_corpus()
    _seed_with_card_and_additional(session_factory)
    body = client.get("/api/mitre/coverage").json()
    by_id = {t["technique_id"]: t for t in body["techniques"]}
    dk = by_id["T1003"]["detection_kinds"]
    assert set(dk) == {"bioc", "xql", "correlation", "ioc", "analytics", "abioc", "modeling"}
    # TTP-2026-0032 carries at least one correlation rule.
    assert dk["correlation"] >= 1
    assert body["summary"]["with_correlation"] >= 1


def test_coverage_additive_fields_present_and_backward_compatible(client, session_factory):
    """All legacy keys preserved; new additive keys present."""
    _load_corpus()
    _seed_with_card_and_additional(session_factory)
    body = client.get("/api/mitre/coverage").json()
    t = body["techniques"][0]
    for legacy in ("technique_id", "technique_name", "tactic_id", "tactic_name",
                   "scenarios", "planes", "status", "total_detections",
                   "observed_detections", "coverage_pct"):
        assert legacy in t, f"legacy key {legacy} dropped"
    for additive in ("sources", "card_refs", "in_card_corpus", "detection_kinds"):
        assert additive in t, f"additive key {additive} missing"
    for legacy in ("total_techniques", "detected", "run_not_detected", "not_run"):
        assert legacy in body["summary"]
    assert body["summary"]["in_card_corpus"] >= 1


def test_coverage_include_inactive_filter(client, session_factory):
    """GAP-API-009 — draft scenarios are excluded by default, included on demand."""
    from models import Scenario

    async def _do():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id="SIM-DRAFT-001", name="Draft", plane="EDR",
                version="1.0", status="draft",
                uc_ref="UCS-X-01", uc_name="X", tc_ref="TC-X-01", tc_name="Y",
                mitre_tactic="TA0001", mitre_tactic_name="Initial Access",
                mitre_technique="T1189", mitre_technique_name="Drive-by",
            ))
            await db.commit()

    asyncio.run(_do())

    # Default: draft excluded.
    default_body = client.get("/api/mitre/coverage").json()
    assert "T1189" not in {t["technique_id"] for t in default_body["techniques"]}
    assert default_body["summary"]["include_inactive"] is False

    # include_inactive=true: draft included.
    inc_body = client.get("/api/mitre/coverage?include_inactive=true").json()
    assert "T1189" in {t["technique_id"] for t in inc_body["techniques"]}
    assert inc_body["summary"]["include_inactive"] is True
