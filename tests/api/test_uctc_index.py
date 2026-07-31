"""Direct router tests for the UC/TC index API.

Both halves of the join are exercised against real data: the registry singleton
is loaded from the committed v2.2 snapshot, and the scenario catalog is seeded
from ``crosswalk-v2.2.csv`` — the same 161 ``tc_refs`` sets the loader writes to
the DB at boot. That is what makes the coverage assertions meaningful: a made-up
fixture would confirm the arithmetic but not the ground truth (84 of 266, 65 of
107) this surface exists to report.

These counts MOVE whenever the crosswalk moves. That is deliberate — they are the
tripwire on coverage drift, and they caught the alignment corrections that took
evidenced from 81 to 84. When a crosswalk change is intentional, re-run
``python3 scripts/uctc_crosswalk_v2.2.py --report`` and update them here.
"""
from __future__ import annotations

import asyncio
import csv
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INDEX_DIR = REPO_ROOT / "docs" / "uc_tc_mapping" / "_v2.2-source"
CROSSWALK = REPO_ROOT / "docs" / "uc_tc_mapping" / "crosswalk-v2.2.csv"


@pytest.fixture(scope="module", autouse=True)
def _load_registry():
    """Router handlers read the process-level singleton, which only the FastAPI
    lifespan normally loads — without this every assertion would pass on zero
    rows."""
    from engine.uctc_registry import default_index_dir, registry  # noqa: PLC0415

    registry.load(default_index_dir(str(REPO_ROOT)))
    assert len(registry.all_test_cases()) == 266


@pytest.fixture
def client(make_client):
    from api.uctc import router  # noqa: PLC0415
    return make_client(router)


@pytest.fixture
def seeded(session_factory):
    """Seed the real 161-scenario evidence set from the crosswalk.

    Only the columns the reverse index touches are populated; the rest carry
    placeholders because this surface never reads them.
    """
    from models import Scenario  # noqa: PLC0415

    rows = list(csv.DictReader(CROSSWALK.open(encoding="utf-8")))
    assert len(rows) == 161

    async def _seed() -> None:
        async with session_factory() as session:
            for r in rows:
                refs = r["new_tc_refs"].split()
                session.add(Scenario(
                    scenario_id=r["scenario_id"],
                    name=r["scenario_id"],
                    version="1.0.0",
                    status="active",
                    plane=r["plane"],
                    detection_types=["BIOC"],
                    uc_ref=r["new_uc_ref"],
                    tc_ref=r["new_tc_ref"],
                    tc_refs=refs,
                    pov_scenario_id=r["pov_scenario_id"],
                    uc_name=r["new_uc_name"],
                    tc_name=r["new_tc_name"],
                    mitre_tactic="TA0006",
                    mitre_tactic_name="Credential Access",
                    mitre_technique="T1003",
                    mitre_technique_name="OS Credential Dumping",
                    moat_tier=r["old_moat_tier"] or None,
                ))
            await session.commit()

    asyncio.run(_seed())
    return rows


# ---------------------------------------------------------------------------
# Summary — the header numbers
# ---------------------------------------------------------------------------


def test_summary_reports_the_index_totals(client, seeded):
    body = client.get("/api/uctc/summary").json()
    assert body["index_loaded"] is True
    assert body["index_version"] == "2.2"
    assert body["totals"] == {
        "use_cases": 49, "ucs_groups": 203, "test_cases": 266,
        "payloads": 140, "skus": 38,
    }
    assert body["by_validation_class"] == {"DET": 102, "HNT": 5, "POS": 110,
                                           "PLT": 43, "AUT": 6}
    assert body["by_tier"] == {"MOAT": 89, "LEAD": 84, "PARITY": 90, "EMERGING": 3}
    assert body["by_priority"] == {"P1": 89, "P2": 87, "P3": 90}
    assert body["by_sheet"] == {"SecOps": 146, "Cloud": 120}


def test_summary_reports_the_evidence_ground_truth(client, seeded):
    ev = client.get("/api/uctc/summary").json()["evidence"]
    assert ev["evidenced"] == 84
    assert ev["detection_backable"] == 107
    assert ev["evidenced_detection_backable"] == 65
    # Over half the detection-backable surface carries no measurable threshold,
    # so the verifier can never return a pass for it.
    assert ev["unscoreable"] == 57
    assert "EDR" in ev["planes"] and "CDR" in ev["planes"]


