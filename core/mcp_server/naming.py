"""Canonical tool ids and the one derived wire-name mapping (spec section 5.1).

The canonical id is dotted (``xsiam.dataset.list``). The wire name is derived,
never authored: ``.`` becomes ``_`` and the result must match
:data:`WIRE_PATTERN` (48 = 64 minus the ``mcp__cortexsim__`` prefix). Nothing
else in the tree spells a wire name.
"""
from __future__ import annotations

import re

#: Appendix A, in order: 25 ``cortexsim.*`` then 16 ``xsiam.*``.
CANONICAL_IDS: tuple[str, ...] = (
    "cortexsim.health.get",
    "cortexsim.readiness.get",
    "cortexsim.scenario.list",
    "cortexsim.scenario.get",
    "cortexsim.pov.profiles",
    "cortexsim.pov.scope",
    "cortexsim.uctc.summary",
    "cortexsim.uctc.use_case",
    "cortexsim.uctc.test_case",
    "cortexsim.uctc.coverage",
    "cortexsim.uctc.gaps",
    "cortexsim.uctc.by_scenario",
    "cortexsim.run.launch",
    "cortexsim.run.get",
    "cortexsim.run.list",
    "cortexsim.run.results",
    "cortexsim.run.report",
    "cortexsim.run.pair",
    "cortexsim.run.abort",
    "cortexsim.run.reconcile",
    "cortexsim.run.verify",
    "cortexsim.connector.preflight",
    "cortexsim.assertion.list",
    "cortexsim.assertion.get",
    "cortexsim.assertion.run",
    "xsiam.operation.list",
    "xsiam.operation.get",
    "xsiam.operation.execute",
    "xsiam.session.index",
    "xsiam.dataset.list",
    "xsiam.dataset.schema",
    "xsiam.alert.list",
    "xsiam.correlation_rule.list",
    "xsiam.xql.query",
    "xsiam.incident.list",
    "xsiam.playbook.list",
    "xsiam.healthcheck",
    "xsiam.modeling_rule.list",
    "xsiam.parser.list",
    "xsiam.content.list",
    "xsiam.ingest.status",
)

WIRE_PATTERN = re.compile(r"^[a-z0-9_]{1,48}$")

GROUP = {"cortexsim", "xsiam"}


def wire_name(canonical: str) -> str:
    """Derive the wire name: ``.`` to ``_``; ``ValueError`` if it breaks the pattern."""
    wire = canonical.replace(".", "_")
    if not WIRE_PATTERN.match(wire):
        raise ValueError(f"wire name {wire!r} (from {canonical!r}) does not match {WIRE_PATTERN.pattern}")
    return wire


def canonical_id(wire: str) -> str:
    """Reverse lookup over :data:`CANONICAL_IDS`; ``ValueError`` if unknown."""
    for cid in CANONICAL_IDS:
        if cid.replace(".", "_") == wire:
            return cid
    raise ValueError(f"unknown tool wire name {wire!r}")


def group_of(canonical: str) -> str:
    """``cortexsim`` or ``xsiam``; ``ValueError`` for any other prefix."""
    prefix = canonical.split(".", 1)[0]
    if prefix not in GROUP:
        raise ValueError(f"tool id {canonical!r} has no known group (expected one of {sorted(GROUP)})")
    return prefix
