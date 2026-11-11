"""The case-ledger refresh and its opt-in sweep (Task B5).

Transports are injected; nothing touches a tenant (tenant-verified is 0).
Disposition drifts after the run (Auto Triage seals cases ~6 h later), so the
stored ledger is re-read: CASES only, alerts and their issue disposition are
carried forward unchanged.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from connectors.case_ledger import LedgerAlert, LedgerCase, build_case_ledger
from connectors.socfw import TRIAGE_RESOLVE_COMMENT_PREFIX

pytestmark = pytest.mark.usefixtures("non_utc_host")


def _epoch_ms(naive_utc: datetime) -> int:
    return int(naive_utc.replace(tzinfo=timezone.utc).timestamp() * 1000)


def tenant(rows_by_status=None, *, http=200):
    """Fetcher answering only ``get_incidents``; counts calls per endpoint."""
    calls = {"cases": 0, "other": 0}
    status = rows_by_status or {}

    def fetcher(method, url, headers, body, timeout):
        rd = json.loads(body)["request_data"]
        if "incidents" not in url:
            calls["other"] += 1
            return 200, json.dumps({"reply": {}})
        calls["cases"] += 1
        if http != 200:
            return http, "{}"
        ids = rd["filters"][0]["value"]
        rows = []
        for cid in ids:
            st = status.get(cid, "new")
            row = {"incident_id": cid, "status": st, "alert_count": 2,
                   "hosts": ["web-01:aef3"], "starred": False,
                   "creation_time": _epoch_ms(datetime.utcnow())}
            if st != "new":
                row["resolve_comment"] = TRIAGE_RESOLVE_COMMENT_PREFIX + " done"
                row["resolved_timestamp"] = _epoch_ms(datetime.utcnow())
            rows.append(row)
        return 200, json.dumps({"reply": {"incidents": rows, "result_count": len(rows),
                                          "total_count": len(rows)}})
    return fetcher, calls


def stored_ledger(*, case_id="900", status="new", closed_by="other_resolved"):
    now = datetime.now(timezone.utc)
    alerts = [LedgerAlert("a", now, "high", case_id, "Resolved", None, None),
              LedgerAlert("b", now + timedelta(seconds=30), "high", case_id, "Resolved", None, None)]
    cases = {case_id: LedgerCase(case_id, status, None, None, False, False, None, 2)}
    led = build_case_ledger(alerts, cases, basis="alert_incident_ids", read_at=now)
    for a in led["alerts"]:
        a["issue_closed_by"] = closed_by  # distinguishable from anything a refresh could recompute
    return led


def seed(SessionLocal, runs, *, integration=True):
    """runs: [(run_id, started_at, ledger-or-None)]."""
    from models import Run
    from security.credentials import CredentialStore

    async def go():
        async with SessionLocal() as db:
            for rid, started, led in runs:
                db.add(Run(run_id=rid, scenario_id="SIM-X", mode="pull", status="complete",
                           started_at=started, case_ledger=led))
            await db.commit()
            if integration:
                await CredentialStore(db).put_integration(
                    name="acme", kind="xsiam", plaintext_secret="k" * 40,
                    config={"fqdn": "api-t.xdr.us.paloaltonetworks.com", "api_key_id": "1"})
                await db.commit()
    asyncio.run(go())


def sweep(SessionLocal, fetcher, *, window_hours=24, max_runs=20):
    from connectors.service import refresh_case_ledgers_once

    async def go():
        async with SessionLocal() as db:
            return await refresh_case_ledgers_once(
                db, window_hours=window_hours, max_runs=max_runs, fetcher=fetcher)
    return asyncio.run(go())


def ledger_of(SessionLocal, run_id):
    from models import Run
    from sqlalchemy import select

    async def go():
        async with SessionLocal() as db:
            return (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one().case_ledger
    return asyncio.run(go())


def test_sweep_skips_runs_older_than_window(session_factory):
    now = datetime.utcnow()
    seed(session_factory, [("fresh", now - timedelta(hours=1), stored_ledger()),
                           ("stale", now - timedelta(hours=48), stored_ledger()),
                           ("noledger", now - timedelta(hours=1), None)])
    fetcher, calls = tenant({"900": "resolved_other"})
    out = sweep(session_factory, fetcher, window_hours=24)
    assert out["refreshed"] == 1 and calls["cases"] == 1
    assert out["queries_issued"] == 1
    assert ledger_of(session_factory, "fresh")["cases"][0]["status"] == "resolved_other"
    assert ledger_of(session_factory, "stale")["cases"][0]["status"] == "new"
    assert ledger_of(session_factory, "noledger") is None


def test_sweep_respects_max_runs(session_factory):
    now = datetime.utcnow()
    seed(session_factory, [(f"r{i}", now - timedelta(minutes=i), stored_ledger())
                           for i in range(3)])
    fetcher, calls = tenant()
    out = sweep(session_factory, fetcher, max_runs=2)
    assert out["refreshed"] == 2 and calls["cases"] == 2
    assert out["queries_issued"] == 2


def test_sweep_stops_on_quota_error(session_factory):
    now = datetime.utcnow()
    seed(session_factory, [(f"r{i}", now - timedelta(minutes=i), stored_ledger())
                           for i in range(3)])
    fetcher, calls = tenant(http=429)
    out = sweep(session_factory, fetcher)
    assert calls["cases"] == 1, "a 429 is the customer's shared quota: stop the whole sweep"
    assert out["refreshed"] == 0 and out["queries_issued"] == 1
    # The failed refresh must not clobber the stored ledger.
    assert ledger_of(session_factory, "r0")["merge_class"] == "ONE_CASE"


def test_sweep_continues_past_a_non_quota_failure(session_factory):
    now = datetime.utcnow()
    seed(session_factory, [("r0", now, stored_ledger()), ("r1", now - timedelta(minutes=1), stored_ledger())])
    fetcher, calls = tenant(http=500)
    out = sweep(session_factory, fetcher)
    assert calls["cases"] == 2 and out["refreshed"] == 0 and out["skipped"] == 2


def test_sweep_without_integration_reads_nothing(session_factory):
    seed(session_factory, [("r0", datetime.utcnow(), stored_ledger())], integration=False)
    fetcher, calls = tenant()
    out = sweep(session_factory, fetcher)
    assert calls["cases"] == 0 and out["refreshed"] == 0 and out["skipped"] == 1


def test_standalone_commit_failure_surfaces_without_a_second_commit(session_factory, monkeypatch):
    """Carried B3 finding: a failed commit must reach the caller, not be re-caught
    as an 'unexpected' ledger error and retried on a failed session."""
    from connectors import ConnectorConfig, get_connector
    from connectors.base import ObservedAlert
    from connectors.service import build_and_store_case_ledger
    from models import Run

    fetcher, _ = tenant()
    conn = get_connector("xsiam", fetcher=fetcher)
    cfg = ConnectorConfig(integration_name="acme", secret="k" * 40,
                          config={"fqdn": "api-t.xdr.us.paloaltonetworks.com", "api_key_id": "1"})
    alert = ObservedAlert(external_id="a", name="n", source="XDR BIOC", observed_at=datetime.now(timezone.utc),
                          severity="high", incident_id="900")

    async def go():
        async with session_factory() as db:
            run = Run(run_id="c1", scenario_id="SIM-X", mode="pull", status="complete")
            db.add(run)
            await db.flush()
            commits = {"n": 0}

            async def boom():
                commits["n"] += 1
                raise RuntimeError("disk full")
            monkeypatch.setattr(db, "commit", boom)
            with pytest.raises(RuntimeError, match="disk full"):
                await build_and_store_case_ledger(db, run, conn=conn, cfg=cfg, alerts=[alert],
                                                  basis="alert_incident_ids", commit=True)
            return commits["n"]
    assert asyncio.run(go()) == 1
