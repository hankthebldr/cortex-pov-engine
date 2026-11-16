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
                    "body_random_bytes"}             # the beacon also takes body_b64
    {"op": "dns",   "qname", "qtype", "resolver"}      # resolver "" = the host's own
    {"op": "tcp",   "host", "port", "timeout_s", "probe",
                    "frames": [{"send_b64", "read"}]}   # read = seconds to wait for a reply
    {"op": "sleep", "seconds"}

Nothing in this module opens a socket or resolves a name. Every destination is
checked against the campaign's allowlist with the same ``authorise`` call the
plugin itself makes.

The campaign text comes from a target, so nothing it says is allowed to
multiply work without limit. Operations are added through one :class:`Plan`,
which refuses the operation that would cross a ceiling instead of checking a
finished list, and each renderer first asks ``Plan.expect`` whether its
parameters can fit at all, before it builds anything. A plugin's own params
allow far more than a step should be handed: one beacon a day for a year is a
valid ``c2_http_beacon`` step.

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

# What one relayed campaign may be handed. The beacon holds its own copy and
# refuses a reply that exceeds them.
MAX_OPS = 4096
MAX_SLEEP_SECONDS = 600.0           # one sleep op
MAX_TOTAL_SLEEP_SECONDS = 3600.0    # every sleep op together
MAX_BODY_RANDOM_BYTES = 1024 ** 3   # one http op
MAX_TOTAL_BODY_BYTES = 4 * 1024 ** 3
MAX_HTTP_TIMEOUT_S = 3600.0
MAX_TCP_TIMEOUT_S = 60.0
MAX_FRAME_READ_S = 60.0
MAX_FRAME_BYTES = 1024 * 1024       # one tcp frame, decoded

PLAN_REFUSED = "EAL_WIRE_PLAN_REFUSED"

Authorise = Callable[..., None]
Op = dict[str, Any]


class WirePlanError(Exception):
    """A plan that may not be handed to a beacon. ``code`` is stable, and
    ``built`` is how many operations existed when it was refused."""

    def __init__(self, detail: str, *, built: int = 0) -> None:
        super().__init__(f"{PLAN_REFUSED}: {detail}")
        self.code = PLAN_REFUSED
        self.detail = detail
        self.built = built


