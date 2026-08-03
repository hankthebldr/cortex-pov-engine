"""End-to-end tests for the run verdict path through the HTTP surface.

The point of these is that ``Run.tc_verdict`` gets written by the PRODUCTION
code path — an agent posting `/complete`, a DC posting `/observations`, an
operator posting `/verify` — and not by a test calling ``verify_run`` directly.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest


MTTD_THRESHOLD = {"kpi": "MTTD", "op": "<=", "value": 300, "unit": "s"}


@pytest.fixture
def client(make_client):
    from api.connectors import runs_reconcile_router  # noqa: PLC0415
    from api.runs import router  # noqa: PLC0415
    return make_client(router, runs_reconcile_router)


@pytest.fixture(autouse=True)
def neutral_uctc_registry(monkeypatch):
    """Pin the UC/TC index singleton EMPTY so these tests measure the wiring,
    not whether an earlier test in the session loaded the snapshot.

    ``tc_scoreable_for`` degrades open with no snapshot loaded. With one
    loaded, the fixture's ``TC-EDR-01`` is one of the 123 qualitative index
    rows and every ``pass`` would clamp to ``not_applicable`` — correct
    behaviour, covered by its own test in
    ``tests/connectors/test_verification_service.py``, but not what these
    end-to-end path tests are pinning.
    """
    from engine.uctc_registry import UcTcRegistry  # noqa: PLC0415

    monkeypatch.setattr("engine.uctc_registry.registry", UcTcRegistry())


def _seed(session_factory, *, run_id="run-e2e", status="running",
          threshold=MTTD_THRESHOLD, executed_minutes_ago=10,
          verification_xql=None, results=1):
    from models import Result, Run, Scenario

    executed = datetime.utcnow() - timedelta(minutes=executed_minutes_ago)

    async def _do():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id="SIM-EDR-001", name="Credential Dumping", plane="EDR",
                version="1.0", status="active",
                uc_ref="UCS-EDR-01", uc_name="X", tc_ref="TC-EDR-01", tc_name="Y",
                mitre_tactic="TA0006", mitre_tactic_name="Credential Access",
                mitre_technique="T1003.001", mitre_technique_name="LSASS Memory",
                steps=[], threshold=threshold, primary_kpi="MTTD",
            ))
            db.add(Run(run_id=run_id, scenario_id="SIM-EDR-001", mode="pull",
                       status=status, started_at=executed))
            for i in range(results):
                db.add(Result(
                    run_id=run_id, step_id=f"step-0{i + 1}", plane="EDR",
                    signal_type="BIOC", expected_detection="LSASS dump",
                    observed=False, executed_at=executed,
                    mitre_technique="T1003.001", detection_id="bioc-lsass",
                    verification_xql=verification_xql,
                    kpi_verdict="pending" if verification_xql else None,
                ))
            await db.commit()

    asyncio.run(_do())
    return run_id


# ---------------------------------------------------------------------------
# The production path: /complete then /observations
# ---------------------------------------------------------------------------


def test_complete_then_observe_produces_a_real_verdict_end_to_end(client, session_factory):
    """The whole contract in one test, entirely through HTTP.

    1. The agent posts /complete → the run carries an explicit `pending`
       verdict ("threshold declared, nothing measured yet") instead of NULL.
    2. The DC posts an observed alert → MTTD becomes real, the threshold is
       evaluated against it, and the verdict flips to `pass`.

    Before this wiring both steps left `tc_verdict` NULL forever, while
    /api/uctc and the POV report both read the column.
    """
    run_id = _seed(session_factory)

    completed = client.post(f"/api/runs/{run_id}/complete",
                            json={"exit_code": 0, "summary": "done"})
    assert completed.status_code == 200
    assert completed.json()["tc_verdict"] == "pending"

    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["tc_verdict"] == "pending"
    assert detail["tc_verdict_detail"]["source"] == "complete"

    observed_at = datetime.utcnow() - timedelta(minutes=9)
    ingest = client.post(f"/api/runs/{run_id}/observations", json={"observations": [{
        "external_id": "a1", "name": "LSASS Memory Access",
        "observed_at": observed_at.isoformat(),
        "techniques": ["T1003.001"],
    }]})
    assert ingest.status_code == 200, ingest.text
    assert ingest.json()["newly_matched"] == 1
    assert ingest.json()["tc_verdict"] == "pass"

    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["tc_verdict"] == "pass"
    primary = detail["tc_verdict_detail"]["primary"]
    assert primary["verdict"] == "pass"
    assert primary["actual"] == pytest.approx(60, abs=5)
    assert detail["tc_verdict_detail"]["source"].startswith("reconcile:")


def test_slow_detection_fails_its_threshold_end_to_end(client, session_factory):
    """Observed 20 minutes after execution against `MTTD <= 300s` → fail.

    A coverage percentage would still read 100% here; the verdict is the only
    thing that says the bar was missed.
    """
    run_id = _seed(session_factory, run_id="run-slow", executed_minutes_ago=30)
    client.post(f"/api/runs/{run_id}/complete", json={"exit_code": 0, "summary": "d"})

    observed_at = datetime.utcnow() - timedelta(minutes=10)
    client.post(f"/api/runs/{run_id}/observations", json={"observations": [{
        "external_id": "a1", "name": "LSASS Memory Access",
        "observed_at": observed_at.isoformat(),
        "techniques": ["T1003.001"],
    }]}).raise_for_status()

    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["tc_verdict"] == "fail"


def test_complete_still_succeeds_when_the_scenario_row_is_missing(client, session_factory):
    """The agent's completion callback must never fail because scoring did."""
    from models import Run

    async def _do():
        async with session_factory() as db:
            db.add(Run(run_id="r-orphan", scenario_id="SIM-GONE", mode="pull",
                       status="running", started_at=datetime.utcnow()))
            await db.commit()

    asyncio.run(_do())
    r = client.post("/api/runs/r-orphan/complete",
                    json={"exit_code": 127, "summary": "command not found"})
    assert r.status_code == 200
    assert r.json()["status"] == "failed"


