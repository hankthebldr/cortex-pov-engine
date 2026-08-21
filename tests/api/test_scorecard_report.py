"""Router tests for the efficacy-scorecard report branch of
``GET /api/runs/{run_id}/report`` (TC-02).

The scorecard branch (``?format=scorecard`` / ``?format=scorecard-html`` in
``core/api/runs.py``) had zero HTTP-level coverage: the content-type split
(text/plain vs text/html), the scenario-name title interpolation, and the
coverage denominator were all untested — a passing pure builder gave false
safety.

These tests exercise the branch end-to-end against a **real**
``engine.efficacy_scorecard.build_efficacy_scorecard`` (never stubbed — per the
"tests must not stub their own subject" invariant), over a run seeded with a
deterministic detected / missed / pending mix so the rendered coverage numbers
are load-bearing assertions.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def client(make_client):
    from api.runs import router
    return make_client(router)


@pytest.fixture
def seeded_run(session_factory):
    """Seed one complete Run + Scenario + 3 Results with a known status mix.

    Status derivation (``efficacy_scorecard._status``):
      * observed=True                       -> detected
      * observed=False + observed_at set    -> missed
      * observed=False + observed_at None   -> pending

    So the seeded denominator is **1 detected · 1 missed · 1 pending = 3
    expected**, i.e. 33.3 % coverage. Returns the run_id.
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
                    {"id": "step-01", "name": "Read passwd"},
                    {"id": "step-02", "name": "Read shadow"},
                    {"id": "step-03", "name": "Dump creds"},
                ],
            ))
            db.add(Run(
                run_id="r-sc",
                scenario_id="SIM-EDR-001",
                mode="pull",
                status="complete",
                started_at=datetime.utcnow() - timedelta(minutes=5),
                completed_at=datetime.utcnow() - timedelta(minutes=2),
            ))
            base = datetime.utcnow() - timedelta(minutes=3)
            # ``mttd_seconds`` is a computed property (observed_at - executed_at),
            # not a settable column — the detected row derives ~90s on its own.
            # (signal_type, expected, observed, observed_at)
            rows = [
                ("ABIOC", "passwd read", True, base + timedelta(seconds=90)),
                ("BIOC", "shadow read", False, base + timedelta(seconds=120)),
                ("XQL", "creds dumped", False, None),
            ]
            for i, (sig, det, obs, obs_at) in enumerate(rows, start=1):
                db.add(Result(
                    run_id="r-sc",
                    step_id=f"step-0{i}",
                    step_name=f"step {i}",
                    plane="EDR",
                    signal_type=sig,
                    expected_detection=det,
                    observed=obs,
                    observed_at=obs_at,
                    executed_at=base,
                ))
            await db.commit()

    asyncio.run(_seed())
    return "r-sc"


# ---------------------------------------------------------------------------
# scorecard (markdown / text/plain)
# ---------------------------------------------------------------------------


def test_scorecard_returns_plain_text(client, seeded_run):
    r = client.get(f"/api/runs/{seeded_run}/report", params={"format": "scorecard"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")


def test_scorecard_coverage_matches_seeded_results(client, seeded_run):
    """1 detected · 1 missed · 1 pending / 3 expected → 33.3 % coverage."""
    r = client.get(f"/api/runs/{seeded_run}/report", params={"format": "scorecard"})
    assert r.status_code == 200
    body = r.text
    # Coverage headline table (efficacy_scorecard.render_markdown)
    assert "| Coverage | **33.3%** |" in body
    assert "| Detected | 1 |" in body
    assert "| Missed | 1 |" in body
    assert "| Pending | 1 |" in body
    assert "| Expected (denominator) | 3 |" in body
    # Executive-summary sentence restates the same denominator.
    assert "1 of 3" in body


def test_scorecard_title_interpolates_scenario_name(client, seeded_run):
    r = client.get(f"/api/runs/{seeded_run}/report", params={"format": "scorecard"})
    assert r.status_code == 200
    assert "Cortex POV Efficacy Scorecard — Credential Dumping" in r.text


# ---------------------------------------------------------------------------
# scorecard-html (text/html)
# ---------------------------------------------------------------------------


def test_scorecard_html_returns_html(client, seeded_run):
    r = client.get(f"/api/runs/{seeded_run}/report", params={"format": "scorecard-html"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")


def test_scorecard_html_coverage_matches_seeded_results(client, seeded_run):
    r = client.get(f"/api/runs/{seeded_run}/report", params={"format": "scorecard-html"})
    assert r.status_code == 200
    body = r.text
    # The Cortex-branded HTML doc carries the same denominator + coverage %.
    assert "<strong>1 of 3</strong>" in body
    assert "33.3% coverage" in body
    # KPI tiles surface the raw detected / missed / pending counts.
    assert 'class="cs-kpi-lbl">Detected<' in body
    assert 'class="cs-kpi-lbl">Missed<' in body
    assert 'class="cs-kpi-lbl">Pending<' in body


def test_scorecard_html_and_markdown_agree_on_coverage(client, seeded_run):
    """Both renderers project the same seeded denominator — neither format
    recomputes coverage independently."""
    md = client.get(f"/api/runs/{seeded_run}/report",
                    params={"format": "scorecard"}).text
    h = client.get(f"/api/runs/{seeded_run}/report",
                   params={"format": "scorecard-html"}).text
    assert "33.3%" in md
    assert "33.3%" in h
    assert "1 of 3" in md
    assert "1 of 3" in h


# ---------------------------------------------------------------------------
# error paths
# ---------------------------------------------------------------------------


def test_scorecard_unknown_run_404(client):
    r = client.get("/api/runs/no-such-run/report", params={"format": "scorecard"})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "RUN_NOT_FOUND"
