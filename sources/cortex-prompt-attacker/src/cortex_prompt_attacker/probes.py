"""
Probe schema — promptmap-compatible Pydantic model.

We mirror promptmap's YAML schema so existing rule packs load unchanged.
We extend with three CortexSim-only fields, all default-empty so a vanilla
promptmap rule still validates:

  * ``schema_version`` — int, currently ``1``; rejected if unknown
  * ``owasp_id``       — ``LLM01..LLM10``; primary key for aggregation
  * ``mutators``       — ordered list of mutator names to apply
  * ``scorer``         — primary scorer name; ``extended_scorers`` is
                          optional list of additional scorers

We never import from promptmap. Schema field names are not copyrightable;
copying the YAML *structure* is fine. Any code import would contaminate
the rest of CortexSim with GPL-3.0. See THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


_PROBE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")


class ProbeSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ProbeType(str, Enum):
    """Categories that mirror promptmap's directory layout. Free-form for
    forward compatibility; validation only checks length / charset."""
    PROMPT_INJECTION = "prompt_injection"
    PROMPT_STEALING = "prompt_stealing"
    JAILBREAK = "jailbreak"
    HARMFUL = "harmful"
    HATE = "hate"
    SOCIAL_BIAS = "social_bias"
    INDIRECT_INJECTION = "indirect_injection"
    TOOL_ABUSE = "tool_abuse"
    DATA_POISONING = "data_poisoning"
    DOS = "dos"
    SUPPLY_CHAIN = "supply_chain"
    MISINFORMATION = "misinformation"


class Probe(BaseModel):
    # Schema versioning — extended field
    schema_version: int = Field(default=1)

    # promptmap-compatible required fields
    name: str
    type: str
    severity: ProbeSeverity = ProbeSeverity.MEDIUM
    prompt: str

    # promptmap-compatible optional fields
    pass_conditions: list[str] = Field(default_factory=list)
    fail_conditions: list[str] = Field(default_factory=list)
    description: Optional[str] = None
    goal: Optional[str] = None

    # CortexSim extensions
    owasp_id: Optional[str] = Field(default=None)
    mutators: list[str] = Field(default_factory=list)
    scorer: Optional[str] = Field(default=None)
    extended_scorers: list[str] = Field(default_factory=list)
    target_path: Optional[str] = Field(
        default=None,
        description="Override target URL path for this probe (relative).",
    )

    @field_validator("schema_version")
    @classmethod
    def _schema_supported(cls, v: int) -> int:
        if v != 1:
            raise ValueError(
                f"unsupported probe schema_version={v}; this build supports 1"
            )
        return v

    @field_validator("name")
    @classmethod
    def _name_format(cls, v: str) -> str:
        if not _PROBE_NAME_RE.match(v):
            raise ValueError(
                "probe name must match [a-z0-9][a-z0-9_-]{0,127} (lower-case, "
                "no spaces; underscores or hyphens allowed; 1-128 chars)"
            )
        return v

    @field_validator("owasp_id")
    @classmethod
    def _owasp_id_shape(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v_up = v.upper()
        if not re.match(r"^LLM(0[1-9]|10)$", v_up):
            raise ValueError(
                f"owasp_id must match LLM01..LLM10, got '{v}'"
            )
        return v_up

    @field_validator("type")
    @classmethod
    def _type_shape(cls, v: str) -> str:
        if not v or len(v) > 64 or not re.match(r"^[a-z0-9_]+$", v):
            raise ValueError(
                "type must be lowercase letters/digits/underscores, max 64 chars"
            )
        return v

    @field_validator("mutators", "extended_scorers")
    @classmethod
    def _list_unique_strings(cls, v: list[str]) -> list[str]:
        seen = set()
        out = []
        for entry in v:
            if not isinstance(entry, str):
                raise ValueError("mutators/extended_scorers must be strings")
            if entry not in seen:
                seen.add(entry)
                out.append(entry)
        return out
