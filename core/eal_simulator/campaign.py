"""
Declarative campaign schema for the EAL Traffic Simulator.

Operators describe an attack narrative as an ordered list of plugin
invocations bound to a single ``Campaign``. The schema is Pydantic-native so
it round-trips cleanly through JSON, YAML, and the FastAPI request body.

Example campaign (YAML):

    campaign_id: CMP-NDR-001
    name: NDR validation — C2 beacon + DNS exfil
    authorized_by: hank@paloaltonetworks.com
    simulation_authorized: true
    target_allowlist:
      - testmynids.org
      - 10.0.0.0/24
    steps:
      - step_id: step-01
        plugin: c2_http_beacon
        params:
          target_url: http://testmynids.org/uid/index.html
          iterations: 10
          sleep_seconds: 30
          jitter_pct: 25
      - step_id: step-02
        plugin: dns_tunnel_exfil
        params:
          base_domain: testmynids.org
          chunks: 20
          query_type: TXT
"""

from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


_STEP_ID_RE = re.compile(r"^step-\d{2,3}$")
_CAMPAIGN_ID_RE = re.compile(r"^CMP(?:-[A-Z0-9]+)+-\d{3,5}$")


class PluginInvocation(BaseModel):
    """A single plugin call (used for ad-hoc one-shot runs via the API)."""

    plugin: str = Field(..., description="Plugin Meta.name")
    params: dict[str, Any] = Field(default_factory=dict)


class CampaignStep(BaseModel):
    step_id: str = Field(..., description="Stable step identifier (step-NN).")
    plugin: str = Field(..., description="Plugin Meta.name to invoke.")
    description: Optional[str] = None
    params: dict[str, Any] = Field(default_factory=dict)
    on_error: str = Field(
        default="continue",
        description="'continue' to keep running, 'abort' to stop the campaign.",
    )

    @field_validator("step_id")
    @classmethod
    def _step_id_format(cls, v: str) -> str:
        if not _STEP_ID_RE.match(v):
            raise ValueError("step_id must match step-NN (e.g. step-01)")
        return v

    @field_validator("on_error")
    @classmethod
    def _on_error_value(cls, v: str) -> str:
        if v not in ("continue", "abort"):
            raise ValueError("on_error must be 'continue' or 'abort'")
        return v


class Campaign(BaseModel):
    campaign_id: str
    name: str
    description: Optional[str] = None

    authorized_by: Optional[str] = Field(
        default=None,
        description="Operator who approved this simulation.",
    )
    simulation_authorized: bool = Field(
        default=False,
        description="Must be true for live (non-dry-run) execution.",
    )
    target_allowlist: list[str] = Field(default_factory=list)

    dry_run: bool = Field(
        default=True,
        description="If true, plugins compute and log their planned actions "
                    "without emitting real packets.",
    )

    tags: list[str] = Field(default_factory=list)
    mitre_techniques: list[str] = Field(default_factory=list)

    steps: list[CampaignStep]

    @field_validator("campaign_id")
    @classmethod
    def _campaign_id_format(cls, v: str) -> str:
        if not _CAMPAIGN_ID_RE.match(v):
            raise ValueError(
                "campaign_id must match CMP-{LABEL}-{NNN} (e.g. CMP-NDR-001)"
            )
        return v

    @field_validator("steps")
    @classmethod
    def _steps_non_empty(cls, v: list[CampaignStep]) -> list[CampaignStep]:
        if not v:
            raise ValueError("Campaign must declare at least one step.")
        ids = [s.step_id for s in v]
        if len(ids) != len(set(ids)):
            raise ValueError("Step IDs must be unique within a campaign.")
        return v

    @model_validator(mode="after")
    def _validate_authorisation_block(self) -> "Campaign":
        # If the operator wants live execution, ensure the safety block is sane.
        if not self.dry_run:
            if not self.simulation_authorized:
                raise ValueError(
                    "dry_run=false requires simulation_authorized=true"
                )
            if not self.authorized_by or not self.authorized_by.strip():
                raise ValueError(
                    "dry_run=false requires authorized_by to name the operator"
                )
            if not self.target_allowlist:
                raise ValueError(
                    "dry_run=false requires a non-empty target_allowlist"
                )
        return self

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
