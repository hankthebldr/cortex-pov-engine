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

    # Silence noisy libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.CORTEXSIM_ENV == "development" else logging.WARNING
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

    # 1. Initialize database (create tables)
    await init_db()
    logger.info("Database initialized at %s/data/cortexsim.db", settings.CORTEXSIM_BASE_DIR)

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
        logger.info("Auto-reconcile loop started (interval=%ds)",
                    settings.CORTEXSIM_AUTO_RECONCILE_INTERVAL)

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

# CORS — allow all origins (jumpbox internal tool)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
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
# ---------------------------------------------------------------------------

_APP_VERSION = "1.0.0"


def _commit_sha() -> str:
    """Best-effort build/commit identifier.

    Tries (in order): ``CORTEXSIM_COMMIT_SHA`` / ``GIT_COMMIT`` env vars, a
    stamped file at ``{BASE_DIR}/COMMIT_SHA``, then ``git rev-parse`` if a
    checkout is present. Returns ``"unknown"`` when none resolve — never raises
    (a health probe must not fail because the build wasn't stamped)."""
    for var in ("CORTEXSIM_COMMIT_SHA", "GIT_COMMIT", "SOURCE_COMMIT"):
        val = os.environ.get(var)
        if val:
            return val.strip()[:40]
    stamp = os.path.join(settings.CORTEXSIM_BASE_DIR, "COMMIT_SHA")
    try:
        if os.path.isfile(stamp):
            with open(stamp, encoding="utf-8") as fh:
                line = fh.readline().strip()
                if line:
                    return line[:40]
    except OSError:  # pragma: no cover - defensive
        pass
    try:
        import subprocess  # noqa: PLC0415

        out = subprocess.run(
            ["git", "-C", settings.CORTEXSIM_BASE_DIR, "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2, check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:  # pragma: no cover - defensive
        pass
    return "unknown"


async def _component_health() -> dict:
    """Per-component readiness — DB reachability + catalog/EAL load counts.

    Each probe is independently guarded so one failing component degrades the
    overall status to ``degraded`` rather than throwing. The DB probe runs a
    trivial ``SELECT 1``; catalog probes read the already-loaded in-process
    singletons (no disk I/O)."""
    components: dict[str, dict] = {}

    # Database — round-trip a trivial query.
    try:
        from sqlalchemy import text  # noqa: PLC0415
        from database import AsyncSessionLocal  # noqa: PLC0415

        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        components["db"] = {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        components["db"] = {"status": "error", "detail": str(exc)}

    # Scenario catalog — count rows in the durable store.
    try:
        from sqlalchemy import func, select as _select  # noqa: PLC0415
        from database import AsyncSessionLocal  # noqa: PLC0415
        from models import Scenario  # noqa: PLC0415

        async with AsyncSessionLocal() as session:
            count = await session.scalar(_select(func.count()).select_from(Scenario))
        components["scenario_catalog"] = {"status": "ok", "count": int(count or 0)}
    except Exception as exc:  # noqa: BLE001
        components["scenario_catalog"] = {"status": "error", "detail": str(exc)}

    # TTP detection-card catalog (count of distinct TTP entries).
    try:
        from engine.ttp_catalog import catalog as ttp_catalog  # noqa: PLC0415

        components["ttp_catalog"] = {"status": "ok", "count": len(ttp_catalog.all_entries())}
    except Exception as exc:  # noqa: BLE001
        components["ttp_catalog"] = {"status": "error", "detail": str(exc)}

    # Tool adapter catalog.
    try:
        from tools.adapter_catalog import catalog as adapter_catalog  # noqa: PLC0415

        components["adapter_catalog"] = {"status": "ok", "count": adapter_catalog.count()}
    except Exception as exc:  # noqa: BLE001
        components["adapter_catalog"] = {"status": "error", "detail": str(exc)}

    # Master UC/TC index snapshot.
    try:
        from engine.uctc_registry import registry as uctc_registry  # noqa: PLC0415

        components["uctc_registry"] = {
            "status": "ok" if uctc_registry.loaded else "degraded",
            "count": len(uctc_registry.all_test_cases()),
            "version": uctc_registry.version or None,
        }
    except Exception as exc:  # noqa: BLE001
        components["uctc_registry"] = {"status": "error", "detail": str(exc)}

    # EAL simulator plugin registry.
    try:
        from eal_simulator import get_default_registry  # noqa: PLC0415

        components["eal"] = {"status": "ok", "plugins": len(get_default_registry().manifest())}
    except Exception as exc:  # noqa: BLE001
        components["eal"] = {"status": "error", "detail": str(exc)}

    return components


@app.get("/api/health", tags=["health"])
async def health_check():
    """Liveness + readiness probe (GAP-API-007).

    Reports the app version, a best-effort commit SHA, and per-component
    health (db, scenario catalog, ttp catalog, adapter catalog, eal). Overall
    ``status`` is ``ok`` only when every component is ``ok``; otherwise
    ``degraded`` (the endpoint itself still returns 200 so a probe can read the
    detail)."""
    components = await _component_health()
    overall = "ok" if all(c.get("status") == "ok" for c in components.values()) else "degraded"
    return {
        "status": overall,
        "version": _APP_VERSION,
        "commit": _commit_sha(),
        "components": components,
    }


# ---------------------------------------------------------------------------
# API routers
# ---------------------------------------------------------------------------

from api.scenarios import router as scenarios_router  # noqa: E402
from api.runs import router as runs_router              # noqa: E402
from api.runs import compat_router as runs_compat_router  # noqa: E402
from api.results import router as results_router        # noqa: E402
from api.tools import router as tools_router            # noqa: E402
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

app.include_router(scenarios_router, prefix="/api")
app.include_router(runs_router, prefix="/api")
# GAP-API-008 — backward-compat alias for the historical singular launch path
# POST /api/run. New clients should use POST /api/runs.
app.include_router(runs_compat_router, prefix="/api")
app.include_router(results_router, prefix="/api")
app.include_router(tools_router, prefix="/api")
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
