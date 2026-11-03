"""The 25 ``cortexsim.*`` tools against a fake SimCore."""
from __future__ import annotations

import json

import httpx
import pytest

from mcp_server.tools.cortexsim import specs as cortexsim_specs
from tests.mcp_server.conftest import (
    call,
    integration,
    ledger_snapshot,
    make_ctx,
    make_fake_simcore,
    mcp_with,
    paths,
    readiness_body,
)

TENANTS = readiness_body(integrations=[
    integration("deathstar", kind="xsiam_tenant", ledger=ledger_snapshot()),
    integration("lab-alerts", kind="xsiam"),
])
NOT_CHECKED = [{"claim": "tenant reachable", "checked_by": "POST /api/runs/{id}/reconcile"},
               {"claim": "beacon executes", "checked_by": "GET /api/agents"}]


def _mcp(routes):
    client, calls = make_fake_simcore(routes)
    return mcp_with(cortexsim_specs(), make_ctx(client)), calls


def _sent_json(calls, method, path):
    [req] = [r for r in calls if r.method == method and r.url.path == path]
    return req


@pytest.mark.asyncio
async def test_health_get_keeps_not_checked_verbatim():
    mcp, _ = _mcp({("GET", "/api/health"): {"status": "degraded", "not_checked": NOT_CHECKED}})
    res = await call(mcp, "cortexsim.health.get")
    assert res.structuredContent["data"]["not_checked"] == NOT_CHECKED
    assert res.structuredContent["data"]["status"] == "degraded"


@pytest.mark.asyncio
async def test_scenario_list_caps_and_flags_truncation():
    rows = [{"id": f"SIM-EDR-{i:03d}"} for i in range(60)]
    mcp, calls = _mcp({("GET", "/api/scenarios"):
                       {"scenarios": rows, "total": 60, "projection": "summary"}})
    res = await call(mcp, "cortexsim.scenario.list", plane="EDR")
    data = res.structuredContent["data"]
    assert len(data["scenarios"]) == 50 and data["truncated"] is True and data["total"] == 60
    assert res.structuredContent["evidence"]["truncated"] is True
    assert res.structuredContent["evidence"]["basis"] == "authored"
    req = _sent_json(calls, "GET", "/api/scenarios")
    assert dict(req.url.params) == {"plane": "EDR"}


@pytest.mark.asyncio
async def test_uctc_index_not_loaded_is_degraded_never_zero():
    mcp, _ = _mcp({("GET", "/api/uctc/summary"):
                   {"index_loaded": False, "index_version": None, "test_cases": 0}})
    res = await call(mcp, "cortexsim.uctc.summary")
    assert res.isError
    sc = res.structuredContent
    assert sc["code"] == "UCTC_INDEX_NOT_LOADED"
    assert sc["evidence"]["basis"] == "authored"
    assert "data" not in sc or sc.get("data") is None


@pytest.mark.asyncio
async def test_uctc_response_without_index_loaded_is_shape_unknown():
    mcp, _ = _mcp({("GET", "/api/uctc/coverage"): {"test_cases": []}})
    res = await call(mcp, "cortexsim.uctc.coverage")
    assert res.isError and res.structuredContent["code"] == "SIMCORE_SHAPE_UNKNOWN"


@pytest.mark.asyncio
async def test_run_launch_posts_the_projection_only():
    mcp, calls = _mcp({("POST", "/api/runs"):
                       {"run_id": "r1", "mode": "pull", "message": "queued"}})
    res = await call(mcp, "cortexsim.run.launch", scenario_id="SIM-EDR-001",
                     target_agent_id="agent-1", simulation_authorized=True)
    assert not res.isError
    body = json.loads(_sent_json(calls, "POST", "/api/runs").content)
    assert body == {"scenario_id": "SIM-EDR-001", "mode": "pull", "target_agent_id": "agent-1",
                    "consent": {"simulation_authorized": True}}
    for excluded in ("allow_runtime_install", "agent_profile_mode", "bas_mode_attested",
                     "soakless_attested", "attested_by"):
        assert excluded not in body


@pytest.mark.asyncio
async def test_run_launch_push_returns_run_id_mode_and_download_url_only():
    mcp, _ = _mcp({("POST", "/api/runs"): {"run_id": "r2", "mode": "push", "message": "staged",
                                            "download_url": "/api/runs/r2/bundle"}})
    res = await call(mcp, "cortexsim.run.launch", scenario_id="SIM-EDR-001", mode="push")
    assert res.structuredContent["data"] == {"run_id": "r2", "mode": "push",
                                             "download_url": "/api/runs/r2/bundle"}


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [{"allow_runtime_install": True}, {"c2_authorized": True},
                                   {"attested_by": "henry"}])
