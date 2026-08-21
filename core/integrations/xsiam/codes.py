# core/integrations/xsiam/codes.py
"""ONE error vocabulary for the tenant, carried by two different objects.

CortexSim talks to a Cortex tenant over two deliberately separate call paths
(see ``core/connectors/service.py`` for why they stay separate):

  * the **reconcile connector** (``core/connectors/xsiam.py``) — stdlib urllib,
    offline-safe, returns ``PullResult(ok=False, error=...)`` and never raises;
  * the **tenant client** (``core/integrations/xsiam/client.py``) — httpx,
    raises typed :class:`XsiamError` subclasses.

Those are two carriers, and before this module they were also two *vocabularies*:
a DC hitting a 401 saw ``"tenant returned HTTP 401"`` from one half of the
product and ``XSIAM_AUTH_ERROR`` from the other. The remediation text had to be
written twice and could drift. This module is the single vocabulary; both
carriers now populate it, and one remediation table serves both.

The strings are exactly the ``XsiamError.code`` values, so nothing downstream
has to translate.
"""
from __future__ import annotations

from typing import Optional

XSIAM_AUTH_ERROR = "XSIAM_AUTH_ERROR"
XSIAM_QUOTA_ERROR = "XSIAM_QUOTA_ERROR"
XSIAM_API_ERROR = "XSIAM_API_ERROR"
XSIAM_QUERY_ERROR = "XSIAM_QUERY_ERROR"
XSIAM_CONFIG_ERROR = "XSIAM_CONFIG_ERROR"
XSIAM_TRANSPORT_ERROR = "XSIAM_TRANSPORT_ERROR"
XSIAM_PARSE_ERROR = "XSIAM_PARSE_ERROR"

ALL_CODES = (
    XSIAM_AUTH_ERROR,
    XSIAM_QUOTA_ERROR,
    XSIAM_API_ERROR,
    XSIAM_QUERY_ERROR,
    XSIAM_CONFIG_ERROR,
    XSIAM_TRANSPORT_ERROR,
    XSIAM_PARSE_ERROR,
)

#: What the DC should DO, and — critically — what the failure means for the
#: verdict. "403" is a status, not remediation. Every string here names the
#: consequence in verdict terms so nobody reads an infrastructure problem as a
#: statement about the customer's detection coverage.
REMEDIATION: dict[str, str] = {
    XSIAM_AUTH_ERROR: (
        "The tenant rejected this API key. Check the key, its key id "
        "(x-xdr-auth-id) and the auth mode (standard vs advanced). Nothing was "
        "measured, so every affected verdict stays `pending` — this is not a "
        "detection failure."
    ),
    XSIAM_QUOTA_ERROR: (
        "The tenant's API/XQL quota is exhausted; the customer's own analysts "
        "share it. CortexSim has stopped querying for this cycle. Verdicts stay "
        "`pending` and will be retried after the backoff."
    ),
    XSIAM_API_ERROR: (
        "The tenant returned an unexpected HTTP status. If it is 403 the API "
        "key's role is missing a scope (alert read / XQL); if 5xx the tenant is "
        "unhealthy. Verdicts stay `pending`."
    ),
    XSIAM_QUERY_ERROR: (
        "The tenant accepted the request but the query failed (syntax, unknown "
        "dataset, or a timeout). Verdicts stay `pending` — a query CortexSim "
        "could not run proves nothing about the detection."
    ),
    XSIAM_CONFIG_ERROR: (
        "The registered tenant config is invalid. base_url must be "
        "https://api-<sub>.(xdr|xsiam).<region>.paloaltonetworks.com and "
        "api_key_id must be set. Re-register the tenant."
    ),
    XSIAM_TRANSPORT_ERROR: (
        "CortexSim could not reach the tenant (DNS, TLS, connection refused, or "
        "an egress policy on this host). Verdicts stay `pending`."
    ),
    XSIAM_PARSE_ERROR: (
        "The tenant replied in a shape CortexSim does not recognise. This is a "
        "CortexSim/tenant-version mismatch, NOT an empty tenant — verdicts stay "
        "`pending` rather than being scored against zero rows."
    ),
}


def code_for_status(status: int) -> str:
    """Map an upstream HTTP status onto the shared vocabulary.

    Used by the stdlib connector, which only ever sees a status code — so a DC
    gets the same ``XSIAM_AUTH_ERROR`` from a reconcile 401 as from an XQL 401.
    """
    if status == 401:
        return XSIAM_AUTH_ERROR
    if status == 403:
        return XSIAM_API_ERROR
    if status == 429:
        return XSIAM_QUOTA_ERROR
    return XSIAM_API_ERROR


def remediation_for(code: Optional[str]) -> Optional[str]:
    """Remediation text for a code, or None when the code is unknown."""
    return REMEDIATION.get(code or "")
