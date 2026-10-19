"""A port-pinned allowlist entry scopes collector POSTs to that port.

``safety.py`` documents port pins as a safety feature: ``host:8088`` must
authorise THAT port only, so a campaign scoped to a Broker VM's HTTP applet
cannot also POST to the same host's 443. The collector-POST spine called
``ctx.authorise(host)`` without the port the URL already names, and
``_port_ok(None, {8088})`` is True — so every pinned entry authorised every
port. The advisory resolver erred the other way: it never parsed ``host:port``
entries, so ``host:8088`` did not even match a collector ON 8088 and
``GET /collectors`` listed it under ``blocked_by_allowlist`` while the live run
accepted it. Both now ask the real ``SafetyPolicy`` with the URL's port.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from eal_simulator import AuditLogger, Campaign, CampaignExecutor
from eal_simulator.collector import resolve_campaign_collectors
from eal_simulator.plugins.k8s_audit_emitter import K8sAuditEmitter
from eal_simulator.registry import get_default_registry

_HOST = "broker.cortexsim-canary.invalid"


class _Stub:
    def __init__(self):
        self.requests: list[dict[str, Any]] = []

    async def post(self, url, *, headers=None, content=None):
        self.requests.append({"url": url})

        class _R:
            status_code = 202
            headers = {"content-type": "application/json"}
            content = b"{}"

        return _R()

    async def aclose(self):
        return None


def _campaign(url, allowlist, *, dry_run=False):
    spec = {
        "campaign_id": "CMP-PORTPIN-001",
        "name": "port-pinned collector",
        "dry_run": dry_run,
        "target_allowlist": allowlist,
        "steps": [{
            "step_id": "step-01",
            "plugin": "k8s_audit_emitter",
            "params": {"collector_url": url, "provider": "kubernetes",
                       "event_pattern": "pod_exec"},
        }],
    }
    if not dry_run:
        spec.update({"simulation_authorized": True, "authorized_by": "tester"})
    return Campaign.model_validate(spec)


def _run(monkeypatch, campaign):
    stub = _Stub()
    monkeypatch.setattr(K8sAuditEmitter, "_build_client", lambda self, params: stub)
    state = asyncio.run(
        CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
    return state.step_results[0], stub


@pytest.mark.parametrize("url", [
    f"https://{_HOST}/logs/v1/event",          # implicit :443
    f"http://{_HOST}/logs/v1/event",           # implicit :80
    f"https://{_HOST}:9443/logs/v1/event",     # explicit other port
])
def test_a_pinned_entry_refuses_other_ports_before_any_post(monkeypatch, url):
    step, stub = _run(monkeypatch, _campaign(url, [f"{_HOST}:8088"]))
    assert stub.requests == [], f"POSTed to {url} under an allowlist pinned to :8088"
    assert step.status == "error"
    assert step.detail.get("failure_code") == "target_not_authorised"


def test_the_pinned_port_itself_is_authorised(monkeypatch):
    step, stub = _run(monkeypatch, _campaign(
        f"http://{_HOST}:8088/logs/v1/event", [f"{_HOST}:8088"]))
    assert step.status == "success"
    assert stub.requests


def test_a_host_level_entry_still_authorises_any_port(monkeypatch):
    step, stub = _run(monkeypatch, _campaign(
        f"https://{_HOST}:9443/logs/v1/event", [_HOST]))
    assert step.status == "success"
    assert stub.requests


@pytest.mark.parametrize("url,allowed", [
    (f"http://{_HOST}:8088/logs/v1/event", True),
    (f"https://{_HOST}/logs/v1/event", False),
])
def test_the_advisory_agrees_with_the_live_policy(url, allowed):
    payload = resolve_campaign_collectors(
        _campaign(url, [f"{_HOST}:8088"], dry_run=True), get_default_registry())
    assert payload["collectors"][0]["allowlisted"] is allowed
    assert (payload["blocked_by_allowlist"] == ["step-01"]) is (not allowed)

