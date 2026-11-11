"""Tests for the agent enrollment-token onboarding flow."""
from __future__ import annotations

import shutil
import subprocess

import pytest


@pytest.fixture
def client(make_client):
    from api.agents import router
    return make_client(router)


def test_mint_reveals_token_once_then_only_tail(client):
    r = client.post("/api/agents/enroll/tokens", json={"label": "lab-jumpboxes", "max_uses": 3})
    assert r.status_code == 200, r.text
    minted = r.json()
    assert minted["token"].startswith("cxs_")  # full value revealed at mint
    assert minted["remaining_uses"] == 3

    # Listing shows only the tail, never the full secret.
    lst = client.get("/api/agents/enroll/tokens").json()["tokens"]
    assert len(lst) == 1
    assert lst[0]["token"].startswith("...")
    assert not lst[0]["token"].startswith("cxs_")
    assert lst[0]["valid"] is True


def test_enroll_assigns_server_side_id_and_registers(client):
    token = client.post("/api/agents/enroll/tokens", json={"max_uses": 2}).json()["token"]
    r = client.post("/api/agents/enroll", json={
        "token": token, "hostname": "jumpbox-prod-01", "os": "linux"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "enrolled"
    assert body["agent_id"].startswith("jumpbox-prod-01-")  # stem from hostname
    assert body["remaining_uses"] == 1

    # The agent now appears in the roster.
    agents = client.get("/api/agents").json()["agents"]
    assert any(a["agent_id"] == body["agent_id"] for a in agents)


def test_desired_name_is_sanitised(client):
    token = client.post("/api/agents/enroll/tokens", json={}).json()["token"]
    r = client.post("/api/agents/enroll", json={
        "token": token, "hostname": "h", "os": "linux",
        "desired_name": "Prod Box #1!!"})
    aid = r.json()["agent_id"]
    assert aid.startswith("prod-box-1-")
    assert " " not in aid and "#" not in aid


def test_max_uses_exhausted_then_denied(client):
    token = client.post("/api/agents/enroll/tokens", json={"max_uses": 1}).json()["token"]
    ok = client.post("/api/agents/enroll", json={"token": token, "hostname": "a", "os": "linux"})
    assert ok.status_code == 200
    denied = client.post("/api/agents/enroll", json={"token": token, "hostname": "b", "os": "linux"})
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "ENROLL_DENIED"


def test_revoked_token_denied(client):
    minted = client.post("/api/agents/enroll/tokens", json={"max_uses": 5}).json()
    token, tid = minted["token"], minted["id"]
    assert client.delete(f"/api/agents/enroll/tokens/{tid}").status_code == 200
    denied = client.post("/api/agents/enroll", json={"token": token, "hostname": "a", "os": "linux"})
    assert denied.status_code == 403


def test_unknown_token_denied_opaquely(client):
    r = client.post("/api/agents/enroll", json={"token": "cxs_nope", "hostname": "a", "os": "linux"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "ENROLL_DENIED"


def test_installer_with_token_is_valid_bash(client):
    token = client.post("/api/agents/enroll/tokens", json={}).json()["token"]
    r = client.get(f"/api/agents/install?os=linux&token={token}")
    assert r.status_code == 200
    script = r.text
    assert "/api/agents/enroll" in script
    assert token in script
    if shutil.which("bash"):
        p = subprocess.run(["bash", "-n", "/dev/stdin"], input=script,
                           capture_output=True, text=True, timeout=10)
        assert p.returncode == 0, f"generated installer failed bash -n:\n{p.stderr}"


def test_installer_without_token_is_legacy_id_path(client):
    r = client.get("/api/agents/install?os=linux&id=my-box")
    assert r.status_code == 200
    # No token → the enroll block is inert (empty token guard) and the explicit
    # id is used.
    assert "my-box" in r.text


def test_windows_installer_includes_enroll(client):
    token = client.post("/api/agents/enroll/tokens", json={}).json()["token"]
    r = client.get(f"/api/agents/install?os=windows&token={token}")
    assert r.status_code == 200
    assert "Invoke-RestMethod" in r.text
    assert "/api/agents/enroll" in r.text


# ── null lists from already-shipped beacons (kind run, D2) ───────────────────
# A pod with no detectable interpreter sent "interpreters": null and got 422,
# then crash-looped. Null is coerced to []; a wrong TYPE is still refused.

def _agent(client, agent_id):
    return next(a for a in client.get("/api/agents").json()["agents"] if a["agent_id"] == agent_id)


def test_enroll_accepts_null_capabilities_and_interpreters(client):
    token = client.post("/api/agents/enroll/tokens", json={"max_uses": 2}).json()["token"]
    r = client.post("/api/agents/enroll", json={
        "token": token, "hostname": "bare-pod", "os": "linux",
        "capabilities": None, "interpreters": None})
    assert r.status_code == 200, r.text
    a = _agent(client, r.json()["agent_id"])
    # null reads exactly as an omitted list: enroll's default capabilities apply.
    omitted = client.post("/api/agents/enroll", json={
        "token": token, "hostname": "bare-pod", "os": "linux"}).json()["agent_id"]
    assert a["interpreters"] == []
    assert a["capabilities"] == _agent(client, omitted)["capabilities"]


def test_register_accepts_null_capabilities_and_interpreters(client):
    r = client.post("/api/agents/register", json={
        "agent_id": "bare-pod-1", "hostname": "bare-pod", "os": "linux",
        "capabilities": None, "interpreters": None})
    assert r.status_code == 200, r.text
    a = _agent(client, "bare-pod-1")
    assert a["capabilities"] == [] and a["interpreters"] == []


@pytest.mark.parametrize("field", ["capabilities", "interpreters"])
def test_a_string_where_a_list_belongs_is_still_refused(client, field):
    token = client.post("/api/agents/enroll/tokens", json={}).json()["token"]
    r = client.post("/api/agents/enroll", json={
        "token": token, "hostname": "h", "os": "linux", field: "bash"})
    assert r.status_code == 422, r.text
    r = client.post("/api/agents/register", json={
        "agent_id": "h-1", "hostname": "h", "os": "linux", field: "bash"})
    assert r.status_code == 422, r.text
