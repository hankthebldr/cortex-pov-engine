"""POST /api/runs carries the operator-declared launch context onto the Run."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select


@pytest.fixture
def client(make_client):
    from api.runs import router
    return make_client(router)


def _seed(session_factory, first_status="complete", completed=True):
    from models import Run, Scenario

    async def go():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id="SIM-T-001", name="t", plane="EDR", version="1.0",
                status="active", uc_ref="UCS-EDR-01", uc_name="u", tc_ref="TC-EDR-01",
                tc_name="t", mitre_tactic="TA0006", mitre_tactic_name="c",
                mitre_technique="T1003", mitre_technique_name="d",
                steps=[{"id": "step-01", "name": "s", "expected_detections": []}],
            ))
            db.add(Run(
                run_id="first", scenario_id="SIM-T-001", mode="pull", target="agent-1",
                status=first_status, agent_profile_mode="report",
                started_at=datetime.utcnow() - timedelta(minutes=5),
                completed_at=datetime.utcnow() if completed else None,
            ))
            await db.commit()
    asyncio.run(go())


def _runs(session_factory):
    from models import Run

    async def go():
        async with session_factory() as db:
            return (await db.execute(select(Run))).scalars().all()
    return asyncio.run(go())


def _body(**kw):
    b = {"scenario_id": "SIM-T-001", "mode": "push"}
    b.update(kw)
    return b


def test_launch_persists_attestations(client, session_factory):
    _seed(session_factory)
    r = client.post("/api/runs", json=_body(
        agent_profile_mode="block", bas_mode_attested=True, soakless_attested=True,
        attested_by="henry", canary_run_id="canary-9"))
    assert r.status_code == 200, r.text
    run = next(x for x in _runs(session_factory) if x.run_id == r.json()["run_id"])
    assert run.agent_profile_mode == "block"
    assert run.bas_mode_attested is True and run.soakless_attested is True
    assert run.attested_by == "henry" and run.attested_at is not None
    assert run.canary_run_id == "canary-9"
    assert run.block_mode_status is None


def test_default_launch_has_no_attestation(client, session_factory):
    _seed(session_factory)
    r = client.post("/api/runs", json=_body())
    assert r.status_code == 200, r.text
    run = next(x for x in _runs(session_factory) if x.run_id == r.json()["run_id"])
    assert run.agent_profile_mode == "unknown"
    assert run.attested_at is None and run.paired_with_run_id is None


def test_launch_rejects_unknown_profile_mode(client, session_factory):
    _seed(session_factory)
    r = client.post("/api/runs", json=_body(agent_profile_mode="allow"))
    assert r.status_code == 422
    assert len(_runs(session_factory)) == 1


def test_launch_refuses_pair_with_non_terminal_first_run(client, session_factory):
    _seed(session_factory, first_status="running", completed=False)
    r = client.post("/api/runs", json=_body(
        mode="pull", target_agent_id="agent-1", agent_profile_mode="block",
        paired_with_run_id="first"))
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "PAIR_INVALID"
    assert "terminal" in r.json()["detail"]["detail"]
    assert len(_runs(session_factory)) == 1


def test_launch_refuses_pair_with_unknown_first_run(client, session_factory):
    _seed(session_factory)
    r = client.post("/api/runs", json=_body(
        agent_profile_mode="block", paired_with_run_id="nope"))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "PAIR_INVALID"
    assert len(_runs(session_factory)) == 1


def test_seed_results_copies_expected_outcome(client, session_factory):
    from models import Result, Scenario

    async def seed():
        async with session_factory() as db:
            sc = (await db.execute(select(Scenario))).scalar_one()
            sc.steps = [{"id": "step-01", "name": "s", "expected_detections": [
                {"type": "BIOC", "description": "a", "outcome": "prevent"},
                {"type": "BIOC", "description": "b"},
            ]}]
            await db.commit()
    _seed(session_factory)
    asyncio.run(seed())
    r = client.post("/api/runs", json=_body())
    assert r.status_code == 200, r.text

    async def rows():
        async with session_factory() as db:
            return (await db.execute(
                select(Result).where(Result.run_id == r.json()["run_id"]).order_by(Result.id)
            )).scalars().all()
    got = asyncio.run(rows())
    assert [x.expected_outcome for x in got] == ["prevent", "detect"]
    assert all(x.prevention_module is None for x in got)


def _launch_block(client, **kw):
    body = _body(mode="pull", target_agent_id="agent-1", agent_profile_mode="block",
                 paired_with_run_id="first")
    body.update(kw)
    return client.post("/api/runs", json=body)


def test_launch_valid_pair_persists_link_and_pair_endpoint_resolves(client, session_factory):
    _seed(session_factory)
    r = _launch_block(client)
    assert r.status_code == 200, r.text
    block_id = r.json()["run_id"]
    run = next(x for x in _runs(session_factory) if x.run_id == block_id)
    assert run.paired_with_run_id == "first"
    p = client.get(f"/api/runs/{block_id}/pair")
    assert p.status_code == 200, p.text
    assert {x["run_id"] for x in p.json()["runs"]} == {"first", block_id}


def test_launch_refuses_pair_on_different_target(client, session_factory):
    _seed(session_factory)
    r = _launch_block(client, target_agent_id="agent-2")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "PAIR_INVALID"
    assert "same target" in r.json()["detail"]["detail"]
    assert len(_runs(session_factory)) == 1


def test_launch_refuses_pair_with_same_profile_mode(client, session_factory):
    _seed(session_factory)
    r = _launch_block(client, agent_profile_mode="report")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "PAIR_INVALID"
    assert "different" in r.json()["detail"]["detail"]
    assert len(_runs(session_factory)) == 1


def test_launch_refuses_second_pairing_of_same_first_run(client, session_factory):
    _seed(session_factory)
    r1 = _launch_block(client)
    assert r1.status_code == 200, r1.text
    r2 = _launch_block(client)
    assert r2.status_code == 409 and r2.json()["detail"]["code"] == "PAIR_INVALID"
    assert f"first run first is already paired with {r1.json()['run_id']}" in r2.json()["detail"]["detail"]
    assert len(_runs(session_factory)) == 2


def test_seed_results_sets_prevention_module_from_card(client, session_factory):
    import os
    from pathlib import Path
    from engine.ttp_catalog import catalog, default_corpus_dir
    from models import Result, Scenario

    _seed(session_factory)
    fixtures = Path(__file__).resolve().parent.parent / "fixtures" / "cards"

    async def seed():
        async with session_factory() as db:
            sc = (await db.execute(select(Scenario))).scalar_one()
            sc.steps = [{"id": "step-01", "name": "s", "expected_detections": [{
                "plane": "EDR", "type": "Prevention", "description": "d",
                "outcome": "prevent", "ttp_ref": "TTP-2026-9901",
                "detection_id": "prevention-btp-reverse-shell-from-web-server"}]}]
            await db.commit()
    asyncio.run(seed())

    catalog.load(str(fixtures))
    try:
        r = client.post("/api/runs", json=_body())
    finally:
        catalog.load(default_corpus_dir(os.environ["CORTEXSIM_BASE_DIR"]))
    assert r.status_code == 200, r.text

    async def rows():
        async with session_factory() as db:
            return (await db.execute(
                select(Result).where(Result.run_id == r.json()["run_id"])
            )).scalars().all()
    got = asyncio.run(rows())
    assert len(got) == 1
    assert got[0].expected_outcome == "prevent"
    assert got[0].prevention_module == "btp"