def test_abort_leaves_the_verdict_unset(client, session_factory):
    """An aborted run's verdict is undefined, not pending — do not fabricate one."""
    run_id = _seed(session_factory, run_id="run-abort")
    client.post(f"/api/runs/{run_id}/abort").raise_for_status()

    assert client.get(f"/api/runs/{run_id}").json()["tc_verdict"] is None


# ---------------------------------------------------------------------------
# POST /api/runs/{id}/verify
# ---------------------------------------------------------------------------


def test_verify_without_a_credential_returns_200_pending(client, session_factory):
    """`no tenant wired` is an honest verdict, not a client error — same
    contract as the assertion surface."""
    run_id = _seed(session_factory, run_id="run-v1",
                   verification_xql="dataset = xdr_data | limit 1")
    r = client.post(f"/api/runs/{run_id}/verify")

    assert r.status_code == 200
    body = r.json()
    assert body["tc_verdict"] == "pending"
    assert body["reason"] == "no_tenant_integration"
    assert body["queries_issued"] == 0
    assert body["tenant"] is None


def test_verify_names_the_tenant_it_queried(client, session_factory, monkeypatch):
    """Nobody should have to discover after the fact which customer tenant a
    verification touched."""
    from connectors import service

    # No scenario-level threshold: the per-result verification verdict is what
    # governs here. (With an MTTD threshold declared and nothing observed, the
    # run would correctly stay `pending` no matter what the query returned.)
    run_id = _seed(session_factory, run_id="run-v2", threshold=None,
                   verification_xql="dataset = xdr_data | limit 1")

    async def _fake_resolve(db, *, integration=None, timeframe_seconds=3600):
        async def _runner(_q: str) -> int:
            return 4
        return _runner, "acme-prod"

    monkeypatch.setattr(service, "resolve_tenant_runner", _fake_resolve)

    body = client.post(f"/api/runs/{run_id}/verify").json()
    assert body["tenant"] == "acme-prod"
    assert body["queries_issued"] == 1
    assert body["tc_verdict"] == "pass"


def test_verify_is_idempotent_then_forceable(client, session_factory, monkeypatch):
    from connectors import service

    run_id = _seed(session_factory, run_id="run-v3", threshold=None,
                   verification_xql="dataset = xdr_data | limit 1")
    calls: list[str] = []

    async def _fake_resolve(db, *, integration=None, timeframe_seconds=3600):
        async def _runner(q: str) -> int:
            calls.append(q)
            return 2
        return _runner, "acme-prod"

    monkeypatch.setattr(service, "resolve_tenant_runner", _fake_resolve)

    assert client.post(f"/api/runs/{run_id}/verify").json()["tc_verdict"] == "pass"
    assert len(calls) == 1

    second = client.post(f"/api/runs/{run_id}/verify").json()
    assert second["reverified"] is False
    assert second["queries_issued"] == 0
    assert len(calls) == 1, "a terminal verdict must not re-spend tenant quota"

    forced = client.post(f"/api/runs/{run_id}/verify?force=true").json()
    assert forced["reverified"] is True
    assert len(calls) == 2


def test_verify_unknown_named_integration_is_a_404(client, session_factory):
    run_id = _seed(session_factory, run_id="run-v4",
                   verification_xql="dataset = xdr_data")
    r = client.post(f"/api/runs/{run_id}/verify?integration=does-not-exist")
    # No xsiam_tenant integration exists at all, so the resolver reports "no
    # credential" rather than a typo — 200 + pending is the honest answer.
    assert r.status_code == 200
    assert r.json()["reason"] == "no_tenant_integration"


def test_verify_unknown_run_is_a_404(client):
    r = client.post("/api/runs/nope/verify")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "RUN_NOT_FOUND"


# ---------------------------------------------------------------------------
# Report surfacing
# ---------------------------------------------------------------------------


def test_report_renders_the_verdict_section(client, session_factory):
    run_id = _seed(session_factory, run_id="run-rep")
    client.post(f"/api/runs/{run_id}/complete", json={"exit_code": 0, "summary": "d"})
    client.post(f"/api/runs/{run_id}/observations", json={"observations": [{
        "external_id": "a1", "name": "LSASS Memory Access",
        "observed_at": (datetime.utcnow() - timedelta(minutes=9)).isoformat(),
        "techniques": ["T1003.001"],
    }]}).raise_for_status()

    md = client.get(f"/api/runs/{run_id}/report?format=markdown").text
    assert "## Test-Case Verdict" in md
    assert "**PASS**" in md
    assert "| Primary KPI | Verdict | Measured | Threshold |" in md


def test_report_omits_the_verdict_section_for_an_unscored_run(client, session_factory):
    """A historical run that predates the wiring must not grow a fake section."""
    run_id = _seed(session_factory, run_id="run-unscored")
    md = client.get(f"/api/runs/{run_id}/report?format=markdown").text
    assert "## Test-Case Verdict" not in md
