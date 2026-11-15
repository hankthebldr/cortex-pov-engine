"""Network-shape plugin steps rendered to operations for a beacon (#325).

``spec/eal_wire_ops_cases.json`` pins, for each renderer, the exact operations
a fixed seed produces. This test renders each case and compares; the beacon's
``TestOpsExecutedInOrderFromSpecCases`` reads the same file and performs the
same operations against a fake network. Hosts are ``.test`` names and TEST-NET
addresses, and nothing here opens a socket.

To re-pin after a deliberate renderer change::

    CORTEXSIM_WRITE_WIRE_CASES=1 PYTHONPATH=core python -m pytest tests/eal_simulator/test_wire_plan.py
"""
from __future__ import annotations

import json
import os
import random
from pathlib import Path

import pytest

SPEC = Path(__file__).resolve().parents[2] / "spec" / "eal_wire_ops_cases.json"


def _render(case: dict) -> tuple[list[dict], list[str]]:
    from eal_simulator import get_default_registry, wire_plan
    from eal_simulator.safety import SafetyPolicy

    policy = SafetyPolicy(
        simulation_authorized=True, authorized_by="spec",
        target_allowlist=case["target_allowlist"], dry_run=False, c2_authorized=True)
    params = get_default_registry().get(case["plugin"]).validate_params(case["params"])
    ops = wire_plan.render(case["plugin"], params, wire_plan.WireContext(
        campaign_id=case["campaign_id"], simulation_run_id=case["simulation_run_id"],
        authorise=policy.authorise, verify_tls=case.get("verify_tls", False),
        rng=random.Random(case["seed"])))
    return ops, wire_plan.allowlist_for(ops)


def test_wire_plan_matches_spec_cases():
    from eal_simulator import wire_plan

    spec = json.loads(SPEC.read_text())
    if os.environ.get("CORTEXSIM_WRITE_WIRE_CASES"):
        for case in spec["cases"]:
            case["ops"], case["allowlist"] = _render(case)
        SPEC.write_text(json.dumps(spec, indent=2) + "\n")

    assert {c["plugin"] for c in spec["cases"]} == set(wire_plan.RENDERERS), (
        "every renderer needs at least one pinned case")
    for case in spec["cases"]:
        ops, allowlist = _render(case)
        assert ops == case["ops"], case["name"]
        assert allowlist == case["allowlist"], case["name"]


def test_a_destination_outside_the_campaign_allowlist_is_not_rendered():
    from eal_simulator.safety import SafetyError

    case = next(c for c in json.loads(SPEC.read_text())["cases"]
                if c["plugin"] == "stratum_tcp_connect")
    with pytest.raises(SafetyError):
        _render({**case, "target_allowlist": ["198.51.100.9"]})


def _sweep(**params):
    from eal_simulator import get_default_registry

    return get_default_registry().get("smb_rpc_sweep").validate_params(
        {"target_cidr": "10.20.0.0/16", **params})


def _context():
    from eal_simulator import wire_plan

    return wire_plan.WireContext(campaign_id="CMP-SPEC-001", simulation_run_id="cortexsim-spec",
                                 authorise=lambda *a, **k: None, rng=random.Random(1))


def test_wire_plan_stops_at_op_cap_while_rendering(monkeypatch):
    """The campaign text comes from a target. A /16 swept on 100 ports is
    4096 x 100 probes by its own params; none of them may be built just to
    find out there are too many."""
    from eal_simulator import wire_plan

    params = _sweep(ports=list(range(1, 101)), max_hosts=4096, inter_host_delay=0)

    # The arithmetic refuses it before the sweep loop builds anything.
    plan = wire_plan.Plan()
    with pytest.raises(wire_plan.WirePlanError) as refused:
        wire_plan.render("smb_rpc_sweep", params, _context(), plan)
    assert refused.value.code == "EAL_WIRE_PLAN_REFUSED"
    assert refused.value.built == 0 and plan.ops == []

    # Without that arithmetic the loop still cannot outgrow the cap: every
    # operation is added through the plan, which refuses the one past it.
    monkeypatch.setattr(wire_plan.Plan, "expect", lambda self, **kw: None)
    plan = wire_plan.Plan()
    with pytest.raises(wire_plan.WirePlanError) as refused:
        wire_plan.render("smb_rpc_sweep", params, _context(), plan)
    assert refused.value.built == len(plan.ops) == wire_plan.MAX_OPS


def test_a_sweep_takes_at_most_256_ports():
    from pydantic import ValidationError

    assert len(_sweep(ports=list(range(1, 257))).ports) == 256
    with pytest.raises(ValidationError):
        _sweep(ports=list(range(1, 258)))


