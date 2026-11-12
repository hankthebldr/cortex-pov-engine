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


def tenant(rows_by_status=None, *, http=200, http_for_ids=None):
    """Fetcher answering only ``get_incidents``; counts calls per endpoint and
    records the host of each call (``calls["hosts"]``)."""
    calls = {"cases": 0, "other": 0, "hosts": []}
    status = rows_by_status or {}

    def fetcher(method, url, headers, body, timeout):
        rd = json.loads(body)["request_data"]
        if "incidents" not in url:
            calls["other"] += 1
            return 200, json.dumps({"reply": {}})
        calls["cases"] += 1
        calls["hosts"].append(url.split("/")[2])
        ids = rd["filters"][0]["value"]
        if http != 200 or (http_for_ids and set(ids) & set(http_for_ids)):
            return (http if http != 200 else 500), "{}"
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


def stored_ledger(*, case_id="900", status="new", closed_by="other_resolved",
                  read_age=timedelta(hours=2), integration=None):
    now = datetime.now(timezone.utc)
    resolved_at = now if status != "new" else None
    alerts = [LedgerAlert("a", now, "high", case_id, "Resolved", None, None),
              LedgerAlert("b", now + timedelta(seconds=30), "high", case_id, "Resolved", None, None)]
    cases = {case_id: LedgerCase(case_id, status, None, resolved_at, False, False, None, 2)}
    led = build_case_ledger(alerts, cases, basis="alert_incident_ids", read_at=now - read_age)
    if integration:
        led["integration"] = integration
    for a in led["alerts"]:
        a["issue_closed_by"] = closed_by  # distinguishable from anything a refresh could recompute
    return led


def seed(SessionLocal, runs, *, integration=True, names=("acme",)):
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
                for name in names:
                    await CredentialStore(db).put_integration(
                        name=name, kind="xsiam", plaintext_secret="k" * 40,
                        config={"fqdn": f"api-{name}.xdr.us.paloaltonetworks.com",
                                "api_key_id": "1"})
                await db.commit()
    asyncio.run(go())


def sweep(SessionLocal, fetcher, *, window_hours=24, max_runs=20, min_interval_minutes=60):
    from connectors.service import refresh_case_ledgers_once

    async def go():
        async with SessionLocal() as db:
            return await refresh_case_ledgers_once(
                db, window_hours=window_hours, max_runs=max_runs,
                min_interval_minutes=min_interval_minutes, fetcher=fetcher)
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


# ── fix round 1 ───────────────────────────────────────────────────────────

def test_fresh_read_at_is_skipped_without_a_call(session_factory):
    seed(session_factory, [("r0", datetime.utcnow(),
                            stored_ledger(read_age=timedelta(minutes=5)))])
    fetcher, calls = tenant()
    out = sweep(session_factory, fetcher, min_interval_minutes=60)
    assert calls["cases"] == 0 and out["refreshed"] == 0 and out["queries_issued"] == 0


def test_all_sealed_ledger_is_skipped(session_factory):
    seed(session_factory, [("r0", datetime.utcnow(), stored_ledger(status="resolved_other"))])
    fetcher, calls = tenant()
    out = sweep(session_factory, fetcher)
    assert calls["cases"] == 0 and out["refreshed"] == 0


def test_auto_resolved_only_ledger_is_still_refreshed(session_factory):
    """An auto_resolved case can still reopen: not terminal."""
    seed(session_factory, [("r0", datetime.utcnow(),
                            stored_ledger(status="resolved_auto_resolve"))])
    fetcher, calls = tenant({"900": "new"})
    out = sweep(session_factory, fetcher)
    assert calls["cases"] == 1 and out["refreshed"] == 1


def test_sweep_rotates_least_recently_read_first(session_factory):
    now = datetime.utcnow()
    seed(session_factory, [
        ("newer", now, stored_ledger(case_id="901", read_age=timedelta(hours=2))),
        ("older", now, stored_ledger(case_id="902", read_age=timedelta(hours=5)))])
    fetcher, calls = tenant()
    # Each refresh stamps read_at=now, so with min interval 60 the refreshed run
    # drops out and the other one is next.
    first = sweep(session_factory, fetcher, max_runs=1)
    assert first["refreshed"] == 1
    assert ledger_of(session_factory, "older")["read_at"] > ledger_of(session_factory, "newer")["read_at"]
    second = sweep(session_factory, fetcher, max_runs=1)
    assert second["refreshed"] == 1 and calls["cases"] == 2
    third = sweep(session_factory, fetcher, max_runs=1)
    assert third["refreshed"] == 0 and calls["cases"] == 2


