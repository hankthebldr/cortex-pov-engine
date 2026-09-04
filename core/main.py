"""
CortexSim — FastAPI application entry point.

Startup sequence:
  1. Configure logging (file + stdout)
  2. Initialize SQLite database (create tables)
  3. Load scenarios from YAML files
  4. Initialize tool registry / ToolInstantiator

Static files: React UI served from CORTEXSIM_STATIC_DIR at "/"
API:          All routers mounted under /api
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from config import settings
from database import get_db, init_db
from engine.scenario_loader import load_scenarios
from tools.instantiator import instantiator


# ---------------------------------------------------------------------------
# Logging setup — must happen before anything else imports the logger
# ---------------------------------------------------------------------------

def _configure_logging() -> None:
    log_level = logging.DEBUG if settings.CORTEXSIM_ENV == "development" else logging.INFO

    # Resolve log file path relative to BASE_DIR if not absolute
    log_file = settings.CORTEXSIM_LOG_FILE
    if not os.path.isabs(log_file):
        log_file = os.path.join(settings.CORTEXSIM_BASE_DIR, log_file)

    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    fmt = logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Stdout handler
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    root_logger.addHandler(stream_handler)

    # File handler (rotating — 10 MB × 5 files)
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root_logger.addHandler(file_handler)

    # Silence noisy libraries.
    #
    # sqlalchemy.engine at INFO logs every statement, and the engine's own
    # `echo` flag logs them a SECOND time through the same handler — a dev boot
    # log carried hundreds of duplicated aiosqlite lines, and a DC reading it
    # for the one real error scrolled straight past. Both are now tied to the
    # single explicit opt-in, CORTEXSIM_SQL_ECHO.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.CORTEXSIM_SQL_ECHO else logging.WARNING
    )
    logging.getLogger("aiosqlite").setLevel(
        logging.DEBUG if settings.CORTEXSIM_SQL_ECHO else logging.INFO
    )


_configure_logging()
logger = logging.getLogger("cortexsim.main")


# ---------------------------------------------------------------------------
# Lifespan handler (startup + shutdown)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan context — runs startup logic before yielding."""
    logger.info("CortexSim starting up — env=%s port=%d", settings.CORTEXSIM_ENV, settings.CORTEXSIM_PORT)

    # 0. Validate master key before anything else.  Refuses to boot production
    #    with default/empty/short CORTEXSIM_SECRET — the credentials layer
    #    would otherwise be cryptographically worthless.
    from config import validate_master_key  # noqa: PLC0415
    validate_master_key(settings.CORTEXSIM_SECRET, env=settings.CORTEXSIM_ENV)

    # 1. Initialize database (create tables).
    #    A storage fault here is a DEPLOYMENT problem, not a defect, so it gets
    #    the same treatment as the master-key guard: a named code, a plain
    #    remediation, and a distinct exit status — never a raw traceback the
    #    operator has to interpret. Exit 4 (the master-key guard uses 3).
    from database import DB_BOOT_EXIT_CODE, DatabaseBootError  # noqa: PLC0415

    try:
        await init_db()
    except DatabaseBootError as exc:
        import sys  # noqa: PLC0415

        # Terse to the log stream, full text to stderr — printing the whole
        # remediation through both handlers would show it twice, which is the
        # exact noise this pass removed from the SQL echo.
        logger.error("BOOT REFUSED [%s] — see stderr for the remediation", exc.code)
        print(f"\n[cortexsim] BOOT REFUSED — {exc.code}\n{exc}", file=sys.stderr, flush=True)
        # os._exit, not sys.exit: a SystemExit raised inside the lifespan is
        # caught by uvicorn's startup handler and reported as a generic
        # "Application startup failed", which buries the message above.
        os._exit(DB_BOOT_EXIT_CODE)
    logger.info("Database initialized at %s", settings.CORTEXSIM_BASE_DIR + "/data/cortexsim.db")

    # 2. Load scenarios from YAML
    scenarios_dir = settings.CORTEXSIM_SCENARIOS_DIR
    if not os.path.isabs(scenarios_dir):
        scenarios_dir = os.path.join(settings.CORTEXSIM_BASE_DIR, settings.CORTEXSIM_SCENARIOS_DIR)

    # 2a. Load TTP detection-card catalog BEFORE scenarios so the loader
    #     can flag dangling ttp_ref / detection_id pointers as it walks each
    #     scenario's expected_detections.
    from engine.ttp_catalog import catalog as ttp_catalog, default_corpus_dir  # noqa: PLC0415
    corpus_dir = default_corpus_dir(settings.CORTEXSIM_BASE_DIR)
    cards_loaded = ttp_catalog.load(corpus_dir)
    logger.info("TTP catalog ready: %d detection cards", cards_loaded)

    # 2b. Load Tool Adapter catalog (Phase A — tool framework). Same
    #     warn-not-fail pattern: missing adapter packs are advisory and the
    #     scenario loader logs a warning per dangling adapter_ref.
    from tools.adapter_catalog import catalog as adapter_catalog  # noqa: PLC0415
    from tools.adapter_loader import default_packs_dir  # noqa: PLC0415
    packs_dir = default_packs_dir(settings.CORTEXSIM_BASE_DIR)
    adapters_loaded = adapter_catalog.load(packs_dir)
    logger.info("Tool adapter catalog ready: %d adapter(s)", adapters_loaded)

    # 2c. Load the master UC/TC index snapshot BEFORE scenarios so the loader
    #     can validate uc_ref / tc_ref as a real foreign key (S-10..S-14).
    #     Missing snapshot → empty registry → validation degrades to advisory.
    from engine.uctc_registry import registry as uctc_registry, default_index_dir  # noqa: PLC0415
    index_dir = default_index_dir(settings.CORTEXSIM_BASE_DIR)
    tcs_loaded = uctc_registry.load(index_dir)
    logger.info(
        "UC/TC registry ready: %d test case(s) (v%s, strict_refs=%s)",
        tcs_loaded, uctc_registry.version or "?", settings.CORTEXSIM_STRICT_REFS,
    )

    # 2c. Load XSIAM API operation catalog (API harness). Declarative packs
    #     describing every callable Cortex Platform API operation; same
    #     reject-and-log, warn-not-fail policy as the adapter catalog.
    from integrations.xsiam.operations.catalog import catalog as xsiam_op_catalog  # noqa: PLC0415
    from integrations.xsiam.operations.loader import default_ops_dir  # noqa: PLC0415
    ops_loaded = xsiam_op_catalog.load(default_ops_dir(settings.CORTEXSIM_BASE_DIR))
    logger.info("XSIAM operation catalog ready: %d operation(s)", ops_loaded)

    async with _db_context() as db:
        loaded = await load_scenarios(scenarios_dir, db)
    logger.info("Scenarios loaded: %d scenario(s)", len(loaded))

    # 3a. Merge installed content into tool registry (no-op if not on a jumpbox)
    from content_loader import merge_installed_tools  # noqa: PLC0415
    try:
        merged = merge_installed_tools()
        logger.info("Content tools merged into registry: %d", merged)
    except Exception:
        logger.exception("content_loader merge failed — continuing without installed content")

    # 3. Initialize tool instantiator (set base_dir from config)
    instantiator._base_dir = settings.CORTEXSIM_BASE_DIR
    logger.info("Tool instantiator initialized base_dir=%s", settings.CORTEXSIM_BASE_DIR)

    # 3b. Rehydrate the durable pull-mode task queue (GAP-API-005). Restores
    #     undelivered tasks into the in-memory queue and fails any orphaned
    #     'running' run whose task did not survive the restart.
    from engine.orchestrator import orchestrator  # noqa: PLC0415
    try:
        async with _db_context() as db:
            stats = await orchestrator.rehydrate(db)
        logger.info(
            "Task queue rehydrated: %d restored, %d orphan(s) failed",
            stats["rehydrated"], stats["failed_orphans"],
        )
    except Exception:
        logger.exception("orchestrator rehydrate failed — continuing with empty queue")

    # 4. Background heartbeat sweep — emits agent.status SSE events when an
    #    agent crosses online → stale → offline. list_agents derives status at
    #    read time regardless; this loop exists only to push live transitions.
    import asyncio  # noqa: PLC0415
    from api.agents import heartbeat_sweep_loop  # noqa: PLC0415
    sweep_task = asyncio.create_task(heartbeat_sweep_loop(30))
    logger.info("Heartbeat sweep task started")

    # 5. Auto-reconcile loop (opt-in) — periodically validate recent runs'
    #    detections against configured connectors. OFF by default; it makes
    #    outbound calls to the customer tenant, so it must be opted into.
    reconcile_task = None
    if settings.CORTEXSIM_AUTO_RECONCILE:
        from connectors.service import auto_reconcile_loop  # noqa: PLC0415
        reconcile_task = asyncio.create_task(auto_reconcile_loop(
            settings.CORTEXSIM_AUTO_RECONCILE_INTERVAL,
            settings.CORTEXSIM_AUTO_RECONCILE_LOOKBACK,
            settings.CORTEXSIM_AUTO_RECONCILE_WINDOW,
        ))
        logger.info("Auto-reconcile loop started (interval=%ds, auto_verify=%s)",
                    settings.CORTEXSIM_AUTO_RECONCILE_INTERVAL,
                    settings.CORTEXSIM_AUTO_VERIFY)
    elif settings.CORTEXSIM_AUTO_VERIFY:
        # Verify runs as a PHASE of the reconcile sweep, so with the reconcile
        # loop off there is no timer to carry it. Deliberately NOT starting a
        # standalone timer here: silently beginning to issue XQL against a
        # customer tenant off the back of an unrelated flag is exactly the
        # surprise the opt-in exists to prevent.
        logger.warning(
            "CORTEXSIM_AUTO_VERIFY is on but CORTEXSIM_AUTO_RECONCILE is off — "
            "auto-verify is INERT. It runs as a phase of the reconcile sweep; "
            "enable CORTEXSIM_AUTO_RECONCILE too, or verify on demand with "
            "POST /api/runs/{run_id}/verify."
        )

    logger.info("CortexSim ready — listening on port %d", settings.CORTEXSIM_PORT)
    yield

    logger.info("CortexSim shutting down")
    sweep_task.cancel()
    if reconcile_task is not None:
        reconcile_task.cancel()
    for task in (sweep_task, reconcile_task):
        if task is None:
            continue
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


