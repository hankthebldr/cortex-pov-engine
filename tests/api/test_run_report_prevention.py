"""Report: outcome icons, chain integrity, prevention delta, attestations."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from .test_runs_api import seeded_run  # noqa: F401  (fixture reuse)


@pytest.fixture
def client(make_client):
    from api.runs import router
    return make_client(router)


def _seed_pair(session_factory):
    from models import Result, Run, Scenario

    async def go():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id="SIM-T-014", name="Prevention Chain", plane="EDR", version="1.0",
                status="active", uc_ref="UCS-EDR-01", uc_name="u", tc_ref="TC-EDR-01",
                tc_name="t", mitre_tactic="TA0006", mitre_tactic_name="c",
                mitre_technique="T1003", mitre_technique_name="d",
                steps=[{"id": "step-01", "name": "a"}, {"id": "step-02", "name": "b"},
                       {"id": "step-03", "name": "c"}]))
            now = datetime.utcnow()
            db.add(Run(run_id="rep", scenario_id="SIM-T-014", mode="pull", target="a1",
                       status="complete", agent_profile_mode="report",
                       started_at=now - timedelta(minutes=9), completed_at=now))
            db.add(Run(run_id="blk", scenario_id="SIM-T-014", mode="pull", target="a1",
                       status="complete", agent_profile_mode="block",
                       paired_with_run_id="rep", severed_at_step="step-02",
                       bas_mode_attested=True, soakless_attested=True,
                       attested_by="henry", attested_at=datetime(2026, 10, 26, 15, 2),
                       canary_run_id="canary-1", block_mode_status="block_mode_confirmed",
                       started_at=now - timedelta(minutes=8), completed_at=now))
            for step, det, sig in (("step-01", "d1", "BIOC"), ("step-02", "d2", "BIOC")):
                db.add(Result(run_id="rep", step_id=step, step_name=step, plane="EDR",
                              signal_type=sig, expected_detection=det, detection_id=det,
                              observed=True, executed_at=now, outcome="detected"))
            rows = [
                ("step-01", "d1", "BIOC", "detected", None, True, None),
                ("step-02", "d2", "BIOC", "prevented", None, True, "Behavioral Threat Protection"),
                ("step-03", "d3", "BIOC", "not_reached", None, False, None),
                ("step-01", "d4", "Analytics", "pending", "ANALYTICS_ACTIVATION_PERIOD", False, None),
            ]
            for step, det, sig, outcome, reason, obs, mod in rows:
                db.add(Result(run_id="blk", step_id=step, step_name=step, plane="EDR",
                              signal_type=sig, expected_detection=det, detection_id=det,
                              observed=obs, executed_at=now, outcome=outcome,
                              outcome_reason=reason, prevention_module=mod))
            await db.commit()
    asyncio.run(go())


def _report(client, rid):
    r = client.get(f"/api/runs/{rid}/report", params={"format": "markdown"})
    assert r.status_code == 200, r.text
    return r.text


def test_report_chain_integrity_and_icons(client, session_factory):
    _seed_pair(session_factory)
    body = _report(client, "blk")
    assert "## Chain Integrity" in body
    assert "severed at step-02 of 3" in body
    assert "not_reached: [step-03]" in body
    assert "🛡" in body and "⏭" in body
    assert "⏳ (ANALYTICS_ACTIVATION_PERIOD)" in body
    # 4 rows: not_reached and soak-pending excluded -> denominator 2
    assert ("**Overall: 2/2 detections confirmed (100.0%) — 1 prevented · "
            "2 excluded (not reached / not executed / soak-pending)**") in body
    assert "| Detection Type | Observed | Prevented | Total | Coverage |" in body


def test_report_prevention_delta_table(client, session_factory):
    _seed_pair(session_factory)
    body = _report(client, "rep")
    assert "## Prevention Delta (report → block)" in body
    assert "| Step | Detection | Type | Module | Report-mode | Block-mode |" in body
    row = next(l for l in body.splitlines() if l.startswith("| step-02"))
    assert "detected" in row and "prevented" in row
    assert "Behavioral Threat Protection" in row
    # order: coverage summary < chain integrity/delta < attestations < details
    assert body.index("## Detection Coverage Summary") < body.index("## Prevention Delta")
    assert body.index("## Prevention Delta") < body.index("## Detailed Detection Results")


def test_report_attestations_section(client, session_factory):
    _seed_pair(session_factory)
    body = _report(client, "blk")
    assert "## Attestations" in body
    assert ("agent profile mode: block — attested by henry at "
            "2026-10-26T15:02:00, not verified") in body
    assert "BAS mode: enabled — attested" in body
    assert "soakless: enabled — attested" in body
    assert "canary: block_mode_confirmed (run canary-1)" in body
    assert body.index("## Attestations") < body.index("## Detailed Detection Results")
    assert "## Evidence Classes" not in body


def test_unpaired_unattested_run_report_is_byte_identical(client, seeded_run):
    body = client.get(f"/api/runs/{seeded_run}/report", params={"format": "markdown"}).text
    for header in ("## Chain Integrity", "## Prevention Delta", "## Attestations"):
        assert header not in body
    assert ("**Overall: 2/2 detections confirmed (100.0%) — 0 prevented · "
            "0 excluded (not reached / not executed / soak-pending)**") in body
    # Strip the two stated changes; what remains must be the legacy rendering.
    legacy = body.replace(
        " — 0 prevented · 0 excluded (not reached / not executed / soak-pending)**", "**"
    ).replace("| Detection Type | Observed | Prevented | Total | Coverage |",
              "| Detection Type | Observed | Total | Coverage |"
    ).replace("| Analytics | 1 | 0 | 1 | 100.0% |", "| Analytics | 1 | 1 | 100.0% |"
    ).replace("| BIOC | 1 | 0 | 1 | 100.0% |", "| BIOC | 1 | 1 | 100.0% |"
    ).replace("|----------------|----------|-----------|-------|----------|",
              "|----------------|----------|-------|----------|")
    assert "**Overall: 2/2 detections confirmed (100.0%)**" in legacy
    assert "| Analytics | 1 | 1 | 100.0% |" in legacy and "Prevented" not in legacy
    assert "- ✅ **[Analytics]** passwd read" in legacy
