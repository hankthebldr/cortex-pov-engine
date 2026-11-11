"""Shared scaffolding for the run-lifecycle -> reconcile guard tests.

Every helper drives the REAL routers (runs, agents, connectors, results) over
the in-memory database from ``tests/api/conftest.py``; nothing here stubs the
orchestrator, the matcher or ``apply_verdicts``. A test that wants to know what
a DC would see asks the API, exactly as the console does.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Optional

THREE_STEP = "SIM-RG-003"
ONE_STEP = "SIM-RG-001"


def build_client(make_client):
    from api.agents import router as agents_router
    from api.connectors import router as connectors_router
    from api.connectors import runs_reconcile_router
    from api.results import router as results_router
    from api.runs import router as runs_router

    return make_client(runs_router, agents_router, connectors_router,
                       runs_reconcile_router, results_router)


def reset_orchestrator() -> None:
    from engine.orchestrator import orchestrator

    orchestrator._aborted.clear()
    orchestrator._queue.clear()


def _detection(n: int, name: str) -> list[dict[str, Any]]:
    return [{"plane": "EDR", "type": "BIOC", "description": name,
             "detection_id": f"d-{n}", "name": name}]


def seed_scenarios(session_factory) -> None:
    """Two scenarios: a three-step chain (echo / sleep / echo) and a one-step."""
    from models import Scenario

    def _scenario(sid: str, steps: list[dict[str, Any]]) -> Scenario:
        return Scenario(
            scenario_id=sid, name="T", version="1.0", status="active", plane="EDR",
            uc_ref="UCS-EDR-01", uc_name="x", tc_ref="TC-EDR-01", tc_name="y",
            mitre_tactic="TA0006", mitre_tactic_name="Credential Access",
            mitre_technique="T1003", mitre_technique_name="OS Credential Dumping",
            execution_identity={"default": "root"}, push_supported=True,
            pull_supported=True, steps=steps)

    async def _go():
        async with session_factory() as db:
            db.add(_scenario(THREE_STEP, [
                {"id": "step-01", "name": "a", "command": "echo one", "identity": "root",
                 "mitre_technique": "T1003",
                 "expected_detections": _detection(1, "Shadow file read")},
                {"id": "step-02", "name": "b", "command": "sleep 20", "identity": "root",
                 "mitre_technique": "T1059",
                 "expected_detections": _detection(2, "Suspicious shell spawn")},
                {"id": "step-03", "name": "c", "command": "echo three", "identity": "root",
                 "mitre_technique": "T1486",
                 "expected_detections": _detection(3, "Ransomware file encryption")},
            ]))
            db.add(_scenario(ONE_STEP, [
                {"id": "step-01", "name": "a", "command": "cat /etc/shadow",
                 "identity": "root", "mitre_technique": "T1003",
                 "expected_detections": _detection(1, "Shadow file read")},
            ]))
            await db.commit()

    asyncio.run(_go())


def register(client, agent_id: str, hostname: str):
    r = client.post("/api/agents/register", json={
        "agent_id": agent_id, "hostname": hostname, "os": "linux",
        "capabilities": ["shell", "identity-harness", "artifact-fetch"]})
    assert r.status_code == 200, r.text
    return r


def launch(client, *, agent_id: Optional[str] = None, mode: str = "pull",
           scenario_id: str = ONE_STEP) -> str:
    body: dict[str, Any] = {"scenario_id": scenario_id, "mode": mode}
    if agent_id:
        body["target_agent_id"] = agent_id
    r = client.post("/api/runs", json=body)
    assert r.status_code == 200, r.text
    return r.json()["run_id"]


def collect_task(client, agent_id: str) -> dict[str, Any]:
    """The beacon's poll: delivers the task and stamps ``delivered_at``."""
    r = client.get(f"/api/agents/{agent_id}/tasks")
    assert r.status_code == 200, r.text
    task = r.json().get("task")
    assert task, "no task was queued for the agent"
    return task


def alert(ext: str, name: str, technique: str, detection_id: str, host: Optional[str],
          *, plus: float = 5) -> dict[str, Any]:
    out = {"external_id": ext, "name": name, "mitre_technique": technique,
           "detection_id": detection_id,
           "observed_at": (datetime.utcnow() + timedelta(seconds=plus)).isoformat() + "Z"}
    if host is not None:
        out["host"] = host
    return out


def observe(client, run_id: str, alerts: list[dict[str, Any]], *,
            host: Optional[str] = None, reevaluate: bool = False):
    body: dict[str, Any] = {"observations": alerts, "reevaluate": reevaluate}
    if host:
        body["host"] = host
    return client.post(f"/api/runs/{run_id}/observations", json=body)


def rows(client, run_id: str) -> dict[str, dict[str, Any]]:
    """``{step_id: result row}`` as the results API reports them."""
    r = client.get(f"/api/results/{run_id}")
    assert r.status_code == 200, r.text
    return {x["step_id"]: x for x in r.json()["results"]}


def results_body(client, run_id: str) -> dict[str, Any]:
    r = client.get(f"/api/results/{run_id}")
    assert r.status_code == 200, r.text
    return r.json()


def view(row: dict[str, Any]) -> tuple:
    """The five fields every one of these guards is about."""
    return (row.get("observed"), row.get("outcome"), row.get("outcome_reason"),
            row.get("mttd_seconds") is not None)
