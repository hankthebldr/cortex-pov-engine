"""A run refused at the artifact-staging gate never executed, so it is never matched.

``GET /api/agents/{id}/tasks`` refuses to hand an artifact-carrying task to a
beacon that does not advertise ``artifact-fetch`` and fails the run with
"NO STEPS RAN. This run proves NOTHING about the customer's detection
coverage." The run's own output said that; its Results did not. The refusal
stamped no Result, and the dequeue had already stamped ``delivered_at``, so
neither OPS-01 defence applied (stamping, or the never-delivered candidate
exclusion). Executed on c852eac:

    GET /tasks                 -> 409 AGENT_CANNOT_STAGE_ARTIFACTS, run failed
    POST /observations (1 alert from the host)
                               -> observed 1, newly_matched 1, coverage 100.0,
                                  mttd {count 1, avg 5.0}
    Result row                 -> observed True, outcome 'detected'

A detection, with an MTTD, credited to a run whose only output line says it
ran nothing. ``REASON_AGENT_CANNOT_STAGE_ARTIFACTS`` was defined for this path
and referenced nowhere.

Stamping is scoped to the REFUSED TASK's own steps: in a multi-endpoint run
the other endpoint's steps may really have executed, and a composed run's EAL
steps ran inside SimCore.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

T0 = datetime(2026, 11, 5, 12, 0, 0)
HOST = "lab-old-beacon"

ARTIFACT = {
    "name": "linpeas.sh",
    "sha256": "0ea7e9ce5fcca464b5998cb73930e36647ebf9b38590fe250bbd5664fe8670a5",
    "path": "/api/shelf/payload/linpeas.sh",
    "dest": "/tmp/.cache/sysinfo.sh",
    "mode": "0755",
    "steps": ["step-01"],
}


@pytest.fixture
def client(session_factory):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.agents import router as agents_router
    from api.connectors import router, runs_reconcile_router
    from api.runs import router as runs_router
    from database import get_db

    async def _get_db():
        async with session_factory() as s:
            yield s

    app = FastAPI()
    for r in (agents_router, router, runs_reconcile_router, runs_router):
        app.include_router(r, prefix="/api")
    app.dependency_overrides[get_db] = _get_db
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_orchestrator():
    from engine.orchestrator import orchestrator

    def _reset():
        orchestrator._aborted.clear()
        orchestrator._queue.clear()
        orchestrator.reset_liveness()

    _reset()
    yield
    _reset()


def _detection(det_id: str, text: str) -> dict:
    return {"plane": "EDR", "type": "BIOC", "description": text, "detection_id": det_id}


async def _seed(db, *, agents: dict[str, list[str]]):
    from models import Agent, Scenario

    db.add(Scenario(
        scenario_id="SIM-EDR-951", name="T", version="1.0", status="active", plane="EDR",
        uc_ref="UCS-EDR-01", uc_name="x", tc_ref="TC-EDR-01", tc_name="y",
        mitre_tactic="TA0006", mitre_tactic_name="Credential Access",
        mitre_technique="T1003.001", mitre_technique_name="LSASS Memory",
        execution_identity={"default": "root"}, push_supported=True, pull_supported=True,
        steps=[
            {"id": "step-01", "name": "enumerate", "identity": "root", "command": "id",
             "mitre_technique": "T1003.001",
             "expected_detections": [
                 _detection("bioc-lsass-access", "LSASS memory access")]},
            {"id": "step-02", "name": "discover", "identity": "root", "command": "id",
             "mitre_technique": "T1082",
             "expected_detections": [
                 _detection("bioc-sysinfo", "System information discovery")]},
        ],
    ))
    for agent_id, caps in agents.items():
        db.add(Agent(agent_id=agent_id, hostname=HOST, os="linux",
                     capabilities=caps, interpreters=[],
                     registered_at=T0, last_seen=datetime.utcnow(), status="online"))
    await db.commit()


def _launch(session_factory, agents: dict[str, list[str]], target: str) -> str:
    """A real pull launch; executed_at pinned to T0 so the MTTD is deterministic."""
    from engine.orchestrator import orchestrator
    from models import Result, Run

    async def _go():
        async with session_factory() as db:
            await _seed(db, agents=agents)
            res = await orchestrator.launch(scenario_id="SIM-EDR-951", mode="pull",
                                            db=db, target_agent_id=target)
            assert res.success, res.message
            for r in (await db.execute(select(Result))).scalars().all():
                r.executed_at = T0
            run = (await db.execute(select(Run))).scalar_one()
            run.started_at = T0
            await db.commit()
            return res.run_id

    return asyncio.run(_go())


def _attach_artifact(agent_id: str, *, only_steps: list[str] | None = None) -> None:
    """Make the queued task artifact-carrying, as a shelf-backed scenario's is."""
    from engine.orchestrator import orchestrator

    (task,) = orchestrator._queue[agent_id]
    task.artifacts = [dict(ARTIFACT)]
    if only_steps is not None:
        task.steps = [s for s in task.steps if s.get("id") in only_steps]


