"""
analytics_emitter — shared base + transport for the analytics log-streamer
EAL plugin family.

This module is the framework spine for a family of *analytics log-streamer*
plugins: emitters that POST **shape-true** JSON log records to an
operator-supplied collector (an HTTP log collector / XSIAM Broker VM HTTP
applet / Cribl-or-nginx forwarder — all of which speak the same POST-JSON
contract) so a customer can validate that their Cortex XSIAM **Analytics /
ABIOC** detections fire on the data source. It extends the proven
``idp_signin_emulator`` / ``email_emitter`` pattern with one reusable
transport driver so each per-data-source plugin only has to describe *its*
log-record shapes.

It lives *outside* ``plugins/`` on purpose so the registry never tries to
load it as a plugin, and ``AnalyticsLogEmitter`` is abstract
(``build_events`` is unimplemented) so it can never be instantiated on its
own — a per-data-source subclass supplies the record builder.

Two layers:

  * ``AnalyticsEmitterParams`` — the reused exemplar field-set (``collector_url``
    with the same scheme/hostname validator, ``iterations``, ``sleep_seconds``,
    ``request_timeout``, ``burst_count``, ``user_agent`` override) PLUS the
    net-new ingestion door: an optional bearer/HEC/Splunk ``auth_token`` (a
    ``SecretStr`` that is never logged and never leaks into any emitted event),
    a configurable ``auth_header`` / ``auth_scheme``, and two transport toggles
    (``compress`` for ``Content-Encoding: gzip`` and ``batch`` for a single
    NDJSON POST per iteration). Each data-source plugin SUBCLASSES this and adds
    only its own ``provider`` / ``event_pattern`` presets + validators.

  * ``AnalyticsLogEmitter`` — the shared driver. It owns the injectable httpx
    client, the ``ctx.authorise``-before-emit gate, the dry-run short-circuit,
    the per-event (or batched) POST loop with budget charging + per-event audit
    events, and the ``SimulationResult`` aggregation. The ONLY method a source
    plugin implements is ``build_events`` — its shape-true log-record builders.

The transport is injectable (``_build_client`` is monkeypatched by tests) so
unit tests never hit the network, exactly like the exemplars.
"""

from __future__ import annotations

import abc
import asyncio
import gzip
import json
import logging
import re
import secrets
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field, SecretStr, field_validator

from .audit import ecs_event
from .base import BaseSimulation, SimulationContext, SimulationResult
from .delivery import REMEDIATION, DeliveryLedger


logger = logging.getLogger("cortexsim.eal.analytics_emitter")


# --------------------------------------------------------------------------
# Shared params — reused exemplar field-set + the ingestion-auth / transport
# net-new door. Each data-source plugin subclasses this.
# --------------------------------------------------------------------------


