"""GAP-API-007 — ``GET /api/health`` is the ONE diagnostic surface.

These tests exist because the previous implementation went green on the two
deployments that are broken:

  * booting without ``tools/`` reported ``adapter_catalog {status: ok, count: 0}``
    and an overall ``ok``, while every ``adapter_ref`` in the corpus was
    unresolvable and the payload shelf could stage nothing;
  * the ``xsiam_operation_catalog`` probe was UNREACHABLE — merge 0ef802b left
    it after ``return components`` — so it never appeared in a live response and
    no test noticed for two releases.

So the suite asserts three properties that no amount of "the tests pass" gives
you on its own:

  1. **Structural** — every component key the module *defines* is present in the
     payload. A probe orphaned by a merge fails this immediately.
  2. **A zero is degraded, not ok** — for each catalog whose whole purpose is to
     be non-empty.
  3. **Every non-ok component carries a machine code and a remediation line.**
"""
from __future__ import annotations

import asyncio

import pytest


# ---------------------------------------------------------------------------
# Build identity
# ---------------------------------------------------------------------------


def test_commit_sha_is_nonempty_string(monkeypatch):
    from api import health

    monkeypatch.setenv("CORTEXSIM_COMMIT_SHA", "deadbeefcafe")
    assert health.commit_sha() == "deadbeefcafe"


