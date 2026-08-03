"""The launch-time consent gate — one entry point, two clauses.

``_check_launch_consent`` is THE place a launch's authorization is decided. It
composes the pre-existing tool-adapter clause with a cluster-posture clause that
delegates to ``engine.k8s_manifest.check_cluster_consent`` — the same authority
the generation gate in ``core/api/scenarios.py`` calls. There is deliberately no
second implementation of the decision.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


POSTURE_TIER1 = {
    "app_identity": {"name": "ci-runner"},
    "service_account": {
        "automount_token": True,
        "cluster_role_rules": [
            {"api_groups": ["*"], "resources": ["*"], "verbs": ["*"]},
        ],
    },
}

POSTURE_TIER2 = {
    "app_identity": {"name": "ci-runner"},
    "security_context": {"privileged": True},
    "host_access": {
        "host_pid": True,
        "host_paths": [{
            "name": "hostroot", "host_path": "/", "mount_path": "/host",
            "type": "Directory", "read_only": True,
        }],
    },
}

POSTURE_BASELINE = {"app_identity": {"name": "ci-runner"}}


def _scn(posture=None, *, scenario_id="SIM-CDR-999", external_tools=None):
    """Stand-in for the Scenario ORM row.

    The ``cluster_posture`` column is owned by another track; the clause reads
    the attribute, so a stand-in exercises exactly the code path a real row will
    take the day the column lands.
    """
    ns = SimpleNamespace(
        scenario_id=scenario_id,
        external_tools=external_tools or [],
    )
    if posture is not None:
        ns.cluster_posture = posture
    return ns


@pytest.fixture
def kill_switch(monkeypatch):
    def _set(value: bool):
        monkeypatch.setattr("engine.orchestrator.allow_privileged_k8s", lambda: value)
    _set(False)
    return _set


# ===========================================================================
# 1 · The deployment kill switch
# ===========================================================================

def test_kill_switch_defaults_off(monkeypatch):
    """A shared or demo SimCore must not emit node-breakout bytes out of the box."""
    from engine.orchestrator import allow_privileged_k8s  # noqa: PLC0415
    from config import settings  # noqa: PLC0415

    monkeypatch.delenv("CORTEXSIM_ALLOW_PRIVILEGED_K8S", raising=False)
    if hasattr(settings, "CORTEXSIM_ALLOW_PRIVILEGED_K8S"):
        monkeypatch.setattr(settings, "CORTEXSIM_ALLOW_PRIVILEGED_K8S", False)
    assert allow_privileged_k8s() is False


@pytest.mark.parametrize("raw,expected", [
    ("true", True), ("TRUE", True), ("1", True), ("yes", True), ("on", True),
    ("false", False), ("0", False), ("", False), ("maybe", False),
])
def test_kill_switch_env_parsing(monkeypatch, raw, expected):
    from engine.orchestrator import allow_privileged_k8s  # noqa: PLC0415
    from config import settings  # noqa: PLC0415

    if hasattr(settings, "CORTEXSIM_ALLOW_PRIVILEGED_K8S"):
        pytest.skip("Settings owns the flag; the env fallback is no longer the path")
    monkeypatch.setenv("CORTEXSIM_ALLOW_PRIVILEGED_K8S", raw)
    assert allow_privileged_k8s() is expected


# ===========================================================================
# 2 · The adapter clause still decides what it always decided
# ===========================================================================

def test_adapter_clause_still_refuses_through_the_new_entry_point(monkeypatch):
    """Extending the gate must not weaken it. A c2-framework adapter is still
    refused when the launch goes through _check_launch_consent."""
    from engine.orchestrator import _check_launch_consent  # noqa: PLC0415
    from tools import adapter_catalog  # noqa: PLC0415

    adapter = SimpleNamespace(
        name="Sliver", version="1.5", safety_class="c2-framework", cleanup=None,
    )
    monkeypatch.setattr(adapter_catalog.catalog, "find", lambda ref: adapter)
    scn = _scn(external_tools=[{"adapter_ref": "TOOL-SLIVER"}])

    err = _check_launch_consent(scn, {}, mode="pull")
    assert err is not None and "c2_authorized" in err
    assert _check_launch_consent(scn, {"c2_authorized": True}, mode="pull") is None


def test_scenario_with_neither_adapters_nor_posture_passes():
    from engine.orchestrator import _check_launch_consent  # noqa: PLC0415

    assert _check_launch_consent(_scn(), {}, mode="push") is None


# ===========================================================================
# 3 · The cluster clause
# ===========================================================================

def test_push_launch_of_a_gated_scenario_is_refused_actionably(kill_switch):
    from engine.orchestrator import _check_launch_consent  # noqa: PLC0415

    err = _check_launch_consent(_scn(POSTURE_TIER1), {}, mode="push")
    assert err is not None
    # names the capability, the object it lands on, and the literal key
    assert "clusterrole-wildcard-verbs on the clusterrole" in err
    assert "consent.cluster_privilege_authorized=true" in err
    # and is honest that a launch does not authorise the artifact
    assert "POST /api/scenarios/SIM-CDR-999/bundle" in err
    assert "docs/reference/k8s-delivery.md" in err


def test_push_launch_with_the_right_consent_is_cleared(kill_switch):
    from engine.orchestrator import _check_launch_consent  # noqa: PLC0415

    assert _check_launch_consent(
        _scn(POSTURE_TIER1), {"cluster_privilege_authorized": True}, mode="push",
    ) is None


def test_pull_launch_is_not_gated_on_cluster_posture(kill_switch):
    """Pull dispatches to an enrolled beacon and emits no manifest. Gating it
    would fire the prompt on a case it does not describe, which is how an
    operator learns the flags are ceremony."""
    from engine.orchestrator import _check_launch_consent  # noqa: PLC0415

    assert _check_launch_consent(_scn(POSTURE_TIER2), {}, mode="pull") is None


def test_baseline_posture_does_not_gate_a_push_launch(kill_switch):
    from engine.orchestrator import _check_launch_consent  # noqa: PLC0415

    assert _check_launch_consent(_scn(POSTURE_BASELINE), {}, mode="push") is None


def test_kill_switch_refusal_points_at_the_env_var_not_a_consent_key(kill_switch):
    """The fix is a different action by a different person; saying 'add a
    consent key' when the fix is a deployment flag sends the DC to the wrong
    file."""
    from engine.orchestrator import _check_launch_consent  # noqa: PLC0415

    kill_switch(False)
    err = _check_launch_consent(
        _scn(POSTURE_TIER2), {"node_access_authorized": True}, mode="push",
    )
    assert err is not None
    assert "CORTEXSIM_ALLOW_PRIVILEGED_K8S is false" in err
    assert "no consent key will change it" in err
    assert "Re-launch with consent." not in err


def test_node_access_clears_only_with_both_switch_and_consent(kill_switch):
    from engine.orchestrator import _check_launch_consent  # noqa: PLC0415

    kill_switch(True)
    assert _check_launch_consent(_scn(POSTURE_TIER2), {}, mode="push") is not None
    assert _check_launch_consent(
        _scn(POSTURE_TIER2), {"node_access_authorized": True}, mode="push",
    ) is None


@pytest.mark.parametrize("posture,granted,expected_key", [
    (POSTURE_TIER2, "cluster_privilege_authorized", "node_access_authorized"),
    (POSTURE_TIER1, "node_access_authorized", "cluster_privilege_authorized"),
])
def test_the_two_keys_are_orthogonal(kill_switch, posture, granted, expected_key):
    """Not nested. A wildcard-RBAC manifest touches no node; a privileged
    manifest needs no RBAC. Requiring both for either forces the harder approval
    onto the easier case."""
    from engine.orchestrator import _check_launch_consent  # noqa: PLC0415

    kill_switch(True)
    err = _check_launch_consent(_scn(posture), {granted: True}, mode="push")
    assert err is not None
    assert f"consent {expected_key} is not set" in err


def test_a_posture_no_consent_key_unlocks_is_refused_at_launch(kill_switch):
    from engine.orchestrator import _check_launch_consent  # noqa: PLC0415

    kill_switch(True)
    err = _check_launch_consent(
        _scn({**POSTURE_TIER2, "namespace": "kube-system"}),
        {"node_access_authorized": True, "cluster_privilege_authorized": True},
        mode="push",
    )
    assert err is not None
    assert "refuses regardless of consent" in err


# ===========================================================================
# 4 · Wiring — the gate is actually on the launch path
# ===========================================================================

@pytest.fixture
def session_factory() -> async_sessionmaker:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _init() -> None:
        from database import Base
        import models  # noqa: F401

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    return SessionLocal


async def _seed(db, scenario_id="SIM-CDR-999"):
    from models import Scenario  # noqa: PLC0415

    db.add(Scenario(
        scenario_id=scenario_id, name="T", version="1.0", status="active", plane="CDR",
        uc_ref="UCS-CDR-01", uc_name="x", tc_ref="TC-CDR-01", tc_name="y",
        mitre_tactic="TA0004", mitre_tactic_name="Privilege Escalation",
        mitre_technique="T1611", mitre_technique_name="Escape to Host",
        execution_identity={"default": "root"},
        push_supported=True, pull_supported=True,
        steps=[{"id": "step-01", "name": "s", "command": "id", "expected_detections": []}],
    ))
    await db.commit()


def test_launch_routes_through_the_gate_and_creates_no_run_when_refused(
    session_factory, monkeypatch,
):
    """A refusal must abort before the Run record exists — otherwise the POV
    report carries a run that never had authorization behind it."""
    from engine import orchestrator as orch  # noqa: PLC0415
    from models import Run  # noqa: PLC0415

    seen = {}

    def _fake(scenario, consent, *, mode=None):
        seen["mode"] = mode
        seen["consent"] = consent
        return "refused: node_access_authorized is not set"

    monkeypatch.setattr(orch, "_check_launch_consent", _fake)

    async def _go():
        async with session_factory() as db:
            await _seed(db)
            result = await orch.Orchestrator().launch(
                scenario_id="SIM-CDR-999", mode="push", db=db,
                consent={"node_access_authorized": False},
            )
            rows = (await db.execute(select(Run))).scalars().all()
            return result, rows

    result, rows = asyncio.run(_go())
    assert result.success is False
    assert "node_access_authorized" in result.error
    assert rows == []
    # the clause needs the mode to scope itself to push
    assert seen["mode"] == "push"
    assert seen["consent"] == {"node_access_authorized": False}


def test_launch_passes_the_mode_through_on_the_pull_path(session_factory, monkeypatch):
    from engine import orchestrator as orch  # noqa: PLC0415

    seen = {}

    def _fake(scenario, consent, *, mode=None):
        seen["mode"] = mode
        return "stop here"

    monkeypatch.setattr(orch, "_check_launch_consent", _fake)

    async def _go():
        async with session_factory() as db:
            await _seed(db)
            return await orch.Orchestrator().launch(
                scenario_id="SIM-CDR-999", mode="pull", db=db, target_agent_id="a1",
            )

    asyncio.run(_go())
    assert seen["mode"] == "pull"
