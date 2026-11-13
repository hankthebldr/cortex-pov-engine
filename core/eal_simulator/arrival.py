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

import re
from datetime import datetime, timezone
from typing import Any, Optional

from integrations.xsiam.exceptions import (
    XsiamApiError, XsiamAuthError, XsiamCapabilityError, XsiamConfigError,
    XsiamQueryError, XsiamQuotaError,
)
from integrations.xsiam.guards import require_xql_capability, tenant_budget
from integrations.xsiam.ledger import DailyBudgetExhausted, QuotaBreakerOpen
from integrations.xsiam.queries import result_rows

def proves_for(dataset: Optional[str], found: Optional[int] = None,
               delivered: Optional[int] = None) -> list[str]:
    """What a count proves. Empty unless the tenant counted at least one tagged record."""
    if not found or found < 1:
        return []
    if delivered is not None and found < delivered:
        return [f"{found} of the {delivered} records sent from SimCore just now were "
                f"ingested into `{dataset or '<dataset>'}`."]
    return [f"These records, sent from SimCore just now, were ingested into "
            f"`{dataset or '<dataset>'}`."]


DOES_NOT_PROVE = [
    "That a jumpbox inside the customer network can reach the collector.",
    "That XDM maps the dataset.",
    "That any detection fires.",
]

_DAY_MS = 24 * 60 * 60 * 1000

# A stored dataset is pasted into the query text, so only a bare identifier is queried.
_DATASET_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


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


def _result(state: str, *, dataset: Optional[str], delivered: int, query_id: Optional[str], issued: int,
            found: Optional[int] = None, first_seen: Any = None, last_seen: Any = None,
            code: Optional[str] = None, reason: Optional[str] = None) -> dict[str, Any]:
    return {"state": state, "found": found, "delivered": delivered,
            "first_seen": first_seen, "last_seen": last_seen, "query_id": query_id,
            "queries_issued": issued, "code": code, "reason": reason,
            "proves": proves_for(dataset, found, delivered) if state == "counted" else [],
            "does_not_prove": list(DOES_NOT_PROVE)}


def not_checked_result(*, dataset: Optional[str], delivered: int, query_id: Optional[str],
                       code: str, reason: str, issued: int = 0) -> dict[str, Any]:
    """The one shape of a check that did not run or did not finish: no count, a code."""
    return _result("not_checked", dataset=dataset, delivered=delivered, query_id=query_id,
                   issued=issued, code=code, reason=reason)


async def check_arrival(client: Any, *, dataset: str, token: str, delivered: int,
                        since: Optional[datetime],
                        query_id: Optional[str]) -> dict[str, Any]:
    issued = 0
    polling = False  # True once the start (if any) is behind us

    def refused(code: str, reason: str) -> dict[str, Any]:
        return not_checked_result(dataset=dataset, delivered=delivered, query_id=query_id,
                                  code=code, reason=reason, issued=issued)

    if not isinstance(dataset, str) or not _DATASET_RE.fullmatch(dataset):
        return refused("DATASET_NOT_QUERYABLE",
                       "the target's dataset is not a bare dataset name, so it is not "
                       "put into a query; nothing was sent to the tenant")

    tenant_query = False  # inside a call the tenant answers: its query errors are QUERY_FAILED
    try:
        require_xql_capability(getattr(client, "capabilities", None), tenant="")
        if query_id is None:
            async with tenant_budget(client):
                issued = 1  # the ledger is charged on entry, before the call is made
                tenant_query = True
                query_id = await client.start_xql_query(
                    arrival_query(dataset, token), _timeframe(since))
                tenant_query = False
        polling = True
        tenant_query = True
        reply = await client.get_query_results(query_id, limit=10)
        tenant_query = False
        if not isinstance(reply, dict):
            return refused("UNRECOGNISED_REPLY",
                           f"the tenant's reply was a {type(reply).__name__}, not an object")
        status = reply.get("status")
        if status is None or status == "PENDING":
            return _result("pending", dataset=dataset, delivered=delivered, query_id=query_id, issued=issued)
        if status != "SUCCESS":
            return refused("QUERY_FAILED", f"the tenant ended the query with status {status!r}")
        rows = result_rows(reply)
    except XsiamConfigError as exc:
        return refused("TENANT_NOT_CONFIGURED", exc.detail)
    except XsiamCapabilityError as exc:
        return refused("XQL_NOT_AUTHORISED", exc.detail)
    except XsiamQuotaError as exc:
        if getattr(exc, "local", False):
            if polling or getattr(exc, "query_started", None):
                # POVengine's own budget refused the results fetch, so this is
                # pending, not a failure. Only a query this call started is known
                # to be running; a caller-supplied id is not claimed to be.
                started = getattr(exc, "query_started", None)
                reason = ("POVengine's own request budget refused the results fetch "
                          "locally; the query is running on the tenant. Test again later."
                          if issued else
                          "POVengine's own request budget refused the results fetch for "
                          "this query id locally; no answer was read. Test again with the "
                          "same query id later.")
                return _result(
                    "pending", dataset=dataset, delivered=delivered,
                    query_id=started or query_id, issued=issued, reason=reason)
            issued = 0  # refused before the start left; tenant_budget refunded it
            return refused("QUOTA", "POVengine's own request budget refused the "
                           "request before it was sent to the tenant; nothing was "
                           "charged. Test again later.")
        return refused("QUOTA", getattr(exc, "detail", None) or str(exc))
    except (QuotaBreakerOpen, DailyBudgetExhausted) as exc:
        return refused("QUOTA", getattr(exc, "detail", None) or str(exc))
    except XsiamAuthError as exc:
        return refused("TENANT_UNAUTHORIZED", exc.detail)
    except XsiamApiError as exc:
        code = "TENANT_UNAUTHORIZED" if exc.upstream_status in (401, 403) else "TENANT_ERROR"
        return refused(code, exc.detail)
    except XsiamQueryError as exc:
        # From the tenant's own call it is the tenant's error (syntax, no such
        # dataset); from result_rows it is a reply shape this code cannot read.
        return refused("QUERY_FAILED" if tenant_query else "UNRECOGNISED_REPLY", exc.detail)

    if not rows:  # SUCCESS with no rows is a real zero: the tenant answered
        return _result("counted", dataset=dataset, delivered=delivered, query_id=query_id,
                       issued=issued, found=0)
    found = rows[0].get("found") if len(rows) == 1 and isinstance(rows[0], dict) else None
    if type(found) is not int:
        return refused("UNRECOGNISED_REPLY",
                       "the tenant's reply carried no single integer 'found' count")
    return _result("counted", dataset=dataset, delivered=delivered, query_id=query_id, issued=issued,
                   found=found, first_seen=rows[0].get("first_seen"),
                   last_seen=rows[0].get("last_seen"))
