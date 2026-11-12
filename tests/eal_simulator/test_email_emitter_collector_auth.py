"""The EMAIL plane's ``email_emitter`` must authenticate to an XSIAM HTTP log collector.

``EmailEmitterParams`` had no ``auth_token`` / ``auth_header`` / ``auth_scheme``
fields, so pydantic dropped a supplied token and every POST went out without an
``Authorization`` header. An XSIAM collector answers 401, which ``delivery.py``
reports as ``collector_unauthorized``: the EMAIL scenarios could not reach a real
tenant at all.

The collector takes the bare API key (``Authorization: <api_key>``), so the
default scheme is empty, as it is for the analytics log-streamer emitters.
The token is an ingestion credential: it must appear on the client and nowhere
else. That means not in an audit event, not in a POST body, not in a
per-request header dict, and not in the offline bundle manifest.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from eal_simulator import AuditLogger, Campaign, CampaignExecutor
from eal_simulator.bundle import plan_campaign
from eal_simulator.plugins.email_emitter import EmailEmitter, EmailEmitterParams
from eal_simulator.registry import get_default_registry

_TOKEN = "xsiam-email-api-key-7c1e"
_COLLECTOR = "https://collector.cortexsim-canary.invalid/logs/v1/event"


def _params(**extra: Any) -> EmailEmitterParams:
    return EmailEmitterParams.model_validate({"collector_url": _COLLECTOR, **extra})


def _client_headers(params: EmailEmitterParams):
    plugin = EmailEmitter()
    client = plugin._build_client(params)
    try:
        return dict(client.headers)
    finally:
        asyncio.run(client.aclose())


class TestEmailEmitterSendsTheKey:
    def test_token_alone_is_sent_bare(self):
        params = _params(auth_token=_TOKEN)
        assert params.auth_scheme == ""
        assert params.auth_header == "Authorization"
        assert _client_headers(params)["authorization"] == _TOKEN

    def test_no_token_means_no_authorization_header(self):
        assert "authorization" not in _client_headers(_params())

    def test_explicit_scheme_and_header_are_honoured(self):
        headers = _client_headers(_params(
            auth_token=_TOKEN, auth_scheme="Bearer", auth_header="X-Ingest-Key",
        ))
        assert headers["x-ingest-key"] == f"Bearer {_TOKEN}"
        assert "authorization" not in headers

    def test_token_is_a_secret_on_the_params_model(self):
        p = _params(auth_token=_TOKEN)
        assert _TOKEN not in repr(p)
        assert _TOKEN not in str(p)
        assert _TOKEN not in p.model_dump_json()


class _CapturingAudit(AuditLogger):
    def __init__(self) -> None:
        super().__init__(file_path=None)
        self.events: list[dict[str, Any]] = []

    def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        super().emit(event)


class _RecordingClient:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def post(self, url, *, headers=None, content=None):
        self.requests.append({"url": url, "headers": dict(headers or {}), "content": content})

        class _R:
            status_code = 202
            headers = {"content-type": "application/json"}
            content = b"{}"
        return _R()

    async def aclose(self) -> None:
        return None


class TestTokenNeverLeaves:
    def _campaign(self, **step_params: Any) -> Campaign:
        return Campaign.model_validate({
            "campaign_id": "CMP-EMAIL-AUTH-001",
            "name": "email collector auth",
            "dry_run": False,
            "simulation_authorized": True,
            "authorized_by": "tester",
            "target_allowlist": ["collector.cortexsim-canary.invalid"],
            "steps": [{
                "step_id": "step-01",
                "plugin": "email_emitter",
                "params": {"collector_url": _COLLECTOR, "sleep_seconds": 0.0, **step_params},
            }],
        })

    def test_token_is_absent_from_events_bodies_and_request_headers(self, monkeypatch):
        stub = _RecordingClient()
        seen: dict[str, Any] = {}
        original = EmailEmitter._build_client

        def _capture(self, params):
            real = original(self, params)
            seen["authorization"] = real.headers.get("authorization")
            return stub

        monkeypatch.setattr(EmailEmitter, "_build_client", _capture)
        audit = _CapturingAudit()
        state = asyncio.run(CampaignExecutor(audit=audit).execute(
            self._campaign(event_pattern="phishing_link", auth_token=_TOKEN),
        ))

        assert state.step_results[0].status == "success"
        # The run authenticates...
        assert seen["authorization"] == _TOKEN
        # ...and the key is on the client only.
        assert stub.requests, "the stub collector received nothing"
        for req in stub.requests:
            assert _TOKEN not in json.dumps(req["headers"])
            assert _TOKEN.encode() not in req["content"]
        assert audit.events, "no audit events were emitted"
        assert _TOKEN not in json.dumps(audit.events, default=str)
        assert _TOKEN not in json.dumps(state.step_results[0].detail, default=str)

    def test_offline_bundle_manifest_names_an_env_var_not_the_token(self):
        plan = plan_campaign(
            Campaign.model_validate({
                "campaign_id": "CMP-EMAIL-BUNDLE-001",
                "name": "email offline bundle",
                "target_allowlist": ["collector.cortexsim-canary.invalid"],
                "dry_run": True,
                "steps": [{
                    "step_id": "step-01",
                    "plugin": "email_emitter",
                    "params": {"collector_url": _COLLECTOR, "auth_token": _TOKEN},
                }],
            }),
            get_default_registry(),
        )
        manifest = json.dumps(plan["manifest"])
        assert _TOKEN not in manifest
        assert _TOKEN not in json.dumps(plan["records"])
        step = plan["manifest"]["steps"][0]
        assert step["auth_header"] == "Authorization"
        assert step["auth_scheme"] == ""
        assert step["auth_token_env"] == "CORTEXSIM_COLLECTOR_TOKEN_STEP_01"
