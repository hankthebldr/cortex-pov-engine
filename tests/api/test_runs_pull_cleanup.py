"""What a beacon reports about scenario cleanup lands on the run (#324).

The beacon runs the task's cleanup commands when the task ends, writes one
``=== CLEANUP`` section to the run output and sends the outcome on
``/complete``. SimCore keeps the outcome on ``runs.cleanup`` (one entry per
task), keeps the section's frame under the output cap, and stops telling the
report reader that pull mode ran no cleanup when it did.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

SECTION = (
    "=== CLEANUP · 2 commands · identity=direct ===\n"
    "$ rm -f /tmp/cortexsim_marker\n"
    "--- cleanup 1/2 exit_code=0 duration=4ms ---\n"
    "$ systemctl disable --now cortexsim-decoy.service\n"
    "--- STDERR ---\n"
    "sh: 1: systemctl: command not found\n"
    "--- cleanup 2/2 exit_code=127 duration=3ms ---\n"
)


@pytest.fixture
def client(make_client):
    from api.runs import router as runs_router

    return make_client(runs_router)


@pytest.fixture(autouse=True)
def _clean_orchestrator():
    from engine.orchestrator import orchestrator

    orchestrator._aborted.clear()
    orchestrator._queue.clear()
    yield
    orchestrator._aborted.clear()
    orchestrator._queue.clear()


def _seed(session_factory, run_id="r1", *, open_tasks=1, status="running",
          output=None, cleanup_commands=("rm -f /tmp/cortexsim_marker",)):
    from models import Run, Scenario

    async def _go():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id=f"SIM-CL-{run_id}", name="cleanup", version="1.0",
                status="active", plane="EDR", detection_types=["BIOC"],
                uc_ref="UC-01", tc_ref="TC-01", uc_name="uc", tc_name="tc",
                mitre_tactic="TA0003", mitre_tactic_name="Persistence",
                mitre_technique="T1053", mitre_technique_name="Scheduled Task",
                execution_identity={}, push_supported=True, pull_supported=True,
                external_tools=[], steps=[],
                cleanup={"commands": list(cleanup_commands)},
            ))
            db.add(Run(run_id=run_id, scenario_id=f"SIM-CL-{run_id}", mode="pull",
                       target="agent-1", status=status, open_tasks=open_tasks,
                       output=output,
                       started_at=datetime.utcnow() - timedelta(seconds=30),
                       delivered_at=datetime.utcnow() - timedelta(seconds=29)))
            await db.commit()

    asyncio.run(_go())
    return run_id


def _complete(client, run_id="r1", *, agent_id="agent-1", task_id="task-1",
              exit_code=0, cleanup=None):
    body = {"exit_code": exit_code, "summary": "done", "steps": [],
            "agent_id": agent_id, "task_id": task_id}
    if cleanup is not None:
        body["cleanup"] = cleanup
    return client.post(f"/api/runs/{run_id}/complete", json=body)


def _output(session_factory, run_id="r1") -> str:
    from models import Run

    async def _go():
        async with session_factory() as db:
            return (await db.execute(
                select(Run.output).where(Run.run_id == run_id))).scalar_one() or ""

    return asyncio.run(_go())


CLEAN = {"status": "clean", "ran": 2, "failed": 0, "reason": None}
FAILED = {"status": "failed", "ran": 3, "failed": 1, "reason": "COMMAND_FAILED"}


def test_complete_stores_cleanup_outcome(client, session_factory):
    _seed(session_factory)

    r = _complete(client, cleanup=FAILED)
    assert r.status_code == 200, r.text

    run = client.get("/api/runs/r1").json()
    assert run["status"] == "complete"
    assert run["cleanup"] == [{
        "agent_id": "agent-1", "task_id": "task-1",
        "status": "failed", "ran": 3, "failed": 1, "reason": "COMMAND_FAILED",
    }]


def test_a_run_whose_beacon_reported_no_cleanup_stores_none(client, session_factory):
    """An older beacon sends no `cleanup`; the run says nothing, not `clean`."""
    _seed(session_factory)
    assert _complete(client).status_code == 200
    assert client.get("/api/runs/r1").json()["cleanup"] is None


def test_a_retried_complete_stores_the_outcome_once(client, session_factory):
    _seed(session_factory, open_tasks=2)

    assert _complete(client, cleanup=CLEAN).status_code == 200
    assert _complete(client, cleanup=CLEAN).json().get("duplicate") is True

    assert len(client.get("/api/runs/r1").json()["cleanup"]) == 1


def test_each_endpoint_of_a_fan_out_adds_its_own_entry(client, session_factory):
    _seed(session_factory, open_tasks=2)

    assert _complete(client, agent_id="agent-1", task_id="task-1",
                     cleanup=CLEAN).status_code == 200
    assert _complete(client, agent_id="agent-2", task_id="task-2",
                     cleanup=FAILED).status_code == 200

    entries = client.get("/api/runs/r1").json()["cleanup"]
    assert [(e["agent_id"], e["status"]) for e in entries] == [
        ("agent-1", "clean"), ("agent-2", "failed")]


def test_cleanup_after_an_operator_abort_is_still_stored(client, session_factory):
    """The abort makes the run terminal before the beacon's /complete lands
    (409). What the beacon then cleaned up is still worth knowing."""
    _seed(session_factory)
    assert client.post("/api/runs/r1/abort").status_code == 200

    r = _complete(client, exit_code=130, cleanup=CLEAN)
    assert r.status_code == 409, r.text
    # The beacon treats 409 as delivered; a repeat must not add a second entry.
    assert _complete(client, exit_code=130, cleanup=CLEAN).status_code == 409

    run = client.get("/api/runs/r1").json()
    assert run["status"] == "aborted"
    assert run["cleanup"] == [{
        "agent_id": "agent-1", "task_id": "task-1",
        "status": "clean", "ran": 2, "failed": 0, "reason": None,
    }]


def test_cleanup_frame_survives_output_cap(client, session_factory, monkeypatch):
    """Past the run cap only integrity lines and the step frame are kept. The
    cleanup section's header and per-command footers are its frame."""
    from api import runs

    monkeypatch.setattr(runs, "RUN_OUTPUT_CAP_BYTES", 1000, raising=False)
    _seed(session_factory)
    assert client.post("/api/runs/r1/output", json={"output": "x" * 900 + "\n"}).status_code == 200

    r = client.post("/api/runs/r1/output", json={"output": SECTION})
    assert r.status_code == 200, r.text
    assert r.json()["truncated"] is True

    out = _output(session_factory)
    assert "=== CLEANUP · 2 commands · identity=direct ===" in out
    assert "--- cleanup 1/2 exit_code=0 duration=4ms ---" in out
    assert "--- cleanup 2/2 exit_code=127 duration=3ms ---" in out
    # Ordinary lines past the cap are still dropped.
    assert "$ rm -f /tmp/cortexsim_marker" not in out


