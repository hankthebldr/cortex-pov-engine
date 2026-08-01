"""
email_emitter — synthetic email-security event emitter for the EMAIL plane.

Generates authentic-shape email-security audit events against an
**operator-supplied collector endpoint** (typically an HTTP log collector
that the customer has already wired into Cortex XSIAM / NG-SIEM as a
third-party Proofpoint TAP / Microsoft 365 source). The plugin never talks
to a real Proofpoint TAP or Microsoft 365 tenant — every event is a JSON
blob shaped *like* the provider's message/threat audit schema and POSTed to
the collector URL.

Why this approach: email detection looks at the raw Proofpoint TAP
(``proofpoint_tap_raw``) and Microsoft 365 (``msft_o365``) ingestion feeds.
By posting shape-true events into a collector the customer already trusts,
we exercise the same parsing + modeling + behavioural rules without touching
the real tenant or sending a real phishing message. The customer's NGFW EAL
stack also sees the outbound POST and may correlate.

Event-shape presets (parameter ``event_pattern``):

  ============================ ========================================================
  preset                       what it simulates
  ============================ ========================================================
  phishing_link                an inbound message carrying a credential-harvest URL
                                (T1566.002 / T1598) — blocked + a permitted click
  malicious_attachment         an inbound message carrying a weaponized attachment
                                (macro / ISO / HTML-smuggling) — T1566.001
  bec_impersonation            an executive-impersonation / business-email-compromise
                                lure with a display-name spoof (T1656 / T1534)
  thread_hijack                a reply-chain hijack from a compromised internal/partner
                                mailbox re-using an existing subject (T1566.002 / T1534)
  ============================ ========================================================

Each event carries ``cortexsim_run_id`` in its body and an
``X-Simulation-Run-ID`` HTTP header so SOC analysts can filter the simulator
traffic.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field, field_validator

from ..audit import ecs_event
from ..base import BaseSimulation, SimulationContext, SimulationResult
from ..delivery import REMEDIATION, DeliveryLedger


logger = logging.getLogger("cortexsim.eal.plugins.email_emitter")


# --------------------------------------------------------------------------
# Synthetic indicator constants (grounded in the EMAIL TTP cards)
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _MailIndicators:
    sender_domain: str
    sender_address: str
    sender_ip: str
    payload_url: str
    attachment_name: str
    attachment_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "sender_domain": self.sender_domain,
            "sender_address": self.sender_address,
            "sender_ip": self.sender_ip,
            "payload_url": self.payload_url,
            "attachment_name": self.attachment_name,
            "attachment_sha256": self.attachment_sha256,
        }


# One indicator bundle per pattern. All hosts use the reserved .invalid TLD so
# nothing here resolves on a real network.
_PATTERN_INDICATORS: dict[str, _MailIndicators] = {
    "phishing_link": _MailIndicators(
        sender_domain="secure-mail-update.invalid",
        sender_address="it-helpdesk@secure-mail-update.invalid",
        sender_ip="203.0.113.66",
        payload_url="https://secure-mail-update.invalid/sso/verify?id=cortexsim",
        attachment_name="",
        attachment_sha256="",
    ),
    "malicious_attachment": _MailIndicators(
        sender_domain="invoices-billing.invalid",
        sender_address="accounts@invoices-billing.invalid",
        sender_ip="198.51.100.66",
        payload_url="",
        attachment_name="Invoice_948213.iso",
        attachment_sha256="9f2a1c0b7e4d5a63c1f0e9d8b7a6f5e4d3c2b1a09f8e7d6c5b4a39281706f5e4d",
    ),
    "bec_impersonation": _MailIndicators(
        sender_domain="ceo-office-external.invalid",
        sender_address="ceo.office@ceo-office-external.invalid",
        sender_ip="192.0.2.66",
        payload_url="",
        attachment_name="",
        attachment_sha256="",
    ),
    "thread_hijack": _MailIndicators(
        sender_domain="partner-supplier.invalid",
        sender_address="ap.contact@partner-supplier.invalid",
        sender_ip="203.0.113.77",
        payload_url="https://partner-supplier.invalid/shared/po-update?id=cortexsim",
        attachment_name="",
        attachment_sha256="",
    ),
}


# --------------------------------------------------------------------------
# Provider event-shape adapters
# --------------------------------------------------------------------------


def _proofpoint_tap_event(
    *,
    message_type: str,
    classification: str,
    recipient: str,
    indicators: _MailIndicators,
    sim_run_id: str,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Shape a Proofpoint TAP SIEM-API event (subset of messagesBlocked /
    messagesDelivered / clicksPermitted -> proofpoint_tap_raw)."""
    now = datetime.now(timezone.utc).isoformat()
    threats: list[dict[str, Any]] = []
    if indicators.payload_url:
        threats.append({
            "threatType": "url",
            "classification": classification,
            "threatUrl": indicators.payload_url,
            "threatStatus": "active",
        })
    if indicators.attachment_sha256:
        threats.append({
            "threatType": "attachment",
            "classification": classification,
            "threat": indicators.attachment_name,
            "sha256": indicators.attachment_sha256,
            "threatStatus": "active",
        })
    body = {
        "dataset": "proofpoint_tap_raw",
        "messageType": message_type,
        "GUID": secrets.token_hex(12),
        "messageTime": now,
        "eventTime": now,
        "sender": indicators.sender_address,
        "fromAddress": [indicators.sender_address],
        "headerFrom": indicators.sender_address,
        "senderIP": indicators.sender_ip,
        "recipient": [recipient],
        "subject": "CortexSim canary — please review",
        "spamScore": 0,
        "phishScore": 100 if classification in ("phish", "credphish") else 0,
        "malwareScore": 100 if classification == "malware" else 0,
        "impostorScore": 100 if classification == "impostor" else 0,
        "classification": classification,
        "threatsInfoMap": threats,
        "cortexsim_run_id": sim_run_id,
    }
    if extra:
        body.update(extra)
    return body