@asynccontextmanager
async def _db_context():
    """Context manager for getting a DB session outside of a request."""
    from database import AsyncSessionLocal  # noqa: PLC0415
    async with AsyncSessionLocal() as session:
        yield session


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="CortexSim",
    version="1.0.0",
    description="Enterprise detection simulation engine for Palo Alto Networks Cortex",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# CORS — allow all origins (jumpbox internal tool, no auth exists). Per the
# CORS spec a wildcard origin cannot carry credentials — browsers reject
# `allow_origins=["*"]` + `allow_credentials=True` outright — so credentials
# stay OFF. Nothing here sends them (no auth, no cookies, no session), so this
# keeps the API fully open while making the config spec-valid. Do not flip
# allow_credentials back to True without narrowing allow_origins off "*" — the
# two together are invalid and a browser will refuse the response.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Specific exception handlers — registered BEFORE the Exception catch-all so
# Starlette's isinstance-based dispatch resolves the most-specific handler.
# (Registration order matters: Exception registered first would shadow all
# subclass handlers because isinstance(XsiamError(), Exception) is True.)
# ---------------------------------------------------------------------------

from security.crypto import CryptoError  # noqa: E402


@app.exception_handler(CryptoError)
async def crypto_error_handler(request: Request, exc: CryptoError) -> JSONResponse:
    """Crypto failures (bad ciphertext, wrong master key) get a structured
    500 without a stack trace so we don't accidentally leak ciphertext slices
    in error bodies."""
    logger.error("CryptoError on %s %s: %s", request.method, request.url, exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Credential decryption failed",
            "code": "CRYPTO_ERROR",
            "detail": "Master key rotation or ciphertext corruption suspected. See server logs.",
        },
    )


