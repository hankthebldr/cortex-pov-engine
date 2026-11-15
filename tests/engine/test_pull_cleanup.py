"""A pull task carries the scenario's cleanup, resolved for the agent's OS (#324).

Pull mode never ran ``cleanup.commands``: only the push bundle emitted them, so
every pull run left its files, units, cron lines and accounts on the target.
The task a beacon collects now carries the commands that run on ITS operating
system, taken from the resolver the push bundle already uses
(``push_generator._resolve_cleanup``), and the beacon runs them when the task
ends.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

POSIX_LINE = "rm -f /tmp/cortexsim_marker"
POWERSHELL_LINE = "Remove-Item -Force C:\\Windows\\Temp\\cortexsim_marker.txt"
CMD_LINE = "reg.exe delete HKCU\\Software\\CortexSimMarker /f"


@pytest.fixture
def session_factory() -> async_sessionmaker:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _init() -> None:
        from database import Base
        import models  # noqa: F401

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    return SessionLocal


async def _scenario(db, *, cleanup, scenario_id="SIM-EDR-001"):
    from models import Scenario

    db.add(Scenario(
        scenario_id=scenario_id, name="Test", version="1.0", status="active",
        plane="EDR", uc_ref="UCS-EDR-01", uc_name="x", tc_ref="TC-EDR-01", tc_name="y",
        mitre_tactic="TA0003", mitre_tactic_name="Persistence",
        mitre_technique="T1053", mitre_technique_name="Scheduled Task",
        execution_identity={"default": "root"},
        push_supported=True, pull_supported=True,
        # `echo '<literal>'` means the same thing in sh and PowerShell, so the
        # one step resolves for a POSIX agent and for a Windows one.
        steps=[{"id": "step-01", "name": "s", "identity": "root",
                "command": "echo 'cortexsim'", "expected_detections": []}],
        cleanup=cleanup,
    ))
    await db.commit()


async def _agent(db, agent_id, os_name, *, capabilities):
    from models import Agent

    now = datetime.utcnow()
    db.add(Agent(agent_id=agent_id, hostname=f"host-{agent_id}", os=os_name,
                 capabilities=capabilities, interpreters=[],
                 registered_at=now, last_seen=now, status="online"))
    await db.commit()


NEW_POSIX = ["shell", "identity-harness", "artifact-fetch", "cleanup"]
NEW_WINDOWS = ["shell", "powershell", "artifact-fetch", "cleanup"]
OLD_POSIX = ["shell", "identity-harness", "artifact-fetch"]


def _run_cleanup(session_factory, run_id):
    """`runs.cleanup` as the launch left it."""
    from models import Run

    async def _go():
        async with session_factory() as db:
            return (await db.execute(
                select(Run.cleanup).where(Run.run_id == run_id))).scalar_one()

    return asyncio.run(_go())


def _launch(session_factory, *, cleanup, os_name="linux", capabilities=None,
            scenario_id="SIM-EDR-001", agent_id="agent-1"):
    """Launch one pull run; returns (LaunchResult, queued task dict, Run.output)."""
    from engine.orchestrator import Orchestrator
    from models import Run

    async def _go():
        orch = Orchestrator()
        async with session_factory() as db:
            await _scenario(db, cleanup=cleanup, scenario_id=scenario_id)
            await _agent(db, agent_id, os_name,
                         capabilities=NEW_POSIX if capabilities is None else capabilities)
            res = await orch.launch(scenario_id=scenario_id, mode="pull", db=db,
                                    target_agent_id=agent_id)
            assert res.success, res.error
            task = orch.peek_queue(agent_id)[0].to_dict()
            output = (await db.execute(
                select(Run.output).where(Run.run_id == res.run_id))).scalar_one()
            return res, task, output or ""

    return asyncio.run(_go())


def test_pull_task_carries_cleanup_for_target_os(session_factory):
    cleanup = {"commands": [POSIX_LINE, POWERSHELL_LINE, CMD_LINE]}

    res, task, _out = _launch(session_factory, cleanup=cleanup, os_name="linux")
    assert task["cleanup"] == {
        "commands": [{"command": POSIX_LINE, "shell": ""}],
        "timeout_seconds": 120,
    }
    # The run records which agent the task went to and how many commands it
    # carried. What the beacon reports later fills this entry; the report is
    # never taken at its word for either.
    assert _run_cleanup(session_factory, res.run_id) == [{
        "agent_id": "agent-1", "task_id": task["task_id"], "commands": 1,
        "status": "pending", "ran": 0, "failed": 0, "reason": None,
    }]


def test_pull_task_for_a_windows_agent_carries_the_windows_lines(session_factory):
    cleanup = {"commands": [POSIX_LINE, POWERSHELL_LINE, CMD_LINE]}

    _res, task, _out = _launch(session_factory, cleanup=cleanup, os_name="windows",
                               capabilities=NEW_WINDOWS)
    assert task["cleanup"] == {
        "commands": [{"command": POWERSHELL_LINE, "shell": "powershell"},
                     {"command": CMD_LINE, "shell": "cmd"}],
        "timeout_seconds": 120,
    }


def test_windows_only_cleanup_line_withheld_on_posix(session_factory):
    """On Linux a Windows teardown line removes nothing and prints a
    `command not found`; the push bundle withholds it and so does the task."""
    cleanup = {"commands": [POWERSHELL_LINE, POSIX_LINE, CMD_LINE,
                            "userdel -r cortexsim_testuser || true"]}

    _res, task, _out = _launch(session_factory, cleanup=cleanup, os_name="linux")
    sent = [c["command"] for c in task["cleanup"]["commands"]]
    assert sent == [POSIX_LINE, "userdel -r cortexsim_testuser || true"]
    assert all(c["shell"] == "" for c in task["cleanup"]["commands"])


def test_old_beacon_launch_warns_cleanup_not_run(session_factory):
    cleanup = {"commands": [POSIX_LINE]}

    res, task, out = _launch(session_factory, cleanup=cleanup, capabilities=OLD_POSIX)
    assert res.warnings == [{
        "code": "CLEANUP_NOT_RUN",
        "level": "info",
        "agent_id": "agent-1",
        "detail": "this beacon does not run scenario cleanup; update it, "
                  "or clean the host by hand after the run",
    }]
    # On the run record too: the launch response reaches one browser tab.
    assert "!! CLEANUP_NOT_RUN: agent 'agent-1'" in out
    # The task still carries the commands: a roster can be older than the beacon.
    assert task["cleanup"]["commands"] == [{"command": POSIX_LINE, "shell": ""}]

    # The warning is about commands that will not run. A scenario whose only
    # cleanup line is Windows teardown sends a Linux agent none: no `cleanup`
    # key on the task, and nothing to warn about.
    res, task, out = _launch(session_factory, cleanup={"commands": [POWERSHELL_LINE]},
                             capabilities=OLD_POSIX,
                             scenario_id="SIM-EDR-002", agent_id="agent-2")
    assert "cleanup" not in task
    assert res.warnings == []
    assert "CLEANUP_NOT_RUN" not in out
    assert _run_cleanup(session_factory, res.run_id) is None

    # And a beacon that advertises `cleanup` is not warned about.
    res, _task, out = _launch(session_factory, cleanup={"commands": [POSIX_LINE]},
                              capabilities=NEW_POSIX,
                              scenario_id="SIM-EDR-003", agent_id="agent-3")
    assert res.warnings == []
    assert "CLEANUP_NOT_RUN" not in out


def test_the_dry_run_reports_the_same_cleanup_warning(session_factory):
    from engine.orchestrator import Orchestrator

    async def _go():
        orch = Orchestrator()
        async with session_factory() as db:
            await _scenario(db, cleanup={"commands": [POSIX_LINE]})
            await _agent(db, "agent-1", "linux", capabilities=OLD_POSIX)
            ev = await orch.preflight(scenario_id="SIM-EDR-001", mode="pull", db=db,
                                      target_agent_id="agent-1")
            return ev

    ev = asyncio.run(_go())
    assert ev.refusal is None
    assert [w["code"] for w in ev.warnings if isinstance(w, dict)] == ["CLEANUP_NOT_RUN"]


def test_rehydrated_task_keeps_cleanup(session_factory):
    """A SimCore restart rebuilds the queue from `queued_tasks`. A task that
    came back without its cleanup would leave the host as dirty as before."""
    from engine.orchestrator import Orchestrator

    expected = {"commands": [{"command": POSIX_LINE, "shell": ""}], "timeout_seconds": 120}

    async def _go():
        first = Orchestrator()
        async with session_factory() as db:
            await _scenario(db, cleanup={"commands": [POSIX_LINE, POWERSHELL_LINE]})
            await _agent(db, "agent-1", "linux", capabilities=NEW_POSIX)
            res = await first.launch(scenario_id="SIM-EDR-001", mode="pull", db=db,
                                     target_agent_id="agent-1")
            assert res.success

        restarted = Orchestrator()
        assert restarted.peek_queue("agent-1") == []
        async with session_factory() as db:
            stats = await restarted.rehydrate(db)
            assert stats["rehydrated"] == 1
            task = await restarted.dequeue_for_agent("agent-1", db)
            return task.to_dict()

    assert asyncio.run(_go())["cleanup"] == expected


def test_each_endpoint_of_a_composed_run_gets_cleanup_for_its_own_os(session_factory):
    """A second endpoint is its own task; each carries the lines its OS runs."""
    from engine.orchestrator import Orchestrator
    from models import Scenario

    async def _go():
        orch = Orchestrator()
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id="SIM-MP-900", name="Two endpoints", version="1.0",
                status="active", plane="ANALYTICS",
                uc_ref="UCS-EDR-01", uc_name="x", tc_ref="TC-EDR-01", tc_name="y",
                mitre_tactic="TA0008", mitre_tactic_name="Lateral Movement",
                mitre_technique="T1021", mitre_technique_name="Remote Services",
                execution_identity={"default": "root"},
                push_supported=False, pull_supported=True,
                steps=[
                    {"id": "step-01", "name": "a", "identity": "root",
                     "command": "echo 'one'", "expected_detections": []},
                    {"id": "step-02", "name": "b", "identity": "root", "target": "win-1",
                     "command": "echo 'two'", "expected_detections": []},
                ],
                cleanup={"commands": [POSIX_LINE, POWERSHELL_LINE]},
            ))
            await db.commit()
            await _agent(db, "lin-1", "linux", capabilities=NEW_POSIX)
            await _agent(db, "win-1", "windows", capabilities=NEW_WINDOWS)
            res = await orch.launch(scenario_id="SIM-MP-900", mode="pull", db=db,
                                    target_agent_id="lin-1")
            assert res.success, res.error
            return (res.run_id, orch.peek_queue("lin-1")[0].to_dict(),
                    orch.peek_queue("win-1")[0].to_dict())

    run_id, lin, win = asyncio.run(_go())
    assert lin["cleanup"]["commands"] == [{"command": POSIX_LINE, "shell": ""}]
    assert win["cleanup"]["commands"] == [{"command": POWERSHELL_LINE, "shell": "powershell"}]
    assert [(e["agent_id"], e["task_id"], e["commands"], e["status"])
            for e in _run_cleanup(session_factory, run_id)] == [
        ("lin-1", lin["task_id"], 1, "pending"), ("win-1", win["task_id"], 1, "pending")]


def test_an_existing_database_gains_the_cleanup_column(tmp_path):
    """`create_all` never adds a column, so a box with an older cortexsim.db
    gets `runs.cleanup` at startup. A run from before reads NULL."""
    from sqlalchemy import create_engine, inspect, text

    from database import _migrate_run_cleanup_columns

    eng = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE runs (run_id VARCHAR PRIMARY KEY, status VARCHAR)"))
        conn.execute(text("INSERT INTO runs (run_id, status) VALUES ('old', 'complete')"))
    with eng.begin() as conn:
        _migrate_run_cleanup_columns(conn)
    with eng.begin() as conn:
        _migrate_run_cleanup_columns(conn)  # a second boot changes nothing
        assert "cleanup" in {c["name"] for c in inspect(conn).get_columns("runs")}
        assert conn.execute(text("SELECT cleanup FROM runs")).scalar() is None