class Plan:
    """The operations of one relayed campaign, bounded as they are added.

    Every operation goes through one of the four methods below, and each
    refuses the operation that would cross a ceiling. So a plan is never
    larger than its ceilings at any moment, whatever a renderer loops over.
    """

    def __init__(self) -> None:
        self.ops: list[Op] = []
        self.sleep_seconds = 0.0
        self.body_bytes = 0

    def refuse(self, detail: str) -> WirePlanError:
        return WirePlanError(detail, built=len(self.ops))

    def expect(self, *, ops: int, sleep: float = 0.0, longest_sleep: float = 0.0,
               body: int = 0, largest_body: int = 0) -> None:
        """Refuse a step whose parameters already say it cannot fit, before a
        single operation is built for it. Arithmetic only."""
        if len(self.ops) + ops > MAX_OPS:
            raise self.refuse(
                f"{ops} more operations would make {len(self.ops) + ops}, "
                f"over the {MAX_OPS} one step may be handed")
        if longest_sleep > MAX_SLEEP_SECONDS:
            raise self.refuse(
                f"a {longest_sleep:g}s sleep, over the {MAX_SLEEP_SECONDS:g}s one operation may wait")
        if self.sleep_seconds + sleep > MAX_TOTAL_SLEEP_SECONDS:
            raise self.refuse(
                f"{self.sleep_seconds + sleep:g}s of sleep in all, over the "
                f"{MAX_TOTAL_SLEEP_SECONDS:g}s one step may wait")
        if largest_body > MAX_BODY_RANDOM_BYTES:
            raise self.refuse(
                f"a {largest_body}-byte request body, over the {MAX_BODY_RANDOM_BYTES} one request may carry")
        if self.body_bytes + body > MAX_TOTAL_BODY_BYTES:
            raise self.refuse(
                f"{self.body_bytes + body} bytes of request body in all, over the "
                f"{MAX_TOTAL_BODY_BYTES} one step may send")

    def _add(self, op: Op) -> None:
        if len(self.ops) >= MAX_OPS:
            raise self.refuse(f"over the {MAX_OPS} operations one step may be handed")
        self.ops.append(op)

    def http(self, method: str, url: str, headers: dict[str, str], *, timeout_s: float,
             verify_tls: bool, body_random_bytes: int = 0) -> None:
        self.expect(ops=1, body=body_random_bytes, largest_body=body_random_bytes)
        op: Op = {
            "op": "http", "method": method, "url": url, "headers": headers,
            "timeout_s": min(float(timeout_s), MAX_HTTP_TIMEOUT_S), "verify_tls": verify_tls,
        }
        if body_random_bytes > 0:
            op["body_random_bytes"] = int(body_random_bytes)
        self._add(op)
        self.body_bytes += max(int(body_random_bytes), 0)

    def dns(self, qname: str, qtype: str, resolver: str) -> None:
        self._add({"op": "dns", "qname": qname, "qtype": qtype, "resolver": resolver})

    def tcp(self, host: str, port: int, timeout_s: float, frames: list[Op], *,
            probe: bool = False) -> None:
        for frame in frames:
            if len(frame.get("send_b64") or "") > MAX_FRAME_BYTES * 4 // 3 + 4:
                raise self.refuse("a tcp frame over 1 MiB")
            if "read" in frame:
                frame["read"] = min(float(frame["read"]), MAX_FRAME_READ_S)
        self._add({"op": "tcp", "host": host, "port": int(port),
                   "timeout_s": min(float(timeout_s), MAX_TCP_TIMEOUT_S),
                   "probe": probe, "frames": frames})

    def sleep(self, seconds: float) -> None:
        seconds = round(float(seconds), 3)
        self.expect(ops=1, sleep=seconds, longest_sleep=seconds)
        self._add({"op": "sleep", "seconds": seconds})
        self.sleep_seconds += seconds


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


def _between(count: int, seconds: float) -> tuple[int, float]:
    """``(sleep ops, total seconds)`` for a sleep between each of ``count``
    actions: none when the sleep is zero."""
    gaps = max(count - 1, 0) if seconds > 0 else 0
    return gaps, gaps * seconds


# ---------------------------------------------------------------------------
# One renderer per plugin. Each follows its plugin's run() line for line, and
# opens with the arithmetic that says whether its parameters can fit.
# ---------------------------------------------------------------------------


def _c2_http_beacon(p: c2_http_beacon.C2HttpBeaconParams, wc: WireContext, plan: Plan) -> None:
    host, _port = url_host_port(p.target_url)
    wc.authorise(host)
    body = p.body_size_bytes if p.method == "POST" else 0
    longest = p.sleep_seconds * (1 + p.jitter_pct / 100.0)  # iterations x sleep x jitter
    plan.expect(ops=2 * p.iterations - 1, sleep=longest * (p.iterations - 1),
                longest_sleep=longest if p.iterations > 1 else 0.0,
                body=body * p.iterations, largest_body=body)
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    for i in range(p.iterations):
        ua = wc.rng.choice(p.user_agents) if p.user_agents else "CortexSim/1.0"
        url = p.target_url
        if p.dga_query_param:
            token = "".join(wc.rng.choice(alphabet) for _ in range(12))
            url = f"{url}{'&' if '?' in url else '?'}q={token}"
        plan.http(p.method, url, {**wc.telemetry_headers, "User-Agent": ua},
                  timeout_s=p.request_timeout, verify_tls=wc.verify_tls, body_random_bytes=body)
        if i < p.iterations - 1:
            delta = p.sleep_seconds * (p.jitter_pct / 100.0)
            plan.sleep(p.sleep_seconds if p.jitter_pct <= 0 else max(
                0.0, p.sleep_seconds + wc.rng.uniform(-delta, delta)))


