"""A pull run must dispatch the command the TARGET'S operating system can run.

``orchestrator._handle_pull`` shipped ``scenario.steps`` verbatim and never read
``Agent.os``. A Windows beacon handed a POSIX primary command runs it through
PowerShell (the beacon-windows-exec lane's shell selection), which exits 0 on a
string it does not understand — so the step "succeeds", nothing the scenario
expects fires, and every seeded expectation reads ``missed``. That is a false
negative about the customer's Windows coverage, manufactured by us.

The push path already solved this per TARGET (``push_generator.resolve_target``)
and refuses with ``BUNDLE_TARGET_UNSATISFIABLE`` when the content cannot satisfy
the target. The pull path now uses the same resolver:

* a satisfiable scenario dispatches the RESOLVED command text per step, plus
  ``shell`` (``powershell``/``cmd`` for windows, ``""`` for posix) and
  ``resolved_from`` (``primary`` / ``neutral`` / ``variant:<os>``);
* an unsatisfiable one is refused ``409 PULL_TARGET_UNSATISFIABLE`` BEFORE any
  Run or Result row exists, naming the unresolved steps;
* an unenrolled target is refused ``409 TARGET_AGENT_NOT_ENROLLED`` before
  seeding (it used to seed a run and queue a task nothing would ever collect);
* every step that ran a platform variant carries an explicit marker, because its
  ``expected_detections`` were authored against the primary command.

Content is the real corpus (SIM-EDR-001/003/006, SIM-MP-030), loaded through
the production schema validator. Nothing here proves a Windows host executes
the variant — that needs the windows CI job and, ultimately, a real endpoint.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

REPO = Path(__file__).resolve().parents[2]
CORPUS = {
    "SIM-EDR-001": REPO / "scenarios/edr/edr-001-credential-dumping.yml",
    "SIM-EDR-003": REPO / "scenarios/edr/edr-003-persistence-mechanisms.yml",
    "SIM-EDR-006": REPO / "scenarios/edr/edr-006-lsass-memory-dump.yml",
    "SIM-MP-030": REPO / "scenarios/multi_plane/mp-030-jumpy-pisces-play-ransomware-handoff.yml",
    "SIM-KOI-009": REPO / "scenarios/koi/sim-koi-009-glassworm-agentic-endpoint-takeover.yml",
}
#: The corpus wires TOOL-ATOMIC-RED-TEAM (dual-use-lab-only). Whether the consent
#: gate fires depends on whether an earlier test loaded the adapter catalog, so
#: consent is always given: the gate under test here is the platform gate.
CONSENT = {"simulation_authorized": True, "c2_authorized": True}


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


@pytest.fixture(autouse=True)
def _no_shelf(monkeypatch):
    """The payload shelf is a separate gate with its own tests; keep it out of
    the way so a refusal here can only be the platform gate's."""
    from engine import orchestrator as orch

    monkeypatch.setattr(orch, "_compose_artifacts", lambda _s: [])
    monkeypatch.setattr(orch, "_egress_warnings", lambda _s: [])


async def _load(db, scenario_id: str):
    from engine.scenario_loader import _parse_and_validate, _schema_to_orm_kwargs
    from models import Scenario

    existing = (await db.execute(
        select(Scenario).where(Scenario.scenario_id == scenario_id))).scalar_one_or_none()
    if existing is not None:
        return existing
    schema, err = _parse_and_validate(str(CORPUS[scenario_id]))
    assert err is None, err
    row = Scenario(**_schema_to_orm_kwargs(schema))
    db.add(row)
    await db.commit()
    return row


async def _agent(db, agent_id: str, os_name, *, seen_ago: float = 0.0):
    from models import Agent

    now = datetime.utcnow()
    db.add(Agent(
        agent_id=agent_id, hostname=f"host-{agent_id}", os=os_name,
        capabilities=["shell", "powershell"] if os_name == "windows"
        else ["shell", "identity-harness"],
        interpreters=[], registered_at=now,
        last_seen=now - timedelta(seconds=seen_ago), status="online",
    ))
    await db.commit()


async def _counts(db) -> tuple[int, int, int]:
    from models import QueuedTask, Result, Run

    runs = (await db.execute(select(func.count(Run.id)))).scalar_one()
    results = (await db.execute(select(func.count(Result.id)))).scalar_one()
    tasks = (await db.execute(select(func.count(QueuedTask.id)))).scalar_one()
    return runs, results, tasks


