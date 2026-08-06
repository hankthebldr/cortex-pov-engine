"""A missing consent flag is a precondition, not a malformed request.

Observed on a live SimCore, launching SIM-EDR-001 in pull mode:

    HTTP 422
    {"detail": {"error": "Scenario uses dual-use adapter 'TOOL-ATOMIC-RED-TEAM'
                         (Atomic Red Team vmaster) but consent
                         simulation_authorized is not set. Re-launch with
                         consent.simulation_authorized=true to proceed.",
                "code": "LAUNCH_FAILED", "detail": ""}}

Three problems in one response:

  * **422** says the body was malformed. It was not — it was a perfectly valid
    request for an action the operator has not authorised. A DC reads 422 and
    goes looking at their JSON.
  * **LAUNCH_FAILED** is the generic code, so the only machine-actionable part
    of the answer (WHICH consent key) lives in prose a console has to regex.
  * The gate returned at the **first** unmet requirement, so a scenario binding
    two gated adapters cost three round trips to discover two facts SimCore
    knew before the first one.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def _scn(tools):
    return SimpleNamespace(
        scenario_id="SIM-TEST-001", external_tools=tools, cluster_posture=None,
    )


def _adapter(name, safety, version="1.0"):
    return SimpleNamespace(
        name=name, version=version, safety_class=safety,
        cleanup=SimpleNamespace(commands=["true"]),
    )


# ---------------------------------------------------------------------------
# The refusal carries structure without breaking the prose contract
# ---------------------------------------------------------------------------


def test_refusal_is_still_a_string_for_every_existing_caller(monkeypatch):
    """LaunchRefusal subclasses str on purpose — the gate's contract has always
    been 'prose or None' and the whole tree depends on it."""
    from engine.orchestrator import _check_launch_consent
    from tools import adapter_catalog

    monkeypatch.setattr(
        adapter_catalog.catalog, "find",
        lambda ref: _adapter("Atomic Red Team", "dual-use-lab-only"),
    )
    err = _check_launch_consent(_scn([{"adapter_ref": "TOOL-ART"}]), {}, mode="pull")
    assert isinstance(err, str)
    assert "simulation_authorized" in err
    assert err.code == "CONSENT_REQUIRED"


def test_all_unmet_consents_are_collected_in_one_pass(monkeypatch):
    """One round trip, not three. A scenario binding a dual-use adapter AND a
    C2 framework must report both keys the first time it is refused."""
    from engine.orchestrator import _check_launch_consent
    from tools import adapter_catalog

    catalog_map = {
        "TOOL-ART": _adapter("Atomic Red Team", "dual-use-lab-only"),
        "TOOL-SLIVER": _adapter("Sliver", "c2-framework", "1.5"),
    }
    monkeypatch.setattr(adapter_catalog.catalog, "find", catalog_map.get)

    err = _check_launch_consent(
        _scn([{"adapter_ref": "TOOL-ART"}, {"adapter_ref": "TOOL-SLIVER"}]),
        {}, mode="pull",
    )
    assert err.code == "CONSENT_REQUIRED"
    assert err.detail["required_consent"] == ["simulation_authorized", "c2_authorized"]
    assert [r["adapter_id"] for r in err.detail["reasons"]] == \
        ["TOOL-ART", "TOOL-SLIVER"]
    for reason in err.detail["reasons"]:
        assert reason["name"] and reason["safety_class"] and reason["required_consent"]
    # The prose stays readable and admits there is more.
    assert "1 further gated adapter" in err


def test_partial_consent_reports_only_what_is_still_missing(monkeypatch):
    from engine.orchestrator import _check_launch_consent
    from tools import adapter_catalog

    catalog_map = {
        "TOOL-ART": _adapter("Atomic Red Team", "dual-use-lab-only"),
        "TOOL-SLIVER": _adapter("Sliver", "c2-framework", "1.5"),
    }
    monkeypatch.setattr(adapter_catalog.catalog, "find", catalog_map.get)

    err = _check_launch_consent(
        _scn([{"adapter_ref": "TOOL-ART"}, {"adapter_ref": "TOOL-SLIVER"}]),
        {"simulation_authorized": True}, mode="pull",
    )
    assert err.detail["required_consent"] == ["c2_authorized"]
    assert len(err.detail["reasons"]) == 1


def test_full_consent_clears_the_gate(monkeypatch):
    from engine.orchestrator import _check_launch_consent
    from tools import adapter_catalog

    monkeypatch.setattr(
        adapter_catalog.catalog, "find",
        lambda ref: _adapter("Atomic Red Team", "dual-use-lab-only"),
    )
    assert _check_launch_consent(
        _scn([{"adapter_ref": "TOOL-ART"}]),
        {"simulation_authorized": True}, mode="pull",
    ) is None


def test_a_destructive_adapter_without_cleanup_is_not_a_consent_problem(monkeypatch):
    """No key the operator can set fixes it, so it must not be presented as a
    checkbox — that is how an operator learns the flags are ceremony."""
    from engine.orchestrator import _check_launch_consent
    from tools import adapter_catalog

    broken = SimpleNamespace(
        name="Wiper", version="1.0", safety_class="destructive", cleanup=None,
    )
    monkeypatch.setattr(adapter_catalog.catalog, "find", lambda ref: broken)

    err = _check_launch_consent(_scn([{"adapter_ref": "TOOL-WIPER"}]), {}, mode="pull")
    assert err.code == "ADAPTER_MISSING_CLEANUP"
    assert "consent" not in str(err).lower()


# ---------------------------------------------------------------------------
# HTTP status: 409, not 422
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code,expected", [
    ("CONSENT_REQUIRED", 409),
    ("ADAPTER_MISSING_CLEANUP", 409),
    ("LAUNCH_FAILED", 422),
])
def test_precondition_codes_map_to_409(code, expected):
    from api.runs import _LAUNCH_PRECONDITION_CODES

    assert (409 if code in _LAUNCH_PRECONDITION_CODES else 422) == expected


@pytest.mark.asyncio
async def test_launch_endpoint_returns_409_with_the_structured_detail(monkeypatch):
    from fastapi import HTTPException

    from api.runs import LaunchRequest, _launch_run_impl
    from engine import orchestrator as orch_mod

    async def _fake_launch(**kwargs):
        return orch_mod.LaunchResult(
            success=False,
            error="Scenario uses dual-use adapter 'TOOL-ART' ...",
            error_code="CONSENT_REQUIRED",
            error_detail={
                "required_consent": ["simulation_authorized"],
                "reasons": [{"adapter_id": "TOOL-ART", "name": "Atomic Red Team",
                             "safety_class": "dual-use-lab-only",
                             "required_consent": "simulation_authorized"}],
            },
        )

    monkeypatch.setattr(orch_mod.orchestrator, "launch", _fake_launch)

    with pytest.raises(HTTPException) as caught:
        await _launch_run_impl(
            LaunchRequest(scenario_id="SIM-EDR-001", mode="pull"), db=None,
        )
    exc = caught.value
    assert exc.status_code == 409
    assert exc.detail["code"] == "CONSENT_REQUIRED"
    assert exc.detail["detail"]["required_consent"] == ["simulation_authorized"]
    assert exc.detail["detail"]["reasons"][0]["adapter_id"] == "TOOL-ART"


@pytest.mark.asyncio
async def test_an_ordinary_launch_failure_keeps_422(monkeypatch):
    """Only preconditions move. 'Scenario not found' is still a 422 with the
    historical LAUNCH_FAILED envelope."""
    from fastapi import HTTPException

    from api.runs import LaunchRequest, _launch_run_impl
    from engine import orchestrator as orch_mod

    async def _fake_launch(**kwargs):
        return orch_mod.LaunchResult(success=False, error="Scenario 'X' not found")

    monkeypatch.setattr(orch_mod.orchestrator, "launch", _fake_launch)

    with pytest.raises(HTTPException) as caught:
        await _launch_run_impl(LaunchRequest(scenario_id="X", mode="pull"), db=None)
    assert caught.value.status_code == 422
    assert caught.value.detail["code"] == "LAUNCH_FAILED"
