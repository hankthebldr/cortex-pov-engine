"""``GET /api/results/{run_id}`` joins the first observation onto each row."""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from sqlalchemy import select

from tests.api.test_connectors_api import _seed_run_with_results


@pytest.fixture
def client(make_client):
    from api.results import router
    return make_client(router)


def _result_ids(SessionLocal, run_id):
    from models import Result

    async def _go():
        async with SessionLocal() as s:
            rows = (await s.execute(
                select(Result).where(Result.run_id == run_id).order_by(Result.id)
            )).scalars().all()
            return [r.id for r in rows]
    return asyncio.run(_go())


def _add_observations(SessionLocal, run_id, specs):
    """specs: [(result_id | None, external_id, alert_source)]"""
    from models import Observation

    async def _go():
        async with SessionLocal() as s:
            for rid, ext, src in specs:
                s.add(Observation(run_id=run_id, result_id=rid, source="xsiam",
                                  external_id=ext, alert_source=src,
                                  observed_at=datetime(2026, 6, 10, 12, 0, 30)))
            await s.commit()
    asyncio.run(_go())


def _rows(client, run_id):
    r = client.get(f"/api/results/{run_id}")
    assert r.status_code == 200, r.text
    return {row["id"]: row for row in r.json()["results"]}


def test_result_with_observations_carries_first_alert_id_and_source(client, session_factory):
    run_id = _seed_run_with_results(session_factory, run_id="run-join-1")
    rid, _ = _result_ids(session_factory, run_id)
    _add_observations(session_factory, run_id, [
        (rid, "A-1", "XDR Analytics BIOC"),
        (rid, "A-2", "PAN NGFW"),
    ])
    row = _rows(client, run_id)[rid]
    assert row["alert_id"] == "A-1"
    assert row["alert_source"] == "XDR Analytics BIOC"
    assert row["observation_count"] == 2


def test_result_without_observation_is_null_alert_and_zero_count(client, session_factory):
    run_id = _seed_run_with_results(session_factory, run_id="run-join-2")
    rid, other = _result_ids(session_factory, run_id)
    _add_observations(session_factory, run_id, [(rid, "A-1", "PAN NGFW")])
    row = _rows(client, run_id)[other]
    assert row["alert_id"] is None
    assert row["alert_source"] is None
    assert row["observation_count"] == 0


def test_results_alert_id_matches_storyline(client, session_factory):
    from api.storyline import load_observations
    run_id = _seed_run_with_results(session_factory, run_id="run-join-3")
    rid, _ = _result_ids(session_factory, run_id)
    _add_observations(session_factory, run_id, [(rid, "A-9", "XDR Analytics BIOC")])

    async def _go():
        async with session_factory() as s:
            return await load_observations(s, run_id)
    expected = asyncio.run(_go())[rid]["alert_id"]
    assert _rows(client, run_id)[rid]["alert_id"] == expected == "A-9"


def test_observation_without_result_id_is_not_counted(client, session_factory):
    run_id = _seed_run_with_results(session_factory, run_id="run-join-4")
    rid, other = _result_ids(session_factory, run_id)
    _add_observations(session_factory, run_id, [(None, "A-orphan", "PAN NGFW")])
    rows = _rows(client, run_id)
    for r in (rid, other):
        assert rows[r]["observation_count"] == 0
        assert rows[r]["alert_id"] is None