async def test_run_launch_rejects_excluded_fields_before_any_call(extra):
    mcp, calls = _mcp({("POST", "/api/runs"): {"run_id": "r1", "mode": "pull", "message": "q"}})
    res = await call(mcp, "cortexsim.run.launch", scenario_id="SIM-EDR-001", **extra)
    assert res.isError
    assert "evidence" in res.structuredContent
    assert paths(calls, "POST") == []


@pytest.mark.asyncio
async def test_run_report_is_capped_at_256_kib():
    big = "# POV report\n" + ("x" * 300_000)
    report = httpx.Response(200, text=big, headers={"content-type": "text/markdown; charset=utf-8"})
    mcp, calls = _mcp({("GET", "/api/runs/r1/report"): report})
    res = await call(mcp, "cortexsim.run.report", run_id="r1")
    data = res.structuredContent["data"]
    assert len(data["markdown"].encode()) == 262144
    assert data["truncated"] is True and data["bytes"] == len(big.encode())
    assert res.structuredContent["evidence"]["truncated"] is True
    assert dict(_sent_json(calls, "GET", "/api/runs/r1/report").url.params) == {"format": "markdown"}


@pytest.mark.asyncio
async def test_simcore_only_tools_have_null_ledger_and_round_trips():
    mcp, _ = _mcp({("GET", "/api/runs/r1"): {"run_id": "r1", "status": "completed"},
                   ("GET", "/api/readiness"): TENANTS})
    res = await call(mcp, "cortexsim.run.get", run_id="r1")
    ev = res.structuredContent["evidence"]
    assert ev["ledger"] is None and ev["http_round_trips"] is None
    assert ev["queries_issued"] == 0 and ev["tenant"] is None and ev["basis"] == "simcore"


@pytest.mark.asyncio
async def test_reconcile_reads_queries_issued_off_the_route():
    mcp, calls = _mcp({("POST", "/api/runs/r1/reconcile"): {"matched": 2, "queries_issued": 3},
                       ("GET", "/api/readiness"): TENANTS})
    res = await call(mcp, "cortexsim.run.reconcile", run_id="r1")
    ev = res.structuredContent["evidence"]
    assert ev["queries_issued"] == 3 and ev["basis"] == "reachable" and ev["tenant"] == "lab-alerts"
    assert ev["ledger"] is None  # kind xsiam carries no ledger on /api/readiness
    req = _sent_json(calls, "POST", "/api/runs/r1/reconcile")
    assert dict(req.url.params) == {"connector": "xsiam", "integration": "lab-alerts",
                                    "window_seconds": "3600"}


@pytest.mark.asyncio
async def test_reconcile_pull_failure_counts_the_failing_page():
    failed = httpx.Response(502, json={"detail": {
        "error": "pull failed", "code": "CONNECTOR_PULL_FAILED",
        "detail": {"page": 1, "status": 401}}})
    mcp, _ = _mcp({("POST", "/api/runs/r1/reconcile"): failed, ("GET", "/api/readiness"): TENANTS})
    res = await call(mcp, "cortexsim.run.reconcile", run_id="r1")
    assert res.isError
    sc = res.structuredContent
    assert sc["code"] == "CONNECTOR_PULL_FAILED"
    assert sc["evidence"]["queries_issued"] == 1
    assert sc["evidence"]["tenant"] == "lab-alerts"
    assert sc["evidence"]["basis"] == "reachable"


@pytest.mark.asyncio
async def test_verify_takes_tenant_and_ledger_and_passes_pending_through():
    mcp, calls = _mcp({("POST", "/api/runs/r1/verify"):
                       {"tc_verdict": "pass", "tenant": "deathstar", "queries_issued": 2},
                       ("GET", "/api/readiness"): TENANTS})
    res = await call(mcp, "cortexsim.run.verify", run_id="r1", force=True)
    ev = res.structuredContent["evidence"]
    assert ev["queries_issued"] == 2 and ev["tenant"] == "deathstar" and ev["basis"] == "reachable"
    assert ev["ledger"]["used_today"] == 7 and ev["ledger"]["budget"] == 500
    assert dict(_sent_json(calls, "POST", "/api/runs/r1/verify").url.params) == {
        "integration": "deathstar", "force": "true"}

    pending = {"tc_verdict": "pending", "reason": "no_tenant_integration", "tenant": None,
               "queries_issued": 0}
    mcp2, _ = _mcp({("POST", "/api/runs/r1/verify"): pending})
    res2 = await call(mcp2, "cortexsim.run.verify", run_id="r1")
    assert not res2.isError
    assert res2.structuredContent["data"] == pending
    assert res2.structuredContent["evidence"]["queries_issued"] == 0


