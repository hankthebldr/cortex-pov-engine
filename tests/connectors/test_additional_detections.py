"""#346: real alerts on a run's own steps are credited or shown.

15 of the sweep's 25 host alerts came from XDR Agent modules ("Local Analysis
Malware", "Credential Gathering Protection", ...) on runs that expect no
Prevention row. The family gate dropped them, correctly: marking an expected
BIOC "detected" because an agent module alerted would be a false claim. But
they were dropped without a trace. They are now attributed to the run and the
step as ADDITIONAL detections: stored on the run, served, never a credit and
never part of Detection Accuracy.

Service level: the production ``reconcile_run`` over an in-memory database and
an injected fetcher.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest

from connectors.service import step_at

pytestmark = pytest.mark.usefixtures("non_utc_host")

HOST = "lab-endpoint-01"
AGENT = "agent-1"


def _now() -> datetime:
    return datetime.utcnow().replace(microsecond=0)


def _epoch_ms(naive_utc: datetime) -> int:
    return int(naive_utc.replace(tzinfo=timezone.utc).timestamp() * 1000)


def tenant_alert(ext: str, when: datetime, *, name: str = "Credential Gathering Protection",
                 source: str = "XDR Agent", techniques=("T1552.001",),
                 host: Optional[str] = HOST, action: Optional[str] = None) -> dict:
    row = {"alert_id": ext, "name": name, "source": source,
           "mitre_technique_id_and_name": list(techniques),
           "detection_timestamp": _epoch_ms(when)}
    if host is not None:
        row["host_name"] = host
    if action is not None:
        row["action_pretty"] = action
    return row


def fetcher(alerts: list[dict]):
    def fetch(method, url, headers, body, timeout):
        if "incidents" in url:
            return 200, json.dumps({"reply": {"total_count": 0, "incidents": []}})
        rd = json.loads(body)["request_data"]
        page = alerts[rd["search_from"]:rd["search_to"]]
        return 200, json.dumps({"reply": {"total_count": len(alerts),
                                          "result_count": len(page), "alerts": page}})
    return fetch


def seed(SessionLocal, *runs: dict, agent_host: Optional[str] = HOST) -> None:
    """Each run: ``{"id", "launched", "completed", "rows": [(step, type, technique)],
    "steps": [(step, reported)]}`` on one agent."""
    from models import Agent, Result, Run, Scenario, StepOutcome
    from security.credentials import CredentialStore

    async def _go():
        async with SessionLocal() as db:
            await CredentialStore(db).put_integration(
                name="acme", kind="xsiam", plaintext_secret="k" * 40,
                config={"fqdn": "api-t.xdr.us.paloaltonetworks.com", "api_key_id": "1"})
            if agent_host:
                db.add(Agent(agent_id=AGENT, hostname=agent_host, os="linux"))
            db.add(Scenario(
                scenario_id="SIM-EDR-900", name="T", plane="EDR", version="1.0",
                status="active", uc_ref="UCS-EDR-01", uc_name="X", tc_ref="TC-EDR-01",
                tc_name="Y", mitre_tactic="TA0006", mitre_tactic_name="Credential Access",
                mitre_technique="T1003", mitre_technique_name="OS Credential Dumping",
                steps=[], primary_kpi="Detection Accuracy",
                threshold={"kpi": "Detection Accuracy", "op": "≥", "value": 50, "unit": "%"}))
            for r in runs:
                db.add(Run(run_id=r["id"], scenario_id="SIM-EDR-900", mode="pull",
                           status="complete", target=AGENT if agent_host else None,
                           started_at=r["launched"],
                           delivered_at=r["launched"] + timedelta(seconds=1),
                           completed_at=r["completed"]))
                for step, signal_type, technique in r.get("rows", [("step-01", "BIOC", "T1003")]):
                    db.add(Result(run_id=r["id"], step_id=step, step_name=f"name of {step}",
                                  plane="EDR", signal_type=signal_type,
                                  expected_detection=f"expected on {step}", observed=False,
                                  executed_at=r["launched"], mitre_technique=technique,
                                  detection_id=f"det-{step}"))
                for step, reported in r.get("steps", []):
                    db.add(StepOutcome(run_id=r["id"], step_id=step, started_at=reported,
                                       exit_code=0, termination="exited"))
            await db.commit()

    asyncio.run(_go())


def reconcile(SessionLocal, run_id: str, alerts: list[dict]) -> dict:
    from connectors.service import reconcile_run
    from models import Result, Run
    from sqlalchemy import select

    async def _go():
        async with SessionLocal() as db:
            run = (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one()
            results = list((await db.execute(
                select(Result).where(Result.run_id == run_id))).scalars().all())
            out = await reconcile_run(db, run, results, connector_kind="xsiam",
                                      fetcher=fetcher(alerts))
            return out.summary

    return asyncio.run(_go())


def stored(SessionLocal, run_id: str) -> Optional[dict]:
    from models import Run
    from sqlalchemy import select

    async def _go():
        async with SessionLocal() as db:
            return (await db.execute(
                select(Run.alert_attribution).where(Run.run_id == run_id))).scalar_one()

    return asyncio.run(_go())


def one_run(session_factory, **extra) -> datetime:
    launched = _now() - timedelta(minutes=10)
    seed(session_factory, {"id": "run-a", "launched": launched,
                           "completed": launched + timedelta(minutes=2), **extra})
    return launched


# ── listed, not credited ────────────────────────────────────────────────────

def test_an_agent_module_alert_is_listed_on_the_run_and_credits_nothing(session_factory):
    launched = one_run(session_factory)
    alert = tenant_alert("1502424", launched + timedelta(seconds=30),
                         action="Detected (Reported)")

    summary = reconcile(session_factory, "run-a", [alert])

    assert summary["observed"] == 0 and summary["verdicts"] == []
    assert summary["additional_detections"] == [{
        "alert_external_id": "1502424", "alert_name": "Credential Gathering Protection",
        "alert_source": "XDR Agent", "techniques": ["T1552.001"], "severity": None,
        "action": "Detected (Reported)", "alert_outcome": "detected",
        "observed_at": (launched + timedelta(seconds=30)).isoformat(),
        "step_id": None, "step_name": None}]
    kept = stored(session_factory, "run-a")
    assert kept["additional"] == summary["additional_detections"]
    assert kept["host"] == HOST and kept["source"] == "xsiam:acme" and kept["reviewed_at"]


def test_additional_detections_stay_out_of_detection_accuracy(session_factory):
    """One expected row, credited; three agent alerts beside it. Accuracy is
    1 of 1 expected, not 4 of 4 and not 1 of 4."""
    launched = one_run(session_factory)
    alerts = [tenant_alert("own", launched + timedelta(seconds=20), name="Shadow read",
                           source="XDR BIOC", techniques=("T1003",))]
    alerts += [tenant_alert(f"agent-{i}", launched + timedelta(seconds=30 + i))
               for i in range(3)]

    summary = reconcile(session_factory, "run-a", alerts)

    assert len(summary["additional_detections"]) == 3
    assert (summary["total_expected"], summary["denominator"], summary["observed"]) == (1, 1, 1)
    assert summary["coverage_pct"] == 100.0
    assert summary["measurement"]["family"] == "accuracy"
    assert summary["measurement"]["value"] == 100.0
    assert summary["tc_verdict"] == "pass"


def test_an_alert_that_matches_an_expected_row_is_a_credit_not_an_extra(session_factory):
    """... and a second firing of it is a repeat of that credit, not an extra."""
    launched = one_run(session_factory)
    twice = [tenant_alert(ext, launched + timedelta(seconds=s), name="Shadow read",
                          source="XDR BIOC", techniques=("T1003",))
             for ext, s in (("first", 20), ("second", 25))]
    summary = reconcile(session_factory, "run-a", twice)
    assert summary["observed"] == 1 and summary["additional_detections"] == []


# ── only the run's own window, on its own host ──────────────────────────────

def test_only_alerts_inside_the_runs_window_on_its_host_are_listed(session_factory):
    launched = one_run(session_factory)
    completed = launched + timedelta(minutes=2)
    alerts = [
        tenant_alert("before-launch", launched - timedelta(seconds=5)),
        tenant_alert("during", launched + timedelta(seconds=40)),
        tenant_alert("settling", completed + timedelta(minutes=3)),     # inside the hour
        tenant_alert("no-host", launched + timedelta(seconds=41), host=None),
        tenant_alert("other-host", launched + timedelta(seconds=42), host="db-02"),
    ]
    summary = reconcile(session_factory, "run-a", alerts)
    assert [e["alert_external_id"] for e in summary["additional_detections"]] == [
        "during", "settling"]


def test_an_alert_past_the_settle_time_is_not_the_runs(session_factory):
    """The settle time is the default window (1 h) after the run ended, for
    every family. An expected Analytics row may wait a day because a key must
    still agree; an alert tied to the run by the clock alone may not."""
    launched = _now() - timedelta(hours=3)
    seed(session_factory, {"id": "run-a", "launched": launched,
                           "completed": launched + timedelta(minutes=2)})
    def abioc(ext, when):
        return tenant_alert(ext, when, name="An uncommon service was started",
                            source="XDR Analytics BIOC", techniques=("T1543.002",))

    alerts = [abioc("abioc-settling", launched + timedelta(minutes=40)),
              tenant_alert("agent-late", launched + timedelta(hours=2)),
              abioc("abioc-late", launched + timedelta(hours=2))]
    summary = reconcile(session_factory, "run-a", alerts)
    assert [e["alert_external_id"] for e in summary["additional_detections"]] == [
        "abioc-settling"]


def test_an_alert_raised_after_the_next_run_began_is_the_next_runs(session_factory):
    t0 = _now() - timedelta(minutes=20)
    seed(session_factory,
         {"id": "run-a", "launched": t0, "completed": t0 + timedelta(minutes=2)},
         {"id": "run-b", "launched": t0 + timedelta(minutes=3),
          "completed": t0 + timedelta(minutes=5)})
    alerts = [tenant_alert("in-a", t0 + timedelta(seconds=50)),
              tenant_alert("in-b", t0 + timedelta(minutes=4))]

    a = reconcile(session_factory, "run-a", alerts)
    b = reconcile(session_factory, "run-b", alerts)

    assert [e["alert_external_id"] for e in a["additional_detections"]] == ["in-a"]
    assert [e["alert_external_id"] for e in b["additional_detections"]] == ["in-b"]


def test_a_run_with_no_host_lists_nothing_and_stores_nothing(session_factory):
    """Nothing ties an alert to a run that has no endpoint to place it on."""
    launched = _now() - timedelta(minutes=10)
    seed(session_factory, {"id": "run-a", "launched": launched,
                           "completed": launched + timedelta(minutes=2)}, agent_host=None)
    summary = reconcile(session_factory, "run-a",
                        [tenant_alert("x", launched + timedelta(seconds=30))])
    assert "additional_detections" not in summary
    assert stored(session_factory, "run-a") is None


# ── the step ────────────────────────────────────────────────────────────────

def test_step_at_names_the_step_that_was_running():
    t = datetime(2026, 11, 15, 5, 42, 10)
    reported = [(t + timedelta(seconds=1), "step-01"), (t + timedelta(seconds=42), "step-02"),
                (t + timedelta(seconds=83), "step-03")]
    assert step_at(t, reported) == "step-01"                              # during step-01
    assert step_at(t + timedelta(seconds=30), reported) == "step-02"      # during step-02
    assert step_at(t + timedelta(seconds=42), reported) == "step-02"      # as it reported
    assert step_at(t + timedelta(seconds=200), reported) == "step-03"     # after the run
    assert step_at(t, []) is None                                         # no host record


def test_an_additional_detection_names_its_step_from_the_host_records(session_factory):
    launched = _now() - timedelta(minutes=10)
    seed(session_factory, {
        "id": "run-a", "launched": launched, "completed": launched + timedelta(seconds=90),
        "rows": [("step-01", "BIOC", "T1003"), ("step-02", "BIOC", "T1098.004")],
        "steps": [("step-01", launched + timedelta(seconds=2)),
                  ("step-02", launched + timedelta(seconds=45))]})
    alerts = [tenant_alert("during-02", launched + timedelta(seconds=30)),
              tenant_alert("after", launched + timedelta(seconds=120))]
    summary = reconcile(session_factory, "run-a", alerts)
    assert [(e["alert_external_id"], e["step_id"], e["step_name"])
            for e in summary["additional_detections"]] == [
        ("during-02", "step-02", "name of step-02"), ("after", "step-02", "name of step-02")]


# ── across reviews ──────────────────────────────────────────────────────────

def test_a_listed_alert_survives_a_later_pull_that_no_longer_returns_it(session_factory):
    """The tenant ages alerts out. A later, emptier pull is not evidence that
    the alert was never raised."""
    launched = one_run(session_factory)
    reconcile(session_factory, "run-a", [tenant_alert("aged", launched + timedelta(seconds=30))])
    again = reconcile(session_factory, "run-a",
                      [tenant_alert("new", launched + timedelta(seconds=50))])
    assert [e["alert_external_id"] for e in again["additional_detections"]] == ["aged", "new"]


def test_a_review_does_not_list_the_same_alert_twice(session_factory):
    launched = one_run(session_factory)
    alerts = [tenant_alert("same", launched + timedelta(seconds=30))]
    reconcile(session_factory, "run-a", alerts)
    again = reconcile(session_factory, "run-a", alerts)
    assert [e["alert_external_id"] for e in again["additional_detections"]] == ["same"]
