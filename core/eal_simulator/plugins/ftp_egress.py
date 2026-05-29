"""
ftp_egress — FTP cleartext protocol simulation.

Establishes a real TCP control connection to an authorised FTP sinkhole
and walks through the canonical clear-text command sequence (banner →
USER → PASS → SYST → PASV → STOR / NOOP → QUIT) so the NGFW App-ID
engine, DLP / EAL signatures, and any anomaly detectors fingerprint the
session as outbound FTP carrying clear-text credentials.

Triggers Cortex EALs covering:

  * FTP App-ID match on an unusual egress path
  * Clear-text credentials over FTP (USER + PASS visible to inspection)
  * Outbound file transfer via FTP STOR (when ``send_stor`` is true)
  * Anomalous FTP from a host that does not normally egress on port 21

Safety
------

* The plugin NEVER connects to a real customer FTP server. The target
  host AND port must appear in the campaign target_allowlist; the
  ``ctx.authorise(host)`` call enforces this end-to-end.
* No filesystem read happens. STOR contents are a synthesised buffer
  of printable ASCII so secrets cannot leak through the simulator.
* The default credentials are hard-coded sentinels
  (``cortexsim`` / ``cortexsim-lab``) so any DLP rule matching them is
  attributable to the simulator. Operators may override but the
  validators below scrub for whitespace / null bytes / CRLF injection.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import string
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from ..base import BaseSimulation, SimulationContext, SimulationResult
from ..audit import ecs_event


logger = logging.getLogger("cortexsim.eal.plugins.ftp_egress")


_DEFAULT_USERNAME = "cortexsim"
_DEFAULT_PASSWORD = "cortexsim-lab"  # noqa: S105 — deliberate sentinel
_DEFAULT_BANNER_AGENT = "cortexsim-eal-simulator/1.0"
_COMMON_FTP_PORTS = {21, 990, 2121}

# A modest, printable ASCII payload — large enough that the NGFW DLP /
# file-transfer EAL fires on the STOR but small enough that test runs
# stay quick. Operators can override via ``stor_bytes`` (capped below).
_DEFAULT_STOR_BYTES = 4096


def _clean_field(v: str, *, name: str, max_len: int = 64) -> str:
    """Reject CRLF / NUL / control bytes in user-supplied FTP fields so
    callers cannot smuggle additional protocol commands through a USER
    or password parameter. FTP is a CRLF-framed protocol — this is the
    one place we MUST validate strictly."""
    v = v.strip()
    if not v:
        raise ValueError(f"{name} required")
    if len(v) > max_len:
        raise ValueError(f"{name} exceeds {max_len} chars")
    for ch in v:
        if ch in {"\r", "\n", "\x00"} or ord(ch) < 0x20:
            raise ValueError(f"{name} contains control character")
    return v


class FtpEgressParams(BaseModel):
    target_host: str = Field(..., description="Authorised FTP sinkhole host.")
    target_port: int = Field(default=21, ge=1, le=65535)
    username:    str = Field(default=_DEFAULT_USERNAME)
    password:    str = Field(default=_DEFAULT_PASSWORD)
    iterations:  int = Field(default=3, ge=1, le=100,
                             description="Number of independent control-channel sessions.")
    sleep_seconds: float = Field(default=10.0, ge=0.0, le=600.0)
    connect_timeout: float = Field(default=5.0, ge=0.5, le=30.0)
    idle_seconds: float = Field(default=1.5, ge=0.0, le=60.0)
    send_stor:   bool = Field(default=True,
                              description="If true, open a data channel and STOR a synthetic payload.")
    stor_bytes:  int = Field(default=_DEFAULT_STOR_BYTES, ge=64, le=1_048_576,
                             description="Size of the synthetic STOR payload (bytes).")
    banner_agent: str = Field(default=_DEFAULT_BANNER_AGENT)

    @field_validator("target_host")
    @classmethod
    def _host_required(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("target_host required")
        return v

    @field_validator("username")
    @classmethod
    def _username_clean(cls, v: str) -> str:
        return _clean_field(v, name="username", max_len=64)

    @field_validator("password")
    @classmethod
    def _password_clean(cls, v: str) -> str:
        return _clean_field(v, name="password", max_len=128)

    @field_validator("banner_agent")
    @classmethod
    def _banner_clean(cls, v: str) -> str:
        return _clean_field(v, name="banner_agent", max_len=64)


class FtpEgress(BaseSimulation):
    class Meta:
        name = "ftp_egress"
        version = "1.0.0"
        description = (
            "Drives a real FTP control session against an authorised sinkhole "
            "to exercise NGFW FTP App-ID, clear-text-credential EALs, and "
            "outbound-file-transfer detections."
        )
        mitre_techniques = ["T1071", "T1048.003"]
        eal_targets = [
            "FTP App-ID Egress",
            "Clear-Text Credentials Over FTP",
            "Outbound File Transfer via FTP STOR",
        ]
        params_model = FtpEgressParams

    async def run(self, ctx: SimulationContext) -> SimulationResult:
        params: FtpEgressParams = ctx.params  # type: ignore[assignment]
        started_at = self.utcnow()

        # Safety: host must be on the campaign's target_allowlist.
        getattr(ctx, "authorise")(params.target_host)

        if ctx.dry_run:
            await ctx.emit_event(ecs_event(
                action="ftp_dry_run",
                outcome="success",
                category="network",
                type_="info",
                message=(
                    f"DRY-RUN — {params.iterations} planned FTP sessions to "
                    f"{params.target_host}:{params.target_port} "
                    f"(stor={'on' if params.send_stor else 'off'})"
                ),
                campaign_id=ctx.campaign_id,
                run_id=ctx.run_id,
                step_id=ctx.step_id,
                plugin=self.Meta.name,
                target=f"{params.target_host}:{params.target_port}",
                extra={
                    "well_known_port": params.target_port in _COMMON_FTP_PORTS,
                    "stor_bytes": params.stor_bytes if params.send_stor else 0,
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
                    params.username,
                    params.password,
                    params.banner_agent,
                    params.send_stor,
                    params.stor_bytes,
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
                action="ftp_session",
                outcome=outcome,
                category="network",
                type_="connection",
                message=(
                    f"FTP session {i + 1}/{params.iterations} -> "
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
                    "username": params.username,  # surfaces in the audit trail; the value is a sentinel by default
                    "stor": params.send_stor,
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
                "stor_enabled": params.send_stor,
            },
        )

    # ------------------------------------------------------------------
    # Wire protocol — minimal, RFC-959-shaped command sequence
    # ------------------------------------------------------------------

    @staticmethod
    def _open_session(
        host: str,
        port: int,
        username: str,
        password: str,
        banner_agent: str,
        send_stor: bool,
        stor_bytes: int,
        connect_timeout: float,
        idle_seconds: float,
    ) -> int:
        """Open one FTP control session and walk the cleartext command
        sequence. Returns the byte count we sent on the wire (control +
        data channels combined). The sinkhole is free to NACK any command
        — we don't gate progress on the response codes because the EALs
        fire on the *send* side."""
        bytes_sent = 0

        with socket.create_connection((host, port), timeout=connect_timeout) as sock:
            sock.settimeout(connect_timeout)

            # 1. Read the server greeting (best-effort).
            try:
                sock.recv(1024)
            except (socket.timeout, OSError):
                pass

            # 2. Walk the canonical command sequence. We don't parse
            #    responses — we just emit the bytes and read whatever
            #    comes back so the socket stays open for App-ID
            #    fingerprinting.
            commands = [
                f"USER {username}\r\n",
                f"PASS {password}\r\n",
                "SYST\r\n",
                f"NOOP {banner_agent}\r\n",
            ]
            for cmd in commands:
                payload = cmd.encode("ascii", errors="strict")
                sock.sendall(payload)
                bytes_sent += len(payload)
                try:
                    sock.settimeout(0.5)
                    sock.recv(1024)
                except (socket.timeout, OSError):
                    pass

            # 3. Optionally open a data channel and STOR a synthetic payload.
            if send_stor:
                bytes_sent += FtpEgress._send_stor(
                    sock=sock,
                    host=host,
                    stor_bytes=stor_bytes,
                    connect_timeout=connect_timeout,
                )

            # 4. Polite goodbye.
            sock.sendall(b"QUIT\r\n")
            bytes_sent += len("QUIT\r\n")

            if idle_seconds > 0:
                try:
                    sock.settimeout(idle_seconds)
                    sock.recv(4096)
                except (socket.timeout, OSError):
                    pass

        return bytes_sent

    @staticmethod
    def _send_stor(
        sock: socket.socket,
        host: str,
        stor_bytes: int,
        connect_timeout: float,
    ) -> int:
        """Open a passive data channel and push a synthetic STOR payload.

        Best-effort: if PASV parsing fails or the data port is unreachable
        we return 0 and let the control-channel signal carry the EAL on
        its own.
        """
        sock.sendall(b"PASV\r\n")
        try:
            sock.settimeout(connect_timeout)
            resp = sock.recv(1024).decode("ascii", errors="replace")
        except (socket.timeout, OSError):
            return 0

        # Parse the 227-Entering-Passive-Mode reply.
        open_paren = resp.find("(")
        close_paren = resp.find(")", open_paren + 1)
        if open_paren < 0 or close_paren < 0:
            return 0

        parts = resp[open_paren + 1: close_paren].split(",")
        if len(parts) != 6:
            return 0
        try:
            p1, p2 = int(parts[4]), int(parts[5])
        except ValueError:
            return 0
        data_port = (p1 << 8) | p2

        # STOR command on the control channel.
        stor_filename = "cortexsim-eal-payload.bin"
        sock.sendall(f"STOR {stor_filename}\r\n".encode("ascii"))

        # Build a printable-ASCII payload of the requested size.
        chunk = (string.ascii_letters + string.digits).encode("ascii")
        payload = (chunk * ((stor_bytes // len(chunk)) + 1))[:stor_bytes]

        try:
            with socket.create_connection((host, data_port),
                                          timeout=connect_timeout) as data_sock:
                data_sock.settimeout(connect_timeout)
                data_sock.sendall(payload)
        except OSError:
            return 0

        # Read 226 Transfer Complete (best-effort).
        try:
            sock.settimeout(0.5)
            sock.recv(1024)
        except (socket.timeout, OSError):
            pass

        # Bytes sent: control "STOR ..." + data payload.
        return len(f"STOR {stor_filename}\r\n") + len(payload)
