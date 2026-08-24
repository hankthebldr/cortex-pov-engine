"""Collector destination resolution + preflight.

Covers the "a DC should be able to see and set where signal is going without
reading Python" requirement: the resolver turns a campaign into an explicit,
validated destination list and flags the two mistakes that silently break a
live run (host outside the allowlist, pathless URL), and the preflight answers
"can this collector actually ingest?" in the delivery taxonomy.
"""
from __future__ import annotations

import asyncio
import ssl

import httpx
import pytest

from eal_simulator import Campaign
from eal_simulator.collector import (
    CollectorTarget,
    canary_record,
    preflight_collector,
    resolve_campaign_collectors,
    resolve_step_collector,
)
from eal_simulator.delivery import (
    COLLECTOR_TIMEOUT,
    COLLECTOR_TLS_ERROR,
    COLLECTOR_UNAUTHORIZED,
    COLLECTOR_UNREACHABLE,
)
from eal_simulator.registry import get_default_registry


_COLLECTOR = "https://collector.cortexsim-canary.invalid/logs/v1/event"


def _campaign(steps, allowlist=None, dry_run=True):
    spec = {
        "campaign_id": "CMP-COLL-001",
        "name": "collector resolution",
        "target_allowlist": allowlist if allowlist is not None
        else ["collector.cortexsim-canary.invalid"],
        "dry_run": dry_run,
        "steps": steps,
    }
    if not dry_run:
        spec["simulation_authorized"] = True
        spec["authorized_by"] = "tester@example.com"
    return Campaign.model_validate(spec)


def _k8s_step(step_id="step-01", collector_url=_COLLECTOR, **extra):
    params = {
        "collector_url": collector_url,
        "provider": "kubernetes",
        "event_pattern": "pod_exec",
        "iterations": 1,
    }
    params.update(extra)
    return {"step_id": step_id, "plugin": "k8s_audit_emitter", "params": params}


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


class TestResolveCampaignCollectors:
    def test_resolves_destination_for_a_log_streamer_step(self):
        payload = resolve_campaign_collectors(
            _campaign([_k8s_step()]), get_default_registry(),
        )
        assert payload["destinations"] == [_COLLECTOR]
        target = payload["collectors"][0]
        assert target["step_id"] == "step-01"
        assert target["host"] == "collector.cortexsim-canary.invalid"
        assert target["port"] == 443
        assert target["path"] == "/logs/v1/event"
        assert target["dataset"] == "kubernetes_audit_logs"
        assert target["allowlisted"] is True
        assert target["warnings"] == []

    def test_never_leaks_the_ingestion_token(self):
        payload = resolve_campaign_collectors(
            _campaign([_k8s_step(auth_token="super-secret-token")]),
            get_default_registry(),
        )
        target = payload["collectors"][0]
        assert target["has_auth_token"] is True
        assert "super-secret-token" not in str(payload)

    def test_host_outside_allowlist_is_flagged_before_launch(self):
        payload = resolve_campaign_collectors(
            _campaign([_k8s_step()], allowlist=["some.other.host"]),
            get_default_registry(),
        )
        assert payload["blocked_by_allowlist"] == ["step-01"]
        assert any("target_allowlist" in w for w in payload["warnings"])

    def test_cidr_allowlist_entry_covers_an_ip_collector(self):
        payload = resolve_campaign_collectors(
            _campaign(
                [_k8s_step(collector_url="http://10.0.0.9:8088/logs/v1/event")],
                allowlist=["10.0.0.0/24"],
            ),
            get_default_registry(),
        )
        target = payload["collectors"][0]
        assert target["allowlisted"] is True
        assert target["port"] == 8088

    def test_pathless_url_warns_about_the_ingestion_path(self):
        payload = resolve_campaign_collectors(
            _campaign([_k8s_step(collector_url="https://collector.cortexsim-canary.invalid")]),
            get_default_registry(),
        )
        assert any("no path" in w for w in payload["warnings"])

    def test_plaintext_http_to_a_remote_host_warns(self):
        payload = resolve_campaign_collectors(
            _campaign([_k8s_step(collector_url="http://collector.cortexsim-canary.invalid/logs")]),
            get_default_registry(),
        )
        assert any("plaintext http" in w for w in payload["warnings"])

    def test_localhost_broker_applet_does_not_warn_about_plaintext(self):
        payload = resolve_campaign_collectors(
            _campaign(
                [_k8s_step(collector_url="http://localhost:8088/logs/v1/event")],
                allowlist=["localhost"],
            ),
            get_default_registry(),
        )
        assert not any("plaintext http" in w for w in payload["warnings"])

    def test_non_collector_plugin_is_reported_not_dropped(self):
        payload = resolve_campaign_collectors(
            _campaign([{
                "step_id": "step-01", "plugin": "c2_http_beacon",
                "params": {"target_url": "http://testmynids.org/uid/index.html"},
            }]),
            get_default_registry(),
        )
        assert payload["collectors"] == []
        assert payload["unresolved_steps"][0]["reason"] == "no_collector"

    def test_unknown_plugin_and_bad_params_are_both_reported(self):
        payload = resolve_campaign_collectors(
            _campaign([
                {"step_id": "step-01", "plugin": "nope_plugin", "params": {}},
                _k8s_step(step_id="step-02", event_pattern="not_a_pattern"),
            ]),
            get_default_registry(),
        )
        reasons = {s["reason"] for s in payload["unresolved_steps"]}
        assert reasons == {"plugin_not_registered", "params_invalid"}

    def test_returns_none_for_params_without_a_collector_url(self):
        class _Params:
            target_url = "http://elsewhere.test"

        assert resolve_step_collector("step-01", "x", _Params()) is None


