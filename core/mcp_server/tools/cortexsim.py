"""The ``cortexsim.*`` group: 27 tools over SimCore's own REST API (spec 5.2).

Each tool is one REST call (Appendix A, plus the tenant reader's two run tools)
and the evidence envelope. SimCore computes every number; these functions only
read it off the response. A response without a field a tool reads raises
``SimCoreShapeUnknown`` — never read as empty, never as zero.
"""
from __future__ import annotations

from typing import Any, Optional

from connectors.tenant_reader import DATA_NOTICE, project_case_ledger

from ..client import SimCoreError, SimCoreShapeUnknown, encode_segment
from ..envelope import Envelope
from ..errors import NO_TENANT_CONFIGURED, McpToolError
from ..registry import (
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
    tenant_call,
)
from ..schemas import (
    AssertionListIn,
    AssertionRunIn,
    EmptyInput,
    IdIn,
    ListIn,
    McpLaunchRequest,
    PovScopeIn,
    PreflightIn,
    ReconcileIn,
    ReportIn,
    RunIdIn,
    RunListIn,
    RunResultsIn,
    ScenarioListIn,
    TenantEvidenceReadIn,
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


def _cap_field(body: dict, where: str, key: str, args: ListIn) -> tuple[dict, bool]:
    """Cap ``body[key]`` (section 5.5); ``total`` is the uncapped length."""
    rows = _require(body, where, key)[key]
    if not isinstance(rows, list):
        raise SimCoreShapeUnknown(200, f"{where}: {key} is not a list")
    page, truncated = cap_list(rows, limit=args.limit, offset=args.offset)
    return {**body, key: page, "total": len(rows), "truncated": truncated}, truncated


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


async def pov_scope(ctx: ToolContext, args: PovScopeIn) -> Envelope:
    where = "POST /api/pov/scope"
    body = _require(await ctx.client.post("/api/pov/scope", json=args.to_scope_body()), where,
                    "runnable", "blocked", "upsell")
    runnable, t1 = cap_list(body["runnable"], limit=args.limit, offset=args.offset)
    blocked, t2 = cap_list(body["blocked"], limit=args.limit, offset=args.offset)
    data = {**body, "runnable": runnable, "blocked": blocked, "truncated": t1 or t2}
    return await _simcore(ctx, data, "authored", t1 or t2)


async def _uctc(ctx: ToolContext, path: str, *, cap: Optional[tuple[str, ListIn]] = None) -> Envelope:
    body = _require(await ctx.client.get(path), f"GET {path}", "index_loaded")
    if body["index_loaded"] is not True:
        raise McpToolError(
            UCTC_INDEX_NOT_LOADED, "the UC/TC index snapshot is not loaded on this SimCore",
            f"GET {path} answered index_loaded: false — this is degraded, not '0 test cases'",
            remediation="ship docs/uc_tc_mapping/_v2.2-source/ in the image; the registry is fail-soft",
            basis="authored")
    if cap is None:
        return await _simcore(ctx, body, "authored")
    data, truncated = _cap_field(body, f"GET {path}", cap[0], cap[1])
    return await _simcore(ctx, data, "authored", truncated)


async def uctc_summary(ctx: ToolContext, args: EmptyInput) -> Envelope:
    return await _uctc(ctx, "/api/uctc/summary")


async def uctc_use_case(ctx: ToolContext, args: IdIn) -> Envelope:
    return await _uctc(ctx, f"/api/uctc/use-cases/{encode_segment(args.id)}")


async def uctc_test_case(ctx: ToolContext, args: IdIn) -> Envelope:
    return await _uctc(ctx, f"/api/uctc/test-cases/{encode_segment(args.id)}")


async def uctc_coverage(ctx: ToolContext, args: ListIn) -> Envelope:
    return await _uctc(ctx, "/api/uctc/coverage", cap=("by_use_case", args))


async def uctc_gaps(ctx: ToolContext, args: ListIn) -> Envelope:
    return await _uctc(ctx, "/api/uctc/gaps", cap=("gaps", args))


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
    """The run as SimCore returns it, with its case ledger projected
    (``project_case_ledger``): a case's ``resolve_comment`` sits under
    ``tenant_text`` and a rejected ``xdr_url`` is withheld. A ledger with
    cases adds ``data_notice``."""
    body = await ctx.client.get(_run_path(args.run_id))
    if isinstance(body, dict) and body.get("case_ledger") is not None:
        ledger = project_case_ledger(body["case_ledger"])
        body = {**body, "case_ledger": ledger}
        if isinstance(ledger, dict) and isinstance(ledger.get("cases"), list) and ledger["cases"]:
            body["data_notice"] = DATA_NOTICE
    return await _simcore(ctx, body, "simcore")


async def run_list(ctx: ToolContext, args: RunListIn) -> Envelope:
    where = "GET /api/runs"
    # The route pages itself (limit 1..5000, default 500, plus offset); a call
    # without them reads only the newest 500, so the page is always asked for.
    body = await ctx.client.get("/api/runs", params=_params(
        status=args.status, scenario_id=args.scenario_id,
        limit=args.limit, offset=args.offset or None))
    _require(body, where, "runs")
    total = _int(body, where, "total")
    page = body["runs"][:args.limit]
    truncated = args.offset + len(page) < total
    return await _simcore(ctx, {"runs": page, "total": total, "truncated": truncated}, "simcore", truncated)


async def run_results(ctx: ToolContext, args: RunResultsIn) -> Envelope:
    path = f"/api/results/{encode_segment(args.run_id)}"
    data, truncated = _cap_field(await ctx.client.get(path), f"GET {path}", "results", args)
    return await _simcore(ctx, data, "simcore", truncated)


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

    def pull_failed_page(err: SimCoreError) -> Optional[int]:
        return _pull_failed_page(err) if err.code == "CONNECTOR_PULL_FAILED" else None

    body, queries = await tenant_call(
        ctx, tenant=tenant,
        coro=ctx.client.post(path, params={"connector": "xsiam", "integration": tenant,
                                           "window_seconds": args.window_seconds}),
        read=lambda b: _int(b, f"POST {path}", "queries_issued"),
        failure_count=pull_failed_page)
    ev = await evidence_for_tenant(ctx, tenant=tenant, queries_issued=queries,
                                   ledger_snapshot=await ledger_for(ctx, tenant))
    return Envelope(data=body, evidence=ev)


def _verify_read(path: str):
    def read(body: dict) -> tuple[int, Optional[str]]:
        return _int(body, f"POST {path}", "queries_issued"), _require(body, f"POST {path}", "tenant")["tenant"]
    return read


async def run_verify(ctx: ToolContext, args: VerifyIn) -> Envelope:
    tenant = await resolve_tenant(ctx, args.tenant, kind="xsiam_tenant", required=False)
    path = _run_path(args.run_id, "/verify")
    coro = ctx.client.post(path, params=_params(
        integration=tenant, force="true" if args.force else None,
        timeframe_seconds=args.timeframe_seconds))
    if tenant is None:  # nothing configured: the route answers pending without a tenant call
        body = await coro
        queries, named = _verify_read(path)(body)
    else:
        take_token(ctx)
        body, (queries, named) = await tenant_call(ctx, tenant=tenant, coro=coro, read=_verify_read(path))
    named = named or tenant
    ev = await evidence_for_tenant(ctx, tenant=named, queries_issued=queries,
                                   ledger_snapshot=await ledger_for(ctx, named))
    return Envelope(data=body, evidence=ev)


async def connector_preflight(ctx: ToolContext, args: PreflightIn) -> Envelope:
    tenant = await resolve_tenant(ctx, args.tenant, kind=args.kind)
    take_token(ctx)
    path = f"/api/connectors/{args.kind}/preflight"

    # The report's queries_issued already counts tenant OPERATIONS (healthcheck,
    # scope XQL, each dataset probe, each alert pull) — the ledger unit. The
    # route reports no raw HTTP count, so http_round_trips stays null.
    def read(body: dict) -> int:
        _require(body, f"POST {path}", "stages")
        return _int(body, f"POST {path}", "queries_issued")

    body, queries = await tenant_call(
        ctx, tenant=tenant,
        coro=ctx.client.post(path, params=_params(integration=tenant, datasets=args.datasets)),
        read=read)
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
    if args.dry_run:
        body = await ctx.client.post(path, json={**_params(integration=args.tenant),
                                                 "dry_run": True, "context": args.context})
        # The 0 is the route's, read and checked — never a constant here, so a
        # route that started querying on a dry run could not hide behind "preview".
        queries = _int(body, f"POST {path}", "queries_issued")
        if queries != 0:
            raise SimCoreShapeUnknown(
                200, f"POST {path} answered a dry run with queries_issued={queries}; a preview "
                "sends nothing to the tenant, so this is refused rather than reported as a preview")
        return await _simcore(ctx, body, "preview")
    tenant = await resolve_tenant(ctx, args.tenant, kind="xsiam_tenant", required=False)
    coro = ctx.client.post(path, json={**_params(integration=tenant),
                                       "dry_run": False, "context": args.context})

    def read(body: dict) -> tuple[Optional[str], int]:
        named = _require(body, f"POST {path}", "tenant")["tenant"]
        if named is None:  # no tenant wired: the route resolved pending without a query
            return None, 0
        if "queries_issued" not in body:
            raise SimCoreShapeUnknown(
                200, f"POST {path} ran against tenant {named} (its result is in data) but does not "
                "report queries_issued (spec Appendix D); do not retry to get a count — a retry "
                "queries the tenant again")
        return named, _int(body, f"POST {path}", "queries_issued")

    if tenant is None:
        body = await coro
        named, queries = read(body)
    else:
        take_token(ctx)
        body, (named, queries) = await tenant_call(ctx, tenant=tenant, coro=coro, read=read)
    ev = await evidence_for_tenant(ctx, tenant=named, queries_issued=queries,
                                   ledger_snapshot=await ledger_for(ctx, named))
    return Envelope(data=body, evidence=ev)


# --- run tenant evidence (tenant-reader spec §7.4, §7.6) -----------------------
async def tenant_evidence_get(ctx: ToolContext, args: RunIdIn) -> Envelope:
    """The stored snapshot, read off SimCore's database: zero tenant queries."""
    path = _run_path(args.run_id, "/tenant-evidence")
    body = _require(await ctx.client.get(path), f"GET {path}", "code", "snapshot", "history")
    return await _simcore(ctx, {**body, "data_notice": DATA_NOTICE}, "simcore")


def _evidence_read(path: str):
    """``(queries_issued, tenant_answered)``: the snapshot's count when this call
    read (``read: true``; a null count stays null), else 0 — a returned snapshot
    from an earlier read is not this call's cost."""
    where = f"POST {path}"

    def read(body: dict) -> tuple[Optional[int], bool]:
        _require(body, where, "read", "code", "snapshot")
        flag, snap = body["read"], body["snapshot"]
        if not isinstance(flag, bool) or not (snap is None or isinstance(snap, dict)):
            raise SimCoreShapeUnknown(200, f"{where}: read {flag!r} / snapshot {type(snap).__name__} "
                                           "is not a boolean and an object or null")
        if flag is False:
            return 0, False
        if snap is None:
            raise SimCoreShapeUnknown(200, f"{where}: read is true but snapshot is null")
        queries = _require(snap, f"{where} snapshot", "queries_issued", "payload")["queries_issued"]
        if queries is not None and (isinstance(queries, bool) or not isinstance(queries, int)):
            raise SimCoreShapeUnknown(200, f"{where}: snapshot.queries_issued is not an integer "
                                           f"({queries!r})")
        payload = snap["payload"] if isinstance(snap["payload"], dict) else {}
        return queries, payload.get("tenant_answered") is True
    return read


async def tenant_evidence_read(ctx: ToolContext, args: TenantEvidenceReadIn) -> Envelope:
    """One explicit, metered read; the route resolves the credential itself
    (``xsiam`` or ``xsiam_tenant``, as reconcile does) and answers ``pending``
    when none is configured."""
    take_token(ctx)
    path = _run_path(args.run_id, "/tenant-evidence")
    body, (queries, answered) = await tenant_call(
        ctx, tenant=args.integration, read=_evidence_read(path),
        coro=ctx.client.post(path, params=_params(force="true" if args.force else None,
                                                   integration=args.integration)))
    snap = body["snapshot"] or {}
    tenant = (None if body["code"] == NO_TENANT_CONFIGURED
              else snap.get("integration") or args.integration)
    ev = await evidence_for_tenant(ctx, tenant=tenant, queries_issued=queries,
                                   ledger_snapshot=await ledger_for(ctx, tenant),
                                   tenant_answered=answered)
    return Envelope(data={**body, "data_notice": DATA_NOTICE}, evidence=ev)


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
        s("cortexsim.pov.scope", PovScopeIn, pov_scope, "read", "simcore",
          "POST /api/pov/scope (mutates nothing): scope the corpus to an entitlement set. runnable[] and "
          "blocked[] are capped by limit/offset (default 50, max 200; not posted to REST) with truncated; "
          "the route's total/runnable_count/blocked_count are kept; the upsell roll-up (at most one row "
          f"per SKU, 38 today) is returned whole. {_SIMCORE_ONLY}"),
        s("cortexsim.uctc.summary", EmptyInput, uctc_summary, "read", "simcore",
          f"GET /api/uctc/summary. index_loaded: false is an error (UCTC_INDEX_NOT_LOADED), never "
          f"'0 test cases'. {_SIMCORE_ONLY}"),
        s("cortexsim.uctc.use_case", IdIn, uctc_use_case, "read", "simcore",
          f"GET /api/uctc/use-cases/{{id}}; index_loaded: false is UCTC_INDEX_NOT_LOADED. {_SIMCORE_ONLY}"),
        s("cortexsim.uctc.test_case", IdIn, uctc_test_case, "read", "simcore",
          f"GET /api/uctc/test-cases/{{id}}; index_loaded: false is UCTC_INDEX_NOT_LOADED. {_SIMCORE_ONLY}"),
        s("cortexsim.uctc.coverage", ListIn, uctc_coverage, "read", "simcore",
          "GET /api/uctc/coverage; by_use_case[] capped with truncated + total; index_loaded: false is "
          f"UCTC_INDEX_NOT_LOADED. {_SIMCORE_ONLY}"),
        s("cortexsim.uctc.gaps", ListIn, uctc_gaps, "read", "simcore",
          "GET /api/uctc/gaps; gaps[] capped with truncated + total; index_loaded: false is "
          f"UCTC_INDEX_NOT_LOADED. {_SIMCORE_ONLY}"),
        s("cortexsim.uctc.by_scenario", IdIn, uctc_by_scenario, "read", "simcore",
          f"GET /api/uctc/by-scenario/{{id}}; index_loaded: false is UCTC_INDEX_NOT_LOADED. {_SIMCORE_ONLY}"),
        s("cortexsim.run.launch", McpLaunchRequest, run_launch, "write", "host",
          "POST /api/runs with a projection of LaunchRequest: only consent.simulation_authorized is "
          "settable; c2/cluster/node consents, allow_runtime_install and the agent-profile attestations "
          "are refused before any call. The effect lands on a customer host. Push mode returns "
          "{run_id, mode, download_url}, never a script body.", idempotent=False),
        s("cortexsim.run.get", RunIdIn, run_get, "read", "simcore",
          f"GET /api/runs/{{id}}. case_ledger.cases[] come projected: a case's resolve_comment sits "
          "under tenant_text (neutralised, capped, text_truncated) and an xdr_url that is not https "
          "on paloaltonetworks.com is null with xdr_url_withheld: true. A ledger with cases adds "
          f"data_notice: {DATA_NOTICE} {_SIMCORE_ONLY}"),
        s("cortexsim.run.list", RunListIn, run_list, "read", "simcore",
          f"GET /api/runs filtered by status/scenario_id, newest first; capped with truncated + total. "
          f"{_SIMCORE_ONLY}"),
        s("cortexsim.run.results", RunResultsIn, run_results, "read", "simcore",
          "GET /api/results/{run_id}, capped with truncated + total: each Result keeps its outcome axis verbatim (prevented needs two "
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
        s("cortexsim.run.tenant_evidence.get", RunIdIn, tenant_evidence_get, "read", "simcore",
          "GET /api/runs/{id}/tenant-evidence: the latest stored tenant-evidence snapshot (issues, "
          "cases, endpoint record, agreement with the matcher), history[] (last 10: id, read_at, "
          "code) and case_ledger (projected: a case's resolve_comment under tenant_text, a "
          "rejected xdr_url null with xdr_url_withheld); never read is snapshot: null with "
          "TENANT_EVIDENCE_NOT_READ. "
          "Holds no tenant key and makes no tenant call: it returns what SimCore stored. "
          f"data_notice: {DATA_NOTICE}"),
        s("cortexsim.run.tenant_evidence.read", TenantEvidenceReadIn, tenant_evidence_read, "read",
          "tenant",
          "POST /api/runs/{id}/tenant-evidence[?force&integration]: one explicit, metered, "
          "read-only read of the run's issues, cases and endpoint record, stored as a snapshot "
          "(its only write); it never changes a score. Inside the minimum interval it answers "
          "read: false (TENANT_EVIDENCE_READ_RECENTLY) with the stored snapshot unless force; no "
          "credential is pending with NO_TENANT_CONFIGURED; a push run or one with no executed "
          "step is NO_RUN_WINDOW. evidence.queries_issued is snapshot.queries_issued when read is "
          "true, else 0. Rate-limited. Holds no tenant key: SimCore reads with the integration "
          "credential stored at tenant onboarding, under the per-tenant request budget reconcile "
          f"shares. data_notice: {DATA_NOTICE}",
          scopes=("alert.read",), idempotent=False),
    ]
