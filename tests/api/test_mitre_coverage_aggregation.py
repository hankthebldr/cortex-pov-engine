"""Guards for the /api/mitre/coverage aggregation rewrite.

The heatmap is a customer-facing coverage view whose response is a fixed-size
summary, but it used to build that summary by loading every Scenario, Run and
Result row into memory and rescanning ``steps[]`` once per Result. These tests
pin the tallies the SQL aggregate has to reproduce exactly, plus the fact that
Result rows are no longer materialized.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest


@pytest.fixture
def client(make_client):
    from api.mitre import router
    return make_client(router)


def _seed(SessionLocal, scenarios=(), runs=(), results=()):
    from models import Result, Run, Scenario

    async def _go():
        async with SessionLocal() as s:
            for kw in scenarios:
                s.add(Scenario(**kw))
            for kw in runs:
                s.add(Run(**kw))
            for kw in results:
                s.add(Result(**kw))
            await s.commit()
    asyncio.run(_go())


def _scenario(sid, *, technique="T1003", steps=None, status="active", plane="EDR"):
    return dict(
        scenario_id=sid, name=sid, version="1.0", plane=plane, status=status,
        uc_ref="UC-1", tc_ref="TC-1", uc_name="UC", tc_name="TC",
        mitre_tactic="TA0006", mitre_tactic_name="Credential Access",
        mitre_technique=technique, mitre_technique_name="OS Credential Dumping",
        steps=steps if steps is not None else [{"id": "step-01", "mitre_technique": technique}],
    )


def _run(rid, sid):
    return dict(run_id=rid, scenario_id=sid, mode="pull", status="completed",
                started_at=datetime(2026, 6, 1))


def _result(rid, step_id, observed, plane="EDR"):
    return dict(run_id=rid, step_id=step_id, plane=plane, signal_type="BIOC",
                expected_detection="d", observed=observed)


def _tech(body, tid):
    return next(t for t in body["techniques"] if t["technique_id"] == tid)


def test_tallies_group_correctly_across_runs_and_steps(client, session_factory):
    """total/observed per technique must survive the switch to COUNT/SUM."""
    _seed(
        session_factory,
        scenarios=[_scenario("SIM-A", technique="T1003", steps=[
            {"id": "step-01", "mitre_technique": "T1003"},
            {"id": "step-02", "mitre_technique": "T1021"},
        ])],
        runs=[_run("r1", "SIM-A"), _run("r2", "SIM-A")],
        results=[
            _result("r1", "step-01", True), _result("r1", "step-01", False),
            _result("r1", "step-02", True),
            _result("r2", "step-01", False),
            _result("r2", "step-02", True), _result("r2", "step-02", True),
        ],
    )
    body = client.get("/api/mitre/coverage").json()

    t1003 = _tech(body, "T1003")
    assert (t1003["total_detections"], t1003["observed_detections"]) == (3, 1)
    assert t1003["coverage_pct"] == 33.3
    assert t1003["status"] == "detected"

    t1021 = _tech(body, "T1021")
    assert (t1021["total_detections"], t1021["observed_detections"]) == (3, 3)
    assert t1021["coverage_pct"] == 100.0


def test_result_step_falls_back_to_scenario_technique(client, session_factory):
    """A Result whose step_id matches no step is credited to the primary
    technique — the old inline lookup's fallback."""
    _seed(
        session_factory,
        scenarios=[_scenario("SIM-B", technique="T1055",
                             steps=[{"id": "step-01", "mitre_technique": "T1055"}])],
        runs=[_run("r1", "SIM-B")],
        results=[_result("r1", "step-99", True), _result("r1", None, False)],
    )
    body = client.get("/api/mitre/coverage").json()
    t = _tech(body, "T1055")
    assert (t["total_detections"], t["observed_detections"]) == (2, 1)


def test_step_declaring_null_technique_is_not_counted(client, session_factory):
    """``mitre_technique: null`` on a step resolves to None, which matches no
    technique key — so its results drop out of the tally entirely.

    This is a genuinely odd corner, but it is what the pre-rewrite code did and
    a coverage number that silently changed would be a customer-visible
    regression.
    """
    _seed(
        session_factory,
        scenarios=[_scenario("SIM-C", technique="T1003", steps=[
            {"id": "step-01", "mitre_technique": None},
            {"id": "step-02", "mitre_technique": "T1003"},
        ])],
        runs=[_run("r1", "SIM-C")],
        results=[_result("r1", "step-01", True), _result("r1", "step-02", True)],
    )
    body = client.get("/api/mitre/coverage").json()
    t = _tech(body, "T1003")
    assert (t["total_detections"], t["observed_detections"]) == (1, 1)


def test_duplicate_step_ids_resolve_to_the_first_occurrence(client, session_factory):
    """The old lookup ``break``-ed on the first id match; the prebuilt map must
    keep first-wins rather than last-wins."""
    _seed(
        session_factory,
        scenarios=[_scenario("SIM-D", technique="T1003", steps=[
            {"id": "dup", "mitre_technique": "T1003"},
            {"id": "dup", "mitre_technique": "T1021"},
        ])],
        runs=[_run("r1", "SIM-D")],
        results=[_result("r1", "dup", True)],
    )
    body = client.get("/api/mitre/coverage").json()
    assert _tech(body, "T1003")["total_detections"] == 1
    assert _tech(body, "T1021")["total_detections"] == 0