# ---------------------------------------------------------------------------
# Hierarchy — UC → UCS → TC
# ---------------------------------------------------------------------------


def test_use_case_list_covers_the_index(client, seeded):
    body = client.get("/api/uctc/use-cases").json()
    assert body["total"] == 49
    assert [u["uc_id"] for u in body["use_cases"]] == sorted(
        u["uc_id"] for u in body["use_cases"]
    )
    assert sum(u["counts"]["test_cases"] for u in body["use_cases"]) == 266


def test_use_case_entry_carries_split_entitlements(client, seeded):
    body = client.get("/api/uctc/use-cases").json()
    edr = next(u for u in body["use_cases"] if u["uc_id"] == "UC-EDR")
    assert edr["base_platform_list"], "expected the licence contract split into chips"
    assert "·" not in "".join(edr["base_platform_list"] + edr["addons_list"])


def test_use_case_detail_returns_ucs_groups_and_test_cases(client, seeded):
    body = client.get("/api/uctc/use-cases/UC-EDR").json()
    assert body["use_case"]["uc_id"] == "UC-EDR"
    assert body["ucs_groups"], "expected at least one UCS group"
    assert sum(g["test_case_count"] for g in body["ucs_groups"]) == len(body["test_cases"])
    for g in body["ucs_groups"]:
        assert g["evidenced_count"] <= g["test_case_count"]
    # Every TC under the UC declares that UC as its parent — the FK the loader
    # enforces as S-12, asserted here from the read side.
    assert all(tc["uc_id"] == "UC-EDR" for tc in body["test_cases"])


def test_unknown_use_case_is_a_structured_404(client, seeded):
    resp = client.get("/api/uctc/use-cases/UC-NOPE")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "UC_NOT_FOUND"


def test_use_case_evidenced_filter_partitions_the_index(client, seeded):
    total = client.get("/api/uctc/use-cases").json()["total"]
    parts = [
        client.get(f"/api/uctc/use-cases?evidenced={v}").json()["total"]
        for v in ("yes", "no", "partial")
    ]
    assert sum(parts) == total


# ---------------------------------------------------------------------------
# The main table + its filters
# ---------------------------------------------------------------------------


def test_test_case_list_is_unpaginated_and_sorted(client, seeded):
    body = client.get("/api/uctc/test-cases").json()
    assert body["total"] == 266
    assert body["index_total"] == 266
    ids = [t["tc_id"] for t in body["test_cases"]]
    assert ids == sorted(ids)


def test_validation_class_filter_accepts_a_comma_list(client, seeded):
    body = client.get("/api/uctc/test-cases?validation_class=DET,HNT").json()
    assert body["total"] == 107
    assert {t["validation_class"] for t in body["test_cases"]} == {"DET", "HNT"}


def test_filters_compose_with_and(client, seeded):
    body = client.get("/api/uctc/test-cases?validation_class=DET&tier=MOAT&priority=P1").json()
    for t in body["test_cases"]:
        assert (t["validation_class"], t["differentiation_tier"], t["priority"]) == (
            "DET", "MOAT", "P1"
        )


def test_unknown_filter_value_empties_the_list_rather_than_400(client, seeded):
    """Defensive against UI taxonomy drift, mirroring /api/ttps."""
    resp = client.get("/api/uctc/test-cases?tier=NOT-A-TIER")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


def test_scoreable_filter_splits_the_detection_surface(client, seeded):
    det = client.get("/api/uctc/test-cases?validation_class=DET,HNT").json()["total"]
    qual = client.get(
        "/api/uctc/test-cases?validation_class=DET,HNT&scoreable=false"
    ).json()
    assert qual["total"] == 57
    assert all(t["is_scoreable"] is False for t in qual["test_cases"])
    assert det - qual["total"] == 50


def test_plane_filter_implies_evidenced(client, seeded):
    body = client.get("/api/uctc/test-cases?plane=EDR").json()
    assert body["total"] > 0
    for t in body["test_cases"]:
        assert t["evidence"]["planes"] == ["EDR"]


# ---------------------------------------------------------------------------
# The reverse index
# ---------------------------------------------------------------------------