def _launch(session_factory, scenario_id, agent_id, os_name, *, seen_ago=0.0,
            enroll=True):
    from engine.orchestrator import Orchestrator

    async def _go():
        orch = Orchestrator()
        async with session_factory() as db:
            await _load(db, scenario_id)
            if enroll:
                await _agent(db, agent_id, os_name, seen_ago=seen_ago)
            res = await orch.launch(scenario_id=scenario_id, mode="pull", db=db,
                                    target_agent_id=agent_id, consent=CONSENT)
            return res, orch, await _counts(db)

    return asyncio.run(_go())


def _scenario_steps(scenario_id):
    import yaml

    return {s["id"]: s for s in yaml.safe_load(CORPUS[scenario_id].read_text())["steps"]}


# ---------------------------------------------------------------------------
# (a) a "both" scenario dispatched to a Windows agent runs its Windows variants
# ---------------------------------------------------------------------------


def test_both_scenario_to_windows_agent_dispatches_windows_variants(session_factory):
    res, orch, (runs, results, tasks) = _launch(
        session_factory, "SIM-EDR-003", "win-1", "windows")
    assert res.success, (res.error_code, res.error, res.error_detail)
    assert runs == 1 and tasks == 1

    (task,) = orch.peek_queue("win-1")
    authored = _scenario_steps("SIM-EDR-003")
    assert [s["id"] for s in task.steps] == list(authored)
    for step in task.steps:
        variant = authored[step["id"]]["platform_variants"]["windows"]
        # The Windows text, never the POSIX primary a PowerShell would swallow.
        assert step["command"] == variant, step["id"]
        assert step["command"] != authored[step["id"]]["command"]
        assert step["resolved_from"] == "variant:windows"
        assert step["shell"] in ("cmd", "powershell"), step
        # The authored platform label travels unchanged.
        assert step["platforms"] == authored[step["id"]]["platforms"]
    # The dispatched shell is the push generator's own dialect reading of the
    # variant (the Windows content lane rewrote SIM-EDR-003's variants as
    # PowerShell blocks with an exit-code check, so no step is cmd any more).
    from engine.push_generator import infer_windows_shell

    shells = {s["id"]: s["shell"] for s in task.steps}
    for sid, shell in shells.items():
        assert shell == infer_windows_shell(authored[sid]["platform_variants"]["windows"]), sid
    assert shells["step-04"] == "powershell"

    # CONT-09: every variant step is flagged — in the response AND on the run.
    markers = [w for w in res.warnings if w.startswith("!! PLATFORM VARIANT:")]
    assert len(markers) == len(authored), res.warnings
    assert any("step=step-01 ran its windows variant" in m for m in markers)
    assert all("unverified for this OS" in m for m in markers)

    async def _output():
        from models import Run
        async with session_factory() as db:
            return (await db.execute(select(Run.output))).scalar_one()

    output = asyncio.run(_output())
    assert output.count("!! PLATFORM VARIANT: step=") == len(authored)


def test_posix_agent_keeps_the_primary_command(session_factory):
    """The posix target dispatches the primary text untouched, shell ''."""
    res, orch, _ = _launch(session_factory, "SIM-EDR-003", "lin-1", "linux")
    assert res.success, res.error
    (task,) = orch.peek_queue("lin-1")
    authored = _scenario_steps("SIM-EDR-003")
    for step in task.steps:
        assert step["command"] == authored[step["id"]]["command"]
        assert step["shell"] == "" and step["resolved_from"] == "primary"
    assert not [w for w in res.warnings if "PLATFORM VARIANT" in w]


# ---------------------------------------------------------------------------
# (b) a POSIX-only scenario to a Windows agent: 409, nothing seeded
# ---------------------------------------------------------------------------


def test_posix_only_scenario_to_windows_agent_is_refused_before_seeding(session_factory):
    res, orch, (runs, results, tasks) = _launch(
        session_factory, "SIM-EDR-001", "win-1", "windows")
    assert res.success is False
    assert res.error_code == "PULL_TARGET_UNSATISFIABLE"
    assert res.http_status == 409
    assert res.run_id is None
    assert (runs, results, tasks) == (0, 0, 0)
    assert orch.peek_queue("win-1") == []
    unresolved = {u["step_id"]: u["reason"] for u in res.error_detail["unresolved"]}
    assert unresolved == {f"step-0{i}": "WINDOWS_COMMAND_UNAVAILABLE" for i in range(1, 6)}
    assert res.error_detail["target"] == "windows"
    assert res.error_detail["agent_id"] == "win-1"


