"""The registry wrapper: one place where results and errors are shaped."""
from __future__ import annotations

import dataclasses
import json

import httpx
import pytest
from mcp.server.fastmcp import FastMCP

from mcp_server.errors import NO_TENANT_CONFIGURED, TENANT_AMBIGUOUS, McpToolError
from mcp_server.naming import CANONICAL_IDS, wire_name
from mcp_server.ratelimit import TokenBucket
from mcp_server.registry import cap_list, cap_text, register, resolve_tenant, take_token
from mcp_server.tools.cortexsim import specs as cortexsim_specs
from tests.mcp_server.conftest import (
    AS_OF,
    call,
    integration,
    make_ctx,
    make_fake_simcore,
    mcp_with,
    paths,
    readiness_body,
)

CORTEXSIM_IDS = {c for c in CANONICAL_IDS if c.startswith("cortexsim.")}
HEALTH = {"status": "ok", "not_checked": [{"claim": "tenant reachable", "checked_by": "POST /x"}]}


def _mcp(routes=None, specs=None, bucket=None):
    client, calls = make_fake_simcore(routes or {})
    ctx = make_ctx(client, bucket=bucket)
    return mcp_with(specs or cortexsim_specs(), ctx), calls, ctx


@pytest.mark.asyncio
async def test_registered_wire_names_equal_the_canonical_set_for_the_group():
    mcp, _, _ = _mcp()
    tools = await mcp.list_tools()
    assert len(CORTEXSIM_IDS) == 25
    assert {t.name for t in tools} == {wire_name(c) for c in CORTEXSIM_IDS}
    assert {t.title for t in tools} == CORTEXSIM_IDS

    client, _ = make_fake_simcore({})
    with pytest.raises(AssertionError):
        register(FastMCP("short"), cortexsim_specs()[:-1], make_ctx(client))


@pytest.mark.asyncio
async def test_input_schema_is_flat_and_output_schema_is_envelope():
    mcp, _, _ = _mcp()
    by_wire = {wire_name(s.id): s for s in cortexsim_specs()}
    for tool in await mcp.list_tools():
        spec = by_wire[tool.name]
        assert set(tool.inputSchema.get("properties", {})) == set(spec.input_model.model_fields), tool.name
        assert "evidence" in tool.outputSchema["required"], tool.name
        ann = tool.annotations
        assert ann.readOnlyHint is (spec.klass == "read")
        assert ann.destructiveHint is (spec.klass == "destructive")
        assert ann.openWorldHint is (spec.network in {"tenant", "host"})
    launch = by_wire["cortexsim_run_launch"]
    assert (launch.klass, launch.network) == ("write", "host")
    assert by_wire["cortexsim_run_abort"].klass == "destructive"


@pytest.mark.asyncio
async def test_text_content_starts_with_the_statement_line():
    mcp, _, _ = _mcp({("GET", "/api/health"): HEALTH})
    res = await call(mcp, "cortexsim.health.get")
    assert not res.isError
    first, rest = res.content[0].text.split("\n", 1)
    assert first == res.structuredContent["evidence"]["statement"]
    assert first == "No tenant answered this call. This is simcore, not tenant evidence. tenant-verified: 0"
    assert json.loads(rest) == res.structuredContent
    assert res.structuredContent["evidence"]["as_of"] == AS_OF


@pytest.mark.asyncio
async def test_simcore_error_code_passes_through_as_is_error():
    nested = httpx.Response(409, json={"detail": {
        "error": "bundle cannot satisfy target", "code": "BUNDLE_TARGET_UNSATISFIABLE",
        "detail": "steps 3,4 are windows-only"}})
    mcp, _, _ = _mcp({("POST", "/api/runs"): nested})
    res = await call(mcp, "cortexsim.run.launch", scenario_id="SIM-EDR-013", mode="push")
    assert res.isError
    sc = res.structuredContent
    assert sc["code"] == "BUNDLE_TARGET_UNSATISFIABLE"
    assert sc["detail"] == "steps 3,4 are windows-only"
    assert sc["evidence"]["basis"] == "simcore"
    assert res.content[0].text == (
        "BUNDLE_TARGET_UNSATISFIABLE: bundle cannot satisfy target — steps 3,4 are windows-only")


