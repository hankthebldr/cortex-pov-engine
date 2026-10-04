"""A requested negative control is never silently turned into a positive one.

``AnalyticsEmitterParams`` inherits pydantic's default ``extra='ignore'``. Most
analytics emitters' params subclass it directly and do not declare
``negative_control``, so ``{"negative_control": true}`` validated cleanly, the
key was DROPPED, and ``records_for`` emitted the POSITIVE records. The refusal
in ``records_for`` only ever saw ``NegativeControlEmitterParams`` models.

The consequence is a false claim in a POV: the operator asks for the record
that must NOT fire, the detector fires on the positive record that was sent
instead, and the readout says "the detector fires on anything".
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from eal_simulator import AuditLogger, Campaign, CampaignExecutor
from eal_simulator.analytics_emitter import (
    AnalyticsEmitterParams,
    NegativeControlEmitterParams,
)
from eal_simulator.plugins.k8s_audit_emitter import K8sAuditEmitter
from eal_simulator.registry import get_default_registry


_COLLECTOR = "https://collector.cortexsim-canary.invalid/logs/v1/event"


def _undeclared_emitters():
    reg = get_default_registry()
    out = []
    for name in sorted(reg.names()):
        model = reg.get(name).Meta.params_model
        if issubclass(model, AnalyticsEmitterParams) and not issubclass(
            model, NegativeControlEmitterParams
        ):
            out.append(name)
    return out


def test_the_population_under_test_is_not_empty():
    # A zero here would make the parametrised test below vacuously green.
    assert len(_undeclared_emitters()) >= 5


@pytest.mark.parametrize("plugin", _undeclared_emitters())
def test_params_refuse_negative_control_they_cannot_honour(plugin):
    cls = get_default_registry().get(plugin)
    with pytest.raises(Exception) as exc:
        cls.validate_params({"collector_url": _COLLECTOR, "negative_control": True})
    assert "negative_control" in str(exc.value)


@pytest.mark.parametrize("plugin", _undeclared_emitters())
def test_omitting_or_disabling_it_is_unchanged(plugin):
    cls = get_default_registry().get(plugin)
    base = {"collector_url": _COLLECTOR}
    cls.validate_params(base)
    cls.validate_params({**base, "negative_control": False})


class _RecordingClient:
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


def test_a_live_campaign_refuses_the_step_and_sends_nothing(monkeypatch):
    stub = _RecordingClient()
    monkeypatch.setattr(K8sAuditEmitter, "_build_client", lambda self, params: stub)
    campaign = Campaign.model_validate({
        "campaign_id": "CMP-NEGCTL-001",
        "name": "negative control on an emitter without one",
        "dry_run": False,
        "simulation_authorized": True,
        "authorized_by": "tester",
        "target_allowlist": ["collector.cortexsim-canary.invalid"],
        "steps": [{
            "step_id": "step-01",
            "plugin": "k8s_audit_emitter",
            "params": {
                "collector_url": _COLLECTOR,
                "provider": "kubernetes",
                "event_pattern": "pod_exec",
                "negative_control": True,
            },
        }],
    })
    state = asyncio.run(
        CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
    step = state.step_results[0]
    assert stub.requests == [], (
        f"{len(stub.requests)} POSITIVE record(s) were sent for a negative-control request"
    )
    assert step.status == "error"
    assert step.error.startswith("params_invalid")
    assert step.detail["failure_code"] == "plugin_misconfigured"
