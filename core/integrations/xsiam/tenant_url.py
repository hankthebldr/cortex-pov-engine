# core/integrations/xsiam/tenant_url.py
"""THE tenant URL validator. There is exactly one, and this is it.

Before this module there were two: ``XsiamTenantConfig._validate_base_url``
enforced the PANW host pattern with a comment calling itself "the only place a
customer's tenant URL is validated before we send their API key to it", and
``XsiamConnector._base_url`` had **no validation at all** — it accepted
``http://10.0.0.5:8080`` and would POST the customer's API key to it in
cleartext on the next reconcile. Two validators for one concern, one of them
empty, is how a credential leak ships.

Note for whoever reads ``core/eal_simulator/bundle.py`` next: that module
deliberately allows ``verify_tls=False`` because a POV MitMs collector traffic
through the customer's own NGFW. **That reasoning does not transfer here.** A
collector receives synthetic events CortexSim generated; the tenant API receives
the customer's API key. Do not copy the bundle's leniency into this file.
"""
from __future__ import annotations

import re

from .exceptions import XsiamConfigError

# ── CONTRIBUTION POINT (Henry) ──────────────────────────────────────────────
# Too loose = SSRF-shaped credential leak to a typo'd or malicious host.
# Too strict = breaks when PANW adds a region/FQDN shape.
# Accepts both product families:
#   XDR   → https://api-<sub>.xdr.<region>.paloaltonetworks.com
#   XSIAM → https://api-<sub>.xsiam.<region>.paloaltonetworks.com   (3.x)
# Tighten or loosen to match the tenant FQDNs you actually see in the field.
TENANT_FQDN = re.compile(
    r"^https://api-[a-z0-9][a-z0-9-]*\.(?:xdr|xsiam)\.[a-z0-9.-]+\.paloaltonetworks\.com/?$",
    re.IGNORECASE,
)

#: The one-line contract, reused verbatim in every error message and in the
#: preflight's ``config`` stage so the DC reads the same sentence everywhere.
EXPECTED_SHAPE = "https://api-<sub>.(xdr|xsiam).<region>.paloaltonetworks.com"


def normalize_tenant_base_url(value: str) -> str:
    """Return the canonical ``https://…`` tenant base URL, or raise.

    Accepts a bare FQDN (the reconcile credential spells it ``fqdn``) and
    prepends ``https://`` — but ONLY https: an explicit ``http://`` is rejected
    rather than silently upgraded, because silently upgrading would hide an
    operator's mistake instead of correcting the record they will reuse.

    Raises :class:`XsiamConfigError` on anything that is not a PANW tenant host.
    """
    raw = (value or "").strip()
    if not raw:
        raise XsiamConfigError(f"tenant URL is empty; expected {EXPECTED_SHAPE}")

    lowered = raw.lower()
    if lowered.startswith("http://"):
        raise XsiamConfigError(
            f"refusing plaintext http:// for a tenant URL — the API key would be "
            f"sent in cleartext. Expected {EXPECTED_SHAPE} (got {_safe(raw)})"
        )
    if "://" in raw and not lowered.startswith("https://"):
        scheme = raw.split("://", 1)[0]
        raise XsiamConfigError(
            f"unsupported scheme '{scheme}' for a tenant URL; expected {EXPECTED_SHAPE}"
        )

    candidate = raw if lowered.startswith("https://") else f"https://{raw}"

    # userinfo (https://user:pass@evil/) parses as a valid URL and would send the
    # key to `evil`. The host regex alone would not catch it because `@` is not
    # in its character class — it just fails to match, but the error message
    # should say why rather than "bad shape".
    host_part = candidate[len("https://"):]
    if "@" in host_part.split("/", 1)[0]:
        raise XsiamConfigError(
            "tenant URL must not carry userinfo (user:pass@host) — the host after "
            f"'@' is where the API key would actually go. Expected {EXPECTED_SHAPE}"
        )

    if not TENANT_FQDN.match(candidate):
        raise XsiamConfigError(
            f"tenant URL must be {EXPECTED_SHAPE} (got {_safe(candidate)})"
        )
    return candidate.rstrip("/")


def is_valid_tenant_base_url(value: str) -> bool:
    """Non-raising form, for callers that only need a boolean (preflight)."""
    try:
        normalize_tenant_base_url(value)
    except XsiamConfigError:
        return False
    return True


def _safe(value: str) -> str:
    """Truncate an operator-supplied URL before it lands in an error string.

    An unbounded value here would be echoed into an API response body; keep it
    short and single-line.
    """
    flat = " ".join(str(value).split())
    return flat if len(flat) <= 120 else flat[:117] + "..."
