"""The EAL relay: a beacon step that calls the SimCore-only EAL CLI (#325).

71 scenario files run ``python3 -m scripts.eal_simulator.cli`` as an ordinary
agent step. That CLI exists only inside SimCore, so on a beacon the step died
with ``ModuleNotFoundError``. A relay-capable beacon now runs the step through
``POST /api/eal/relay``: SimCore executes collector steps itself, with the same
executor and target resolution as the in-process ``channel: eal`` path, and
returns primitive network operations for the network-shape plugins.

Everything here drives the real launch, the real task and the real endpoint.
No test reaches a host: the collector client is a recording stub and network
steps are only ever rendered (``tests/conftest.py`` fails any test that
resolves or connects off loopback).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

import pytest
from sqlalchemy import select

FAKE_KEY = "fake-collector-key-for-tests-0000"
TARGET_URL = "https://api-acme.example.test/logs/v1/event"
ATTACKER_URL = "https://attacker.example.test/steal"
AGENT = "agent-relay"
RELAY_CAPS = ["shell", "identity-harness", "artifact-fetch", "eal-relay"]
OLD_CAPS = ["shell", "identity-harness", "artifact-fetch"]
LIVE = {"consent": {"simulation_authorized": True, "c2_authorized": True},
        "authorized_by": "tester"}

EAL_COMMAND = (
    'C=$(mktemp -d /tmp/cortexsim.XXXXXX) || exit 1; trap \'rm -rf "$C"\' EXIT\n'
    'cat > "$C/c.yml" <<YML\n'
    "campaign_id: CMP-TEST-001\n"
    "YML\n"
    'python3 -m scripts.eal_simulator.cli run "$C/c.yml" --live\n'
)


def _campaign(*steps: dict, **top) -> str:
    """A campaign file as a scenario heredoc writes it. It claims its own
    consent, as the corpus files do; the relay must not read those fields."""
    import yaml

    doc = {
        "campaign_id": "CMP-TEST-001", "name": "relay test",
        "authorized_by": "someone-on-the-target", "simulation_authorized": True,
        "c2_authorized": True, "target_allowlist": [], "dry_run": False,
        "steps": [{"step_id": f"step-{i + 1:02d}", **s} for i, s in enumerate(steps)],
    }
    doc.update(top)
    return yaml.safe_dump(doc)


M365 = {"plugin": "m365_activity_emitter", "params": {}}


@pytest.fixture
def client(session_factory):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.agents import router as agents_router
    from api.connectors import router as connectors_router, runs_reconcile_router
    from api.eal import router as eal_router
    from api.runs import router as runs_router
    from database import get_db

    async def _get_db():
        async with session_factory() as s:
            yield s

    app = FastAPI()
    for r in (agents_router, connectors_router, runs_reconcile_router, runs_router, eal_router):
        app.include_router(r, prefix="/api")
    app.dependency_overrides[get_db] = _get_db
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_orchestrator():
    from engine.orchestrator import orchestrator

    def _reset():
        orchestrator._aborted.clear()
        orchestrator._queue.clear()
        orchestrator.reset_liveness()

    _reset()
    yield
    _reset()


class _RecordingClient:
    def __init__(self, status_code: int = 202):
        self.requests: list[dict] = []
        self.status_code = status_code

    async def post(self, url: str, *, headers=None, content=None):
        self.requests.append({"url": url, "headers": dict(headers or {}), "content": content})

        class _R:
            status_code = self.status_code

        return _R()

    async def aclose(self) -> None:
        pass


class _Recorder:
    def __init__(self) -> None:
        self.status_code = 202
        self.clients: list[_RecordingClient] = []
        self.authorization: list = []

    @property
    def posts(self) -> list[dict]:
        return [r for c in self.clients for r in c.requests]


@pytest.fixture
def recorder(monkeypatch) -> _Recorder:
    """Every client a collector emitter builds, and the key the real one carried."""
    from eal_simulator.analytics_emitter import AnalyticsLogEmitter

    rec = _Recorder()
    original = AnalyticsLogEmitter._build_client

    def _build(self, params):
        real = original(self, params)
        rec.authorization.append(real.headers.get("authorization"))
        stub = _RecordingClient(rec.status_code)
        rec.clients.append(stub)
        return stub

    monkeypatch.setattr(AnalyticsLogEmitter, "_build_client", _build)
    return rec


def _launch(session_factory, *, caps=RELAY_CAPS, target_plugins=("m365_activity_emitter",),
            key=FAKE_KEY, with_target=True, eal_step=None, **launch_kw):
    """Seed a two-step scenario (one plain step, one EAL CLI step), an agent and
    a collector target, then launch it in pull mode. Returns the LaunchResult."""
    from engine.collector_targets import upsert
    from engine.orchestrator import orchestrator
    from models import Agent, Scenario

    async def _go():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id="SIM-NDR-951", name="T", version="1.0", status="active",
                plane="NDR", uc_ref="UCS-NDR-01", uc_name="x", tc_ref="TC-NDR-01",
                tc_name="y", mitre_tactic="TA0011", mitre_tactic_name="Command and Control",
                mitre_technique="T1071.001", mitre_technique_name="Web Protocols",
                execution_identity={"default": "root"}, push_supported=True,
                pull_supported=True,
                steps=[
                    {"id": "step-01", "name": "plain", "identity": "root", "command": "id",
                     "mitre_technique": "T1082",
                     "expected_detections": [
                         {"plane": "EDR", "type": "BIOC", "description": "sysinfo",
                          "detection_id": "bioc-sysinfo"}]},
                    {"id": "step-02", "name": "eal", "identity": "root",
                     "command": EAL_COMMAND, "mitre_technique": "T1071.001",
                     "expected_detections": [
                         {"plane": "NDR", "type": "XQL", "description": "periodic beacon",
                          "detection_id": "xql-periodic-beacon"}],
                     **(eal_step or {})},
                ],
            ))
            db.add(Agent(agent_id=AGENT, hostname="lab-relay", os="linux",
                         capabilities=list(caps), interpreters=[],
                         registered_at=datetime.utcnow(), last_seen=datetime.utcnow(),
                         status="online"))
            await db.commit()
            if with_target:
                await upsert(
                    db, "m365-hec", kind="xsiam_http_collector", url=TARGET_URL,
                    dataset=None, auth_header="Authorization", auth_scheme="",
                    plugins=list(target_plugins), is_default=False, token=key,
                    clear_token=False)
            res = await orchestrator.launch(
                scenario_id="SIM-NDR-951", mode="pull", db=db,
                target_agent_id=AGENT, **launch_kw)
            assert res.success, res.error
            return res

    return asyncio.run(_go())


def _relay(client, run_id, *, step_id="step-02", agent_id=AGENT, verb="run",
           campaign_yaml=None, plugin=None, live=True):
    return client.post("/api/eal/relay", json={
        "agent_id": agent_id, "run_id": run_id, "step_id": step_id, "verb": verb,
        "campaign_yaml": campaign_yaml, "plugin": plugin, "live": live,
    })


def _rows(session_factory, step_id="step-02"):
    from models import Result

    async def _go():
        async with session_factory() as db:
            return (await db.execute(
                select(Result).where(Result.step_id == step_id))).scalars().all()

    return asyncio.run(_go())


def _task_steps(client) -> dict[str, dict]:
    task = client.get(f"/api/agents/{AGENT}/tasks").json()["task"]
    return {s["id"]: s for s in task["steps"]}


# ---------------------------------------------------------------------------
# Launch: which steps the task marks
# ---------------------------------------------------------------------------


def test_launch_marks_only_the_eal_cli_step_for_a_relay_capable_beacon(client, session_factory):
    res = _launch(session_factory)
    steps = _task_steps(client)
    assert steps["step-02"].get("eal_relay") is True
    assert "eal_relay" not in steps["step-01"]
    # The command is delivered untouched: the beacon rewrites the one call.
    assert steps["step-02"]["command"] == EAL_COMMAND
    assert not [w for w in res.warnings if isinstance(w, dict)
                and w.get("code") == "EAL_RELAY_UNAVAILABLE"]


def test_launch_warns_when_the_beacon_cannot_relay(client, session_factory):
    from models import Run

    res = _launch(session_factory, caps=OLD_CAPS)
    (warning,) = [w for w in res.warnings if isinstance(w, dict)
                  and w.get("code") == "EAL_RELAY_UNAVAILABLE"]
    assert warning["agent_id"] == AGENT
    assert warning["steps"] == ["step-02"]
    assert "eal_relay" not in _task_steps(client)["step-02"]

    async def _output():
        async with session_factory() as db:
            return (await db.execute(select(Run))).scalar_one().output or ""

    assert "EAL_RELAY_UNAVAILABLE" in asyncio.run(_output())
    # No grant was written, so the endpoint refuses the step outright.
    r = _relay(client, res.run_id, campaign_yaml=_campaign(M365))
    assert r.status_code == 403, r.text


def test_a_marked_step_no_longer_needs_python_on_the_target(client, session_factory):
    """SIM-CDR-030 declares Python on every step, for the EAL CLI alone. Once
    the beacon runs that call itself, a target without Python can run the step;
    a step that uses Python for anything else still says so."""
    _launch(session_factory, eal_step={"requires_interpreters": ["python"]})
    assert "requires_interpreters" not in _task_steps(client)["step-02"]


def test_a_marked_step_that_uses_python_elsewhere_still_requires_it(client, session_factory):
    _launch(session_factory, eal_step={
        "requires_interpreters": ["python"],
        "command": "python3 -c 'print(1)'\n" + EAL_COMMAND})
    assert _task_steps(client)["step-02"]["requires_interpreters"] == ["python"]


# ---------------------------------------------------------------------------
# Collector steps run on SimCore
# ---------------------------------------------------------------------------


def test_relay_collector_step_delivers_to_instance_target(client, session_factory, recorder):
    res = _launch(session_factory, **LIVE)
    r = _relay(client, res.run_id, campaign_yaml=_campaign(M365))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["exit_code"] == 0, body
    assert body["ops"] == [] and body["allowlist"] == []

    assert recorder.posts, "a live collector step must POST"
    assert {p["url"] for p in recorder.posts} == {TARGET_URL}
    assert recorder.authorization == [FAKE_KEY]

    printed = json.loads(body["stdout"])
    assert printed["dry_run"] is False
    assert printed["status"] == "complete"
    assert printed["step_results"][0]["events_emitted"] >= 1
    assert printed["delivery"]["records_delivered"] >= 1
    # The key reaches the collector and nothing else.
    assert FAKE_KEY not in r.text

    # A step that delivered stays in the denominator, as an in-process one does.
    assert [(x.outcome, x.outcome_reason) for x in _rows(session_factory)] == [(None, None)]


def test_relay_collector_that_refuses_every_record_is_not_delivered(client, session_factory, recorder):
    """A collector answering 401 accepted nothing. The step still exits as the
    CLI does, and its rows say nothing was delivered instead of reading a miss."""
    recorder.status_code = 401
    res = _launch(session_factory, **LIVE)
    body = _relay(client, res.run_id, campaign_yaml=_campaign(M365)).json()
    assert recorder.posts, "the live step must have tried"
    printed = json.loads(body["stdout"])
    assert printed["dry_run"] is False
    assert printed["delivery"]["delivery_verdict"] == "not_delivered"
    assert [(x.outcome, x.outcome_reason) for x in _rows(session_factory)] == [
        ("not_executed", "not_delivered")]


def test_relay_without_consent_is_dry_and_rows_not_executed(client, session_factory, recorder):
    res = _launch(session_factory)  # no consent, no authorized_by
    r = _relay(client, res.run_id, campaign_yaml=_campaign(M365))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["exit_code"] == 0, body
    assert recorder.posts == []

    printed = json.loads(body["stdout"])
    assert printed["dry_run"] is True
    assert printed["relay"]["code"] == "EAL_DRY_RUN_NO_CONSENT"

    assert [(x.outcome, x.outcome_reason) for x in _rows(session_factory)] == [
        ("not_executed", "EAL_DRY_RUN_NO_CONSENT")]
    # The plain step beside it is untouched.
    assert [(x.outcome, x.outcome_reason) for x in _rows(session_factory, "step-01")] == [
        (None, None)]


def test_relay_never_sends_stored_key_to_campaign_supplied_url(client, session_factory, recorder):
    """The campaign text comes from the target. Naming its own collector, header
    or key must not move the stored key off the stored destination."""
    hostile = {"plugin": "m365_activity_emitter", "params": {
        "collector_url": ATTACKER_URL, "auth_header": "X-Leak", "auth_scheme": "Bearer",
        "auth_token": "attacker-chosen"}}
    res = _launch(session_factory, **LIVE)
    r = _relay(client, res.run_id, campaign_yaml=_campaign(
        hostile, target_allowlist=["attacker.example.test"]))
    assert r.status_code == 200, r.text
    assert recorder.posts, "the stored target still receives the records"
    assert {p["url"] for p in recorder.posts} == {TARGET_URL}
    assert recorder.authorization == [FAKE_KEY]
    assert FAKE_KEY not in r.text


def test_relay_with_no_stored_target_for_the_plugin_sends_nothing(client, session_factory, recorder):
    """No instance target serves the plugin, so the campaign's own collector_url
    is all there is. SimCore does not POST there, with or without a key."""
    hostile = {"plugin": "m365_activity_emitter", "params": {"collector_url": ATTACKER_URL}}
    res = _launch(session_factory, target_plugins=("k8s_audit_emitter",), **LIVE)
    r = _relay(client, res.run_id, campaign_yaml=_campaign(
        hostile, target_allowlist=["attacker.example.test"]))
    assert r.status_code == 200, r.text
    assert recorder.posts == []
    assert recorder.authorization in ([], [None])
    printed = json.loads(r.json()["stdout"])
    assert printed["dry_run"] is True
    assert printed["relay"]["code"] == "EAL_DRY_RUN_TARGET_UNRESOLVED"
    assert FAKE_KEY not in r.text
    assert [x.outcome for x in _rows(session_factory)] == ["not_executed"]


# ---------------------------------------------------------------------------
# Network-shape steps come back as operations for the beacon
# ---------------------------------------------------------------------------

C2 = {"plugin": "c2_http_beacon", "params": {
    "target_url": "http://c2.example.test:8080/checkin", "iterations": 4,
    "sleep_seconds": 2, "jitter_pct": 25, "method": "GET", "dga_query_param": True}}
DNS = {"plugin": "dns_tunnel_exfil", "params": {
    "base_domain": "exfil.example.test", "chunks": 3, "sleep_seconds": 0.5,
    "query_type": "TXT", "resolver": "127.0.0.1:5353"}}


def _destination_allowed(op: dict, allowlist: list[str]) -> bool:
    from urllib.parse import urlparse

    if op["op"] == "sleep":
        return True
    if op["op"] == "http":
        u = urlparse(op["url"])
        return f"{u.hostname}:{u.port or {'http': 80, 'https': 443}[u.scheme]}" in allowlist
    if op["op"] == "tcp":
        return f"{op['host']}:{op['port']}" in allowlist
    if op["op"] == "dns":
        names = [a[4:] for a in allowlist if a.startswith("dns:")]
        named = any(op["qname"] == n or op["qname"].endswith("." + n) for n in names)
        return named and (not op.get("resolver") or op["resolver"] in allowlist)
    return False


def test_relay_wire_step_returns_ops_inside_allowlist(client, session_factory, recorder):
    res = _launch(session_factory, **LIVE)
    r = _relay(client, res.run_id, campaign_yaml=_campaign(
        C2, DNS, target_allowlist=["c2.example.test", "exfil.example.test", "127.0.0.1"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["exit_code"] == 0, body

    ops = body["ops"]
    kinds = [o["op"] for o in ops]
    # 4 beacons with 3 sleeps between, then 3 queries with 2 sleeps between.
    assert kinds == ["http", "sleep", "http", "sleep", "http", "sleep", "http",
                     "dns", "sleep", "dns", "sleep", "dns"]
    assert sorted(body["allowlist"]) == [
        "127.0.0.1:5353", "c2.example.test:8080", "dns:exfil.example.test"]
    assert all(_destination_allowed(o, body["allowlist"]) for o in ops), ops

    beacon = ops[0]
    assert beacon["method"] == "GET"
    assert beacon["url"].startswith("http://c2.example.test:8080/checkin?q=")
    assert beacon["headers"]["X-Simulation-Run-ID"].startswith("cortexsim-")
    assert "CortexSim" in beacon["headers"]["User-Agent"]
    query = ops[7]
    assert query["qtype"] == "TXT"
    assert query["qname"].endswith(".exfil-0000.exfil.example.test")

    # SimCore renders; it never sends a network-shape step itself.
    assert recorder.posts == []
    # Operations were issued live, so the rows stay in the denominator.
    assert [(x.outcome, x.outcome_reason) for x in _rows(session_factory)] == [(None, None)]


def test_relay_wire_step_outside_the_campaign_allowlist_returns_no_ops(client, session_factory):
    res = _launch(session_factory, **LIVE)
    r = _relay(client, res.run_id, campaign_yaml=_campaign(
        C2, target_allowlist=["somewhere-else.example.test"]))
    body = r.json()
    assert body["ops"] == [] and body["allowlist"] == []
    printed = json.loads(body["stdout"])
    assert printed["step_results"][0]["status"] == "error"
    assert "safety_violation" in printed["step_results"][0]["error"]


def test_relay_c2_beacon_without_c2_consent_is_dry(client, session_factory):
    res = _launch(session_factory, consent={"simulation_authorized": True},
                  authorized_by="tester")
    r = _relay(client, res.run_id, campaign_yaml=_campaign(
        C2, target_allowlist=["c2.example.test"]))
    body = r.json()
    assert body["ops"] == []
    assert json.loads(body["stdout"])["relay"]["code"] == "EAL_DRY_RUN_NO_C2_CONSENT"
    assert [(x.outcome, x.outcome_reason) for x in _rows(session_factory)] == [
        ("not_executed", "EAL_DRY_RUN_NO_C2_CONSENT")]


def test_relay_bounds_the_operations_it_hands_out(client, session_factory):
    """One beacon a day for 50 days is a valid c2_http_beacon step. It is not a
    plan a step may be handed."""
    endless = {"plugin": "c2_http_beacon", "params": {
        "target_url": "http://c2.example.test/x", "iterations": 50, "sleep_seconds": 86400}}
    res = _launch(session_factory, **LIVE)
    r = _relay(client, res.run_id, campaign_yaml=_campaign(
        endless, target_allowlist=["c2.example.test"]))
    body = r.json()
    assert body["ops"] == []
    assert body["exit_code"] != 0
    assert "EAL_WIRE_PLAN_REFUSED" in body["stdout"]


def test_relay_unrendered_wire_plugin_is_not_executed_not_missed(client, session_factory, recorder):
    res = _launch(session_factory, **LIVE)
    llm = {"plugin": "llm_provider_egress", "params": {"provider": "openai"}}
    r = _relay(client, res.run_id, campaign_yaml=_campaign(
        llm, target_allowlist=["api.openai.com"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["exit_code"] != 0
    assert body["stdout"].strip() == "EAL_WIRE_NOT_RELAYABLE: llm_provider_egress"
    assert body["ops"] == [] and recorder.posts == []
    assert [(x.outcome, x.outcome_reason) for x in _rows(session_factory)] == [
        ("not_executed", "EAL_WIRE_NOT_RELAYABLE")]

    # The beacon reports the step's non-zero exit and the run ends. An alert
    # that matches the row arrives afterwards; the row is neither credited nor
    # called a miss.
    done = client.post(f"/api/runs/{res.run_id}/complete", json={
        "exit_code": body["exit_code"], "summary": "step-02 failed", "agent_id": AGENT,
        "steps": [
            {"step_id": "step-01", "exit_code": 0, "termination": "exited"},
            {"step_id": "step-02", "exit_code": body["exit_code"], "termination": "exited"},
        ]})
    assert done.status_code == 200, done.text
    obs = client.post(f"/api/runs/{res.run_id}/observations", json={"observations": [{
        "external_id": "a-1", "name": "periodic beacon", "technique": "T1071.001",
        "detection_id": "xql-periodic-beacon", "host": "lab-relay",
        "observed_at": datetime.utcnow().isoformat() + "Z"}]})
    assert obs.status_code == 200, obs.text
    assert [(x.outcome, x.outcome_reason) for x in _rows(session_factory)] == [
        ("not_executed", "EAL_WIRE_NOT_RELAYABLE")]


# ---------------------------------------------------------------------------
# Who may ask
# ---------------------------------------------------------------------------


def test_relay_refuses_step_not_marked_for_this_run(client, session_factory, recorder):
    res = _launch(session_factory, **LIVE)
    campaign = _campaign(M365)

    def refused(**kw):
        r = _relay(client, kw.pop("run_id", res.run_id), campaign_yaml=campaign, **kw)
        return r.status_code, r.json()["detail"]

    # A step of this run that does not call the EAL CLI.
    status, detail = refused(step_id="step-01")
    assert (status, detail["code"]) == (403, "EAL_RELAY_STEP_NOT_MARKED")
    assert set(detail) == {"error", "code", "detail"}
    # The marked step, asked for by another agent.
    assert refused(agent_id="some-other-agent")[0] == 403
    # A run that does not exist.
    assert refused(run_id="00000000-0000-0000-0000-000000000000")[0] == 403

    # The run ends; its marked step can no longer relay.
    done = client.post(f"/api/runs/{res.run_id}/complete", json={
        "exit_code": 0, "summary": "ok", "agent_id": AGENT, "steps": []})
    assert done.status_code == 200, done.text
    status, detail = refused()
    assert (status, detail["code"]) == (409, "EAL_RELAY_RUN_NOT_IN_FLIGHT")

    assert recorder.posts == []


# ---------------------------------------------------------------------------
# The two read-only verbs print what the CLI prints
# ---------------------------------------------------------------------------


def test_relay_describe_and_list_plugins_print_the_cli_json(client, session_factory):
    res = _launch(session_factory)
    described = _relay(client, res.run_id, verb="describe", plugin="dns_tunnel_exfil").json()
    assert described["exit_code"] == 0
    assert json.loads(described["stdout"])["name"] == "dns_tunnel_exfil"
    assert json.loads(described["stdout"])["eal_targets"]

    listed = _relay(client, res.run_id, verb="list-plugins").json()
    assert listed["exit_code"] == 0
    assert json.loads(listed["stdout"])["total"] >= 4

    unknown = _relay(client, res.run_id, verb="describe", plugin="no_such_plugin").json()
    assert unknown["exit_code"] == 2
    assert "unknown plugin" in unknown["stderr"]