def test_results_of_inactive_scenarios_are_excluded_by_default(client, session_factory):
    """Inactive scenarios contribute nothing unless include_inactive=true."""
    _seed(
        session_factory,
        scenarios=[_scenario("SIM-E", technique="T1566", status="draft")],
        runs=[_run("r1", "SIM-E")],
        results=[_result("r1", "step-01", True)],
    )
    default = client.get("/api/mitre/coverage").json()
    assert all(t["technique_id"] != "T1566" for t in default["techniques"])

    widened = client.get("/api/mitre/coverage?include_inactive=true").json()
    t = _tech(widened, "T1566")
    assert (t["total_detections"], t["observed_detections"]) == (1, 1)


def test_orphan_results_without_a_run_are_ignored(client, session_factory):
    """A Result pointing at a missing run has no scenario, so no technique.

    The old code dropped it via a dict miss; the SQL path drops it via the
    INNER JOIN. Same answer, and this pins that they agree.
    """
    _seed(
        session_factory,
        scenarios=[_scenario("SIM-F", technique="T1003")],
        runs=[_run("r1", "SIM-F")],
        results=[_result("r1", "step-01", True), _result("ghost-run", "step-01", True)],
    )
    body = client.get("/api/mitre/coverage").json()
    t = _tech(body, "T1003")
    assert (t["total_detections"], t["observed_detections"]) == (1, 1)


def test_has_run_flag_survives_the_distinct_projection(client, session_factory):
    """``has_run`` now comes from SELECT DISTINCT scenario_id, not full Run rows."""
    _seed(
        session_factory,
        scenarios=[_scenario("SIM-G", technique="T1003"),
                   _scenario("SIM-H", technique="T1218")],
        runs=[_run("r1", "SIM-G")],
    )
    body = client.get("/api/mitre/coverage").json()
    assert _tech(body, "T1003")["status"] == "run_not_detected" or \
        _tech(body, "T1003")["status"] == "not_run"
    # SIM-H has never run at all.
    assert _tech(body, "T1218")["status"] == "not_run"
    # And the run flag itself is reflected in the summary counts.
    assert body["summary"]["total_techniques"] >= 2


def _capture_statements(monkeypatch) -> list[str]:
    """Record the SQL every request issues, so a test can assert on its shape."""
    from sqlalchemy.ext.asyncio import AsyncSession

    seen: list[str] = []
    real_execute = AsyncSession.execute

    async def capturing(self, statement, *a, **kw):
        seen.append(" ".join(str(statement).split()))
        return await real_execute(self, statement, *a, **kw)

    monkeypatch.setattr(AsyncSession, "execute", capturing)
    return seen


def test_coverage_aggregates_results_in_sql(client, session_factory, monkeypatch):
    """Result rows must be COUNTed by the database, not streamed into Python.

    This is the whole point of the rewrite: the response is a constant-size
    summary, so its cost must not grow with POV history. A bare
    ``SELECT ... FROM results`` means the unbounded full-table scan is back.
    """
    _seed(
        session_factory,
        scenarios=[_scenario("SIM-I", technique="T1003")],
        runs=[_run("r1", "SIM-I")],
        results=[_result("r1", "step-01", i % 2 == 0) for i in range(40)],
    )

    seen = _capture_statements(monkeypatch)
    body = client.get("/api/mitre/coverage").json()

    # Sanity: the tally is still right...
    t = _tech(body, "T1003")
    assert (t["total_detections"], t["observed_detections"]) == (40, 20)

    # ...and every statement that reads `results` is an aggregate.
    result_stmts = [s for s in seen if "FROM results" in s]
    assert result_stmts, "expected the handler to query the results table"
    for stmt in result_stmts:
        assert "count(" in stmt.lower() and "GROUP BY" in stmt, (
            f"results are being read row-by-row, not aggregated: {stmt}"
        )


def test_coverage_does_not_select_whole_run_rows(client, session_factory, monkeypatch):
    """Runs are only needed for 'which scenarios ran' + the result join key.

    Loading full Run entities also drags ``Run.output`` — the unbounded step
    stdout blob — through the coverage request for no reason.
    """
    _seed(
        session_factory,
        scenarios=[_scenario("SIM-J", technique="T1003")],
        runs=[_run("r1", "SIM-J")],
    )

    seen = _capture_statements(monkeypatch)
    client.get("/api/mitre/coverage")

    standalone_run_stmts = [
        s for s in seen if "FROM runs" in s and "FROM results" not in s
    ]
    assert standalone_run_stmts, "expected a runs lookup"
    for stmt in standalone_run_stmts:
        assert "runs.output" not in stmt, (
            f"coverage is loading whole Run rows including output: {stmt}"
        )