from integrations.xsiam.exceptions import XsiamError  # noqa: E402


@app.exception_handler(XsiamError)
async def xsiam_error_handler(request: Request, exc: XsiamError) -> JSONResponse:
    """XSIAM integration failures → structured {error, code, detail} envelope.
    API key values never appear in XsiamError.detail (only HTTP status text)."""
    logger.warning("XsiamError on %s %s: %s", request.method, request.url, exc.detail)
    return JSONResponse(
        status_code=exc.http_status,
        content={"error": "XSIAM integration error", "code": exc.code, "detail": exc.detail},
    )


from fastapi.exceptions import RequestValidationError  # noqa: E402


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError,
) -> JSONResponse:
    """Pydantic 422s in the project's own ``{error, code, detail}`` envelope.

    FastAPI's default body is ``{"detail": [{"type": "missing", "loc": [...]}]}``
    — a shape that violates the structured-error contract every other endpoint
    honours, and that ``ui/src/api/client.js::formatValidationDetail`` had to
    reverse-engineer client-side. The server sends it properly now. Status stays
    422; only the body shape changes, so existing clients that read
    ``detail`` still find a list under ``detail.fields``.
    """
    fields: list[dict[str, object]] = []
    for err in exc.errors():
        # loc is ("body", "field", 0, "sub") — drop the source segment for the
        # human string but keep the whole path in the machine field.
        loc = [str(part) for part in err.get("loc", ())]
        label = ".".join(loc[1:]) or (loc[0] if loc else "(body)")
        fields.append({
            "field": label,
            "location": loc[0] if loc else "body",
            "message": err.get("msg", "invalid"),
            "type": err.get("type", "value_error"),
            "loc": loc,
        })
    summary = "; ".join(f"{f['field']}: {f['message']}" for f in fields) or "invalid request"
    return JSONResponse(
        status_code=422,
        content={
            "error": summary,
            "code": "VALIDATION_ERROR",
            "detail": {"fields": fields},
        },
    )