def _dns_tunnel_exfil(p: dns_tunnel_exfil.DnsTunnelExfilParams, wc: WireContext, plan: Plan) -> None:
    wc.authorise(p.base_domain)
    # The plugin sends an A query through the host's resolver whatever
    # ``resolver`` says; only a TXT query goes to the named one.
    resolver = _resolver_pin(p.resolver) if p.resolver and p.query_type == "TXT" else ""
    if resolver:
        wc.authorise(resolver)
    gaps, total = _between(p.chunks, p.sleep_seconds)  # chunks x sleep
    plan.expect(ops=p.chunks + gaps, sleep=total, longest_sleep=p.sleep_seconds)
    for i in range(p.chunks):
        label = dns_tunnel_exfil._encode_chunk(  # noqa: SLF001 - the plugin's own encoder
            wc.rng.randbytes(p.chunk_size_bytes), p.encoding)
        plan.dns(f"{label}.exfil-{i:04d}.{p.base_domain}", p.query_type, resolver)
        if p.sleep_seconds > 0 and i < p.chunks - 1:
            plan.sleep(p.sleep_seconds)


def _stratum_tcp_connect(
    p: stratum_tcp_connect.StratumTcpConnectParams, wc: WireContext, plan: Plan,
) -> None:
    wc.authorise(p.target_host, port=p.target_port)
    gaps, total = _between(p.iterations, p.sleep_seconds)  # iterations x sleep
    plan.expect(ops=p.iterations + gaps, sleep=total, longest_sleep=p.sleep_seconds)
    for i in range(p.iterations):
        subscribe = {"id": wc.rng.randint(1, 9999), "jsonrpc": "2.0",
                     "method": "mining.subscribe", "params": [p.user_agent]}
        login = {"id": wc.rng.randint(1, 9999), "jsonrpc": "2.0", "method": "login",
                 "params": {"login": p.wallet, "pass": "x", "agent": p.user_agent}}
        plan.tcp(p.target_host, p.target_port, p.connect_timeout, [
            {"send_b64": _b64((json.dumps(subscribe) + "\n").encode())},
            {"send_b64": _b64((json.dumps(login) + "\n").encode()),
             "read": float(p.idle_seconds)},
        ])
        if i < p.iterations - 1 and p.sleep_seconds > 0:
            plan.sleep(p.sleep_seconds)


def _bulk_https_exfil(p: bulk_https_exfil.BulkHttpsExfilParams, wc: WireContext, plan: Plan) -> None:
    host, _port = url_host_port(p.target_url)
    wc.authorise(host)
    per_request = p.total_bytes // p.request_count
    last = p.total_bytes - per_request * (p.request_count - 1)  # total_bytes / request_count
    plan.expect(ops=p.request_count, body=p.total_bytes, largest_body=max(per_request, last))
    for i in range(p.request_count):
        plan.http(p.method, p.target_url, dict(wc.telemetry_headers),
                  timeout_s=p.request_timeout, verify_tls=wc.verify_tls,
                  body_random_bytes=per_request if i < p.request_count - 1 else last)


def _smb_rpc_sweep(p: smb_rpc_sweep.SmbRpcSweepParams, wc: WireContext, plan: Plan) -> None:
    wc.authorise(p.target_cidr.split("/", 1)[0])
    net = ipaddress.ip_network(p.target_cidr, strict=False)
    # How many addresses net.hosts() yields, without asking it for them.
    usable = net.num_addresses
    if usable > 2:
        usable -= 2 if net.version == 4 else 1
    hosts = min(p.max_hosts, usable)  # max_hosts x ports x inter_host_delay
    gaps = hosts if p.inter_host_delay > 0 else 0
    plan.expect(ops=hosts * len(p.ports) + gaps, sleep=gaps * p.inter_host_delay,
                longest_sleep=p.inter_host_delay)
    # The plugin's host list, cut to max_hosts without first building all of it
    # (a /8 is 16 million addresses).
    for ip in itertools.islice(net.hosts() if net.num_addresses > 2 else iter(net), hosts):
        try:
            wc.authorise(str(ip))
        except SafetyError:
            continue  # the plugin skips a host the allowlist does not admit
        # A connect-and-close probe. A closed port is the expected answer, so
        # the beacon does not count it as a failed operation. ``probe_ntlm`` (an
        # impacket session setup) has no operation and is not rendered.
        for port in p.ports:
            plan.tcp(str(ip), port, p.connect_timeout, [], probe=True)
        if p.inter_host_delay > 0:
            plan.sleep(p.inter_host_delay)


