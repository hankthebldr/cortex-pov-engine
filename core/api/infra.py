"""
CortexSim API — /api/infra router.

Endpoints:
  POST /api/infra/generate                     → render + bundle Terraform
  GET  /api/infra/modules[?provider=aws]       → list available modules
  GET  /api/infra/bundles                      → list previously generated bundles
  GET  /api/infra/bundles/{bundle_id}/download → download tar.gz
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from config import settings
from engine.infra_catalog import InfraCatalog
from engine.infra_generator import GenerationError, InfraGenerator
from engine.infra_models import (
    InfraGenerateRequest,
    InfraGenerateResponse,
    InfraModuleMetadata,
)

logger = logging.getLogger("cortexsim.api.infra")

router = APIRouter(prefix="/infra", tags=["infra"])

# -----------------------------------------------------------------------------
# Module-level paths and lazy generator
# -----------------------------------------------------------------------------

_MODULES_DIR: Path = Path(settings.CORTEXSIM_BASE_DIR) / "infra" / "modules"
_TEMPLATES_DIR: Path = Path(settings.CORTEXSIM_BASE_DIR) / "infra" / "templates"
_BLUEPRINTS_DIR: Path = Path(settings.CORTEXSIM_BASE_DIR) / "infra" / "blueprints"

_generator: Optional[InfraGenerator] = None


def _reset_generator() -> None:
    """Test helper — forces the next request to rebuild the generator with
    current module-level paths (useful when monkeypatching _BLUEPRINTS_DIR)."""
    global _generator
    _generator = None


def _get_generator() -> InfraGenerator:
    global _generator
    if _generator is None:
        catalog = InfraCatalog(modules_root=_MODULES_DIR)
        _generator = InfraGenerator(
            catalog=catalog,
            templates_dir=_TEMPLATES_DIR,
            blueprints_dir=_BLUEPRINTS_DIR,
        )
    return _generator


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------


@router.get("/modules")
def list_modules(provider: str = Query("aws")) -> dict:
    catalog = InfraCatalog(modules_root=_MODULES_DIR)
    modules = catalog.list_modules(provider=provider)
    return {"modules": [m.model_dump() for m in modules], "total": len(modules)}


@router.post("/generate", response_model=InfraGenerateResponse)
def generate_bundle(body: InfraGenerateRequest) -> InfraGenerateResponse:
    gen = _get_generator()
    try:
        return gen.generate(body)
    except GenerationError as e:
        logger.warning("generation failed: %s", e)
        raise HTTPException(
            status_code=422,
            detail={"error": str(e), "code": "GENERATION_FAILED", "detail": ""},
        )


@router.get("/bundles")
def list_bundles() -> dict:
    gen = _get_generator()
    summaries = gen.list_bundles()
    return {"bundles": [s.model_dump() for s in summaries], "total": len(summaries)}


@router.get("/bundles/{bundle_id}/download")
def download_bundle(bundle_id: str):
    gen = _get_generator()
    archive = gen.archive_path(bundle_id)
    if archive is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Bundle not found", "code": "BUNDLE_NOT_FOUND",
                    "detail": f"bundle_id='{bundle_id}'"},
        )
    return FileResponse(
        path=str(archive),
        media_type="application/gzip",
        filename=f"cortexsim-infra-{bundle_id}.tar.gz",
    )