class AnalyticsEmitterParams(BaseModel):
    """Base params every analytics log-streamer plugin shares.

    The ``collector_url`` field + validator, ``iterations`` / ``sleep_seconds``
    / ``request_timeout`` / ``burst_count`` bounds and the ``user_agent``
    override are copied verbatim from the ``idp_signin_emulator`` /
    ``email_emitter`` exemplars so an operator who knows one plugin knows them
    all. The auth + transport fields are the one net-new door the real XSIAM
    HTTP ingestion contract requires.
    """

    collector_url: str = Field(
        ...,
        description="HTTP collector endpoint to POST shape-true log records to. "
                    "The operator supplies the FULL URL — this fronts any of the "
                    "three real XSIAM HTTP doors (native /logs/v1/event cloud "
                    "collector, Broker VM HTTP applet localhost:8088/logs/v1/event, "
                    "or a Cribl/nginx forwarder). The plugin never invents a "
                    "tenant path.",
    )
    iterations: int = Field(default=1, ge=1, le=200)
    sleep_seconds: float = Field(default=0.0, ge=0.0, le=600.0)
    request_timeout: float = Field(default=15.0, ge=1.0, le=300.0)
    burst_count: int = Field(
        default=8, ge=2, le=200,
        description="Burst size for the fan-out event patterns (events per "
                    "iteration).",
    )
    user_agent: Optional[str] = Field(
        default=None,
        description="Override the outbound User-Agent header on POSTs to the collector.",
    )

    # -- net-new ingestion-auth door -------------------------------------
    auth_token: Optional[SecretStr] = Field(
        default=None,
        description="Optional ingestion token (HEC-style raw token, or the "
                    "credential portion of a Bearer/Splunk header). Held as a "
                    "SecretStr so it is never logged and never emitted into an "
                    "event body. Comes from the operator/vault, never hardcoded.",
    )
    auth_header: str = Field(
        default="Authorization",
        description="Header name the auth token is presented under (e.g. "
                    "Authorization for Bearer, or a custom X-... header for a "
                    "raw HEC-style token).",
    )
    auth_scheme: str = Field(
        default="Bearer",
        description="Scheme prefix prepended to the token (e.g. 'Bearer' or "
                    "'Splunk'). Empty string is allowed for collectors that want "
                    "the raw token with no scheme prefix.",
    )

    # -- net-new transport toggles ---------------------------------------
    compress: bool = Field(
        default=False,
        description="When true, gzip each POST body and set Content-Encoding: gzip.",
    )
    batch: bool = Field(
        default=False,
        description="When true, pack the iteration's records into ONE NDJSON POST "
                    "(application/x-ndjson) instead of one POST per record — cuts "
                    "POST count for high-volume streams.",
    )

    # -- net-new ingestion round-trip marker -----------------------------
    canary_token: Optional[str] = Field(
        default=None,
        description="Optional per-run ingestion-round-trip marker. When set, "
                    "every emitted record carries the token TWICE — once as the "
                    "top-level raw field 'cortexsim_canary' and once inside the "
                    "data source's OWN schema fields (see the plugin's "
                    "CANARY_FIELDS) — so a read-back query can tell 'never "
                    "ingested' apart from 'ingested but not normalized'. Leave "
                    "it None (the default) and the emitted records are "
                    "byte-identical to what they were before this field existed.",
    )

    @field_validator("collector_url")
    @classmethod
    def _collector_url_safe(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("collector_url must use http or https")
        if not parsed.hostname:
            raise ValueError("collector_url must include a hostname")
        return v

    @field_validator("canary_token")
    @classmethod
    def _canary_token_safe(cls, v: Optional[str]) -> Optional[str]:
        # The token lands inside log-record field VALUES and, downstream,
        # inside an XQL string literal in an assertion query. Restricting it to
        # lowercase alphanumerics and hyphens is what makes both safe with no
        # escaping anywhere: it survives a Kubernetes object name, an email
        # local part, a Windows sAMAccountName and an XQL literal untouched, and
        # it satisfies the assertion substrate's own context-value whitelist.
        if v is None:
            return v
        if not CANARY_TOKEN_RE.match(v):
            raise ValueError(
                "canary_token must match ^[a-z0-9][a-z0-9-]{5,63}$ — it is "
                "substituted into log-record field values and into XQL string "
                "literals, so it may not carry characters that could reshape "
                "either (e.g. 'csim-9f2a41c0d3e7')"
            )
        return v


# --------------------------------------------------------------------------
# Ingestion round-trip canary
#
# A record POSTed to a collector proves nothing until it is read back out of
# the tenant. Reading it back needs a marker, and the marker has to be planted
# in TWO places or the read-back cannot tell three very different failures
# apart:
#
#   * a top-level raw field ('cortexsim_canary'). A vendor-specific parser maps
#     the fields IT knows and routinely discards unknown top-level keys, so a
#     round-trip built only on this reads "never ingested" against precisely
#     the tenants whose parsers are working. On its own it is a bytes-arrived
#     signal, nothing more.
#
#   * the data source's OWN schema fields (each plugin's CANARY_FIELDS). This
#     is what the parser is guaranteed to map, so it is the normalization
#     signal.
#
# raw hit + native miss  -> ingested, NOT normalized  (parser / modeling-rule gap)
# raw miss + native miss -> never landed              (collector / transport gap)
# both hit               -> ingested AND normalized
#
# The duplication is not redundancy; it IS the discriminator.
# --------------------------------------------------------------------------

CANARY_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9-]{5,63}$")

#: Top-level raw marker. Deliberately distinct from ``cortexsim_run_id``, which
#: is re-minted per ITERATION — a per-iteration id cannot join records emitted
#: by three different plugins into one round-trip claim.
CANARY_RAW_FIELD = "cortexsim_canary"

#: Reserved, non-resolving domain (RFC 2606 .invalid) for the canary principal.
CANARY_DOMAIN = "cortexsim-canary.invalid"


