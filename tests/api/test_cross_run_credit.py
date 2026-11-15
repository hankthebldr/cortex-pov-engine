"""#347 through the real routes: two runs on one endpoint, one alert.

A row's window outlasts its run, and reconcile is scoped by host, so an alert
raised during the SECOND run on an endpoint sat inside the first run's window
too. Posted to both runs it credited both: one alert, two "detected".

Driven through ``POST /api/runs/{id}/observations`` (the manual path shares
the one-run rule with the connector pull), plus the column's migration.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tests.api import _reconcile_flow as flow

AGENT, HOST = "ag-347", "lab-endpoint-01"


@pytest.fixture
def client(make_client, session_factory):
    flow.seed_scenarios(session_factory)
    return flow.build_client(make_client)


@pytest.fixture(autouse=True)
def _clean():
    flow.reset_orchestrator()
    yield
    flow.reset_orchestrator()


def _executed_run(client) -> str:
    run_id = flow.launch(client, agent_id=AGENT)
    flow.collect_task(client, AGENT)
    done = client.post(f"/api/runs/{run_id}/complete", json={
        "exit_code": 0, "summary": "ok",
        "steps": [{"step_id": "step-01", "exit_code": 0, "termination": "exited"}]})
    assert done.status_code == 200, done.text
    return run_id


def _technique_only_alert(ext: str, *, plus: float = 5) -> dict:
    """An alert that shares the step's technique and nothing else: no rule
    name, no detection id, no words of the expected detection."""
    return {"external_id": ext, "name": "Unrelated analytics alert",
            "mitre_technique": "T1003", "host": HOST,
            "observed_at": (datetime.utcnow() + timedelta(seconds=plus)).isoformat() + "Z"}


def test_an_alert_raised_during_the_second_run_credits_only_the_second(client):
    flow.register(client, AGENT, HOST)
    first = _executed_run(client)
    second = _executed_run(client)
    alert = _technique_only_alert("al-347")

    to_first = flow.observe(client, first, [alert])
    to_second = flow.observe(client, second, [alert])

    assert to_first.status_code == 200, to_first.text
    summary = to_first.json()
    assert summary["observed"] == 0 and summary["verdicts"] == []
    assert [(e["alert_external_id"], e["reason"], e["run_id"])
            for e in summary["not_credited"]] == [("al-347", "LATER_RUN_STARTED", second)]
    assert flow.view(flow.rows(client, first)["step-01"])[0] is False

    assert to_second.json()["observed"] == 1, to_second.text
    assert flow.view(flow.rows(client, second)["step-01"])[:2] == (True, "detected")


def test_two_runs_expecting_one_detection_id_the_alert_goes_to_the_run_on_the_host(client):
    """A detection id ties an alert to a run past the cap. Both runs expect
    ``d-1`` here, so it ties the alert to both, and the run that was on the
    host when it fired takes it: one alert, one run."""
    flow.register(client, AGENT, HOST)
    first = _executed_run(client)
    second = _executed_run(client)
    alert = flow.alert("al-347-id", "Shadow file read", "T1003", "d-1", HOST)

    assert flow.observe(client, first, [alert]).json()["observed"] == 0
    assert flow.observe(client, second, [alert]).json()["observed"] == 1


def test_one_run_on_the_endpoint_is_credited_as_before(client):
    flow.register(client, AGENT, HOST)
    only = _executed_run(client)
    r = flow.observe(client, only, [_technique_only_alert("al-347-solo")])
    assert r.json()["observed"] == 1 and "not_credited" not in r.json(), r.text


def test_a_run_that_never_reached_a_beacon_does_not_cap_the_one_before_it(client):
    """A queued run raised nothing: its launch is not a later run's start."""
    flow.register(client, AGENT, HOST)
    first = _executed_run(client)
    flow.launch(client, agent_id=AGENT)          # never collected
    r = flow.observe(client, first, [_technique_only_alert("al-347-queued")])
    assert r.json()["observed"] == 1, r.text


def test_startup_adds_alert_attribution_to_a_legacy_runs_table(tmp_path, monkeypatch):
    """An existing cortexsim.db has no runs.alert_attribution; create_all never
    adds a column, so without the startup migration every Run SELECT fails
    after the upgrade. A run stored before it reads as not reviewed (NULL)."""
    import asyncio

    import database
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.ext.asyncio import create_async_engine

    path = tmp_path / "legacy.db"
    legacy = create_engine(f"sqlite:///{path}")
    with legacy.begin() as conn:
        conn.execute(text("CREATE TABLE runs (run_id VARCHAR PRIMARY KEY, scenario_id VARCHAR)"))
        conn.execute(text("INSERT INTO runs (run_id, scenario_id) VALUES ('old', 'S')"))
    monkeypatch.setattr(database, "engine", create_async_engine(f"sqlite+aiosqlite:///{path}"))
    for _ in range(2):                                  # the second boot proves idempotence
        asyncio.run(database._init_db_inner())
    with legacy.begin() as conn:
        assert "alert_attribution" in {c["name"] for c in inspect(conn).get_columns("runs")}
        assert conn.execute(text(
            "SELECT alert_attribution FROM runs WHERE run_id = 'old'")).scalar_one() is None
