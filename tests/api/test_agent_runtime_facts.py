"""Agent.runtime_facts: what a beacon running inside a pod reports about itself.

NULL means "not a container agent, facts unknown" and must never be read as
"no tools". The field arrives from an unauthenticated beacon, so it is
validated at the API boundary: known keys only, unknown keys dropped, a wrong
type is a 422.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text

from database import _migrate_composer_channel_columns

FACTS = {
    "container": True, "pod_name": "agent-0", "pod_namespace": "cortexsim-agent",
    "node_name": "node-a", "posture_profile": "baseline",
    "prepare_status": "PREPARE_OK", "tools": ["curl", "python3"],
    "accounts": ["www-data"], "anchor_rename": True,
}


@pytest.fixture
def client(make_client):
    from api.agents import router
    return make_client(router)


def _agent(client, agent_id):
    for a in client.get("/api/agents").json()["agents"]:
        if a["agent_id"] == agent_id:
            return a
    raise AssertionError(f"agent {agent_id!r} not listed")


def _enroll(client, **extra):
    token = client.post("/api/agents/enroll/tokens", json={}).json()["token"]
    return client.post("/api/agents/enroll", json={
        "token": token, "hostname": "pod", "os": "linux", **extra})


def _register(client, **extra):
    return client.post("/api/agents/register", json={
        "agent_id": "a-1", "hostname": "pod", "os": "linux", **extra})


def test_enroll_with_facts_is_listed(client):
    r = _enroll(client, runtime_facts=FACTS)
    assert r.status_code == 200, r.text
    assert _agent(client, r.json()["agent_id"])["runtime_facts"] == FACTS


def test_enroll_without_facts_is_null(client):
    r = _enroll(client)
    assert _agent(client, r.json()["agent_id"])["runtime_facts"] is None


def test_register_new_facts_replace(client):
    _register(client, runtime_facts=FACTS).raise_for_status()
    newer = {**FACTS, "tools": ["curl"], "prepare_status": "PREPARE_DEGRADED"}
    _register(client, runtime_facts=newer).raise_for_status()
    assert _agent(client, "a-1")["runtime_facts"] == newer


def test_register_omitted_keeps_stored(client):
    _register(client, runtime_facts=FACTS).raise_for_status()
    _register(client).raise_for_status()
    assert _agent(client, "a-1")["runtime_facts"] == FACTS


def test_register_without_facts_on_create_is_null(client):
    _register(client).raise_for_status()
    assert _agent(client, "a-1")["runtime_facts"] is None


def test_unknown_keys_are_dropped_not_an_error(client):
    _register(client, runtime_facts={**FACTS, "from_the_future": 1}).raise_for_status()
    assert _agent(client, "a-1")["runtime_facts"] == FACTS


def test_probed_list_is_kept(client):
    """A beacon that says which tools it looked for is stored with that list:
    the dependency preflight reads it to tell "not found" from "never looked"."""
    facts = {**FACTS, "probed": ["curl", "python3", "ssh"]}
    _register(client, runtime_facts=facts).raise_for_status()
    assert _agent(client, "a-1")["runtime_facts"] == facts


def test_agent_preflight_reads_the_tool_facts(client, session_factory):
    """GET /api/agents/{id}/preflight judges a tool on the facts the beacon
    registered: sudo was found, jq was looked for and not found, and no beacon
    looks for ssh."""
    import asyncio

    from models import Scenario

    async def _scenario():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id="SIM-EDR-993", name="tool facts", version="1.0",
                status="active", plane="EDR",
                uc_ref="UCS-EDR-01", uc_name="x", tc_ref="TC-EDR-01", tc_name="y",
                mitre_tactic="TA0006", mitre_tactic_name="Credential Access",
                mitre_technique="T1003", mitre_technique_name="OS Credential Dumping",
                execution_identity={"default": "root"},
                push_supported=True, pull_supported=True,
                steps=[
                    {"id": "step-01", "command": "sudo -V", "requires_interpreters": ["sudo"]},
                    {"id": "step-02", "command": "jq .", "requires_interpreters": ["jq"]},
                    {"id": "step-03", "command": "ssh -V", "requires_interpreters": ["ssh"]},
                ],
            ))
            await db.commit()

    asyncio.run(_scenario())
    _register(client, interpreters=["python"], runtime_facts={
        "container": False, "tools": ["curl", "sudo"], "accounts": []}).raise_for_status()

    r = client.get("/api/agents/a-1/preflight", params={"scenario_id": "SIM-EDR-993"})
    assert r.status_code == 200, r.text
    assert r.json() == {
        "agent_id": "a-1",
        "scenario_id": "SIM-EDR-993",
        "ready": False,
        "gaps": [
            {"step_id": "step-02", "missing": ["jq"]},
            {"step_id": "step-03", "missing": [], "unverified": ["ssh"]},
        ],
        "agent_interpreters": ["python"],
    }


@pytest.mark.parametrize("bad", [
    {"container": "yes"},
    {"tools": "curl"},
    {"tools": [1]},
    {"tools": ["x" * 65]},
    {"tools": ["a"] * 201},
    {"pod_name": "x" * 254},
    {"anchor_rename": "maybe"},
    ["not", "a", "dict"],
])
def test_wrong_shape_is_422(client, bad):
    assert _register(client, runtime_facts=bad).status_code == 422
    assert _enroll(client, runtime_facts=bad).status_code == 422


def test_boot_migration_adds_runtime_facts_column(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path/'legacy.db'}")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE agents (id INTEGER PRIMARY KEY, "
                          "agent_id VARCHAR, hostname VARCHAR, os VARCHAR)"))
        conn.execute(text("INSERT INTO agents (agent_id, hostname, os) "
                          "VALUES ('old', 'h', 'linux')"))
    for _ in range(2):  # second pass proves idempotence
        with eng.begin() as conn:
            _migrate_composer_channel_columns(conn)
    with eng.begin() as conn:
        assert "runtime_facts" in {c["name"] for c in inspect(conn).get_columns("agents")}
        assert conn.execute(text(
            "SELECT runtime_facts FROM agents WHERE agent_id='old'")).one()[0] is None