def _m365_event(
    *,
    message_type: str,
    classification: str,
    recipient: str,
    indicators: _MailIndicators,
    sim_run_id: str,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Shape a Microsoft 365 / Defender for Office 365 EmailEvents +
    message-trace event (subset -> msft_o365)."""
    now = datetime.now(timezone.utc).isoformat()
    threat_types: list[str] = []
    if classification in ("phish", "credphish", "impostor"):
        threat_types.append("Phish")
    if classification == "malware":
        threat_types.append("Malware")
    body = {
        "dataset": "msft_o365",
        "Operation": message_type,
        "RecordType": "EmailEvents",
        "NetworkMessageId": secrets.token_hex(12),
        "Timestamp": now,
        "SenderFromAddress": indicators.sender_address,
        "SenderMailFromDomain": indicators.sender_domain,
        "SenderIPv4": indicators.sender_ip,
        "RecipientEmailAddress": recipient,
        "Subject": "CortexSim canary — please review",
        "DeliveryAction": "Blocked" if message_type == "messagesBlocked" else "Delivered",
        "ThreatTypes": ",".join(threat_types),
        "DetectionMethods": "Advanced filter",
        "UrlCount": 1 if indicators.payload_url else 0,
        "AttachmentCount": 1 if indicators.attachment_sha256 else 0,
        "Url": indicators.payload_url or None,
        "FileName": indicators.attachment_name or None,
        "SHA256": indicators.attachment_sha256 or None,
        "cortexsim_run_id": sim_run_id,
    }
    if extra:
        body.update(extra)
    return body


_PROVIDER_BUILDERS = {
    "proofpoint": _proofpoint_tap_event,
    "microsoft365": _m365_event,
}


def _list_providers() -> list[str]:
    return sorted(_PROVIDER_BUILDERS)


# --------------------------------------------------------------------------
# Event patterns — what each preset emits
# --------------------------------------------------------------------------


_EVENT_PATTERNS = (
    "phishing_link",
    "malicious_attachment",
    "bec_impersonation",
    "thread_hijack",
)


def _list_event_patterns() -> list[str]:
    return sorted(_EVENT_PATTERNS)


# Per-pattern threat classification used by the provider builders.
_PATTERN_CLASSIFICATION = {
    "phishing_link": "credphish",
    "malicious_attachment": "malware",
    "bec_impersonation": "impostor",
    "thread_hijack": "phish",
}


# --------------------------------------------------------------------------
# Pydantic params
# --------------------------------------------------------------------------


class EmailEmitterParams(BaseModel):
    collector_url: str = Field(
        ...,
        description="HTTP collector endpoint to POST synthetic email-security "
                    "events to. Typically an in-customer log forwarder already "
                    "wired into Cortex XSIAM / NG-SIEM as a Proofpoint TAP / "
                    "Microsoft 365 third-party source.",
    )
    provider: str = Field(
        default="proofpoint",
        description="Email audit-event shape: proofpoint | microsoft365.",
    )
    event_pattern: str = Field(
        default="phishing_link",
        description="Behavioural pattern: phishing_link | malicious_attachment | "
                    "bec_impersonation | thread_hijack.",
    )
    target_user: str = Field(
        default="ada.lovelace@cortexsim-canary.invalid",
        description="Recipient mailbox the synthetic message is addressed to.",
    )
    iterations: int = Field(default=1, ge=1, le=200)
    sleep_seconds: float = Field(default=0.0, ge=0.0, le=600.0)
    request_timeout: float = Field(default=15.0, ge=1.0, le=300.0)
    burst_count: int = Field(
        default=4, ge=2, le=200,
        description="Burst size for the malicious_attachment / bec_impersonation "
                    "campaign-fan-out patterns (events per iteration).",
    )
    user_agent: Optional[str] = Field(
        default=None,
        description="Override the outbound User-Agent header on POSTs to the collector.",
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

    @field_validator("provider")
    @classmethod
    def _provider_known(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in _PROVIDER_BUILDERS:
            raise ValueError(
                f"provider must be one of {sorted(_PROVIDER_BUILDERS)}, got '{v}'"
            )
        return v

    @field_validator("event_pattern")
    @classmethod
    def _pattern_known(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in _EVENT_PATTERNS:
            raise ValueError(
                f"event_pattern must be one of {sorted(_EVENT_PATTERNS)}, got '{v}'"
            )
        return v

    @field_validator("target_user")
    @classmethod
    def _target_user_canary(cls, v: str) -> str:
        if "@" not in v:
            raise ValueError("target_user must look like a mailbox (user@host)")
        return v


# --------------------------------------------------------------------------
# Plugin
# --------------------------------------------------------------------------


class EmailEmitter(BaseSimulation):
    class Meta:
        name = "email_emitter"
        version = "1.0.0"
        description = (
            "Emits synthetic email-security audit events (Proofpoint TAP / "
            "Microsoft 365 shape) into an operator-supplied collector so "
            "Cortex XSIAM / NG-SIEM exercises its phishing / BEC / "
            "impersonation / attachment detection rules without touching the "
            "real Proofpoint TAP or Microsoft 365 tenant."
        )
        mitre_techniques = ["T1566.002", "T1566.001", "T1656", "T1534", "T1598"]
        eal_targets = [
            "EMAIL — credential-harvest phishing-link detection (Proofpoint TAP)",
            "EMAIL — malicious-attachment detection (macro / ISO / HTML-smuggling)",
            "EMAIL — BEC / executive-impersonation display-name spoof detection",
            "EMAIL — thread-hijack reply-chain phishing detection",
            "EMAIL — Microsoft 365 EmailEvents parser parity",
            "NGFW EAL — outbound POST to email log-collector App-ID match",
        ]
        params_model = EmailEmitterParams

    #: Record building is a pure function here too, so this plugin can ride the
    #: offline bundle out to a jumpbox inside the customer network.
    supports_offline_bundle = True

    def plan_offline_records(
        self, params: EmailEmitterParams, *, sim_run_id: str, iteration: int,
    ) -> list[dict[str, Any]]:
        """Offline-bundle hook — the records this iteration would POST."""
        return self._build_events_for_pattern(params, sim_run_id=sim_run_id)

    async def run(self, ctx: SimulationContext) -> SimulationResult:
        params: EmailEmitterParams = ctx.params  # type: ignore[assignment]
        started_at = self.utcnow()

        host = urlparse(params.collector_url).hostname or ""
        getattr(ctx, "authorise")(host)

        if ctx.dry_run:
            await ctx.emit_event(ecs_event(
                action="email_emitter_dry_run",
                outcome="success",
                category="email",
                type_="info",
                message=(
                    f"DRY-RUN — would POST {params.iterations} {params.event_pattern} "
                    f"event burst(s) for {params.target_user} to {host}"
                ),
                campaign_id=ctx.campaign_id,
                run_id=ctx.run_id,
                step_id=ctx.step_id,
                plugin=self.Meta.name,
                target=host,
                extra={
                    "provider": params.provider,
                    "event_pattern": params.event_pattern,
                    "iterations": params.iterations,
                    "burst_count": params.burst_count,
                    "target_user": params.target_user,
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
                    "provider": params.provider,
                    "event_pattern": params.event_pattern,
                    "iterations_planned": params.iterations,
                },
            )

        # Delivery is accounted, not assumed: a POST that came back 401/404/302
        # is a refusal, not an ingested email event.
        ledger = DeliveryLedger()

        # Stash verify_tls where _build_client reads it without changing its
        # (monkeypatched-in-tests) signature.
        self._verify_tls = ctx.verify_tls
        client = self._build_client(params)
        try:
            for i in range(params.iterations):
                await self._emit_pattern(
                    client, params, ctx, iteration=i + 1, ledger=ledger,
                )
                if i < params.iterations - 1 and params.sleep_seconds > 0:
                    await asyncio.sleep(params.sleep_seconds)
        finally:
            await client.aclose()

        return SimulationResult(
            plugin=self.Meta.name,
            step_id=ctx.step_id,
            status=ledger.outcome,
            started_at=started_at,
            completed_at=self.utcnow(),
            events_emitted=ledger.records_delivered,
            bytes_sent=ledger.bytes_delivered,
            error=ledger.error_summary(),
            detail={
                "provider": params.provider,
                "event_pattern": params.event_pattern,
                "iterations_completed": params.iterations,
                "events_posted": ledger.records_delivered,
                "response_status_counts": dict(ledger.status_counts),
                "target": host,
                "target_user": params.target_user,
                "delivery": ledger.to_dict(),
            },
        )

    # ----------------------------------------------------------------------
    # Internals
    # ----------------------------------------------------------------------

    def _build_client(self, params: EmailEmitterParams) -> httpx.AsyncClient:
        headers: dict[str, str] = {"content-type": "application/json"}
        if params.user_agent:
            headers["user-agent"] = params.user_agent
        return httpx.AsyncClient(
            timeout=params.request_timeout,
            # default False (NGFW MitM friendly); opt back in per campaign via
            # the verify_tls knob, read off the plugin instance (set in run()).
            verify=getattr(self, "_verify_tls", False),
            follow_redirects=False,
            headers=headers,
        )

    def _build_events_for_pattern(
        self, params: EmailEmitterParams, *, sim_run_id: str,
    ) -> list[dict[str, Any]]:
        """Return the list of email audit-event bodies the pattern should POST."""
        builder = _PROVIDER_BUILDERS[params.provider]
        indicators = _PATTERN_INDICATORS[params.event_pattern]
        classification = _PATTERN_CLASSIFICATION[params.event_pattern]
        recipient = params.target_user

        if params.event_pattern == "phishing_link":
            return [
                builder(
                    message_type="messagesBlocked",
                    classification=classification,
                    recipient=recipient,
                    indicators=indicators,
                    sim_run_id=sim_run_id,
                    extra={"phishing_link_marker": True},
                ),
                builder(
                    message_type="clicksPermitted",
                    classification=classification,
                    recipient=recipient,
                    indicators=indicators,
                    sim_run_id=sim_run_id,
                    extra={"click_permitted_marker": True},
                ),
            ]

        if params.event_pattern == "malicious_attachment":
            evs: list[dict[str, Any]] = []
            for _ in range(params.burst_count):
                evs.append(builder(
                    message_type="messagesBlocked",
                    classification=classification,
                    recipient=recipient,
                    indicators=indicators,
                    sim_run_id=sim_run_id,
                    extra={"malicious_attachment_marker": True},
                ))
            return evs

        if params.event_pattern == "bec_impersonation":
            evs = []
            for _ in range(params.burst_count):
                evs.append(builder(
                    message_type="messagesDelivered",
                    classification=classification,
                    recipient=recipient,
                    indicators=indicators,
                    sim_run_id=sim_run_id,
                    extra={
                        "bec_marker": True,
                        "display_name_spoof": "Chief Executive Officer",
                    },
                ))
            return evs

        if params.event_pattern == "thread_hijack":
            return [
                builder(
                    message_type="messagesDelivered",
                    classification=classification,
                    recipient=recipient,
                    indicators=indicators,
                    sim_run_id=sim_run_id,
                    extra={
                        "thread_hijack_marker": True,
                        "in_reply_to_existing_subject": True,
                    },
                ),
                builder(
                    message_type="clicksPermitted",
                    classification=classification,
                    recipient=recipient,
                    indicators=indicators,
                    sim_run_id=sim_run_id,
                    extra={"thread_hijack_marker": True, "click_permitted_marker": True},
                ),
            ]

        raise ValueError(  # pragma: no cover — gated by the pydantic validator
            f"unknown event_pattern {params.event_pattern!r}"
        )

    async def _emit_pattern(
        self,
        client: httpx.AsyncClient,
        params: EmailEmitterParams,
        ctx: SimulationContext,
        *,
        iteration: int,
        ledger: DeliveryLedger,
    ) -> None:
        per_request_sim_id = f"{ctx.simulation_run_id}-i{iteration}-{secrets.token_hex(2)}"
        events = self._build_events_for_pattern(
            params, sim_run_id=per_request_sim_id,
        )

        headers = {
            **{k.lower(): v for k, v in ctx.telemetry_headers.items()},
            "x-simulation-run-id": per_request_sim_id,
            "content-type": "application/json",
        }

        host = urlparse(params.collector_url).hostname or ""

        for evt in events:
            body_bytes = json.dumps(evt).encode("utf-8")
            # Charge the campaign-level cumulative budget per event POST
            # (EAL-G06) before sending.
            ctx.charge_request()
            ctx.charge_bytes(len(body_bytes))
            try:
                resp = await client.post(
                    params.collector_url, headers=headers, content=body_bytes,
                )
                failure_code = ledger.record_response(
                    resp.status_code, records=1, wire_bytes=len(body_bytes),
                )
                delivered = failure_code is None
                await ctx.emit_event(ecs_event(
                    action="email_emitter_event",
                    outcome="success" if delivered else "failure",
                    category="email",
                    type_="info" if delivered else "error",
                    message=(
                        f"email event provider={params.provider} "
                        f"pattern={params.event_pattern} "
                        f"recipient={params.target_user} "
                        f"{'-> ' if delivered else 'REFUSED by '}{host} "
                        f"status={resp.status_code}"
                    ),
                    campaign_id=ctx.campaign_id,
                    run_id=ctx.run_id,
                    step_id=ctx.step_id,
                    plugin=self.Meta.name,
                    target=host,
                    bytes_sent=len(body_bytes),
                    extra={
                        "iteration": iteration,
                        "provider": params.provider,
                        "event_pattern": params.event_pattern,
                        "message_type": evt.get("messageType") or evt.get("Operation"),
                        "classification": evt.get("classification")
                                          or evt.get("ThreatTypes"),
                        "status_code": resp.status_code,
                        "delivered": delivered,
                        "failure_code": failure_code,
                        "remediation": REMEDIATION.get(failure_code or "", ""),
                        "simulation_request_id": per_request_sim_id,
                    },
                ))
            except httpx.HTTPError as exc:
                failure_code = ledger.record_exception(
                    exc, records=1, wire_bytes=len(body_bytes),
                )
                await ctx.emit_event(ecs_event(
                    action="email_emitter_event",
                    outcome="failure",
                    category="email",
                    type_="error",
                    message=f"email event POST failed ({failure_code}): {exc}",
                    campaign_id=ctx.campaign_id,
                    run_id=ctx.run_id,
                    step_id=ctx.step_id,
                    plugin=self.Meta.name,
                    target=host,
                    bytes_sent=len(body_bytes),
                    extra={
                        "iteration": iteration,
                        "provider": params.provider,
                        "error": str(exc),
                        "delivered": False,
                        "failure_code": failure_code,
                        "remediation": REMEDIATION.get(failure_code or "", ""),
                        "simulation_request_id": per_request_sim_id,
                    },
                ))