def test_evidence_resolves_a_known_scenario_binding(client, seeded):
    body = client.get("/api/uctc/test-cases/TC-EDR-03").json()
    ev = body["evidence"]
    assert ev["evidenced"] is True
    assert "SIM-EDR-001" in ev["scenario_ids"]
    assert "EDR" in ev["planes"]
    primaries = [e["scenario_id"] for e in body["evidenced_by"] if e["is_primary"]]
    assert "SIM-EDR-001" in primaries


def test_a_trailing_tc_refs_entry_still_counts_as_evidence(client, seeded):
    """TC-CITH-03 is never a primary binding — it appears only as a trailing
    ``tc_refs`` entry, so keying evidence off ``tc_ref`` alone would report it
    unevidenced."""
    body = client.get("/api/uctc/test-cases/TC-CITH-03").json()
    assert body["evidence"]["evidenced"] is True
    assert all(e["is_primary"] is False for e in body["evidenced_by"])


def test_tc_refs_fallback_covers_rows_written_before_the_column(session_factory, client):
    """A stale DB row can carry ``tc_refs=[]`` and a valid ``tc_ref``; dropping
    it would silently remove the scenario from coverage."""
    from models import Scenario  # noqa: PLC0415

    async def _seed() -> None:
        async with session_factory() as session:
            session.add(Scenario(
                scenario_id="SIM-LEGACY-001", name="legacy", version="1.0.0",
                status="active", plane="EDR", detection_types=["BIOC"],
                uc_ref="UCS-EDR-02", tc_ref="TC-EDR-03", tc_refs=[],
                uc_name="x", tc_name="y", mitre_tactic="TA0006",
                mitre_tactic_name="Credential Access", mitre_technique="T1003",
                mitre_technique_name="OS Credential Dumping",
            ))
            await session.commit()

    asyncio.run(_seed())
    body = client.get("/api/uctc/test-cases/TC-EDR-03").json()
    assert body["evidence"]["scenario_ids"] == ["SIM-LEGACY-001"]


def test_inactive_scenarios_are_excluded_by_default(session_factory, client):
    from models import Scenario  # noqa: PLC0415

    async def _seed() -> None:
        async with session_factory() as session:
            session.add(Scenario(
                scenario_id="SIM-DRAFT-001", name="draft", version="1.0.0",
                status="draft", plane="EDR", detection_types=["BIOC"],
                uc_ref="UCS-EDR-02", tc_ref="TC-EDR-03", tc_refs=["TC-EDR-03"],
                uc_name="x", tc_name="y", mitre_tactic="TA0006",
                mitre_tactic_name="Credential Access", mitre_technique="T1003",
                mitre_technique_name="OS Credential Dumping",
            ))
            await session.commit()

    asyncio.run(_seed())
    assert client.get("/api/uctc/test-cases/TC-EDR-03").json()["evidence"]["evidenced"] is False
    body = client.get("/api/uctc/test-cases/TC-EDR-03?include_inactive=true").json()
    assert body["evidence"]["scenario_ids"] == ["SIM-DRAFT-001"]


# ---------------------------------------------------------------------------
# Test-case detail — entitlement, scoreability, payload, runs
# ---------------------------------------------------------------------------


def test_detail_carries_the_measurement_contract(client, seeded):
    body = client.get("/api/uctc/test-cases/TC-EDR-03").json()
    for key in ("description", "validation_methodology", "measurement_method",
                "success_criteria", "expected_signal", "simulation_input",
                "primary_kpi", "threshold", "is_scoreable"):
        assert key in body, f"missing {key!r} in detail payload"


def test_detail_entitlements_agree_with_pov_scope(client, seeded):
    """Resolved through ``entitlements_for``, so the answer matches
    ``POST /api/pov/scope`` instead of echoing the raw TC columns (which still
    carry capacity SKUs)."""
    from engine.uctc_registry import registry  # noqa: PLC0415

    body = client.get("/api/uctc/test-cases/TC-CDR-01").json()
    base, addons = registry.entitlements_for("TC-CDR-01")
    assert body["entitlements"] == {"base_platform": base, "addons": addons}
    assert "Cortex Cloud" in body["entitlements"]["base_platform"]


def test_capacity_skus_never_appear_as_a_licence_gate(client, seeded):
    body = client.get("/api/uctc/test-cases/TC-NDR-03").json()
    assert body["entitlements"]["addons"] == []


def test_detail_flags_an_unscoreable_test_case(client, seeded):
    from engine.uctc_registry import registry  # noqa: PLC0415

    tc = registry.unscoreable()[0]
    body = client.get(f"/api/uctc/test-cases/{tc.tc_id}").json()
    assert body["is_scoreable"] is False
    assert body["needs_detection_content"] is True


