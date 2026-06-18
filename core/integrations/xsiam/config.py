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
