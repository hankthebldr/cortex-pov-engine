"""
CortexSim API -- embedded ARTEX status.

  GET /api/artex/status -- two axes: is the embedded ARTEX service reachable, and (when it is)
                           has it finished first-run admin setup.

Read-only. When ``CORTEXSIM_ARTEX_ORIGIN`` is empty ARTEX is not enabled and NO outbound call is
made (no client is even constructed). Otherwise one short GET goes to
``{origin}/artex/api/auth/status`` (ARTEX serves its API under ``/artex`` in embedded mode),
which answers ``{"initialized": bool}``. Any connect error, non-2xx or unrecognised body is
``unreachable`` -- an unrecognised shape is never read as "not initialized".

``probe_artex`` is module-level and client-injectable so the health component and the reverse
proxy can reuse it.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx
from fastapi import APIRouter

router = APIRouter(tags=["artex"])

_TIMEOUT_SECONDS = 3.0
_STATUS_PATH = "/artex/api/auth/status"


def _result(reachability: str, initialized: Optional[bool], configured: bool,
            code: str, detail: str) -> dict[str, Any]:
    return {
        "reachability": reachability,
        "initialized": initialized,
        "origin_configured": configured,
        "code": code,
        "detail": detail,
    }


async def probe_artex(settings: Any, client: Optional[httpx.AsyncClient] = None) -> dict[str, Any]:
    origin = (getattr(settings, "CORTEXSIM_ARTEX_ORIGIN", "") or "").strip().rstrip("/")
    if not origin:
        return _result("not_enabled", None, False, "ARTEX_NOT_ENABLED",
                       "CORTEXSIM_ARTEX_ORIGIN is empty; the embedded ARTEX service is not enabled.")

    url = f"{origin}{_STATUS_PATH}"
    owned = client is None
    http = client if client is not None else httpx.AsyncClient(timeout=_TIMEOUT_SECONDS)
    try:
        resp = await http.get(url, timeout=_TIMEOUT_SECONDS)
        resp.raise_for_status()
        body = resp.json()
        initialized = body.get("initialized") if isinstance(body, dict) else None
        if not isinstance(initialized, bool):
            return _result("unreachable", None, True, "ARTEX_UNREACHABLE",
                           f"ARTEX at {origin} answered with an unrecognised status body.")
    except (httpx.HTTPError, ValueError) as exc:
        return _result("unreachable", None, True, "ARTEX_UNREACHABLE",
                       f"ARTEX at {origin} did not answer: {type(exc).__name__}: {exc}")
    finally:
        if owned:
            await http.aclose()

    if initialized:
        return _result("reachable", True, True, "ARTEX_READY", "ARTEX is reachable and initialized.")
    return _result("reachable", False, True, "ARTEX_NEEDS_SETUP",
                   "ARTEX is reachable but first-run admin setup has not been completed.")


@router.get("/artex/status")
async def artex_status() -> dict[str, Any]:
    import config
    return await probe_artex(config.settings)
