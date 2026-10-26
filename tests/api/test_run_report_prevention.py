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
            "2026-10-26T15:02:00Z, not verified") in body
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


# --- fix round 1 -----------------------------------------------------------

def _seed_simple(session_factory, run_kw=None, rows=()):
    """One run 'x' (+ scenario) with Result rows: (step, det, sig, outcome, observed, mttd)."""
    from models import Result, Run, Scenario

    async def go():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id="SIM-T-015", name="n", plane="EDR", version="1.0", status="active",
                uc_ref="UCS-EDR-01", uc_name="u", tc_ref="TC-EDR-01", tc_name="t",
                mitre_tactic="TA0006", mitre_tactic_name="c", mitre_technique="T1003",
                mitre_technique_name="d", steps=[{"id": "step-01"}, {"id": "step-02"}]))
            now = datetime.utcnow()
            db.add(Run(run_id="x", scenario_id="SIM-T-015", mode="pull", status="complete",
                       started_at=now, completed_at=now, **(run_kw or {})))
            for step, det, sig, outcome, obs, mttd in rows:
                db.add(Result(run_id="x", step_id=step, step_name=step, plane="EDR",
                              signal_type=sig, expected_detection=det, detection_id=det,
                              observed=obs, executed_at=now, outcome=outcome,
                              observed_at=None if mttd is None else now + timedelta(seconds=mttd)))
            await db.commit()
    asyncio.run(go())


def test_attestation_unset_flags_have_no_attested_suffix(client, session_factory):
    _seed_simple(session_factory, {"agent_profile_mode": "block"})
    body = _report(client, "x")
    lines = body.splitlines()
    assert "- BAS mode: not attested" in lines
    assert "- soakless: not attested" in lines


def test_attested_at_renders_with_z(client, session_factory):
    _seed_simple(session_factory, {"agent_profile_mode": "block", "attested_by": "henry",
                                   "attested_at": datetime(2026, 10, 26, 15, 2)})
    assert "attested by henry at 2026-10-26T15:02:00Z, not verified" in _report(client, "x")


@pytest.mark.parametrize("kw,expected", [
    ({"block_mode_status": "block_mode_confirmed", "canary_run_id": "c1"},
     "- canary: block_mode_confirmed (run c1)"),
    ({"block_mode_status": "canary_not_available"}, "- canary: canary_not_available"),
    ({"canary_run_id": "c1"}, "- canary: run c1 (status unknown)"),
    ({}, "- canary: not run"),
])
def test_canary_line_shapes(client, session_factory, kw, expected):
    _seed_simple(session_factory, {"agent_profile_mode": "block", **kw})
    body = _report(client, "x")
    assert expected in body.splitlines()
    assert "None" not in body


def test_mttd_section_excludes_excluded_rows(client, session_factory):
    _seed_simple(session_factory, None, [
        ("step-01", "d1", "BIOC", "detected", True, 10.0),
        ("step-02", "d2", "BIOC", "not_reached", False, 999.0),
    ])
    body = _report(client, "x")
    assert "| Detections Measured | 1 |" in body
    assert "999" not in body


def test_headline_observed_counts_in_denominator_only(client, session_factory):
    _seed_simple(session_factory, None, [
        ("step-01", "d1", "BIOC", "detected", True, None),
        ("step-02", "d2", "BIOC", "not_reached", True, None),
    ])
    body = _report(client, "x")
    assert "**Overall: 1/1 detections confirmed (100.0%)" in body
    assert "| BIOC | 1 | 0 | 1 | 100.0% |" in body


def test_json_coverage_semantics(client, session_factory):
    _seed_simple(session_factory, None, [
        ("step-01", "d1", "BIOC", "prevented", True, None),
        ("step-01", "d2", "BIOC", "missed", False, None),
        ("step-02", "d3", "BIOC", "not_reached", True, None),
    ])
    cov = client.get("/api/runs/x/report", params={"format": "json"}).json()["coverage"]
    assert cov["total"] == 3 and cov["denominator"] == 2
    assert cov["observed"] == 2 and cov["observed_in_denominator"] == 1
    assert cov["pct"] == round(cov["observed_in_denominator"] / cov["denominator"] * 100, 1) == 50.0
    assert cov["prevented"] == 1 and cov["excluded"] == 1


def test_unrecognised_outcome_raises():
    from api.runs import _result_icon
    from engine.report_generator import _status_from_result

    class R:
        outcome, outcome_reason, observed = "bogus", None, True
    with pytest.raises(ValueError, match="bogus"):
        _result_icon(R())
    with pytest.raises(ValueError, match="bogus"):
        _status_from_result(True, True, outcome="bogus")


def test_prevention_delta_exact_row_and_detected_both_modes(client, session_factory):
    _seed_pair(session_factory)
    from models import Result
    from datetime import datetime as dt

    async def go():
        async with session_factory() as db:
            for rid in ("rep", "blk"):
                db.add(Result(run_id=rid, step_id="step-01", step_name="s", plane="EDR",
                              signal_type="Prevention", expected_detection="d9",
                              detection_id="d9", observed=True, executed_at=dt.utcnow(),
                              outcome="detected"))
            await db.commit()
    asyncio.run(go())
    body = _report(client, "rep")
    assert "| step-02 | d2 | BIOC | Behavioral Threat Protection | detected | prevented |" in body
    assert "| step-01 | d9 | Prevention | — | detected | detected |" in body
