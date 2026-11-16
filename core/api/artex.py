"""
CortexSim API -- embedded ARTEX status.

  GET /api/artex/status -- two axes: is the embedded ARTEX service reachable, and (when it is)
                           has it finished first-run admin setup.

Read-only. When ``CORTEXSIM_ARTEX_ORIGIN`` is empty ARTEX is not enabled and NO outbound call is
made (no client is even constructed). Otherwise one short GET goes to
``{origin}/artex/api/auth/status`` (ARTEX serves its API under ``/artex`` in embedded mode),
which answers ``{"initialized": bool}``. Any connect error, non-2xx or unrecognised body is
``unreachable`` -- an unrecognised shape is never read as "not initialized".

  ANY /artex/{path}      -- transparent, streaming reverse proxy (``proxy_router``, mounted at the
                           app root, NOT under /api). The ``/artex`` prefix is kept on the wire
                           because ARTEX strips it itself. Responses are never buffered, so
                           ``text/event-stream`` frames flow through as the upstream emits them.

``probe_artex`` is module-level and client-injectable so the health component and the reverse
proxy can reuse it.
"""
from __future__ import annotations

from typing import Any, Optional
from urllib.parse import unquote, urlparse

import httpx
from fastapi import APIRouter, Request
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse, RedirectResponse, StreamingResponse

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


# ---------------------------------------------------------------------------
# Reverse proxy
# ---------------------------------------------------------------------------

proxy_router = APIRouter(tags=["artex"])

# Test seam: an httpx transport to use instead of the network (same role as the
# injectable client on probe_artex). None in production.
_transport: Optional[httpx.AsyncBaseTransport] = None

_HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade",
    "proxy-authorization", "proxy-authenticate", "proxy-connection",
})
# Dropped inbound only: host is the upstream's, content-length is recomputed by httpx.
_DROP_REQUEST = _HOP_BY_HOP | {"host", "content-length"}
# Dropped outbound only: the body is streamed, so the length is not known to Starlette.
_DROP_RESPONSE = _HOP_BY_HOP | {"content-length"}


def _error(status: int, code: str, error: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": error, "code": code, "detail": detail})


# Accepted v1 limitations (deliberately not fixed):
#   * the request body is read fully into memory, not streamed upstream (fine for ARTEX's API);
#   * WebSockets are not proxied (ARTEX streams over SSE).

_PREFIX = "/artex"
_STATE_CHANGING = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _bad_path(detail: str) -> JSONResponse:
    return _error(400, "ARTEX_PROXY_BAD_PATH", "Invalid ARTEX proxy path", detail)


def _upstream_rest(request: Request) -> Optional[str]:
    """The still-encoded path after /artex, or None when it could escape the prefix."""
    raw = request.scope.get("raw_path")
    raw_path = raw.decode("latin-1") if raw else request.url.path.replace("%", "%25")
    if "?" in raw_path:  # defensive: raw_path should carry no query
        raw_path = raw_path.split("?", 1)[0]
    if not raw_path.startswith(_PREFIX):
        return None
    rest = raw_path[len(_PREFIX):]
    if rest and not rest.startswith("/"):
        return None
    for seg in rest.split("/")[1:]:
        dec = unquote(seg)
        if dec in (".", "..") or any(c in dec for c in "\\?#%/"):
            return None
        if "\\" in seg:
            return None
    return rest or "/"


def _cross_site(request: Request) -> bool:
    if request.method not in _STATE_CHANGING:
        return False
    sfs = (request.headers.get("sec-fetch-site") or "").lower()
    if sfs == "cross-site":
        return True
    if sfs == "same-origin":
        return False
    origin = request.headers.get("origin")
    if origin is not None:
        return urlparse(origin).netloc.lower() != (request.headers.get("host") or "").lower()
    return False


@proxy_router.get("/artex", include_in_schema=False)
async def artex_bare(request: Request):
    target = "/artex/" + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(target, status_code=307)


@proxy_router.api_route(
    "/artex/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
async def artex_proxy(path: str, request: Request):
    import config

    origin = (getattr(config.settings, "CORTEXSIM_ARTEX_ORIGIN", "") or "").strip().rstrip("/")
    if not origin:
        return _error(404, "ARTEX_NOT_ENABLED", "ARTEX is not enabled",
                      "CORTEXSIM_ARTEX_ORIGIN is empty; the embedded ARTEX service is not enabled.")

    if _cross_site(request):
        return _error(403, "ARTEX_PROXY_CROSS_SITE", "Cross-site request refused",
                      "State-changing requests to ARTEX must be same-origin.")
    rest = _upstream_rest(request)
    if rest is None:
        return _bad_path("The path has a dot segment, backslash or encoded separator.")
    url = f"{origin}{_PREFIX}{rest}"
    if not httpx.URL(url).path.startswith(_PREFIX + "/"):
        return _bad_path("The path would leave the /artex prefix.")
    if request.url.query:
        url = f"{url}?{request.url.query}"
    headers = [(k, v) for k, v in request.headers.raw
               if k.decode("latin-1").lower() not in _DROP_REQUEST]
    body = await request.body()

    # No read timeout: SSE streams legitimately idle between frames.
    client = httpx.AsyncClient(transport=_transport,
                               timeout=httpx.Timeout(connect=5.0, read=None, write=30.0, pool=5.0))
    try:
        upstream = await client.send(
            client.build_request(request.method, url, headers=headers, content=body or None),
            stream=True,
        )
    except httpx.HTTPError as exc:
        await client.aclose()
        return _error(502, "ARTEX_PROXY_UPSTREAM_ERROR", "ARTEX upstream unreachable",
                      f"{type(exc).__name__}: {exc}")

    async def _close() -> None:
        await upstream.aclose()
        await client.aclose()

    async def _body():
        # Raw (still content-encoded) bytes, forwarded chunk by chunk as they arrive.
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await _close()

    response = StreamingResponse(_body(), status_code=upstream.status_code,
                                 background=BackgroundTask(_close))
    response.raw_headers = [(k, v) for k, v in upstream.headers.raw
                            if k.decode("latin-1").lower() not in _DROP_RESPONSE]
    return response
