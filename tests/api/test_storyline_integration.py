"""Non-stubbed integration test for GET /api/runs/{run_id}/storyline (TC-01).

The sibling ``test_storyline_api.py`` exercises the HTTP router in isolation by
injecting a **fake** ``engine.detection_storyline`` into ``sys.modules`` — which
means a shape drift in ``Result.to_dict()`` / ``Run.to_dict()`` / ``Scenario.steps``
ships green even though the live endpoint would return a wrong/empty storyline.
That is the false-green called out as gap TC-01 in
``docs/reference/detection-engine-gap-analysis.md``.

This module deliberately does **not** stub the builder. It seeds a real
Run + Scenario + Results (mirroring the ``seeded_run`` fixture), hits the
endpoint, and asserts the ``entries[]`` and ``summary.coverage_pct`` in the
response were produced by the *real* ``engine.detection_storyline.build_storyline``.
So a rename/shape drift in the frozen dicts the endpoint hands the builder now
fails a test instead of passing silently.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def client(make_client):
    from api.storyline import router
    return make_client(router)


@pytest.fixture
def seeded_run(session_factory):
    """Insert one complete Run + Scenario + 2 Results. Returns run_id.

    Mirrors the ``seeded_run`` fixture in ``test_storyline_api.py`` exactly so
    the two files describe the identical seeded denominator — the difference is
    solely that this one does NOT stub the builder.

    Shape of the seeded run:
      * step-01 — ABIOC "passwd read", observed=True, executed 3m ago,
        observed 1m ago  → DETECTED, MTTD = 120s.
      * step-02 — BIOC "shadow read", observed=False, never reviewed → run is
        terminal ("complete"), so this is a genuine MISSED gap, not pending.
      * coverage = 1 detected / 2 expected = 50.0 %.
    """
    from models import Run, Result, Scenario

    async def _seed():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id="SIM-EDR-001",
                name="Credential Dumping",
                plane="EDR",
                version="1.0",
                status="active",
                uc_ref="UCS-EDR-01",
                uc_name="Endpoint Credential Theft",
                tc_ref="TC-EDR-01",
                tc_name="Linux Credential Harvest",
                mitre_tactic="TA0006",
                mitre_tactic_name="Credential Access",
                mitre_technique="T1003.008",
                mitre_technique_name="OS Credential Dumping",
                steps=[
                    {"id": "step-01", "name": "Read passwd", "mitre_technique": "T1087.001"},
                    {"id": "step-02", "name": "Read shadow", "mitre_technique": "T1003.008"},
                ],
            ))
            db.add(Run(
                run_id="r-1",
                scenario_id="SIM-EDR-001",
                mode="pull",
                status="complete",
                started_at=datetime.utcnow() - timedelta(minutes=5),
                completed_at=datetime.utcnow() - timedelta(minutes=2),
            ))
            for i, (sig, det) in enumerate(
                [("ABIOC", "passwd read"), ("BIOC", "shadow read")], start=1
            ):
                db.add(Result(
                    run_id="r-1",
                    step_id=f"step-0{i}",
                    step_name=f"step {i}",
                    plane="EDR",
                    signal_type=sig,
                    expected_detection=det,
                    observed=(i == 1),
                    observed_at=datetime.utcnow() - timedelta(minutes=1) if i == 1 else None,
                    executed_at=datetime.utcnow() - timedelta(minutes=3),
                ))
            await db.commit()

    asyncio.run(_seed())
    return "r-1"


def test_storyline_real_builder_end_to_end(client, seeded_run):
    """The endpoint returns the REAL builder's output — no stub in sight.

    Every assertion below is on a value only ``build_storyline`` can produce
    from the seeded ``Result.to_dict()`` / ``Run.to_dict()`` / ``Scenario.steps``
    projections, so a shape drift in any of those breaks this test.
    """
    r = client.get(f"/api/runs/{seeded_run}/storyline")
    assert r.status_code == 200

    story = r.json()["detection_storyline"]

    # --- run-level identity carried through from Run.to_dict() ---------------
    assert story["run_id"] == "r-1"
    assert story["scenario_id"] == "SIM-EDR-001"
    assert story["run_status"] == "complete"

    # --- entries[]: the real flat kill-chain, in scenario step order ---------
    entries = story["entries"]
    assert [e["step_id"] for e in entries] == ["step-01", "step-02"]
    assert [e["step_index"] for e in entries] == [0, 1]

    detected, missed = entries

    # step-01 fired: DETECTED with a real observed sub-object + MTTD = 120s.
    assert detected["status"] == "detected"
    assert detected["expected_detection"]["type"] == "ABIOC"
    assert detected["expected_detection"]["description"] == "passwd read"
    assert detected["expected_detection"]["plane"] == "EDR"
    assert detected["observed"] is not None
    assert detected["observed"]["observed_at"] is not None
    assert detected["mttd_seconds"] == pytest.approx(120.0)

    # step-02 never fired and the run is terminal → genuine MISSED gap.
    assert missed["status"] == "missed"
    assert missed["expected_detection"]["type"] == "BIOC"
    assert missed["observed"] is None
    assert missed["mttd_seconds"] is None

    # --- summary.coverage_pct: computed by build_summary, not the router -----
    summary = story["summary"]
    assert summary["total"] == 2
    assert summary["detected"] == 1
    assert summary["missed"] == 1
    assert summary["pending"] == 0
    assert summary["coverage_pct"] == 50.0
    # MTTD distribution is taken over the single detected entry only.
    assert summary["mttd"]["count"] == 1
    assert summary["mttd"]["median"] == pytest.approx(120.0)


def test_storyline_grouped_steps_match_entries(client, seeded_run):
    """The per-step grouping face is the same real entries, regrouped."""
    r = client.get(f"/api/runs/{seeded_run}/storyline")
    assert r.status_code == 200
    story = r.json()["detection_storyline"]

    steps = story["steps"]
    assert [s["step_id"] for s in steps] == ["step-01", "step-02"]
    # Every entry appears under exactly one step group; counts line up.
    grouped = [d for s in steps for d in s["detections"]]
    assert len(grouped) == len(story["entries"]) == 2
    assert steps[0]["detections"][0]["status"] == "detected"
    assert steps[1]["detections"][0]["status"] == "missed"