# ---------------------------------------------------------------------------
# (c) a Windows-only scenario to a Linux agent: 409
# ---------------------------------------------------------------------------


def test_windows_only_scenario_to_linux_agent_is_refused(session_factory):
    res, _, (runs, results, tasks) = _launch(
        session_factory, "SIM-EDR-006", "lin-1", "linux")
    assert res.success is False
    assert res.error_code == "PULL_TARGET_UNSATISFIABLE"
    assert res.http_status == 409
    assert (runs, results, tasks) == (0, 0, 0)
    assert {u["reason"] for u in res.error_detail["unresolved"]} == {"POSIX_COMMAND_UNAVAILABLE"}


def test_mixed_single_target_scenario_is_unsatisfiable_on_either_os(session_factory):
    """CONT-08: SIM-MP-030 needs a Windows AND a Linux host but declares one
    target. Neither OS can run every step, so neither launch is dispatched."""
    for agent_id, os_name, reason in (("win-1", "windows", "WINDOWS_COMMAND_UNAVAILABLE"),
                                      ("lin-1", "linux", "POSIX_COMMAND_UNAVAILABLE")):
        res, _, counts = _launch(session_factory, "SIM-MP-030", agent_id, os_name)
        assert res.success is False, agent_id
        assert res.error_code == "PULL_TARGET_UNSATISFIABLE"
        assert counts == (0, 0, 0)
        assert {u["reason"] for u in res.error_detail["unresolved"]} == {reason}


# ---------------------------------------------------------------------------
# (d) an unknown target: 409, nothing seeded (OPS-08)
# ---------------------------------------------------------------------------


def test_unenrolled_target_is_refused_before_seeding(session_factory):
    res, orch, (runs, results, tasks) = _launch(
        session_factory, "SIM-EDR-003", "ghost-1", "linux", enroll=False)
    assert res.success is False
    assert res.error_code == "TARGET_AGENT_NOT_ENROLLED"
    assert res.http_status == 409
    assert (runs, results, tasks) == (0, 0, 0)
    assert orch.peek_queue("ghost-1") == []


def test_unenrolled_target_is_a_409_through_the_api(make_client_engine):
    client, session_factory = make_client_engine

    async def _seed():
        async with session_factory() as db:
            await _load(db, "SIM-EDR-003")

    asyncio.run(_seed())
    r = client.post("/api/runs", json={"scenario_id": "SIM-EDR-003", "mode": "pull",
                                       "target_agent_id": "ghost-1", "consent": CONSENT})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "TARGET_AGENT_NOT_ENROLLED"


def test_unsatisfiable_target_is_a_409_through_the_api(make_client_engine):
    client, session_factory = make_client_engine

    async def _seed():
        async with session_factory() as db:
            await _load(db, "SIM-EDR-001")
            await _agent(db, "win-1", "windows")

    asyncio.run(_seed())
    r = client.post("/api/runs", json={"scenario_id": "SIM-EDR-001", "mode": "pull",
                                       "target_agent_id": "win-1", "consent": CONSENT})
    assert r.status_code == 409, r.text
    body = r.json()["detail"]
    assert body["code"] == "PULL_TARGET_UNSATISFIABLE"
    assert "step-01" in str(body["detail"])


# ---------------------------------------------------------------------------
# Warnings that do not refuse
# ---------------------------------------------------------------------------


def test_offline_target_warns_but_still_queues(session_factory):
    res, orch, (runs, _, tasks) = _launch(
        session_factory, "SIM-EDR-003", "lin-1", "linux", seen_ago=3600)
    assert res.success, res.error
    assert runs == 1 and tasks == 1
    assert any(w.startswith("TARGET_AGENT_OFFLINE") for w in res.warnings), res.warnings
    # The response spelling is unchanged (no `!!`): the console parses both.
    assert sum(w.startswith("TARGET_AGENT_OFFLINE") for w in res.warnings) == 1, res.warnings

    # RG-RUNS-06: the reservation is ON the run too, so another browser and the
    # exported report carry it, not only the tab that launched.
    async def _output():
        from models import Run
        async with session_factory() as db:
            return (await db.execute(select(Run.output))).scalar_one() or ""

    output = asyncio.run(_output())
    assert output.count("!! TARGET_AGENT_OFFLINE: agent 'lin-1'") == 1, output