def test_commit_sha_falls_back_to_unknown(monkeypatch):
    import config
    from api import health

    for var in ("CORTEXSIM_COMMIT_SHA", "GIT_COMMIT", "SOURCE_COMMIT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(config.settings, "CORTEXSIM_BASE_DIR", "/nonexistent-xyz")
    assert health.commit_sha() == "unknown"


def test_main_still_exports_the_health_helpers():
    """`main._commit_sha` / `_component_health` / `health_check` are part of the
    module's surface — moving the implementation must not break importers."""
    import main
    from api import health

    assert main._commit_sha is health.commit_sha
    assert main._component_health is health.component_health
    assert main.health_check is health.health_check


# ---------------------------------------------------------------------------
# Structural guard — the one that catches an orphaned probe
# ---------------------------------------------------------------------------


def test_every_declared_component_appears_in_the_payload():
    """COMPONENT_KEYS is the contract; the live body must satisfy it exactly.

    This is the guard against the 0ef802b class of damage: a probe that a merge
    strands after a ``return`` disappears from the response silently. Here it
    fails a test.
    """
    from api import health

    body = asyncio.run(health.health_payload())
    assert set(body["components"]) == set(health.COMPONENT_KEYS), (
        "component set drifted from COMPONENT_KEYS — a probe was added without "
        "declaring it, or a merge orphaned one"
    )
    assert body["checked"] == list(health.COMPONENT_KEYS)


def test_xsiam_operation_catalog_probe_is_reachable():
    """The specific probe that merge 0ef802b left as dead code."""
    from api import health

    body = asyncio.run(health.health_payload())
    comp = body["components"]["xsiam_operation_catalog"]
    assert comp["status"] in {"ok", "degraded", "error"}
    assert "count" in comp or comp["status"] == "error"


def test_every_component_carries_a_status_and_a_code():
    from api import health

    body = asyncio.run(health.health_payload())
    for name, comp in body["components"].items():
        assert comp.get("status") in {"ok", "degraded", "error"}, name
        assert comp.get("code"), f"{name} has no machine code"


def test_non_ok_components_carry_a_remediation_line():
    """A code without a fix is a shrug. Every degraded/error component must say
    what to do about it."""
    from api import health

    body = asyncio.run(health.health_payload())
    for name, comp in body["components"].items():
        if comp["status"] == "ok":
            continue
        detail = comp.get("detail") or ""
        assert len(detail) > 40, f"{name}: remediation too thin: {detail!r}"
        assert "Fix:" in detail or "fix:" in detail or "NOT a liveness probe" in detail, (
            f"{name}: detail names no remediation: {detail!r}"
        )


def test_degraded_components_is_a_flat_top_level_list():
    """The console's context bar renders this without walking the map."""
    from api import health

    body = asyncio.run(health.health_payload())
    expected = sorted(
        n for n, c in body["components"].items() if c["status"] != "ok"
    )
    assert body["degraded_components"] == expected
    assert body["status"] == ("ok" if not expected else "degraded")


def test_health_declares_what_it_did_not_check():
    """A green health check must not be over-read. The boundary of the claim is
    part of the response."""
    from api import health

    body = asyncio.run(health.health_payload())
    not_checked = {item["what"] for item in body["not_checked"]}
    assert "xsiam_tenant_reachability" in not_checked
    assert "eal_collector_reachability" in not_checked
    for item in body["not_checked"]:
        assert item["why"] and item["checked_by"]


# ---------------------------------------------------------------------------
# A zero is degraded, not ok — the "green on a broken deployment" class
# ---------------------------------------------------------------------------


class _EmptyCatalog:
    def count(self):
        return 0

    def all_entries(self):
        return []

    def all_test_cases(self):
        return []

    loaded = False
    version = None


def test_adapter_catalog_zero_is_degraded_and_names_tools_dir(monkeypatch):
    """Booting without tools/ used to report ok/count:0 while every adapter_ref
    in the corpus was unresolvable."""
    import tools.adapter_catalog as mod
    from api import health

    monkeypatch.setattr(mod, "catalog", _EmptyCatalog())
    comp = health.probe_adapter_catalog()
    assert comp["status"] == "degraded"
    assert comp["code"] == "ADAPTER_CATALOG_EMPTY"
    assert "tools/" in comp["detail"]
    assert "COPY" in comp["detail"]


def test_ttp_catalog_zero_is_degraded(monkeypatch):
    import engine.ttp_catalog as mod
    from api import health

    monkeypatch.setattr(mod, "catalog", _EmptyCatalog())
    comp = health.probe_ttp_catalog()
    assert comp["status"] == "degraded"
    assert comp["code"] == "TTP_CATALOG_EMPTY"


def test_uctc_snapshot_absent_is_degraded_not_zero_test_cases(monkeypatch):
    import engine.uctc_registry as mod
    from api import health

    monkeypatch.setattr(mod, "registry", _EmptyCatalog())
    comp = health.probe_uctc_registry()
    assert comp["status"] == "degraded"
    assert comp["code"] == "UCTC_SNAPSHOT_ABSENT"
    # Strictness is load-bearing: an absent snapshot silently turns strict-mode
    # ref validation into a no-op, which is how the corpus rotted before.
    assert "advisory" in comp["detail"]


def test_xsiam_operation_catalog_zero_is_degraded(monkeypatch):
    import integrations.xsiam.operations.catalog as mod
    from api import health

    monkeypatch.setattr(mod, "catalog", _EmptyCatalog())
    comp = health.probe_xsiam_operation_catalog()
    assert comp["status"] == "degraded"
    assert comp["code"] == "XSIAM_OPERATION_CATALOG_EMPTY"


def test_scenario_catalog_zero_is_degraded():
    from api import health

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def scalar(self, _stmt):
            return 0

    comp = asyncio.run(health.probe_scenario_catalog(lambda: _Session()))
    assert comp["status"] == "degraded"
    assert comp["code"] == "SCENARIO_CATALOG_EMPTY"


# ---------------------------------------------------------------------------
# Payload shelf — a component that did not exist at all
# ---------------------------------------------------------------------------


class _Art:
    def __init__(self, name, pinned=True, used_by=""):
        self.name = name
        self.pinned = pinned
        self.used_by = used_by


def test_payload_shelf_missing_artifacts_is_degraded_and_names_scenarios(monkeypatch):
    from api import health
    from engine import payload_shelf

    monkeypatch.setattr(
        payload_shelf, "declared_artifacts",
        lambda **kw: [
            _Art("linpeas.sh", used_by="SIM-EDR-022"),
            _Art("pspy64", used_by="SIM-EDR-022, SIM-CDR-007"),
        ],
    )
    import api.payloads as payloads_mod

    monkeypatch.setattr(payloads_mod, "inventory", lambda: [{"name": "linpeas.sh"}])

    comp = health.probe_payload_shelf([])
    assert comp["status"] == "degraded"
    assert comp["code"] == "SHELF_ARTIFACTS_MISSING"
    assert comp["declared"] == 2 and comp["staged"] == 1
    assert comp["missing"] == ["pspy64"]
    # The remediation names the scenarios that will refuse at compose time —
    # a count alone does not tell a DC which demo is about to break.
    assert "SIM-EDR-022" in comp["detail"] and "SIM-CDR-007" in comp["detail"]
    assert "PAYLOAD_NOT_STAGED" in comp["detail"]


def test_payload_shelf_unpinned_artifact_is_degraded(monkeypatch):
    from api import health
    from engine import payload_shelf
    import api.payloads as payloads_mod

    monkeypatch.setattr(
        payload_shelf, "declared_artifacts",
        lambda **kw: [_Art("linpeas.sh", pinned=False)],
    )
    monkeypatch.setattr(payloads_mod, "inventory", lambda: [{"name": "linpeas.sh"}])

    comp = health.probe_payload_shelf([])
    assert comp["status"] == "degraded"
    assert comp["code"] == "SHELF_ARTIFACT_UNPINNED"


def test_payload_shelf_probe_is_called_with_the_scenario_join(monkeypatch):
    """`used_by` only populates when declared_artifacts is given scenarios= —
    the CLI does it and the health probe must too, or the remediation cannot
    name a scenario."""
    from api import health
    from engine import payload_shelf
    import api.payloads as payloads_mod

    seen = {}

    def _fake(**kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(payload_shelf, "declared_artifacts", _fake)
    monkeypatch.setattr(payloads_mod, "inventory", lambda: [])
    rows = [{"scenario_id": "SIM-EDR-022", "external_tools": []}]
    health.probe_payload_shelf(rows)
    assert seen.get("scenarios") == rows


# ---------------------------------------------------------------------------
# Agent binaries + liveness
# ---------------------------------------------------------------------------


def test_agent_binaries_incomplete_matrix_is_degraded(monkeypatch):
    import api.agents as agents_mod
    from api import health

    monkeypatch.setattr(
        agents_mod, "_available_binaries",
        lambda: [{"os": "linux", "arch": "amd64"}, {"os": "darwin", "arch": "amd64"}],
    )
    comp = health.probe_agent_binaries()
    assert comp["status"] == "degraded"
    assert comp["code"] == "AGENT_DIST_INCOMPLETE"
    assert comp["missing"] == ["windows/amd64"]
    assert "WINDOWS_AGENT_UNAVAILABLE" in comp["detail"]


def test_agent_binaries_empty_dist_is_degraded(monkeypatch):
    import api.agents as agents_mod
    from api import health

    monkeypatch.setattr(agents_mod, "_available_binaries", lambda: [])
    comp = health.probe_agent_binaries()
    assert comp["status"] == "degraded"
    assert comp["code"] == "AGENT_DIST_EMPTY"


def test_agent_binaries_full_matrix_is_ok(monkeypatch):
    import api.agents as agents_mod
    from api import health

    monkeypatch.setattr(
        agents_mod, "_available_binaries",
        lambda: [{"os": o, "arch": "amd64"} for o in ("linux", "darwin", "windows")],
    )
    comp = health.probe_agent_binaries()
    assert comp["status"] == "ok"
    assert comp["missing"] == []


# ---------------------------------------------------------------------------
# Integrations — the component that must never imply reachability
# ---------------------------------------------------------------------------


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, _stmt):
        rows = self._rows

        class _R:
            def scalars(self_inner):
                class _S:
                    def all(self_s):
                        return rows

                return _S()

        return _R()


def test_integrations_never_claims_reachability():
    from api import health

    class _Cred:
        def __init__(self, name, kind, ok=None, at=None):
            self.name, self.kind = name, kind
            self.last_verified_ok, self.last_verified_at = ok, at

    rows = [_Cred("lab", "xsiam"), _Cred("lab-xql", "xsiam_tenant")]
    comp = asyncio.run(health.probe_integrations(lambda: _Rows(rows)))
    assert comp["status"] == "ok"
    assert comp["configured"] == 2
    assert comp["verified"] == 0
    assert comp["by_kind"] == {"xsiam": 1, "xsiam_tenant": 1}
    # The claim boundary is IN the payload, not just in a doc nobody reads.
    assert "NOT a liveness probe" in comp["detail"]
    assert "No tenant call is made" in comp["detail"]


def test_integrations_reports_a_failed_last_test():
    from datetime import datetime

    from api import health

    class _Cred:
        def __init__(self, name, kind, ok, at):
            self.name, self.kind = name, kind
            self.last_verified_ok, self.last_verified_at = ok, at

    rows = [_Cred("prod", "xsiam", False, datetime.utcnow())]
    comp = asyncio.run(health.probe_integrations(lambda: _Rows(rows)))
    assert comp["status"] == "degraded"
    assert comp["code"] == "INTEGRATION_LAST_TEST_FAILED"
    assert comp["last_test_failed"] == ["prod"]


# ---------------------------------------------------------------------------
# A probe that throws is a reported state, never a 500
# ---------------------------------------------------------------------------


def test_no_agents_enrolled_is_ok_not_degraded():
    """A fresh SimCore, and any push-only deployment, has no agents by design.
    Flagging that amber forever makes the console banner background noise — and
    a banner nobody reads is how the real degradation gets missed."""
    from api import health

    comp = asyncio.run(health.probe_agents(lambda: _Rows([])))
    assert comp["status"] == "ok"
    assert comp["code_hint"] == "NO_AGENTS_ENROLLED"
    assert comp["registered"] == 0
    assert "enrol" in comp["detail"].lower()


def test_enrolled_but_no_agent_online_is_degraded():
    """Once an agent exists, an intent has been declared. A launch that queues
    a task nothing collects IS a fault."""
    from datetime import datetime, timedelta

    from api import health

    class _Agent:
        def __init__(self, agent_id, age_s):
            self.agent_id = agent_id
            self.last_seen = datetime.utcnow() - timedelta(seconds=age_s)

    comp = asyncio.run(health.probe_agents(
        lambda: _Rows([_Agent("jumpbox-1", 4000), _Agent("jumpbox-2", 120)])
    ))
    assert comp["status"] == "degraded"
    assert comp["code"] == "NO_AGENT_ONLINE"
    assert comp["registered"] == 2 and comp["online"] == 0
    assert "systemctl status cortexsim-agent" in comp["detail"]
    assert comp["derived_from"].startswith("last_seen age")


def test_tasks_queued_for_an_offline_agent_are_degraded():
    """The shape of 'I launched it and nothing happened'. Depth alone is not a
    fault — depth for a dead agent is."""
    from datetime import datetime, timedelta

    from api import health

    class _Agent:
        def __init__(self, agent_id, age_s):
            self.agent_id = agent_id
            self.last_seen = datetime.utcnow() - timedelta(seconds=age_s)

    class _Task:
        def __init__(self, agent_id):
            self.agent_id = agent_id

    class _Multi:
        """Two sequential .execute() calls: tasks, then agents."""

        def __init__(self):
            self._calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, _stmt):
            self._calls += 1
            rows = [_Task("dead-box")] if self._calls == 1 else [_Agent("dead-box", 9000)]

            class _R:
                def scalars(self_inner):
                    class _S:
                        def all(self_s):
                            return rows

                    return _S()

            return _R()

    comp = asyncio.run(health.probe_task_queue(lambda: _Multi()))
    assert comp["status"] == "degraded"
    assert comp["code"] == "TASKS_QUEUED_FOR_UNAVAILABLE_AGENT"
    assert comp["stranded"] == {"dead-box": 1}
    # The queue is durable — saying so stops a DC from assuming the run is lost.
    assert "durable" in comp["detail"]


