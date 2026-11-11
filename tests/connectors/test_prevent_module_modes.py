"""Per-module Action Mode through every grading surface (review F1).

The reconcile funnel, the silent-row judge, host-evidence seeding and the
Tier-2 verifier each used to read the run-level ``agent_profile_mode`` alone.
A block-mode run whose ``btp`` module the operator attested Report must not
grade a Detected BTP alert as ``DETECTED_NOT_PREVENTED`` on any of them.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from tests.connectors.test_measurement_service import (
    _alert,
    _prepare_prevention,
    _reconcile,
    _results_by_step,
    _tenant,
)
from tests.connectors.test_verification_service import _seed

pytestmark = pytest.mark.usefixtures("non_utc_host")


def _detected_alert(ext="d1", *, technique="T1003.001"):
    a = _alert(ext, technique=technique)
    a["action"] = "REPORTED"
    a["action_pretty"] = "Detected (Reported)"
    return a


async def _attest(SessionLocal, module_modes, *, module="btp"):
    from models import Result, Run
    from sqlalchemy import select

    async with SessionLocal() as db:
        run = (await db.execute(select(Run))).scalars().first()
        run.module_modes = module_modes
        for r in (await db.execute(select(Result))).scalars().all():
            if r.step_id == "step-02":
                r.prevention_module = module
        await db.commit()


def _detected_in_block_run(session_factory, module_modes):
    _seed(session_factory, results=2)
    asyncio.run(_prepare_prevention(session_factory, termination="exited"))
    asyncio.run(_attest(session_factory, module_modes))
    fetcher, _ = _tenant([_detected_alert("d1")])
    asyncio.run(_reconcile(session_factory, fetcher))
    return asyncio.run(_results_by_step(session_factory))["step-02"]


def test_detected_alert_from_report_module_is_not_a_prevention_fail(session_factory):
    """The F1 guard: without the fix this row reads fail / DETECTED_NOT_PREVENTED."""
    r = _detected_in_block_run(session_factory, {"btp": "report"})
    assert r.outcome == "detected"
    assert r.outcome_reason == "MODULE_NOT_IN_BLOCK"
    assert r.kpi_verdict != "fail"


def test_detected_alert_from_block_module_still_fails(session_factory):
    """Block attested, or another module attested and this one not: still a fail."""
    r = _detected_in_block_run(session_factory, {"btp": "block"})
    assert r.outcome == "detected"
    assert r.outcome_reason == "DETECTED_NOT_PREVENTED"
    assert r.kpi_verdict == "fail"


# --- host evidence --------------------------------------------------------

def _host_rows(module_modes, termination="signaled"):
    from engine.host_evidence import apply_host_outcomes

    r = SimpleNamespace(step_id="step-01", expected_outcome="prevent",
                        prevention_module="btp", outcome=None, outcome_reason=None,
                        kpi_verdict=None)
    s = SimpleNamespace(step_id="step-01", termination=termination, reason=None)
    apply_host_outcomes([r], [s], agent_profile_mode="block", module_modes=module_modes)
    return r


def test_host_kill_seeds_pending_for_block_module():
    r = _host_rows({"btp": "block"})
    assert (r.outcome, r.outcome_reason) == ("pending", "PREVENTION_UNCONFIRMED_NO_ALERT")


@pytest.mark.parametrize("mode", ["report", "disabled"])
def test_host_kill_is_not_this_modules_prevention_when_not_in_block(mode):
    """A kill on a Report/Disabled module's step came from somewhere else."""
    r = _host_rows({"btp": mode})
    assert r.outcome is None and r.outcome_reason is None


# --- Tier-2 verifier --------------------------------------------------------

def _verify(module_modes):
    from engine.verifier import verify_results

    r = SimpleNamespace(id=1, detection_id="d", expected_outcome="prevent",
                        prevention_module="btp", verification_xql="dataset = x",
                        outcome=None, outcome_reason=None, kpi_verdict="pending",
                        kpi_contribution=None, verified_at=None)
    calls = []

    async def runner(xql):
        calls.append(xql)
        return 1
    counts = asyncio.run(verify_results([r], runner, agent_profile_mode="block",
                                        module_modes=module_modes))
    return counts, calls


def test_verifier_skips_a_block_module_prevent_row():
    """A row count proves an alert exists, never that the agent blocked."""
    counts, calls = _verify({"btp": "block"})
    assert counts["skipped"] == 1 and calls == []


def test_verifier_counts_a_report_module_prevent_row_as_a_detect():
    """Report module: the row is scored as a detect, so a row count is evidence."""
    counts, calls = _verify({"btp": "report"})
    assert calls == ["dataset = x"] and counts["skipped"] == 0
