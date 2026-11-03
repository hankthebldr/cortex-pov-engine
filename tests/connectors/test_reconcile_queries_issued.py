"""reconcile_run reports ``queries_issued`` in ledger units: one per tenant
page read, plus the pages of the incidents read when the correlation fallback
was attempted. The number is what an MCP tool reply must state as spend."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from tests.connectors.test_verification_service import _seed

pytestmark = pytest.mark.usefixtures("non_utc_host")

ACC = {"kpi": "Detection Accuracy", "op": "≥", "value": 50, "unit": "%"}
CORR = {"kpi": "Cross-Source Correlation Rate", "op": "≥", "value": 80, "unit": "%"}


def _epoch_ms(naive_utc: datetime) -> int:
    return int(naive_utc.replace(tzinfo=timezone.utc).timestamp() * 1000)


def _alert(ext, *, host="web-01", seconds=30, technique="T1003.001", incident=None):
    a = {"alert_id": ext, "name": f"alert {ext}", "host_name": host, "source": "XDR BIOC",
         "mitre_technique_ids": [technique],
         "detection_timestamp": _epoch_ms(datetime.utcnow() - timedelta(minutes=10)
                                          + timedelta(seconds=seconds))}
    if incident:
        a["incident_id"] = incident
    return a


def _tenant(alerts, incidents=None):
    calls = []

    def fetcher(method, url, headers, body, timeout):
        rd = json.loads(body)["request_data"]
        calls.append(url)
        if "incidents" in url:
            rows = incidents or []
            return 200, json.dumps({"reply": {"total_count": len(rows), "result_count": len(rows),
                                              "incidents": rows}})
        page = alerts[rd["search_from"]:rd["search_to"]]
        return 200, json.dumps({"reply": {"total_count": len(alerts), "result_count": len(page),
                                          "alerts": page}})
    return fetcher, calls


async def _setup(SessionLocal, *, hostname="web-01", techniques=("T1003.001",),
                 config_extra=None):
    from models import Agent, Result, Run
    from security.credentials import CredentialStore
    from sqlalchemy import select

    config = {"fqdn": "api-t.xdr.us.paloaltonetworks.com", "api_key_id": "1"}
    config.update(config_extra or {})
    async with SessionLocal() as db:
        await CredentialStore(db).put_integration(
            name="acme", kind="xsiam", plaintext_secret="k" * 40, config=config)
        run = (await db.execute(select(Run))).scalars().first()
        if hostname:
            db.add(Agent(agent_id="agent-1", hostname=hostname, os="linux"))
            run.target = "agent-1"
        for i, r in enumerate((await db.execute(select(Result))).scalars().all()):
            r.mitre_technique = techniques[i % len(techniques)]
        await db.commit()


async def _reconcile(SessionLocal, fetcher):
    from connectors.service import reconcile_run
    from models import Result, Run
    from sqlalchemy import select

    async with SessionLocal() as db:
        run = (await db.execute(select(Run))).scalars().first()
        results = (await db.execute(select(Result))).scalars().all()
        out = await reconcile_run(db, run, results, connector_kind="xsiam", fetcher=fetcher)
        await db.refresh(run)
        return out.summary, run


def test_single_page_pull_is_one_query(session_factory):
    _seed(session_factory, results=2, threshold=ACC, primary_kpi="Detection Accuracy")
    asyncio.run(_setup(session_factory, techniques=("T1003.001", "T1059")))
    fetcher, calls = _tenant([_alert("a"), _alert("b", technique="T1059")])
    summary, _ = asyncio.run(_reconcile(session_factory, fetcher))
    assert len(calls) == 1
    assert summary["queries_issued"] == 1


def test_three_pages_truncated_is_three_queries(session_factory):
    _seed(session_factory, results=2, threshold=ACC, primary_kpi="Detection Accuracy")
    asyncio.run(_setup(session_factory, techniques=("T1003.001", "T1059"),
                       config_extra={"max_pages": 3}))
    alerts = [_alert(f"x{i}") for i in range(310)]
    fetcher, calls = _tenant(alerts)
    summary, _ = asyncio.run(_reconcile(session_factory, fetcher))
    assert len(calls) == 3
    assert summary["truncated"] is True
    assert summary["queries_issued"] == 3


def test_correlation_scenario_incident_read_adds_one(session_factory):
    _seed(session_factory, results=2, threshold=CORR, primary_kpi="Cross-Source Correlation Rate")
    asyncio.run(_setup(session_factory, techniques=("T1003.001", "T1059")))
    fetcher, calls = _tenant(
        [_alert("a"), _alert("b", technique="T1059")],
        incidents=[{"incident_id": "104", "alert_count": 2, "hosts": ["web-01:aef3"],
                    "creation_time": _epoch_ms(datetime.utcnow())}])
    summary, _ = asyncio.run(_reconcile(session_factory, fetcher))
    assert any("incidents" in c for c in calls), "fallback incidents read must be attempted"
    assert summary["queries_issued"] == 1 + 1
