"""reconcile_run scopes alerts to the run's host and reports truncation (audit items 1, 2)."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

from tests.connectors.test_verification_service import _seed

def _alert(ext, *, host, seconds, technique="T1003.001"):
    return {"alert_id": ext, "name": f"alert {ext}", "host_name": host, "source": "XDR BIOC",
            "mitre_technique_ids": [technique],
            "detection_timestamp": int(((datetime.utcnow() - timedelta(minutes=10))
                                        + timedelta(seconds=seconds)).timestamp() * 1000)}


def _fetcher(alerts, *, total_count=None):
    def fetcher(method, url, headers, body, timeout):
        rd = json.loads(body)["request_data"]
        page = alerts[rd["search_from"]:rd["search_to"]]
        return 200, json.dumps({"reply": {
            "total_count": len(alerts) if total_count is None else total_count,
            "result_count": len(page), "alerts": page}})
    return fetcher


async def _setup(SessionLocal, *, hostname="web-01", target="agent-1", max_pages=None):
    from models import Agent, Result, Run
    from security.credentials import CredentialStore
    from sqlalchemy import select

    async with SessionLocal() as db:
        config = {"fqdn": "api-t.xdr.us.paloaltonetworks.com", "api_key_id": "1"}
        if max_pages is not None:
            config["max_pages"] = max_pages
        await CredentialStore(db).put_integration(
            name="acme", kind="xsiam", plaintext_secret="k" * 40, config=config)
        if hostname:
            db.add(Agent(agent_id="agent-1", hostname=hostname, os="linux"))
        run = (await db.execute(select(Run))).scalars().first()
        run.target = target
        for r in (await db.execute(select(Result))).scalars().all():
            r.mitre_technique = "T1003.001"
        await db.commit()


async def _reconcile(SessionLocal, fetcher):
    from connectors.service import reconcile_run
    from models import Result, Run
    from sqlalchemy import select

    async with SessionLocal() as db:
        run = (await db.execute(select(Run))).scalars().first()
        results = (await db.execute(select(Result))).scalars().all()
        out = await reconcile_run(db, run, results, connector_kind="xsiam", fetcher=fetcher)
        return out.summary


def test_an_earlier_alert_from_another_host_is_not_credited(session_factory):
    _seed(session_factory, results=1)
    asyncio.run(_setup(session_factory))
    summary = asyncio.run(_reconcile(session_factory, _fetcher([
        _alert("other", host="db-02.corp.local", seconds=10),
        _alert("mine", host="WEB-01.corp.local", seconds=45),
    ])))
    assert summary["host_scope"] == {"host": "web-01", "eligible": 1,
                                     "excluded_other_host": 1, "without_host": 0}
    assert [v["alert_external_id"] for v in summary["verdicts"]] == ["mine"]
    assert abs(summary["verdicts"][0]["mttd_seconds"] - 45.0) < 2.0
    assert "unscoped" not in summary.get("warning", "")


def test_a_run_without_a_resolvable_host_is_reconciled_unscoped_and_says_so(session_factory):
    _seed(session_factory, results=1)
    asyncio.run(_setup(session_factory, hostname=None, target=None))
    summary = asyncio.run(_reconcile(session_factory, _fetcher([
        _alert("anyone", host="db-02", seconds=10),
    ])))
    assert summary["host_scope"]["host"] is None
    assert [v["alert_external_id"] for v in summary["verdicts"]] == ["anyone"]
    assert "unscoped" in summary["warning"]


def test_truncation_is_reported_as_a_floor(session_factory):
    _seed(session_factory, results=1)
    asyncio.run(_setup(session_factory, max_pages=1))
    alerts = [_alert(str(i), host="web-01", seconds=i, technique="T1999") for i in range(100)]
    summary = asyncio.run(_reconcile(session_factory, _fetcher(alerts, total_count=150)))
    assert summary["truncated"] is True
    assert summary["truncation"]["total_count"] == 150
    assert summary["observed"] == 0
    assert "floor, not a measurement" in summary["warning"]
    assert any("150 alerts" in w for w in summary["warnings"])
