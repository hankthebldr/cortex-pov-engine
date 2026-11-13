"""The error contract this layer adds (spec section 8).

SimCore's own codes pass through untouched. These are the ones the MCP layer
raises itself, each with a one-line remediation.
"""
from __future__ import annotations

from typing import Any, Optional

from .envelope import Evidence

MCP_WRITE_NOT_EXECUTABLE = "MCP_WRITE_NOT_EXECUTABLE"
TOOL_NOT_BACKED = "TOOL_NOT_BACKED"
NO_TENANT_CONFIGURED = "NO_TENANT_CONFIGURED"
TENANT_AMBIGUOUS = "TENANT_AMBIGUOUS"
MCP_RATE_LIMITED = "MCP_RATE_LIMITED"
SIMCORE_UNREACHABLE = "SIMCORE_UNREACHABLE"
SIMCORE_SHAPE_UNKNOWN = "SIMCORE_SHAPE_UNKNOWN"
SIMCORE_TRANSPORT_ERROR = "SIMCORE_TRANSPORT_ERROR"
MCP_INVALID_ARGUMENTS = "MCP_INVALID_ARGUMENTS"
MCP_HOST_ACTION_DISABLED = "MCP_HOST_ACTION_DISABLED"

REMEDIATION: dict[str, str] = {
    MCP_WRITE_NOT_EXECUTABLE: "a human runs the previewed request in the console; MCP only previews writes",
    TOOL_NOT_BACKED: "author a pack under core/integrations/xsiam/operations/packs/ if an endpoint exists",
    NO_TENANT_CONFIGURED: "store an xsiam_tenant credential in the vault (/api/credentials/integrations), then retry",
    TENANT_AMBIGUOUS: "more than one tenant is configured; pass the tenant name explicitly",
    MCP_RATE_LIMITED: "wait retry_after_seconds, or raise CORTEXSIM_MCP_MAX_TENANT_CALLS_PER_MINUTE",
    SIMCORE_UNREACHABLE: "start SimCore (./scripts/dev-up.sh) or set CORTEXSIM_URL; this is not a tenant error",
    SIMCORE_SHAPE_UNKNOWN: "the SimCore response did not match what this tool reads; check the SimCore version",
    SIMCORE_TRANSPORT_ERROR: "the request reached SimCore but no complete answer came back; check SimCore's log before retrying a tenant-class call",
    MCP_INVALID_ARGUMENTS: "fix the arguments; the tool's inputSchema lists the accepted fields",
    MCP_HOST_ACTION_DISABLED: "a human runs the previewed request from the console, or the operator sets CORTEXSIM_MCP_ALLOW_HOST_ACTIONS=1 and restarts SimCore",
}


class McpToolError(Exception):
    """A tool failure that keeps its code. ``data`` carries a preview, if any."""

    def __init__(
        self,
        code: str,
        error: str,
        detail: str,
        remediation: Optional[str] = None,
        basis: str = "authored",
        tenant: Optional[str] = None,
        data: Any = None,
    ) -> None:
        super().__init__(f"{code}: {error} - {detail}")
        self.code = code
        self.error = error
        self.detail = detail
        self.remediation = remediation
        self.basis = basis
        self.tenant = tenant
        self.data = data

    def as_structured(self, evidence: Evidence) -> dict:
        out: dict[str, Any] = {
            "error": self.error,
            "code": self.code,
            "detail": self.detail,
            "remediation": self.remediation,
            "evidence": evidence.model_dump(),
        }
        if self.data is not None:
            out["data"] = self.data
        return out
