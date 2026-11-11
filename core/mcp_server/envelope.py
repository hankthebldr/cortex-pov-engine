"""The evidence envelope every tool returns (spec section 7).

The MCP layer computes none of the numbers it reports: ``queries_issued``,
the ledger and ``tenant_verified`` are read off SimCore responses. This module
only shapes them and writes the one honesty sentence in the console's words.
"""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel

from connectors.base import coerce_utc

Basis = Literal["authored", "configured", "reachable", "verified", "preview", "simcore", "derived"]


class Ledger(BaseModel):
    key: str
    used_today: int
    budget: int
    remaining: int
    breaker_open: bool
    breaker_until: Optional[str] = None


def _iso_utc(value: Any) -> Optional[str]:
    """ISO-8601 with a ``+00:00`` offset, via the repo's one timestamp coercer."""
    dt = coerce_utc(value)
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).isoformat()


def ledger_from_snapshot(snap: Optional[dict]) -> Optional[Ledger]:
    """Map ``integrations.xsiam.ledger.snapshot`` keys; ``None`` stays ``None``."""
    if snap is None:
        return None
    until = snap["breaker_open_until"]
    return Ledger(
        key=snap["tenant"],
        used_today=snap["queries_last_24h"],
        budget=snap["limit"],
        remaining=snap["remaining"],
        breaker_open=snap["breaker_open"],
        breaker_until=None if until is None else _iso_utc(until),
    )


class Evidence(BaseModel):
    basis: Basis
    #: ``None`` = SimCore accepted the request and did not report a count; whether
    #: the tenant was called is then not known either. Never read as 0.
    queries_issued: Optional[int] = 0
    #: ``True`` only when the tool got the tenant's reply back through SimCore (a
    #: live executor read, an XQL start/poll). It is independent of
    #: ``queries_issued``: a results poll answers at a metered cost of 0.
    tenant_answered: bool = False
    http_round_trips: Optional[int] = None
    tenant: Optional[str] = None
    ledger: Optional[Ledger] = None
    #: ``None`` = ``GET /api/readiness`` could not be read; never a stated 0.
    tenant_verified: Optional[int] = 0
    statement: str
    as_of: Optional[str] = None
    truncated: bool = False


class Envelope(BaseModel):
    """``data`` is optional so an error's structured content validates too."""

    data: Any = None
    evidence: Evidence


def statement(
    *,
    basis: str,
    queries_issued: Optional[int],
    tenant: Optional[str],
    tenant_verified: Optional[int],
    tool_id: Optional[str] = None,
    not_backed: bool = False,
    simcore_unreachable: bool = False,
    tenant_answered: bool = False,
) -> str:
    """The section 7 sentence for the call's condition (checked in this order)."""
    tail = "tenant-verified: not read" if tenant_verified is None else f"tenant-verified: {tenant_verified}"
    if simcore_unreachable:
        return f"No tenant answered this call. SimCore itself was unreachable. {tail}"
    if not_backed:
        return (
            f"{tool_id} has no backing operation in the XSIAM public API; "
            f"nothing was queried. {tail}"
        )
    if basis == "preview":
        return (
            "Preview only. Nothing was sent to the tenant. To execute, a human runs "
            f"the request in execute_as_human. {tail}"
        )
    reachable = ("This is reachable evidence; a detection is verified only when a run "
                 f"verdict carries tenant provenance. {tail}")
    if tenant_answered or (queries_issued is not None and queries_issued >= 1):
        # Metered operations are the ledger's unit, not "was the tenant contacted":
        # a results poll answers and costs 0, and the sentence says exactly that.
        if queries_issued is None:
            return (f"Tenant {tenant or '(unnamed)'} answered; how many metered operations this "
                    f"call cost is not known — this is not zero. {reachable}")
        noun = "operation" if queries_issued == 1 else "operations"
        return (f"Tenant {tenant or '(unnamed)'} answered; this call cost {queries_issued} "
                f"metered {noun}. {reachable}")
    if queries_issued is None:
        return (
            "SimCore accepted the request; whether the tenant was called and how many "
            f"operations it issued is not known — this is not zero. {tail}"
        )
    return f"No tenant answered this call. This is {basis}, not tenant evidence. {tail}"


def as_of_from_date_header(value: Optional[str], *, now: Optional[datetime] = None) -> str:
    """SimCore's clock in UTC with offset: the ``Date`` header, else ``now``."""
    if value:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            parsed = None
        iso = _iso_utc(parsed) if parsed is not None else None
        if iso is not None:
            return iso
    return _iso_utc(now or datetime.now(timezone.utc)) or datetime.now(timezone.utc).isoformat()
