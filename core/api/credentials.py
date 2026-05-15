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


@router.get("/secrets")
async def list_secrets(session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    store = CredentialStore(session)
    secrets = await store.list()
    return {"secrets": [s.to_dict() for s in secrets]}


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
        raise HTTPException(status_code=404, detail=f"secret '{name}' not found")
    return row.to_dict()


@router.delete("/secrets/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_secret(
    name: str,
    session: AsyncSession = Depends(get_db),
):
    store = CredentialStore(session)
    removed = await store.delete(name)
    if not removed:
        raise HTTPException(status_code=404, detail=f"secret '{name}' not found")
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


@router.put("/integrations")
async def put_integration(
    body: IntegrationPut,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
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
        raise HTTPException(status_code=404, detail=f"integration '{name}' not found")
    return row.to_dict()


@router.delete("/integrations/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_integration(
    name: str,
    session: AsyncSession = Depends(get_db),
):
    store = CredentialStore(session)
    removed = await store.delete_integration(name)
    if not removed:
        raise HTTPException(status_code=404, detail=f"integration '{name}' not found")
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
        raise HTTPException(status_code=404, detail=f"integration '{name}' not found")
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
