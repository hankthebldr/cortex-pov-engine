"""A run whose beacon dies is terminalised honestly by the heartbeat sweep."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import select

MARKER = ("\n--- BEACON LOST — agent went offline mid-run; "
          "enable BAS mode so only the simulation is terminated ---\n")
NOW = datetime(2026, 10, 26, 12, 0, 0)
STEPS = ("step-01", "step-02", "step-03")


def _seed(SessionLocal, *, started_ago, delivered_ago=900, run_id="r1", agent="agent-1",
          mode="block", prevent_steps=(), steps=STEPS, with_scenario=True):
    from models import Result, Run, Scenario, StepOutcome

    async def _go():
        async with SessionLocal() as db:
            if with_scenario:
                db.add(Scenario(
                    scenario_id="SIM-EDR-001", name="X", plane="EDR", version="1.0",
                    status="active", uc_ref="UCS-EDR-01", uc_name="X", tc_ref="TC-EDR-01",
                    tc_name="Y", mitre_tactic="TA0006", mitre_tactic_name="CA",
                    mitre_technique="T1003.001", mitre_technique_name="LSASS",
                    steps=[{"id": s} for s in steps]))
            db.add(Run(run_id=run_id, scenario_id="SIM-EDR-001", mode="pull", target=agent,
                       status="running", open_tasks=1, agent_profile_mode=mode,
                       started_at=NOW - timedelta(seconds=delivered_ago),
                       delivered_at=NOW - timedelta(seconds=delivered_ago)))
            for sid in steps:
                db.add(Result(run_id=run_id, step_id=sid, plane="EDR", signal_type="Prevention",
                              expected_detection=f"det {sid}",
                              expected_outcome="prevent" if sid in prevent_steps else "detect",
                              executed_at=NOW - timedelta(seconds=delivered_ago)))
            if started_ago is not None:
                # step-01 ended (its /output landed); step-02 is in flight and,
                # having produced no /output yet, has no row.
                db.add(StepOutcome(run_id=run_id, step_id="step-01",
                                   started_at=NOW - timedelta(seconds=started_ago)))
            await db.commit()

    asyncio.run(_go())


def _terminalise(SessionLocal, agent="agent-1", after=180):
    from engine.beacon_lost import terminalise_lost_runs

    async def _go():
        async with SessionLocal() as db:
            return await terminalise_lost_runs(db, agent, NOW, after_seconds=after)

    return asyncio.run(_go())


def _load(SessionLocal, model, run_id="r1"):
    async def _go():
        async with SessionLocal() as db:
            q = select(model).where(model.run_id == run_id)
            return (await db.execute(q.order_by(model.id))).scalars().all()

    return asyncio.run(_go())


def test_offline_agent_terminalises_running_run_as_beacon_lost(session_factory):
    from models import Result, Run, StepOutcome

    _seed(session_factory, started_ago=600)
    assert _terminalise(session_factory) == ["r1"]

    run = _load(session_factory, Run)[0]
    assert run.status == "complete" and run.completed_at == NOW
    assert run.severed_at_step == "step-02"
    assert run.output.endswith(MARKER)
    so = {o.step_id: o for o in _load(session_factory, StepOutcome)}
    assert so["step-02"].termination == "beacon_lost"  # the in-flight step: no row until it ends
    assert so["step-01"].termination is None  # step-01 ended; its row is untouched
    assert so["step-03"].termination == "not_run" and so["step-03"].reason == "UPSTREAM_SEVERED"
    res = {r.step_id: r for r in _load(session_factory, Result)}
    assert res["step-03"].outcome == "not_reached"
    assert res["step-02"].outcome != "prevented"


def test_recently_reconnected_agent_is_left_alone(session_factory):
    from models import Run

    _seed(session_factory, started_ago=30)
    assert _terminalise(session_factory) == []
    run = _load(session_factory, Run)[0]
    assert run.status == "running" and run.severed_at_step is None and not run.output


def test_prevent_result_caps_at_pending_beacon_lost(session_factory):
    from connectors.matcher import MatchVerdict
    from connectors.service import apply_verdicts
    from models import Result

    _seed(session_factory, started_ago=600, prevent_steps=("step-02",))
    assert _terminalise(session_factory) == ["r1"]

    async def _go():
        async with session_factory() as db:
            results = (await db.execute(select(Result).where(Result.run_id == "r1"))).scalars().all()
            target = next(r for r in results if r.step_id == "step-02")
            v = MatchVerdict(result_id=target.id, matched=True, observed_at=NOW,
                             mttd_seconds=1.0, matched_on=["source"],
                             alert_external_id="A1", alert_name="n",
                             alert_action="Prevented (Blocked)", alert_outcome="prevented")
            await apply_verdicts(db, "r1", results, [v], "xsiam")
            await db.refresh(target)
            return target.kpi_verdict, target.outcome_reason, target.outcome

    kpi, reason, outcome = asyncio.run(_go())
    assert kpi == "pending" and reason == "PREVENTION_UNCONFIRMED_BEACON_LOST"
    assert outcome != "prevented"


def test_no_started_step_severs_at_first_step(session_factory):
    from models import Run, StepOutcome

    _seed(session_factory, started_ago=None, delivered_ago=900)
    assert _terminalise(session_factory) == ["r1"]
    assert _load(session_factory, Run)[0].severed_at_step == "step-01"
    so = {o.step_id: o.termination for o in _load(session_factory, StepOutcome)}
    assert so == {"step-01": "beacon_lost", "step-02": "not_run", "step-03": "not_run"}


def test_other_agents_and_undelivered_runs_untouched(session_factory):
    _seed(session_factory, started_ago=600, agent="other")
    assert _terminalise(session_factory, agent="agent-1") == []


# ---------------------------------------------------------------------------
# Fix round 1
# ---------------------------------------------------------------------------


async def _aset_run(SessionLocal, **vals):
    from models import Run
    from sqlalchemy import update

    async with SessionLocal() as db:
        await db.execute(update(Run).where(Run.run_id == "r1").values(**vals))
        await db.commit()


def _set_run(SessionLocal, **vals):
    asyncio.run(_aset_run(SessionLocal, **vals))


def test_already_complete_run_is_untouched(session_factory):
    from models import Run, StepOutcome

    _seed(session_factory, started_ago=600)
    _set_run(session_factory, status="complete", completed_at=NOW - timedelta(seconds=5),
             output="done")
    assert _terminalise(session_factory) == []
    run = _load(session_factory, Run)[0]
    assert run.output == "done" and run.severed_at_step is None
    assert [o.termination for o in _load(session_factory, StepOutcome)] == [None]


def test_run_completed_by_another_session_after_candidate_read_is_skipped(
        session_factory, monkeypatch):
    from engine import beacon_lost
    from models import Run, StepOutcome

    _seed(session_factory, started_ago=600)
    real = beacon_lost._candidates

    async def _stale(db, agent_id):
        runs = await real(db, agent_id)
        await _aset_run(session_factory, status="complete", completed_at=NOW, output="x")
        return runs

    monkeypatch.setattr(beacon_lost, "_candidates", _stale)

    async def _go():
        async with session_factory() as db:
            return await beacon_lost.terminalise_lost_runs(db, "agent-1", NOW, after_seconds=180)

    published = []
    import api.runs as runs_mod

    async def _pub(run_id, ev):
        published.append(ev)

    monkeypatch.setattr(runs_mod, "_safe_publish", _pub)
    assert asyncio.run(_go()) == []
    assert published == []
    run = _load(session_factory, Run)[0]
    assert run.output == "x" and run.severed_at_step is None
    assert [o.termination for o in _load(session_factory, StepOutcome)] == [None]


def _set_agent(SessionLocal, last_seen):
    from models import Agent

    async def _go():
        async with SessionLocal() as db:
            db.add(Agent(agent_id="agent-1", hostname="h", os="linux", capabilities=[],
                         status="offline", last_seen=last_seen))
            await db.commit()

    asyncio.run(_go())


def test_agent_that_polled_control_recently_is_not_lost(session_factory):
    from models import Run

    _seed(session_factory, started_ago=600)
    _set_agent(session_factory, NOW - timedelta(seconds=10))
    assert _terminalise(session_factory) == []
    assert _load(session_factory, Run)[0].status == "running"


def test_control_poll_refreshes_agent_last_seen(session_factory, make_client):
    from api.runs import router
    from models import Agent

    _seed(session_factory, started_ago=600)
    old = datetime.utcnow() - timedelta(hours=1)
    _set_agent(session_factory, old)
    client = make_client(router)
    r = client.get("/api/runs/r1/control")
    assert r.status_code == 200 and set(r.json()) == {"abort", "run_id", "status"}

    async def _go():
        async with session_factory() as db:
            return (await db.execute(select(Agent))).scalar_one()

    a = asyncio.run(_go())
    assert a.last_seen > old + timedelta(minutes=30) and a.status == "online"


def test_unclassifiable_run_is_failed_not_left_running(session_factory, caplog):
    from models import Run

    _seed(session_factory, started_ago=None, steps=(), with_scenario=False)
    with caplog.at_level("WARNING"):
        assert _terminalise(session_factory) == ["r1"]
    run = _load(session_factory, Run)[0]
    assert run.status == "failed" and run.completed_at == NOW
    assert run.output.endswith(
        "\n--- BEACON LOST — run could not be classified (no scenario steps, no results); "
        "agent went offline mid-run ---\n")
    assert any(r.levelname == "WARNING" for r in caplog.records)


def test_witnessed_severed_step_keeps_its_witness(session_factory):
    from models import Run, StepOutcome

    _seed(session_factory, started_ago=600)

    async def _go():
        async with session_factory() as db:
            db.add(StepOutcome(run_id="r1", step_id="step-02",
                               termination="anchor_terminated", recorded_at=NOW))
            await db.commit()

    asyncio.run(_go())
    assert _terminalise(session_factory) == ["r1"]
    assert _load(session_factory, Run)[0].severed_at_step == "step-02"
    so = {o.step_id: o for o in _load(session_factory, StepOutcome)}
    assert so["step-02"].termination == "anchor_terminated"
    assert so["step-03"].termination == "not_run"
