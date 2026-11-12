"""
rdp_egress — outbound RDP protocol simulation (traffic-level EAL).

Opens a **real TCP connection** to an authorised RDP sinkhole and sends the
RDP **X.224 Connection Request** PDU (TPKT header → COTP CR → the
``Cookie: mstshash=<user>`` token → RDP Negotiation Request), so the NGFW
App-ID engine fingerprints the session as **ms-rdp** outbound. The plugin
deliberately stops before the TLS/CredSSP security handshake — no credential
or cryptographic state is ever established. The goal is *shape*, not *substance*:
put a genuine RDP connection request on the wire so the Cortex NGFW traffic /
NDR analytics fire, exactly as ``ssh_egress`` / ``ftp_egress`` do for SSH/FTP.

Triggers Cortex EALs / NGFW-traffic analytics covering:

  * ms-rdp App-ID match on an unusual egress path (workstation → 3389)
  * Outbound/lateral RDP from a host class that normally only accepts inbound
  * Anomalous RDP to an internal host (lateral movement, T1021.001)
  * The ``mstshash`` cookie username carried in the X.224 CR (who is pivoting)

Safety
------
* Target host + port must appear in the campaign's target_allowlist.
* No password / NLA / CredSSP material ever leaves the simulator — the session
  closes right after the X.224 Connection Request, before any security exchange.
* The ``mstshash`` cookie value is scrubbed for CRLF / NUL so an operator can't
  smuggle a second protocol token through the parameter.
"""
from __future__ import annotations

import asyncio
import logging
import socket
import struct
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from ..base import BaseSimulation, SimulationContext, SimulationResult
from ..audit import ecs_event


logger = logging.getLogger("cortexsim.eal.plugins.rdp_egress")

_COMMON_RDP_PORTS = {3389, 3388, 33890}

# RDP Negotiation Request — requested security protocols (MS-RDPBCGR 2.2.1.1.1).
_PROTOCOL_RDP = 0x00000000       # standard RDP security
_PROTOCOL_SSL = 0x00000001       # TLS
_PROTOCOL_HYBRID = 0x00000002    # CredSSP (NLA)


