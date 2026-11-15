"""Tenant provenance of a terminal verdict — the Python port of the console's rule.

``ui/src/components/console/readiness/connectorState.js::verdictProvenance`` is
the definition the console renders ``tenant-verified: N`` from. The MCP server
and ``GET /api/readiness`` must state the same number, so the rule is ported
line for line: same checks, same order, same tri-state strings. The shared
fixture ``tests/fixtures/provenance_parity.json`` is the oracle for both sides.

Authored is not proven: only a terminal (``pass`` / ``fail``) verdict can carry
tenant provenance. ``pending`` and ``not_applicable`` never count, whatever
their detail says.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_TERMINAL = ("pass", "fail")


def _truthy(v: Any) -> bool:
    """JS truthiness for the values these JSON columns can hold.

    ``{}`` and ``[]`` are truthy in JS and falsy in Python; ``tenant`` /
    ``integration`` fields are strings in practice, but the port must not
    diverge on the odd row.
    """
    if isinstance(v, (dict, list)):
        return True
    return bool(v)


def _is_count(v: Any) -> bool:
    # JS ``typeof v === 'number'``; a Python bool is not a number there.
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def verdict_provenance(row: Mapping[str, Any]) -> str:
    """``'none' | 'local' | 'tenant'`` — port of ``verdictProvenance``."""
    if not row or row.get("tc_verdict") not in _TERMINAL:
        return "none"
    d = row.get("tc_verdict_detail")
    if isinstance(d, Mapping):
        v = d.get("verification")
        if isinstance(v, Mapping):
            if _is_count(v.get("queries_issued")) and v["queries_issued"] > 0:
                return "tenant"
            if _truthy(v.get("tenant")) or _truthy(v.get("integration")):
                return "tenant"
        if _is_count(d.get("queries_issued")) and d["queries_issued"] > 0:
            return "tenant"
        if d.get("verified_against_tenant") is True:
            return "tenant"
    # AssertionRun carries the tenant it ran against on the row itself, and a
    # dry run can never reach a terminal verdict.
    if _truthy(row.get("tenant")) and row.get("dry_run") is not True:
        return "tenant"
    return "local"


def tenant_verified_names(rows: Iterable[Mapping[str, Any]]) -> list[str]:
    """Names of tenants a terminal, tenant-sourced verdict points at (sorted)."""
    out: set[str] = set()
    for r in rows:
        if verdict_provenance(r) != "tenant":
            continue
        d = r.get("tc_verdict_detail")
        d = d if isinstance(d, Mapping) else {}
        v = d.get("verification")
        v = v if isinstance(v, Mapping) else {}
        n = (r.get("tenant") or d.get("tenant") or v.get("tenant")
             or v.get("integration"))
        if n:
            out.add(str(n))
    return sorted(out)


@dataclass(frozen=True)
class TenantVerified:
    count: int
    names: list[str] = field(default_factory=list)


async def tenant_verified(session: AsyncSession) -> TenantVerified:
    """Count terminal, tenant-sourced verdicts over every Run + AssertionRun.

    Reads only the columns the rule consumes, restricted to terminal verdicts —
    the same rows ``to_dict()`` would give the rule, without loading the world.
    """
    from models import AssertionRun, Run  # local: keeps import graph light

    rows: list[dict[str, Any]] = []
    res = await session.execute(
        select(Run.tc_verdict, Run.tc_verdict_detail).where(Run.tc_verdict.in_(_TERMINAL)))
    for verdict, detail in res.all():
        rows.append({"tc_verdict": verdict, "tc_verdict_detail": detail})
    res = await session.execute(
        select(AssertionRun.tc_verdict, AssertionRun.tc_verdict_detail, AssertionRun.tenant)
        .where(AssertionRun.tc_verdict.in_(_TERMINAL)))
    for verdict, detail, tenant in res.all():
        # AssertionRun.to_dict() has no dry_run key; the column does not exist
        # either, so the rule's `dry_run !== true` is vacuously satisfied.
        rows.append({"tc_verdict": verdict, "tc_verdict_detail": detail, "tenant": tenant})
    count = sum(1 for r in rows if verdict_provenance(r) == "tenant")
    return TenantVerified(count=count, names=tenant_verified_names(rows))


async def count_tenant_verified(session: AsyncSession) -> int:
    return (await tenant_verified(session)).count