@pytest.mark.parametrize("plugin, params, what", [
    ("c2_http_beacon", {"target_url": "http://c2.example.test/x", "iterations": 10_000,
                        "sleep_seconds": 0.1}, "operations"),
    ("c2_http_beacon", {"target_url": "http://c2.example.test/x", "iterations": 3,
                        "sleep_seconds": 86_400}, "sleep"),
    ("c2_http_beacon", {"target_url": "http://c2.example.test/x", "iterations": 100,
                        "sleep_seconds": 60}, "sleep in all"),
    ("c2_http_beacon", {"target_url": "http://c2.example.test/x", "iterations": 4097,
                        "sleep_seconds": 0.1, "method": "POST",
                        "body_size_bytes": 1_048_576}, "operations"),
    ("bulk_https_exfil", {"target_url": "https://x.example.test/u",
                          "total_bytes": 2 * 1024 ** 3, "request_count": 1}, "request body"),
    ("bulk_https_exfil", {"target_url": "https://x.example.test/u",
                          "total_bytes": 8 * 1024 ** 3, "request_count": 16}, "request body in all"),
    ("dns_tunnel_exfil", {"base_domain": "x.example.test", "chunks": 2000,
                          "sleep_seconds": 5}, "sleep in all"),
    ("stratum_tcp_connect", {"target_host": "192.0.2.1", "target_port": 3333,
                             "iterations": 200, "sleep_seconds": 600}, "sleep in all"),
    ("ftp_egress", {"target_host": "192.0.2.1", "iterations": 100,
                    "sleep_seconds": 600}, "sleep in all"),
    ("ssh_egress", {"target_host": "192.0.2.1", "iterations": 200,
                    "sleep_seconds": 600}, "sleep in all"),
    ("smb_rpc_sweep", {"target_cidr": "10.20.0.0/16", "max_hosts": 4096,
                       "ports": [445]}, "operations"),
])
def test_every_multiplying_parameter_is_refused_before_anything_is_built(plugin, params, what):
    """Each of these is valid for its plugin and far past what one step may be
    handed. The renderer's own arithmetic refuses it with nothing built."""
    from eal_simulator import get_default_registry, wire_plan

    validated = get_default_registry().get(plugin).validate_params(params)
    plan = wire_plan.Plan()
    with pytest.raises(wire_plan.WirePlanError) as refused:
        wire_plan.render(plugin, validated, _context(), plan)
    assert what in refused.value.detail
    assert refused.value.built == 0 and plan.ops == []


def test_one_plan_bounds_a_whole_campaign_not_each_step():
    """Two steps that each fit, on one plan, do not fit together."""
    from eal_simulator import get_default_registry, wire_plan

    step = get_default_registry().get("c2_http_beacon").validate_params(
        {"target_url": "http://c2.example.test/x", "iterations": 1500, "sleep_seconds": 0.1})
    plan = wire_plan.Plan()
    assert len(wire_plan.render("c2_http_beacon", step, _context(), plan)) == 2999
    with pytest.raises(wire_plan.WirePlanError):
        wire_plan.render("c2_http_beacon", step, _context(), plan)
    assert len(plan.ops) == 2999


def test_ssh_kexinit_frame_is_the_plugins_packet():
    """The renderer restates the plugin's packet builder so it can seed the
    random bytes. Everything that is not random must be the same."""
    from eal_simulator import wire_plan
    from eal_simulator.plugins.ssh_egress import SshEgress

    mine = wire_plan._ssh_kexinit(random.Random(1))
    theirs = SshEgress._build_kexinit_packet()
    assert len(mine) == len(theirs)
    # length(4) pad_len(1) type(1) cookie(16) | name-lists ... | padding
    pad = mine[4]
    assert mine[:6] == theirs[:6]
    assert mine[22:-pad] == theirs[22:-pad]


def test_c2_beacon_ops_are_the_requests_the_plugin_sends(monkeypatch):
    """Run the plugin itself against a recording transport and compare what it
    put on the wire with what the renderer plans."""
    import asyncio

    import httpx

    from eal_simulator import get_default_registry, wire_plan
    from eal_simulator.base import SimulationContext
    from eal_simulator.plugins import c2_http_beacon

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    real_client, real_sleep = httpx.AsyncClient, asyncio.sleep
    monkeypatch.setattr(
        c2_http_beacon.httpx, "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(c2_http_beacon.asyncio, "sleep", lambda _s: real_sleep(0))

    raw = {"target_url": "http://c2.example.test:8080/checkin", "iterations": 3,
           "sleep_seconds": 1, "method": "POST", "body_size_bytes": 64,
           "user_agents": ["CortexSimBeacon/1.0"]}
    plugin_cls = get_default_registry().get("c2_http_beacon")
    params = plugin_cls.validate_params(raw)

    async def _emit(_event):
        return None

    asyncio.run(plugin_cls().run(SimulationContext(
        campaign_id="CMP-SPEC-001", run_id="r", step_id="step-01",
        simulation_run_id="cortexsim-spec", dry_run=False,
        target_allowlist=["c2.example.test"], emit_event=_emit, params=params)))

    ops = [o for o in wire_plan.render("c2_http_beacon", params, wire_plan.WireContext(
        campaign_id="CMP-SPEC-001", simulation_run_id="cortexsim-spec",
        authorise=lambda *a, **k: None)) if o["op"] == "http"]

    assert len(ops) == len(seen) == 3
    for op, request in zip(ops, seen):
        assert op["method"] == request.method
        assert op["url"].split("?q=")[0] == str(request.url).split("?q=")[0]
        assert len(op["url"]) == len(str(request.url))  # same DGA token length
        assert op["body_random_bytes"] == len(request.content)
        for name, value in op["headers"].items():
            assert request.headers[name] == value
