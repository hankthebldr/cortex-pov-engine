"""D5 launch gate — the run-launch path refuses a Composer draft that is not
launchable (docs/superpowers/specs/2026-09-04-composer-workflow-design.md §6.3).

The gate lives in ``api.runs._draft_launch_gate`` and fires inside
``_launch_run_impl`` BEFORE ``orchestrator.launch`` seeds anything. It applies
ONLY to ``status='draft'`` rows:

  * an UNBOUND draft (``tc_ref='UNBOUND'``) → 409 ``DRAFT_NOT_TC_BOUND``;
  * a draft with a step missing a command / expected detection → 409
    ``DRAFT_CHAIN_INVALID`` naming the offending step id;
  * a draft bound to a REAL FY27 index test case with a valid chain launches
    (or reaches the next real gate);
  * a NON-draft (active / deprecated) row is untouched — the corpus launch path
    is unchanged, even with an UNBOUND-shaped ref.

Honesty doctrine (Gate A5): binding cannot be *proven* without the index
snapshot, so an absent snapshot resolves "not bound", never a permissive pass.
"""
from __future__ import annotations

import asyncio

import pytest


@pytest.fixture
def client(make_client):
    from api.runs import router, compat_router
    return make_client(router, compat_router)


def _seed(session_factory, **overrides):
    """Seed one Scenario ORM row, filling every nullable=False column. Overrides
    win. Returns the scenario_id."""
    from models import Scenario

    base = dict(
        scenario_id="SIM-DRAFT-gate",
        name="gate draft",
        version="0.0.0-draft",
        status="draft",
        plane="EDR",
        uc_ref="UNBOUND",
        uc_name="(unbound draft)",
        tc_ref="UNBOUND",
        tc_name="(unbound draft)",
        tc_refs=[],
        mitre_tactic="TA0000",
        mitre_tactic_name="Uncategorized",
        mitre_technique="T1003",
        mitre_technique_name="",
        push_supported=True,
        pull_supported=True,
        execution_identity={"default": "direct", "options": ["direct"]},
        steps=[
            {
                "id": "step-01",
                "name": "s",
                "identity": "direct",
                "command": "id",
                "mitre_technique": "T1003",
                "expected_detections": [
                    {"plane": "EDR", "type": "BIOC", "description": "d"}
                ],
            }
        ],
    )
    base.update(overrides)

    async def _do():
        async with session_factory() as db:
            db.add(Scenario(**base))
            await db.commit()

    asyncio.run(_do())
    return base["scenario_id"]


# ---------------------------------------------------------------------------
# DRAFT_NOT_TC_BOUND
# ---------------------------------------------------------------------------

def test_unbound_draft_refused_409(client, session_factory):
    """A draft with tc_ref='UNBOUND' but an otherwise valid chain is refused
    409 DRAFT_NOT_TC_BOUND — never launched, nothing seeded."""
    sid = _seed(session_factory)  # tc_ref/uc_ref default to UNBOUND
    r = client.post("/api/runs", json={"scenario_id": sid, "mode": "push"})
    assert r.status_code == 409, r.text
    body = r.json()["detail"]
    assert body["code"] == "DRAFT_NOT_TC_BOUND"
    assert body["detail"] == "bind a real test case from the UC/TC Index before launching"


def test_unbound_draft_refused_on_singular_alias_too(client, session_factory):
    """The compat alias POST /api/run enforces the same gate (one impl)."""
    sid = _seed(session_factory)
    r = client.post("/api/run", json={"scenario_id": sid, "mode": "push"})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "DRAFT_NOT_TC_BOUND"


# ---------------------------------------------------------------------------
# DRAFT_CHAIN_INVALID (checked before tc binding)
# ---------------------------------------------------------------------------

def test_draft_step_without_detection_is_chain_invalid(client, session_factory):
    """A step with no expected detection → 409 DRAFT_CHAIN_INVALID naming the
    offending step id. Chain validity is checked before tc binding."""
    sid = _seed(
        session_factory,
        scenario_id="SIM-DRAFT-nodet",
        steps=[
            {"id": "step-01", "name": "s", "identity": "direct",
             "command": "id", "mitre_technique": "T1003",
             "expected_detections": [{"plane": "EDR", "type": "BIOC", "description": "d"}]},
            {"id": "step-02", "name": "s2", "identity": "direct",
             "command": "whoami", "mitre_technique": "T1003",
             "expected_detections": []},
        ],
    )
    r = client.post("/api/runs", json={"scenario_id": sid, "mode": "push"})
    assert r.status_code == 409, r.text
    body = r.json()["detail"]
    assert body["code"] == "DRAFT_CHAIN_INVALID"
    assert "step-02" in body["detail"]


def test_draft_step_with_empty_command_is_chain_invalid(client, session_factory):
    sid = _seed(
        session_factory,
        scenario_id="SIM-DRAFT-nocmd",
        steps=[
            {"id": "step-01", "name": "s", "identity": "direct",
             "command": "", "mitre_technique": "T1003",
             "expected_detections": [{"plane": "EDR", "type": "BIOC", "description": "d"}]},
        ],
    )
    r = client.post("/api/runs", json={"scenario_id": sid, "mode": "push"})
    assert r.status_code == 409, r.text
    body = r.json()["detail"]
    assert body["code"] == "DRAFT_CHAIN_INVALID"
    assert "step-01" in body["detail"]


# ---------------------------------------------------------------------------
# Bound draft launches
# ---------------------------------------------------------------------------

def test_bound_draft_with_valid_chain_launches(client, session_factory):
    """A draft bound to a real FY27 index test case with a valid chain passes
    the gate and launches (push mode → 200, staged)."""
    import os
    from engine.uctc_registry import registry, default_index_dir

    n = registry.load(default_index_dir(os.environ.get("CORTEXSIM_BASE_DIR", ".")))
    if not registry.loaded or n == 0:
        pytest.skip("UC/TC index snapshot not available in this environment")

    tc_ref, tc = next(iter(registry._tc.items()))
    uc_ref = tc.ucs_id

    sid = _seed(
        session_factory,
        scenario_id="SIM-DRAFT-bound",
        uc_ref=uc_ref,
        uc_name="x",
        tc_ref=tc_ref,
        tc_name="y",
        tc_refs=[tc_ref],
    )
    r = client.post("/api/runs", json={"scenario_id": sid, "mode": "push"})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "push"
    assert "run_id" in r.json()


# ---------------------------------------------------------------------------
# NON-draft rows bypass the gate entirely
# ---------------------------------------------------------------------------

def test_active_scenario_bypasses_the_gate(client, session_factory):
    """An active corpus row launches even with an UNBOUND-shaped ref and an
    empty-detection step — the gate only touches drafts."""
    sid = _seed(
        session_factory,
        scenario_id="SIM-EDR-active",
        status="active",
        steps=[
            {"id": "step-01", "name": "s", "identity": "www-data",
             "command": "id", "expected_detections": []},
        ],
    )
    r = client.post("/api/runs", json={"scenario_id": sid, "mode": "push"})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "push"
