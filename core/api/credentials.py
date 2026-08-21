"""Credentials REST API.

The router exposes two resource families:

  * /api/credentials/secrets             — generic encrypted key/value vault
  * /api/credentials/integrations        — typed external-integration credentials

Plaintext values are accepted on PUT and never returned on GET. The only way
to retrieve plaintext is via internal Python code calling CredentialStore.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from security import CredentialStore
from security.crypto import CryptoError


router = APIRouter(prefix="/credentials", tags=["credentials"])


def _not_found(resource: str, name: str) -> dict[str, str]:
    """Structured 404 envelope (GAP-API-012) — matches every other router's
    ``{error, code, detail}`` shape instead of a bare string detail."""
    return {
        "error": f"{resource.capitalize()} not found",
        "code": f"{resource.upper()}_NOT_FOUND",
        "detail": f"{resource}='{name}'",
    }


# ── Pydantic request/response models ───────────────────────────────────────


class SecretPut(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    plaintext: str = Field(..., min_length=1)
    type_hint: str = Field(default="generic", max_length=80)
    description: Optional[str] = Field(default=None, max_length=2000)
    rotation_days: Optional[int] = Field(default=None, ge=1, le=3650)


class IntegrationPut(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    kind: str = Field(..., min_length=1, max_length=80)
    plaintext_secret: str = Field(..., min_length=1)
    config: dict[str, Any] = Field(default_factory=dict)
    secret_type_hint: Optional[str] = Field(default=None, max_length=80)
    description: Optional[str] = Field(default=None, max_length=2000)


class VerifyMark(BaseModel):
    ok: bool
    error: Optional[str] = Field(default=None, max_length=2000)


# ── Secrets endpoints ──────────────────────────────────────────────────────


#: ``put_integration`` stores an integration's API key as a Secret named
#: ``__integration__/{name}``, and its docstring claims that keeps it out of the
#: plain ``list()`` call. It does not — ``CredentialStore.list()`` is an
#: unfiltered SELECT — so this endpoint returned every tenant's backing row
#: including ``preview_tail``, the last four characters of the customer's API
#: key, from an endpoint that is unauthenticated in a default deploy.
_BACKING_SECRET_PREFIX = "__integration__/"


@router.get("/secrets")
async def list_secrets(session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Operator-managed secrets. Integration-backing rows are NOT listed here.

    An integration's key is managed through /credentials/integrations, which
    already names the tenant; surfacing the backing Secret row here buys the
    operator nothing and leaks a preview of the key.
    """
    store = CredentialStore(session)
    secrets = [s for s in await store.list()
               if not (s.name or "").startswith(_BACKING_SECRET_PREFIX)]
    return {"secrets": [_redact_backing(s.to_dict()) for s in secrets]}


def _redact_backing(row: dict[str, Any]) -> dict[str, Any]:
    """Belt and braces: strip ``preview_tail`` from any integration credential.

    The filter above is the primary fix, but a future export, POV report or MCP
    surface could reach these rows another way. Two layers, because the leak
    reappearing is a credential disclosure and the cost of the second layer is
    one dict comparison.
    """
    if str(row.get("type_hint") or "").endswith("_credential"):
        row = dict(row)
        row["preview_tail"] = None
    return row


