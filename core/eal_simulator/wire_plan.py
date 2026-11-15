"""Render a network-shape plugin step into primitive network operations.

A network-shape plugin (``c2_http_beacon``, ``dns_tunnel_exfil`` ...) puts its
own traffic on the wire, and the point of that traffic is its source: a firewall
attributes it to whichever host sent it. Run inside SimCore, the traffic is
SimCore's. So for a step relayed from a beacon (``engine/eal_relay.py``) these
plugins are not executed here at all. Each one's ``run()`` is restated below as
a pure function from validated params to an ordered list of operations, and the
beacon performs them from the target.

Four operations, all JSON-serialisable dicts::

    {"op": "http",  "method", "url", "headers", "timeout_s", "verify_tls",
                    "body_b64" | "body_random_bytes"}
    {"op": "dns",   "qname", "qtype", "resolver"}      # resolver "" = the host's own
    {"op": "tcp",   "host", "port", "timeout_s", "probe",
                    "frames": [{"send_b64", "read"}]}   # read = seconds to wait for a reply
    {"op": "sleep", "seconds"}

Nothing in this module opens a socket or resolves a name. Every destination is
checked against the campaign's allowlist with the same ``authorise`` call the
plugin itself makes, and :func:`bound` caps what one step may be handed.

A renderer can drift from its plugin's ``run()``. ``spec/eal_wire_ops_cases.json``
pins the rendered operations for a fixed seed; a Python test and a Go test both
read it.
"""

from __future__ import annotations

import base64
import ipaddress
import itertools
import json
import random
from typing import Any, Callable, Optional

from pydantic import BaseModel

from .plugins import (
    bulk_https_exfil,
    c2_http_beacon,
    dns_tunnel_exfil,
    ftp_egress,
    smb_rpc_sweep,
    ssh_egress,
    stratum_tcp_connect,
)
from .safety import SafetyError, url_host_port

# What one relayed step may be handed. The beacon holds its own copy of the
# first four and refuses a response that exceeds them.
MAX_OPS = 4096
MAX_SLEEP_SECONDS = 600.0           # one sleep op
MAX_TOTAL_SLEEP_SECONDS = 3600.0    # every sleep op of one step
MAX_BODY_RANDOM_BYTES = 1024 ** 3   # one http op
MAX_TOTAL_BODY_BYTES = 4 * 1024 ** 3
MAX_HTTP_TIMEOUT_S = 3600.0
MAX_TCP_TIMEOUT_S = 60.0
MAX_FRAME_READ_S = 60.0
MAX_FRAME_BYTES = 1024 * 1024       # one tcp frame / inline http body, decoded

Authorise = Callable[..., None]
Op = dict[str, Any]


