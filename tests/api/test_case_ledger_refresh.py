"""POST /api/runs/{id}/case-ledger/refresh and the pair endpoint's merge (Task B5)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from tests.connectors.test_case_ledger_sweep import seed, stored_ledger, tenant


@pytest.fixture
def client(make_client):
    from api.runs import router
    return make_client(router)


@pytest.fixture
def inject(monkeypatch):
    """Route every get_connector call through the injected fetcher."""
    import connectors.service as svc
    real = svc.get_connector

    def install(injected):
        monkeypatch.setattr(svc, "get_connector",
                            lambda kind, fetcher=None: real(kind, fetcher=injected))
    return install


def _get(session_factory, run_id):
    from models import Run
    from sqlalchemy import select

    async def go():
        async with session_factory() as db:
            return (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one()
    return asyncio.run(go())


def test_refresh_rereads_cases_keeps_alerts(client, session_factory, inject):
    seed(session_factory, [("r1", datetime.utcnow(), stored_ledger())])
    fetcher, calls = tenant({"900": "resolved_other"})
    inject(fetcher)
    r = client.post("/api/runs/r1/case-ledger/refresh")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["queries_issued"] == 1 and (calls["cases"], calls["other"]) == (1, 0)
    led = _get(session_factory, "r1").case_ledger
    assert led["cases"][0]["status"] == "resolved_other"
    assert led["cases"][0]["sealed"] is True
    # Alerts carried forward unchanged, including the issue disposition this
    # refresh did not re-read.
    assert [a["external_id"] for a in led["alerts"]] == ["a", "b"]
    assert {a["issue_closed_by"] for a in led["alerts"]} == {"other_resolved"}
    assert led["merge_class"] == "ONE_CASE"
    assert body["merge_class"] == "ONE_CASE"


def test_refresh_without_ledger_is_409_no_case_ledger(client, session_factory, inject):
    seed(session_factory, [("r1", datetime.utcnow(), None)])
    fetcher, calls = tenant()
    inject(fetcher)
    r = client.post("/api/runs/r1/case-ledger/refresh")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "NO_CASE_LEDGER"
    assert calls["cases"] == 0


def test_refresh_unknown_run_is_404(client):
    r = client.post("/api/runs/nope/case-ledger/refresh")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "RUN_NOT_FOUND"


def test_refresh_read_failure_keeps_the_stored_ledger(client, session_factory, inject):
    seed(session_factory, [("r1", datetime.utcnow(), stored_ledger())])
    fetcher, _ = tenant(http=500)
    inject(fetcher)
    r = client.post("/api/runs/r1/case-ledger/refresh")
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "CONNECTOR_PULL_FAILED"
    assert _get(session_factory, "r1").case_ledger["cases"][0]["status"] == "new"


def test_refresh_unrecognised_status_degrades_with_code(client, session_factory, inject):
    seed(session_factory, [("r1", datetime.utcnow(), stored_ledger())])
    fetcher, _ = tenant({"900": "resolved_unheard_of"})
    inject(fetcher)
    r = client.post("/api/runs/r1/case-ledger/refresh")
    assert r.status_code == 200
    led = _get(session_factory, "r1").case_ledger
    assert led["code"] == "CASE_STATUS_UNRECOGNIZED" and led["merge_class"] == "PENDING"


def test_refresh_commit_failure_is_an_error_not_a_retry(client, session_factory, inject, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession
    seed(session_factory, [("r1", datetime.utcnow(), stored_ledger())])
    fetcher, _ = tenant({"900": "resolved_other"})
    inject(fetcher)
    n = {"commits": 0}

    async def boom(self):
        n["commits"] += 1
        raise RuntimeError("disk full")
    monkeypatch.setattr(AsyncSession, "commit", boom)
    from fastapi.testclient import TestClient
    c = TestClient(client.app, raise_server_exceptions=False)
    r = c.post("/api/runs/r1/case-ledger/refresh")
    assert r.status_code == 500
    assert n["commits"] == 1


def _pair_seed(session_factory, la, lb):
    from models import Run
    now = datetime.utcnow()

    async def go():
        async with session_factory() as db:
            db.add(Run(run_id="rep", scenario_id="S", mode="pull", status="complete",
                       agent_profile_mode="report", started_at=now, case_ledger=la))
            db.add(Run(run_id="blk", scenario_id="S", mode="pull", status="complete",
                       agent_profile_mode="block", paired_with_run_id="rep",
                       started_at=now, case_ledger=lb))
            await db.commit()
    asyncio.run(go())


def test_pair_endpoint_carries_merge(client, session_factory):
    _pair_seed(session_factory, stored_ledger(case_id="900"), stored_ledger(case_id="901"))
    r = client.get("/api/runs/rep/pair")
    assert r.status_code == 200, r.text
    merge = r.json()["merge"]
    assert merge["merge_class"] in ("SPLIT_NATIVE", "SPLIT_SEALED", "ONE_CASE")
    assert merge["merge_class"] == "SPLIT_NATIVE" and merge["basis"] == "alert_incident_ids"


def test_pair_merge_pending_when_one_run_has_no_ledger(client, session_factory):
    _pair_seed(session_factory, stored_ledger(), None)
    merge = client.get("/api/runs/rep/pair").json()["merge"]
    assert merge["merge_class"] == "PENDING"


def test_pair_merge_corrupt_ledger_degrades_with_code(client, session_factory):
    _pair_seed(session_factory, stored_ledger(), {"basis": "alert_incident_ids", "cases": "x"})
    merge = client.get("/api/runs/rep/pair").json()["merge"]
    assert merge["merge_class"] == "PENDING" and merge["code"] == "CASE_LEDGER_UNREADABLE"


def test_refresh_tenant_unknown_is_409(client, session_factory, inject):
    seed(session_factory, [("r1", datetime.utcnow(), stored_ledger())], names=("alpha", "beta"))
    fetcher, calls = tenant()
    inject(fetcher)
    r = client.post("/api/runs/r1/case-ledger/refresh")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "CASE_LEDGER_TENANT_UNKNOWN"
    assert calls["cases"] == 0


def test_refresh_recorded_integration_gone_is_404(client, session_factory, inject):
    seed(session_factory, [("r1", datetime.utcnow(), stored_ledger(integration="gone"))])
    fetcher, calls = tenant()
    inject(fetcher)
    r = client.post("/api/runs/r1/case-ledger/refresh")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "INTEGRATION_NOT_FOUND"
    assert _get(session_factory, "r1").case_ledger["cases"][0]["status"] == "new"


def test_pair_merge_wrong_typed_field_degrades_not_500(client, session_factory):
    bad = stored_ledger()
    bad["read_at"] = 12345  # compared with the other run's str read_at
    _pair_seed(session_factory, stored_ledger(), bad)
    r = client.get("/api/runs/rep/pair")
    assert r.status_code == 200, r.text
    m = r.json()["merge"]
    assert m["merge_class"] == "PENDING" and m["code"] == "CASE_LEDGER_UNREADABLE"


def test_refresh_of_a_no_case_yet_ledger_points_at_the_next_reconcile(client, session_factory, inject):
    from connectors.case_ledger import LedgerAlert, build_case_ledger
    now = datetime.utcnow()
    led = build_case_ledger([LedgerAlert("a", now, "high", None, None, None, None),
                             LedgerAlert("b", now, "high", None, None, None, None)], {},
                            basis="alert_incident_ids", read_at=now - timedelta(hours=2))
    assert led["merge_class"] == "NO_CASE_YET"
    seed(session_factory, [("r1", now, led)])
    fetcher, calls = tenant()
    inject(fetcher)
    body = client.post("/api/runs/r1/case-ledger/refresh").json()
    assert body["refreshed"] is False and calls["cases"] == 0
    assert "reconcile" in body["reason"]