def test_unknown_os_is_treated_as_posix_with_a_warning(session_factory):
    res, orch, _ = _launch(session_factory, "SIM-EDR-003", "lin-1", "")
    assert res.success, res.error
    (task,) = orch.peek_queue("lin-1")
    assert all(s["resolved_from"] == "primary" for s in task.steps)
    assert any(w.startswith("!! TARGET_AGENT_OS_UNKNOWN") for w in res.warnings), res.warnings


def test_unrecognised_os_is_refused_rather_than_guessed(session_factory):
    res, _, counts = _launch(session_factory, "SIM-EDR-003", "odd-1", "plan9")
    assert res.success is False
    assert res.error_code == "PULL_TARGET_OS_UNRECOGNISED"
    assert res.http_status == 409
    assert counts == (0, 0, 0)


# ---------------------------------------------------------------------------
# The composed (multi-endpoint) path resolves per target agent
# ---------------------------------------------------------------------------


def _step(step_id, command, *, target=None, variants=None, platforms=None):
    step = {"id": step_id, "name": step_id, "identity": "root", "command": command,
            "mitre_technique": "T1059",
            "expected_detections": [{"type": "BIOC", "description": step_id}]}
    if target:
        step["target"] = target
    if variants:
        step["platform_variants"] = variants
    if platforms:
        step["platforms"] = platforms
    return step


async def _synthetic(db, steps, scenario_id="SIM-EDR-900"):
    from models import Scenario

    db.add(Scenario(
        scenario_id=scenario_id, name="T", version="1.0", status="active", plane="EDR",
        uc_ref="UCS-EDR-01", uc_name="x", tc_ref="TC-EDR-01", tc_name="y",
        mitre_tactic="TA0002", mitre_tactic_name="Execution",
        mitre_technique="T1059", mitre_technique_name="Command and Scripting Interpreter",
        execution_identity={"default": "root"}, push_supported=True, pull_supported=True,
        steps=steps,
    ))
    await db.commit()


def test_composed_run_resolves_each_partition_for_its_own_agent(session_factory):
    from engine.orchestrator import Orchestrator

    async def _go():
        orch = Orchestrator()
        async with session_factory() as db:
            await _agent(db, "lin-1", "linux")
            await _agent(db, "win-2", "windows")
            await _synthetic(db, [
                _step("step-01", "id -un"),
                _step("step-02", "uname -a", target="win-2",
                      variants={"windows": "Get-ComputerInfo | Select-Object OsName"},
                      platforms=["linux", "windows"]),
            ])
            res = await orch.launch(scenario_id="SIM-EDR-900", mode="pull", db=db,
                                    target_agent_id="lin-1")
            return res, orch

    res, orch = asyncio.run(_go())
    assert res.success, res.error
    (lin,) = orch.peek_queue("lin-1")
    (win,) = orch.peek_queue("win-2")
    assert lin.steps[0]["command"] == "id -un" and lin.steps[0]["shell"] == ""
    assert win.steps[0]["command"] == "Get-ComputerInfo | Select-Object OsName"
    assert win.steps[0]["shell"] == "powershell"
    assert win.steps[0]["resolved_from"] == "variant:windows"
    assert any("step=step-02 ran its windows variant" in w for w in res.warnings)


def test_composed_run_refuses_a_partition_its_agent_cannot_run(session_factory):
    """A composed step aimed at a Windows endpoint with only a POSIX command is
    refused for the whole launch, and the seeded rows are stamped not_executed."""
    from engine.orchestrator import Orchestrator
    from models import QueuedTask, Result, Run

    async def _go():
        orch = Orchestrator()
        async with session_factory() as db:
            await _agent(db, "lin-1", "linux")
            await _agent(db, "win-2", "windows")
            await _synthetic(db, [
                _step("step-01", "id -un"),
                _step("step-02", "cat /etc/passwd | head -1", target="win-2"),
            ])
            res = await orch.launch(scenario_id="SIM-EDR-900", mode="pull", db=db,
                                    target_agent_id="lin-1")
            run = (await db.execute(select(Run))).scalar_one()
            rows = (await db.execute(select(Result))).scalars().all()
            tasks = (await db.execute(select(QueuedTask))).scalars().all()
            return res, orch, run, rows, tasks

    res, orch, run, rows, tasks = asyncio.run(_go())
    assert res.success is False
    assert res.error_code == "PULL_TARGET_UNSATISFIABLE"
    assert res.http_status == 409
    assert tasks == [] and orch.peek_queue("lin-1") == [] and orch.peek_queue("win-2") == []
    assert run.status == "failed" and run.completed_at is not None
    assert rows and {(r.outcome, r.outcome_reason) for r in rows} == {
        ("not_executed", "LAUNCH_REFUSED")}


