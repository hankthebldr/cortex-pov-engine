# core/integrations/xsiam/client.py
"""Thin async httpx client for the Cortex XSIAM/XDR public API (Standard auth).

The `transport` kwarg lets tests inject httpx.MockTransport so the real client
code runs against a faked network (no live tenant, no new deps).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from .auth import standard_auth_headers
from .config import AuthMode, XsiamTenantConfig
from .exceptions import (
    XsiamApiError, XsiamAuthError, XsiamConfigError,
    XsiamQuotaError,
)

logger = logging.getLogger("cortexsim.xsiam")


class XsiamClient:
    def __init__(
        self,
        config: XsiamTenantConfig,
        api_key: str,
        *,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        timeout: float = 30.0,
    ):
        if config.auth_mode is not AuthMode.standard:
            raise XsiamConfigError(
                f"auth_mode '{config.auth_mode.value}' unsupported in Slice 1 (standard only)"
            )
        self._base = config.base_url.rstrip("/")
        self._headers = standard_auth_headers(api_key, config.api_key_id)
        self._transport = transport
        self._timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base,
            headers=self._headers,
            transport=self._transport,
            timeout=self._timeout,
        )

    async def healthcheck(self) -> dict[str, Any]:
        async with self._client() as c:
            resp = await c.get("/public_api/v1/healthcheck")
        data = self._unwrap(resp)
        # Some tenants wrap in {reply: {...}}, others return the object directly.
        return data.get("reply", data) if isinstance(data, dict) else {"status": data}

    async def ping_via_endpoints(self) -> int:
        """Liveness fallback when /healthcheck is license-gated (403).

        A single get_endpoints call asking for one row — used only to prove the
        key authenticates. NOT the Slice-2 agent-fleet-health feature.
        """
        body = {"request_data": {"search_from": 0, "search_to": 1}}
        async with self._client() as c:
            resp = await c.post("/public_api/v1/endpoints/get_endpoints", json=body)
        data = self._unwrap(resp)
        reply = data.get("reply", data) if isinstance(data, dict) else {}
        return int(reply.get("result_count", 0) or 0)

    def _unwrap(self, resp: httpx.Response) -> Any:
        if resp.status_code == 401:
            raise XsiamAuthError("XSIAM rejected the API key (HTTP 401)")
        if resp.status_code == 429:
            raise XsiamQuotaError("XSIAM rate/quota limit hit (HTTP 429)")
        if resp.status_code >= 400:
            raise XsiamApiError(
                f"XSIAM API error (HTTP {resp.status_code})",
                upstream_status=resp.status_code,
            )
        try:
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            raise XsiamApiError("XSIAM returned a non-JSON response") from exc
