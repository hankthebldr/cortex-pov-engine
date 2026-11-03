"""The ``cortexsim.*`` group: 25 tools over SimCore's own REST API (spec 5.2).

Each tool is one REST call (Appendix A) plus the evidence envelope. SimCore
computes every number; these functions only read it off the response. A
response without a field a tool reads raises ``SimCoreShapeUnknown`` — never
read as empty, never as zero.
"""
from __future__ import annotations

from typing import Any, Optional

from ..client import SimCoreError, SimCoreShapeUnknown, encode_segment
from ..envelope import Envelope
from ..errors import SIMCORE_SHAPE_UNKNOWN, McpToolError
from ..registry import (
    TenantCallError,
    ToolContext,
    ToolSpec,
    cap_list,
    cap_text,
    evidence_for_simcore,
    evidence_for_tenant,
    ledger_for,
    readiness,
    resolve_tenant,
    take_token,
)
from ..schemas import (
    AssertionListIn,
    AssertionRunIn,
    EmptyInput,
    IdIn,
    McpLaunchRequest,
    PreflightIn,
    ReconcileIn,
    ReportIn,
    RunIdIn,
    RunListIn,
    ScenarioListIn,
    ScopeRequestIn,
    VerifyIn,
)

UCTC_INDEX_NOT_LOADED = "UCTC_INDEX_NOT_LOADED"


def _require(body: dict, where: str, *keys: str) -> dict:
    missing = [k for k in keys if k not in body]
    if missing:
        raise SimCoreShapeUnknown(200, f"{where}: response has no {', '.join(missing)}")
    return body


