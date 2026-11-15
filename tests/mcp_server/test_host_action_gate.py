"""Host actions (run.launch / run.abort) refuse unless explicitly allowed (spec §7.2)."""
from __future__ import annotations

import json
import shlex

import pytest

from mcp_server.naming import wire_name
from mcp_server.server import build
from tests.mcp_server.conftest import call, integration, make_ctx, paths, readiness_body

LAUNCH_ARGS = {"scenario_id": "SIM-EDR-001", "target_agent_id": "agent-1", "simulation_authorized": True}
ROUTES = {
    ("POST", "/api/runs"): {"run_id": "r1", "mode": "pull", "message": "queued"},
    ("POST", "/api/runs/r1/abort"): {"run_id": "r1", "status": "aborted"},
    ("POST", "/api/runs/r1/reconcile"): {"run_id": "r1", "queries_issued": 0, "observed": 0},
    ("GET", "/api/readiness"): readiness_body(integrations=[integration("t1", kind="xsiam")]),
}


def _built(fake_simcore, **kwargs):
    client, calls = fake_simcore(ROUTES)
    return build(make_ctx(client), **kwargs), calls


@pytest.mark.asyncio
async def test_launch_and_abort_refuse_by_default_and_never_reach_simcore(fake_simcore):
    mcp, calls = _built(fake_simcore)

    launch = await call(mcp, "cortexsim.run.launch", **LAUNCH_ARGS)
    assert launch.isError
    sc = launch.structuredContent
    assert sc["code"] == "MCP_HOST_ACTION_DISABLED"
    assert sc["remediation"]
    assert sc["data"]["console"] == "#/library"
    cmd = sc["data"]["execute_as_human"]
    assert cmd.startswith("curl -X POST http://simcore.test/api/runs ")
    assert json.loads(shlex.split(cmd)[-1]) == {
        "scenario_id": "SIM-EDR-001", "mode": "pull", "target_agent_id": "agent-1",
        "consent": {"simulation_authorized": True}}

    abort = await call(mcp, "cortexsim.run.abort", run_id="r1")
    assert abort.isError
    sc = abort.structuredContent
    assert sc["code"] == "MCP_HOST_ACTION_DISABLED"
    assert sc["data"]["console"] == "#/runs"
    assert sc["data"]["execute_as_human"] == "curl -X POST http://simcore.test/api/runs/r1/abort"

    assert paths(calls, "POST") == []
    # the tools stay registered: a refusing tool says who acts
    names = {t.name for t in mcp._tool_manager.list_tools()}
    assert {wire_name("cortexsim.run.launch"), wire_name("cortexsim.run.abort")} <= names


@pytest.mark.asyncio
async def test_allow_host_actions_executes(fake_simcore):
    mcp, calls = _built(fake_simcore, allow_host_actions=True)

    launch = await call(mcp, "cortexsim.run.launch", **LAUNCH_ARGS)
    assert not launch.isError, launch.content[0].text
    abort = await call(mcp, "cortexsim.run.abort", run_id="r1")
    assert not abort.isError, abort.content[0].text
    assert paths(calls, "POST") == ["/api/runs", "/api/runs/r1/abort"]


@pytest.mark.asyncio
async def test_tenant_class_tools_are_unaffected(fake_simcore):
    mcp, calls = _built(fake_simcore)
    res = await call(mcp, "cortexsim.run.reconcile", run_id="r1")
    assert not res.isError, res.content[0].text
    assert "/api/runs/r1/reconcile" in paths(calls, "POST")


def test_stdio_entry_reads_the_same_flag(monkeypatch):
    import mcp_server.server as server
    from mcp_server.__main__ import main

    seen: list[dict] = []
    real = server.build

    def spy(ctx, **kwargs):
        seen.append(kwargs)
        return real(ctx, **kwargs)

    monkeypatch.setattr(server, "build", spy)
    monkeypatch.setenv("CORTEXSIM_MCP_ALLOW_HOST_ACTIONS", "1")
    assert main(["--list-tools"]) == 0
    assert seen[-1].get("allow_host_actions") is True

    monkeypatch.setenv("CORTEXSIM_MCP_ALLOW_HOST_ACTIONS", "0")
    assert main(["--list-tools"]) == 0
    assert seen[-1].get("allow_host_actions") is False

    # the same parsing rule as pydantic on the mount: "y" is true, junk is false
    monkeypatch.setenv("CORTEXSIM_MCP_ALLOW_HOST_ACTIONS", "y")
    assert main(["--list-tools"]) == 0
    assert seen[-1].get("allow_host_actions") is True
    monkeypatch.setenv("CORTEXSIM_MCP_ALLOW_HOST_ACTIONS", "banana")
    assert main(["--list-tools"]) == 0
    assert seen[-1].get("allow_host_actions") is False


@pytest.mark.asyncio
@pytest.mark.parametrize("run_id", ["", ".", ".."])
async def test_refusal_code_survives_an_unusable_run_id(fake_simcore, run_id):
    mcp, calls = _built(fake_simcore)
    res = await call(mcp, "cortexsim.run.abort", run_id=run_id)
    assert res.isError
    assert res.structuredContent["code"] == "MCP_HOST_ACTION_DISABLED"
    assert res.structuredContent["data"]["console"] == "#/runs"
    assert paths(calls, "POST") == []


@pytest.mark.filterwarnings("ignore::starlette.exceptions.StarletteDeprecationWarning")
@pytest.mark.asyncio
async def test_http_mount_refuses_host_actions_unless_the_flag_is_set(boot_main, monkeypatch):
    main = boot_main(http=True, token="tok-" + "q" * 40)
    res = await main.app.state.mcp.call_tool(wire_name("cortexsim.run.abort"), {"run_id": "r1"})
    assert res.isError and res.structuredContent["code"] == "MCP_HOST_ACTION_DISABLED"

    main = boot_main(http=True, token="tok-" + "q" * 40, host_actions="true")
    res = await main.app.state.mcp.call_tool(wire_name("cortexsim.run.abort"), {"run_id": "r1"})
    # allowed: it reaches the (in-process) SimCore, which has no such run — not our refusal
    assert res.structuredContent["code"] != "MCP_HOST_ACTION_DISABLED"
