"""
Collector destination resolution + preflight for the log-streamer families.

Where a campaign's signal is *going* used to be knowable only by reading each
step's ``params.collector_url`` out of a stored JSON blob. That is the one
setting a DC must get right before a POV — the Broker VM's HTTP applet, the
native cloud collector's ``/logs/v1/event``, or a Cribl/nginx forwarder — and
getting it wrong produces a green campaign that ingested nothing.

This module makes the destination a first-class, inspectable object:

  * ``resolve_campaign_collectors`` walks a campaign's steps, finds every step
    whose plugin params carry a ``collector_url``, and returns a validated
    ``CollectorTarget`` per step plus the campaign-wide destination set. It
    flags the two mistakes that silently break a live run — a collector host
    missing from ``target_allowlist`` (the safety policy will refuse the step)
    and a plaintext-http destination.
  * ``preflight_collector`` POSTs one tiny canary record and reports back in
    the ``delivery`` taxonomy, so "can this host actually ingest?" is answered
    before the customer is watching.

Secrets never leave: a target reports *whether* an auth token is configured
and under which header, never the token itself.
"""

from __future__ import annotations

import dataclasses
import ipaddress
import json
import time
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import httpx
from pydantic import SecretStr

from .campaign import Campaign
from .delivery import REMEDIATION, classify_exception, classify_status
from .registry import PluginRegistry


#: Default port per scheme, so a target always reports a concrete port for the
#: firewall rule a DC has to request.
_DEFAULT_PORTS = {"http": 80, "https": 443}


@dataclasses.dataclass
class CollectorTarget:
    """One resolved collector destination for one campaign step."""

    step_id: str
    plugin: str
    url: str
    scheme: str
    host: str
    port: int
    path: str
    auth_header: Optional[str] = None
    auth_scheme: Optional[str] = None
    has_auth_token: bool = False
    batch: bool = False
    compress: bool = False
    request_timeout: float = 15.0
    dataset: Optional[str] = None
    allowlisted: bool = True
    warnings: list[str] = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _params_value(params: Any, name: str) -> Any:
    value = getattr(params, name, None)
    if isinstance(value, SecretStr):  # never surface the secret itself
        return None
    return value


def _host_allowlisted(host: str, allowlist: list[str]) -> bool:
    """Mirror the safety policy's host match closely enough to preflight it.

    Exact hostname match, suffix match on a leading-dot entry, or containment
    in a CIDR entry. Deliberately permissive on parse errors — the authority is
    ``SafetyPolicy.authorise`` at run time; this is an advisory check so the
    API can warn *before* a live launch is refused mid-campaign.
    """
    if not allowlist:
        return False
    for entry in allowlist:
        entry = entry.strip()
        if not entry:
            continue
        if host == entry or host.endswith("." + entry.lstrip(".")):
            return True
        try:
            network = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            continue
        try:
            if ipaddress.ip_address(host) in network:
                return True
        except ValueError:
            continue
    return False


def resolve_step_collector(
    step_id: str,
    plugin_name: str,
    params: Any,
    *,
    target_allowlist: Optional[list[str]] = None,
) -> Optional[CollectorTarget]:
    """Build a ``CollectorTarget`` from a step's validated params.

    Returns ``None`` for plugins that do not POST to an operator-supplied
    collector (c2 beacons, DNS tunnels, browser drivers …) — those emit at a
    third-party or lab target and have no ingestion door to inspect.
    """
    url = _params_value(params, "collector_url")
    if not isinstance(url, str) or not url:
        return None

    parsed = urlparse(url)
    host = parsed.hostname or ""
    scheme = parsed.scheme
    port = parsed.port or _DEFAULT_PORTS.get(scheme, 0)

    dataset = _params_value(params, "dataset")
    auth_token = getattr(params, "auth_token", None)
    target = CollectorTarget(
        step_id=step_id,
        plugin=plugin_name,
        url=url,
        scheme=scheme,
        host=host,
        port=port,
        path=parsed.path or "/",
        auth_header=_params_value(params, "auth_header"),
        auth_scheme=_params_value(params, "auth_scheme"),
        has_auth_token=auth_token is not None,
        batch=bool(_params_value(params, "batch") or False),
        compress=bool(_params_value(params, "compress") or False),
        request_timeout=float(_params_value(params, "request_timeout") or 15.0),
        dataset=dataset if isinstance(dataset, str) else None,
    )

    allowlist = list(target_allowlist or [])
    target.allowlisted = _host_allowlisted(host, allowlist)
    if not target.allowlisted:
        target.warnings.append(
            f"collector host '{host}' is not covered by the campaign "
            "target_allowlist — a live run will be refused by the safety policy"
        )
    if scheme == "http" and host not in ("localhost", "127.0.0.1", "::1"):
        target.warnings.append(
            "collector_url is plaintext http to a non-local host — log records "
            "(including any planted canaries) traverse the network in the clear"
        )
    if target.path in ("", "/"):
        target.warnings.append(
            "collector_url has no path — XSIAM's native HTTP collector expects "
            "the full ingestion path (e.g. /logs/v1/event)"
        )
    return target


