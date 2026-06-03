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