def _ftp_egress(p: ftp_egress.FtpEgressParams, wc: WireContext, plan: Plan) -> None:
    wc.authorise(p.target_host, port=p.target_port)
    gaps, total = _between(p.iterations, p.sleep_seconds)  # iterations x sleep
    plan.expect(ops=p.iterations + gaps, sleep=total, longest_sleep=p.sleep_seconds)
    commands = [f"USER {p.username}\r\n", f"PASS {p.password}\r\n", "SYST\r\n",
                f"NOOP {p.banner_agent}\r\n"]
    for i in range(p.iterations):
        frames: list[Op] = [{"read": float(p.connect_timeout)}]  # the server greeting
        frames += [{"send_b64": _b64(c.encode("ascii")), "read": 0.5} for c in commands]
        # ``send_stor`` opens a second connection to a port the server names in
        # its PASV reply. No operation can express that, so the control session
        # is rendered without the STOR (and ``stor_bytes`` multiplies nothing).
        frames.append({"send_b64": _b64(b"QUIT\r\n"), "read": float(p.idle_seconds)})
        plan.tcp(p.target_host, p.target_port, p.connect_timeout, frames)
        if i < p.iterations - 1 and p.sleep_seconds > 0:
            plan.sleep(p.sleep_seconds)


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


def _ssh_egress(p: ssh_egress.SshEgressParams, wc: WireContext, plan: Plan) -> None:
    wc.authorise(p.target_host, port=p.target_port)
    gaps, total = _between(p.iterations, p.sleep_seconds)  # iterations x sleep
    plan.expect(ops=p.iterations + gaps, sleep=total, longest_sleep=p.sleep_seconds)
    for i in range(p.iterations):
        frames: list[Op] = [
            {"read": float(p.connect_timeout)},  # drain a server banner sent first
            {"send_b64": _b64((p.client_banner + "\r\n").encode("ascii"))},
        ]
        if p.send_kexinit:
            frames.append({"send_b64": _b64(_ssh_kexinit(wc.rng))})
        frames[-1]["read"] = float(p.idle_seconds)
        plan.tcp(p.target_host, p.target_port, p.connect_timeout, frames)
        if i < p.iterations - 1 and p.sleep_seconds > 0:
            plan.sleep(p.sleep_seconds)


RENDERERS: dict[str, Callable[[Any, WireContext, Plan], None]] = {
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


def render(plugin: str, params: BaseModel, wc: WireContext,
           plan: Optional[Plan] = None) -> list[Op]:
    """Add one validated step's operations to ``plan`` and return them.

    Pass the same ``plan`` for every step of a campaign: the ceilings are the
    campaign's, not each step's. Raises ``SafetyError`` when a destination is
    outside the campaign allowlist, exactly as the plugin would, and
    ``WirePlanError`` at the operation that would cross a ceiling.
    """
    plan = plan if plan is not None else Plan()
    start = len(plan.ops)
    RENDERERS[plugin](params, wc, plan)
    return plan.ops[start:]


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
        # The zone the renderer built the name under: everything after the two
        # generated labels.
        out = ["dns:" + str(op["qname"]).split(".", 2)[-1]]
        if op.get("resolver"):
            out.append(str(op["resolver"]))
        return out
    return []


def allowlist_for(ops: list[Op]) -> list[str]:
    """Every destination the plan touches, once each, in first-use order. Built
    from the rendered operations, never from campaign text."""
    seen: dict[str, None] = {}
    for op in ops:
        for dest in destinations(op):
            seen.setdefault(dest)
    return list(seen)