def test_detail_surfaces_the_shared_payload(client, seeded):
    body = client.get("/api/uctc/test-cases/TC-EDR-03").json()
    payload = body["payload"]
    assert payload is not None
    assert payload["bound_tc_count"] >= 1
    assert payload["needs_split"] is (payload["reuse_flag"] == "SPLIT REQUIRED")


def test_detail_rolls_up_run_verdicts(client, seeded, session_factory):
    from datetime import datetime  # noqa: PLC0415

    from models import Run  # noqa: PLC0415

    async def _seed() -> None:
        async with session_factory() as session:
            session.add(Run(run_id="r-1", scenario_id="SIM-EDR-001", mode="pull",
                            status="complete", tc_verdict="pass",
                            started_at=datetime(2026, 7, 1)))
            session.add(Run(run_id="r-2", scenario_id="SIM-EDR-001", mode="pull",
                            status="complete", tc_verdict="fail",
                            started_at=datetime(2026, 7, 2)))
            # NULL verdict — an unscored run is a gap in the proof, not absent.
            session.add(Run(run_id="r-3", scenario_id="SIM-EDR-001", mode="pull",
                            status="running", started_at=datetime(2026, 6, 1)))
            await session.commit()

    asyncio.run(_seed())
    runs = client.get("/api/uctc/test-cases/TC-EDR-03").json()["runs"]
    assert runs["total"] == 3
    assert runs["verdicts"]["pass"] == 1
    assert runs["verdicts"]["fail"] == 1
    assert runs["verdicts"]["pending"] == 1
    assert runs["latest"]["run_id"] == "r-2"


def test_unknown_test_case_is_a_structured_404(client, seeded):
    resp = client.get("/api/uctc/test-cases/TC-NOPE-99")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "TC_NOT_FOUND"


# ---------------------------------------------------------------------------
# Coverage rollups
# ---------------------------------------------------------------------------


def test_coverage_totals_match_the_summary(client, seeded):
    cov = client.get("/api/uctc/coverage").json()
    summ = client.get("/api/uctc/summary").json()["evidence"]
    assert cov["totals"]["evidenced"] == summ["evidenced"] == 84
    assert cov["totals"]["evidenced_detection_backable"] == 65


def test_coverage_by_use_case_puts_the_worst_gap_first(client, seeded):
    rows = client.get("/api/uctc/coverage").json()["by_use_case"]
    assert len(rows) == 49
    assert [r["coverage_pct"] for r in rows] == sorted(r["coverage_pct"] for r in rows)


def test_coverage_dimensions_sum_to_the_index(client, seeded):
    cov = client.get("/api/uctc/coverage").json()
    for key in ("by_validation_class", "by_tier", "by_priority", "by_sheet"):
        assert sum(r["total"] for r in cov[key]) == 266, key


def test_coverage_by_plane_is_scenario_derived(client, seeded):
    """Plane is not an index attribute — it reaches a test case only through
    the evidence join, so an unevidenced TC can have no plane at all."""
    rows = client.get("/api/uctc/coverage").json()["by_plane"]
    planes = {r["plane"] for r in rows}
    assert {"EDR", "CDR", "NDR", "ITDR"} <= planes
    for r in rows:
        assert r["test_cases_evidenced"] == len(r["test_case_ids"])


# ---------------------------------------------------------------------------
# Gaps
# ---------------------------------------------------------------------------


def test_gaps_default_to_the_actionable_detection_classes(client, seeded):
    body = client.get("/api/uctc/gaps").json()
    assert body["total"] == 42
    assert all(g["evidence"]["evidenced"] is False for g in body["gaps"])
    assert {g["validation_class"] for g in body["gaps"]} <= {"DET", "HNT"}
    assert body["scope"]["validation_class"] == "DET,HNT"


def test_gaps_sort_p1_first(client, seeded):
    gaps = client.get("/api/uctc/gaps").json()["gaps"]
    priorities = [g["priority"] for g in gaps]
    assert priorities == sorted(priorities)
    assert priorities[0] == "P1"


def test_gaps_roll_up_by_use_case(client, seeded):
    body = client.get("/api/uctc/gaps").json()
    assert sum(b["gap_count"] for b in body["by_use_case"]) == body["total"]
    counts = [b["gap_count"] for b in body["by_use_case"]]
    assert counts == sorted(counts, reverse=True)


