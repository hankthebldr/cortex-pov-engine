"""Sprint 2 end-to-end through reconcile_run: accuracy and correlation become
measured values with the honesty guard applied, and the completion-time score
never erases a real measurement."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

from tests.connectors.test_verification_service import _seed

ACC = {"kpi": "Detection Accuracy", "op": "≥", "value": 50, "unit": "%"}
CORR = {"kpi": "Cross-Source Correlation Rate", "op": "≥", "value": 80, "unit": "%"}


def _alert(ext, *, host="web-01", seconds=30, technique="T1003.001", incident=None):
    a = {"alert_id": ext, "name": f"alert {ext}", "host_name": host, "source": "XDR BIOC",
         "mitre_technique_ids": [technique],
         "detection_timestamp": int(((datetime.utcnow() - timedelta(minutes=10))
                                     + timedelta(seconds=seconds)).timestamp() * 1000)}
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


async def _setup(SessionLocal, *, hostname="web-01", techniques=("T1003.001",)):
    from models import Agent, Result, Run
    from security.credentials import CredentialStore
    from sqlalchemy import select

    async with SessionLocal() as db:
        await CredentialStore(db).put_integration(
            name="acme", kind="xsiam", plaintext_secret="k" * 40,
            config={"fqdn": "api-t.xdr.us.paloaltonetworks.com", "api_key_id": "1"})
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


def test_accuracy_is_measured_and_passes_the_bar(session_factory):
    """2 seeded, technique T1003.001 and T1059; one alert for the first → 50 % ≥ 50 %."""
    _seed(session_factory, results=2, threshold=ACC, primary_kpi="Detection Accuracy")
    asyncio.run(_setup(session_factory, techniques=("T1003.001", "T1059")))
    fetcher, _ = _tenant([_alert("a")])
    summary, run = asyncio.run(_reconcile(session_factory, fetcher))
    m = summary["measurement"]
    assert m["family"] == "accuracy" and m["value"] == 50.0 and m["observed"] == 1 and m["seeded"] == 2
    assert run.tc_verdict == "pass"
    assert run.tc_verdict_detail["primary"]["actual"] == 50.0


def test_accuracy_fails_the_bar_when_the_run_was_scoped_and_complete(session_factory):
    _seed(session_factory, results=4, threshold=ACC, primary_kpi="Detection Accuracy")
    asyncio.run(_setup(session_factory, techniques=("T1003.001", "T1059", "T1021", "T1078")))
    fetcher, _ = _tenant([_alert("a")])
    summary, run = asyncio.run(_reconcile(session_factory, fetcher))
    assert summary["measurement"]["value"] == 25.0
    assert run.tc_verdict == "fail"


def test_zero_matched_stays_pending_not_fail(session_factory):
    _seed(session_factory, results=2, threshold=ACC, primary_kpi="Detection Accuracy")
    asyncio.run(_setup(session_factory))
    fetcher, _ = _tenant([_alert("a", technique="T9999")])
    summary, run = asyncio.run(_reconcile(session_factory, fetcher))
    assert summary["measurement"]["value"] == 0.0
    assert "no alert matched" in summary["measurement"]["withheld"]
    assert run.tc_verdict == "pending"


def test_an_unscoped_pull_cannot_produce_an_accuracy_pass(session_factory):
    _seed(session_factory, results=1, threshold=ACC, primary_kpi="Detection Accuracy")
    asyncio.run(_setup(session_factory, hostname=None))
    fetcher, _ = _tenant([_alert("a", host="someone-else")])
    summary, run = asyncio.run(_reconcile(session_factory, fetcher))
    assert summary["measurement"]["value"] == 100.0
    assert "unscoped" in summary["measurement"]["withheld"]
    assert run.tc_verdict == "pending"


def test_correlation_uses_incident_ids_on_the_alerts_when_present(session_factory):
    _seed(session_factory, results=3, threshold=CORR, primary_kpi="Cross-Source Correlation Rate")
    asyncio.run(_setup(session_factory, techniques=("T1003.001", "T1059", "T1021")))
    fetcher, calls = _tenant([_alert("a", incident="I1"), _alert("b", technique="T1059", incident="I1"),
                              _alert("c", technique="T1021", incident="I1")])
    summary, run = asyncio.run(_reconcile(session_factory, fetcher))
    m = summary["measurement"]
    assert m["basis"] == "alert_incident_ids" and m["value"] == 100.0 and m["alerts"] == 3
    assert run.tc_verdict == "pass"
    assert not any("incidents" in c for c in calls), "no incidents read when the alerts carry ids"


def test_correlation_falls_back_to_host_scoped_incidents(session_factory):
    _seed(session_factory, results=2, threshold=CORR, primary_kpi="Cross-Source Correlation Rate")
    asyncio.run(_setup(session_factory, techniques=("T1003.001", "T1059")))
    fetcher, calls = _tenant(
        [_alert("a"), _alert("b", technique="T1059")],
        incidents=[{"incident_id": "104", "alert_count": 6, "hosts": ["web-01:aef3"],
                    "creation_time": int(datetime.utcnow().timestamp() * 1000)},
                   {"incident_id": "105", "alert_count": 3, "hosts": ["db-02:1"],
                    "creation_time": int(datetime.utcnow().timestamp() * 1000)}])
    summary, run = asyncio.run(_reconcile(session_factory, fetcher))
    m = summary["measurement"]
    assert any("incidents" in c for c in calls)
    assert summary["incidents_considered"] == 1          # db-02's incident excluded
    assert m["basis"] == "host_incidents" and m["value"] == 100.0 and m["alerts"] == 6
    assert run.tc_verdict == "pass"


def test_a_completion_rescore_keeps_the_last_measurement(session_factory):
    from connectors.service import score_run_for_run
    from models import Run
    from sqlalchemy import select

    _seed(session_factory, results=2, threshold=ACC, primary_kpi="Detection Accuracy")
    asyncio.run(_setup(session_factory, techniques=("T1003.001", "T1059")))
    fetcher, _ = _tenant([_alert("a")])
    asyncio.run(_reconcile(session_factory, fetcher))

    async def _rescore():
        async with session_factory() as db:
            run = (await db.execute(select(Run))).scalars().first()
            await score_run_for_run(db, run, source="complete")
            return run.tc_verdict, run.tc_verdict_detail
    verdict, detail = asyncio.run(_rescore())
    assert detail["measurement"]["value"] == 50.0
    # and the verdict does not flip back to pending because this caller held no pull
    assert verdict == "pass"
    assert detail["primary"]["actual"] == 50.0