@router.put("/secrets")
async def put_secret(
    body: SecretPut,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    store = CredentialStore(session)
    secret = await store.put(
        body.name,
        body.plaintext,
        type_hint=body.type_hint,
        description=body.description,
        rotation_days=body.rotation_days,
    )
    await session.commit()
    return secret.to_dict()


@router.get("/secrets/{name}")
async def get_secret_meta(
    name: str,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Metadata only — never returns the plaintext."""
    store = CredentialStore(session)
    row = await store._get_secret_row(name)  # noqa: SLF001 — controlled internal access
    if row is None:
        raise HTTPException(status_code=404, detail=_not_found("secret", name))
    if (row.name or "").startswith(_BACKING_SECRET_PREFIX):
        # Addressable by name is still addressable. 404 rather than 403 so this
        # endpoint does not confirm which integrations exist.
        raise HTTPException(status_code=404, detail=_not_found("secret", name))
    return _redact_backing(row.to_dict())


@router.delete("/secrets/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_secret(
    name: str,
    session: AsyncSession = Depends(get_db),
):
    store = CredentialStore(session)
    removed = await store.delete(name)
    if not removed:
        raise HTTPException(status_code=404, detail=_not_found("secret", name))
    await session.commit()


# ── Integration endpoints ──────────────────────────────────────────────────


@router.get("/integrations")
async def list_integrations(
    kind: Optional[str] = None,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    store = CredentialStore(session)
    rows = await store.list_integrations(kind=kind)
    return {"integrations": [r.to_dict() for r in rows]}


def _validate_integration_config(kind: str, config: dict[str, Any]) -> None:
    """Per-kind config validation at WRITE time. Raises HTTPException(400).

    A bad tenant URL used to be accepted here silently and only became a problem
    at the first ``/reconcile`` — at which point CortexSim had already sent the
    customer's API key to whatever host the config named. Rejecting at PUT means
    the key never leaves the process, and the operator learns about their typo
    while they are still looking at the form.
    """
    from integrations.xsiam.exceptions import XsiamConfigError  # noqa: PLC0415
    from integrations.xsiam.loader import XSIAM_KIND  # noqa: PLC0415
    from integrations.xsiam.tenant_url import normalize_tenant_base_url  # noqa: PLC0415

    if kind not in ("xsiam", XSIAM_KIND):
        return
    url = config.get("base_url") or config.get("fqdn") or config.get("tenant_url")
    if not url:
        raise HTTPException(status_code=400, detail={
            "error": "Tenant URL is required",
            "code": "INTEGRATION_CONFIG_INVALID",
            "detail": f"kind '{kind}' needs config.base_url (or config.fqdn)"})
    try:
        normalize_tenant_base_url(str(url))
    except XsiamConfigError as exc:
        raise HTTPException(status_code=400, detail={
            "error": "Tenant URL rejected", "code": "INTEGRATION_CONFIG_INVALID",
            "detail": str(exc)}) from exc

    caps = config.get("capabilities")
    if caps is not None and (not isinstance(caps, list)
                             or any(not isinstance(c, str) for c in caps)):
        raise HTTPException(status_code=400, detail={
            "error": "capabilities must be a list of strings",
            "code": "INTEGRATION_CONFIG_INVALID",
            "detail": "e.g. [\"read_alerts\"] or [\"read_alerts\", \"xql\"]"})


@router.put("/integrations")
async def put_integration(
    body: IntegrationPut,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    _validate_integration_config(body.kind, body.config or {})
    store = CredentialStore(session)
    row = await store.put_integration(
        name=body.name,
        kind=body.kind,
        plaintext_secret=body.plaintext_secret,
        config=body.config,
        secret_type_hint=body.secret_type_hint,
        description=body.description,
    )
    await session.commit()
    return row.to_dict()


@router.get("/integrations/{name}")
async def get_integration(
    name: str,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    store = CredentialStore(session)
    row = await store.get_integration(name)
    if row is None:
        raise HTTPException(status_code=404, detail=_not_found("integration", name))
    return row.to_dict()


@router.delete("/integrations/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_integration(
    name: str,
    session: AsyncSession = Depends(get_db),
):
    store = CredentialStore(session)
    removed = await store.delete_integration(name)
    if not removed:
        raise HTTPException(status_code=404, detail=_not_found("integration", name))
    await session.commit()


@router.post("/integrations/{name}/verify")
async def mark_integration_verified(
    name: str,
    body: VerifyMark,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Mark a successful or failed liveness probe.

    This endpoint records the outcome — the actual probe runs in the consuming
    integration's module (e.g. xsiam.client). Decoupled so this layer stays
    integration-agnostic.
    """
    store = CredentialStore(session)
    row = await store.mark_integration_verified(name, ok=body.ok, error=body.error)
    if row is None:
        raise HTTPException(status_code=404, detail=_not_found("integration", name))
    await session.commit()
    return row.to_dict()


# ── Error mapping ──────────────────────────────────────────────────────────


@router.api_route(
    "/_internal/probe-crypto-error",
    methods=["GET"],
    include_in_schema=False,
)
async def _probe_crypto_error() -> None:
    """Test hook — never used in production. Lets the test suite assert
    that CryptoError maps to 500 rather than leaking a stack trace."""
    raise CryptoError("synthetic crypto failure")