def _clean_cookie(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("mstshash cookie (username) required")
    if len(v) > 128:
        raise ValueError("mstshash cookie too long")
    for ch in v:
        if ch in {"\r", "\n", "\x00"} or ord(ch) < 0x20:
            raise ValueError("mstshash cookie contains a control character")
    return v


class RdpEgressParams(BaseModel):
    target_host: str = Field(..., description="Authorised RDP sinkhole / lab host.")
    target_port: int = Field(default=3389, ge=1, le=65535)
    mstshash_cookie: str = Field(default="svc-rdp",
                                 description="Username carried in the X.224 'Cookie: mstshash=' token.")
    requested_protocols: int = Field(default=_PROTOCOL_HYBRID, ge=0, le=0x0F,
                                     description="RDP Negotiation Request requestedProtocols flags.")
    iterations: int = Field(default=3, ge=1, le=200)
    sleep_seconds: float = Field(default=15.0, ge=0.0, le=600.0)
    connect_timeout: float = Field(default=5.0, ge=0.5, le=30.0)
    idle_seconds: float = Field(default=1.0, ge=0.0, le=60.0)
    send_negotiation: bool = Field(default=True,
                                   description="If true, include the RDP Negotiation Request after the cookie.")

    @field_validator("target_host")
    @classmethod
    def _host_required(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("target_host required")
        return v

    @field_validator("mstshash_cookie")
    @classmethod
    def _cookie_clean(cls, v: str) -> str:
        return _clean_cookie(v)


class RdpEgress(BaseSimulation):
    class Meta:
        name = "rdp_egress"
        version = "1.0.0"
        description = (
            "Opens a real TCP connection to an authorised RDP sinkhole and sends "
            "the RDP X.224 Connection Request (mstshash cookie + Negotiation "
            "Request) so NGFW App-ID fingerprints the session as ms-rdp egress."
        )
        mitre_techniques = ["T1021.001"]
        eal_targets = [
            "ms-rdp App-ID Outbound",
            "Outbound/Lateral RDP from Non-Admin Endpoint",
            "Anomalous RDP to an internal host",
        ]
        params_model = RdpEgressParams

    async def run(self, ctx: SimulationContext) -> SimulationResult:
        params: RdpEgressParams = ctx.params  # type: ignore[assignment]
        started_at = self.utcnow()
        ctx.authorise(params.target_host, port=params.target_port)

        if ctx.dry_run:
            await ctx.emit_event(ecs_event(
                action="rdp_dry_run", outcome="success", category="network", type_="info",
                message=(f"DRY-RUN — {params.iterations} planned RDP X.224 connection requests "
                         f"to {params.target_host}:{params.target_port}"),
                campaign_id=ctx.campaign_id, run_id=ctx.run_id, step_id=ctx.step_id,
                plugin=self.Meta.name, target=f"{params.target_host}:{params.target_port}",
                extra={"well_known_port": params.target_port in _COMMON_RDP_PORTS,
                       "mstshash": params.mstshash_cookie},
            ))
            return SimulationResult(
                plugin=self.Meta.name, step_id=ctx.step_id, status="success",
                started_at=started_at, completed_at=self.utcnow(), events_emitted=1,
                detail={"dry_run": True, "iterations_planned": params.iterations},
            )

        events_emitted = 0
        bytes_sent = 0
        for i in range(params.iterations):
            try:
                sent = await asyncio.to_thread(
                    self._open_session, params.target_host, params.target_port,
                    params.mstshash_cookie, params.requested_protocols,
                    params.send_negotiation, params.connect_timeout, params.idle_seconds,
                )
                bytes_sent += sent
                events_emitted += 1
                ctx.charge_request()
                ctx.charge_bytes(sent)
                outcome = "success"
                detail_extra = {"bytes_sent_this_session": sent}
            except OSError as exc:
                outcome = "failure"
                detail_extra = {"error": str(exc)}

            await ctx.emit_event(ecs_event(
                action="rdp_session", outcome=outcome, category="network", type_="connection",
                message=(f"RDP X.224 CR {i + 1}/{params.iterations} -> "
                         f"{params.target_host}:{params.target_port}"),
                campaign_id=ctx.campaign_id, run_id=ctx.run_id, step_id=ctx.step_id,
                plugin=self.Meta.name, target=f"{params.target_host}:{params.target_port}",
                bytes_sent=bytes_sent,
                extra={"iteration": i + 1, "mstshash": params.mstshash_cookie,
                       "requested_protocols": params.requested_protocols, **detail_extra},
            ))
            if i < params.iterations - 1 and params.sleep_seconds > 0:
                await asyncio.sleep(params.sleep_seconds)

        return SimulationResult(
            plugin=self.Meta.name, step_id=ctx.step_id, status="success",
            started_at=started_at, completed_at=self.utcnow(),
            events_emitted=events_emitted, bytes_sent=bytes_sent,
            detail={"sessions_completed": events_emitted,
                    "target": f"{params.target_host}:{params.target_port}",
                    "mstshash": params.mstshash_cookie},
        )

    # ------------------------------------------------------------------
    # Wire protocol — TPKT + X.224 Connection Request (MS-RDPBCGR 2.2.1.1)
    # ------------------------------------------------------------------
    @staticmethod
    def _x224_connection_request(cookie_user: str, protocols: int, negotiation: bool) -> bytes:
        cookie = f"Cookie: mstshash={cookie_user}\r\n".encode("ascii", "replace")
        nego = b""
        if negotiation:
            # RDP Negotiation Request: type=0x01, flags=0x00, length=8 (LE), requestedProtocols (LE)
            nego = struct.pack("<BBHI", 0x01, 0x00, 0x0008, protocols)
        # COTP Connection Request: LI, CR(0xE0), dst-ref(0), src-ref(0), class(0)
        cotp_payload = struct.pack(">BHHB", 0xE0, 0x0000, 0x0000, 0x00) + cookie + nego
        cotp = struct.pack(">B", len(cotp_payload)) + cotp_payload
        # TPKT header: version(3), reserved(0), total length (BE)
        total = 4 + len(cotp)
        tpkt = struct.pack(">BBH", 0x03, 0x00, total)
        return tpkt + cotp

    @classmethod
    def _open_session(cls, host: str, port: int, cookie_user: str, protocols: int,
                      negotiation: bool, connect_timeout: float, idle_seconds: float) -> int:
        pdu = cls._x224_connection_request(cookie_user, protocols, negotiation)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(connect_timeout)
        try:
            s.connect((host, port))
            s.sendall(pdu)
            if idle_seconds:
                try:
                    s.settimeout(idle_seconds)
                    s.recv(256)  # read the server's X.224 CC (if any); ignore contents
                except OSError:
                    pass
            return len(pdu)
        finally:
            try:
                s.close()
            except OSError:
                pass
