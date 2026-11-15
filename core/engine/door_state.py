"""Door state as a timeline (spec 2026-11-11-ingestion-doors §6).

Every check appends a ``DoorEvent``; a door's state at any moment is the last
conclusive event at or before it. ``unknown`` never changes it, and nothing
expires on a timer. All timestamps are naive UTC, like the rest of the models.
Nothing here calls a tenant: ``configured_facts`` reads rows SimCore already has.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from engine.ingestion_doors import DOORS, door_key
from models import DoorEvent

STATES = ("open", "closed", "unknown")
BASES = ("verified", "attested", "check", "configured")
RUNGS = ("authored", "configured", "reachable", "verified")
_CONCLUSIVE = ("open", "closed")


def _require_naive(value: datetime, what: str) -> None:
    if value.tzinfo is not None:
        raise ValueError(f"{what} must be naive UTC, got a tz-aware datetime")


def _ordered(events: list[DoorEvent]) -> list[DoorEvent]:
    return sorted(events, key=lambda e: (e.at, e.id if e.id is not None else 0))


def effective_state(events: list[DoorEvent], when: datetime) -> tuple[str, Optional[DoorEvent]]:
    """The last conclusive event at or before ``when``; ``unknown`` rows never count."""
    _require_naive(when, "when")
    found: Optional[DoorEvent] = None
    for e in _ordered(events):
        if e.state not in STATES:
            raise ValueError(f"unrecognised door state {e.state!r}")
        if e.at > when:
            break
        if e.state in _CONCLUSIVE:
            found = e
    if found is None:
        return "unknown", None
    return found.state, found


async def record(db: AsyncSession, *, door_key: str, door_id: str, state: str,
                 basis: str, rung: str, evidence: dict,
                 attested_by: Optional[str] = None, queries_issued: int = 0,
                 at: Optional[datetime] = None) -> DoorEvent:
    if state not in STATES:
        raise ValueError(f"unrecognised door state {state!r}")
    if basis not in BASES:
        raise ValueError(f"unrecognised door basis {basis!r}")
    if rung not in RUNGS:
        raise ValueError(f"unrecognised door rung {rung!r}")
    if at is None:
        at = datetime.utcnow()
    _require_naive(at, "at")
    ev = DoorEvent(door_key=door_key, door_id=door_id, state=state, basis=basis,
                   rung=rung, at=at, evidence=evidence, attested_by=attested_by,
                   queries_issued=queries_issued)
    db.add(ev)
    await db.commit()
    return ev


async def current(db: AsyncSession) -> dict[str, dict]:
    """Per door key: effective state now, basis, rung and how stale it is."""
    now = datetime.utcnow()
    rows = (await db.execute(select(DoorEvent))).scalars().all()
    by_key: dict[str, list[DoorEvent]] = {}
    for r in rows:
        by_key.setdefault(r.door_key, []).append(r)
    out: dict[str, dict] = {}
    for key, evs in by_key.items():
        state, ev = effective_state(evs, now)
        last = _ordered(evs)[-1]
        shown = ev or last
        out[key] = {
            "door_id": shown.door_id,
            "state": state,
            "basis": ev.basis if ev else None,
            "rung": shown.rung,
            "last_conclusive_at": ev.at if ev else None,
            "last_event_at": last.at,
            "age_seconds": (now - ev.at).total_seconds() if ev else None,
        }
    return out


async def configured_facts(db: AsyncSession) -> dict[str, str]:
    """Rungs derivable with zero outbound calls: ``configured`` or ``authored``.

    An agent-scoped door (``xdr_agent``, ``xdr_agent_xth``, ``ngfw_stitch``) reads
    ``configured`` for a beacon only when the beacon is online **and** an
    ``xsiam_tenant`` credential with XQL granted exists to check it with;
    ``ngfw_stitch`` also needs a registered firewall. An online beacon alone is the
    CortexSim beacon, not the Cortex XDR agent on that host (spec §1 defect 1), so
    it configures nothing by itself.
    """
    import integrations.panos as panos
    from api.agents import _derive_status
    from engine import collector_targets
    from integrations.xsiam.guards import xql_capability_granted
    from models import Agent
    from security import CredentialStore

    store = CredentialStore(db)
    tenants = await store.list_integrations(kind="xsiam_tenant")
    xql = any(xql_capability_granted(t.config) for t in tenants)
    firewall = bool(await store.list_integrations(kind=panos.PANOS_KIND))
    specs = await collector_targets.load_specs(db)
    agents = (await db.execute(select(Agent))).scalars().all()
    now = datetime.utcnow()
    online = {a.agent_id for a in agents if _derive_status(a.last_seen, now)[0] == "online"}

    facts: dict[str, str] = {}
    for d in DOORS:
        if d.scope == "tenant":
            ok = firewall if d.id.startswith("ngfw_") else xql
            facts[d.id] = "configured" if ok else "authored"
        elif d.scope == "agent":
            for a in agents:
                ok = a.agent_id in online and xql and (firewall if d.id == "ngfw_stitch" else True)
                facts[door_key(d.id, a.agent_id)] = "configured" if ok else "authored"
        elif d.scope == "target":
            for s in specs:
                facts[door_key(d.id, s.name)] = "configured"
        else:
            raise ValueError(f"unrecognised door scope {d.scope!r}")
    return facts