def test_gaps_can_drop_the_unscoreable_rows(client, seeded):
    every = client.get("/api/uctc/gaps").json()["total"]
    scoreable = client.get("/api/uctc/gaps?include_unscoreable=false").json()
    assert scoreable["total"] < every
    assert all(g["is_scoreable"] for g in scoreable["gaps"])


def test_gaps_and_evidenced_partition_the_detection_surface(client, seeded):
    gaps = client.get("/api/uctc/gaps").json()["total"]
    evidenced = client.get("/api/uctc/summary").json()["evidence"]
    assert gaps + evidenced["evidenced_detection_backable"] == 107


# ---------------------------------------------------------------------------
# Payloads + the forward view
# ---------------------------------------------------------------------------


def test_payloads_report_engine_usage_and_the_split_flag(client, seeded):
    body = client.get("/api/uctc/payloads").json()
    assert body["total"] == 140
    in_use = client.get("/api/uctc/payloads?in_use=true").json()
    assert in_use["total"] == 30
    assert all(p["scenario_count"] > 0 for p in in_use["payloads"])
    sc1 = next(p for p in body["payloads"] if p["pov_scenario_id"] == "POV-SC-001")
    assert sc1["needs_split"] is True
    assert sc1["bound_tc_count"] == 21


def test_by_scenario_returns_the_forward_view(client, seeded):
    body = client.get("/api/uctc/by-scenario/SIM-EDR-001").json()
    assert body["tc_ref"] == body["tc_refs"][0]
    assert [t["tc_id"] for t in body["resolved"]] == body["tc_refs"]
    # Strict-ref mode is on by default, so nothing in the corpus should dangle.
    assert body["unresolved"] == []
    assert body["entitlements"]["base_platform"]


def test_by_scenario_reports_the_tier_delta_without_calling_it_an_error(client, seeded):
    """101 S-13 disagreements are deliberate positioning calls — the surface
    reports the delta, it does not grade it."""
    body = client.get("/api/uctc/by-scenario/SIM-EDR-001").json()
    assert set(body["tier_delta"]) == {"scenario_moat_tier", "index_tiers", "disagrees"}
    assert isinstance(body["tier_delta"]["disagrees"], bool)


def test_unknown_scenario_is_a_structured_404(client, seeded):
    resp = client.get("/api/uctc/by-scenario/SIM-NOPE-999")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "SCENARIO_NOT_FOUND"


# ---------------------------------------------------------------------------
# Degradation — the shipped image has historically had no docs/
# ---------------------------------------------------------------------------


@pytest.fixture
def degraded(monkeypatch):
    """Swap in an unloaded registry, reproducing a deploy with no snapshot."""
    from api import uctc  # noqa: PLC0415
    from engine.uctc_registry import UcTcRegistry  # noqa: PLC0415

    monkeypatch.setattr(uctc, "registry", UcTcRegistry())


def test_missing_snapshot_degrades_to_an_explicit_empty_envelope(client, degraded):
    """200 with ``index_loaded: false``, never 500 — and never an unqualified
    zero, which a caller would misread as "266 rows, none evidenced"."""
    for path in ("/api/uctc/summary", "/api/uctc/use-cases",
                 "/api/uctc/test-cases", "/api/uctc/coverage",
                 "/api/uctc/gaps", "/api/uctc/payloads"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        body = resp.json()
        assert body["index_loaded"] is False, path
        assert body["index_version"] is None, path

    assert client.get("/api/uctc/summary").json()["totals"]["test_cases"] == 0
    assert client.get("/api/uctc/test-cases").json()["test_cases"] == []
    assert client.get("/api/uctc/gaps").json()["gaps"] == []


def test_degraded_registry_still_404s_a_lookup(client, degraded):
    assert client.get("/api/uctc/test-cases/TC-EDR-03").status_code == 404
    assert client.get("/api/uctc/use-cases/UC-EDR").status_code == 404


def test_degraded_by_scenario_shows_every_ref_as_unresolved(client, degraded, seeded):
    """The loader is disarmed when the registry is empty, so this is the one
    place a stripped deploy's dangling refs become visible."""
    body = client.get("/api/uctc/by-scenario/SIM-EDR-001").json()
    assert body["resolved"] == []
    assert body["unresolved"] == body["tc_refs"]