def test_sweep_continues_after_a_failed_refresh_expires_the_session(session_factory, monkeypatch):
    """A rollback expires every instance in the shared session; the next run must
    still be refreshed in the same sweep."""
    import connectors.service as svc
    now = datetime.utcnow()
    seed(session_factory, [
        ("r0", now, stored_ledger(case_id="901", read_age=timedelta(hours=5))),
        ("r1", now, stored_ledger(case_id="902", read_age=timedelta(hours=3)))])
    fetcher, calls = tenant({"902": "resolved_other"})
    real = svc.refresh_case_ledger

    async def flaky(db, run, **kw):
        if run.run_id == "r0":
            await db.rollback()  # what a failed commit does
            raise RuntimeError("commit failed")
        return await real(db, run, **kw)
    monkeypatch.setattr(svc, "refresh_case_ledger", flaky)
    out = sweep(session_factory, fetcher)
    assert out["skipped"] == 1 and out["refreshed"] == 1
    assert ledger_of(session_factory, "r1")["cases"][0]["status"] == "resolved_other"


def test_read_error_on_first_run_does_not_stop_the_second(session_factory):
    now = datetime.utcnow()
    seed(session_factory, [
        ("r0", now, stored_ledger(case_id="901", read_age=timedelta(hours=5))),
        ("r1", now, stored_ledger(case_id="902", read_age=timedelta(hours=3)))])
    fetcher, calls = tenant(http_for_ids=["901"])
    out = sweep(session_factory, fetcher)
    assert calls["cases"] == 2 and out["refreshed"] == 1 and out["skipped"] == 1


def test_refresh_uses_the_recorded_integration(session_factory):
    seed(session_factory, [("r0", datetime.utcnow(), stored_ledger(integration="beta"))],
         names=("alpha", "beta"))
    fetcher, calls = tenant()
    out = sweep(session_factory, fetcher)
    assert out["refreshed"] == 1
    assert calls["hosts"] == ["api-beta.xdr.us.paloaltonetworks.com"]
    assert ledger_of(session_factory, "r0")["integration"] == "beta"


def test_missing_integration_field_with_two_integrations_is_refused(session_factory):
    seed(session_factory, [("r0", datetime.utcnow(), stored_ledger())], names=("alpha", "beta"))
    fetcher, calls = tenant()
    out = sweep(session_factory, fetcher)
    assert calls["cases"] == 0 and out["refreshed"] == 0 and out["skipped"] == 1
    assert "integration" not in ledger_of(session_factory, "r0")


def test_missing_integration_field_with_one_integration_proceeds(session_factory):
    seed(session_factory, [("r0", datetime.utcnow(), stored_ledger())], names=("alpha",))
    fetcher, calls = tenant()
    out = sweep(session_factory, fetcher)
    assert out["refreshed"] == 1
    assert ledger_of(session_factory, "r0")["integration"] == "alpha"


def test_recorded_integration_gone_keeps_the_stored_ledger(session_factory):
    seed(session_factory, [("r0", datetime.utcnow(), stored_ledger(integration="deleted"))],
         names=("alpha",))
    fetcher, calls = tenant()
    out = sweep(session_factory, fetcher)
    assert calls["cases"] == 0 and out["refreshed"] == 0
    assert ledger_of(session_factory, "r0")["cases"][0]["status"] == "new"


def _run_loop_once(monkeypatch, stats):
    import asyncio as aio
    import connectors.service as svc
    phase = {"n": 0}

    async def fake_once(db, **kw):
        return stats

    async def fake_phase():
        phase["n"] += 1
    sleeps = {"n": 0}

    async def fake_sleep(_):
        sleeps["n"] += 1
        if sleeps["n"] > 1:
            raise aio.CancelledError
    monkeypatch.setattr(svc, "auto_reconcile_once", fake_once)
    monkeypatch.setattr(svc, "_refresh_ledgers_phase", fake_phase)
    monkeypatch.setattr(aio, "sleep", fake_sleep)
    with pytest.raises(aio.CancelledError):
        aio.run(svc.auto_reconcile_loop(1, 60, 60))
    return phase["n"]


def test_loop_runs_the_refresh_phase(monkeypatch):
    assert _run_loop_once(monkeypatch, {"newly_matched": 0, "quota_tripped": False}) == 1


def test_loop_skips_the_refresh_phase_when_the_breaker_tripped(monkeypatch):
    assert _run_loop_once(monkeypatch, {"newly_matched": 0, "quota_tripped": True}) == 0


# ── the IP-to-host bridge across a refresh ─────────────────────────────────

def _split_with_bridge(read_at):
    now = datetime.now(timezone.utc)
    alerts = [LedgerAlert("a", now, "high", "900", "New", None, None),
              LedgerAlert("b", now + timedelta(seconds=30), "high", "901", "New", None, None)]
    cases = {c: LedgerCase(c, "new", None, None, False, False, None, 2) for c in ("900", "901")}
    led = build_case_ledger(alerts, cases, basis="alert_incident_ids", read_at=now)
    assert led["merge_class"] == "SPLIT_NATIVE"
    led["integration"] = "acme"
    led["bridge"] = {"host": "web-01", "agent_ip": "10.0.1.20", "endpoint_ips": ["10.0.1.20"],
                     "code": "BRIDGED", "read_at": read_at}
    led["merge_reason"] += "; ip-to-host bridge: BRIDGED"
    return led