@pytest.mark.asyncio
async def test_preflight_reads_queries_issued_off_the_report_and_leaves_round_trips_null():
    report = {"kind": "xsiam_tenant", "verdict": "ok", "queries_issued": 4,
              "stages": [{"id": "auth", "status": "ok", "code": "PF_OK", "detail": "",
                          "remediation": None, "extra": {}, "ms": 12}]}
    mcp, calls = _mcp({("POST", "/api/connectors/xsiam_tenant/preflight"): report,
                       ("GET", "/api/readiness"): TENANTS})
    res = await call(mcp, "cortexsim.connector.preflight", kind="xsiam_tenant", datasets="xdr_data")
    ev = res.structuredContent["evidence"]
    assert ev["queries_issued"] == 4
    assert ev["http_round_trips"] is None
    assert ev["tenant"] == "deathstar" and ev["basis"] == "reachable"
    assert res.structuredContent["data"]["stages"] == report["stages"]
    assert dict(_sent_json(calls, "POST", "/api/connectors/xsiam_tenant/preflight").url.params) == {
        "integration": "deathstar", "datasets": "xdr_data"}


@pytest.mark.asyncio
async def test_assertion_run_defaults_to_a_preview():
    out = {"assertion_id": "A-1", "tc_verdict": "pending", "reason": "dry_run", "dry_run": True,
           "tenant": None, "checks": []}
    mcp, calls = _mcp({("POST", "/api/assertions/A-1/run"): out})
    res = await call(mcp, "cortexsim.assertion.run", id="A-1")
    ev = res.structuredContent["evidence"]
    assert ev["basis"] == "preview" and ev["queries_issued"] == 0
    assert json.loads(_sent_json(calls, "POST", "/api/assertions/A-1/run").content)["dry_run"] is True


@pytest.mark.asyncio
async def test_assertion_run_live_without_a_reported_count_is_not_read_as_zero():
    out = {"assertion_id": "A-1", "tc_verdict": "pass", "dry_run": False, "tenant": "deathstar",
           "checks": [], "run_id": "AR-1"}
    mcp, _ = _mcp({("POST", "/api/assertions/A-1/run"): out, ("GET", "/api/readiness"): TENANTS})
    res = await call(mcp, "cortexsim.assertion.run", id="A-1", dry_run=False)
    assert res.isError
    sc = res.structuredContent
    assert sc["code"] == "SIMCORE_SHAPE_UNKNOWN"
    assert sc["data"]["run_id"] == "AR-1"  # the executed run is not lost
    assert sc["evidence"]["queries_issued"] is None
    assert "No tenant answered" not in sc["evidence"]["statement"]
    assert "not known" in sc["evidence"]["statement"] and "not zero" in sc["evidence"]["statement"]


@pytest.mark.asyncio
async def test_assertion_run_live_reads_queries_issued_off_the_route():
    out = {"assertion_id": "A-1", "tc_verdict": "pass", "dry_run": False, "tenant": "deathstar",
           "checks": [], "run_id": "AR-1", "queries_issued": 3}
    mcp, _ = _mcp({("POST", "/api/assertions/A-1/run"): out, ("GET", "/api/readiness"): TENANTS})
    res = await call(mcp, "cortexsim.assertion.run", id="A-1", dry_run=False)
    assert not res.isError
    ev = res.structuredContent["evidence"]
    assert ev["queries_issued"] == 3 and ev["basis"] == "reachable"


@pytest.mark.asyncio
async def test_assertion_list_surfaces_rejected():
    rejected = [{"source": "assertions/pos/x.yml", "code": "A-17", "detail": "cannot fail"}]
    mcp, _ = _mcp({("GET", "/api/assertions"): {
        "assertions": [{"id": "A-1"}], "count": 1, "total": 18, "by_validation_class": {},
        "rejected": rejected, "warnings": [], "probes": []}})
    res = await call(mcp, "cortexsim.assertion.list")
    assert res.structuredContent["data"]["rejected"] == rejected


