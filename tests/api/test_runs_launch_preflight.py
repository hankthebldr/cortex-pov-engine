"""POST /api/runs/preflight: the launch's own checks, run without launching.

The console used to guess whether a launch would be refused: of about twenty
refusals POST /api/runs can return, it predicted six, and Library Quick launch
and the TTP browser's launch-all predicted almost none. The dry run calls the
SAME checks the launch calls, in the same order, so for every refusal:

* the dry run names it, with the code and status the launch will answer;
* the launch then answers exactly that code and status;
* neither of them leaves a run, a result row or a queued task behind.

docs/superpowers/specs/2026-11-13-launch-gate-one-model-design.md §4.2.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from sqlalchemy import func, select

CONTAINER_FACTS = {"container": True, "posture_profile": "baseline",
                   "tools": ["curl"], "accounts": [], "prepare_status": "PREPARE_OK"}

DETECTION = {"detection_id": "d-1", "detection_type": "BIOC", "name": "n"}


@pytest.fixture
def client(make_client):
    from api.runs import compat_router, router
    return make_client(router, compat_router)


def _seed(session_factory, *, scenario_id="SIM-EDR-990", status="active", steps=None,
          tc_ref="TC-EDR-01", stitch_context=None, agents=()):
    """One scenario plus agents given as (agent_id, os, runtime_facts)."""
    from models import Agent, Scenario

    async def _do():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id=scenario_id, name="preflight", version="1.0",
                status=status, plane="EDR",
                uc_ref="UCS-EDR-01", uc_name="x", tc_ref=tc_ref, tc_name="y",
                mitre_tactic="TA0006", mitre_tactic_name="Credential Access",
                mitre_technique="T1003", mitre_technique_name="OS Credential Dumping",
                execution_identity={"default": "root"},
                push_supported=True, pull_supported=True,
                stitch_context=stitch_context,
                steps=steps or [{
                    "id": "step-01", "name": "s", "command": "id",
                    "platforms": ["linux"],
                    "expected_detections": [DETECTION],
                }],
            ))
            for agent_id, os_name, facts in agents:
                db.add(Agent(agent_id=agent_id, hostname=agent_id, os=os_name,
                             capabilities=["shell"], interpreters=[],
                             runtime_facts=facts,
                             registered_at=datetime.utcnow(),
                             last_seen=datetime.utcnow(), status="online"))
            await db.commit()

    asyncio.run(_do())
    return scenario_id


def _counts(session_factory):
    from models import Result, Run

    async def _read():
        async with session_factory() as db:
            runs = (await db.execute(select(func.count()).select_from(Run))).scalar_one()
            results = (await db.execute(select(func.count()).select_from(Result))).scalar_one()
            return runs, results

    return asyncio.run(_read())


def _queued(agent_id):
    from engine.orchestrator import orchestrator
    return orchestrator.peek_queue(agent_id)


def _refuse_payload(_scenario):
    from engine.payload_shelf import PayloadResolutionError
    raise PayloadResolutionError(
        "PAYLOAD_NOT_STAGED", "Payload not staged",
        "linpeas.sh is declared by TOOL-LINPEAS but is not on this shelf")


def _refuse_stitch(spec, *, seed=None, target=None):
    from engine.stitch_context import StitchContextValidationError
    raise StitchContextValidationError(
        "unknown directive 'teleport'", key="principal", directive="teleport")


def _refuse_consent(scenario, consent, *, mode=None):
    from engine.orchestrator import LaunchRefusal
    if (consent or {}).get("simulation_authorized"):
        return None
    return LaunchRefusal(
        "Scenario uses a dual-use-lab-only adapter; set consent.simulation_authorized",
        code="CONSENT_REQUIRED", detail={"required_consent": ["simulation_authorized"]})


# Each case: (name, setup(session_factory, monkeypatch) -> launch body, code, status).
def _case_invalid_mode(sf, mp):
    sid = _seed(sf, agents=[("a-mode", "linux", None)])
    return {"scenario_id": sid, "mode": "sideways", "target_agent_id": "a-mode"}


def _case_scenario_not_found(sf, mp):
    return {"scenario_id": "SIM-NOPE-000", "mode": "pull", "target_agent_id": "a-x"}


def _case_draft_chain_invalid(sf, mp):
    sid = _seed(sf, status="draft", steps=[{
        "id": "step-01", "name": "s", "command": "",
        "expected_detections": [DETECTION]}], agents=[("a-dc", "linux", None)])
    return {"scenario_id": sid, "mode": "pull", "target_agent_id": "a-dc"}


def _case_draft_not_tc_bound(sf, mp):
    sid = _seed(sf, status="draft", tc_ref="UNBOUND", agents=[("a-tc", "linux", None)])
    return {"scenario_id": sid, "mode": "pull", "target_agent_id": "a-tc"}


def _case_consent(sf, mp):
    from engine import orchestrator as orch
    mp.setattr(orch, "_check_launch_consent", _refuse_consent)
    sid = _seed(sf, agents=[("a-consent", "linux", None)])
    return {"scenario_id": sid, "mode": "pull", "target_agent_id": "a-consent"}


def _case_pair(sf, mp):
    sid = _seed(sf, agents=[("a-pair", "linux", None)])
    return {"scenario_id": sid, "mode": "pull", "target_agent_id": "a-pair",
            "paired_with_run_id": "no-such-run"}


def _case_canary(sf, mp):
    sid = _seed(sf, agents=[("a-canary", "linux", None)])
    return {"scenario_id": sid, "mode": "pull", "target_agent_id": "a-canary",
            "canary_run_id": "no-such-run"}


def _case_pull_without_target(sf, mp):
    sid = _seed(sf)
    return {"scenario_id": sid, "mode": "pull"}


def _case_target_not_enrolled(sf, mp):
    sid = _seed(sf)
    return {"scenario_id": sid, "mode": "pull", "target_agent_id": "ghost"}


def _case_os_unrecognised(sf, mp):
    sid = _seed(sf, agents=[("a-plan9", "plan9", None)])
    return {"scenario_id": sid, "mode": "pull", "target_agent_id": "a-plan9"}


def _case_unsatisfiable(sf, mp):
    sid = _seed(sf, steps=[{
        "id": "step-01", "name": "s", "command": "cat /etc/passwd | grep root",
        "platforms": ["linux"], "expected_detections": [DETECTION]}],
        agents=[("a-win", "windows", None)])
    return {"scenario_id": sid, "mode": "pull", "target_agent_id": "a-win"}


def _case_payload(sf, mp):
    from engine import orchestrator as orch
    mp.setattr(orch, "_compose_artifacts", _refuse_payload)
    sid = _seed(sf, agents=[("a-payload", "linux", None)])
    return {"scenario_id": sid, "mode": "pull", "target_agent_id": "a-payload"}


def _case_payload_at_the_shelf(sf, mp):
    """The shelf itself refuses, not the orchestrator's wrapper. The dry run
    used to call the shelf a second time for egress warnings after the payload
    check had already blocked, and raised (500) where the launch answered 409."""
    from engine import payload_shelf as shelf

    def _compose(*_a, **_kw):
        raise shelf.PayloadResolutionError(
            "PAYLOAD_NOT_STAGED", "Payload not staged",
            "linpeas.sh is declared by TOOL-LINPEAS but is not on this shelf")

    mp.setattr(shelf, "compose", _compose)
    sid = _seed(sf, agents=[("a-shelf", "linux", None)])
    return {"scenario_id": sid, "mode": "pull", "target_agent_id": "a-shelf"}


def _case_stitch(sf, mp):
    from engine import stitch_context as sc
    mp.setattr(sc, "resolve_stitch_context", _refuse_stitch)
    sid = _seed(sf, stitch_context={"principal": {"directive": "static", "value": "svc"}},
                agents=[("a-stitch", "linux", None)])
    return {"scenario_id": sid, "mode": "pull", "target_agent_id": "a-stitch"}


def _case_composed_not_enrolled(sf, mp):
    sid = _seed(sf, steps=[{
        "id": "step-01", "name": "s", "command": "id", "target": "phantom-endpoint",
        "expected_detections": [DETECTION]}], agents=[("a-composed", "linux", None)])
    return {"scenario_id": sid, "mode": "pull", "target_agent_id": "a-composed"}


def _case_tool_absent(sf, mp):
    sid = _seed(sf, steps=[{
        "id": "step-01", "name": "s", "command": "jq . /etc/os-release",
        "expected_detections": [DETECTION]}],
        agents=[("pod-preflight", "linux", CONTAINER_FACTS)])
    return {"scenario_id": sid, "mode": "pull", "target_agent_id": "pod-preflight"}


CASES = [
    ("invalid_mode", _case_invalid_mode, "INVALID_MODE", 400),
    ("scenario_not_found", _case_scenario_not_found, "SCENARIO_NOT_FOUND", 404),
    ("draft_chain_invalid", _case_draft_chain_invalid, "DRAFT_CHAIN_INVALID", 409),
    ("draft_not_tc_bound", _case_draft_not_tc_bound, "DRAFT_NOT_TC_BOUND", 409),
    ("consent", _case_consent, "CONSENT_REQUIRED", 409),
    ("pair", _case_pair, "PAIR_INVALID", 409),
    ("canary", _case_canary, "CANARY_INVALID", 409),
    ("pull_without_target", _case_pull_without_target, "PULL_TARGET_MISSING", 422),
    ("target_not_enrolled", _case_target_not_enrolled, "TARGET_AGENT_NOT_ENROLLED", 409),
    ("os_unrecognised", _case_os_unrecognised, "PULL_TARGET_OS_UNRECOGNISED", 409),
    ("unsatisfiable", _case_unsatisfiable, "PULL_TARGET_UNSATISFIABLE", 409),
    ("payload", _case_payload, "PAYLOAD_NOT_STAGED", 409),
    ("payload_at_the_shelf", _case_payload_at_the_shelf, "PAYLOAD_NOT_STAGED", 409),
    ("stitch", _case_stitch, "STITCH_CONTEXT_INVALID", 409),
    ("composed_not_enrolled", _case_composed_not_enrolled, "TARGET_AGENT_NOT_ENROLLED", 409),
    ("tool_absent", _case_tool_absent, "AGENT_TOOL_ABSENT", 409),
]


@pytest.mark.parametrize("name,setup,code,status", CASES, ids=[c[0] for c in CASES])
def test_the_dry_run_names_the_refusal_the_launch_returns(
    client, session_factory, monkeypatch, name, setup, code, status,
):
    body = setup(session_factory, monkeypatch)

    pre = client.post("/api/runs/preflight", json=body)
    assert pre.status_code == 200, pre.text
    verdict = pre.json()
    assert verdict["launchable"] is False
    assert verdict["would_refuse_with"]["code"] == code
    assert verdict["would_refuse_with"]["http_status"] == status
    assert code in [r["code"] for r in verdict["refusals"]]
    assert _counts(session_factory) == (0, 0), "the dry run wrote to the database"

    launched = client.post("/api/runs", json=body)
    assert launched.status_code == status, launched.text
    assert launched.json()["detail"]["code"] == code
    # A refused launch leaves nothing: no failed run, no stamped results.
    assert _counts(session_factory) == (0, 0)
    assert _queued(body.get("target_agent_id") or "") == []


def test_a_clean_launch_is_predicted_launchable(client, session_factory):
    sid = _seed(session_factory, agents=[("a-clean", "linux", None)])
    body = {"scenario_id": sid, "mode": "pull", "target_agent_id": "a-clean"}

    pre = client.post("/api/runs/preflight", json=body).json()
    assert pre["launchable"] is True
    assert pre["would_refuse_with"] is None
    assert pre["refusals"] == []
    assert {c["verdict"] for c in pre["checks"]} <= {"pass", "skipped"}
    assert _counts(session_factory) == (0, 0)
    assert _queued("a-clean") == []

    launched = client.post("/api/runs", json=body)
    assert launched.status_code == 200, launched.text
    assert _counts(session_factory) == (1, 1)


def test_the_dry_run_reports_every_refusal_and_the_launch_the_first(
    client, session_factory, monkeypatch,
):
    """Three independent problems: the dry run lists all three, in launch order;
    the launch stops at the first, exactly as before."""
    from engine import orchestrator as orch
    monkeypatch.setattr(orch, "_check_launch_consent", _refuse_consent)
    monkeypatch.setattr(orch, "_compose_artifacts", _refuse_payload)
    sid = _seed(session_factory, agents=[("a-many", "linux", None)])
    body = {"scenario_id": sid, "mode": "pull", "target_agent_id": "a-many",
            "paired_with_run_id": "no-such-run"}

    pre = client.post("/api/runs/preflight", json=body).json()
    assert [r["code"] for r in pre["refusals"]] == [
        "CONSENT_REQUIRED", "PAIR_INVALID", "PAYLOAD_NOT_STAGED"]
    assert pre["would_refuse_with"]["code"] == "CONSENT_REQUIRED"

    launched = client.post("/api/runs", json=body)
    assert launched.json()["detail"]["code"] == "CONSENT_REQUIRED"


def test_a_check_that_cannot_be_judged_is_skipped_with_its_reason(client, session_factory):
    """No target means nothing target-shaped can be judged. Skipped is not a
    pass: each skipped check says what it was waiting on."""
    sid = _seed(session_factory)
    pre = client.post("/api/runs/preflight",
                      json={"scenario_id": sid, "mode": "pull"}).json()
    skipped = [c for c in pre["checks"] if c["verdict"] == "skipped"]
    assert skipped, pre["checks"]
    assert all(c["reason"] for c in skipped)


def test_an_offline_target_is_launchable_with_its_warning(client, session_factory):
    from models import Agent

    sid = _seed(session_factory)

    async def _offline():
        async with session_factory() as db:
            db.add(Agent(agent_id="a-off", hostname="a-off", os="linux",
                         capabilities=["shell"], interpreters=[],
                         registered_at=datetime(2026, 1, 1),
                         last_seen=datetime(2026, 1, 1), status="offline"))
            await db.commit()

    asyncio.run(_offline())
    pre = client.post("/api/runs/preflight", json={
        "scenario_id": sid, "mode": "pull", "target_agent_id": "a-off"}).json()
    assert pre["launchable"] is True
    assert any("a-off" in str(w) for w in pre["warnings"]), pre["warnings"]


def test_a_stitch_refusal_does_not_hide_a_composed_target_refusal(
    client, session_factory, monkeypatch,
):
    """Fixing the stitch context and relaunching must not be the moment the
    operator learns the second endpoint is not enrolled."""
    from engine import stitch_context as sc
    monkeypatch.setattr(sc, "resolve_stitch_context", _refuse_stitch)
    sid = _seed(session_factory,
                stitch_context={"principal": {"directive": "static", "value": "svc"}},
                steps=[{"id": "step-01", "name": "s", "command": "id",
                        "target": "phantom-endpoint", "expected_detections": [DETECTION]}],
                agents=[("a-both", "linux", None)])
    body = {"scenario_id": sid, "mode": "pull", "target_agent_id": "a-both"}

    pre = client.post("/api/runs/preflight", json=body).json()
    assert [r["code"] for r in pre["refusals"]] == [
        "STITCH_CONTEXT_INVALID", "TARGET_AGENT_NOT_ENROLLED"]
    # The gate needs both the binding and the resolved endpoints; it is skipped
    # once, with the reason it met first, never passed.
    gate = [c for c in pre["checks"] if c["id"] == "pull.container_gate"]
    assert len(gate) == 1 and gate[0]["verdict"] == "skipped" and gate[0]["reason"]
    assert client.post("/api/runs", json=body).json()["detail"]["code"] == "STITCH_CONTEXT_INVALID"


def test_a_composed_run_does_not_call_an_unenrolled_launch_target_a_pass(
    client, session_factory,
):
    sid = _seed(session_factory, steps=[{
        "id": "step-01", "name": "s", "command": "id", "target": "phantom-endpoint",
        "expected_detections": [DETECTION]}])
    pre = client.post("/api/runs/preflight", json={
        "scenario_id": sid, "mode": "pull", "target_agent_id": "ghost"}).json()
    target = [c for c in pre["checks"] if c["id"] == "pull.target"]
    assert target and target[0]["verdict"] == "skipped", target
    composed = [c for c in pre["checks"] if c["id"] == "pull.composed_targets"][0]
    assert composed["code"] == "TARGET_AGENT_NOT_ENROLLED"
    # "ghost" carries no step here, so the one endpoint the run needs is the phantom.
    assert composed["detail"]["missing_agents"] == ["phantom-endpoint"]


def test_the_dry_run_carries_the_soc_framework_advisories_a_launch_carries(
    client, session_factory, monkeypatch,
):
    """Same read-only function, so the warning a DC sees before launch is the
    one the launch response carries."""
    from types import SimpleNamespace

    from engine import socfw_advisory
    from security import credentials

    advisory = {"code": "SOCFW_TRIAGE_MAY_SEAL", "tenant": "t1"}

    async def _integrations(self, kind=None):
        return [SimpleNamespace(name="t1", response_layer=None, response_layer_read_at=None)]

    monkeypatch.setattr(credentials.CredentialStore, "list_integrations", _integrations)
    monkeypatch.setattr(socfw_advisory, "triage_advisories", lambda **_kw: [advisory])
    sid = _seed(session_factory, agents=[("a-socfw", "linux", None)])
    body = {"scenario_id": sid, "mode": "pull", "target_agent_id": "a-socfw"}

    pre = client.post("/api/runs/preflight", json=body).json()
    assert advisory in pre["warnings"]
    launched = client.post("/api/runs", json=body)
    assert launched.status_code == 200, launched.text
    assert advisory in launched.json()["warnings"]
