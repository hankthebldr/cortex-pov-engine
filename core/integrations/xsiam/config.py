# core/integrations/xsiam/config.py
"""Typed XSIAM tenant configuration.

Decision B stores tenants in the generic IntegrationCredential.config JSON blob,
which is schema-agnostic. This model is where XSIAM-specific validation actually
happens — applied when the client loads a tenant (loader.py), so a malformed
tenant fails cleanly at /test rather than silently.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, field_validator

from .tenant_url import TENANT_FQDN, normalize_tenant_base_url  # noqa: F401 — re-export


class AuthMode(str, Enum):
    standard = "standard"   # API key sent verbatim
    advanced = "advanced"   # per-request sha256(key + nonce + timestamp) signature


# The URL pattern moved to ``tenant_url.py``. It is imported above rather than
# redefined here because this model used to describe itself as "the only place a
# customer's tenant URL is validated before we send their API key to it" while
# ``connectors/xsiam.py`` sent the same key to the same tenant with no validation
# at all. One definition, two callers.
_TENANT_FQDN = TENANT_FQDN  # back-compat alias for anything importing the regex


class XsiamTenantConfig(BaseModel):
    base_url: str
    region: str
    auth_mode: AuthMode = AuthMode.standard
    api_key_id: str

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, v: str) -> str:
        # normalize_tenant_base_url raises XsiamConfigError; pydantic wants a
        # ValueError to render a field error, and XsiamConfigError is a
        # RuntimeError. Translate at the boundary and keep the message.
        from .exceptions import XsiamConfigError  # noqa: PLC0415

        try:
            return normalize_tenant_base_url(v or "")
        except XsiamConfigError as exc:
            raise ValueError(str(exc)) from exc
