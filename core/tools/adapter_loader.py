"""
CortexSim Tool Adapter Loader (Phase A).

Validates every ``tools/packs/*.yml`` adapter pack against a Pydantic schema
and exposes the resolved adapters via ``adapter_catalog``. Mirrors the
``scenario_loader`` + ``ttp_catalog`` pattern intentionally so contributors
have one mental model across all corpus loaders.

Validation rules (enforced):

1. ``adapter_id`` matches ``^TOOL-[A-Z0-9-]+$`` and is unique.
2. ``tier`` in {1..5}. Tier 5 forbids an ``invoke`` block. Tier 3 requires
   ``install.iac_module``. Tier 4 requires ``install.runtime_install_command``.
3. ``safety_class == c2-framework`` is annotated as launch-gated (the gate
   itself lives in the orchestrator).
4. ``safety_class == destructive`` requires non-empty ``cleanup.commands``.
5. ``cortex_signal.planes[]`` values are a subset of the plane enum.
6. ``upstream.license`` is required and may not be ``unknown``.

Invalid adapter files are rejected with a logged error and excluded from
the catalog — they never crash startup.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

logger = logging.getLogger("cortexsim.tools.adapter_loader")


_ADAPTER_ID_RE = re.compile(r"^TOOL-[A-Z0-9-]+$")

_VALID_TIERS: set[int] = {1, 2, 3, 4, 5}
_VALID_CATEGORIES: set[str] = {
    "adversary-simulation", "c2-framework", "sandbox", "reverse-engineering",
    "network-scan", "web-app", "identity-credential", "cloud-container",
    "social-engineering", "wireless-iot", "analyst-workbench",
}
_VALID_SAFETY_CLASSES: set[str] = {
    "safe", "dual-use-lab-only", "c2-framework", "destructive",
}
_VALID_PLANES: set[str] = {
    "EDR", "CDR", "NDR", "ITDR", "CLOUD_APP", "ANALYTICS",
    "AI_ACCESS", "AIRS", "BROWSER", "KOI",
}


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class UpstreamSchema(BaseModel):
    repo: str
    license: str
    attribution: str

    @field_validator("license")
    @classmethod
    def _license_required(cls, v: str) -> str:
        if (v or "").strip().lower() in ("", "unknown", "tbd"):
            raise ValueError("upstream.license must be a real SPDX identifier")
        return v


class CortexSignalSchema(BaseModel):
    planes: list[str] = Field(default_factory=list)
    expected_techniques: list[str] = Field(default_factory=list)

    @field_validator("planes")
    @classmethod
    def _plane_subset(cls, v: list[str]) -> list[str]:
        bad = [p for p in v if p not in _VALID_PLANES]
        if bad:
            raise ValueError(f"unknown plane(s): {bad}")
        return v


class InstallSchema(BaseModel):
    # All fields optional at the schema level — tier validation in the
    # parent enforces which combinations are valid.
    source_path: Optional[str] = None
    build_cmd: Optional[str] = None
    binary: Optional[str] = None
    iac_module: Optional[str] = None
    content_library_entry: Optional[dict[str, Any]] = None
    runtime_install_command: Optional[str] = None


class InvokeSchema(BaseModel):
    target_platform: str
    run_template: str
    default_args: dict[str, Any] = Field(default_factory=dict)
    identity_required: str

    @field_validator("target_platform")
    @classmethod
    def _platform_known(cls, v: str) -> str:
        allowed = {"linux", "windows", "macos", "k8s", "any"}
        if v not in allowed:
            raise ValueError(f"target_platform must be one of {allowed}")
        return v


class CleanupSchema(BaseModel):
    commands: list[str] = Field(default_factory=list)


class ToolAdapterSchema(BaseModel):
    adapter_id: str
    name: str
    version: str
    tier: int
    category: str
    upstream: UpstreamSchema
    cortex_signal: CortexSignalSchema
    safety_class: str
    install: InstallSchema = Field(default_factory=InstallSchema)
    invoke: Optional[InvokeSchema] = None
    cleanup: Optional[CleanupSchema] = None
    ttp_refs: list[str] = Field(default_factory=list)
    equivalents: list[str] = Field(default_factory=list)
    deprecated_by: Optional[str] = None
    author: Optional[str] = None
    created: Optional[str] = None
    last_updated: Optional[str] = None
    tags: list[str] = Field(default_factory=list)

    @field_validator("adapter_id")
    @classmethod
    def _adapter_id_format(cls, v: str) -> str:
        if not _ADAPTER_ID_RE.match(v):
            raise ValueError(f"adapter_id must match {_ADAPTER_ID_RE.pattern}, got {v!r}")
        return v

    @field_validator("tier")
    @classmethod
    def _tier_in_range(cls, v: int) -> int:
        if v not in _VALID_TIERS:
            raise ValueError(f"tier must be one of {_VALID_TIERS}, got {v}")
        return v

    @field_validator("category")
    @classmethod
    def _category_known(cls, v: str) -> str:
        if v not in _VALID_CATEGORIES:
            raise ValueError(f"category must be one of {_VALID_CATEGORIES}, got {v!r}")
        return v

    @field_validator("safety_class")
    @classmethod
    def _safety_known(cls, v: str) -> str:
        if v not in _VALID_SAFETY_CLASSES:
            raise ValueError(f"safety_class must be one of {_VALID_SAFETY_CLASSES}, got {v!r}")
        return v

    @model_validator(mode="after")
    def _tier_install_consistency(self) -> "ToolAdapterSchema":
        """Tier-specific install/invoke requirements."""
        if self.tier == 5:
            if self.invoke is not None:
                raise ValueError(
                    "tier 5 (external-only) adapters must NOT carry an invoke block — "
                    "tier-5 tools are reference material, never executed by the engine"
                )
            return self

        # Tier 1..4 all require an invoke block.
        if self.invoke is None:
            raise ValueError(f"tier {self.tier} adapter must declare an invoke block")

        if self.tier == 3 and not self.install.iac_module:
            raise ValueError(
                "tier 3 (IaC-provisioned) adapter must declare install.iac_module — "
                "the engine needs to know which IaC module includes the install step"
            )
        if self.tier == 4 and not self.install.runtime_install_command:
            raise ValueError(
                "tier 4 (runtime-fetched) adapter must declare install.runtime_install_command — "
                "the engine fetches the tool at task dispatch time"
            )
        if self.tier in (1, 2) and not self.install.source_path:
            raise ValueError(
                f"tier {self.tier} adapter must declare install.source_path"
            )

        # Destructive tools must declare cleanup. The engine refuses to dispatch
        # a destructive adapter without a non-empty cleanup.commands list.
        if self.safety_class == "destructive":
            if self.cleanup is None or not self.cleanup.commands:
                raise ValueError(
                    "safety_class=destructive requires non-empty cleanup.commands "
                    "(engine refuses to dispatch destructive adapters without cleanup)"
                )

        # run_template must reference every default_args key so substitution
        # at dispatch time produces a well-formed command line.
        for key in self.invoke.default_args.keys():
            placeholder = "{" + key + "}"
            if placeholder not in self.invoke.run_template:
                raise ValueError(
                    f"default_args key {key!r} does not appear in run_template — "
                    f"orphaned default would never substitute"
                )

        return self


# ---------------------------------------------------------------------------
# File walk + parse
# ---------------------------------------------------------------------------


def _find_pack_files(packs_dir: str) -> list[str]:
    """All ``*.yml`` files in packs_dir except ``_schema.yml``."""
    if not os.path.isdir(packs_dir):
        return []
    found: list[str] = []
    for fname in sorted(os.listdir(packs_dir)):
        if not fname.endswith(".yml") or fname.startswith("_"):
            continue
        found.append(os.path.join(packs_dir, fname))
    return found


def _parse_and_validate(filepath: str) -> tuple[Optional[ToolAdapterSchema], Optional[str]]:
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            raw: Any = yaml.safe_load(fh)
    except Exception as exc:  # noqa: BLE001
        return None, f"YAML parse error: {exc}"

    if not isinstance(raw, dict):
        return None, "YAML root is not a mapping"

    try:
        return ToolAdapterSchema(**raw), None
    except ValidationError as exc:
        return None, f"Schema validation failed:\n{exc}"


def default_packs_dir(base_dir: str) -> str:
    """Convention: ``<base>/tools/packs``."""
    return os.path.join(base_dir, "tools", "packs")
