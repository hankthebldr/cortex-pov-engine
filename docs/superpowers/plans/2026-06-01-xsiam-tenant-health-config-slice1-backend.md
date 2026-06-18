# XSIAM Tenant Health & Config — Slice 1 (Backend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the CortexSim FastAPI backend a read-only XSIAM tenant Health & Config surface — register a tenant, prove it's reachable (`/healthcheck`), and pull health metrics + ad-hoc results via XQL — built on the already-shipped credential layer.

**Architecture:** A new `core/integrations/xsiam/` package holds a thin async `httpx` client (Standard auth), typed tenant config, and the XQL start/poll lifecycle. A thin `core/api/xsiam.py` router exposes only live-tenant operations; tenant CRUD reuses the existing `/api/credentials/integrations` endpoints (decision B). XSIAM failures map to the repo's `{error, code, detail}` envelope via a global exception handler, mirroring the existing `CryptoError` handler.

**Tech Stack:** Python 3.11, FastAPI, async SQLAlchemy, `httpx` (already a dep), Pydantic v2, pytest + pytest-asyncio. Tests use `httpx.MockTransport` (no new deps) + an env-gated live smoke test.

**Spec:** `docs/superpowers/specs/2026-06-01-xsiam-tenant-health-config-integration-design.md`

**Henry-owned contribution points (do NOT auto-fill — see Tasks 2 & 6):**
1. `XsiamTenantConfig.base_url` validator strictness (security judgment). Task 2 ships a working reference; Henry tunes.
2. `INGESTION_HEALTH_XQL` query body (XSIAM domain knowledge). Task 6 ships the slot + contract; Henry writes the query (verified at smoke-test time).

**Scope note:** UI (`client.js` additions + `TenantManager.jsx` + tab wiring) is **Slice 1b**, planned after this backend lands and the smoke test pins the real response shapes.

---

## File Structure

| File | Responsibility |
|---|---|
| `core/integrations/__init__.py` | Package marker (new namespace). |
| `core/integrations/xsiam/__init__.py` | Package marker. |
| `core/integrations/xsiam/exceptions.py` | `XsiamError` base + subclasses carrying `code` + `http_status`. |
| `core/integrations/xsiam/config.py` | `XsiamTenantConfig` (Pydantic) + `AuthMode` enum + `base_url` validator. |
| `core/integrations/xsiam/auth.py` | `standard_auth_headers()` builder. |
| `core/integrations/xsiam/client.py` | `XsiamClient`: `healthcheck()`, XQL `start`/`get`/`run_xql()`, `ping_via_endpoints()`. |
| `core/integrations/xsiam/queries.py` | `INGESTION_HEALTH_XQL` slot + `shape_ingestion_results()`. |
| `core/integrations/xsiam/loader.py` | `load_xsiam_client(session, name)` via `CredentialStore`. |
| `core/api/xsiam.py` | Thin router: `/tenants/{name}/test|health|metrics|xql`. |
| `core/main.py` | Register router + `XsiamError` exception handler (modify). |
| `tests/integration/xsiam/*` | Unit (MockTransport) + API (TestClient) + env-gated smoke. |
| `CLAUDE.md`, `CORTEXSIM_AGENT_CONTEXT.md` | "No Cortex API connection" caveat (modify). |

Conventions to follow (verified against the codebase):
- Modules under `core/` import as top-level (`from config import settings`, `from security import CredentialStore`) because `core/` is on `sys.path` (see `tests/conftest.py`).
- Routers: `APIRouter(prefix="/...", tags=[...])`, `Depends(get_db)`, `await session.commit()` after writes, `HTTPException(404, ...)` for not-found.
- The `{error, code, detail}` envelope is produced by **global** exception handlers in `main.py` — do not build it per-route. The `CryptoError` handler (main.py) is the exact pattern to copy.

---

## Task 1: Package scaffold + exceptions

**Files:**
- Create: `core/integrations/__init__.py` (empty)
- Create: `core/integrations/xsiam/__init__.py` (empty)
- Create: `core/integrations/xsiam/exceptions.py`
- Create: `tests/integration/__init__.py` (empty)
- Create: `tests/integration/xsiam/__init__.py` (empty)
- Test: `tests/integration/xsiam/test_exceptions.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/xsiam/test_exceptions.py
from __future__ import annotations


def test_exception_hierarchy_and_codes():
    from integrations.xsiam.exceptions import (
        XsiamError, XsiamConfigError, XsiamAuthError,
        XsiamApiError, XsiamQueryError, XsiamQuotaError,
    )
    # All subclasses derive from XsiamError
    for cls in (XsiamConfigError, XsiamAuthError, XsiamApiError, XsiamQueryError, XsiamQuotaError):
        assert issubclass(cls, XsiamError)

    # Each carries a stable code + an HTTP status for the envelope
    assert XsiamConfigError("x").http_status == 400
    assert XsiamQuotaError("x").http_status == 429
    assert XsiamAuthError("x").code == "XSIAM_AUTH_ERROR"

    # XsiamApiError remembers the upstream status (drives the 403 fallback)
    err = XsiamApiError("boom", upstream_status=403)
    assert err.upstream_status == 403
    assert err.detail == "boom"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/xsiam/test_exceptions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'integrations.xsiam'`

- [ ] **Step 3: Create the package markers and exceptions**

Create the four empty `__init__.py` files (`core/integrations/__init__.py`, `core/integrations/xsiam/__init__.py`, `tests/integration/__init__.py`, `tests/integration/xsiam/__init__.py`), each containing a single comment line, then:

```python
# core/integrations/xsiam/exceptions.py
"""Typed failures for the XSIAM integration.

Each error carries a stable ``code`` and an ``http_status`` so the global
exception handler in core/main.py can render the repo's {error, code, detail}
envelope without per-route plumbing (mirrors security.crypto.CryptoError).
"""
from __future__ import annotations

from typing import Optional


class XsiamError(RuntimeError):
    code = "XSIAM_ERROR"
    http_status = 502  # default: bad upstream

    def __init__(self, detail: str, *, upstream_status: Optional[int] = None):
        super().__init__(detail)
        self.detail = detail
        self.upstream_status = upstream_status


class XsiamConfigError(XsiamError):
    code = "XSIAM_CONFIG_ERROR"
    http_status = 400  # caller's tenant config is wrong


class XsiamAuthError(XsiamError):
    code = "XSIAM_AUTH_ERROR"
    http_status = 502  # tenant rejected OUR key — upstream, not caller


class XsiamApiError(XsiamError):
    code = "XSIAM_API_ERROR"
    http_status = 502


class XsiamQueryError(XsiamError):
    code = "XSIAM_QUERY_ERROR"
    http_status = 502


class XsiamQuotaError(XsiamError):
    code = "XSIAM_QUOTA_ERROR"
    http_status = 429
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/integration/xsiam/test_exceptions.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/integrations/__init__.py core/integrations/xsiam/__init__.py \
        core/integrations/xsiam/exceptions.py \
        tests/integration/__init__.py tests/integration/xsiam/__init__.py \
        tests/integration/xsiam/test_exceptions.py
git commit -m "feat(xsiam): scaffold integration package + typed exceptions"
```

---

## Task 2: `XsiamTenantConfig` + base_url validation  ⟶ **Henry contribution**

**Files:**
- Create: `core/integrations/xsiam/config.py`
- Test: `tests/integration/xsiam/test_config.py`

The test encodes the *contract*; the validator body is Henry's to tune (strictness is a security judgment — this is the URL we send the customer API key to). Step 3 ships a working reference so the build stays green.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/xsiam/test_config.py
from __future__ import annotations

import pytest


def test_valid_tenant_config():
    from integrations.xsiam.config import XsiamTenantConfig, AuthMode
    cfg = XsiamTenantConfig(
        base_url="https://api-acme.xdr.us.paloaltonetworks.com",
        region="us",
        auth_mode="standard",
        api_key_id="42",
    )
    assert cfg.auth_mode is AuthMode.standard
    assert cfg.base_url.startswith("https://api-")


@pytest.mark.parametrize("bad_url", [
    "http://api-acme.xdr.us.paloaltonetworks.com",      # not https
    "https://acme.example.com",                          # not a PANW tenant FQDN
    "https://api-acme.xdr.us.paloaltonetworks.com.evil.com",  # suffix smuggling
    "not-a-url",
])
def test_rejects_dangerous_base_url(bad_url):
    from pydantic import ValidationError
    from integrations.xsiam.config import XsiamTenantConfig
    with pytest.raises(ValidationError):
        XsiamTenantConfig(base_url=bad_url, region="us", auth_mode="standard", api_key_id="1")


def test_rejects_unknown_auth_mode():
    from pydantic import ValidationError
    from integrations.xsiam.config import XsiamTenantConfig
    with pytest.raises(ValidationError):
        XsiamTenantConfig(base_url="https://api-x.xdr.us.paloaltonetworks.com",
                          region="us", auth_mode="sso", api_key_id="1")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/xsiam/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'integrations.xsiam.config'`

- [ ] **Step 3: Implement `config.py` (reference — Henry may tighten the regex)**

```python
# core/integrations/xsiam/config.py
"""Typed XSIAM tenant configuration.

Decision B stores tenants in the generic IntegrationCredential.config JSON blob,
which is schema-agnostic. This model is where XSIAM-specific validation actually
happens — applied when the client loads a tenant (loader.py), so a malformed
tenant fails cleanly at /test rather than silently.
"""
from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, field_validator


class AuthMode(str, Enum):
    standard = "standard"
    advanced = "advanced"   # Slice 1 client supports `standard` only (Advanced is later)


# ── CONTRIBUTION POINT (Henry) ──────────────────────────────────────────────
# This is the only place a customer's tenant URL is validated before we send
# their API key to it. Too loose = SSRF-shaped credential leak to a typo'd or
# malicious host. Too strict = breaks when PANW adds a region/FQDN shape.
# Reference below accepts https://api-<sub>.xdr.<region>.paloaltonetworks.com.
# Tighten or loosen to match the tenant FQDNs you actually see in the field.
_TENANT_FQDN = re.compile(
    r"^https://api-[a-z0-9][a-z0-9-]*\.xdr\.[a-z0-9.-]+\.paloaltonetworks\.com/?$",
    re.IGNORECASE,
)


class XsiamTenantConfig(BaseModel):
    base_url: str
    region: str
    auth_mode: AuthMode = AuthMode.standard
    api_key_id: str

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, v: str) -> str:
        if not _TENANT_FQDN.match(v or ""):
            raise ValueError(
                "base_url must be https://api-<sub>.xdr.<region>.paloaltonetworks.com"
            )
        return v.rstrip("/")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/integration/xsiam/test_config.py -v`
Expected: PASS (all 4 parametrized bad URLs raise; valid config builds)

> **Henry:** if you tighten `_TENANT_FQDN` (e.g. allowlist exact regions), add the new accept/reject cases to the parametrized test first, then change the regex.

- [ ] **Step 5: Commit**

```bash
git add core/integrations/xsiam/config.py tests/integration/xsiam/test_config.py
git commit -m "feat(xsiam): typed tenant config with base_url FQDN validation"
```

---

## Task 3: Standard auth header builder

**Files:**
- Create: `core/integrations/xsiam/auth.py`
- Test: `tests/integration/xsiam/test_auth_standard.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/xsiam/test_auth_standard.py
from __future__ import annotations


def test_standard_auth_headers():
    from integrations.xsiam.auth import standard_auth_headers
    h = standard_auth_headers("the-api-key", "42")
    assert h["x-xdr-auth-id"] == "42"
    assert h["Authorization"] == "the-api-key"
    assert h["Content-Type"] == "application/json"