def _every_tool_routes():
    run = {"run_id": "r1", "status": "completed"}
    md = httpx.Response(200, text="# report", headers={"content-type": "text/markdown"})
    uctc = {"index_loaded": True, "index_version": "v2.2"}
    return {
        ("GET", "/api/readiness"): TENANTS,
        ("GET", "/api/health"): {"status": "ok", "not_checked": []},
        ("GET", "/api/scenarios"): {"scenarios": [], "total": 0, "projection": "summary"},
        ("GET", "/api/scenarios/SIM-EDR-001"): {"id": "SIM-EDR-001"},
        ("GET", "/api/pov/profiles"): {"profiles": []},
        ("POST", "/api/pov/scope"): {"total": 0, "runnable_count": 0, "blocked_count": 0,
                                     "runnable": [], "blocked": [], "upsell": []},
        ("GET", "/api/uctc/summary"): uctc,
        ("GET", "/api/uctc/use-cases/UC-1"): uctc,
        ("GET", "/api/uctc/test-cases/TC-1"): uctc,
        ("GET", "/api/uctc/coverage"): {**uctc, "by_use_case": []},
        ("GET", "/api/uctc/gaps"): {**uctc, "gaps": [], "total": 0},
        ("GET", "/api/uctc/by-scenario/SIM-EDR-001"): uctc,
        ("POST", "/api/runs"): {"run_id": "r1", "mode": "pull", "message": "queued"},
        ("GET", "/api/runs/r1"): run,
        ("GET", "/api/runs"): {"runs": [run], "total": 1},
        ("GET", "/api/results/r1"): {"results": [], "total": 0},
        ("GET", "/api/runs/r1/report"): md,
        ("GET", "/api/runs/r1/pair"): {"run": run},
        ("POST", "/api/runs/r1/abort"): {"run_id": "r1", "status": "aborted"},
        ("POST", "/api/runs/r1/reconcile"): {"queries_issued": 1},
        ("POST", "/api/runs/r1/verify"): {"tc_verdict": "pending", "tenant": "deathstar",
                                          "queries_issued": 0},
        ("POST", "/api/connectors/xsiam_tenant/preflight"): {"queries_issued": 0, "stages": []},
        ("GET", "/api/assertions"): {"assertions": [], "count": 0, "total": 0, "rejected": []},
        ("GET", "/api/assertions/A-1"): {"id": "A-1"},
        ("POST", "/api/assertions/A-1/run"): {"assertion_id": "A-1", "dry_run": True},
    }


MINIMAL_ARGS = {
    "cortexsim.scenario.get": {"id": "SIM-EDR-001"},
    "cortexsim.pov.scope": {"profile": "enterprise"},
    "cortexsim.uctc.use_case": {"id": "UC-1"},
    "cortexsim.uctc.test_case": {"id": "TC-1"},
    "cortexsim.uctc.by_scenario": {"id": "SIM-EDR-001"},
    "cortexsim.run.launch": {"scenario_id": "SIM-EDR-001", "simulation_authorized": True},
    "cortexsim.run.get": {"run_id": "r1"},
    "cortexsim.run.results": {"run_id": "r1"},
    "cortexsim.run.report": {"run_id": "r1"},
    "cortexsim.run.pair": {"run_id": "r1"},
    "cortexsim.run.abort": {"run_id": "r1"},
    "cortexsim.run.reconcile": {"run_id": "r1"},
    "cortexsim.run.verify": {"run_id": "r1"},
    "cortexsim.connector.preflight": {"kind": "xsiam_tenant"},
    "cortexsim.assertion.get": {"id": "A-1"},
    "cortexsim.assertion.run": {"id": "A-1"},
}


@pytest.mark.asyncio
async def test_every_cortexsim_tool_returns_an_envelope():
    mcp, _ = _mcp(_every_tool_routes())
    specs = cortexsim_specs()
    assert len(specs) == 25
    for spec in specs:
        res = await call(mcp, spec.id, **MINIMAL_ARGS.get(spec.id, {}))
        assert not res.isError, (spec.id, res.content[0].text)
        ev = res.structuredContent["evidence"]
        assert ev["statement"].endswith("tenant-verified: 0"), spec.id
        assert res.content[0].text.split("\n", 1)[0] == ev["statement"], spec.id


@pytest.mark.asyncio
async def test_run_list_passes_filters_and_pages_against_the_route_total():
    runs = [{"run_id": f"r{i}"} for i in range(10)]
    mcp, calls = _mcp({("GET", "/api/runs"): {"runs": runs, "total": 42}})
    res = await call(mcp, "cortexsim.run.list", status="completed", limit=5, offset=5)
    data = res.structuredContent["data"]
    assert [r["run_id"] for r in data["runs"]] == ["r5", "r6", "r7", "r8", "r9"]
    assert data["total"] == 42 and data["truncated"] is True
    assert dict(_sent_json(calls, "GET", "/api/runs").url.params) == {"status": "completed", "limit": "10"}

    mcp2, calls2 = _mcp({("GET", "/api/runs"): {"runs": [], "total": 0}})
    await call(mcp2, "cortexsim.run.list", limit=200, offset=400)
    assert "limit" not in dict(_sent_json(calls2, "GET", "/api/runs").url.params)


