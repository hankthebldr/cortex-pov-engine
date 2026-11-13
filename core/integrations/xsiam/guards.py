"""The XQL safety rules, defined once.

Capability authorisation, the read-only write-stage refusal and the per-tenant
ledger budget used to live in three places (preflight, the verifier, nowhere).
Every surface that runs XQL consumes this module; an AST test asserts there is
exactly one definition of each.
"""
from __future__ import annotations

import contextlib
import re
from typing import Any, Mapping, Optional

from . import codes
from .exceptions import XqlRequestShapeInvalid, XqlWriteStageRefused, XsiamCapabilityError
from .ledger import tenant_ledger_key

#: Ops that are polls of an already-started query: gated, never charged.
XQL_POLL_OPERATION_IDS: frozenset[str] = frozenset(
    {"OP-XQL-GET-QUERY-RESULTS", "OP-XQL-GET-QUERY-RESULTS-STREAM"}
)

#: Every op needing the "xql" capability. GET-QUOTA runs no query: gated, not charged.
XQL_OPERATION_IDS: frozenset[str] = frozenset(
    {"OP-XQL-START-XQL-QUERY", "OP-XQL-GET-QUOTA"} | XQL_POLL_OPERATION_IDS
)

_WRITE_STAGE = re.compile(r"target\s+type\s*=\s*(dataset|lookup)\b", re.I)


def xql_capability_granted(config: Optional[Mapping]) -> bool:
    """True only when ``config["capabilities"]`` is a list containing "xql".

    Absent, empty or non-list is False: never infer a metered authorisation
    from the mere existence of a registration.
    """
    caps = (config or {}).get("capabilities")
    if not isinstance(caps, list):
        return False
    return "xql" in {str(c).lower() for c in caps}


def require_xql_capability(capabilities: Optional[list[str]], *, tenant: str) -> None:
    if not xql_capability_granted({"capabilities": capabilities}):
        raise XsiamCapabilityError(
            f"tenant '{tenant}' is not authorised for XQL: "
            f"config.capabilities does not include \"xql\""
        )


def xql_query_text(payload: Any) -> str:
    """The query string of a start-query body, or raise XqlRequestShapeInvalid."""
    request_data = payload.get("request_data") if isinstance(payload, dict) else None
    if not isinstance(request_data, dict):
        raise XqlRequestShapeInvalid("body.request_data must be an object")
    if "query" not in request_data:
        raise XqlRequestShapeInvalid("body.request_data.query is missing")
    query = request_data["query"]
    if not isinstance(query, str):
        raise XqlRequestShapeInvalid("body.request_data.query must be a string")
    if not query.strip():
        raise XqlRequestShapeInvalid("body.request_data.query is empty")
    return query


def refuse_xql_write_stage(query: str) -> None:
    """Refuse (never rewrite) a query carrying a dataset/lookup write stage."""
    if _WRITE_STAGE.search(query):
        raise XqlWriteStageRefused(
            "query contains a `target type = dataset|lookup` write stage; "
            "XQL through CortexSim is read-only"
        )


def ledger_key_for(client: Any) -> str:
    """A stable ledger key for a client — its tenant host.

    Derived rather than passed so EVERY caller is accounted whether or not it
    remembered to name the tenant. An unattributed query is an unbudgeted query.
    """
    return tenant_ledger_key(str(getattr(client, "_base", "") or ""))


@contextlib.asynccontextmanager
async def tenant_budget(client: Any):
    """Charge the process-wide tenant ledger around one XQL call.

    Both exits resolve ``pending`` upstream: ``verify_results`` and the
    assertion probes catch any runner exception and never score a budget
    CortexSim spent as a detection the customer missed.
    """
    from integrations.xsiam.exceptions import XsiamQuotaError  # noqa: PLC0415
    from integrations.xsiam.ledger import ledger  # noqa: PLC0415

    from connectors import tuning  # noqa: PLC0415

    tenant = ledger_key_for(client)
    ledger.charge(tenant, limit=tuning.xsiam_max_queries_per_day())
    try:
        yield
    except XsiamQuotaError as exc:
        # A 429 already tripped the breaker inside the client: one 429, one trip.
        # A local refusal (our own bucket/breaker) is not the tenant talking.
        if not getattr(exc, "tripped", False) and not getattr(exc, "local", False):
            ledger.trip(tenant, cooldown_seconds=tuning.xsiam_breaker_cooldown())
        raise