def test_standard_auth_coerces_key_id_to_str():
    from integrations.xsiam.auth import standard_auth_headers
    h = standard_auth_headers("k", 7)  # api_key_id sometimes arrives as int
    assert h["x-xdr-auth-id"] == "7"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/xsiam/test_auth_standard.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement `auth.py`**

```python
# core/integrations/xsiam/auth.py
"""Auth header builders for the Cortex XSIAM/XDR public API.

Slice 1 implements Standard (static-header) auth. Advanced (SHA-256 signed
nonce+timestamp) auth is a later slice and slots in here as a sibling builder
without touching the client.
"""
from __future__ import annotations


def standard_auth_headers(api_key: str, api_key_id) -> dict[str, str]:
    return {
        "x-xdr-auth-id": str(api_key_id),
        "Authorization": api_key,
        "Content-Type": "application/json",
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/integration/xsiam/test_auth_standard.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/integrations/xsiam/auth.py tests/integration/xsiam/test_auth_standard.py
git commit -m "feat(xsiam): standard auth header builder"
```

---

## Task 4: `XsiamClient` — healthcheck + response unwrapping + endpoints probe

**Files:**
- Create: `core/integrations/xsiam/client.py`
- Test: `tests/integration/xsiam/test_client_healthcheck.py`

This task introduces the client with a `transport` injection point so tests drive it with `httpx.MockTransport` (real client code, faked network).

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/xsiam/test_client_healthcheck.py
from __future__ import annotations

import httpx
import pytest

from integrations.xsiam.config import XsiamTenantConfig

CFG = XsiamTenantConfig(
    base_url="https://api-test.xdr.us.paloaltonetworks.com",
    region="us", auth_mode="standard", api_key_id="1",
)


def _transport(handler):
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_healthcheck_ok():
    from integrations.xsiam.client import XsiamClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/public_api/v1/healthcheck"
        assert request.headers["x-xdr-auth-id"] == "1"
        return httpx.Response(200, json={"status": "All systems normal"})

    client = XsiamClient(CFG, "key", transport=_transport(handler))
    out = await client.healthcheck()
    assert out["status"] == "All systems normal"


@pytest.mark.asyncio
async def test_healthcheck_401_raises_auth_error():
    from integrations.xsiam.client import XsiamClient
    from integrations.xsiam.exceptions import XsiamAuthError

    client = XsiamClient(CFG, "bad", transport=_transport(
        lambda r: httpx.Response(401, json={"err_msg": "nope"})))
    with pytest.raises(XsiamAuthError):
        await client.healthcheck()


@pytest.mark.asyncio
async def test_healthcheck_403_carries_upstream_status():
    from integrations.xsiam.client import XsiamClient
    from integrations.xsiam.exceptions import XsiamApiError

    client = XsiamClient(CFG, "key", transport=_transport(
        lambda r: httpx.Response(403, json={"err_msg": "license"})))
    with pytest.raises(XsiamApiError) as ei:
        await client.healthcheck()
    assert ei.value.upstream_status == 403


@pytest.mark.asyncio
async def test_advanced_auth_rejected_in_slice1():
    from integrations.xsiam.client import XsiamClient
    from integrations.xsiam.exceptions import XsiamConfigError
    cfg = XsiamTenantConfig(base_url=CFG.base_url, region="us",
                            auth_mode="advanced", api_key_id="1")
    with pytest.raises(XsiamConfigError):
        XsiamClient(cfg, "key")


@pytest.mark.asyncio
async def test_ping_via_endpoints_returns_count():
    from integrations.xsiam.client import XsiamClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/public_api/v1/endpoints/get_endpoints"
        return httpx.Response(200, json={"reply": {"result_count": 1}})

    client = XsiamClient(CFG, "key", transport=_transport(handler))
    assert await client.ping_via_endpoints() == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/xsiam/test_client_healthcheck.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement `client.py` (healthcheck + `_unwrap` + endpoints probe)**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/integration/xsiam/test_client_healthcheck.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add core/integrations/xsiam/client.py tests/integration/xsiam/test_client_healthcheck.py
git commit -m "feat(xsiam): async client healthcheck + response unwrapping"
```

---

## Task 5: `XsiamClient` — XQL start / get / run_xql poll loop

**Files:**
- Modify: `core/integrations/xsiam/client.py` (add three methods)
- Test: `tests/integration/xsiam/test_client_xql.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/xsiam/test_client_xql.py
from __future__ import annotations

import httpx
import pytest

from integrations.xsiam.config import XsiamTenantConfig

CFG = XsiamTenantConfig(
    base_url="https://api-test.xdr.us.paloaltonetworks.com",
    region="us", auth_mode="standard", api_key_id="1",
)


@pytest.mark.asyncio
async def test_start_xql_returns_query_id():
    from integrations.xsiam.client import XsiamClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/public_api/v1/xql/start_xql_query"
        return httpx.Response(200, json={"reply": "query-abc-123"})

    client = XsiamClient(CFG, "key", transport=httpx.MockTransport(handler))
    qid = await client.start_xql_query("dataset = x", {"relativeTime": 1000})
    assert qid == "query-abc-123"


@pytest.mark.asyncio
async def test_run_xql_polls_until_success():
    from integrations.xsiam.client import XsiamClient
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("start_xql_query"):
            return httpx.Response(200, json={"reply": "q1"})
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(200, json={"reply": {"status": "PENDING"}})
        return httpx.Response(200, json={"reply": {
            "status": "SUCCESS",
            "remaining_quota": 0.97,
            "results": {"data": [{"source": "okta", "events": 12}]},
        }})

    client = XsiamClient(CFG, "key", transport=httpx.MockTransport(handler))
    reply = await client.run_xql("dataset=x", {"relativeTime": 1000},
                                 max_wait=5, interval=0)
    assert reply["status"] == "SUCCESS"
    assert reply["results"]["data"][0]["source"] == "okta"