def test_an_oversized_cleanup_chunk_keeps_its_frame(client, session_factory):
    from api.runs import RUN_OUTPUT_CHUNK_MAX_BYTES

    _seed(session_factory)
    noise = "removed '/var/tmp/cortexsim/file'\n" * (2 * RUN_OUTPUT_CHUNK_MAX_BYTES // 34)
    section = ("=== CLEANUP · 1 commands · identity=direct ===\n"
               "$ rm -rfv /var/tmp/cortexsim\n" + noise
               + "--- cleanup 1/1 exit_code=0 duration=2.1s ---\n")
    r = client.post("/api/runs/r1/output", json={"output": section})
    assert r.status_code == 200, r.text
    assert r.json()["chunk_truncated"] is True

    out = _output(session_factory)
    assert "=== CLEANUP · 1 commands · identity=direct ===" in out
    assert "--- cleanup 1/1 exit_code=0 duration=2.1s ---" in out


# ---------------------------------------------------------------------------
# The report reads the stored outcome
# ---------------------------------------------------------------------------

STEP_FRAME = "=== STEP 1/1 · step-01 · T1053 · identity=root ===\nok\n--- exit_code=0 duration=1s ---\n"


def _report(client, run_id="r1") -> str:
    r = client.get(f"/api/runs/{run_id}/report?format=markdown")
    assert r.status_code == 200, r.text
    return r.text


def test_report_says_nothing_about_cleanup_that_ran_clean(client, session_factory):
    _seed(session_factory, output=STEP_FRAME)
    assert _complete(client, cleanup=CLEAN).status_code == 200

    body = _report(client)
    assert "Cleanup Not Run" not in body
    assert "Cleanup Incomplete" not in body


def test_report_names_cleanup_that_failed(client, session_factory):
    _seed(session_factory, output=STEP_FRAME)
    assert _complete(client, cleanup=FAILED).status_code == 200

    body = _report(client)
    assert "Cleanup Incomplete" in body
    section = body.split("Cleanup Incomplete", 1)[1].split("\n## ", 1)[0]
    assert "agent-1" in section and "1 of 3" in section
    assert "rm -f /tmp/cortexsim_marker" in section
    assert "Cleanup Not Run" not in body


def test_cleanup_output_is_not_read_as_a_step_that_did_not_run(client, session_factory):
    """`command not found` in a step's output means the TTP did not run as
    authored. The same words from a cleanup command say nothing about a step."""
    _seed(session_factory, output=STEP_FRAME)
    assert client.post("/api/runs/r1/output", json={"output": SECTION}).status_code == 200
    assert _complete(client, cleanup={"status": "failed", "ran": 2, "failed": 1,
                                      "reason": "COMMAND_FAILED"}).status_code == 200

    body = _report(client)
    assert "command not found" not in body
    assert "READ THIS FIRST" not in body

    # A step that printed it is still reported, cleanup section or not.
    step = ("=== STEP 1/1 · step-01 · T1053 · identity=root ===\n"
            "sh: 1: nmap: command not found\n--- exit_code=127 duration=1s ---\n")
    _seed(session_factory, "r2", output=step)
    assert client.post("/api/runs/r2/output", json={"output": SECTION}).status_code == 200
    assert _complete(client, "r2", exit_code=127, cleanup=CLEAN).status_code == 200
    assert "**command not found**" in _report(client, "r2")
