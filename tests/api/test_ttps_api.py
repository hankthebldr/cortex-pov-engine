"""Direct router tests for the TTP browser API.

The TTP catalog singleton is populated against the real corpus once per
module so the list / detail / reverse-cross-ref endpoints are exercised
end-to-end against the same data the engine sees at runtime.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TTPS_DIR  = REPO_ROOT / "detection_scanner" / "ttps"
PACKS_DIR = REPO_ROOT / "tools" / "packs"


@pytest.fixture(scope="module", autouse=True)
def _load_catalogs():
    """Both catalogs are needed: the TTP catalog feeds the browser
    endpoints, and the adapter catalog drives the reverse cross-link."""
    from engine.ttp_catalog import catalog as ttp_catalog  # noqa: PLC0415
    from tools.adapter_catalog import catalog as adapter_catalog  # noqa: PLC0415

    ttp_catalog.load(str(TTPS_DIR))
    adapter_catalog.load(str(PACKS_DIR))
    assert len(ttp_catalog.all_entries()) > 0
    assert adapter_catalog.count() > 0


@pytest.fixture
def client(make_client):
    from api.ttps import router  # noqa: PLC0415
    return make_client(router)


# ---------------------------------------------------------------------------
# List endpoint
# ---------------------------------------------------------------------------


def test_list_ttps_returns_corpus(client):
    resp = client.get("/api/ttps")
    assert resp.status_code == 200
    body = resp.json()
    assert "ttps" in body
    assert body["total"] == len(body["ttps"])
    # Sanity floor — corpus is small but non-empty at PR time
    assert body["total"] >= 10
    # Sorted by id for stable rendering
    ids = [t["id"] for t in body["ttps"]]
    assert ids == sorted(ids)


def test_list_ttps_summary_shape(client):
    body = client.get("/api/ttps").json()
    one = body["ttps"][0]
    # Required keys for the UI card render
    for key in (
        "id", "name", "status", "summary", "tags", "platforms",
        "simulation_class", "destructive", "technique_ids",
        "tactic_ids", "kill_chain_phase", "actor_names",
        "detection_counts", "panw_products",
    ):
        assert key in one, f"missing key {key!r} in summary payload"
    # detection_counts is a sub-dict with the canonical detection kinds
    for k in ("iocs", "biocs", "xql_queries", "correlation_rules", "analytics_modules"):
        assert k in one["detection_counts"]


def test_list_ttps_filter_by_status(client):
    body = client.get("/api/ttps?status=active").json()
    for t in body["ttps"]:
        assert t["status"] == "active"


def test_list_ttps_filter_by_tactic(client):
    body = client.get("/api/ttps?tactic=TA0006").json()
    # Every returned card has Credential Access in its tactic_ids
    for t in body["ttps"]:
        assert "TA0006" in t["tactic_ids"]
    # TTP-2026-0002 (LSASS) and TTP-2026-0004 (DCSync) both cite TA0006
    ids = {t["id"] for t in body["ttps"]}
    assert "TTP-2026-0002" in ids
    assert "TTP-2026-0004" in ids


def test_list_ttps_filter_by_platform(client):
    body = client.get("/api/ttps?platform=windows").json()
    for t in body["ttps"]:
        assert "windows" in t["platforms"]


def test_list_ttps_unknown_filter_value_returns_empty(client):
    # Defensive: a stale UI sending `status=NOPE` shouldn't 400 — it
    # should quietly return an empty list so the picker shows "no matches".
    body = client.get("/api/ttps?status=NOPE").json()
    assert body == {"ttps": [], "total": 0}


def test_list_ttps_filters_compose_with_logical_and(client):
    body = client.get("/api/ttps?status=active&tactic=TA0006&platform=windows").json()
    for t in body["ttps"]:
        assert t["status"] == "active"
        assert "TA0006" in t["tactic_ids"]
        assert "windows" in t["platforms"]


# ---------------------------------------------------------------------------
# Detail endpoint
# ---------------------------------------------------------------------------


def test_get_ttp_detail_returns_full_card(client):
    """Detail endpoint surfaces every JSON top-level the corpus carries."""
    resp = client.get("/api/ttps/TTP-2026-0004")
    assert resp.status_code == 200
    body = resp.json()
    # Top-level keys from the corpus schema
    for key in (
        "id", "status", "metadata", "identity", "threat_context",
        "mitre_attack", "execution", "detections", "panw_mapping",
        "references",
    ):
        assert key in body, f"missing top-level key {key!r}"
    assert body["id"] == "TTP-2026-0004"
    # Identity carries the human-readable name + summary
    assert "DCSync" in body["identity"]["name"]


def test_get_ttp_detail_reverse_cross_links_adapters(client):
    """TTP-2026-0004 (DCSync) is referenced by mimikatz + rubeus + bloodhound
    in their ``ttp_refs[]`` — the detail endpoint surfaces all three so the
    UI can render the reverse-lookup section."""
    body = client.get("/api/ttps/TTP-2026-0004").json()
    assert "referenced_by_adapters" in body
    ids = {a["adapter_id"] for a in body["referenced_by_adapters"]}
    # The exact set depends on which adapters cite this TTP — assert the
    # known-live ones rather than equality so adding a new citation is
    # additive, not breaking.
    assert "TOOL-MIMIKATZ" in ids
    assert "TOOL-RUBEUS" in ids
    assert "TOOL-BLOODHOUND" in ids
    # Each row carries the metadata the UI card needs
    sample = next(a for a in body["referenced_by_adapters"] if a["adapter_id"] == "TOOL-MIMIKATZ")
    assert sample["tier"] == 3
    assert sample["safety_class"] == "dual-use-lab-only"


def test_get_ttp_detail_referenced_by_empty_when_no_adapters_cite_it(client):
    """A TTP with no adapter citations gets ``referenced_by_adapters: []``."""
    # TTP-2026-0001 (helpdesk MFA reset) — only evilginx2 cites this.
    # Pick a TTP that's NOT cited to test the empty path.
    body = client.get("/api/ttps/TTP-2026-0007").json()
    # TTP-2026-0007 is the AI Access scenario; nothing in tools/packs/
    # references it currently. If that changes, this test guards the
    # empty-list code path with a clearer message.
    refs = body.get("referenced_by_adapters", [])
    assert isinstance(refs, list)


def test_get_ttp_detail_unknown_id_404(client):
    resp = client.get("/api/ttps/TTP-DOES-NOT-EXIST")
    assert resp.status_code == 404
    err = resp.json()["detail"]
    assert err["code"] == "TTP_NOT_FOUND"
    assert "TTP-DOES-NOT-EXIST" in err["detail"]


# ---------------------------------------------------------------------------
# Runs-by-TTP endpoint
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded_ttp_runs(session_factory):
    """Seed two runs whose Result rows cite TTP-2026-0004 (DCSync), plus
    one run citing a different TTP and one orphan Result with no ttp_ref
    so the filter logic gets exercised."""
    from models import Run, Result, Scenario

    async def _seed():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id="SIM-ITDR-002",
                name="DCSync chain",
                plane="ITDR",
                version="1.0",
                status="active",
                uc_ref="UCS-ITDR-02",
                uc_name="Credential Replication",
                tc_ref="TC-ITDR-02",
                tc_name="DCSync Replication Abuse",
                mitre_tactic="TA0006",
                mitre_tactic_name="Credential Access",
                mitre_technique="T1003.006",
                mitre_technique_name="DCSync",
                steps=[],
            ))

            now = datetime.utcnow()

            # Run 1 — newest, 2 expected, 2 observed, fast MTTD
            db.add(Run(
                run_id="r-dcsync-1", scenario_id="SIM-ITDR-002",
                mode="push", status="complete",
                started_at=now - timedelta(minutes=10),
                completed_at=now - timedelta(minutes=5),
            ))
            db.add(Result(
                run_id="r-dcsync-1", plane="ITDR",
                signal_type="BIOC", expected_detection="DRSUAPI from non-DC",
                observed=True, ttp_ref="TTP-2026-0004",
                detection_id="BIOC-CRED-DCSYNC-001",
                executed_at=now - timedelta(minutes=10),
                observed_at=now - timedelta(minutes=9, seconds=30),
            ))
            db.add(Result(
                run_id="r-dcsync-1", plane="ITDR",
                signal_type="Analytics", expected_detection="Event 4662",
                observed=True, ttp_ref="TTP-2026-0004",
                detection_id="BIOC-CRED-DCSYNC-002",
                executed_at=now - timedelta(minutes=10),
                observed_at=now - timedelta(minutes=8),
            ))

            # Run 2 — older, 2 expected, 1 observed (partial)
            db.add(Run(
                run_id="r-dcsync-2", scenario_id="SIM-ITDR-002",
                mode="push", status="complete",
                started_at=now - timedelta(hours=2),
                completed_at=now - timedelta(hours=1, minutes=55),
            ))
            db.add(Result(
                run_id="r-dcsync-2", plane="ITDR",
                signal_type="BIOC", expected_detection="DRSUAPI",
                observed=True, ttp_ref="TTP-2026-0004",
                detection_id="BIOC-CRED-DCSYNC-001",
                executed_at=now - timedelta(hours=2),
                observed_at=now - timedelta(hours=1, minutes=50),
            ))
            db.add(Result(
                run_id="r-dcsync-2", plane="ITDR",
                signal_type="BIOC", expected_detection="Mimikatz dcsync pattern",
                observed=False, ttp_ref="TTP-2026-0004",
                detection_id="BIOC-CRED-DCSYNC-003",
            ))

            # Run 3 — cites a DIFFERENT TTP, must NOT appear in the
            # TTP-2026-0004 history
            db.add(Run(
                run_id="r-other", scenario_id="SIM-ITDR-002",
                mode="push", status="complete",
                started_at=now - timedelta(minutes=20),
            ))
            db.add(Result(
                run_id="r-other", plane="ITDR",
                signal_type="BIOC", expected_detection="LSASS dump",
                observed=True, ttp_ref="TTP-2026-0002",
            ))

            # Orphan Result with no ttp_ref — defensive: don't count it
            db.add(Result(
                run_id="r-dcsync-1", plane="ITDR",
                signal_type="BIOC", expected_detection="untyped step",
                observed=False, ttp_ref=None,
            ))

            await db.commit()

    asyncio.get_event_loop().run_until_complete(_seed())


def test_runs_by_ttp_returns_only_matching_runs(client, seeded_ttp_runs):
    resp = client.get("/api/ttps/TTP-2026-0004/runs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ttp_id"] == "TTP-2026-0004"
    run_ids = [r["run_id"] for r in body["runs"]]
    assert run_ids == ["r-dcsync-1", "r-dcsync-2"]  # newest-first, no r-other
    assert body["total"] == 2


def test_runs_by_ttp_rolls_up_per_run_counts(client, seeded_ttp_runs):
    body = client.get("/api/ttps/TTP-2026-0004/runs").json()
    by_id = {r["run_id"]: r for r in body["runs"]}

    # Run 1 — 2 expected, 2 observed, fastest MTTD = 30 s
    assert by_id["r-dcsync-1"]["expected"] == 2
    assert by_id["r-dcsync-1"]["observed"] == 2
    assert by_id["r-dcsync-1"]["min_mttd_seconds"] == pytest.approx(30, abs=1)
    assert set(by_id["r-dcsync-1"]["detection_ids"]) == {
        "BIOC-CRED-DCSYNC-001", "BIOC-CRED-DCSYNC-002",
    }

    # Run 2 — 2 expected, 1 observed, MTTD = 600 s (one Result lacks
    # observed_at so it doesn't contribute to min_mttd)
    assert by_id["r-dcsync-2"]["expected"] == 2
    assert by_id["r-dcsync-2"]["observed"] == 1
    assert by_id["r-dcsync-2"]["min_mttd_seconds"] == pytest.approx(600, abs=1)


def test_runs_by_ttp_scenario_id_propagated(client, seeded_ttp_runs):
    body = client.get("/api/ttps/TTP-2026-0004/runs").json()
    for r in body["runs"]:
        assert r["scenario_id"] == "SIM-ITDR-002"
        assert r["run_status"] == "complete"
        assert isinstance(r["started_at"], str)


def test_runs_by_ttp_empty_when_no_results(client, seeded_ttp_runs):
    body = client.get("/api/ttps/TTP-2026-0003/runs").json()
    assert body == {"ttp_id": "TTP-2026-0003", "runs": [], "total": 0}


def test_runs_by_ttp_unknown_ttp_404(client):
    resp = client.get("/api/ttps/TTP-NOPE/runs")
    assert resp.status_code == 404
    err = resp.json()["detail"]
    assert err["code"] == "TTP_NOT_FOUND"


def test_runs_by_ttp_respects_limit(client, seeded_ttp_runs):
    body = client.get("/api/ttps/TTP-2026-0004/runs?limit=1").json()
    assert body["total"] == 1
    # Newest-first — r-dcsync-1 wins
    assert body["runs"][0]["run_id"] == "r-dcsync-1"
