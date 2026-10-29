"""Orchestrator projects required binaries and surfaces the egress warning.

Server half of the beacon's TOOL_ABSENT gate: the beacon refuses a step whose
`required_binaries` entry is not on PATH; this proves the list is filled from
the packs actually substituted into each step, and that the pull-mode egress
caveat reaches Run.output, where a DC reads it.
"""
from __future__ import annotations

import asyncio
import logging

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


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


def _pack(adapter_id: str, binary):
    from tools.adapter_loader import ToolAdapterSchema

    install = {
        "runtime_install_command": f"apt-get install -y {adapter_id}",
        "artifact_exempt": {"reason_code": "DISTRO_PACKAGE",
                            "reason": "Installs from the distro package repository."},
    }
    if binary:
        install["binary"] = binary
    return ToolAdapterSchema(**{
        "adapter_id": adapter_id,
        "name": adapter_id,
        "version": "1",
        "tier": 4,
        "category": "network-scan",
        "upstream": {"repo": "https://example.com/x", "license": "MIT",
                     "attribution": "x"},
        "cortex_signal": {"planes": ["EDR"], "expected_techniques": ["T1046"]},
        "safety_class": "safe",
        "install": install,
        "invoke": {"target_platform": "linux",
                   "run_template": "{binary} {flags}" if binary else "tool {flags}",
                   "default_args": {"flags": "-x"},
                   "identity_required": "root"},
    })


@pytest.fixture
def wired(monkeypatch):
    from tools.adapter_catalog import catalog

    packs = {
        "TOOL-NMAP": _pack("TOOL-NMAP", "nmap"),
        "TOOL-NOBIN": _pack("TOOL-NOBIN", None),
    }
    monkeypatch.setattr(catalog, "_by_id", packs)
    return catalog


STEPS = [
    {"id": "step-01", "name": "a", "identity": "root", "command": "echo hi",
     "expected_detections": []},
    {"id": "step-02", "name": "b", "identity": "root",
     "command": "{adapter:TOOL-NMAP} && {adapter:TOOL-NMAP}",
     "expected_detections": []},
    {"id": "step-03", "name": "c", "identity": "root",
     "command": "{adapter:TOOL-NOBIN}", "expected_detections": []},
]


async def _seed(db, steps, tools):
    from models import Agent, Scenario
    from datetime import datetime

    db.add(Scenario(
        scenario_id="SIM-EDR-998", name="T", version="1.0", status="active",
        plane="EDR", uc_ref="UCS-EDR-01", uc_name="x", tc_ref="TC-EDR-01",
        tc_name="y", mitre_tactic="TA0043", mitre_tactic_name="Recon",
        mitre_technique="T1046", mitre_technique_name="NSD",
        execution_identity={"default": "root"}, push_supported=True,
        pull_supported=True, steps=steps, external_tools=tools,
    ))
    db.add(Agent(
        agent_id="agent-1", hostname="h", os="linux",
        capabilities=["shell", "identity-harness"], interpreters=[],
        registered_at=datetime.utcnow(), last_seen=datetime.utcnow(),
        status="online",
    ))
    await db.commit()


def _launch(session_factory, steps, tools):
    from engine.orchestrator import Orchestrator
    from models import Run

    async def _go():
        orch = Orchestrator()
        async with session_factory() as db:
            await _seed(db, steps, tools)
            res = await orch.launch(
                scenario_id="SIM-EDR-998", mode="pull", db=db,
                target_agent_id="agent-1",
            )
            assert res.success, res
            run = (await db.execute(
                select(Run).where(Run.run_id == res.run_id))).scalar_one()
            return orch, res, run

    return asyncio.run(_go())


def _queued_steps(orch):
    queue = next(iter(orch._queue.values()))
    return list(queue)[0].steps


def test_required_binaries_follow_adapter_placeholders(session_factory, wired):
    orch, _res, _run = _launch(
        session_factory, STEPS, [{"name": "nmap", "adapter_ref": "TOOL-NMAP"}])
    steps = _queued_steps(orch)
    assert "required_binaries" not in steps[0]
    assert steps[1]["required_binaries"] == ["nmap"]      # deduplicated
    assert "required_binaries" not in steps[2]            # pack has no binary


def test_pack_without_binary_is_unchecked_and_warned(session_factory, wired, caplog):
    with caplog.at_level(logging.WARNING, logger="engine.orchestrator"):
        _launch(session_factory, STEPS, [])
    msgs = [r.getMessage() for r in caplog.records]
    assert "TOOL_PRESENCE_UNCHECKED adapter=TOOL-NOBIN step=step-03" in msgs
    assert sum("TOOL_PRESENCE_UNCHECKED" in m for m in msgs) == 1


def test_egress_warning_reaches_run_output(session_factory, wired):
    _orch, _res, run = _launch(
        session_factory, STEPS, [{"name": "nmap", "adapter_ref": "TOOL-NMAP"}])
    assert "!! TARGET_EGRESS_REQUIRED: TOOL-NMAP — " in run.output
    assert "the target must already carry nmap on PATH" in run.output
