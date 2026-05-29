"""
ssh_egress — outbound SSH protocol simulation.

Opens a real TCP connection to an authorised SSH sinkhole, exchanges
the SSH-2.0 banner string (RFC-4253 §4.2) and emits a synthesised KEX
init pseudo-frame so the NGFW App-ID engine fingerprints the session
as SSH-Outbound. The plugin deliberately stops before any actual key
exchange so no cryptographic state is established — the goal is
*shape*, not *substance*.

Triggers Cortex EALs covering:

  * SSH App-ID match on an unusual egress path (workstation → public 22)
  * Outbound SSH from a host class that normally only accepts inbound
  * Non-standard SSH client banner (when ``client_banner`` is customised
    to a known-bad string like ``SSH-2.0-OpenSSH_for_Windows_8.1``)
  * Repeated short-lived SSH handshakes — anomalous SSH-scanning behaviour

Safety
------

* Target host + port must appear in the campaign's target_allowlist.
* No password / key material ever leaves the simulator — the session
  closes before auth begins.
* Banner string scrubbed for CRLF / NUL so an operator can't smuggle a
  second protocol command through the parameter.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from ..base import BaseSimulation, SimulationContext, SimulationResult
from ..audit import ecs_event


logger = logging.getLogger("cortexsim.eal.plugins.ssh_egress")


_DEFAULT_BANNER = "SSH-2.0-CortexSim_eal_1.0"
_COMMON_SSH_PORTS = {22, 2222, 22222}

# SSH_MSG_KEXINIT identifier (RFC-4253 §7.1).
_SSH_MSG_KEXINIT = 20

# A minimal but well-formed list of algorithm names that any SSH server
# will accept as an opening offer. The simulator never receives or
# processes the server's KEXINIT reply — it just sends one so the NGFW
# sees a real SSH packet on the wire.
_DEFAULT_KEX_OFFER = {
    "kex_algorithms": [
        "curve25519-sha256",
        "diffie-hellman-group14-sha256",
    ],
    "server_host_key_algorithms": [
        "ssh-ed25519",
        "rsa-sha2-256",
    ],
    "encryption_algorithms_client_to_server": [
        "chacha20-poly1305@openssh.com",
        "aes256-gcm@openssh.com",
    ],
    "encryption_algorithms_server_to_client": [
        "chacha20-poly1305@openssh.com",
        "aes256-gcm@openssh.com",
    ],
    "mac_algorithms_client_to_server": ["hmac-sha2-256"],
    "mac_algorithms_server_to_client": ["hmac-sha2-256"],
    "compression_algorithms_client_to_server": ["none"],
    "compression_algorithms_server_to_client": ["none"],
    "languages_client_to_server": [""],
    "languages_server_to_client": [""],
}


def _clean_banner(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("banner required")
    if not v.startswith("SSH-2.0-") and not v.startswith("SSH-1.99-"):
        raise ValueError("client_banner must start with 'SSH-2.0-' or 'SSH-1.99-'")
    if len(v) > 253:  # RFC-4253 §4.2 — max line length before CRLF
        raise ValueError("client_banner too long for an SSH ID string")
    for ch in v:
        if ch in {"\r", "\n", "\x00"} or ord(ch) < 0x20:
            raise ValueError("client_banner contains control character")
    return v


class SshEgressParams(BaseModel):
    target_host: str = Field(..., description="Authorised SSH sinkhole host.")
    target_port: int = Field(default=22, ge=1, le=65535)
    client_banner: str = Field(default=_DEFAULT_BANNER,
                               description="SSH-2.0-<id> string sent on the wire.")
    iterations: int = Field(default=3, ge=1, le=200)
    sleep_seconds: float = Field(default=15.0, ge=0.0, le=600.0)
    connect_timeout: float = Field(default=5.0, ge=0.5, le=30.0)
    idle_seconds: float = Field(default=2.0, ge=0.0, le=60.0)
    send_kexinit: bool = Field(default=True,
                               description="If true, also send a synthesised SSH_MSG_KEXINIT packet.")

    @field_validator("target_host")
    @classmethod
    def _host_required(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("target_host required")
        return v

    @field_validator("client_banner")
    @classmethod
    def _banner_clean(cls, v: str) -> str:
        return _clean_banner(v)


class SshEgress(BaseSimulation):
    class Meta:
        name = "ssh_egress"
        version = "1.0.0"
        description = (
            "Opens a real TCP connection to an authorised SSH sinkhole, sends "
            "an SSH-2.0 client banner and (optionally) a SSH_MSG_KEXINIT "
            "packet so NGFW App-ID fingerprints the session as SSH egress."
        )
        mitre_techniques = ["T1021.004", "T1572"]
        eal_targets = [
            "SSH App-ID Outbound",
            "Atypical SSH Client Banner",
            "Outbound SSH from Non-Admin Endpoint",
        ]
        params_model = SshEgressParams

    async def run(self, ctx: SimulationContext) -> SimulationResult:
        params: SshEgressParams = ctx.params  # type: ignore[assignment]
        started_at = self.utcnow()

        getattr(ctx, "authorise")(params.target_host)

        if ctx.dry_run:
            await ctx.emit_event(ecs_event(
                action="ssh_dry_run",
                outcome="success",
                category="network",
                type_="info",
                message=(
                    f"DRY-RUN — {params.iterations} planned SSH banner exchanges "
                    f"to {params.target_host}:{params.target_port}"
                ),
                campaign_id=ctx.campaign_id,
                run_id=ctx.run_id,
                step_id=ctx.step_id,
                plugin=self.Meta.name,
                target=f"{params.target_host}:{params.target_port}",
                extra={
                    "well_known_port": params.target_port in _COMMON_SSH_PORTS,
                    "kexinit": params.send_kexinit,
                    "banner": params.client_banner,
                },
            ))
            return SimulationResult(
                plugin=self.Meta.name,
                step_id=ctx.step_id,
                status="success",
                started_at=started_at,
                completed_at=self.utcnow(),
                events_emitted=1,
                detail={"dry_run": True, "iterations_planned": params.iterations},
            )

        events_emitted = 0
        bytes_sent = 0

        for i in range(params.iterations):
            try:
                sent = await asyncio.to_thread(
                    self._open_session,
                    params.target_host,
                    params.target_port,
                    params.client_banner,
                    params.send_kexinit,
                    params.connect_timeout,
                    params.idle_seconds,
                )
                bytes_sent += sent
                events_emitted += 1
                outcome = "success"
                detail_extra = {"bytes_sent_this_session": sent}
            except OSError as exc:
                outcome = "failure"
                detail_extra = {"error": str(exc)}

            await ctx.emit_event(ecs_event(
                action="ssh_session",
                outcome=outcome,
                category="network",
                type_="connection",
                message=(
                    f"SSH session {i + 1}/{params.iterations} -> "
                    f"{params.target_host}:{params.target_port}"
                ),
                campaign_id=ctx.campaign_id,
                run_id=ctx.run_id,
                step_id=ctx.step_id,
                plugin=self.Meta.name,
                target=f"{params.target_host}:{params.target_port}",
                bytes_sent=bytes_sent,
                extra={
                    "iteration": i + 1,
                    "client_banner": params.client_banner,
                    "kexinit": params.send_kexinit,
                    **detail_extra,
                },
            ))

            if i < params.iterations - 1 and params.sleep_seconds > 0:
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
                "sessions_completed": events_emitted,
                "target": f"{params.target_host}:{params.target_port}",
                "client_banner": params.client_banner,
            },
        )

    # ------------------------------------------------------------------
    # Wire protocol — RFC-4253 banner + optional SSH_MSG_KEXINIT
    # ------------------------------------------------------------------

    @staticmethod
    def _open_session(
        host: str,
        port: int,
        client_banner: str,
        send_kexinit: bool,
        connect_timeout: float,
        idle_seconds: float,
    ) -> int:
        bytes_sent = 0
        with socket.create_connection((host, port), timeout=connect_timeout) as sock:
            sock.settimeout(connect_timeout)

            # Some sinkholes emit the server banner first; drain it.
            try:
                sock.recv(1024)
            except (socket.timeout, OSError):
                pass

            # 1. Client banner (RFC-4253 §4.2 — terminated by CRLF).
            banner = (client_banner + "\r\n").encode("ascii", errors="strict")
            sock.sendall(banner)
            bytes_sent += len(banner)

            # 2. Optional KEXINIT pseudo-frame.
            if send_kexinit:
                packet = SshEgress._build_kexinit_packet()
                sock.sendall(packet)
                bytes_sent += len(packet)

            # 3. Stay connected briefly so the App-ID engine fingerprints.
            if idle_seconds > 0:
                try:
                    sock.settimeout(idle_seconds)
                    sock.recv(4096)
                except (socket.timeout, OSError):
                    pass

        return bytes_sent

    @staticmethod
    def _build_kexinit_packet() -> bytes:
        """Build a minimal but parser-valid SSH_MSG_KEXINIT packet.

        Layout per RFC-4253 §6 + §7.1:
            uint32 packet_length
            byte   padding_length
            byte   SSH_MSG_KEXINIT
            byte[16] cookie
            name-list kex_algorithms
            name-list server_host_key_algorithms
            name-list encryption_algorithms_client_to_server
            name-list encryption_algorithms_server_to_client
            name-list mac_algorithms_client_to_server
            name-list mac_algorithms_server_to_client
            name-list compression_algorithms_client_to_server
            name-list compression_algorithms_server_to_client
            name-list languages_client_to_server
            name-list languages_server_to_client
            boolean first_kex_packet_follows
            uint32 reserved
            byte[padding_length] padding
        """
        cookie = os.urandom(16)
        payload = bytes([_SSH_MSG_KEXINIT]) + cookie

        # Append each algorithm name-list (uint32 length-prefixed UTF-8).
        for key in (
            "kex_algorithms",
            "server_host_key_algorithms",
            "encryption_algorithms_client_to_server",
            "encryption_algorithms_server_to_client",
            "mac_algorithms_client_to_server",
            "mac_algorithms_server_to_client",
            "compression_algorithms_client_to_server",
            "compression_algorithms_server_to_client",
            "languages_client_to_server",
            "languages_server_to_client",
        ):
            joined = ",".join(_DEFAULT_KEX_OFFER[key]).encode("ascii")
            payload += len(joined).to_bytes(4, "big") + joined

        # first_kex_packet_follows + reserved uint32.
        payload += b"\x00" + (0).to_bytes(4, "big")

        # SSH binary packet protocol — packet_length covers (padding_length
        # + payload + padding). Padding aligns to 8-byte block, min 4 bytes.
        block = 8
        # +1 for the padding_length byte itself.
        padding_len = block - ((len(payload) + 5) % block)
        if padding_len < 4:
            padding_len += block

        packet_body = bytes([padding_len]) + payload + os.urandom(padding_len)
        packet_length = len(packet_body)
        return packet_length.to_bytes(4, "big") + packet_body
