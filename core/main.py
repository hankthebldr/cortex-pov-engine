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

    logger.info("CortexSim ready — listening on port %d", settings.CORTEXSIM_PORT)
    yield

    logger.info("CortexSim shutting down")


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

@app.get("/api/health", tags=["health"])
async def health_check():
    """Simple liveness probe."""
    return {"status": "ok", "version": "1.0.0"}


# ---------------------------------------------------------------------------
# API routers
# ---------------------------------------------------------------------------

from api.scenarios import router as scenarios_router  # noqa: E402
from api.runs import router as runs_router              # noqa: E402
from api.results import router as results_router        # noqa: E402
from api.tools import router as tools_router            # noqa: E402
from api.agents import router as agents_router          # noqa: E402
from api.mitre import router as mitre_router            # noqa: E402
from api.infra import router as infra_router            # noqa: E402
from api.eal import router as eal_router                # noqa: E402
from api.credentials import router as credentials_router  # noqa: E402
from api.xsiam import router as xsiam_router  # noqa: E402
from api.ttps import router as ttps_router              # noqa: E402

app.include_router(scenarios_router, prefix="/api")
app.include_router(runs_router, prefix="/api")
app.include_router(results_router, prefix="/api")
app.include_router(tools_router, prefix="/api")
app.include_router(agents_router, prefix="/api")
app.include_router(mitre_router, prefix="/api")
app.include_router(infra_router, prefix="/api")
app.include_router(eal_router, prefix="/api")
app.include_router(credentials_router, prefix="/api")
app.include_router(xsiam_router, prefix="/api")
app.include_router(ttps_router, prefix="/api")


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
