"""Drift tests: the MCP tool input schemas are copies of the REST request models.

The tool layer must never import ``api.*`` (D-1), so ``mcp_server.schemas``
copies the models. These tests (which may import ``api.*``) pin the copies to
the originals so a REST field added later fails here instead of diverging.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from api.pov import ScopeRequest
from api.runs import LaunchRequest
from api.xsiam import OperationExecuteRequest, XqlRequest
from mcp_server.schemas import (
    MCP_EXECUTE_DROPPED,
    MCP_LAUNCH_CONSENT_EXCLUDED,
    MCP_LAUNCH_EXCLUDED,
    MCP_LAUNCH_PROJECTED,
    McpLaunchRequest,
    OperationExecuteIn,
    ScopeRequestIn,
    XqlQueryIn,
)

REPO = Path(__file__).resolve().parents[2]
MCP_DIR = REPO / "core" / "mcp_server"


def test_scope_request_fields_match():
    assert set(ScopeRequestIn.model_fields) == set(ScopeRequest.model_fields)


def test_execute_request_fields_match_minus_dropped():
    ours = set(OperationExecuteIn.model_fields) - {"op_id", "tenant"}
    assert ours == set(OperationExecuteRequest.model_fields) - set(MCP_EXECUTE_DROPPED)
    assert "consent" not in OperationExecuteIn.model_fields


def test_xql_request_fields_are_a_superset_of_rest():
    assert set(XqlRequest.model_fields) <= set(XqlQueryIn.model_fields)


def test_launch_projection_is_total():
    rest = set(LaunchRequest.model_fields)
    assert rest == set(MCP_LAUNCH_PROJECTED) | set(MCP_LAUNCH_EXCLUDED)
    assert not set(MCP_LAUNCH_PROJECTED) & set(MCP_LAUNCH_EXCLUDED)


def test_launch_consent_keys_are_all_decided():
    keys = {"simulation_authorized"} | MCP_LAUNCH_CONSENT_EXCLUDED
    assert keys == {
        "simulation_authorized",
        "c2_authorized",
        "cluster_privilege_authorized",
        "node_access_authorized",
    }
    source = (REPO / "core" / "api" / "runs.py").read_text()
    for key in keys:
        assert key in source, f"{key} no longer appears in api/runs.py"


def test_mcp_server_never_imports_core_api():
    offenders = []
    for path in sorted(MCP_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(), str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for n in names:
                if n == "api" or n.startswith("api.") or n == "core.api" or n.startswith("core.api."):
                    offenders.append(f"{path.name}:{node.lineno} imports {n}")
    assert not offenders, offenders


def test_launch_body_omits_excluded_and_none():
    body = McpLaunchRequest(scenario_id="SIM-EDR-001").to_launch_body()
    assert set(body) <= set(MCP_LAUNCH_PROJECTED)
    assert body == {
        "scenario_id": "SIM-EDR-001",
        "mode": "pull",
        "consent": {"simulation_authorized": False},
    }
    full = McpLaunchRequest(
        scenario_id="s", target_agent_id="a", identity="i", paired_with_run_id="p",
        canary_run_id="c", simulation_authorized=True,
    ).to_launch_body()
    assert full["consent"] == {"simulation_authorized": True}
    assert set(full) <= set(MCP_LAUNCH_PROJECTED)
    with pytest.raises(Exception):
        McpLaunchRequest(scenario_id="s", c2_authorized=True)
