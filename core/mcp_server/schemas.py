"""MCP tool input schemas: drift-tested COPIES of the REST request models.

D-1: the stdio MCP server runs in a separate process against SimCore's REST
API, so this package must never import ``api.*`` (that would drag in the
database and the app). The models below therefore copy the REST shapes, and
``tests/mcp_server/test_schema_drift.py`` pins them field-for-field to the
originals (and asserts this package never imports ``api``).

Two deliberate projections: ``OperationExecuteIn`` drops ``consent`` (an agent
must never self-grant consent; writes through MCP always preview), and
``McpLaunchRequest`` exposes 7 of ``LaunchRequest``'s 12 fields.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_STRICT = ConfigDict(extra="forbid")


class _In(BaseModel):
    model_config = _STRICT


# --- shared -----------------------------------------------------------------
class EmptyInput(_In):
    pass


class TenantIn(_In):
    tenant: str | None = None


class IdIn(_In):
    id: str


class RunIdIn(_In):
    run_id: str


class ListIn(_In):
    limit: int = Field(50, ge=1, le=200)
    offset: int = Field(0, ge=0)


# --- list tools ---------------------------------------------------------------
class RunResultsIn(RunIdIn, ListIn):
    pass


class ScenarioListIn(ListIn):
    plane: str | None = None
    uc_ref: str | None = None
    ttp_ref: str | None = None
    entitlement: str | None = None
    status: str | None = None


class RunListIn(ListIn):
    status: str | None = None
    scenario_id: str | None = None


class AssertionListIn(ListIn):
    validation_class: str | None = None
    tc_ref: str | None = None


class OperationListIn(ListIn):
    category: str | None = None
    access_class: str | None = None


# --- POV scoping (mirror of api.pov.ScopeRequest) ---------------------------
class ScopeRequestIn(_In):
    profile: str | None = None
    base_platform: list[str] = Field(default_factory=list)
    addons: list[str] = Field(default_factory=list)
    planes: list[str] = Field(default_factory=list)


class PovScopeIn(ScopeRequestIn, ListIn):
    """``ScopeRequestIn`` plus the section 5.5 caps; ``limit``/``offset`` are never posted."""

    def to_scope_body(self) -> dict[str, Any]:
        return self.model_dump(include=set(ScopeRequestIn.model_fields))


# --- XSIAM operations / XQL (mirror of api.xsiam) -----------------------------
MCP_EXECUTE_DROPPED = {
    "consent": "agent-settable consent is cortex-bot's F2 (A2); writes always preview",
}

XQL_TIMEFRAME_MAX_MS = 7 * 86400000


class OperationExecuteIn(_In):
    op_id: str
    tenant: str | None = None
    path_params: dict[str, Any] = Field(default_factory=dict)
    query: dict[str, Any] | None = None
    body: Any = None
    dry_run: bool | None = None


class XqlQueryIn(_In):
    query: str = Field(..., min_length=1)
    timeframe: dict[str, Any] = Field(default_factory=lambda: {"relativeTime": 86400000})
    limit: int = Field(100, ge=1, le=1000)
    tenant: str | None = None
    max_wait_seconds: float = Field(30.0, ge=1, le=120)
    poll_interval_seconds: float = Field(1.5, ge=0.5, le=10)

    @field_validator("timeframe")
    @classmethod
    def _relative_time_capped(cls, v: dict[str, Any]) -> dict[str, Any]:
        if "relativeTime" in v:
            rt = v["relativeTime"]
            if isinstance(rt, bool) or not isinstance(rt, int):
                raise ValueError("timeframe.relativeTime must be an integer (ms)")
            if rt > XQL_TIMEFRAME_MAX_MS:
                raise ValueError(f"timeframe.relativeTime exceeds {XQL_TIMEFRAME_MAX_MS} ms (7 days)")
        return v


# --- run launch (projection of api.runs.LaunchRequest) ------------------------
MCP_LAUNCH_PROJECTED: dict[str, str] = {
    "scenario_id": "scenario_id",
    "mode": "mode",
    "target_agent_id": "target_agent_id",
    "identity": "identity",
    "paired_with_run_id": "paired_with_run_id",
    "canary_run_id": "canary_run_id",
    "consent": "simulation_authorized (consent.simulation_authorized only)",
}

MCP_LAUNCH_EXCLUDED: dict[str, str] = {
    "allow_runtime_install": "needs the deployment flag too; never granted from an agent",
    "agent_profile_mode": "operator attestation about the agent profile; a human states it, an agent does not",
    "bas_mode_attested": "operator attestation about the agent profile; a human states it, an agent does not",
    "soakless_attested": "operator attestation about the agent profile; a human states it, an agent does not",
    "attested_by": "operator attestation about the agent profile; a human states it, an agent does not",
    "module_modes": "operator attestation about the agent profile; a human states it, an agent does not",
    "allow_missing_tools": "accepts a run whose steps call tools the pod lacks (TOOL_ABSENT); an operator decides to run degraded, an agent does not",
}

MCP_LAUNCH_CONSENT_EXCLUDED = {
    "c2_authorized",
    "cluster_privilege_authorized",
    "node_access_authorized",
}


class McpLaunchRequest(_In):
    scenario_id: str
    mode: str = "pull"
    target_agent_id: str | None = None
    identity: str | None = None
    paired_with_run_id: str | None = None
    canary_run_id: str | None = None
    simulation_authorized: bool = False

    def to_launch_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            k: getattr(self, k)
            for k in ("scenario_id", "mode", "target_agent_id", "identity",
                      "paired_with_run_id", "canary_run_id")
            if getattr(self, k) is not None
        }
        body["consent"] = {"simulation_authorized": self.simulation_authorized}
        return body


# --- connectors / verification / assertions / reports -------------------------
class ReconcileIn(_In):
    run_id: str
    tenant: str | None = None
    window_seconds: int = Field(3600, ge=60, le=86400)


class VerifyIn(_In):
    run_id: str
    tenant: str | None = None
    force: bool = False
    timeframe_seconds: int | None = Field(None, ge=60, le=86400)


class PreflightIn(_In):
    kind: Literal["xsiam", "xsiam_tenant"]
    tenant: str | None = None
    datasets: str | None = None


class AssertionRunIn(_In):
    id: str
    dry_run: bool = True
    tenant: str | None = None
    context: dict[str, str] = Field(default_factory=dict)


class DatasetSchemaIn(_In):
    dataset: str = Field(pattern=r"^[A-Za-z0-9_]{1,128}$")
    tenant: str | None = None


class ReportIn(_In):
    run_id: str
    format: Literal["markdown"] = "markdown"