@pytest.mark.asyncio
async def test_reconcile_failure_survives_a_ledger_read_that_fails():
    n = {"readiness": 0}

    def ready(request):
        n["readiness"] += 1
        if n["readiness"] == 1:
            return TENANTS
        return httpx.Response(503, json={"error": "down", "code": "DB_UNAVAILABLE", "detail": "x"})

    failed = httpx.Response(502, json={"detail": {
        "error": "pull failed", "code": "CONNECTOR_PULL_FAILED", "detail": {"page": 1}}})
    client, _ = make_fake_simcore({("GET", "/api/readiness"): ready,
                                   ("POST", "/api/runs/r1/reconcile"): failed})
    ctx = make_ctx(client)
    ctx.tenant_verified_cache_seconds = 0.0
    res = await call(mcp_with(cortexsim_specs(), ctx), "cortexsim.run.reconcile", run_id="r1")
    sc = res.structuredContent
    assert sc["code"] == "CONNECTOR_PULL_FAILED"
    assert sc["evidence"]["queries_issued"] == 1 and sc["evidence"]["ledger"] is None


@pytest.mark.asyncio
async def test_reconcile_200_without_queries_issued_is_unknown_count_with_body_kept():
    mcp, _ = _mcp({("POST", "/api/runs/r1/reconcile"): {"matched": 2},
                   ("GET", "/api/readiness"): TENANTS})
    res = await call(mcp, "cortexsim.run.reconcile", run_id="r1")
    assert res.isError
    sc = res.structuredContent
    assert sc["code"] == "SIMCORE_SHAPE_UNKNOWN"
    assert sc["evidence"]["queries_issued"] is None
    assert "not known" in sc["evidence"]["statement"] and "not zero" in sc["evidence"]["statement"]
    assert sc["data"] == {"matched": 2}


@pytest.mark.asyncio
async def test_verify_read_timeout_is_a_transport_error_with_unknown_count():
    def timeout(request):
        raise httpx.ReadTimeout("read timed out")

    mcp, _ = _mcp({("POST", "/api/runs/r1/verify"): timeout, ("GET", "/api/readiness"): TENANTS})
    res = await call(mcp, "cortexsim.run.verify", run_id="r1")
    assert res.isError
    sc = res.structuredContent
    assert sc["code"] == "SIMCORE_TRANSPORT_ERROR"
    assert sc["evidence"]["queries_issued"] is None and sc["evidence"]["tenant"] == "deathstar"


@pytest.mark.asyncio
async def test_list_shaped_tools_cap_their_primary_list():
    rows = [{"i": i} for i in range(60)]
    uctc = {"index_loaded": True, "index_version": "v2.2"}
    mcp, calls = _mcp({
        ("GET", "/api/uctc/coverage"): {**uctc, "by_use_case": rows, "by_plane": []},
        ("GET", "/api/uctc/gaps"): {**uctc, "gaps": rows, "total": 60},
        ("GET", "/api/results/r1"): {"results": rows, "total": 60},
        ("POST", "/api/pov/scope"): {"total": 120, "runnable_count": 60, "blocked_count": 60,
                                     "runnable": rows, "blocked": rows,
                                     "upsell": [{"capability": f"c{i}"} for i in range(38)]},
    })
    for tool, field, args in [("cortexsim.uctc.coverage", "by_use_case", {}),
                              ("cortexsim.uctc.gaps", "gaps", {}),
                              ("cortexsim.run.results", "results", {"run_id": "r1"})]:
        res = await call(mcp, tool, **args)
        data = res.structuredContent["data"]
        assert len(data[field]) == 50 and data["truncated"] is True and data["total"] == 60, tool
        assert res.structuredContent["evidence"]["truncated"] is True, tool
    res = await call(mcp, "cortexsim.pov.scope", profile="enterprise", limit=10, offset=55)
    data = res.structuredContent["data"]
    assert len(data["runnable"]) == 5 and len(data["blocked"]) == 5 and data["truncated"] is False
    res = await call(mcp, "cortexsim.pov.scope", profile="enterprise")
    data = res.structuredContent["data"]
    assert len(data["runnable"]) == 50 and data["truncated"] is True and data["total"] == 120
    assert len(data["upsell"]) == 38  # the upsell roll-up stays whole
    for req in [r for r in calls if r.url.path == "/api/pov/scope"]:
        sent = json.loads(req.content)
        assert "limit" not in sent and "offset" not in sent