# ---------------------------------------------------------------------------
# Global error handler — catch-all for anything not matched above
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error %s %s", request.method, request.url)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "code": "INTERNAL_ERROR",
            "detail": str(exc),
        },
    )


# ---------------------------------------------------------------------------
# Health endpoint
#
# The implementation lives in `api/health.py` — ONE diagnostic surface, with a
# machine code and a remediation line per component. It used to be inline here,
# and that is how merge 0ef802b managed to orphan the `xsiam_operation_catalog`
# probe after a `return` where nobody could see it was dead. The names below are
# re-exported because callers (and tests) import them from `main`.
# ---------------------------------------------------------------------------

from api.health import (  # noqa: E402
    APP_VERSION as _APP_VERSION,
    commit_sha as _commit_sha,
    component_health as _component_health,
    health_payload,
    health_check,
    router as health_router,
)

app.include_router(health_router, prefix="/api")


# ---------------------------------------------------------------------------
# API routers
# ---------------------------------------------------------------------------

from api.scenarios import router as scenarios_router  # noqa: E402
# Composer draft persistence — status='draft' Scenario rows authored in the
# console. Registered BEFORE scenarios_router so /api/scenarios/drafts resolves
# to this router rather than being swallowed by scenarios' /{scenario_id}.
from api.drafts import router as drafts_router          # noqa: E402
from api.runs import router as runs_router              # noqa: E402
from api.runs import compat_router as runs_compat_router  # noqa: E402
from api.results import router as results_router        # noqa: E402
from api.tools import router as tools_router            # noqa: E402
from api.tools_dist import router as tools_dist_router  # noqa: E402
from api.agents import router as agents_router          # noqa: E402
from api.mitre import router as mitre_router            # noqa: E402
from api.infra import router as infra_router            # noqa: E402
from api.eal import router as eal_router                # noqa: E402
from api.credentials import router as credentials_router  # noqa: E402
from api.xsiam import router as xsiam_router  # noqa: E402
from api.ttps import router as ttps_router              # noqa: E402
from api.events import router as events_router          # noqa: E402
from api.connectors import router as connectors_router  # noqa: E402
from api.connectors import runs_reconcile_router        # noqa: E402
from api.storyline import router as storyline_router    # noqa: E402
from api.causality import router as causality_router    # noqa: E402
from api.pov import router as pov_router                # noqa: E402
from api.uctc import router as uctc_router              # noqa: E402
# Assertion substrate — the POS/PLT/AUT proof mechanism. The catalog loads
# lazily on first access, so a deployment with no assertions/ directory boots
# exactly as it did before.
from api.assertions import router as assertions_router  # noqa: E402
# K8s payload serving — the pod-facing half of the container delivery mode.
# For a cloud-native target the DEPLOYMENT is the agent: no beacon, no binary on
# a customer host. The init container fetches and sha256-verifies its payload
# from these endpoints. See docs/reference/k8s-delivery.md §5.
from api.payloads import router as k8s_payloads_router  # noqa: E402
# The same shelf, plane-agnostic. Three consumers pull tools from it: the Go
# beacon (EDR-plane endpoint), the K8s init container (CDR/cloud-EDR pod) and
# the console. `/api/k8s/*` stays mounted FOREVER because every manifest this
# engine has emitted hard-codes those paths; `/api/shelf/*` is an additional
# mount of the same handlers, never a redirect.
from api.payloads import shelf_router  # noqa: E402