def _refresh(SessionLocal, fetcher):
    from connectors.service import refresh_case_ledger
    from models import Run
    from sqlalchemy import select

    async def go():
        async with SessionLocal() as db:
            run = (await db.execute(select(Run))).scalar_one()
            # force: these ledgers were read "now"; the min interval is tested on the route.
            return await refresh_case_ledger(db, run, fetcher=fetcher, force=True)
    return asyncio.run(go())


def _tenant_returning(ids):
    fetcher, calls = tenant()

    def f(method, url, headers, body, timeout):
        code, text = fetcher(method, url, headers, body, timeout)
        doc = json.loads(text)
        doc["reply"]["incidents"] = [r for r in doc["reply"]["incidents"]
                                     if r["incident_id"] in ids]
        return code, json.dumps(doc)
    return f


def test_refresh_carries_bridge_marked_carried_with_original_read_at(session_factory):
    orig = "2026-11-11T10:00:00+00:00"
    seed(session_factory, [("r1", datetime.utcnow(), _split_with_bridge(orig))])
    out = _refresh(session_factory, tenant()[0])
    b = out["ledger"]["bridge"]
    assert out["merge_class"] == "SPLIT_NATIVE"
    assert b["carried"] is True and b["read_at"] == orig and b["code"] == "BRIDGED"
    assert out["ledger"]["read_at"] != orig       # the ledger itself is fresh


def test_refresh_drops_bridge_when_class_changes(session_factory):
    seed(session_factory, [("r1", datetime.utcnow(),
                            _split_with_bridge("2026-11-11T10:00:00+00:00"))])
    out = _refresh(session_factory, _tenant_returning({"900"}))   # case 901 no longer returned
    assert out["merge_class"] == "PENDING"
    assert "bridge" not in out["ledger"]
    assert "bridge" not in ledger_of(session_factory, "r1")
    assert "ip-to-host bridge" not in out["ledger"]["merge_reason"]


def test_repeated_refresh_does_not_duplicate_bridge_suffix(session_factory):
    seed(session_factory, [("r1", datetime.utcnow(),
                            _split_with_bridge("2026-11-11T10:00:00+00:00"))])
    f = tenant()[0]
    for _ in range(3):
        out = _refresh(session_factory, f)
    assert out["ledger"]["merge_reason"].count("ip-to-host bridge") == 1
    assert out["ledger"]["bridge"]["read_at"] == "2026-11-11T10:00:00+00:00"


# ── B5: a ledger is terminal only when nothing can change it ───────────────

def test_all_sealed_but_pending_ledger_is_still_refreshed(session_factory):
    led = stored_ledger(status="resolved_other")
    led["merge_class"] = "PENDING"
    seed(session_factory, [("r0", datetime.utcnow(), led)])
    fetcher, calls = tenant({"900": "resolved_other"})
    out = sweep(session_factory, fetcher)
    assert calls["cases"] == 1 and out["refreshed"] == 1


def test_all_sealed_ledger_with_a_missing_referenced_case_is_still_refreshed(session_factory):
    led = stored_ledger(status="resolved_other")
    led["alerts"][1]["case_id"] = "901"          # named by an alert, never returned
    led["merge_class"] = "SPLIT_SEALED"          # whatever the stored class says
    seed(session_factory, [("r0", datetime.utcnow(), led)])
    fetcher, calls = tenant({"900": "resolved_other"})
    out = sweep(session_factory, fetcher)
    assert calls["cases"] == 1 and out["refreshed"] == 1


def test_ledgers_naming_no_case_do_not_starve_the_sweep(session_factory):
    # A NO_CASE_YET ledger never advances its read_at (refresh has no case to
    # re-read and answers CASE_LEDGER_NAMES_NO_CASE), so if it stayed eligible
    # it would sort first on every sweep and take a slot from a real ledger.
    now = datetime.utcnow()
    nocase = build_case_ledger([LedgerAlert("x", now, "high", None, None, None, None),
                                LedgerAlert("y", now, "high", None, None, None, None)], {},
                               basis="alert_incident_ids", read_at=now - timedelta(hours=5))
    assert nocase["merge_class"] == "NO_CASE_YET"
    real = stored_ledger(read_age=timedelta(hours=2))
    seed(session_factory, [("n1", now, nocase), ("n2", now, dict(nocase)), ("r1", now, real)])
    fetcher, calls = tenant({"900": "resolved_other"})
    out = sweep(session_factory, fetcher, max_runs=2)
    assert calls["cases"] == 1
    assert out["refreshed"] == 1
    assert ledger_of(session_factory, "r1")["read_at"] != real["read_at"]