def _alert(det_id: str, technique: str, name: str, seconds_after: int = 5) -> dict:
    return {"external_id": f"alert-{det_id}", "name": name, "technique": technique,
            "detection_id": det_id, "host": HOST,
            "observed_at": (T0 + timedelta(seconds=seconds_after)).isoformat() + "Z"}


ALERTS = [
    _alert("bioc-lsass-access", "T1003.001", "LSASS memory access"),
    _alert("bioc-sysinfo", "T1082", "System information discovery"),
]


def _rows(session_factory) -> dict[str, object]:
    from models import Result

    async def _go():
        async with session_factory() as db:
            rows = (await db.execute(select(Result))).scalars().all()
            return {r.step_id: r for r in rows}

    return asyncio.run(_go())


def _run(session_factory):
    from models import Run

    async def _go():
        async with session_factory() as db:
            return (await db.execute(select(Run))).scalar_one()

    return asyncio.run(_go())


# ---------------------------------------------------------------------------


def test_a_run_refused_at_the_staging_gate_is_never_credited_an_alert(client, session_factory):
    run_id = _launch(session_factory, {"agent-old": ["shell", "identity-harness"]}, "agent-old")
    _attach_artifact("agent-old")

    r = client.get("/api/agents/agent-old/tasks")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "AGENT_CANNOT_STAGE_ARTIFACTS"

    run = _run(session_factory)
    # The precondition that defeats the delivered_at defence in depth: the
    # dequeue stamped it before the gate refused.
    assert run.status == "failed" and run.delivered_at is not None
    assert "NO STEPS RAN" in (run.output or "")

    rows = _rows(session_factory)
    assert {(r.outcome, r.outcome_reason) for r in rows.values()} == {
        ("not_executed", "AGENT_CANNOT_STAGE_ARTIFACTS")}, {
            k: (v.outcome, v.outcome_reason) for k, v in rows.items()}

    r = client.post(f"/api/runs/{run_id}/observations", json={"observations": ALERTS})
    assert r.status_code == 200, r.text
    body = r.json()
    # c852eac: observed 1, newly_matched 1, coverage_pct 100.0, mttd avg 5.0.
    assert body["newly_matched"] == 0, body
    assert body["observed"] == 0, body
    assert body["mttd"]["count"] == 0, body
    assert body["denominator"] == 0, body
    assert body["excluded"]["not_executed"] == 2, body

    for row in _rows(session_factory).values():
        assert row.observed is False and row.observed_at is None and row.mttd_seconds is None
        assert row.outcome == "not_executed"


def test_only_the_refused_tasks_own_steps_are_stamped(client, session_factory):
    """A fan-out partition: the refused endpoint held step-01 only. step-02
    belongs to another endpoint (or ran in-process) and may really have
    executed, so its row must stay matchable."""
    _launch(session_factory, {"agent-old": ["shell", "identity-harness"]}, "agent-old")
    _attach_artifact("agent-old", only_steps=["step-01"])

    assert client.get("/api/agents/agent-old/tasks").status_code == 409

    rows = _rows(session_factory)
    assert (rows["step-01"].outcome, rows["step-01"].outcome_reason) == (
        "not_executed", "AGENT_CANNOT_STAGE_ARTIFACTS")
    assert rows["step-02"].outcome is None, "a step the refused task never carried was stamped"


def test_a_second_refusal_on_an_already_failed_run_still_stamps_its_steps(client, session_factory):
    """The run is already terminal when this endpoint's task is refused (the
    first endpoint's refusal failed it), so the guarded UPDATE matches no row.
    The steps this task carried still never ran."""
    from models import Run

    run_id = _launch(session_factory, {"agent-old": ["shell", "identity-harness"]}, "agent-old")
    _attach_artifact("agent-old")

    async def _fail_first():
        async with session_factory() as db:
            run = (await db.execute(select(Run))).scalar_one()
            run.status = "failed"
            run.completed_at = datetime.utcnow()
            await db.commit()

    asyncio.run(_fail_first())

    assert client.get("/api/agents/agent-old/tasks").status_code == 409
    rows = _rows(session_factory)
    assert {r.outcome for r in rows.values()} == {"not_executed"}, {
        k: v.outcome for k, v in rows.items()}

    body = client.post(f"/api/runs/{run_id}/observations",
                       json={"observations": ALERTS}).json()
    assert body["newly_matched"] == 0, body


def test_a_capable_agent_is_delivered_and_its_run_is_still_matched(client, session_factory):
    """Negative control: the stamp must not fire on the path that delivers."""
    run_id = _launch(
        session_factory, {"agent-new": ["shell", "identity-harness", "artifact-fetch"]}, "agent-new")
    _attach_artifact("agent-new")

    r = client.get("/api/agents/agent-new/tasks")
    assert r.status_code == 200, r.text
    assert r.json()["task"]["artifacts"] == [ARTIFACT]
    assert {r.outcome for r in _rows(session_factory).values()} == {None}

    body = client.post(f"/api/runs/{run_id}/observations",
                       json={"observations": ALERTS}).json()
    assert body["newly_matched"] == 2, body