def test_eal_collector_not_in_allowlist_is_degraded_without_any_outbound_call():
    """Reachability is NOT probed (that is an outbound POST). What IS checkable
    in-process is the config fault that guarantees a refusal."""
    from api import health

    class _Campaign:
        campaign_id = "camp-1"
        target_allowlist = ["collector.lab.internal"]
        spec = {"steps": [
            {"step_id": "step-01",
             "params": {"collector_url": "https://cxs-coll:8443/logs/v1/event"}},
        ]}

    comp = asyncio.run(health.probe_eal_collectors(lambda: _Rows([_Campaign()])))
    assert comp["status"] == "degraded"
    assert comp["code"] == "COLLECTOR_NOT_IN_ALLOWLIST"
    assert comp["reachability_checked"] is False
    assert comp["allowlist_violations"] == [
        {"campaign_id": "camp-1", "step_id": "step-01", "host": "cxs-coll"}
    ]


def test_a_throwing_probe_is_reported_not_swallowed():
    from api import health

    def _boom():
        raise RuntimeError("catalog exploded")

    comp = health._guard("adapter_catalog", _boom)
    assert comp["status"] == "error"
    assert comp["code"] == "PROBE_FAILED"
    assert "catalog exploded" in comp["detail"]


def test_health_payload_survives_a_broken_db(monkeypatch):
    """The endpoint must still answer — and report the fault — when the DB is
    gone. A health check that 500s when storage fails tells a DC nothing."""
    from api import health

    class _Broken:
        async def __aenter__(self):
            raise RuntimeError("unable to open database file")

        async def __aexit__(self, *a):
            return False

    comp = asyncio.run(health.probe_db(lambda: _Broken()))
    assert comp["status"] == "error"
    assert comp["code"] == "DB_UNREACHABLE"
    assert "load from disk" in comp["detail"]