@pytest.mark.asyncio
async def test_run_xql_times_out_while_pending():
    from integrations.xsiam.client import XsiamClient
    from integrations.xsiam.exceptions import XsiamQueryError

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("start_xql_query"):
            return httpx.Response(200, json={"reply": "q1"})
        return httpx.Response(200, json={"reply": {"status": "PENDING"}})

    client = XsiamClient(CFG, "key", transport=httpx.MockTransport(handler))
    with pytest.raises(XsiamQueryError):
        await client.run_xql("dataset=x", {"relativeTime": 1000},
                             max_wait=0, interval=0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/xsiam/test_client_xql.py -v`
Expected: FAIL — `AttributeError: 'XsiamClient' object has no attribute 'start_xql_query'`

- [ ] **Step 3: Add the XQL methods to `client.py`**

Add `import asyncio` at the top of `client.py`, add `XsiamQueryError` to the exceptions import, and append these methods to `XsiamClient`:

```python
    async def start_xql_query(self, query: str, timeframe: dict[str, Any]) -> str:
        body = {"request_data": {"query": query, "timeframe": timeframe}}
        async with self._client() as c:
            resp = await c.post("/public_api/v1/xql/start_xql_query", json=body)
        data = self._unwrap(resp)
        reply = data.get("reply", data) if isinstance(data, dict) else data
        if not reply:
            raise XsiamQueryError("XSIAM did not return a query id")
        return reply

    async def get_query_results(self, query_id: str, *, limit: int = 100) -> dict[str, Any]:
        body = {"request_data": {
            "query_id": query_id, "pending_flag": True,
            "limit": limit, "format": "json",
        }}
        async with self._client() as c:
            resp = await c.post("/public_api/v1/xql/get_query_results", json=body)
        data = self._unwrap(resp)
        return data.get("reply", data) if isinstance(data, dict) else {}

    async def run_xql(
        self, query: str, timeframe: dict[str, Any],
        *, max_wait: float = 30.0, interval: float = 1.5, limit: int = 100,
    ) -> dict[str, Any]:
        """Start a query and poll until SUCCESS or timeout.

        max_wait/interval are deliberately conservative defaults — health
        metrics queries are cheap. Tune if your tenant's XQL latency differs.
        """
        query_id = await self.start_xql_query(query, timeframe)
        waited = 0.0
        while True:
            reply = await self.get_query_results(query_id, limit=limit)
            status = (reply or {}).get("status")
            if status == "SUCCESS":
                return reply
            if status not in ("PENDING", None):
                raise XsiamQueryError(f"XQL query {query_id} returned status {status!r}")
            if waited >= max_wait:
                raise XsiamQueryError(
                    f"XQL query {query_id} still {status!r} after {max_wait}s"
                )
            await asyncio.sleep(interval)
            waited += interval
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/integration/xsiam/test_client_xql.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add core/integrations/xsiam/client.py tests/integration/xsiam/test_client_xql.py
git commit -m "feat(xsiam): XQL start/get + bounded poll lifecycle"
```

---

## Task 6: `queries.py` — ingestion-health query slot + result shaping  ⟶ **Henry contribution**

**Files:**
- Create: `core/integrations/xsiam/queries.py`
- Test: `tests/integration/xsiam/test_queries.py`

The **query body is Henry's** (exact `metrics_*` schema is tenant-version-specific and gets verified at smoke-test time). The **shaping function** is testable now against synthetic XQL output, so it gets full TDD.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/xsiam/test_queries.py
from __future__ import annotations


def test_shape_ingestion_results_maps_contract():
    from integrations.xsiam.queries import shape_ingestion_results
    reply = {"results": {"data": [
        {"source": "okta_audit", "vendor": "okta", "product": "idp",
         "events": 1234, "last_seen": 1717200000000},
        {"dataset": "panw_ngfw", "count": 9, "_last_seen": 1717200001000},
    ]}}
    rows = shape_ingestion_results(reply)
    assert rows[0] == {"source": "okta_audit", "vendor": "okta",
                       "product": "idp", "events": 1234, "last_seen": 1717200000000}
    # Tolerates alternate field names (dataset/count/_last_seen)
    assert rows[1]["source"] == "panw_ngfw"
    assert rows[1]["events"] == 9
    assert rows[1]["last_seen"] == 1717200001000


def test_shape_ingestion_results_handles_empty():
    from integrations.xsiam.queries import shape_ingestion_results
    assert shape_ingestion_results({}) == []
    assert shape_ingestion_results({"results": {}}) == []


def test_ingestion_query_constant_exists():
    from integrations.xsiam.queries import INGESTION_HEALTH_XQL
    assert isinstance(INGESTION_HEALTH_XQL, str) and INGESTION_HEALTH_XQL.strip()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/xsiam/test_queries.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement `queries.py`** (shaping complete; query body is the contribution)

```python
# core/integrations/xsiam/queries.py
"""Curated XQL for tenant health, plus result shaping.

The ingestion-health *query* is config-as-content: schema drift across tenant
versions is a content edit here, not a code change.
"""
from __future__ import annotations

from typing import Any

# ── CONTRIBUTION POINT (Henry) ──────────────────────────────────────────────
# Finalize against your tenant's metrics schema; verify with the smoke test
# (Task 10). CONTRACT: one row per data source over the trailing window, with
# fields the shaper reads -> source, vendor, product, events, last_seen.
# The placeholder below is intentionally non-functional XQL; the build does not
# depend on it until the smoke test runs it for real.
INGESTION_HEALTH_XQL = """
// TODO(Henry): finalize against the tenant metrics schema. Suggested skeleton:
// dataset = metrics_source
// | comp count() as events, max(_time) as last_seen by source, vendor, product
// | sort desc events
""".strip()


def shape_ingestion_results(reply: dict[str, Any]) -> list[dict[str, Any]]:
    """Map a raw get_query_results reply into the ingestion-health contract.

    Tolerant of envelope variation: results may be {"data": [...]} or a bare
    list, and rows may use alternate field names.
    """
    results = reply.get("results") if isinstance(reply, dict) else None
    if isinstance(results, dict):
        rows = results.get("data") or []
    elif isinstance(results, list):
        rows = results
    else:
        rows = []

    shaped: list[dict[str, Any]] = []
    for r in rows:
        shaped.append({
            "source": r.get("source") or r.get("dataset"),
            "vendor": r.get("vendor"),
            "product": r.get("product"),
            "events": r.get("events") or r.get("count") or 0,
            "last_seen": r.get("last_seen") or r.get("_last_seen"),
        })
    return shaped
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/integration/xsiam/test_queries.py -v`
Expected: PASS (3 tests)

> **Henry:** replace `INGESTION_HEALTH_XQL` with your real query during Task 10 (smoke test), where you can iterate against the live tenant and confirm the shaper's field mapping matches what comes back.

- [ ] **Step 5: Commit**

```bash
git add core/integrations/xsiam/queries.py tests/integration/xsiam/test_queries.py
git commit -m "feat(xsiam): ingestion-health query slot + result shaping"
```

---

## Task 7: `loader.py` — tenant name → live client via CredentialStore

**Files:**
- Create: `core/integrations/xsiam/loader.py`
- Test: `tests/integration/xsiam/test_loader.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/xsiam/test_loader.py
from __future__ import annotations

import pytest

from tests.integration.xsiam.conftest import seed_tenant  # helper (Task 8 conftest)


@pytest.mark.asyncio
async def test_loader_builds_client(db_session):
    from integrations.xsiam.loader import load_xsiam_client
    await seed_tenant(db_session, name="acme",
                      base_url="https://api-acme.xdr.us.paloaltonetworks.com",
                      api_key="k-very-long-secret-value-1234567890")
    client = await load_xsiam_client(db_session, "acme")
    assert client is not None


@pytest.mark.asyncio
async def test_loader_missing_tenant_raises_config_error(db_session):
    from integrations.xsiam.loader import load_xsiam_client
    from integrations.xsiam.exceptions import XsiamConfigError
    with pytest.raises(XsiamConfigError):
        await load_xsiam_client(db_session, "nope")


@pytest.mark.asyncio
async def test_loader_rejects_bad_config(db_session):
    from integrations.xsiam.loader import load_xsiam_client
    from integrations.xsiam.exceptions import XsiamConfigError
    await seed_tenant(db_session, name="bad",
                      base_url="http://evil.example.com",  # invalid -> rejected on load
                      api_key="k-very-long-secret-value-1234567890")
    with pytest.raises(XsiamConfigError):
        await load_xsiam_client(db_session, "bad")
```

> The `db_session` fixture and `seed_tenant` helper are defined in Task 8's `conftest.py`. If executing strictly in order, write `conftest.py` (Task 8 Step 0) before running this task's tests.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/xsiam/test_loader.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement `loader.py`**

```python
# core/integrations/xsiam/loader.py
"""Bridge: integration name -> decrypted XsiamClient.

The only place the generic CredentialStore meets the typed XSIAM client.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from security import CredentialStore

from .client import XsiamClient
from .config import XsiamTenantConfig
from .exceptions import XsiamConfigError

XSIAM_KIND = "xsiam_tenant"


async def load_xsiam_client(session: AsyncSession, name: str) -> XsiamClient:
    store = CredentialStore(session)
    row = await store.get_integration(name)
    if row is None or row.kind != XSIAM_KIND:
        raise XsiamConfigError(f"XSIAM tenant '{name}' not found")
    try:
        config = XsiamTenantConfig(**(row.config or {}))
    except Exception as exc:  # noqa: BLE001 — pydantic ValidationError et al.
        raise XsiamConfigError(f"tenant '{name}' has invalid config: {exc}") from exc
    api_key = await store.get_integration_secret(name)
    return XsiamClient(config, api_key)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/integration/xsiam/test_loader.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add core/integrations/xsiam/loader.py tests/integration/xsiam/test_loader.py
git commit -m "feat(xsiam): tenant loader bridging CredentialStore to client"
```

---

## Task 8: API router + main.py wiring + error handler

**Files:**
- Create: `tests/integration/xsiam/conftest.py` (Step 0 — fixtures + `seed_tenant`)
- Create: `core/api/xsiam.py`
- Modify: `core/main.py` (register router + `XsiamError` handler)
- Test: `tests/integration/xsiam/test_api_endpoints.py`

- [ ] **Step 0: Write the shared conftest** (used by Tasks 7 & 8)

```python
# tests/integration/xsiam/conftest.py
"""Fixtures for XSIAM integration tests: isolated SQLite + tenant seeding."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio

os.environ.setdefault("CORTEXSIM_SECRET", "test-master-key-please-ignore-32+chars-entropy")
os.environ.setdefault("CORTEXSIM_ENV", "development")


@pytest_asyncio.fixture
async def db_session(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "xsiam-test.db"
    monkeypatch.setenv("CORTEXSIM_BASE_DIR", str(tmp_path))
    for mod in ("database", "models", "config"):
        sys.modules.pop(mod, None)
    for mod in [m for m in sys.modules if m.startswith("security")]:
        sys.modules.pop(mod, None)

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    from database import Base  # noqa: PLC0415
    import models  # noqa: F401, PLC0415
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


async def seed_tenant(session, *, name, base_url, api_key,
                      region="us", auth_mode="standard", api_key_id="1"):
    """Insert an xsiam_tenant IntegrationCredential the way the generic CRUD would."""
    from security import CredentialStore
    from integrations.xsiam.loader import XSIAM_KIND
    store = CredentialStore(session)
    await store.put_integration(
        name=name, kind=XSIAM_KIND, plaintext_secret=api_key,
        config={"base_url": base_url, "region": region,
                "auth_mode": auth_mode, "api_key_id": api_key_id},
    )
    await session.commit()
```

- [ ] **Step 1: Write the failing API test**

```python
# tests/integration/xsiam/test_api_endpoints.py
from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _build_app(tmp_path, monkeypatch, stub_client):
    """FastAPI app with just the xsiam router + the XsiamError handler,
    backed by tmp SQLite, with load_xsiam_client patched to a stub."""
    import sys
    monkeypatch.setenv("CORTEXSIM_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("CORTEXSIM_SECRET", "test-master-key-please-ignore-32+chars")
    monkeypatch.setenv("CORTEXSIM_ENV", "development")
    for mod in ("database", "models", "config"):
        sys.modules.pop(mod, None)
    for mod in [m for m in sys.modules if m.startswith("security") or m.startswith("api.")]:
        sys.modules.pop(mod, None)

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path/'api.db'}",
        connect_args={"check_same_thread": False})
    from database import Base, get_db
    import models  # noqa: F401
    asyncio.get_event_loop().run_until_complete(_create_all(engine, Base))
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_db():
        async with Session() as s:
            yield s

    import api.xsiam as xsiam_api
    from integrations.xsiam.exceptions import XsiamError

    # Patch the loader the router calls so no real network is hit.
    monkeypatch.setattr(xsiam_api, "load_xsiam_client", lambda session, name: _ret(stub_client))

    app = FastAPI()
    app.include_router(xsiam_api.router, prefix="/api")
    app.dependency_overrides[get_db] = _override_db

    @app.exception_handler(XsiamError)
    async def _h(request: Request, exc: XsiamError):
        return JSONResponse(status_code=exc.http_status,
                            content={"error": "XSIAM integration error",
                                     "code": exc.code, "detail": exc.detail})
    return TestClient(app)


async def _create_all(engine, Base):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _ret(value):  # tiny awaitable wrapper for the patched loader
    return value


class _StubClient:
    def __init__(self, *, health=None, raise_exc=None, xql_reply=None, qid="q1"):
        self._health, self._raise = health, raise_exc
        self._xql_reply, self._qid = xql_reply, qid
    async def healthcheck(self):
        if self._raise:
            raise self._raise
        return self._health or {"status": "ok"}
    async def ping_via_endpoints(self):
        return 1
    async def run_xql(self, *a, **k):
        return self._xql_reply or {"status": "SUCCESS", "results": {"data": []}, "remaining_quota": 1.0}
    async def start_xql_query(self, *a, **k):
        return self._qid
    async def get_query_results(self, qid, **k):
        return {"status": "SUCCESS", "results": {"data": []}, "remaining_quota": 1.0}


def test_test_endpoint_ok(tmp_path, monkeypatch):
    client = _build_app(tmp_path, monkeypatch, _StubClient(health={"status": "healthy"}))
    r = client.post("/api/xsiam/tenants/acme/test")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_test_endpoint_auth_failure_maps_envelope(tmp_path, monkeypatch):
    from integrations.xsiam.exceptions import XsiamAuthError
    client = _build_app(tmp_path, monkeypatch, _StubClient(raise_exc=XsiamAuthError("401")))
    r = client.post("/api/xsiam/tenants/acme/test")
    assert r.status_code == 502
    body = r.json()
    assert body["code"] == "XSIAM_AUTH_ERROR"
    assert set(body) >= {"error", "code", "detail"}


def test_test_endpoint_403_falls_back_to_endpoints(tmp_path, monkeypatch):
    from integrations.xsiam.exceptions import XsiamApiError
    stub = _StubClient(raise_exc=XsiamApiError("license", upstream_status=403))
    client = _build_app(tmp_path, monkeypatch, stub)
    r = client.post("/api/xsiam/tenants/acme/test")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_metrics_endpoint(tmp_path, monkeypatch):
    stub = _StubClient(xql_reply={"status": "SUCCESS", "remaining_quota": 0.5,
                                  "results": {"data": [{"source": "okta", "events": 3}]}})
    client = _build_app(tmp_path, monkeypatch, stub)
    r = client.get("/api/xsiam/tenants/acme/metrics")
    assert r.status_code == 200, r.text
    assert r.json()["sources"][0]["source"] == "okta"


def test_xql_start_and_get(tmp_path, monkeypatch):
    client = _build_app(tmp_path, monkeypatch, _StubClient(qid="qX"))
    r = client.post("/api/xsiam/tenants/acme/xql", json={"query": "dataset=x"})
    assert r.status_code == 200, r.text
    assert r.json()["query_id"] == "qX"
    r2 = client.get("/api/xsiam/tenants/acme/xql/qX")
    assert r2.status_code == 200
    assert r2.json()["status"] == "SUCCESS"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/xsiam/test_api_endpoints.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'api.xsiam'`

- [ ] **Step 3: Implement `core/api/xsiam.py`**

```python
# core/api/xsiam.py
"""XSIAM live-tenant operations router (decision B).

Tenant CRUD lives in the generic /api/credentials/integrations endpoints
(kind="xsiam_tenant"). This router only does things that require talking to the
live tenant: liveness, health, ingestion metrics, and XQL.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from security import CredentialStore
from integrations.xsiam.loader import XSIAM_KIND, load_xsiam_client
from integrations.xsiam.queries import INGESTION_HEALTH_XQL, shape_ingestion_results
from integrations.xsiam.exceptions import XsiamApiError, XsiamError

router = APIRouter(prefix="/xsiam", tags=["xsiam"])

_DEFAULT_TIMEFRAME = {"relativeTime": 24 * 60 * 60 * 1000}  # last 24h


class XqlRequest(BaseModel):
    query: str = Field(..., min_length=1)
    timeframe: dict[str, Any] = Field(default_factory=lambda: dict(_DEFAULT_TIMEFRAME))


@router.post("/tenants/{name}/test")
async def test_tenant(name: str, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    store = CredentialStore(session)
    client = await load_xsiam_client(session, name)
    try:
        try:
            status = await client.healthcheck()
        except XsiamApiError as exc:
            if exc.upstream_status == 403:  # healthcheck is license-gated
                count = await client.ping_via_endpoints()
                status = {"status": "reachable (healthcheck license-gated)",
                          "endpoint_sample": count}
            else:
                raise
    except XsiamError as exc:
        await store.mark_integration_verified(name, ok=False, error=exc.detail)
        await session.commit()
        raise
    await store.mark_integration_verified(name, ok=True)
    await session.commit()
    return {"ok": True, "status": status}


@router.get("/tenants/{name}/health")
async def tenant_health(name: str, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    store = CredentialStore(session)
    row = await store.get_integration(name)
    if row is None or row.kind != XSIAM_KIND:
        raise HTTPException(status_code=404, detail=f"XSIAM tenant '{name}' not found")
    return {
        "name": name,
        "last_verified_ok": row.last_verified_ok,
        "last_verified_at": row.last_verified_at.isoformat() if row.last_verified_at else None,
        "last_verified_error": row.last_verified_error,
    }


@router.get("/tenants/{name}/metrics")
async def tenant_metrics(name: str, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    client = await load_xsiam_client(session, name)
    reply = await client.run_xql(INGESTION_HEALTH_XQL, dict(_DEFAULT_TIMEFRAME))
    return {"sources": shape_ingestion_results(reply),
            "remaining_quota": reply.get("remaining_quota")}


@router.post("/tenants/{name}/xql")
async def start_xql(name: str, body: XqlRequest,
                    session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    client = await load_xsiam_client(session, name)
    query_id = await client.start_xql_query(body.query, body.timeframe)
    return {"query_id": query_id}


@router.get("/tenants/{name}/xql/{query_id}")
async def get_xql(name: str, query_id: str,
                  session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    client = await load_xsiam_client(session, name)
    reply = await client.get_query_results(query_id)
    return {"status": reply.get("status"),
            "results": reply.get("results"),
            "remaining_quota": reply.get("remaining_quota")}
```

- [ ] **Step 4: Wire the router + error handler into `core/main.py`**

In the router-import block (after `from api.credentials import router as credentials_router`), add:

```python
from api.xsiam import router as xsiam_router  # noqa: E402
```

In the `include_router` block (after `app.include_router(credentials_router, prefix="/api")`), add:

```python
app.include_router(xsiam_router, prefix="/api")
```

After the existing `crypto_error_handler` definition, add the XSIAM handler (mirrors it):

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/integration/xsiam/test_api_endpoints.py tests/integration/xsiam/test_loader.py -v`
Expected: PASS (8 tests)

Then the full module + a quick app-import sanity check:

Run: `.venv/bin/pytest tests/integration/xsiam/ -v`
Expected: PASS (all tasks' tests)

Run: `.venv/bin/python -c "import sys; sys.path.insert(0,'core'); import main; print('app import OK')"`
Expected: prints `app import OK` (proves the new imports/handler don't break boot)

- [ ] **Step 6: Commit**

```bash
git add core/api/xsiam.py core/main.py \
        tests/integration/xsiam/conftest.py \
        tests/integration/xsiam/test_api_endpoints.py
git commit -m "feat(xsiam): tenant health/metrics/xql API router + error handler"
```

---

## Task 9: Doc reconciliation — relax the "No Cortex API connection" rule

**Files:**
- Modify: `CLAUDE.md` (the "No Cortex API connection" line, ~line 9)
- Modify: `CORTEXSIM_AGENT_CONTEXT.md` (§10.1, ~line 604)

- [ ] **Step 1: Edit `CLAUDE.md`** — append a caveat to the existing rule. Find:

```
**No Cortex API connection.** SimCore is standalone — it generates signals INTO the environment via agent-based execution; it does not read alerts OUT of Cortex.
```

Replace with:

```
**No Cortex API connection** *(Phase 1 rule; relaxed in Phase 9 Health & Config track)*. SimCore generates signals INTO the environment via agent-based execution. As of Phase 9 it MAY make **opt-in, read-only** calls to a registered XSIAM tenant for health/metrics (`/healthcheck`, XQL over `metrics_*`) — see `docs/superpowers/specs/2026-06-01-xsiam-tenant-health-config-integration-design.md`. It still does **not** write to Cortex and does **not** read alerts OUT for detection auto-validation (that track is parked).
```

- [ ] **Step 2: Edit `CORTEXSIM_AGENT_CONTEXT.md` §10.1** — find the constraint #1 line under "## 10. Technical Constraints" and append:

```
   *(Phase 9 update: a read-only Health & Config integration may call a
   registered XSIAM tenant for /healthcheck + XQL health metrics — opt-in,
   no writes, no detection-alert read-back. See the 2026-06-01 spec.)*
```

- [ ] **Step 3: Verify no tests assert the old wording**

Run: `rg -n "does not read alerts OUT" --glob '!sources/**'`
Expected: only doc hits (no test depends on the phrase). If a test asserts it, update that test.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md CORTEXSIM_AGENT_CONTEXT.md
git commit -m "docs(xsiam): caveat that Phase 9 relaxes the no-API-connection rule"
```

---

## Task 10: Live-tenant smoke test (env-gated)  ⟶ **Henry runs + finalizes the XQL**

**Files:**
- Create: `tests/integration/xsiam/test_smoke_live.py`

This test is skipped unless real tenant creds are present. It is where the under-documented response envelopes get pinned and where Henry finalizes `INGESTION_HEALTH_XQL`.

- [ ] **Step 1: Write the smoke test**

```python
# tests/integration/xsiam/test_smoke_live.py
"""Live-tenant smoke test — SKIPPED unless real creds are present.

Run against a real tenant:
    CORTEXSIM_XSIAM_TEST_TENANT=https://api-<sub>.xdr.<region>.paloaltonetworks.com \
    CORTEXSIM_XSIAM_TEST_KEY=<api-key> \
    CORTEXSIM_XSIAM_TEST_KEY_ID=<id> \
    .venv/bin/pytest tests/integration/xsiam/test_smoke_live.py -v -s
"""
from __future__ import annotations

import os

import pytest

_BASE = os.environ.get("CORTEXSIM_XSIAM_TEST_TENANT")
_KEY = os.environ.get("CORTEXSIM_XSIAM_TEST_KEY")
_KEY_ID = os.environ.get("CORTEXSIM_XSIAM_TEST_KEY_ID")

pytestmark = pytest.mark.skipif(
    not (_BASE and _KEY and _KEY_ID),
    reason="set CORTEXSIM_XSIAM_TEST_TENANT/KEY/KEY_ID to run the live smoke test",
)


def _client():
    from integrations.xsiam.client import XsiamClient
    from integrations.xsiam.config import XsiamTenantConfig
    cfg = XsiamTenantConfig(base_url=_BASE, region="smoke",
                            auth_mode="standard", api_key_id=_KEY_ID)
    return XsiamClient(cfg, _KEY)


@pytest.mark.asyncio
async def test_live_healthcheck_or_endpoints():
    from integrations.xsiam.exceptions import XsiamApiError
    client = _client()
    try:
        status = await client.healthcheck()
        print("HEALTHCHECK:", status)
        assert status is not None
    except XsiamApiError as exc:
        assert exc.upstream_status == 403  # license-gated on this tenant
        print("healthcheck 403; endpoints sample:", await client.ping_via_endpoints())


@pytest.mark.asyncio
async def test_live_trivial_xql():
    client = _client()
    reply = await client.run_xql("dataset = xdr_data | fields _time | limit 1",
                                 {"relativeTime": 60 * 60 * 1000}, max_wait=60, interval=2)
    print("XQL STATUS:", reply.get("status"), "QUOTA:", reply.get("remaining_quota"))
    assert reply.get("status") == "SUCCESS"
```

- [ ] **Step 2: Run it (skips cleanly with no creds)**

Run: `.venv/bin/pytest tests/integration/xsiam/test_smoke_live.py -v`
Expected: SKIPPED (2 skipped) — proves the gate works.

- [ ] **Step 3 (Henry, with real creds): pin shapes + finalize the query**

Run the smoke test with the env vars set. Confirm: (a) the `healthcheck`/`reply` envelope matches `_unwrap`; (b) XQL `run_xql` returns `SUCCESS`. Then replace `INGESTION_HEALTH_XQL` in `queries.py` with the real metrics query and add a third smoke assertion that `run_xql(INGESTION_HEALTH_XQL, ...)` returns shaped rows. If any envelope differs from what `client._unwrap` / `shape_ingestion_results` assume, fix those + their unit tests.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/xsiam/test_smoke_live.py core/integrations/xsiam/queries.py
git commit -m "test(xsiam): env-gated live-tenant smoke test + finalized ingestion XQL"
```

---

## Self-Review

**1. Spec coverage:**
- §3 reuse foundation → Tasks 7, 8 use `CredentialStore` / `IntegrationCredential`. ✓
- §4 endpoints (`/healthcheck`, `start_xql_query`, `get_query_results`) → Tasks 4, 5. ✓
- §4 fallback on 403 → Tasks 4 (`ping_via_endpoints`) + 8 (`/test` fallback branch). ✓
- §5 decisions: D1 storage (Task 7/8 `XSIAM_KIND`), D2=B (router has no CRUD; Task 8), async XQL (Task 5), base_url SSRF control (Task 2), MockTransport (Tasks 4/5/8). ✓
- §6 module layout → Tasks 1–8 create exactly those files. ✓
- §7 endpoints (`/test`, `/health`, `/metrics`, `/xql`, `/xql/{id}`) → Task 8. ✓
- §8 ingestion query + shaping → Task 6. ✓
- §10 testing (unit + env-gated smoke) → Tasks 1–8 unit, Task 10 smoke. ✓
- §11 doc reconciliation → Task 9. ✓
- §14 acceptance → Task 10 Step 3 (live) covers the real-tenant criteria.
- **Gap:** UI (§9) intentionally deferred to Slice 1b (stated in header). Not a gap — a scope decision.

**2. Placeholder scan:** The only `TODO` is `INGESTION_HEALTH_XQL` (Task 6) — a deliberate, contract-documented contribution point Henry approved, finalized in Task 10. No "add error handling"/"similar to"/"TBD" placeholders; every code step is complete.

**3. Type consistency:** `XSIAM_KIND="xsiam_tenant"` used identically in loader, router, conftest. `load_xsiam_client(session, name)` signature consistent across loader/router/tests. `XsiamApiError(..., upstream_status=)` defined in Task 1, used in Tasks 4 & 8. `run_xql`/`start_xql_query`/`get_query_results`/`healthcheck`/`ping_via_endpoints` names consistent between `client.py`, the stub, and the router. `shape_ingestion_results` contract identical in Task 6 and Task 8.

---

## Execution Handoff

Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. (REQUIRED SUB-SKILL: superpowers:subagent-driven-development)

**2. Inline Execution** — Execute tasks in this session with checkpoints. (REQUIRED SUB-SKILL: superpowers:executing-plans)

Note: Tasks 2 & 6 contain your contribution points, and Task 10 Step 3 needs your real tenant creds — so even under subagent execution, those three are natural stop-points for you.
