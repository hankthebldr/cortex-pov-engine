"""Direct router tests for /api/results.

Covers:
  - list (empty + populated)
  - per-run with coverage/MTTD stats
  - validate flow (sets observed_at, computes mttd_seconds)
  - notes update
  - 404 paths
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def client(make_client):
    from api.results import router
    return make_client(router)


@pytest.fixture
def seeded_run(session_factory):
    """Insert a Run + 3 Result rows directly; return (run_id, [result_ids])."""
    from models import Run, Result

    run_id = "test-run-1"

    async def _seed():
        async with session_factory() as db:
            run = Run(
                run_id=run_id,
                scenario_id="SIM-EDR-001",
                mode="push",
                status="complete",
                started_at=datetime.utcnow() - timedelta(seconds=120),
            )
            db.add(run)

            ids = []
            for i, sig_type in enumerate(("Analytics", "BIOC", "Analytics"), start=1):
                r = Result(
                    run_id=run_id,
                    step_id=f"step-0{i}",
                    step_name=f"step {i}",
                    plane="EDR",
                    signal_type=sig_type,
                    expected_detection=f"detection {i}",
                    observed=False,
                    executed_at=datetime.utcnow() - timedelta(seconds=100 - i * 10),
                )
                db.add(r)
            await db.commit()
            # Re-query to get assigned ids
            from sqlalchemy import select
            res = await db.execute(select(Result).where(Result.run_id == run_id))
            return [r.id for r in res.scalars().all()]

    ids = asyncio.get_event_loop().run_until_complete(_seed())
    return run_id, ids


def test_list_empty(client):
    r = client.get("/api/results")
    assert r.status_code == 200
    assert r.json() == {"results": [], "total": 0}


def test_get_results_for_unknown_run_404(client):
    r = client.get("/api/results/no-such-run")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "RUN_NOT_FOUND"


def test_get_results_for_run_with_coverage_stats(client, seeded_run):
    run_id, _ = seeded_run
    body = client.get(f"/api/results/{run_id}").json()
    assert body["run_id"] == run_id
    assert body["total"] == 3
    assert body["coverage"]["observed"] == 0
    assert body["coverage"]["pct"] == 0.0
    # by_type splits across Analytics(2) + BIOC(1)
    assert body["coverage"]["by_type"]["Analytics"]["total"] == 2
    assert body["coverage"]["by_type"]["BIOC"]["total"] == 1


def test_validate_marks_observed_and_computes_mttd(client, seeded_run):
    run_id, result_ids = seeded_run
    rid = result_ids[0]

    r = client.put(
        f"/api/results/{rid}/validate",
        json={"observed": True, "notes": "matched in XSIAM"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["observed"] is True
    assert body["observed_at"] is not None
    # executed_at is ~100s before now → MTTD should be positive
    assert body["mttd_seconds"] is not None
    assert body["mttd_seconds"] > 0


def test_validate_then_unvalidate_clears_observed_at(client, seeded_run):
    _, result_ids = seeded_run
    rid = result_ids[0]
    client.put(f"/api/results/{rid}/validate", json={"observed": True}).raise_for_status()

    r = client.put(f"/api/results/{rid}/validate", json={"observed": False})
    assert r.status_code == 200
    assert r.json()["observed_at"] is None


def test_validate_unknown_result_404(client):
    r = client.put("/api/results/99999/validate", json={"observed": True})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "RESULT_NOT_FOUND"


def test_update_notes(client, seeded_run):
    _, result_ids = seeded_run
    rid = result_ids[0]
    r = client.put(f"/api/results/{rid}/notes", json={"notes": "see ticket DC-123"})
    assert r.status_code == 200
    assert r.json()["notes"] == "see ticket DC-123"


def test_coverage_pct_updates_after_validation(client, seeded_run):
    run_id, result_ids = seeded_run
    # Mark 2 of 3 as observed
    for rid in result_ids[:2]:
        client.put(f"/api/results/{rid}/validate", json={"observed": True}).raise_for_status()

    body = client.get(f"/api/results/{run_id}").json()
    assert body["coverage"]["observed"] == 2
    assert body["coverage"]["pct"] == round(2 / 3 * 100, 1)
    assert body["mttd"]["count"] == 2
    assert body["mttd"]["avg_seconds"] > 0
