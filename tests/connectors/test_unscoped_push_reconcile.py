"""An unscoped match is not a detection (DOCS-03).

A push run has no ``Run.target`` (the bundle never phones home), so
``resolve_run_host`` returns None and its reconcile is UNSCOPED: every alert in
the tenant is eligible. Before this, a ``detect`` expectation that matched on
technique inside the window was written ``detected``, with an MTTD, even when
the alert named a completely different endpoint — on a shared customer tenant
that is another team's machine being credited to this POV. The summary said
"unscoped", but the row, the coverage % and the MTTD did not.

Default (option c): an unscoped match leaves the row ``pending`` with reason
``UNSCOPED_MATCH``, ``observed`` false and NO ``observed_at`` (so no MTTD); the
candidate alert is still recorded as an ``Observation`` and named in the row's
notes. A host-scoped reconcile — the ``host`` override on ``reconcile_run`` —
stays exactly as before, and can later upgrade the row to ``detected``.

The override must be reachable from the API, or every push run — every Windows
``.ps1`` bundle included — could only ever read ``pending`` for its detect
expectations, even when the DC hand-picked the alerts: ``?host=`` on
``POST /reconcile`` and ``host`` in the ``/observations`` body name the endpoint
the bundle ran on, and the summary records ``host_source: "operator"``.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.usefixtures("non_utc_host")

T0 = datetime.utcnow().replace(microsecond=0) - timedelta(minutes=10)


def _epoch_ms(naive_utc: datetime) -> int:
    return int(naive_utc.replace(tzinfo=timezone.utc).timestamp() * 1000)


def _fetcher(*, host: str, seconds: int = 90):
    def fetcher(method, url, headers, body, timeout):
        return 200, json.dumps({"reply": {"total_count": 1, "result_count": 1, "alerts": [{
            "alert_id": "a-other", "name": "LSASS memory access", "host_name": host,
            "source": "XDR BIOC", "mitre_technique_ids": ["T1003.001"],
            "detection_timestamp": _epoch_ms(T0 + timedelta(seconds=seconds)),
        }]}})
    return fetcher


def _seed(session_factory, run_id="push-1"):
    from models import Result, Run, Scenario
    from security.credentials import CredentialStore

    async def _go():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id="SIM-EDR-970", name="T", version="1.0", status="active",
                plane="EDR", uc_ref="UCS-EDR-01", uc_name="x", tc_ref="TC-EDR-01",
                tc_name="y", mitre_tactic="TA0006", mitre_tactic_name="CA",
                mitre_technique="T1003.001", mitre_technique_name="LSASS", steps=[]))
            db.add(Run(run_id=run_id, scenario_id="SIM-EDR-970", mode="push",
                       status="staged", started_at=T0, completed_at=T0))
            db.add(Result(run_id=run_id, step_id="step-01", plane="EDR", signal_type="BIOC",
                          expected_detection="LSASS memory access", observed=False,
                          executed_at=T0, mitre_technique="T1003.001"))
            await db.commit()
            await CredentialStore(db).put_integration(
                name="acme", kind="xsiam", plaintext_secret="k" * 40,
                config={"fqdn": "api-t.xdr.us.paloaltonetworks.com", "api_key_id": "1"})
            await db.commit()

    asyncio.run(_go())
    return run_id


def _reconcile(session_factory, fetcher, *, host=None):
    from connectors.service import reconcile_run
    from models import Result, Run

    async def _go():
        async with session_factory() as db:
            run = (await db.execute(select(Run))).scalar_one()
            results = list((await db.execute(select(Result))).scalars().all())
            return await reconcile_run(db, run, results, connector_kind="xsiam",
                                       fetcher=fetcher, host=host)

    return asyncio.run(_go())


def _row(session_factory):
    from models import Observation, Result

    async def _go():
        async with session_factory() as db:
            r = (await db.execute(select(Result))).scalar_one()
            obs = (await db.execute(select(Observation))).scalars().all()
            return r, obs

    return asyncio.run(_go())


def test_another_hosts_alert_on_an_unscoped_push_run_is_not_detected(session_factory):
    _seed(session_factory)
    out = _reconcile(session_factory, _fetcher(host="finance-laptop-42"))

    row, obs = _row(session_factory)
    # Base cc26ddf: outcome "detected", observed True, MTTD 90 s.
    assert row.outcome != "detected"
    assert (row.outcome, row.outcome_reason) == ("pending", "UNSCOPED_MATCH")
    assert row.observed is False and row.observed_at is None
    assert row.mttd_seconds is None
    assert "unscoped" in (row.notes or "") and "a-other" in (row.notes or "")
    # The alert is still evidence on record — just not a credited detection.
    assert [o.external_id for o in obs] == ["a-other"]

    assert out.newly_matched == 0
    assert out.summary["observed"] == 0 and out.summary["mttd"]["count"] == 0
    assert out.summary["unscoped_matches"] == 1
    assert "unscoped" in out.summary["warning"]


def test_the_manual_path_on_an_unscoped_run_is_the_same(session_factory):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.connectors import runs_reconcile_router
    from database import get_db

    run_id = _seed(session_factory)

    async def _get_db():
        async with session_factory() as s:
            yield s

    app = FastAPI()
    app.include_router(runs_reconcile_router, prefix="/api")
    app.dependency_overrides[get_db] = _get_db
    r = TestClient(app).post(f"/api/runs/{run_id}/observations", json={"observations": [{
        "external_id": "m-1", "name": "LSASS memory access", "technique": "T1003.001",
        "host": "someone-else", "observed_at": (T0 + timedelta(seconds=30)).isoformat() + "Z",
    }]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["newly_matched"] == 0 and body["observed"] == 0
    assert body["unscoped_matches"] == 1
    row, _ = _row(session_factory)
    assert (row.outcome, row.outcome_reason) == ("pending", "UNSCOPED_MATCH")
    assert row.mttd_seconds is None


def test_the_host_override_stays_scoped_and_normal(session_factory):
    """The negative control: name the host and the same alert is a detection."""
    _seed(session_factory)
    out = _reconcile(session_factory, _fetcher(host="lab-win-07", seconds=90),
                     host="lab-win-07")
    row, _ = _row(session_factory)
    assert (row.outcome, row.observed) == ("detected", True)
    assert abs(row.mttd_seconds - 90.0) < 2.0
    assert out.newly_matched == 1
    assert "unscoped_matches" not in out.summary


def test_a_later_scoped_reconcile_upgrades_an_unscoped_match(session_factory):
    _seed(session_factory)
    _reconcile(session_factory, _fetcher(host="lab-win-07"))
    row, _ = _row(session_factory)
    assert row.outcome_reason == "UNSCOPED_MATCH"

    out = _reconcile(session_factory, _fetcher(host="lab-win-07"), host="lab-win-07")
    row, _ = _row(session_factory)
    assert (row.outcome, row.outcome_reason, row.observed) == ("detected", None, True)
    assert out.newly_matched == 1


def test_the_silent_row_judge_does_not_erase_an_unscoped_match(session_factory):
    """Once the window closes, a later empty unscoped pull must not overwrite
    "an alert matched but could be anyone's" with "no host evidence"."""
    from connectors.service import reconcile_run
    from models import Result, Run

    _seed(session_factory)
    _reconcile(session_factory, _fetcher(host="anywhere"))

    async def _close_window_and_pull_nothing():
        def empty(method, url, headers, body, timeout):
            return 200, json.dumps({"reply": {"total_count": 0, "result_count": 0,
                                              "alerts": []}})
        async with session_factory() as db:
            r = (await db.execute(select(Result))).scalar_one()
            r.executed_at = T0 - timedelta(days=2)
            await db.commit()
            run = (await db.execute(select(Run))).scalar_one()
            await reconcile_run(db, run, [r], connector_kind="xsiam", fetcher=empty)

    asyncio.run(_close_window_and_pull_nothing())
    row, _ = _row(session_factory)
    assert (row.outcome, row.outcome_reason) == ("pending", "UNSCOPED_MATCH")


# ---------------------------------------------------------------------------
# The override through the API: push coverage is measurable when the DC names
# the endpoint the bundle ran on
# ---------------------------------------------------------------------------


def _http(session_factory):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.connectors import runs_reconcile_router
    from database import get_db

    async def _get_db():
        async with session_factory() as s:
            yield s

    app = FastAPI()
    app.include_router(runs_reconcile_router, prefix="/api")
    app.dependency_overrides[get_db] = _get_db
    return TestClient(app)


def test_the_reconcile_route_scopes_a_push_run_to_the_host_the_dc_names(
        session_factory, monkeypatch):
    import connectors.base as base

    run_id = _seed(session_factory)
    monkeypatch.setattr(base, "default_http_fetcher", _fetcher(host="LAB-WIN-07.corp.local",
                                                               seconds=90))
    client = _http(session_factory)

    # Without a host the route is unscoped and honest: pending, no MTTD.
    r = client.post(f"/api/runs/{run_id}/reconcile?connector=xsiam")
    assert r.status_code == 200, r.text
    assert r.json()["unscoped_matches"] == 1 and r.json()["host_source"] is None
    row, _ = _row(session_factory)
    assert (row.outcome, row.outcome_reason) == ("pending", "UNSCOPED_MATCH")

    # Naming the endpoint the .ps1 ran on scopes it, and the match is credited.
    r = client.post(f"/api/runs/{run_id}/reconcile?connector=xsiam&host=lab-win-07")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["host_source"] == "operator"
    assert body["host_scope"]["host"] == "lab-win-07"
    assert body["newly_matched"] == 1 and "unscoped_matches" not in body
    row, _ = _row(session_factory)
    assert (row.outcome, row.observed) == ("detected", True)
    assert abs(row.mttd_seconds - 90.0) < 2.0


def test_the_reconcile_route_host_override_still_excludes_another_hosts_alert(
        session_factory, monkeypatch):
    import connectors.base as base

    run_id = _seed(session_factory)
    monkeypatch.setattr(base, "default_http_fetcher", _fetcher(host="finance-laptop-42"))
    r = _http(session_factory).post(
        f"/api/runs/{run_id}/reconcile?connector=xsiam&host=lab-win-07")
    assert r.status_code == 200, r.text
    assert r.json()["host_scope"]["excluded_other_host"] == 1
    assert r.json()["newly_matched"] == 0
    row, _ = _row(session_factory)
    assert row.outcome != "detected" and row.mttd_seconds is None


def test_the_observations_body_takes_the_host_too(session_factory):
    run_id = _seed(session_factory)
    alert = {"external_id": "m-1", "name": "LSASS memory access", "technique": "T1003.001",
             "host": "lab-win-07", "observed_at": (T0 + timedelta(seconds=30)).isoformat() + "Z"}
    r = _http(session_factory).post(f"/api/runs/{run_id}/observations",
                                    json={"host": "lab-win-07", "observations": [alert]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["host_source"] == "operator" and body["host_scope"]["host"] == "lab-win-07"
    assert body["newly_matched"] == 1 and body["observed"] == 1
    assert "unscoped" not in body
    row, _ = _row(session_factory)
    assert (row.outcome, row.observed) == ("detected", True)
    assert abs(row.mttd_seconds - 30.0) < 2.0


def test_a_blank_host_is_no_override(session_factory):
    """`host=""` must not read as a host: it stays unscoped and says so."""
    run_id = _seed(session_factory)
    alert = {"external_id": "m-2", "name": "LSASS memory access", "technique": "T1003.001",
             "host": "anyone", "observed_at": (T0 + timedelta(seconds=30)).isoformat() + "Z"}
    r = _http(session_factory).post(f"/api/runs/{run_id}/observations",
                                    json={"host": "   ", "observations": [alert]})
    assert r.status_code == 200, r.text
    assert r.json()["host_source"] is None and r.json()["unscoped_matches"] == 1
