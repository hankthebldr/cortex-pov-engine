"""Pure canary derivation: scenario lookup and block-mode status."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from engine import canary


def _run(status="complete", completed=True):
    return SimpleNamespace(
        status=status, completed_at=datetime.utcnow() if completed else None
    )


def _res(outcome):
    return SimpleNamespace(outcome=outcome)


def test_block_mode_status_derivation():
    assert canary.block_mode_status_from(None, []) == "canary_not_available"
    assert canary.block_mode_status_from(_run(), [_res("prevented")]) == "block_mode_confirmed"
    assert canary.block_mode_status_from(_run(), [_res("pending"), _res("missed")]) == "block_mode_unconfirmed"
    assert canary.block_mode_status_from(_run("failed"), []) == "block_mode_unconfirmed"


def test_canary_scenario_id_reads_config(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "CORTEXSIM_CANARY_SCENARIO_LINUX", "")
    assert canary.canary_scenario_id("linux") is None
    monkeypatch.setattr(settings, "CORTEXSIM_CANARY_SCENARIO_LINUX", "SIM-CANARY-1")
    assert canary.canary_scenario_id("linux") == "SIM-CANARY-1"
    assert canary.canary_scenario_id("windows") is None
