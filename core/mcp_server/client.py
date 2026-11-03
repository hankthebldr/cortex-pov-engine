"""httpx client for SimCore's REST API.

Every failure keeps its code. SimCore's error object arrives in two shapes,
because ``core/main.py`` registers no ``HTTPException`` handler: flat
``{error, code, detail}`` from routes returning a ``JSONResponse``, and nested
``{"detail": {error, code, detail}}`` from routes raising ``HTTPException``.
Both become :class:`SimCoreError` (the unwrapped object is kept on ``.body``); a dead SimCore is :class:`SimCoreUnreachable` (never
confusable with a tenant error); a body that is not a JSON object is
:class:`SimCoreShapeUnknown` and is never read as empty.
"""
from __future__ import annotations

import json
import urllib.parse
from typing import Any, Optional

import httpx

from .errors import SIMCORE_SHAPE_UNKNOWN, SIMCORE_UNREACHABLE


class SimCoreError(Exception):
    code = "SIMCORE_ERROR"

    def __init__(
        self, status: int, code: str, error: str, detail: str, *, body: Optional[dict] = None
    ) -> None:
        super().__init__(f"{code}: {error} - {detail}")
        self.body = body
        self.status = status
        self.code = code
        self.error = error
        self.detail = detail


class SimCoreUnreachable(SimCoreError):
    code = SIMCORE_UNREACHABLE

    def __init__(self, detail: str) -> None:
        super().__init__(0, SIMCORE_UNREACHABLE, "SimCore did not answer", detail)


class SimCoreShapeUnknown(SimCoreError):
    code = SIMCORE_SHAPE_UNKNOWN

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(status, SIMCORE_SHAPE_UNKNOWN, "SimCore returned an unrecognised response", detail)


def encode_segment(value: str) -> str:
    """One path segment, fully percent-encoded. Empty, ``.`` and ``..`` are refused."""
    if value in ("", ".", ".."):
        raise ValueError(f"{value!r} is not a valid path segment")
    return urllib.parse.quote(value, safe="")


class SimCoreClient:
    def __init__(
        self,
        base_url: str,
        *,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.last_date: Optional[str] = None
        self._http = httpx.AsyncClient(base_url=self.base_url, transport=transport, timeout=timeout)

    async def get(self, path: str, params: Optional[dict] = None) -> dict:
        return await self._request("GET", path, params=params)

    async def post(self, path: str, json: Any = None, params: Optional[dict] = None) -> dict:
        return await self._request("POST", path, json=json, params=params)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        try:
            resp = await self._http.request(method, path, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise SimCoreUnreachable(f"{method} {self.base_url}{path}: {exc}") from exc
        self.last_date = resp.headers.get("date")
        ctype = resp.headers.get("content-type", "")
        try:
            body = resp.json()
        except ValueError:
            body = None
        if not resp.is_success:
            obj = None
            if isinstance(body, dict):
                if "code" in body:
                    obj = body
                elif isinstance(body.get("detail"), dict) and "code" in body["detail"]:
                    obj = body["detail"]
            if obj is not None:
                inner = obj.get("detail", "")
                text = inner if isinstance(inner, str) else json.dumps(inner, sort_keys=True)
                raise SimCoreError(
                    resp.status_code, str(obj["code"]), str(obj.get("error", "")), text, body=obj
                )
            raise SimCoreError(
                resp.status_code,
                f"SIMCORE_HTTP_{resp.status_code}",
                f"SimCore answered HTTP {resp.status_code}",
                f"{method} {path}: content-type {ctype!r}, body {resp.content[:80]!r}",
            )
        if not isinstance(body, dict):
            raise SimCoreShapeUnknown(
                resp.status_code,
                f"{method} {path}: expected a JSON object, got content-type {ctype!r}, "
                f"first 80 bytes {resp.content[:80]!r}",
            )
        return body