class WirePlanError(Exception):
    """A plan that may not be handed to a beacon. ``code`` is stable."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class WireContext:
    """What a renderer needs that the plugin reads from ``SimulationContext``."""

    def __init__(
        self,
        *,
        campaign_id: str,
        simulation_run_id: str,
        authorise: Authorise,
        verify_tls: bool = False,
        rng: Optional[random.Random] = None,
    ) -> None:
        self.campaign_id = campaign_id
        self.simulation_run_id = simulation_run_id
        self.authorise = authorise
        self.verify_tls = verify_tls
        self.rng = rng or random.Random()

    @property
    def telemetry_headers(self) -> dict[str, str]:
        # The same three headers SimulationContext.telemetry_headers sends.
        return {
            "X-Simulation-Run-ID": self.simulation_run_id,
            "X-Simulation-Campaign-ID": self.campaign_id,
            "X-Simulation-Source": "cortexsim-eal-simulator/1.0",
        }


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _sleep(seconds: float) -> Op:
    return {"op": "sleep", "seconds": round(float(seconds), 3)}


def _tcp(host: str, port: int, timeout_s: float, frames: list[Op], *, probe: bool = False) -> Op:
    return {"op": "tcp", "host": host, "port": int(port), "timeout_s": float(timeout_s),
            "probe": probe, "frames": frames}


# ---------------------------------------------------------------------------
# One renderer per plugin. Each follows its plugin's run() line for line.
# ---------------------------------------------------------------------------


def _c2_http_beacon(p: c2_http_beacon.C2HttpBeaconParams, wc: WireContext) -> list[Op]:
    host, _port = url_host_port(p.target_url)
    wc.authorise(host)
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    ops: list[Op] = []
    for i in range(p.iterations):
        ua = wc.rng.choice(p.user_agents) if p.user_agents else "CortexSim/1.0"
        url = p.target_url
        if p.dga_query_param:
            token = "".join(wc.rng.choice(alphabet) for _ in range(12))
            url = f"{url}{'&' if '?' in url else '?'}q={token}"
        op: Op = {
            "op": "http", "method": p.method, "url": url,
            "headers": {**wc.telemetry_headers, "User-Agent": ua},
            "timeout_s": float(p.request_timeout), "verify_tls": wc.verify_tls,
        }
        if p.method == "POST" and p.body_size_bytes > 0:
            op["body_random_bytes"] = int(p.body_size_bytes)
        ops.append(op)
        if i < p.iterations - 1:
            delta = p.sleep_seconds * (p.jitter_pct / 100.0)
            jittered = p.sleep_seconds if p.jitter_pct <= 0 else max(
                0.0, p.sleep_seconds + wc.rng.uniform(-delta, delta))
            ops.append(_sleep(jittered))
    return ops


def _dns_tunnel_exfil(p: dns_tunnel_exfil.DnsTunnelExfilParams, wc: WireContext) -> list[Op]:
    wc.authorise(p.base_domain)
    # The plugin sends an A query through the host's resolver whatever
    # ``resolver`` says; only a TXT query goes to the named one.
    resolver = (p.resolver or "") if p.query_type == "TXT" else ""
    if resolver:
        wc.authorise(_resolver_pin(resolver))
    ops: list[Op] = []
    for i in range(p.chunks):
        label = dns_tunnel_exfil._encode_chunk(  # noqa: SLF001 - the plugin's own encoder
            wc.rng.randbytes(p.chunk_size_bytes), p.encoding)
        ops.append({
            "op": "dns", "qname": f"{label}.exfil-{i:04d}.{p.base_domain}",
            "qtype": p.query_type, "resolver": _resolver_pin(resolver) if resolver else "",
        })
        if p.sleep_seconds > 0 and i < p.chunks - 1:
            ops.append(_sleep(p.sleep_seconds))
    return ops


def _stratum_tcp_connect(
    p: stratum_tcp_connect.StratumTcpConnectParams, wc: WireContext,
) -> list[Op]:
    wc.authorise(p.target_host, port=p.target_port)
    ops: list[Op] = []
    for i in range(p.iterations):
        subscribe = {"id": wc.rng.randint(1, 9999), "jsonrpc": "2.0",
                     "method": "mining.subscribe", "params": [p.user_agent]}
        login = {"id": wc.rng.randint(1, 9999), "jsonrpc": "2.0", "method": "login",
                 "params": {"login": p.wallet, "pass": "x", "agent": p.user_agent}}
        ops.append(_tcp(p.target_host, p.target_port, p.connect_timeout, [
            {"send_b64": _b64((json.dumps(subscribe) + "\n").encode())},
            {"send_b64": _b64((json.dumps(login) + "\n").encode()),
             "read": float(p.idle_seconds)},
        ]))
        if i < p.iterations - 1 and p.sleep_seconds > 0:
            ops.append(_sleep(p.sleep_seconds))
    return ops


def _bulk_https_exfil(p: bulk_https_exfil.BulkHttpsExfilParams, wc: WireContext) -> list[Op]:
    host, _port = url_host_port(p.target_url)
    wc.authorise(host)
    per_request = p.total_bytes // p.request_count
    ops: list[Op] = []
    sent = 0
    for i in range(p.request_count):
        size = per_request if i < p.request_count - 1 else p.total_bytes - sent
        sent += size
        ops.append({
            "op": "http", "method": p.method, "url": p.target_url,
            "headers": dict(wc.telemetry_headers), "body_random_bytes": int(size),
            "timeout_s": float(p.request_timeout), "verify_tls": wc.verify_tls,
        })
    return ops


def _smb_rpc_sweep(p: smb_rpc_sweep.SmbRpcSweepParams, wc: WireContext) -> list[Op]:
    wc.authorise(p.target_cidr.split("/", 1)[0])
    net = ipaddress.ip_network(p.target_cidr, strict=False)
    # The plugin's host list, cut to max_hosts without first building all of it
    # (a /8 is 16 million addresses, and this text came from a target).
    hosts = itertools.islice(net.hosts() if net.num_addresses > 2 else iter(net), p.max_hosts)
    ops: list[Op] = []
    for ip in hosts:
        try:
            wc.authorise(str(ip))
        except SafetyError:
            continue  # the plugin skips a host the allowlist does not admit
        # A connect-and-close probe. A closed port is the expected answer, so
        # the beacon does not count it as a failed operation. ``probe_ntlm`` (an
        # impacket session setup) has no operation and is not rendered.
        ops.extend(_tcp(str(ip), port, p.connect_timeout, [], probe=True) for port in p.ports)
        if p.inter_host_delay > 0:
            ops.append(_sleep(p.inter_host_delay))
    return ops


def _ftp_egress(p: ftp_egress.FtpEgressParams, wc: WireContext) -> list[Op]:
    wc.authorise(p.target_host, port=p.target_port)
    commands = [f"USER {p.username}\r\n", f"PASS {p.password}\r\n", "SYST\r\n",
                f"NOOP {p.banner_agent}\r\n"]
    ops: list[Op] = []
    for i in range(p.iterations):
        frames: list[Op] = [{"read": float(p.connect_timeout)}]  # the server greeting
        frames += [{"send_b64": _b64(c.encode("ascii")), "read": 0.5} for c in commands]
        # ``send_stor`` opens a second connection to a port the server names in
        # its PASV reply. No operation can express that, so the control session
        # is rendered without the STOR.
        frames.append({"send_b64": _b64(b"QUIT\r\n"), "read": float(p.idle_seconds)})
        ops.append(_tcp(p.target_host, p.target_port, p.connect_timeout, frames))
        if i < p.iterations - 1 and p.sleep_seconds > 0:
            ops.append(_sleep(p.sleep_seconds))
    return ops


def _ssh_kexinit(rng: random.Random) -> bytes:
    """``SshEgress._build_kexinit_packet`` with the caller's generator."""
    payload = bytes([ssh_egress._SSH_MSG_KEXINIT]) + rng.randbytes(16)  # noqa: SLF001
    for name_list in ssh_egress._DEFAULT_KEX_OFFER.values():  # noqa: SLF001
        joined = ",".join(name_list).encode("ascii")
        payload += len(joined).to_bytes(4, "big") + joined
    payload += b"\x00" + (0).to_bytes(4, "big")
    padding_len = 8 - ((len(payload) + 5) % 8)
    if padding_len < 4:
        padding_len += 8
    body = bytes([padding_len]) + payload + rng.randbytes(padding_len)
    return len(body).to_bytes(4, "big") + body


