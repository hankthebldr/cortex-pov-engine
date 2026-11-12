"""The artifact-staging capability gate on task delivery.

An OLD beacon decodes a task carrying `artifacts` with encoding/json, which
SILENTLY DROPS the unknown key. It then runs every step WITHOUT its tooling,
exits 0 on all of them, and the absent detections read in a POV report as gaps in
the customer's coverage — the precise manufactured false negative artifact
staging exists to prevent, delivered by the back-compat mechanism itself.

So `GET /api/agents/{id}/tasks` refuses to hand such a task to an agent that does
not advertise `artifact-fetch`, and fails the run LOUDLY instead.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from sqlalchemy import select


ARTIFACT = {
    "name": "linpeas.sh",
    "sha256": "0ea7e9ce5fcca464b5998cb73930e36647ebf9b38590fe250bbd5664fe8670a5",
    "path": "/api/shelf/payload/linpeas.sh",
    "dest": "/tmp/.cache/sysinfo.sh",
    "mode": "0755",
    "steps": ["step-05"],
}


@pytest.fixture
def client(make_client):
    from api.agents import router
    return make_client(router)


@pytest.fixture(autouse=True)
def clean_orchestrator():
    """The orchestrator is a module singleton; keep queues from leaking between
    tests."""
    from engine.orchestrator import orchestrator

    orchestrator._queue.clear()
    orchestrator._aborted.clear()
    yield
    orchestrator._queue.clear()
    orchestrator._aborted.clear()


def _register(client, agent_id: str, capabilities: list[str]) -> None:
    client.post(
        "/api/agents/register",
        json={"agent_id": agent_id, "hostname": "h", "os": "linux",
              "capabilities": capabilities},
    ).raise_for_status()


def _seed_run(session_factory, run_id: str) -> None:
    from models import Run

    async def _go():
        async with session_factory() as db:
            db.add(Run(run_id=run_id, scenario_id="SIM-EDR-999", mode="pull",
                       target="a-old", status="running", started_at=datetime.utcnow()))
            await db.commit()

    asyncio.run(_go())


def _queue_task(agent_id: str, run_id: str, artifacts: list[dict]) -> None:
    from engine.orchestrator import Task, orchestrator

    orchestrator._enqueue(agent_id, Task(
        task_id="t-1", run_id=run_id, scenario_id="SIM-EDR-999",
        steps=[{"id": "step-05", "command": "bash /tmp/.cache/sysinfo.sh"}],
        identity_context=None, artifacts=artifacts,
    ))


def _run_status(session_factory, run_id: str):
    from models import Run

    async def _go():
        async with session_factory() as db:
            row = (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one()
            return row.status, (row.output or "")

    return asyncio.run(_go())


# ---------------------------------------------------------------------------


def test_legacy_agent_is_refused_and_the_run_fails_loudly(client, session_factory):
    _register(client, "a-old", ["shell", "identity-harness"])  # pre-staging roster
    _seed_run(session_factory, "run-legacy")
    _queue_task("a-old", "run-legacy", [ARTIFACT])

    r = client.get("/api/agents/a-old/tasks")

    assert r.status_code == 409
    body = r.json()["detail"]
    assert body["code"] == "AGENT_CANNOT_STAGE_ARTIFACTS"
    # The message must name the artifact, the agent's actual roster and the fix.
    assert "linpeas.sh" in body["detail"]
    assert "identity-harness" in body["detail"]
    assert "/api/agents/install" in body["detail"]

    status, output = _run_status(session_factory, "run-legacy")
    # A run that could not execute must never be mistaken for one that executed
    # and found nothing — and must not hang in `running` forever either.
    assert status == "failed"
    assert "AGENT_CANNOT_STAGE_ARTIFACTS" in output
    assert "NO STEPS RAN" in output
    assert "proves NOTHING" in output


def test_capable_agent_receives_the_artifacts(client, session_factory):
    _register(client, "a-new", ["shell", "identity-harness", "artifact-fetch"])
    _seed_run(session_factory, "run-ok")
    _queue_task("a-new", "run-ok", [ARTIFACT])

    r = client.get("/api/agents/a-new/tasks")

    assert r.status_code == 200
    task = r.json()["task"]
    assert task["artifacts"] == [ARTIFACT]
    status, _ = _run_status(session_factory, "run-ok")
    assert status == "running"


def test_artifact_free_task_is_delivered_to_a_legacy_agent_unchanged(client, session_factory):
    """The gate must not become a general capability requirement: 169 scenarios
    declare no artifacts and must keep working on an unchanged beacon."""
    _register(client, "a-old", ["shell", "identity-harness"])
    _seed_run(session_factory, "run-plain")
    _queue_task("a-old", "run-plain", [])

    r = client.get("/api/agents/a-old/tasks")

    assert r.status_code == 200
    task = r.json()["task"]
    assert "artifacts" not in task  # key omitted entirely, as before
    status, _ = _run_status(session_factory, "run-plain")
    assert status == "running"


def test_refusal_does_not_redeliver_the_task(client, session_factory):
    """The task was already dequeued. A second poll must return idle rather than
    re-refusing forever — the run is terminal and the operator has the message."""
    _register(client, "a-old", ["shell"])
    _seed_run(session_factory, "run-once")
    _queue_task("a-old", "run-once", [ARTIFACT])

    assert client.get("/api/agents/a-old/tasks").status_code == 409
    second = client.get("/api/agents/a-old/tasks")
    assert second.status_code == 200
    assert second.json()["task"] is None


# ---------------------------------------------------------------------------
# The refusal names both causes, and a fix the agent's own OS can run
# ---------------------------------------------------------------------------
#
# The roster the gate reads is not always the beacon's. An enrolled row carries
# what the INSTALLER sent (Windows: shell, powershell; POSIX: shell,
# identity-harness) until the beacon's own Register lands, and the beacon only
# re-registers on a 404. If its boot-time Register failed, a current beacon that
# CAN stage is refused, and the message told the DC, as fact, that "it predates
# artifact staging" and to run a `curl ... os=linux | bash` on a Windows host.


def _refusal_for(client, session_factory, *, agent_os: str, caps: list[str]) -> str:
    client.post("/api/agents/register",
                json={"agent_id": "a-enrolled", "hostname": "h", "os": agent_os,
                      "capabilities": caps}).raise_for_status()
    _seed_run(session_factory, "run-x")
    _queue_task("a-enrolled", "run-x", [ARTIFACT])
    r = client.get("/api/agents/a-enrolled/tasks")
    assert r.status_code == 409
    return r.json()["detail"]["detail"]


def test_the_refusal_does_not_assert_one_cause_as_fact(client, session_factory):
    detail = _refusal_for(client, session_factory, agent_os="linux",
                          caps=["shell", "identity-harness"])
    assert "predates artifact staging" in detail
    assert "has not registered since it was enrolled" in detail, detail
    assert "it predates artifact staging and would" not in detail, (
        "one of two possible causes is still stated as fact")


def test_a_windows_agent_is_given_a_windows_fix(client, session_factory):
    detail = _refusal_for(client, session_factory, agent_os="windows",
                          caps=["shell", "powershell"])
    assert "Restart-Service CortexSimAgent" in detail, detail
    assert "/api/agents/install?os=windows" in detail and "| iex" in detail, detail
    assert "curl" not in detail and "| bash" not in detail and "os=linux" not in detail, detail
    assert "$env:CORTEXSIM_TOKEN" in detail, "the token belongs in the env var, not the URL"


def test_a_posix_agent_is_given_a_posix_fix_naming_this_server(client, session_factory):
    detail = _refusal_for(client, session_factory, agent_os="linux",
                          caps=["shell", "identity-harness"])
    assert "restart" in detail and "cortexsim-agent" in detail, detail
    # The beacon reached SimCore at this address, so it is the one to name.
    assert "curl -fsSL 'http://testserver/api/agents/install?os=linux'" in detail, detail
    assert "<server>" not in detail
    assert "iex" not in detail and "Restart-Service" not in detail, detail