def canary_bindings(token: str) -> dict[str, str]:
    """Template values a plugin's ``CANARY_FIELDS`` may substitute.

    ``principal`` is the load-bearing one: the SAME string in every identity
    source's own principal field is what lets a read-back query ask "does one
    human resolve across AD, Entra and Okta?" rather than "did three unrelated
    records land?".
    """
    # The token is already lowercase alphanumerics-and-hyphens, which is the
    # intersection of what a Kubernetes object name, an email local part, a
    # sAMAccountName and an XQL literal all accept — so ``account`` is the token
    # itself rather than a re-prefixed copy of it. Prefixing here is how you get
    # 'csim-csim-<token>' in a customer readout.
    return {
        "token": token,
        "account": token,
        "principal": f"{token}@{CANARY_DOMAIN}",
    }


def plant_canary(
    record: dict[str, Any], token: str, fields: dict[str, str],
) -> dict[str, Any]:
    """Plant the canary into one record, in place, and return it.

    ``fields`` maps a dotted path into the record to a template over
    :func:`canary_bindings` plus ``{value}`` (the value already at that path).

    A path that is absent, or whose current value is empty when the template
    needs ``{value}``, is SKIPPED rather than created. Creating a field the
    source's real schema does not carry would make the record less shape-true
    than it was, and the whole point of the family is shape truth.
    """
    record[CANARY_RAW_FIELD] = token
    bindings = canary_bindings(token)

    for path, template in fields.items():
        parts = path.split(".")
        node: Any = record
        for part in parts[:-1]:
            if not isinstance(node, dict) or part not in node:
                node = None
                break
            node = node[part]
        leaf = parts[-1]
        if not isinstance(node, dict) or leaf not in node:
            continue
        current = node[leaf]
        if not isinstance(current, str):
            continue
        if "{value}" in template and not current:
            continue
        node[leaf] = template.format(value=current, **bindings)

    return record


# --------------------------------------------------------------------------
# Shared driver — the abstract base every source plugin extends.
# --------------------------------------------------------------------------