def _ssh_egress(p: ssh_egress.SshEgressParams, wc: WireContext) -> list[Op]:
    wc.authorise(p.target_host, port=p.target_port)
    ops: list[Op] = []
    for i in range(p.iterations):
        frames: list[Op] = [
            {"read": float(p.connect_timeout)},  # drain a server banner sent first
            {"send_b64": _b64((p.client_banner + "\r\n").encode("ascii"))},
        ]
        if p.send_kexinit:
            frames.append({"send_b64": _b64(_ssh_kexinit(wc.rng))})
        frames[-1]["read"] = float(p.idle_seconds)
        ops.append(_tcp(p.target_host, p.target_port, p.connect_timeout, frames))
        if i < p.iterations - 1 and p.sleep_seconds > 0:
            ops.append(_sleep(p.sleep_seconds))
    return ops


RENDERERS: dict[str, Callable[[Any, WireContext], list[Op]]] = {
    "c2_http_beacon": _c2_http_beacon,
    "dns_tunnel_exfil": _dns_tunnel_exfil,
    "stratum_tcp_connect": _stratum_tcp_connect,
    "bulk_https_exfil": _bulk_https_exfil,
    "smb_rpc_sweep": _smb_rpc_sweep,
    "ftp_egress": _ftp_egress,
    "ssh_egress": _ssh_egress,
}

#: What a rendered plan leaves out of its plugin's ``run()``, by plugin.
NOT_RENDERED: dict[str, str] = {
    "smb_rpc_sweep": "probe_ntlm (an impacket SMB session setup) is not rendered",
    "ftp_egress": "the PASV data channel and its STOR payload are not rendered",
}


def has_renderer(plugin: str) -> bool:
    return plugin in RENDERERS


