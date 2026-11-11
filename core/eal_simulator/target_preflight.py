"""Per-target preflight: a ladder whose write rung is opt-in.

  configured  the row can be used (URL valid; key present when the kind needs one)
  reachable   DNS + TCP connect (+ TLS handshake for https) — no HTTP request,
              so nothing reaches the tenant
  accepted    ONLY with ``write_canary=True``: POST the one discard-safe canary
              record the campaign preflight already uses, classified by the
              delivery taxonomy the live run uses

Verdict: the highest rung passed or the first failed — ``not_configured`` ·
``unreachable`` · ``reachable`` · ``refused`` · ``accepted``. ``accepted`` means
the collector returned 2xx to SimCore's POST; it does not prove the record was
parsed, landed in a dataset, or fired anything, so ``tenant_verified`` is 0.

Both transports are injectable. The key is used for the canary header and never
appears in the result.
"""

from __future__ import annotations

import asyncio
import ssl
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlparse

from .collector import ClientFactory, CollectorTarget, preflight_collector
from .delivery import REMEDIATION, classify_exception
from .targets import KINDS_REQUIRING_KEY, TargetSpec

#: (host, port, use_tls, timeout) -> None; raises on failure.
Connector = Callable[[str, int, bool, float], Awaitable[None]]

CONNECT_TIMEOUT_S = 10.0

_PROVES = {
    "configured": ("the target can be used", "anything about the network"),
    "reachable": (
        "the collector's host and port answer from SimCore (TCP, and TLS for https)",
        "that it accepts records, or that a jumpbox inside the customer network can reach it",
    ),
    "accepted": (
        "the collector returned 2xx to SimCore's POST contract",
        "that the record was parsed, landed in the dataset, or that any detection fires",
    ),
}


async def _default_connect(host: str, port: int, use_tls: bool, timeout: float) -> None:
    ctx: Optional[ssl.SSLContext] = None
    if use_tls:
        # Matches the live run, which does not verify certificates by default
        # (lab forwarders commonly present self-signed ones). A stricter preflight
        # would fail doors the run would use.
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    _reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port, ssl=ctx, server_hostname=host if use_tls else None),
        timeout=timeout,
    )
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:  # pragma: no cover - close errors carry no signal
        pass


def _rung(name: str, status: str, code: Optional[str] = None,
          detail: Optional[str] = None) -> dict[str, Any]:
    proves, not_proves = _PROVES[name]
    return {"rung": name, "status": status, "code": code, "detail": detail,
            "proves": proves, "does_not_prove": not_proves}


async def preflight_target(
    *,
    spec: TargetSpec,
    token: Optional[str],
    write_canary: bool = False,
    connect: Optional[Connector] = None,
    client_factory: Optional[ClientFactory] = None,
) -> dict[str, Any]:
    """Run the ladder for one target and return the result (never raises for
    a transport problem — that is reported in the taxonomy)."""
    started = time.monotonic()
    parsed = urlparse(spec.url)
    rungs: list[dict[str, Any]] = []
    result: dict[str, Any] = {
        "target": spec.name, "kind": spec.kind, "url": spec.url,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "write_canary": bool(write_canary), "post_attempted": False,
        "records_written": 0, "status_code": None, "tenant_verified": 0,
        "warnings": spec.warnings(),
    }

    def finish(verdict: str, code: Optional[str], remediation: str = "") -> dict[str, Any]:
        for name in ("configured", "reachable", "accepted"):
            if not any(r["rung"] == name for r in rungs):
                rungs.append(_rung(name, "not_checked"))
        result.update(verdict=verdict, code=code, rungs=rungs, remediation=remediation,
                      latency_ms=round((time.monotonic() - started) * 1000, 2))
        return result

    # configured -------------------------------------------------------------
    if spec.kind in KINDS_REQUIRING_KEY and not token:
        rungs.append(_rung("configured", "failed", "COLLECTOR_KEY_MISSING",
                           f"a {spec.kind} refuses a POST without its API key"))
        return finish("not_configured", "COLLECTOR_KEY_MISSING",
                      "Store the collector's API key on this target.")
    rungs.append(_rung("configured", "ok"))

    # reachable --------------------------------------------------------------
    try:
        await (connect or _default_connect)(
            spec.host, spec.port, parsed.scheme == "https", CONNECT_TIMEOUT_S)
    except Exception as exc:
        code = classify_exception(exc)
        rungs.append(_rung("reachable", "failed", code, f"{type(exc).__name__}: {exc}"))
        return finish("unreachable", code, REMEDIATION.get(code, ""))
    rungs.append(_rung("reachable", "ok"))

    if not write_canary:
        rungs.append(_rung("accepted", "not_checked", None,
                           "send a test record (write_canary) to prove the collector accepts records"))
        return finish("reachable", None)

    # accepted (opt-in write) ------------------------------------------------
    target = CollectorTarget(
        step_id="preflight", plugin="collector-target", url=spec.url,
        scheme=parsed.scheme, host=spec.host, port=spec.port, path=parsed.path or "/",
        auth_header=spec.auth_header, auth_scheme=spec.auth_scheme,
        has_auth_token=bool(token), dataset=spec.dataset,
    )
    result["post_attempted"] = True
    probe = await preflight_collector(
        target, campaign_id=f"collector-target:{spec.name}", auth_token=token,
        verify_tls=False, client_factory=client_factory,
    )
    result["status_code"] = probe.get("status_code")
    if probe.get("delivered"):
        result["records_written"] = 1
        rungs.append(_rung("accepted", "ok"))
        return finish("accepted", None)
    code = probe.get("code") or "collector_error"
    if not probe.get("reachable"):
        rungs.append(_rung("accepted", "failed", code, probe.get("error")))
        return finish("unreachable", code, probe.get("remediation") or REMEDIATION.get(code, ""))
    rungs.append(_rung("accepted", "failed", code, f"HTTP {probe.get('status_code')}"))
    return finish("refused", code, probe.get("remediation") or REMEDIATION.get(code, ""))
