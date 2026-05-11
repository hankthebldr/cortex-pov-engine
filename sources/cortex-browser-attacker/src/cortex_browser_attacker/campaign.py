"""
Browser campaign Pydantic schema.

Independent from the EAL simulator's ``Campaign`` schema — browser
actions don't fit the plugin-and-params shape that an HTTP/DNS plugin
uses, so this tool has its own. The ``browser_attack_runner`` EAL
plugin (in core/eal_simulator/plugins/) is what bridges this CLI into
a CortexSim EAL campaign.

Example browser campaign YAML:

    campaign_id: BC-001
    name: "Credential paste into untrusted origin"
    authorized_by: "domain-consultant@paloaltonetworks.com"
    simulation_authorized: true
    target_allowlist:
      - login.cortexsim-test.invalid
      - mail.cortexsim-test.invalid
    actions:
      - action: navigate
        params:
          url: https://login.cortexsim-test.invalid/signin
      - action: paste
        params:
          selector: 'input[name="password"]'
          content: "MyCorpSSO!@#-CORTEXSIM-CANARY-2026"
          expected_detection: "PB DLP — credential-shape paste into non-sanctioned origin"
"""

from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


_CAMPAIGN_ID_RE = re.compile(r"^BC(?:-[A-Z0-9]+)+-\d{3,5}$")


class BrowserAction(BaseModel):
    """One entry in a campaign's ``actions:`` list."""

    action: str = Field(..., description="Action name from the action registry.")
    params: dict[str, Any] = Field(default_factory=dict)
    description: Optional[str] = None
    on_error: str = Field(
        default="continue",
        description="'continue' to keep running, 'abort' to stop the campaign.",
    )

    @field_validator("action")
    @classmethod
    def _action_format(cls, v: str) -> str:
        v = v.strip().lower()
        if not v or not re.match(r"^[a-z][a-z0-9_]+$", v):
            raise ValueError(
                "action must be lowercase letters/digits/underscores"
            )
        return v

    @field_validator("on_error")
    @classmethod
    def _on_error_value(cls, v: str) -> str:
        if v not in ("continue", "abort"):
            raise ValueError("on_error must be 'continue' or 'abort'")
        return v


class BrowserCampaign(BaseModel):
    campaign_id: str
    name: str
    description: Optional[str] = None

    authorized_by: Optional[str] = Field(
        default=None,
        description="Operator who approved this simulation.",
    )
    simulation_authorized: bool = Field(default=False)
    target_allowlist: list[str] = Field(default_factory=list)

    dry_run: bool = Field(
        default=True,
        description="If true, actions report what they *would* do without "
                    "driving a real browser.",
    )

    browser_channel: str = Field(
        default="chromium",
        description="'prisma' for managed Prisma Browser, 'chromium' for "
                    "vanilla; 'stub' is only used by tests.",
    )
    headless: bool = Field(default=True)

    tags: list[str] = Field(default_factory=list)

    actions: list[BrowserAction]

    @field_validator("campaign_id")
    @classmethod
    def _campaign_id_format(cls, v: str) -> str:
        if not _CAMPAIGN_ID_RE.match(v):
            raise ValueError(
                "campaign_id must match BC-{LABEL}-{NNN} (e.g. BC-BROWSER-001)"
            )
        return v

    @field_validator("browser_channel")
    @classmethod
    def _channel_known(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("prisma", "chromium", "stub"):
            raise ValueError("browser_channel must be prisma | chromium | stub")
        return v

    @field_validator("actions")
    @classmethod
    def _actions_non_empty(cls, v: list[BrowserAction]) -> list[BrowserAction]:
        if not v:
            raise ValueError("campaign must declare at least one action")
        return v

    @model_validator(mode="after")
    def _validate_auth_block(self) -> "BrowserCampaign":
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