@pytest.mark.asyncio
async def test_unexpected_exception_never_escapes():
    async def boom(ctx, args):
        raise RuntimeError("kaboom")

    specs = [dataclasses.replace(s, impl=boom) if s.id == "cortexsim.health.get" else s
             for s in cortexsim_specs()]
    mcp, _, _ = _mcp(specs=specs)
    res = await call(mcp, "cortexsim.health.get")
    assert res.isError
    assert res.structuredContent["code"] == "MCP_INTERNAL_ERROR"
    assert res.structuredContent["error"] == "RuntimeError"
    assert "evidence" in res.structuredContent


@pytest.mark.asyncio
async def test_simcore_unreachable_keeps_its_own_code():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    from mcp_server.client import SimCoreClient

    client = SimCoreClient("http://simcore.test", transport=httpx.MockTransport(handler))
    mcp = mcp_with(cortexsim_specs(), make_ctx(client))
    res = await call(mcp, "cortexsim.health.get")
    assert res.isError
    sc = res.structuredContent
    assert sc["code"] == "SIMCORE_UNREACHABLE"
    assert sc["evidence"]["statement"] == (
        "No tenant answered this call. SimCore itself was unreachable. tenant-verified: 0")
    assert sc["evidence"]["as_of"] is None


@pytest.mark.asyncio
async def test_rate_limit_refuses_tenant_tools_and_spares_simcore_tools():
    t = [0.0]
    bucket = TokenBucket(1, clock=lambda: t[0])
    assert bucket.acquire() == 0.0  # drain it
    routes = {
        ("GET", "/api/health"): HEALTH,
        ("GET", "/api/readiness"): readiness_body(integrations=[integration("lab", kind="xsiam")]),
    }
    mcp, calls, _ = _mcp(routes, bucket=bucket)
    res = await call(mcp, "cortexsim.run.reconcile", run_id="r1")
    assert res.isError and res.structuredContent["code"] == "MCP_RATE_LIMITED"
    assert res.structuredContent["data"]["retry_after_seconds"] >= 1
    assert not [p for p in paths(calls, "POST")]
    ok = await call(mcp, "cortexsim.health.get")
    assert not ok.isError


@pytest.mark.asyncio
async def test_take_token_raises_when_the_bucket_is_empty():
    client, _ = make_fake_simcore({})
    ctx = make_ctx(client, bucket=TokenBucket(1, clock=lambda: 0.0))
    take_token(ctx)
    with pytest.raises(McpToolError) as ei:
        take_token(ctx)
    assert ei.value.code == "MCP_RATE_LIMITED"


@pytest.mark.asyncio
@pytest.mark.parametrize("names,given,expect", [
    ([], None, NO_TENANT_CONFIGURED),
    (["deathstar"], None, "deathstar"),
    (["deathstar", "hoth"], None, TENANT_AMBIGUOUS),
    (["deathstar", "hoth"], "hoth", "hoth"),
])
async def test_resolve_tenant_zero_one_many(names, given, expect):
    integs = [integration(n) for n in names] + [integration("other-kind", kind="xsiam")]
    client, _ = make_fake_simcore({("GET", "/api/readiness"): readiness_body(integrations=integs)})
    ctx = make_ctx(client)
    if expect in (NO_TENANT_CONFIGURED, TENANT_AMBIGUOUS):
        with pytest.raises(McpToolError) as ei:
            await resolve_tenant(ctx, given, kind="xsiam_tenant")
        assert ei.value.code == expect
        if expect == TENANT_AMBIGUOUS:
            assert "deathstar" in ei.value.detail and "hoth" in ei.value.detail
    else:
        assert await resolve_tenant(ctx, given, kind="xsiam_tenant") == expect


def test_cap_list_and_cap_text():
    assert cap_list(list(range(60)), limit=50, offset=0) == (list(range(50)), True)
    assert cap_list(list(range(60)), limit=50, offset=50) == (list(range(50, 60)), False)
    text = "é" * 10
    assert cap_text(text, max_bytes=100) == (text, False, 20)
    cut, truncated, total = cap_text(text, max_bytes=5)
    assert (cut, truncated, total) == ("éé", True, 20)