app.include_router(drafts_router, prefix="/api")
app.include_router(scenarios_router, prefix="/api")
app.include_router(runs_router, prefix="/api")
# GAP-API-008 — backward-compat alias for the historical singular launch path
# POST /api/run. New clients should use POST /api/runs.
app.include_router(runs_compat_router, prefix="/api")
app.include_router(results_router, prefix="/api")
app.include_router(tools_router, prefix="/api")
app.include_router(tools_dist_router, prefix="/api")
app.include_router(agents_router, prefix="/api")
app.include_router(mitre_router, prefix="/api")
app.include_router(infra_router, prefix="/api")
app.include_router(eal_router, prefix="/api")
app.include_router(credentials_router, prefix="/api")
app.include_router(xsiam_router, prefix="/api")
app.include_router(ttps_router, prefix="/api")
app.include_router(events_router, prefix="/api")
# Connector framework — optional read-back / measurement loop (GAP: efficacy).
app.include_router(connectors_router, prefix="/api")
app.include_router(runs_reconcile_router, prefix="/api")
# Detection Proof Layer — per-run storyline snapshot (mounts a second router
# under the /runs prefix, same pattern as runs_reconcile_router above).
app.include_router(storyline_router, prefix="/api")
# Causality-Graph — per-run typed DAG (endpoint+network), extends the storyline
# spine; same /runs-prefixed second-router pattern.
app.include_router(causality_router, prefix="/api")
app.include_router(pov_router, prefix="/api")
app.include_router(uctc_router, prefix="/api")
app.include_router(assertions_router, prefix="/api")
app.include_router(k8s_payloads_router, prefix="/api")
app.include_router(shelf_router, prefix="/api")


# ---------------------------------------------------------------------------
# Static files — React UI (mount last so API routes take priority)
# ---------------------------------------------------------------------------

_static_dir = settings.CORTEXSIM_STATIC_DIR
if not os.path.isabs(_static_dir):
    _static_dir = os.path.join(settings.CORTEXSIM_BASE_DIR, settings.CORTEXSIM_STATIC_DIR)

if os.path.isdir(_static_dir):
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="ui")
    logger.info("Serving React UI from %s", _static_dir)
else:
    logger.warning(
        "Static dir '%s' not found — UI will not be served until 'npm run build' is executed",
        _static_dir,
    )
