"""
Pydantic models for the IaC topology generator.

Request shape:
    InfraGenerateRequest -> InfraGenerateResponse

Public catalog shape:
    InfraModuleMetadata (returned from GET /api/infra/modules)
    InfraBundleSummary  (returned from GET /api/infra/bundles)
"""
from __future__ import annotations

import ipaddress
from typing import Literal

from pydantic import BaseModel, Field, field_validator


ALLOWED_PROVIDERS = ("aws", "gcp", "azure")
ALLOWED_MODULES = (
    "base",
    "edr",
    "cdr",
    "ndr",
    "itdr",
    "tim",
    "asm",
    "cspm",
    "content-library",
    "telemetry-replay",
)


class InfraGenerateParams(BaseModel):
    """Per-request parameters applied to the generated Terraform."""

    project_name: str = Field(
        ...,
        min_length=3,
        max_length=48,
        pattern=r"^[a-z0-9][a-z0-9-]*$",
        description="Lowercase-hyphen project name used as resource prefix",
    )
    dc_ssh_cidr: str = Field(..., description="CIDR allowed SSH access, e.g. 203.0.113.0/32")
    jumpbox_size: str = Field(default="t3.medium", description="Provider-specific instance type")
    k8s_node_count: int = Field(
        default=2, ge=1, le=10, description="Worker nodes for CDR module"
    )
    edr_target_count: int = Field(
        default=2, ge=1, le=10, description="Target VMs for EDR module"
    )
    ttl_hours: int = Field(
        default=72, ge=1, le=720, description="Hint for Torque environment TTL"
    )
    tags: dict[str, str] = Field(default_factory=dict)

    @field_validator("dc_ssh_cidr")
    @classmethod
    def _validate_cidr(cls, v: str) -> str:
        try:
            ipaddress.ip_network(v, strict=False)
        except ValueError as e:
            raise ValueError(f"invalid CIDR: {v}") from e
        return v


class InfraGenerateRequest(BaseModel):
    provider: Literal["aws", "gcp", "azure"]
    region: str = Field(..., min_length=3, max_length=32)
    modules: list[str] = Field(..., min_length=1)
    params: InfraGenerateParams

    @field_validator("modules")
    @classmethod
    def _validate_modules(cls, v: list[str]) -> list[str]:
        for m in v:
            if m not in ALLOWED_MODULES:
                raise ValueError(f"unknown module: {m}")
        seen: set[str] = set()
        out: list[str] = []
        for m in v:
            if m not in seen:
                out.append(m)
                seen.add(m)
        return out


class InfraGenerateResponse(BaseModel):
    bundle_id: str
    provider: str
    modules: list[str]
    download_url: str
    files: list[str]


class InfraModuleMetadata(BaseModel):
    name: str
    description: str
    providers: list[str]
    required_params: list[str] = Field(default_factory=list)
    optional_params: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    content_tools: list[str] = Field(
        default_factory=list,
        description="Flattened list of tool names from content.yml",
    )


class InfraBundleSummary(BaseModel):
    bundle_id: str
    provider: str
    modules: list[str]
    created_at: str
    size_bytes: int