# --------------------------------------------------------------------------
# Preflight
# --------------------------------------------------------------------------


def _target(url=_COLLECTOR, **kw):
    defaults = dict(
        step_id="step-01", plugin="k8s_audit_emitter", url=url, scheme="https",
        host="collector.cortexsim-canary.invalid", port=443,
        path="/logs/v1/event", auth_header="Authorization", auth_scheme="Bearer",
        request_timeout=5.0, dataset="kubernetes_audit_logs",
    )
    defaults.update(kw)
    return CollectorTarget(**defaults)


class _StubClient:
    def __init__(self, status_code=None, raise_exc=None):
        self.status_code = status_code
        self.raise_exc = raise_exc
        self.requests = []
        self.closed = False

    async def post(self, url, *, headers=None, content=None):
        self.requests.append({"url": url, "headers": dict(headers or {}),
                              "content": content})
        if self.raise_exc is not None:
            raise self.raise_exc

        class _R:
            def __init__(self, code):
                self.status_code = code

        return _R(self.status_code)

    async def aclose(self):
        self.closed = True


def _factory(stub):
    return lambda timeout, verify: stub


class TestPreflightCollector:
    def test_accepting_collector_reports_delivered(self):
        stub = _StubClient(status_code=202)
        result = asyncio.run(preflight_collector(
            _target(), campaign_id="CMP-COLL-001", client_factory=_factory(stub),
        ))
        assert result["delivered"] is True
        assert result["reachable"] is True
        assert result["status_code"] == 202
        assert result["code"] is None
        assert stub.closed is True

    def test_canary_is_labelled_so_a_soc_can_discard_it(self):
        stub = _StubClient(status_code=202)
        asyncio.run(preflight_collector(
            _target(), campaign_id="CMP-COLL-001", client_factory=_factory(stub),
        ))
        import json

        body = json.loads(stub.requests[0]["content"])
        assert body["cortexsim_preflight"] is True
        assert body["dataset"] == "kubernetes_audit_logs"
        assert stub.requests[0]["headers"]["x-simulation-preflight"] == "1"

    def test_rejected_collector_is_reachable_but_not_delivering(self):
        stub = _StubClient(status_code=401)
        result = asyncio.run(preflight_collector(
            _target(), campaign_id="CMP-COLL-001", client_factory=_factory(stub),
        ))
        assert result["reachable"] is True
        assert result["delivered"] is False
        assert result["code"] == COLLECTOR_UNAUTHORIZED
        assert result["remediation"]

    @pytest.mark.parametrize("exc,expected", [
        (httpx.ConnectError("no route"), COLLECTOR_UNREACHABLE),
        (httpx.ReadTimeout("slow"), COLLECTOR_TIMEOUT),
        (ssl.SSLError("handshake"), COLLECTOR_TLS_ERROR),
    ])
    def test_transport_failures_are_classified_not_raised(self, exc, expected):
        stub = _StubClient(raise_exc=exc)
        result = asyncio.run(preflight_collector(
            _target(), campaign_id="CMP-COLL-001", client_factory=_factory(stub),
        ))
        assert result["reachable"] is False
        assert result["code"] == expected
        assert result["remediation"]
        assert stub.closed is True

    def test_auth_token_is_applied_under_the_configured_header(self):
        stub = _StubClient(status_code=202)
        asyncio.run(preflight_collector(
            _target(auth_header="X-Hec-Token", auth_scheme=""),
            campaign_id="CMP-COLL-001", auth_token="tok-123",
            client_factory=_factory(stub),
        ))
        assert stub.requests[0]["headers"]["X-Hec-Token"] == "tok-123"

    def test_canary_record_is_self_describing(self):
        record = canary_record("CMP-COLL-001")
        assert "discard" in record["message"].lower()
        assert record["cortexsim_campaign_id"] == "CMP-COLL-001"
