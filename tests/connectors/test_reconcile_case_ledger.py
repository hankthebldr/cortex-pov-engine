"""Reconcile builds the run's case ledger and stores it on ``Run.case_ledger``.

Transports are injected; nothing touches a tenant (tenant-verified is 0). The
ledger is disposition CONTEXT: the invariance test pins that detection
verdicts are byte-identical whether the case read succeeds or fails.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from tests.connectors.test_reconcile_queries_issued import _reconcile, _setup
from tests.connectors.test_verification_service import _seed

pytestmark = pytest.mark.usefixtures("non_utc_host")

ACC = {"kpi": "Detection Accuracy", "op": "≥", "value": 50, "unit": "%"}


def _epoch_ms(naive_utc: datetime) -> int:
    return int(naive_utc.replace(tzinfo=timezone.utc).timestamp() * 1000)


def _alert(ext, *, technique, case="900", seconds=30, severity="high"):
    return {"alert_id": ext, "name": f"alert {ext}", "host_name": "web-01",
            "source": "XDR BIOC", "severity": severity, "incident_id": case,
            "mitre_technique_ids": [technique],
            "detection_timestamp": _epoch_ms(datetime.utcnow() - timedelta(minutes=10)
                                             + timedelta(seconds=seconds))}


def _case(cid="900", status="new", **kw):
    return {"incident_id": cid, "status": status, "alert_count": 2, "hosts": ["web-01:aef3"],
            "creation_time": _epoch_ms(datetime.utcnow()), "starred": False, **kw}


def _tenant(alerts, cases, *, issues=None, issue_http=200):
    calls = {"alerts": 0, "cases": 0, "issues": 0}

    def fetcher(method, url, headers, body, timeout):
        rd = json.loads(body)["request_data"]
        if "issue/search" in url:
            calls["issues"] += 1
            if issue_http != 200:
                return issue_http, "{}"
            rows = issues if issues is not None else [
                {"id": 1000 + i, "status.progress": "New"} for i, _ in enumerate(alerts)]
            return 200, json.dumps({"reply": {"DATA": rows, "result_count": len(rows),
                                              "total_count": len(rows)}})
        if "incidents" in url:
            calls["cases"] += 1
            return 200, json.dumps({"reply": {"incidents": cases, "result_count": len(cases),
                                              "total_count": len(cases)}})
        calls["alerts"] += 1
        page = alerts[rd["search_from"]:rd["search_to"]]
        return 200, json.dumps({"reply": {"total_count": len(alerts), "result_count": len(page),
                                          "alerts": page}})
    return fetcher, calls


def _alerts():
    return [_alert("a", technique="T1003.001"), _alert("b", technique="T1059", seconds=60)]


def _go(session_factory, fetcher, *, results=2):
    _seed(session_factory, results=results, threshold=ACC, primary_kpi="Detection Accuracy")
    asyncio.run(_setup(session_factory, techniques=("T1003.001", "T1059")))
    return asyncio.run(_reconcile(session_factory, fetcher))


def test_reconcile_stores_case_ledger(session_factory):
    fetcher, _ = _tenant(_alerts(), [_case()])
    summary, run = _go(session_factory, fetcher)
    assert run.case_ledger["merge_class"] == "ONE_CASE"
    assert run.case_ledger["code"] == "CASE_LEDGER_OK"
    assert summary["case_ledger"] == {"merge_class": "ONE_CASE", "code": "CASE_LEDGER_OK"}
    assert run.to_dict()["case_ledger"]["merge_class"] == "ONE_CASE"


def test_reconcile_counts_case_queries(session_factory):
    fetcher, calls = _tenant(_alerts(), [_case()])
    summary, _ = _go(session_factory, fetcher)
    assert calls == {"alerts": 1, "cases": 1, "issues": 1}
    assert summary["queries_issued"] == 1 + 1 + 1


def test_unrecognized_status_degrades_ledger(session_factory):
    fetcher, calls = _tenant(_alerts(), [_case(status="resolved_unheard_of")])
    summary, run = _go(session_factory, fetcher)
    assert run.case_ledger["code"] == "CASE_STATUS_UNRECOGNIZED"
    assert run.case_ledger["merge_class"] == "PENDING"
    assert summary["case_ledger"]["code"] == "CASE_STATUS_UNRECOGNIZED"
    assert summary["queries_issued"] == 1 + 1  # the case read was attempted; issues were not


def test_case_read_failure_does_not_fail_reconcile(session_factory, monkeypatch):
    from connectors.xsiam import XsiamConnector
    from connectors.base import PullResult

    monkeypatch.setattr(
        XsiamConnector, "pull_cases",
        lambda self, cfg, ids: PullResult(ok=False, connector="xsiam", code="XSIAM_HTTP_ERROR",
                                          error="boom", detail={"pages": 1}))
    fetcher, calls = _tenant(_alerts(), [_case()])
    summary, run = _go(session_factory, fetcher)
    assert run.case_ledger["code"] == "CASE_READ_FAILED"
    assert run.case_ledger["merge_class"] == "PENDING"
    assert calls["issues"] == 0
    assert summary["queries_issued"] == 1 + 1


def test_issue_read_failure_is_recorded_never_open(session_factory):
    fetcher, calls = _tenant(_alerts(), [_case()], issue_http=500)
    summary, run = _go(session_factory, fetcher)
    led = run.case_ledger
    assert led["merge_class"] == "ONE_CASE" and led["code"] == "CASE_LEDGER_OK"
    assert led["issue_read"]["ok"] is False and led["issue_read"]["code"]
    assert {a["issue_closed_by"] for a in led["alerts"]} == {"unread"}
    assert summary["queries_issued"] == 1 + 1 + led["issue_read"]["pages"]


def test_unexpected_exception_degrades_not_fails(session_factory, monkeypatch):
    from connectors.xsiam import XsiamConnector

    def boom(self, cfg, ids):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(XsiamConnector, "pull_cases", boom)
    fetcher, _ = _tenant(_alerts(), [_case()])
    summary, run = _go(session_factory, fetcher)
    assert run.case_ledger["code"] == "CASE_READ_FAILED"
    assert run.case_ledger["merge_class"] == "PENDING"
    assert summary["queries_issued"] == 1  # the failed read never reported pages
    assert run.tc_verdict  # scoring still ran


def test_issue_ids_join_alert_external_ids(session_factory):
    issues = [{"id": "a", "status.progress": "Resolved",
               "status.resolution_reason": "RESOLVED_OTHER", "status.resolution_comment": "done"},
              {"id": "b", "status.progress": "In Progress"}]
    fetcher, _ = _tenant(_alerts(), [_case()], issues=issues)
    _, run = _go(session_factory, fetcher)
    by = {a["external_id"]: a["issue_closed_by"] for a in run.case_ledger["alerts"]}
    assert by == {"a": "other_resolved", "b": "open"}
    assert run.case_ledger["issue_read"]["ok"] is True


def test_ledger_rolls_back_with_verdicts(session_factory, monkeypatch):
    """No commit inside the helper: if scoring/persisting verdicts raises, the
    ledger is not persisted either."""
    import connectors.service as svc
    from models import Run
    from sqlalchemy import select

    async def boom(*a, **k):
        raise RuntimeError("scoring failed")
    monkeypatch.setattr(svc, "apply_verdicts", boom)
    fetcher, _ = _tenant(_alerts(), [_case()])
    _seed(session_factory, results=2, threshold=ACC, primary_kpi="Detection Accuracy")
    asyncio.run(_setup(session_factory, techniques=("T1003.001", "T1059")))
    with pytest.raises(RuntimeError):
        asyncio.run(_reconcile(session_factory, fetcher))

    async def _read():
        async with session_factory() as db:
            return (await db.execute(select(Run))).scalars().first().case_ledger
    assert asyncio.run(_read()) is None


def _verdict_state(session_factory):
    from models import Result, Run
    from sqlalchemy import select

    async def _do():
        async with session_factory() as db:
            rows = (await db.execute(select(Result).order_by(Result.id))).scalars().all()
            run = (await db.execute(select(Run))).scalars().first()
            return json.dumps({
                "results": [[r.observed, r.observed_at.isoformat() if r.observed_at else None,
                             r.outcome, r.mttd_seconds] for r in rows],
                "tc_verdict": run.tc_verdict, "detail": run.tc_verdict_detail}, sort_keys=True,
                default=str)
    return asyncio.run(_do())


def _fresh_db():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                 connect_args={"check_same_thread": False}, poolclass=StaticPool)

    async def _init():
        from database import Base
        import models  # noqa: F401

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    return async_sessionmaker(bind=engine, expire_on_commit=False)


def test_detection_verdicts_identical_with_and_without_ledger(monkeypatch):
    """The ledger is context: observed_at, outcome, MTTD and tc_verdict must not
    move whether the case read succeeds or fails."""
    from connectors.base import PullResult
    from connectors.xsiam import XsiamConnector

    real = XsiamConnector.pull_cases
    states, merges = [], []
    for ledger_ok in (True, False):
        monkeypatch.setattr(
            XsiamConnector, "pull_cases",
            real if ledger_ok else (lambda self, cfg, ids: PullResult(
                ok=False, connector="xsiam", code="XSIAM_HTTP_ERROR", error="x",
                detail={"pages": 1})))
        sf = _fresh_db()
        fetcher, _ = _tenant(_alerts(), [_case()])
        _seed(sf, results=2, threshold=ACC, primary_kpi="Detection Accuracy")
        asyncio.run(_setup(sf, techniques=("T1003.001", "T1059")))
        _, run = asyncio.run(_reconcile(sf, fetcher))
        merges.append(run.case_ledger["merge_class"])
        states.append(json.loads(_verdict_state(sf)))
    assert merges == ["ONE_CASE", "PENDING"]
    assert states[0]["tc_verdict"] and any(r[0] for r in states[0]["results"])

    # Each DB baked its own `utcnow() - 10 min` at seed time, so absolute
    # timestamps differ by the seconds between seeds (MTTD is compared to the minute);
    # everything else must match.
    def norm(d):
        d = json.loads(json.dumps(d))
        (d["detail"] or {}).pop("scored_at", None)  # the wall-clock of scoring itself
        return {**d, "results": [[o, ob is not None, oc, None if m is None else round(m / 60)]
                                 for o, ob, oc, m in d["results"]]}
    assert norm(states[0]) == norm(states[1])


# ── IP-to-host bridge diagnosis ────────────────────────────────────────────

def _ip_only(ext, **kw):
    a = _alert(ext, **kw)
    a["host_name"] = None            # a firewall alert: an IP, no hostname
    return a


def _with_endpoint(fetcher, endpoints, *, http=200):
    """Wrap a tenant fetcher so get_endpoint answers; returns (fetcher, calls)."""
    calls = []

    def f(method, url, headers, body, timeout):
        if "endpoints/get_endpoint" in url:
            calls.append(json.loads(body)["request_data"])
            if http != 200:
                return http, "{}"
            return 200, json.dumps({"reply": {"total_count": len(endpoints),
                                              "result_count": len(endpoints),
                                              "endpoints": endpoints}})
        return fetcher(method, url, headers, body, timeout)
    return f, calls


async def _set_agent_ip(session_factory, ip):
    from models import Agent
    from sqlalchemy import select
    async with session_factory() as db:
        (await db.execute(select(Agent))).scalars().first().last_ip = ip
        await db.commit()


def _go_ip(session_factory, fetcher, *, agent_ip="10.0.1.20"):
    _seed(session_factory, results=2, threshold=ACC, primary_kpi="Detection Accuracy")
    asyncio.run(_setup(session_factory, techniques=("T1003.001", "T1059")))
    asyncio.run(_set_agent_ip(session_factory, agent_ip))
    return asyncio.run(_reconcile(session_factory, fetcher))


def _split_tenant(*, second_host=False):
    second = (_alert if second_host else _ip_only)("b", technique="T1059", case="901", seconds=60)
    return _tenant([_alert("a", technique="T1003.001"), second],
                   [_case("900"), _case("901")])


def test_bridge_bridged_end_to_end(session_factory):
    base, _ = _split_tenant()
    f, calls = _with_endpoint(base, [{"endpoint_name": "web-01", "ip": ["10.0.1.20"]}])
    summary, run = _go_ip(session_factory, f)
    led = run.case_ledger
    assert led["merge_class"] == "SPLIT_NATIVE" and led["code"] == "CASE_LEDGER_OK"
    assert led["bridge"]["code"] == "BRIDGED"
    assert led["bridge"]["agent_ip"] == "10.0.1.20"
    assert "BRIDGED" in led["merge_reason"]
    assert calls[0]["filters"] == [{"field": "hostname", "operator": "in", "value": ["web-01"]}]
    # 1 alert page + 1 case read + 1 issue read + 1 endpoint read
    assert summary["queries_issued"] == 4


def test_bridge_unbridged_end_to_end(session_factory):
    base, _ = _split_tenant()
    f, _ = _with_endpoint(base, [{"endpoint_name": "web-01", "ip": ["10.9.9.9"]}])
    _, run = _go_ip(session_factory, f)
    assert run.case_ledger["bridge"]["code"] == "UNBRIDGED_IP_HOST"
    assert run.case_ledger["merge_class"] == "SPLIT_NATIVE"


@pytest.mark.parametrize("reply", [
    [],                                                           # not returned
    [{"endpoint_name": "other-host", "ip": ["10.0.1.20"]}],       # a different host
    [{"endpoint_name": "web-01"}],                                # no ip field
    [{"endpoint_name": "web-01", "ip": "10.0.1.20"}],             # ip is not a list
])
def test_bridge_unreadable_record_is_unread_never_bridged(session_factory, reply):
    base, _ = _split_tenant()
    f, _ = _with_endpoint(base, reply)
    summary, run = _go_ip(session_factory, f)
    assert run.case_ledger["bridge"]["code"] == "BRIDGE_UNREAD"
    assert summary["queries_issued"] == 4          # the read was attempted: counted


def test_bridge_read_failure_degrades_and_is_counted(session_factory):
    base, _ = _split_tenant()
    f, _ = _with_endpoint(base, [], http=500)
    summary, run = _go_ip(session_factory, f)
    assert run.case_ledger["bridge"]["code"] == "BRIDGE_UNREAD"
    assert run.case_ledger["code"] == "CASE_LEDGER_OK"
    assert summary["queries_issued"] == 4


def test_bridge_only_runs_on_split_native_with_mixed_keys(session_factory, monkeypatch):
    from connectors.xsiam import XsiamConnector
    seen = []
    monkeypatch.setattr(XsiamConnector, "pull_endpoint_ips",
                        lambda self, cfg, hostname: seen.append(hostname) or ["10.0.1.20"])
    # ONE_CASE with mixed keys: never read.
    base, _ = _tenant([_alert("a", technique="T1003.001"),
                       _ip_only("b", technique="T1059", seconds=60)], [_case("900")])
    _, run = _go_ip(session_factory, base)
    assert run.case_ledger["merge_class"] == "ONE_CASE" and "bridge" not in run.case_ledger
    assert seen == []


def test_bridge_not_read_on_split_native_without_mixed_keys(session_factory, monkeypatch):
    from connectors.xsiam import XsiamConnector
    seen = []
    monkeypatch.setattr(XsiamConnector, "pull_endpoint_ips",
                        lambda self, cfg, hostname: seen.append(hostname) or ["10.0.1.20"])
    base, _ = _split_tenant(second_host=True)          # both alerts carry a host
    _, run = _go_ip(session_factory, base)
    assert run.case_ledger["merge_class"] == "SPLIT_NATIVE" and "bridge" not in run.case_ledger
    assert seen == []