class AnalyticsLogEmitter(BaseSimulation):
    """Shared transport driver for the analytics log-streamer family.

    Abstract: ``build_events`` is unimplemented, so this class can never be
    instantiated directly and the registry (which only scans ``plugins/``)
    never sees it. A per-data-source subclass supplies ``build_events`` and a
    ``Meta`` (unique name, params_model subclassing ``AnalyticsEmitterParams``,
    and MITRE techniques drawn from the analytics alerts it fires).

    Subclasses may set ``Meta.ecs_category`` (default ``"process"``) to label
    their audit events with the ECS category that best fits the data source
    (e.g. ``"configuration"`` for cloud-audit, ``"network"`` for NGFW EAL).
    """

    # ------------------------------------------------------------------
    # The ONLY method a source plugin implements.
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def build_events(
        self, params: AnalyticsEmitterParams, *, sim_run_id: str, iteration: int,
    ) -> list[dict[str, Any]]:
        """Return the shape-true log records this iteration should POST.

        Each record MUST carry ``cortexsim_run_id`` (set to ``sim_run_id``) in
        its body and a top-level ``dataset`` hint (the data source's real XSIAM
        dataset, e.g. ``cloud_audit_logs``) — exactly like ``email_emitter``.
        """

    #: Dotted path in this source's OWN record shape -> template over
    #: :func:`canary_bindings` (plus ``{value}``). Empty by default, so a plugin
    #: that does not declare it emits the raw marker only and is honest about
    #: carrying no normalization signal. See the canary block above for why the
    #: native-field plant is what makes the round-trip diagnostic.
    CANARY_FIELDS: dict[str, str] = {}

    #: Every analytics log-streamer is offline-bundleable: ``build_events`` is
    #: a pure record builder with no network, so SimCore can pre-render a
    #: campaign's records and hand the DC a self-contained bundle that runs
    #: from inside the customer network — where the collector actually lives.
    supports_offline_bundle = True

    def records_for(
        self, params: AnalyticsEmitterParams, *, sim_run_id: str, iteration: int,
    ) -> list[dict[str, Any]]:
        """``build_events`` plus the optional canary plant.

        The single seam every record passes through, so the wire path and the
        offline-bundle path cannot drift on whether the marker is present.
        With ``params.canary_token`` unset this returns ``build_events``'s
        output unchanged, which is why adding the marker did not change any
        existing emitter's output shape.
        """
        events = self.build_events(
            params, sim_run_id=sim_run_id, iteration=iteration,
        )
        token = getattr(params, "canary_token", None)
        if not token:
            return events
        return [plant_canary(e, token, self.CANARY_FIELDS) for e in events]

    def plan_offline_records(
        self, params: AnalyticsEmitterParams, *, sim_run_id: str, iteration: int,
    ) -> list[dict[str, Any]]:
        """Offline-bundle hook — the records this iteration would POST."""
        return self.records_for(
            params, sim_run_id=sim_run_id, iteration=iteration,
        )

    # ------------------------------------------------------------------
    # Shared run — identical shape to the exemplars, no per-source logic.
    # ------------------------------------------------------------------

    async def run(self, ctx: SimulationContext) -> SimulationResult:
        params: AnalyticsEmitterParams = ctx.params  # type: ignore[assignment]
        started_at = self.utcnow()

        host = urlparse(params.collector_url).hostname or ""
        # Mandatory per-target gate BEFORE any emit.
        ctx.authorise(host)

        descriptor = self._descriptor(params)

        if ctx.dry_run:
            await ctx.emit_event(ecs_event(
                action=f"{self.Meta.name}_dry_run",
                outcome="success",
                category=self._ecs_category(),
                type_="info",
                message=(
                    f"DRY-RUN — would POST {params.iterations} iteration(s) "
                    f"[{self._descriptor_str(descriptor)}] to {host}"
                ),
                campaign_id=ctx.campaign_id,
                run_id=ctx.run_id,
                step_id=ctx.step_id,
                plugin=self.Meta.name,
                target=host,
                extra={
                    **descriptor,
                    "iterations": params.iterations,
                    "burst_count": params.burst_count,
                    "batch": params.batch,
                    "compress": params.compress,
                },
            ))
            return SimulationResult(
                plugin=self.Meta.name,
                step_id=ctx.step_id,
                status="success",
                started_at=started_at,
                completed_at=self.utcnow(),
                events_emitted=1,
                bytes_sent=0,
                detail={
                    "dry_run": True,
                    **descriptor,
                    "iterations_planned": params.iterations,
                },
            )

        # One ledger per step: it is the single source of truth for what the
        # collector actually accepted, and it — not "the POST didn't raise" —
        # decides the step's status.
        ledger = DeliveryLedger()

        # Stash verify_tls where _build_client reads it without changing its
        # (monkeypatched-in-tests) signature.
        self._verify_tls = ctx.verify_tls
        client = self._build_client(params)
        try:
            for i in range(params.iterations):
                per_request_sim_id = (
                    f"{ctx.simulation_run_id}-i{i + 1}-{secrets.token_hex(2)}"
                )
                events = self.records_for(
                    params, sim_run_id=per_request_sim_id, iteration=i + 1,
                )
                await self._emit_events(
                    client, params, ctx, events,
                    per_request_sim_id=per_request_sim_id,
                    host=host, iteration=i + 1, ledger=ledger,
                )
                if i < params.iterations - 1 and params.sleep_seconds > 0:
                    await asyncio.sleep(params.sleep_seconds)
        finally:
            await client.aclose()

        delivery = ledger.to_dict()
        return SimulationResult(
            plugin=self.Meta.name,
            step_id=ctx.step_id,
            status=ledger.outcome,
            started_at=started_at,
            completed_at=self.utcnow(),
            # events_emitted / bytes_sent report what the collector ACCEPTED.
            # bytes attempted are still visible under detail.delivery so an
            # operator can see the gap between "sent" and "ingested".
            events_emitted=ledger.records_delivered,
            bytes_sent=ledger.bytes_delivered,
            error=ledger.error_summary(),
            detail={
                **descriptor,
                "iterations_completed": params.iterations,
                "events_posted": ledger.records_delivered,
                "response_status_counts": dict(ledger.status_counts),
                "target": host,
                "batch": params.batch,
                "compress": params.compress,
                "delivery": delivery,
            },
        )

    # ------------------------------------------------------------------
    # Internals — injectable transport.
    # ------------------------------------------------------------------

    def _ecs_category(self) -> str:
        return getattr(self.Meta, "ecs_category", "process")

    def _descriptor(self, params: AnalyticsEmitterParams) -> dict[str, Any]:
        """Non-secret, source-describing fields for audit/detail lines.

        Reads ``provider`` / ``event_pattern`` / ``dataset`` off the subclass's
        params model when present. NEVER touches ``auth_token``.
        """
        out: dict[str, Any] = {}
        for attr in ("provider", "event_pattern", "dataset"):
            val = getattr(params, attr, None)
            if val is not None:
                out[attr] = val
        return out

    @staticmethod
    def _descriptor_str(descriptor: dict[str, Any]) -> str:
        return ", ".join(f"{k}={v}" for k, v in descriptor.items()) or "no-descriptor"

    def _build_client(self, params: AnalyticsEmitterParams) -> httpx.AsyncClient:
        """Build the injectable httpx client (monkeypatched to a stub in tests).

        The auth token — when present — is set as a client-level header so it is
        applied to every POST without threading the secret through per-request
        header dicts (which are what land in emitted audit events). The token
        value never appears in any ``ecs_event``.
        """
        headers: dict[str, str] = {
            "content-type": "application/x-ndjson" if params.batch else "application/json",
        }
        if params.user_agent:
            headers["user-agent"] = params.user_agent
        if params.auth_token is not None:
            token = params.auth_token.get_secret_value()
            # Empty scheme -> raw token (HEC-style); .strip() drops the leading
            # space so 'Bearer <t>' and a bare '<t>' both come out clean.
            headers[params.auth_header] = f"{params.auth_scheme} {token}".strip()
        return httpx.AsyncClient(
            timeout=params.request_timeout,
            # default False (NGFW MitM friendly); opt back in per campaign via
            # the verify_tls knob, read off the plugin instance (set in run()).
            verify=getattr(self, "_verify_tls", False),
            follow_redirects=False,
            headers=headers,
        )

    def _encode(
        self, body: bytes, params: AnalyticsEmitterParams,
        base_headers: dict[str, str], content_type: str,
    ) -> tuple[bytes, dict[str, str]]:
        """Return (wire_bytes, per-request headers), applying optional gzip."""
        hdrs = dict(base_headers)
        hdrs["content-type"] = content_type
        if params.compress:
            body = gzip.compress(body)
            hdrs["content-encoding"] = "gzip"
        return body, hdrs

    async def _emit_events(
        self,
        client: httpx.AsyncClient,
        params: AnalyticsEmitterParams,
        ctx: SimulationContext,
        events: list[dict[str, Any]],
        *,
        per_request_sim_id: str,
        host: str,
        iteration: int,
        ledger: DeliveryLedger,
    ) -> None:
        """POST the iteration's records, accounting every send in ``ledger``.

        In batch mode the whole list becomes ONE NDJSON POST (one combined byte
        charge, one status count); otherwise one POST per record. Either way an
        ``httpx.HTTPError`` is swallowed per-POST with a failure audit event so
        one bad send never aborts the iteration — but a non-2xx response is now
        a *failure*, not a delivery, and the ledger says so.
        """
        base_headers = {
            **{k.lower(): v for k, v in ctx.telemetry_headers.items()},
            "x-simulation-run-id": per_request_sim_id,
        }
        descriptor = self._descriptor(params)

        if params.batch:
            body = "\n".join(json.dumps(e) for e in events).encode("utf-8")
            wire, hdrs = self._encode(
                body, params, base_headers, "application/x-ndjson",
            )
            # Charge the campaign budget on the bytes about to go on the wire
            # BEFORE the send (one combined charge for the batch).
            ctx.charge_request()
            ctx.charge_bytes(len(wire))
            try:
                resp = await client.post(
                    params.collector_url, headers=hdrs, content=wire,
                )
                code = ledger.record_response(
                    resp.status_code, records=len(events), wire_bytes=len(wire),
                )
                await ctx.emit_event(self._delivery_event(
                    ctx, params, host=host, iteration=iteration,
                    per_request_sim_id=per_request_sim_id,
                    status_code=resp.status_code, wire_bytes=len(wire),
                    record_count=len(events), batched=True, descriptor=descriptor,
                    failure_code=code,
                ))
            except httpx.HTTPError as exc:
                code = ledger.record_exception(
                    exc, records=len(events), wire_bytes=len(wire),
                )
                await ctx.emit_event(self._failure_event(
                    ctx, params, host=host, iteration=iteration,
                    per_request_sim_id=per_request_sim_id, wire_bytes=len(wire),
                    error=str(exc), descriptor=descriptor, failure_code=code,
                    record_count=len(events),
                ))
            return

        for evt in events:
            body = json.dumps(evt).encode("utf-8")
            wire, hdrs = self._encode(body, params, base_headers, "application/json")
            ctx.charge_request()
            ctx.charge_bytes(len(wire))
            try:
                resp = await client.post(
                    params.collector_url, headers=hdrs, content=wire,
                )
                code = ledger.record_response(
                    resp.status_code, records=1, wire_bytes=len(wire),
                )
                await ctx.emit_event(self._delivery_event(
                    ctx, params, host=host, iteration=iteration,
                    per_request_sim_id=per_request_sim_id,
                    status_code=resp.status_code, wire_bytes=len(wire),
                    record_count=1, batched=False, descriptor=descriptor,
                    event=evt, failure_code=code,
                ))
            except httpx.HTTPError as exc:
                code = ledger.record_exception(exc, records=1, wire_bytes=len(wire))
                await ctx.emit_event(self._failure_event(
                    ctx, params, host=host, iteration=iteration,
                    per_request_sim_id=per_request_sim_id, wire_bytes=len(wire),
                    error=str(exc), descriptor=descriptor, failure_code=code,
                    record_count=1,
                ))

    # ------------------------------------------------------------------
    # Audit event builders (keep the emit loop readable).
    # ------------------------------------------------------------------

    def _delivery_event(
        self, ctx: SimulationContext, params: AnalyticsEmitterParams, *,
        host: str, iteration: int, per_request_sim_id: str, status_code: int,
        wire_bytes: int, record_count: int, batched: bool,
        descriptor: dict[str, Any], failure_code: Optional[str] = None,
        event: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Audit line for a POST that got an HTTP response.

        ``failure_code`` is the taxonomy verdict: ``None`` means the collector
        accepted the records. A rejected POST is logged as an ECS *failure* —
        previously any answered POST logged success, which is how a campaign
        against a 401-ing collector looked green in the audit stream.
        """
        extra = {
            **descriptor,
            "iteration": iteration,
            "status_code": status_code,
            "record_count": record_count,
            "batched": batched,
            "compressed": params.compress,
            "simulation_request_id": per_request_sim_id,
            "delivered": failure_code is None,
        }
        if failure_code is not None:
            extra["failure_code"] = failure_code
            extra["remediation"] = REMEDIATION.get(failure_code, "")
        if event is not None:
            # Surface the record's own action verb where the source provides one.
            extra["event_name"] = (
                event.get("eventName")
                or event.get("protoPayload", {}).get("methodName")
                or event.get("action")
            )
        verb = "accepted by" if failure_code is None else f"REFUSED ({failure_code}) by"
        return ecs_event(
            action=f"{self.Meta.name}_event",
            outcome="success" if failure_code is None else "failure",
            category=self._ecs_category(),
            type_="info" if failure_code is None else "error",
            message=(
                f"analytics log event [{self._descriptor_str(descriptor)}] "
                f"{verb} {host} status={status_code} records={record_count}"
                f"{' (batch)' if batched else ''}"
            ),
            campaign_id=ctx.campaign_id,
            run_id=ctx.run_id,
            step_id=ctx.step_id,
            plugin=self.Meta.name,
            target=host,
            bytes_sent=wire_bytes,
            extra=extra,
        )

    def _failure_event(
        self, ctx: SimulationContext, params: AnalyticsEmitterParams, *,
        host: str, iteration: int, per_request_sim_id: str, wire_bytes: int,
        error: str, descriptor: dict[str, Any],
        failure_code: Optional[str] = None, record_count: int = 1,
    ) -> dict[str, Any]:
        return ecs_event(
            action=f"{self.Meta.name}_event",
            outcome="failure",
            category=self._ecs_category(),
            type_="error",
            message=f"analytics log event POST failed ({failure_code}): {error}",
            campaign_id=ctx.campaign_id,
            run_id=ctx.run_id,
            step_id=ctx.step_id,
            plugin=self.Meta.name,
            target=host,
            bytes_sent=wire_bytes,
            extra={
                **descriptor,
                "iteration": iteration,
                "error": error,
                "failure_code": failure_code,
                "remediation": REMEDIATION.get(failure_code or "", ""),
                "record_count": record_count,
                "delivered": False,
                "simulation_request_id": per_request_sim_id,
            },
        )