def resolve_campaign_collectors(
    campaign: Campaign, registry: PluginRegistry,
) -> dict[str, Any]:
    """Resolve every collector destination a campaign will POST to.

    Steps whose plugin is unknown or whose params fail validation are reported
    with a ``params_error`` instead of being silently dropped — an operator
    reading this endpoint must see every step, not only the healthy ones.
    """
    targets: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for step in campaign.steps:
        if not registry.has(step.plugin):
            unresolved.append({
                "step_id": step.step_id,
                "plugin": step.plugin,
                "reason": "plugin_not_registered",
            })
            continue
        plugin_cls = registry.get(step.plugin)
        try:
            params = plugin_cls.validate_params(step.params)
        except Exception as exc:
            unresolved.append({
                "step_id": step.step_id,
                "plugin": step.plugin,
                "reason": "params_invalid",
                "detail": str(exc),
            })
            continue
        target = resolve_step_collector(
            step.step_id, step.plugin, params,
            target_allowlist=list(campaign.target_allowlist),
        )
        if target is None:
            unresolved.append({
                "step_id": step.step_id,
                "plugin": step.plugin,
                "reason": "no_collector",
                "detail": "plugin does not POST to an operator-supplied collector",
            })
            continue
        targets.append(target.to_dict())

    destinations = sorted({t["url"] for t in targets})
    return {
        "campaign_id": campaign.campaign_id,
        "dry_run": campaign.dry_run,
        "target_allowlist": list(campaign.target_allowlist),
        "collectors": targets,
        "destinations": destinations,
        "unresolved_steps": unresolved,
        "warnings": sorted({w for t in targets for w in t["warnings"]}),
        "blocked_by_allowlist": [
            t["step_id"] for t in targets if not t["allowlisted"]
        ],
    }


# ---------------------------------------------------------------------------
# Preflight — one canary POST, answered in the delivery taxonomy.
# ---------------------------------------------------------------------------


def canary_record(campaign_id: str, *, dataset: Optional[str] = None) -> dict[str, Any]:
    """The single record a preflight POSTs. Marked so a SOC can discard it."""
    record: dict[str, Any] = {
        "cortexsim_preflight": True,
        "cortexsim_campaign_id": campaign_id,
        "event_type": "cortexsim_collector_preflight",
        "message": (
            "CortexSim EAL collector preflight canary — safe to discard. "
            "Confirms the collector accepts the simulator's POST contract."
        ),
    }
    if dataset:
        record["dataset"] = dataset
    return record


ClientFactory = Callable[[float, bool], httpx.AsyncClient]


def _default_client(timeout: float, verify_tls: bool) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout, verify=verify_tls, follow_redirects=False,
    )


async def preflight_collector(
    target: CollectorTarget,
    *,
    campaign_id: str,
    auth_token: Optional[str] = None,
    verify_tls: bool = False,
    client_factory: Optional[ClientFactory] = None,
) -> dict[str, Any]:
    """POST one canary record and classify the answer.

    Never raises for a transport problem — the point is to *report* it in the
    taxonomy so the operator gets a code and a remediation line rather than a
    stack trace. ``client_factory`` is injectable so tests never hit the wire;
    it resolves at call time (rather than as a default argument) so a test can
    also patch ``_default_client`` on this module.
    """
    factory = client_factory or _default_client
    body = json.dumps(canary_record(campaign_id, dataset=target.dataset)).encode("utf-8")
    headers = {
        "content-type": "application/json",
        "x-simulation-source": "cortexsim-eal-simulator/1.0",
        "x-simulation-campaign-id": campaign_id,
        "x-simulation-preflight": "1",
    }
    if auth_token and target.auth_header:
        scheme = (target.auth_scheme or "").strip()
        headers[target.auth_header] = f"{scheme} {auth_token}".strip()

    started = time.monotonic()
    client = factory(target.request_timeout, verify_tls)
    try:
        resp = await client.post(target.url, headers=headers, content=body)
    except Exception as exc:  # transport-level: classify, never propagate
        code = classify_exception(exc)
        return {
            "step_id": target.step_id,
            "url": target.url,
            "reachable": False,
            "delivered": False,
            "status_code": None,
            "code": code,
            "error": f"{type(exc).__name__}: {exc}",
            "latency_ms": round((time.monotonic() - started) * 1000, 2),
            "bytes_sent": len(body),
            "remediation": REMEDIATION.get(code, ""),
        }
    finally:
        await client.aclose()

    code = classify_status(resp.status_code)
    return {
        "step_id": target.step_id,
        "url": target.url,
        "reachable": True,
        "delivered": code is None,
        "status_code": resp.status_code,
        "code": code,
        "error": None,
        "latency_ms": round((time.monotonic() - started) * 1000, 2),
        "bytes_sent": len(body),
        "remediation": REMEDIATION.get(code, "") if code else "",
    }