def _int(body: dict, where: str, key: str) -> int:
    value = _require(body, where, key)[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise SimCoreShapeUnknown(200, f"{where}: {key} is not an integer ({value!r})")
    return value


def _params(**kw: Any) -> dict:
    return {k: v for k, v in kw.items() if v is not None}


async def _simcore(ctx: ToolContext, data: Any, basis: str, truncated: bool = False) -> Envelope:
    return Envelope(data=data, evidence=await evidence_for_simcore(ctx, basis=basis, truncated=truncated))


# --- SimCore state ------------------------------------------------------------
async def health_get(ctx: ToolContext, args: EmptyInput) -> Envelope:
    body = _require(await ctx.client.get("/api/health"), "GET /api/health", "not_checked")
    return await _simcore(ctx, body, "simcore")


async def readiness_get(ctx: ToolContext, args: EmptyInput) -> Envelope:
    body = _require(await readiness(ctx, fresh=True), "GET /api/readiness", "proves", "statement")
    return await _simcore(ctx, body, "configured")


# --- catalog reads (authored) -------------------------------------------------
async def scenario_list(ctx: ToolContext, args: ScenarioListIn) -> Envelope:
    where = "GET /api/scenarios"
    body = _require(await ctx.client.get("/api/scenarios", params=_params(
        plane=args.plane, uc_ref=args.uc_ref, ttp_ref=args.ttp_ref,
        entitlement=args.entitlement, status=args.status)), where, "scenarios")
    rows = body["scenarios"]
    page, truncated = cap_list(rows, limit=args.limit, offset=args.offset)
    data = {"scenarios": page, "total": len(rows), "truncated": truncated,
            "projection": body.get("projection")}
    return await _simcore(ctx, data, "authored", truncated)


async def scenario_get(ctx: ToolContext, args: IdIn) -> Envelope:
    return await _simcore(ctx, await ctx.client.get(f"/api/scenarios/{encode_segment(args.id)}"), "authored")


async def pov_profiles(ctx: ToolContext, args: EmptyInput) -> Envelope:
    body = _require(await ctx.client.get("/api/pov/profiles"), "GET /api/pov/profiles", "profiles")
    return await _simcore(ctx, body, "authored")


async def pov_scope(ctx: ToolContext, args: ScopeRequestIn) -> Envelope:
    return await _simcore(ctx, await ctx.client.post("/api/pov/scope", json=args.model_dump()), "authored")


async def _uctc(ctx: ToolContext, path: str) -> Envelope:
    body = _require(await ctx.client.get(path), f"GET {path}", "index_loaded")
    if body["index_loaded"] is not True:
        raise McpToolError(
            UCTC_INDEX_NOT_LOADED, "the UC/TC index snapshot is not loaded on this SimCore",
            f"GET {path} answered index_loaded: false — this is degraded, not '0 test cases'",
            remediation="ship docs/uc_tc_mapping/_v2.2-source/ in the image; the registry is fail-soft",
            basis="authored")
    return await _simcore(ctx, body, "authored")


async def uctc_summary(ctx: ToolContext, args: EmptyInput) -> Envelope:
    return await _uctc(ctx, "/api/uctc/summary")


async def uctc_use_case(ctx: ToolContext, args: IdIn) -> Envelope:
    return await _uctc(ctx, f"/api/uctc/use-cases/{encode_segment(args.id)}")


async def uctc_test_case(ctx: ToolContext, args: IdIn) -> Envelope:
    return await _uctc(ctx, f"/api/uctc/test-cases/{encode_segment(args.id)}")


async def uctc_coverage(ctx: ToolContext, args: EmptyInput) -> Envelope:
    return await _uctc(ctx, "/api/uctc/coverage")


async def uctc_gaps(ctx: ToolContext, args: EmptyInput) -> Envelope:
    return await _uctc(ctx, "/api/uctc/gaps")


async def uctc_by_scenario(ctx: ToolContext, args: IdIn) -> Envelope:
    return await _uctc(ctx, f"/api/uctc/by-scenario/{encode_segment(args.id)}")


# --- runs (SimCore rows) ------------------------------------------------------
async def run_launch(ctx: ToolContext, args: McpLaunchRequest) -> Envelope:
    body = _require(await ctx.client.post("/api/runs", json=args.to_launch_body()),
                    "POST /api/runs", "run_id", "mode")
    if body["mode"] == "push":
        data = {"run_id": body["run_id"], "mode": "push",
                "download_url": _require(body, "POST /api/runs (push)", "download_url")["download_url"]}
    else:
        data = body
    return await _simcore(ctx, data, "simcore")


def _run_path(run_id: str, suffix: str = "") -> str:
    return f"/api/runs/{encode_segment(run_id)}{suffix}"


async def run_get(ctx: ToolContext, args: RunIdIn) -> Envelope:
    return await _simcore(ctx, await ctx.client.get(_run_path(args.run_id)), "simcore")


async def run_list(ctx: ToolContext, args: RunListIn) -> Envelope:
    where = "GET /api/runs"
    want = args.offset + args.limit  # the route caps limit at 500; past that, fetch unbounded
    body = await ctx.client.get("/api/runs", params=_params(
        status=args.status, scenario_id=args.scenario_id, limit=want if want <= 500 else None))
    _require(body, where, "runs")
    total = _int(body, where, "total")
    page = body["runs"][args.offset:args.offset + args.limit]
    truncated = args.offset + len(page) < total
    return await _simcore(ctx, {"runs": page, "total": total, "truncated": truncated}, "simcore", truncated)


async def run_results(ctx: ToolContext, args: RunIdIn) -> Envelope:
    path = f"/api/results/{encode_segment(args.run_id)}"
    return await _simcore(ctx, _require(await ctx.client.get(path), f"GET {path}", "results"), "simcore")


async def run_report(ctx: ToolContext, args: ReportIn) -> Envelope:
    text = await ctx.client.get_text(_run_path(args.run_id, "/report"), params={"format": args.format})
    capped, truncated, total = cap_text(text)
    data = {"run_id": args.run_id, "format": args.format, "markdown": capped,
            "bytes": total, "truncated": truncated}
    return await _simcore(ctx, data, "simcore", truncated)


async def run_pair(ctx: ToolContext, args: RunIdIn) -> Envelope:
    return await _simcore(ctx, await ctx.client.get(_run_path(args.run_id, "/pair")), "simcore")


async def run_abort(ctx: ToolContext, args: RunIdIn) -> Envelope:
    return await _simcore(ctx, await ctx.client.post(_run_path(args.run_id, "/abort")), "simcore")


# --- tenant-class (rate-limited; counts read off the route) -------------------
def _pull_failed_page(err: SimCoreError) -> int:
    """A failed pull's ``detail.page`` is the pages issued — a 401 on page 1 is one."""
    detail = err.body.get("detail") if isinstance(err.body, dict) else None
    page = detail.get("page") if isinstance(detail, dict) else None
    return page if isinstance(page, int) and not isinstance(page, bool) else 0


async def run_reconcile(ctx: ToolContext, args: ReconcileIn) -> Envelope:
    tenant = await resolve_tenant(ctx, args.tenant, kind="xsiam")
    take_token(ctx)
    path = _run_path(args.run_id, "/reconcile")
    try:
        body = await ctx.client.post(path, params={
            "connector": "xsiam", "integration": tenant, "window_seconds": args.window_seconds})
    except SimCoreError as err:
        if err.code != "CONNECTOR_PULL_FAILED":
            raise
        raise TenantCallError(err.code, err.error, err.detail, tenant=tenant,
                              queries_issued=_pull_failed_page(err),
                              ledger_snapshot=await ledger_for(ctx, tenant)) from err
    queries = _int(body, f"POST {path}", "queries_issued")
    ev = await evidence_for_tenant(ctx, tenant=tenant, queries_issued=queries,
                                   ledger_snapshot=await ledger_for(ctx, tenant))
    return Envelope(data=body, evidence=ev)


async def run_verify(ctx: ToolContext, args: VerifyIn) -> Envelope:
    tenant = await resolve_tenant(ctx, args.tenant, kind="xsiam_tenant", required=False)
    if tenant is not None:
        take_token(ctx)
    path = _run_path(args.run_id, "/verify")
    body = await ctx.client.post(path, params=_params(
        integration=tenant, force="true" if args.force else None,
        timeframe_seconds=args.timeframe_seconds))
    queries = _int(body, f"POST {path}", "queries_issued")
    named = _require(body, f"POST {path}", "tenant")["tenant"] or tenant
    ev = await evidence_for_tenant(ctx, tenant=named, queries_issued=queries,
                                   ledger_snapshot=await ledger_for(ctx, named))
    return Envelope(data=body, evidence=ev)


async def connector_preflight(ctx: ToolContext, args: PreflightIn) -> Envelope:
    tenant = await resolve_tenant(ctx, args.tenant, kind=args.kind)
    take_token(ctx)
    path = f"/api/connectors/{args.kind}/preflight"
    body = _require(await ctx.client.post(path, params=_params(integration=tenant, datasets=args.datasets)),
                    f"POST {path}", "stages")
    # The report's queries_issued already counts tenant OPERATIONS (healthcheck,
    # scope XQL, each dataset probe, each alert pull) — the ledger unit. The
    # route reports no raw HTTP count, so http_round_trips stays null.
    queries = _int(body, f"POST {path}", "queries_issued")
    ev = await evidence_for_tenant(ctx, tenant=tenant, queries_issued=queries,
                                   ledger_snapshot=await ledger_for(ctx, tenant), http_round_trips=None)
    return Envelope(data=body, evidence=ev)


# --- assertions ---------------------------------------------------------------
async def assertion_list(ctx: ToolContext, args: AssertionListIn) -> Envelope:
    where = "GET /api/assertions"
    body = _require(await ctx.client.get("/api/assertions", params=_params(
        validation_class=args.validation_class, tc_ref=args.tc_ref)), where, "assertions", "rejected")
    rows = body["assertions"]
    page, truncated = cap_list(rows, limit=args.limit, offset=args.offset)
    data = {**body, "assertions": page, "total": len(rows), "catalog_total": body.get("total"),
            "truncated": truncated}
    return await _simcore(ctx, data, "authored", truncated)


async def assertion_get(ctx: ToolContext, args: IdIn) -> Envelope:
    return await _simcore(ctx, await ctx.client.get(f"/api/assertions/{encode_segment(args.id)}"), "authored")


async def assertion_run(ctx: ToolContext, args: AssertionRunIn) -> Envelope:
    path = f"/api/assertions/{encode_segment(args.id)}/run"
    tenant: Optional[str] = args.tenant
    if not args.dry_run:
        tenant = await resolve_tenant(ctx, args.tenant, kind="xsiam_tenant", required=False)
        if tenant is not None:
            take_token(ctx)
    body = await ctx.client.post(path, json={**_params(integration=tenant),
                                             "dry_run": args.dry_run, "context": args.context})
    if args.dry_run:
        return await _simcore(ctx, body, "preview")
    named = _require(body, f"POST {path}", "tenant")["tenant"]
    if named is None:  # no tenant wired: the route resolved pending without a query
        ev = await evidence_for_tenant(ctx, tenant=None, queries_issued=0, ledger_snapshot=None)
        return Envelope(data=body, evidence=ev)
    if "queries_issued" not in body:
        raise TenantCallError(
            SIMCORE_SHAPE_UNKNOWN, "the live assertion run did not report queries_issued",
            f"POST {path} ran against tenant {named} (its result is in data); the route does not "
            "report how many tenant operations it issued, so this layer will not state a number. "
            "Do not retry to get one: a retry queries the tenant again.",
            tenant=named, ledger_snapshot=await ledger_for(ctx, named), data=body,
            remediation="add queries_issued to POST /api/assertions/{id}/run (spec Appendix D)",
            statement_override=(
                f"Tenant {named} was called, but SimCore did not report how many operations it "
                "issued; this is neither tenant evidence nor a zero. tenant-verified: {tenant_verified}"))
    ev = await evidence_for_tenant(ctx, tenant=named, queries_issued=_int(body, f"POST {path}", "queries_issued"),
                                   ledger_snapshot=await ledger_for(ctx, named))
    return Envelope(data=body, evidence=ev)


# --- the table ----------------------------------------------------------------
_SIMCORE_ONLY = "SimCore-local; makes no tenant call."


def specs() -> list[ToolSpec]:
    def s(id_, model, impl, klass, network, desc, *, scopes=(), idempotent=True):
        return ToolSpec(id=id_, input_model=model, impl=impl, klass=klass, network=network,
                        scopes=tuple(scopes), description=desc, idempotent=idempotent)

    return [
        s("cortexsim.health.get", EmptyInput, health_get, "read", "simcore",
          f"GET /api/health, passed through; not_checked[] is kept verbatim. {_SIMCORE_ONLY}"),
        s("cortexsim.readiness.get", EmptyInput, readiness_get, "read", "simcore",
          "GET /api/readiness: the four rungs AUTHORED/CONFIGURED/REACHABLE/VERIFIED, never collapsed, "
          f"each with what it proves and does not prove, and tenant-verified: N verbatim. {_SIMCORE_ONLY}"),
        s("cortexsim.scenario.list", ScenarioListIn, scenario_list, "read", "simcore",
          f"GET /api/scenarios filtered by plane/uc_ref/ttp_ref/entitlement/status; capped (default 50, "
          f"max 200) with truncated + total. {_SIMCORE_ONLY}"),
        s("cortexsim.scenario.get", IdIn, scenario_get, "read", "simcore",
          f"GET /api/scenarios/{{id}}. {_SIMCORE_ONLY}"),
        s("cortexsim.pov.profiles", EmptyInput, pov_profiles, "read", "simcore",
          f"GET /api/pov/profiles: the canned tenant entitlement profiles. {_SIMCORE_ONLY}"),
        s("cortexsim.pov.scope", ScopeRequestIn, pov_scope, "read", "simcore",
          f"POST /api/pov/scope (mutates nothing): scope the corpus to an entitlement set. {_SIMCORE_ONLY}"),
        s("cortexsim.uctc.summary", EmptyInput, uctc_summary, "read", "simcore",
          f"GET /api/uctc/summary. index_loaded: false is an error (UCTC_INDEX_NOT_LOADED), never "
          f"'0 test cases'. {_SIMCORE_ONLY}"),
        s("cortexsim.uctc.use_case", IdIn, uctc_use_case, "read", "simcore",
          f"GET /api/uctc/use-cases/{{id}}; index_loaded: false is UCTC_INDEX_NOT_LOADED. {_SIMCORE_ONLY}"),
        s("cortexsim.uctc.test_case", IdIn, uctc_test_case, "read", "simcore",
          f"GET /api/uctc/test-cases/{{id}}; index_loaded: false is UCTC_INDEX_NOT_LOADED. {_SIMCORE_ONLY}"),
        s("cortexsim.uctc.coverage", EmptyInput, uctc_coverage, "read", "simcore",
          f"GET /api/uctc/coverage; index_loaded: false is UCTC_INDEX_NOT_LOADED. {_SIMCORE_ONLY}"),
        s("cortexsim.uctc.gaps", EmptyInput, uctc_gaps, "read", "simcore",
          f"GET /api/uctc/gaps; index_loaded: false is UCTC_INDEX_NOT_LOADED. {_SIMCORE_ONLY}"),
        s("cortexsim.uctc.by_scenario", IdIn, uctc_by_scenario, "read", "simcore",
          f"GET /api/uctc/by-scenario/{{id}}; index_loaded: false is UCTC_INDEX_NOT_LOADED. {_SIMCORE_ONLY}"),
        s("cortexsim.run.launch", McpLaunchRequest, run_launch, "write", "host",
          "POST /api/runs with a projection of LaunchRequest: only consent.simulation_authorized is "
          "settable; c2/cluster/node consents, allow_runtime_install and the agent-profile attestations "
          "are refused before any call. The effect lands on a customer host. Push mode returns "
          "{run_id, mode, download_url}, never a script body.", idempotent=False),
        s("cortexsim.run.get", RunIdIn, run_get, "read", "simcore", f"GET /api/runs/{{id}}. {_SIMCORE_ONLY}"),
        s("cortexsim.run.list", RunListIn, run_list, "read", "simcore",
          f"GET /api/runs filtered by status/scenario_id, newest first; capped with truncated + total. "
          f"{_SIMCORE_ONLY}"),
        s("cortexsim.run.results", RunIdIn, run_results, "read", "simcore",
          "GET /api/results/{run_id}: each Result keeps its outcome axis verbatim (prevented needs two "
          f"witnesses; a lost beacon reads pending). {_SIMCORE_ONLY}"),
        s("cortexsim.run.report", ReportIn, run_report, "read", "simcore",
          f"GET /api/runs/{{id}}/report?format=markdown, capped at 256 KiB with truncated. {_SIMCORE_ONLY}"),
        s("cortexsim.run.pair", RunIdIn, run_pair, "read", "simcore",
          f"GET /api/runs/{{id}}/pair: the paired report/block run and the per-detection delta. {_SIMCORE_ONLY}"),
        s("cortexsim.run.abort", RunIdIn, run_abort, "destructive", "host",
          "POST /api/runs/{id}/abort: kills the run's process group on the host. Idempotent; an abort "
          "can only reduce what runs."),
        s("cortexsim.run.reconcile", ReconcileIn, run_reconcile, "write", "tenant",
          "POST /api/runs/{id}/reconcile?connector=xsiam: alert read-back from the tenant (kind xsiam), "
          "writes SimCore result rows. evidence.queries_issued is the route's pull count; a failed pull "
          "counts its failing page.", scopes=("alert.read",), idempotent=False),
        s("cortexsim.run.verify", VerifyIn, run_verify, "write", "tenant",
          "POST /api/runs/{id}/verify: Tier-2 XQL against the tenant (kind xsiam_tenant), ledger-charged. "
          "pending + reason: no_tenant_integration passes through unchanged. evidence.queries_issued is "
          "the route's count.", scopes=("xql.execute",)),
        s("cortexsim.connector.preflight", PreflightIn, connector_preflight, "read", "tenant",
          "POST /api/connectors/{kind}/preflight: staged config/dns_tls/auth/scope/datasets/clock check. "
          "evidence.queries_issued = the report's queries_issued, which counts tenant operations "
          "(healthcheck, scope XQL, each dataset probe, each alert pull); evidence.http_round_trips is "
          "null because the route reports no raw HTTP count. datasets is opt-in: each costs one query."),
        s("cortexsim.assertion.list", AssertionListIn, assertion_list, "read", "simcore",
          "GET /api/assertions: capped, with rejected[] surfaced — a guard nobody can see is not a guard. "
          f"{_SIMCORE_ONLY}"),
        s("cortexsim.assertion.get", IdIn, assertion_get, "read", "simcore",
          f"GET /api/assertions/{{id}}. {_SIMCORE_ONLY}"),
        s("cortexsim.assertion.run", AssertionRunIn, assertion_run, "write", "tenant",
          "POST /api/assertions/{id}/run. dry_run defaults true (a preview, nothing sent); a live run "
          "needs dry_run: false explicitly and queries the tenant read-only.",
          scopes=("xql.execute",), idempotent=False),
    ]