def render(plugin: str, params: BaseModel, wc: WireContext) -> list[Op]:
    """The ordered operations for one validated step. Raises ``SafetyError``
    when a destination is outside the campaign allowlist, exactly as the plugin
    would, and ``WirePlanError`` when the plan is over a bound."""
    ops = RENDERERS[plugin](params, wc)
    bound(ops)
    return ops


def _resolver_pin(resolver: str) -> str:
    """``ip`` or ``ip:port`` as the ``host:port`` the query is sent to."""
    resolver = resolver.strip()
    if resolver.count(":") == 1 and resolver.rsplit(":", 1)[1].isdigit():
        return resolver
    return f"{resolver}:53"


def destinations(op: Op) -> list[str]:
    """The allowlist entries one operation needs: ``host:port`` for a
    connection, ``dns:<name>`` for a query name."""
    kind = op.get("op")
    if kind == "http":
        host, port = url_host_port(str(op.get("url", "")))
        return [f"{host}:{port}"]
    if kind == "tcp":
        return [f"{op['host']}:{op['port']}"]
    if kind == "dns":
        # The registrable part the renderer built the name under: everything
        # after the two generated labels.
        out = ["dns:" + str(op["qname"]).split(".", 2)[-1]]
        if op.get("resolver"):
            out.append(str(op["resolver"]))
        return out
    return []


def allowlist_for(ops: list[Op]) -> list[str]:
    """Every destination the plan touches, once each, in first-use order. Built
    from the rendered operations, never from campaign text."""
    out: list[str] = []
    for op in ops:
        for dest in destinations(op):
            if dest not in out:
                out.append(dest)
    return out


def bound(ops: list[Op]) -> None:
    """Refuse a plan over any ceiling. A plugin's own params allow far more
    than a step should be handed (a beacon a day for a year is valid)."""
    if len(ops) > MAX_OPS:
        raise WirePlanError("EAL_WIRE_PLAN_REFUSED",
                            f"{len(ops)} operations, over the {MAX_OPS} one step may be handed")
    total_sleep = 0.0
    total_body = 0
    for op in ops:
        kind = op.get("op")
        if kind == "sleep":
            seconds = float(op["seconds"])
            if seconds > MAX_SLEEP_SECONDS:
                raise WirePlanError(
                    "EAL_WIRE_PLAN_REFUSED",
                    f"a {seconds:g}s sleep, over the {MAX_SLEEP_SECONDS:g}s one operation may wait")
            total_sleep += seconds
        elif kind == "http":
            body = int(op.get("body_random_bytes") or 0)
            if body > MAX_BODY_RANDOM_BYTES:
                raise WirePlanError(
                    "EAL_WIRE_PLAN_REFUSED",
                    f"a {body}-byte request body, over the {MAX_BODY_RANDOM_BYTES} one request may carry")
            total_body += body
            if len(op.get("body_b64") or "") > MAX_FRAME_BYTES * 4 // 3 + 4:
                raise WirePlanError("EAL_WIRE_PLAN_REFUSED", "an inline request body over 1 MiB")
            op["timeout_s"] = min(float(op.get("timeout_s") or 0) or 30.0, MAX_HTTP_TIMEOUT_S)
        elif kind == "tcp":
            op["timeout_s"] = min(float(op.get("timeout_s") or 0) or 5.0, MAX_TCP_TIMEOUT_S)
            for frame in op.get("frames") or []:
                if "read" in frame:
                    frame["read"] = min(float(frame["read"]), MAX_FRAME_READ_S)
                if len(frame.get("send_b64") or "") > MAX_FRAME_BYTES * 4 // 3 + 4:
                    raise WirePlanError("EAL_WIRE_PLAN_REFUSED", "a tcp frame over 1 MiB")
    if total_sleep > MAX_TOTAL_SLEEP_SECONDS:
        raise WirePlanError(
            "EAL_WIRE_PLAN_REFUSED",
            f"{total_sleep:g}s of sleep in all, over the {MAX_TOTAL_SLEEP_SECONDS:g}s one step may wait")
    if total_body > MAX_TOTAL_BODY_BYTES:
        raise WirePlanError(
            "EAL_WIRE_PLAN_REFUSED",
            f"{total_body} bytes of request body in all, over the {MAX_TOTAL_BODY_BYTES} one step may send")
