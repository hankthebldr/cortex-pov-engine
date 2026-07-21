# core/integrations/xsiam/operations/schema.py
"""Pydantic schema for a single XSIAM Platform API operation.

The single source of truth for what a catalog operation looks like. Mirrors
``core/tools/adapter_loader.py`` conventions: controlled-vocab frozensets,
per-field validators, and one cross-field ``model_validator``.
"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

# ── controlled vocabularies ─────────────────────────────────────────────────
_VALID_METHODS = frozenset({"GET", "POST"})
# access_class drives the executor's safety gate:
#   read        → pull information; runs when a tenant is configured
#   write       → create/update/insert/action; global flag + per-request consent
#   destructive → delete/remove; global flag + per-request consent
_VALID_ACCESS = frozenset({"read", "write", "destructive"})
_VALID_CONSENT = frozenset({"none", "write", "destructive"})

_OP_ID_RE = re.compile(r"^OP-[A-Z0-9-]+$")
_PATH_PARAM_RE = re.compile(r"\{([a-zA-Z0-9_]+)\}")

# consent defaults derived from access_class when a pack omits `consent`
_DEFAULT_CONSENT = {"read": "none", "write": "write", "destructive": "destructive"}


class XsiamOperationSchema(BaseModel):
    op_id: str
    category: str
    name: str
    method: str
    path: str
    access_class: str
    consent: Optional[str] = None
    path_params: list[str] = Field(default_factory=list)
    doc_link: Optional[str] = None
    tags: list[str] = Field(default_factory=list)

    # ── field validators ────────────────────────────────────────────────
    @field_validator("op_id")
    @classmethod
    def _v_op_id(cls, v: str) -> str:
        if not _OP_ID_RE.match(v):
            raise ValueError(f"op_id {v!r} must match ^OP-[A-Z0-9-]+$")
        return v

    @field_validator("method")
    @classmethod
    def _v_method(cls, v: str) -> str:
        v = (v or "").upper()
        if v not in _VALID_METHODS:
            raise ValueError(f"method {v!r} not in {sorted(_VALID_METHODS)}")
        return v

    @field_validator("access_class")
    @classmethod
    def _v_access(cls, v: str) -> str:
        if v not in _VALID_ACCESS:
            raise ValueError(f"access_class {v!r} not in {sorted(_VALID_ACCESS)}")
        return v

    @field_validator("path")
    @classmethod
    def _v_path(cls, v: str) -> str:
        if not v.startswith("/public_api/"):
            raise ValueError(f"path {v!r} must start with /public_api/")
        return v

    # ── cross-field: derive path_params + default consent ────────────────
    @model_validator(mode="after")
    def _finalize(self) -> "XsiamOperationSchema":
        derived = _PATH_PARAM_RE.findall(self.path)
        if self.path_params and set(self.path_params) != set(derived):
            raise ValueError(
                f"{self.op_id}: path_params {self.path_params} do not match "
                f"the tokens in path ({derived})"
            )
        self.path_params = derived
        if self.consent is None:
            self.consent = _DEFAULT_CONSENT[self.access_class]
        elif self.consent not in _VALID_CONSENT:
            raise ValueError(f"consent {self.consent!r} not in {sorted(_VALID_CONSENT)}")
        return self

    # ── helpers ──────────────────────────────────────────────────────────
    def summary(self) -> dict:
        """Slim card for the list endpoint."""
        return {
            "op_id": self.op_id,
            "category": self.category,
            "name": self.name,
            "method": self.method,
            "path": self.path,
            "access_class": self.access_class,
            "consent": self.consent,
            "path_params": self.path_params,
            "tags": self.tags,
        }
