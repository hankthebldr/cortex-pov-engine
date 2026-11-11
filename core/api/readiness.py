"""CortexSim API — ``GET /api/readiness``: the connector ladder, stated server-side.

The console's Readiness page renders four rungs (AUTHORED · CONFIGURED ·
REACHABLE · VERIFIED) and states ``tenant-verified: N``. This route states the
same facts to callers that are not the console. It makes **zero outbound
calls**: every field is read from the local DB, the vault's metadata or the
in-process quota ledger. It never returns credential config — only
``capabilities`` and a ledger snapshot keyed from ``base_url``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from connectors import tuning
from connectors.base import coerce_utc
from connectors.provenance import tenant_verified
from database import get_db
from integrations.xsiam.guards import xql_capability_granted
from integrations.xsiam.ledger import ledger, tenant_ledger_key
from models import Scenario
from security.credentials import CredentialStore

router = APIRouter(prefix="/readiness", tags=["readiness"])

# Verbatim from connectorState.js (the RUNG objects, :270-305).
_PROVES = {
    "authored": {"proves": "a claim exists in the corpus",
                 "does_not_prove": "nothing about the customer's environment"},
    "configured": {"proves": "a key is in the vault",
                   "does_not_prove": "that it works, or that its role is scoped for what you need"},
    "reachable": {"proves": "auth + network reached the tenant at that moment",
                  "does_not_prove": "that any detection was queried, or that the key has XQL scope"},
    "verified": {"proves": "the measurement loop closed against the customer's stack",
                 "does_not_prove": "—"},
}


def _iso(value: Any) -> Optional[str]:
    """Any timestamp -> ISO-8601 ending ``+00:00``, via the one UTC coercer."""
    dt = coerce_utc(value)
    return dt.replace(tzinfo=timezone.utc).isoformat() if dt else None


def _ledger_for(kind: str, config: dict[str, Any]) -> Optional[dict[str, Any]]:
    if kind != "xsiam_tenant" or not config.get("base_url"):
        return None
    snap = dict(ledger.snapshot(tenant_ledger_key(config["base_url"]),
                                limit=tuning.xsiam_max_queries_per_day()))
    if snap.get("breaker_open_until"):
        snap["breaker_open_until"] = _iso(snap["breaker_open_until"])
    return snap


_RL_STALE_AFTER = timedelta(hours=24)


def _response_layer_block(row: Any) -> Optional[dict[str, Any]]:
    """The stored SOC Framework probe, or None. Reads the row only."""
    layer = getattr(row, "response_layer", None)
    if not isinstance(layer, dict):
        return None
    read_at = coerce_utc(getattr(row, "response_layer_read_at", None))
    # An unreadable/absent timestamp cannot vouch for freshness: stale.
    stale = read_at is None or (datetime.utcnow() - read_at) > _RL_STALE_AFTER
    return {
        "layer": layer,
        "state": layer.get("state"),
        "read_at": _iso(read_at),
        "stale": stale,
        "proves": "the SOC Framework is configured on this tenant",
        "does_not_prove": "that the framework handled any CortexSim case",
    }


@router.get("")
async def get_readiness(session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    tv = await tenant_verified(session)
    authored = (await session.execute(select(func.count(Scenario.id)))).scalar_one() > 0
    verified_names = set(tv.names)

    integrations = []
    for row in await CredentialStore(session).list_integrations():
        config = row.config if isinstance(row.config, dict) else {}
        caps = config.get("capabilities")
        integrations.append({
            "name": row.name,
            "kind": row.kind,
            "capabilities": caps if isinstance(caps, list) else None,
            "xql_capability_granted": xql_capability_granted(config),
            "last_verified_ok": row.last_verified_ok,
            "last_verified_at": _iso(row.last_verified_at),
            "last_verified_error": row.last_verified_error,
            "ledger": _ledger_for(row.kind, config),
            "response_layer": _response_layer_block(row),
            "rungs": {
                "authored": authored,
                "configured": True,
                "reachable": row.last_verified_ok is True,
                "verified": row.name in verified_names,
            },
        })

    return {
        "server_time": _iso(datetime.utcnow()),
        "tenant_verified": tv.count,
        "tenant_verified_names": tv.names,
        "integrations": integrations,
        "proves": _PROVES,
        "outbound_calls": 0,
        "statement": f"No tenant call is made by this route. tenant-verified: {tv.count}",
    }
