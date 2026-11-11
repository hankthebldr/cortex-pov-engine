"""Python port of ``connectorState.js::verdictProvenance`` — parity with the console.

The shared JSON fixture is the oracle: the vitest file
``connectorState.provenanceParity.test.js`` asserts the same expectations
against the JS. If the two sides disagree, the fixture or this port is wrong,
never the JS.
"""
from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

import pytest

from connectors.provenance import (
    count_tenant_verified,
    tenant_verified,
    tenant_verified_names,
    verdict_provenance,
)

REPO = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((REPO / "tests" / "fixtures" / "provenance_parity.json").read_text())


def _rows():
    return [*FIXTURE["runs"], *FIXTURE["assertion_runs"]]


def test_parity_with_console_fixture():
    assert tenant_verified_names(_rows()) == FIXTURE["expected_names"]
    n = sum(1 for r in _rows() if verdict_provenance(r) == "tenant")
    assert n == FIXTURE["expected_tenant_count"]


def test_pending_and_not_applicable_never_count():
    for verdict in ("pending", "not_applicable", None, "", "PASS"):
        row = {
            "tc_verdict": verdict,
            "tc_verdict_detail": {"verification": {"queries_issued": 5, "tenant": "x"},
                                  "queries_issued": 5, "verified_against_tenant": True},
            "tenant": "x", "dry_run": False,
        }
        assert verdict_provenance(row) == "none"
    assert verdict_provenance({}) == "none"
    assert verdict_provenance(None) == "none"  # type: ignore[arg-type]
    assert tenant_verified_names([{"tc_verdict": "pending", "tenant": "x"}]) == []


def test_each_branch_of_the_port():
    t = "pass"
    assert verdict_provenance({"tc_verdict": t}) == "local"
    assert verdict_provenance({"tc_verdict": t, "tc_verdict_detail": "oops"}) == "local"
    assert verdict_provenance({"tc_verdict": t, "tc_verdict_detail": {"verification": {"queries_issued": 1}}}) == "tenant"
    assert verdict_provenance({"tc_verdict": t, "tc_verdict_detail": {"verification": {"queries_issued": "3"}}}) == "local"
    assert verdict_provenance({"tc_verdict": t, "tc_verdict_detail": {"verification": {"integration": "i"}}}) == "tenant"
    assert verdict_provenance({"tc_verdict": t, "tc_verdict_detail": {"queries_issued": 1}}) == "tenant"
    assert verdict_provenance({"tc_verdict": t, "tc_verdict_detail": {"verified_against_tenant": 1}}) == "local"
    assert verdict_provenance({"tc_verdict": t, "tc_verdict_detail": {"verified_against_tenant": True}}) == "tenant"
    assert verdict_provenance({"tc_verdict": t, "tenant": "lab"}) == "tenant"
    assert verdict_provenance({"tc_verdict": t, "tenant": "lab", "dry_run": True}) == "local"
    # bool is not a count: True must not read as one query.
    assert verdict_provenance({"tc_verdict": t, "tc_verdict_detail": {"queries_issued": True}}) == "local"


def test_empty_db_counts_zero(session_factory):
    async def _go():
        async with session_factory() as db:
            tv = await tenant_verified(db)
            return tv.count, tv.names, await count_tenant_verified(db)

    assert asyncio.run(_go()) == (0, [], 0)


def test_exactly_one_definition_of_each():
    core = REPO / "core"
    wanted = {"verdict_provenance": 0, "count_tenant_verified": 0}
    for path in core.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted:
                wanted[node.name] += 1
    assert wanted == {"verdict_provenance": 1, "count_tenant_verified": 1}
