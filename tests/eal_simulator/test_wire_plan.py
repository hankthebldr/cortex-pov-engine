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