# ---------------------------------------------------------------------------
# API client fixture (router-level app, real orchestrator)
# ---------------------------------------------------------------------------


@pytest.fixture
def make_client_engine(session_factory):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.runs import router as runs_router
    from database import get_db

    async def _get_db():
        async with session_factory() as s:
            yield s

    app = FastAPI()
    app.include_router(runs_router, prefix="/api")
    app.dependency_overrides[get_db] = _get_db
    return TestClient(app), session_factory


# ---------------------------------------------------------------------------
# The pull refusal's hint follows the reason, as the push one does
# ---------------------------------------------------------------------------
#
# Handed over by the push lane: _unsatisfiable_refusal always said "declare
# platform_variants.windows on those steps", which is wrong for a step that
# HAS a Windows variant the resolver refuses as MIXED_DIALECT or
# CMD_PERCENT_CONTEXT. BundleTargetUnsatisfiable.to_error() already gave the
# per-reason fix; both now read it from one place.

@pytest.mark.parametrize("reason, says, never", [
    ("MIXED_DIALECT", "ONE dialect", "declare platform_variants.windows"),
    ("CMD_PERCENT_CONTEXT", "ForEach-Object", "declare platform_variants.windows"),
])
def test_the_pull_refusal_hint_names_the_fix_for_its_reason(reason, says, never):
    from engine.orchestrator import _unsatisfiable_refusal

    res = _unsatisfiable_refusal(target="windows", agent_id="w1", agent_os="windows",
                                 unresolved=[("step-01", reason)])
    hint = res.error_detail["hint"]
    assert says in hint, hint
    assert never not in hint, hint
    assert "launch against a POSIX agent" in hint


def test_a_missing_variant_still_says_declare_one():
    from engine.orchestrator import _unsatisfiable_refusal
    from engine.push_generator import REASON_WINDOWS_UNAVAILABLE

    res = _unsatisfiable_refusal(target="windows", agent_id="w1", agent_os="windows",
                                 unresolved=[("step-01", REASON_WINDOWS_UNAVAILABLE)])
    assert "declare platform_variants.windows" in res.error_detail["hint"]


# ---------------------------------------------------------------------------
# Inside the POSIX family: linux versus macOS (RG-UI-01, server half)
# ---------------------------------------------------------------------------
#
# ``platform_target_for_os`` maps darwin to ``posix``, and the resolver knows
# only posix and windows. So a macOS beacon was sent the primary command of a
# Linux-only step (SIM-EDR-001: ``cat /etc/shadow``, ``runuser``) and a Linux
# beacon the macOS-only SIM-KOI-009, with nothing on the run to say so. The
# console's preflight blocked it; an API client got no word. The step labels are
# the only linux-versus-macOS statement there is, so the server now reads them
# the way the console does (``posixFamilyCheck``) and marks each such step.
# It does not refuse: what runs is unchanged, and labels are known to disagree
# with command text, so the marker is a caveat on the evidence, not a gate.

_MISMATCH = "!! PLATFORM MISMATCH:"


def _run_output(session_factory) -> str:
    async def _go():
        from models import Run
        async with session_factory() as db:
            return (await db.execute(select(Run.output))).scalar_one() or ""

    return asyncio.run(_go())


@pytest.mark.parametrize("os_name", ["darwin", "macos"])
def test_a_linux_only_scenario_to_a_macos_beacon_is_marked_on_the_run(session_factory, os_name):
    res, orch, (runs, _, tasks) = _launch(session_factory, "SIM-EDR-001", "mac-1", os_name)
    assert res.success, (res.error_code, res.error)
    assert runs == 1 and tasks == 1

    # What runs is unchanged: the POSIX primary, as before.
    (task,) = orch.peek_queue("mac-1")
    authored = _scenario_steps("SIM-EDR-001")
    assert all(s["command"] == authored[s["id"]]["command"] for s in task.steps)

    # Every linux-labelled step is marked; step-01 declares no platform and is not.
    marked = [w for w in res.warnings if w.startswith(_MISMATCH)]
    assert [m.split()[3] for m in marked] == [
        f"step=step-0{i}" for i in range(2, 6)], res.warnings
    for m in marked:
        assert "authored for linux" in m and os_name in m and "agent 'mac-1'" in m, m
        assert "unverified" in m, m
    output = _run_output(session_factory)
    assert output.count(_MISMATCH) == 4, output
    assert "step=step-01" not in output


