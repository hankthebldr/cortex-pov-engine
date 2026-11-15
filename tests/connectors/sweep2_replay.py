"""The sweep of issue #346, rebuilt in an in-memory SimCore.

``tests/fixtures/sweep2_replay/sweep2.json`` holds the issue's alert table and
the seventeen runs that raised it. :class:`Sweep` seeds those runs the way a
launch does (one ``Result`` per expected detection of the real scenario file,
enriched from the real TTP card), serves the alerts from an injected fetcher in
the tenant's wire shape, and reconciles each run through the production
``reconcile_run``. No tenant, no network.

Times are shifted so the sweep ended a few minutes ago: reconcile pulls up to
"now", exactly as the lab's reconcile did right after the sweep.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "sweep2_replay" / "sweep2.json"
AGENT_ID = "agent-sweep"
INTEGRATION = "replay"
_SCENARIO_ID_RE = re.compile(r"^scenario_id:\s*[\"']?([A-Za-z0-9-]+)", re.MULTILINE)


def _epoch_ms(naive_utc: datetime) -> int:
    return int(naive_utc.replace(tzinfo=timezone.utc).timestamp() * 1000)


def _scenario_documents(wanted: set[str]) -> dict[str, dict]:
    docs: dict[str, dict] = {}
    for path in sorted((REPO / "scenarios").rglob("*.yml")):
        if path.name.startswith("_"):
            continue
        text = path.read_text()
        found = _SCENARIO_ID_RE.search(text)
        if found and found.group(1) in wanted:
            docs[found.group(1)] = yaml.safe_load(text)
    missing = wanted - set(docs)
    assert not missing, f"scenario files not found for {sorted(missing)}"
    return docs


class Sweep:
    def __init__(self) -> None:
        self.data = json.loads(FIXTURE.read_text())
        self.host: str = self.data["host"]
        day = datetime.fromisoformat(self.data["day"])
        self._day = day
        last = max(self._clock(r["completed"]) for r in self.data["runs"])
        self._shift = (datetime.utcnow().replace(microsecond=0)
                       - timedelta(minutes=5)) - last
        self.runs = {r["run"]: r for r in self.data["runs"]}
        self.alerts = {a["alert_id"]: a for a in self.data["alerts"]}

    # ── times and ids ───────────────────────────────────────────────────────

    def _clock(self, hhmmss: str) -> datetime:
        h, m, s = (int(x) for x in hhmmss.split(":"))
        return self._day + timedelta(hours=h, minutes=m, seconds=s)

    def when(self, hhmmss: str) -> datetime:
        """A fixture wall-clock time as the (shifted) naive-UTC test time."""
        return self._clock(hhmmss) + self._shift

    @staticmethod
    def run_id(short: str) -> str:
        return f"{short}-0000-4000-8000-000000000000"

    @staticmethod
    def short(run_id: str) -> str:
        return run_id.split("-", 1)[0]

    # ── the tenant ──────────────────────────────────────────────────────────

    def tenant_alerts(self) -> list[dict[str, Any]]:
        """The alerts in the alerts API's wire shape."""
        rows = []
        for a in self.data["alerts"]:
            row: dict[str, Any] = {
                "alert_id": a["alert_id"], "name": a["name"], "source": a["source"],
                "mitre_technique_id_and_name": list(a["techniques"]),
                "detection_timestamp": _epoch_ms(self.when(a["time"])),
            }
            if a.get("host", self.host) is not None:
                row["host_name"] = self.host
            if a["rule_id"]:
                row["matching_service_rule_id"] = a["rule_id"]
            rows.append(row)
        rows.sort(key=lambda r: r["detection_timestamp"])
        return rows

    def fetcher(self):
        alerts = self.tenant_alerts()

        def fetch(method, url, headers, body, timeout):
            request = json.loads(body)["request_data"]
            if "incidents" in url:
                return 200, json.dumps({"reply": {"total_count": 0, "result_count": 0,
                                                  "incidents": []}})
            lo = hi = None
            for f in request.get("filters") or []:
                if f.get("field") == "creation_time" and f.get("operator") == "gte":
                    lo = f["value"]
                if f.get("field") == "creation_time" and f.get("operator") == "lte":
                    hi = f["value"]
            window = [a for a in alerts
                      if (lo is None or a["detection_timestamp"] >= lo)
                      and (hi is None or a["detection_timestamp"] <= hi)]
            page = window[request.get("search_from", 0):request.get("search_to", 100)]
            return 200, json.dumps({"reply": {"total_count": len(window),
                                              "result_count": len(page), "alerts": page}})
        return fetch

    # ── SimCore ─────────────────────────────────────────────────────────────

    async def seed(self, SessionLocal) -> None:
        from engine.ttp_catalog import TtpCatalog
        from models import Agent, Result, Run, Scenario
        from security.credentials import CredentialStore

        catalog = TtpCatalog()
        catalog.load(str(REPO / "detection_scanner" / "ttps"))
        docs = _scenario_documents({r["scenario"] for r in self.data["runs"]})

        async with SessionLocal() as db:
            await CredentialStore(db).put_integration(
                name=INTEGRATION, kind="xsiam", plaintext_secret="k" * 40,
                config={"fqdn": "api-replay.xdr.us.paloaltonetworks.com", "api_key_id": "1"})
            db.add(Agent(agent_id=AGENT_ID, hostname=self.host, os="linux"))
            for sid, doc in docs.items():
                db.add(Scenario(
                    scenario_id=sid, name=doc["name"], version=str(doc.get("version", "1.0")),
                    status="active", plane=doc["plane"],
                    uc_ref=doc["uc_ref"], tc_ref=doc["tc_ref"],
                    uc_name=doc.get("uc_name", ""), tc_name=doc.get("tc_name", ""),
                    mitre_tactic=doc.get("mitre_tactic", ""),
                    mitre_tactic_name=doc.get("mitre_tactic_name", ""),
                    mitre_technique=doc.get("mitre_technique", ""),
                    mitre_technique_name=doc.get("mitre_technique_name", ""),
                    steps=doc.get("steps") or [],
                    primary_kpi=doc.get("primary_kpi"), threshold=doc.get("threshold")))
            for r in self.data["runs"]:
                launched = self.when(r["launched"])
                db.add(Run(run_id=self.run_id(r["run"]), scenario_id=r["scenario"],
                           mode="pull", status="complete", target=AGENT_ID,
                           started_at=launched,
                           delivered_at=launched + timedelta(seconds=1),
                           completed_at=self.when(r["completed"])))
                doc = docs[r["scenario"]]
                for step in doc.get("steps") or []:
                    for det in step.get("expected_detections") or []:
                        card = catalog.find(det.get("ttp_ref"), det.get("detection_id"))
                        db.add(Result(
                            run_id=self.run_id(r["run"]), step_id=step.get("id"),
                            step_name=step.get("name", ""),
                            plane=det.get("plane", doc["plane"]),
                            signal_type=det.get("type", "BIOC"),
                            expected_detection=det.get("description", ""),
                            observed=False, executed_at=launched,
                            ttp_ref=det.get("ttp_ref"), detection_id=det.get("detection_id"),
                            mitre_technique=step.get("mitre_technique"),
                            expected_outcome=det.get("outcome", "detect"),
                            detection_kind=card.kind if card else None,
                            detection_name=card.name if card else None,
                            prevention_module=(card.module if card and card.kind == "prevention"
                                               else None)))
            await db.commit()

    async def reconcile(self, SessionLocal, short: str) -> dict[str, Any]:
        from connectors.service import reconcile_run
        from models import Result, Run
        from sqlalchemy import select

        async with SessionLocal() as db:
            run = (await db.execute(
                select(Run).where(Run.run_id == self.run_id(short)))).scalar_one()
            results = list((await db.execute(
                select(Result).where(Result.run_id == run.run_id))).scalars().all())
            out = await reconcile_run(db, run, results, connector_kind="xsiam",
                                      fetcher=self.fetcher())
            return out.summary

    async def reconcile_all(self, SessionLocal, order: Optional[list[str]] = None) -> dict:
        return {short: await self.reconcile(SessionLocal, short)
                for short in (order or list(self.runs))}

    async def credits(self, SessionLocal) -> list[dict[str, Any]]:
        """Every credited row: the run it is in, the alert that credited it, and
        the run that alert was raised in (the issue's own assignment)."""
        from models import Observation, Result
        from sqlalchemy import select

        async with SessionLocal() as db:
            rows = (await db.execute(
                select(Result).where(Result.observed.is_(True)))).scalars().all()
            obs = (await db.execute(
                select(Observation).where(Observation.result_id.is_not(None)))).scalars().all()
        by_row: dict[int, list] = {}
        for o in obs:
            by_row.setdefault(o.result_id, []).append(o)
        out = []
        for r in rows:
            crediting = next((o for o in by_row.get(r.id, ())
                              if o.observed_at == r.observed_at), None)
            alert_id = crediting.external_id if crediting else None
            raised_in = self.alerts[alert_id]["run"] if alert_id in self.alerts else None
            out.append({"run": self.short(r.run_id), "detection_id": r.detection_id,
                        "step_id": r.step_id, "alert_id": alert_id, "raised_in": raised_in,
                        "mttd_seconds": r.mttd_seconds,
                        "own_run": raised_in == self.short(r.run_id)})
        return sorted(out, key=lambda c: (c["run"], c["detection_id"] or ""))

    async def without_the_one_run_rule(self, SessionLocal) -> list[dict[str, Any]]:
        """What the matcher credits when every alert in a row's window is
        taken: the 1.2.0 behaviour, computed without touching the database."""
        from connectors.matcher import reconcile, scope_to_host
        from connectors.service import match_window_ends, window_by_type
        from connectors.xsiam import XsiamConnector
        from models import Result
        from sqlalchemy import select

        normalize = XsiamConnector()._normalize_alert
        alerts = [normalize(a) for a in self.tenant_alerts()]
        eligible, _ = scope_to_host(alerts, self.host)
        out = []
        async with SessionLocal() as db:
            for short in self.runs:
                results = list((await db.execute(select(Result).where(
                    Result.run_id == self.run_id(short)))).scalars().all())
                ends = await match_window_ends(db, self.run_id(short), results,
                                               window_seconds=3600)
                for v in reconcile(results, eligible, window_seconds=3600,
                                   only_unobserved=False,
                                   window_seconds_by_type=window_by_type(),
                                   window_end_by_result=ends):
                    if not v.matched:
                        continue
                    raised_in = self.alerts[v.alert_external_id]["run"]
                    out.append({"run": short, "alert_id": v.alert_external_id,
                                "raised_in": raised_in, "mttd_seconds": v.mttd_seconds,
                                "own_run": raised_in == short,
                                "matched_on": v.matched_on})
        return out
