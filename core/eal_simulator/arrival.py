"""Did the tagged example records reach the collector's dataset?

One fixed, read-only XQL query through the existing client. No FastAPI here.
Three states, and only the first carries a number:

  counted      the tenant answered SUCCESS; ``found`` is what it counted
  pending      the query is still running; ``found`` is None, never 0
  not_checked  something stopped the check; ``code`` says what

``result_rows`` returns ``[]`` for a reply with no ``results`` key, so the
status is read first: a running query must not read as zero rows.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from integrations.xsiam.exceptions import (
    XsiamApiError, XsiamAuthError, XsiamCapabilityError, XsiamConfigError,
    XsiamQueryError, XsiamQuotaError,
)
from integrations.xsiam.guards import require_xql_capability, tenant_budget
from integrations.xsiam.ledger import DailyBudgetExhausted, QuotaBreakerOpen
from integrations.xsiam.queries import result_rows

PROVES = [
    "These records, sent from SimCore just now, were ingested into the dataset.",
]
DOES_NOT_PROVE = [
    "That a jumpbox inside the customer network can reach the collector.",
    "That XDM maps the dataset.",
    "That any detection fires.",
]

_DAY_MS = 24 * 60 * 60 * 1000


def arrival_query(dataset: str, token: str) -> str:
    return (f"dataset = {dataset}\n"
            f'| filter cortexsim_canary = "{token}"\n'
            "| comp count() as found, min(_insert_time) as first_seen, "
            "max(_insert_time) as last_seen")


def _timeframe(since: Optional[datetime]) -> dict[str, int]:
    if since is None:
        return {"relativeTime": _DAY_MS}
    if since.tzinfo is None:  # a naive time is UTC, never host-local
        since = since.replace(tzinfo=timezone.utc)
    return {"from": int(since.timestamp() * 1000),
            "to": int(datetime.now(timezone.utc).timestamp() * 1000)}


def _result(state: str, *, delivered: int, query_id: Optional[str], issued: int,
            found: Optional[int] = None, first_seen: Any = None, last_seen: Any = None,
            code: Optional[str] = None, reason: Optional[str] = None) -> dict[str, Any]:
    return {"state": state, "found": found, "delivered": delivered,
            "first_seen": first_seen, "last_seen": last_seen, "query_id": query_id,
            "queries_issued": issued, "code": code, "reason": reason,
            "proves": list(PROVES), "does_not_prove": list(DOES_NOT_PROVE)}


async def check_arrival(client: Any, *, dataset: str, token: str, delivered: int,
                        since: Optional[datetime],
                        query_id: Optional[str]) -> dict[str, Any]:
    issued = 0

    def refused(code: str, reason: str) -> dict[str, Any]:
        return _result("not_checked", delivered=delivered, query_id=query_id,
                       issued=issued, code=code, reason=reason)

    try:
        require_xql_capability(getattr(client, "capabilities", None), tenant="")
        if query_id is None:
            async with tenant_budget(client):
                query_id = await client.start_xql_query(
                    arrival_query(dataset, token), _timeframe(since))
            issued = 1
        reply = await client.get_query_results(query_id, limit=10)
        status = reply.get("status")
        if status is None or status == "PENDING":
            return _result("pending", delivered=delivered, query_id=query_id, issued=issued)
        if status != "SUCCESS":
            return refused("QUERY_FAILED", f"the tenant ended the query with status {status!r}")
        rows = result_rows(reply)
    except XsiamConfigError as exc:
        return refused("TENANT_NOT_CONFIGURED", exc.detail)
    except XsiamCapabilityError as exc:
        return refused("XQL_NOT_AUTHORISED", exc.detail)
    except (QuotaBreakerOpen, DailyBudgetExhausted, XsiamQuotaError) as exc:
        return refused("QUOTA", getattr(exc, "detail", None) or str(exc))
    except XsiamAuthError as exc:
        return refused("TENANT_UNAUTHORIZED", exc.detail)
    except XsiamApiError as exc:
        code = "TENANT_UNAUTHORIZED" if exc.upstream_status in (401, 403) else "TENANT_ERROR"
        return refused(code, exc.detail)
    except XsiamQueryError as exc:
        return refused("UNRECOGNISED_REPLY", exc.detail)

    if not rows:  # SUCCESS with no rows is a real zero: the tenant answered
        return _result("counted", delivered=delivered, query_id=query_id,
                       issued=issued, found=0)
    found = rows[0].get("found") if len(rows) == 1 and isinstance(rows[0], dict) else None
    if type(found) is not int:
        return refused("UNRECOGNISED_REPLY",
                       "the tenant's reply carried no single integer 'found' count")
    return _result("counted", delivered=delivered, query_id=query_id, issued=issued,
                   found=found, first_seen=rows[0].get("first_seen"),
                   last_seen=rows[0].get("last_seen"))