def test_a_macos_only_scenario_to_a_linux_beacon_is_marked_on_the_run(session_factory):
    """SIM-KOI-009 shape: every step is ``platforms: [macos]``."""
    res, _, _ = _launch(session_factory, "SIM-KOI-009", "lin-1", "linux")
    assert res.success, (res.error_code, res.error)
    marked = [w for w in res.warnings if w.startswith(_MISMATCH)]
    assert len(marked) == len(_scenario_steps("SIM-KOI-009")) == 6, res.warnings
    assert all("authored for macos, not linux" in m for m in marked), marked
    assert _run_output(session_factory).count(_MISMATCH) == 6


def test_a_beacon_on_an_os_every_step_declares_gets_no_mismatch(session_factory):
    res, _, _ = _launch(session_factory, "SIM-EDR-001", "lin-1", "linux")
    assert res.success, res.error
    assert not [w for w in res.warnings if w.startswith(_MISMATCH)], res.warnings
    assert _MISMATCH not in _run_output(session_factory)


def test_a_darwin_beacon_matches_steps_labelled_macos(session_factory):
    res, _, _ = _launch(session_factory, "SIM-KOI-009", "mac-1", "darwin")
    assert res.success, res.error
    assert not [w for w in res.warnings if w.startswith(_MISMATCH)], res.warnings


def _launch_synthetic(session_factory, steps, agents, target):
    from engine.orchestrator import Orchestrator

    async def _go():
        orch = Orchestrator()
        async with session_factory() as db:
            for agent_id, os_name in agents:
                await _agent(db, agent_id, os_name)
            await _synthetic(db, steps)
            return await orch.launch(scenario_id="SIM-EDR-900", mode="pull", db=db,
                                     target_agent_id=target)

    return asyncio.run(_go())


def test_container_and_k8s_labels_are_satisfied_by_linux_only(session_factory):
    """The console's rule: a container/k8s step runs wherever the runtime is,
    which a Linux beacon satisfies and a macOS one does not."""
    steps = [_step("step-01", "id -un", platforms=["container"]),
             _step("step-02", "uname -a", platforms=["k8s", "container"])]
    res = _launch_synthetic(session_factory, steps, [("lin-1", "linux")], "lin-1")
    assert res.success, res.error
    assert not [w for w in res.warnings if w.startswith(_MISMATCH)], res.warnings


def test_container_labels_on_a_macos_beacon_are_marked(session_factory):
    steps = [_step("step-01", "id -un", platforms=["container"])]
    res = _launch_synthetic(session_factory, steps, [("mac-1", "darwin")], "mac-1")
    assert res.success, res.error
    assert [w for w in res.warnings if w.startswith(_MISMATCH)] == [
        "!! PLATFORM MISMATCH: step=step-01 is authored for container, not darwin; "
        "SimCore dispatched it unchanged to agent 'mac-1' (darwin), so its "
        "expected_detections are unverified on this OS"], res.warnings


@pytest.mark.parametrize("os_name", ["linux", "darwin"])
def test_a_windows_only_label_says_nothing_about_linux_versus_macos(session_factory, os_name):
    """SIM-EDR-014 shape: POSIX text under ``platforms: [windows]``. That label
    disagreement is not an intra-POSIX statement, so it raises no mismatch."""
    steps = [_step("step-01", "id -un", platforms=["windows"])]
    res = _launch_synthetic(session_factory, steps, [("a-1", os_name)], "a-1")
    assert res.success, res.error
    assert not [w for w in res.warnings if w.startswith(_MISMATCH)], res.warnings


def test_the_composed_path_marks_each_partition_for_its_own_agent(session_factory):
    steps = [_step("step-01", "id -un", platforms=["linux"]),
             _step("step-02", "uname -a", target="mac-2", platforms=["linux"])]
    res = _launch_synthetic(session_factory, steps,
                            [("lin-1", "linux"), ("mac-2", "darwin")], "lin-1")
    assert res.success, res.error
    marked = [w for w in res.warnings if w.startswith(_MISMATCH)]
    assert len(marked) == 1, res.warnings
    assert "step=step-02" in marked[0] and "agent 'mac-2' (darwin)" in marked[0]
    assert _run_output(session_factory).count(_MISMATCH) == 1
