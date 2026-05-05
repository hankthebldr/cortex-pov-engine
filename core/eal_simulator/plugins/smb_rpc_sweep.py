"""
smb_rpc_sweep — lateral-movement reconnaissance simulation.

Sweeps a CIDR for hosts with SMB (445) and RPC (135) listening, mimicking
the behaviour of credential-spraying / host-enumeration tooling. Triggers
Cortex EALs covering:

  * Host Sweeping (many short-lived SMB/RPC connections from one src)
  * Anomalous SMB/RPC traffic outside of normal admin patterns
  * Repeated 445 connection attempts from a workstation context

The plugin does NOT attempt authentication — just SYN/connect probes — so it
is safe to run inside a customer lab. If ``impacket`` is installed, the
plugin will additionally attempt an unauthenticated SMB session-setup so that
NTLM-related EALs fire; this is opt-in via ``probe_ntlm: true``.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from ..base import BaseSimulation, SimulationContext, SimulationResult
from ..audit import ecs_event


logger = logging.getLogger("cortexsim.eal.plugins.smb_rpc_sweep")


_DEFAULT_PORTS = [445, 139, 135]


class SmbRpcSweepParams(BaseModel):
    target_cidr: str = Field(..., description="CIDR or single IP to sweep.")
    ports: list[int] = Field(default_factory=lambda: list(_DEFAULT_PORTS))
    connect_timeout: float = Field(default=1.5, ge=0.1, le=10.0)
    inter_host_delay: float = Field(default=0.05, ge=0.0, le=10.0)
    max_hosts: int = Field(default=256, ge=1, le=4096)
    probe_ntlm: bool = Field(
        default=False,
        description="If true and impacket is installed, attempt an "
                    "unauthenticated SMB session setup per host.",
    )

    @field_validator("ports")
    @classmethod
    def _ports_valid(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("ports must be non-empty")
        for p in v:
            if p < 1 or p > 65535:
                raise ValueError(f"port {p} out of range")
        return v

    @field_validator("target_cidr")
    @classmethod
    def _cidr_valid(cls, v: str) -> str:
        try:
            ipaddress.ip_network(v, strict=False)
        except ValueError as exc:
            raise ValueError(f"target_cidr invalid: {exc}") from exc
        return v


class SmbRpcSweep(BaseSimulation):
    class Meta:
        name = "smb_rpc_sweep"
        version = "1.0.0"
        description = (
            "TCP connect-sweep across SMB/RPC ports for a CIDR; optional "
            "unauthenticated NTLM session setup if impacket is available."
        )
        mitre_techniques = ["T1018", "T1021.002", "T1046"]
        eal_targets = [
            "Host Sweeping",
            "Anomalous SMB Traffic",
            "Anomalous RPC Traffic",
        ]
        params_model = SmbRpcSweepParams

    async def run(self, ctx: SimulationContext) -> SimulationResult:
        params: SmbRpcSweepParams = ctx.params  # type: ignore[assignment]
        started_at = self.utcnow()

        # Pre-authorise the network base so a misconfigured campaign fails
        # fast before we expand the host list. We then re-check every
        # individual host below — a partial-overlap allowlist that admits
        # the base IP must NOT permit out-of-scope sweeps.
        from ..safety import SafetyError  # local to avoid top-level cycle

        authorise = getattr(ctx, "authorise")
        authorise(params.target_cidr.split("/", 1)[0])

        net = ipaddress.ip_network(params.target_cidr, strict=False)
        # Skip network and broadcast for /<31 nets; small subnets keep all hosts.
        hosts = list(net.hosts()) if net.num_addresses > 2 else list(net)
        hosts = hosts[: params.max_hosts]

        if ctx.dry_run:
            await ctx.emit_event(ecs_event(
                action="smb_sweep_dry_run",
                outcome="success",
                category="network",
                type_="info",
                message=(
                    f"DRY-RUN — would probe {len(hosts)} host(s) on "
                    f"ports {params.ports}"
                ),
                campaign_id=ctx.campaign_id,
                run_id=ctx.run_id,
                step_id=ctx.step_id,
                plugin=self.Meta.name,
                target=params.target_cidr,
                extra={"host_count": len(hosts), "ports": params.ports},
            ))
            return SimulationResult(
                plugin=self.Meta.name,
                step_id=ctx.step_id,
                status="success",
                started_at=started_at,
                completed_at=self.utcnow(),
                events_emitted=1,
                detail={"dry_run": True, "hosts_planned": len(hosts)},
            )

        events_emitted = 0
        successes = 0
        ntlm_module = None
        if params.probe_ntlm:
            try:  # pragma: no cover - optional dep
                from impacket.smbconnection import SMBConnection  # type: ignore

                ntlm_module = SMBConnection
            except Exception:
                logger.warning("probe_ntlm requested but impacket not available")

        skipped: list[str] = []
        for ip in hosts:
            ip_str = str(ip)
            # Authorise EACH host independently. A partial-overlap allowlist
            # may admit the base IP but exclude the rest of the swept range;
            # we must skip those hosts rather than emit real connect attempts.
            try:
                authorise(ip_str)
            except SafetyError as exc:
                skipped.append(ip_str)
                await ctx.emit_event(ecs_event(
                    action="smb_sweep_skipped",
                    outcome="failure",
                    category="iam",
                    type_="denied",
                    message=f"host {ip_str} skipped: {exc}",
                    campaign_id=ctx.campaign_id,
                    run_id=ctx.run_id,
                    step_id=ctx.step_id,
                    plugin=self.Meta.name,
                    target=ip_str,
                    extra={"reason": "not_in_allowlist"},
                ))
                continue

            for port in params.ports:
                opened = await asyncio.to_thread(
                    self._tcp_probe, ip_str, port, params.connect_timeout
                )
                events_emitted += 1
                if opened:
                    successes += 1

                outcome = "success" if opened else "failure"
                await ctx.emit_event(ecs_event(
                    action="smb_sweep_probe",
                    outcome=outcome,
                    category="network",
                    type_="connection",
                    message=f"probe {ip_str}:{port} {'OPEN' if opened else 'CLOSED'}",
                    campaign_id=ctx.campaign_id,
                    run_id=ctx.run_id,
                    step_id=ctx.step_id,
                    plugin=self.Meta.name,
                    target=f"{ip_str}:{port}",
                    extra={"opened": opened},
                ))

                if opened and port == 445 and ntlm_module is not None:
                    self._probe_ntlm(ntlm_module, ip_str, ctx)

            if params.inter_host_delay > 0:
                await asyncio.sleep(params.inter_host_delay)

        return SimulationResult(
            plugin=self.Meta.name,
            step_id=ctx.step_id,
            status="success",
            started_at=started_at,
            completed_at=self.utcnow(),
            events_emitted=events_emitted,
            detail={
                "hosts_probed": len(hosts) - len(skipped),
                "hosts_skipped_unauthorised": len(skipped),
                "ports_probed": params.ports,
                "open_ports_observed": successes,
            },
        )

    @staticmethod
    def _tcp_probe(host: str, port: int, timeout: float) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    @staticmethod
    def _probe_ntlm(SMBConnection, ip: str, ctx: SimulationContext) -> None:  # pragma: no cover - opt-in
        try:
            conn = SMBConnection(remoteName=ip, remoteHost=ip, sess_port=445, timeout=3)
            conn.login("", "")  # null session
            conn.close()
        except Exception as exc:
            logger.debug("ntlm probe %s -> %s", ip, exc)
