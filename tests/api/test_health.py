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


@pytest.fixture(autouse=True)
def _no_ignored_forwarders(monkeypatch):
    """api.agents keeps the peers whose X-Forwarded-For it ignored in process
    memory, and other test files POST such headers. probe_agents reports a
    non-empty record (RG-INSTALL-05), so start every test here with none; a
    test that wants one patches it again."""
    import api.agents as agents_mod

    monkeypatch.setattr(agents_mod, "ignored_forwarders", lambda: [])


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


def test_health_reports_how_long_this_instance_has_been_up(monkeypatch):
    """The console header states this POVengine's uptime. It has to come from
    SimCore's own clock: a browser cannot know when the process started, and a
    difference of two clocks would drift by the DC laptop's skew."""
    from datetime import datetime, timezone
    import time

    from api import health

    monkeypatch.setattr(health, "_PROCESS_STARTED_MONO", time.monotonic() - 3725)
    body = asyncio.run(health.health_payload())

    assert 3725 <= body["uptime_seconds"] <= 3730
    started = datetime.fromisoformat(body["started_at"].replace("Z", "+00:00"))
    assert started.tzinfo is not None and started.utcoffset() == timezone.utc.utcoffset(None)
    assert started <= datetime.now(timezone.utc)


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


def test_assertion_catalog_zero_is_degraded(monkeypatch):
    """An unshipped assertions/ must not read as 'nobody authored any'.

    This is the probe that did not exist while the Dockerfile did not COPY
    assertions/ — 18 artifacts on disk, 0 in the image, rejected:[], and no
    component said so. See tests/tools/test_image_ships_runtime_content.py.
    """
    import engine.assertions as mod
    from api import health

    class _EmptyAssertionCatalog:
        _by_id: dict = {}
        _rejected: list = []

        def ensure_loaded(self):
            return self

    monkeypatch.setattr(mod, "catalog", _EmptyAssertionCatalog())
    comp = health.probe_assertion_catalog()
    assert comp["status"] == "degraded"
    assert comp["code"] == "ASSERTION_CATALOG_EMPTY"
    # The remediation must name the packaging cause, not just the symptom —
    # that distinction is the whole reason this defect survived.
    assert "assertions/" in comp["detail"]
    assert "COPY" in comp["detail"]


def test_assertion_catalog_is_a_registered_component():
    """A probe nobody dispatches is a probe nobody reads."""
    from api import health

    assert "assertion_catalog" in health.COMPONENT_KEYS


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

        async def execute(self, _stmt):
            class _Empty:
                def all(self):
                    return []
            return _Empty()

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
        lambda: [{"os": o, "arch": a} for o, a in SHIPPED_MATRIX if o != "windows"],
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


SHIPPED_MATRIX = [("linux", "amd64"), ("linux", "arm64"), ("darwin", "amd64"),
                  ("darwin", "arm64"), ("windows", "amd64")]


def test_agent_binaries_full_matrix_is_ok(monkeypatch):
    import api.agents as agents_mod
    from api import health

    monkeypatch.setattr(
        agents_mod, "_available_binaries",
        lambda: [{"os": o, "arch": a} for o, a in SHIPPED_MATRIX],
    )
    comp = health.probe_agent_binaries()
    assert comp["status"] == "ok"
    assert comp["missing"] == []


def test_agent_binaries_amd64_only_shelf_is_degraded(monkeypatch):
    """CRITIC-SHIPSTATE-AMD64. `want` was <goos>/amd64 only, so a shelf without
    the arm64 beacons read ok while an Apple-silicon Mac or a Graviton host got
    a 404 from /api/agents/binary and an installer that fell back to a source
    build. The shipped matrix is five targets."""
    import api.agents as agents_mod
    from api import health

    monkeypatch.setattr(
        agents_mod, "_available_binaries",
        lambda: [{"os": o, "arch": "amd64"} for o in ("linux", "darwin", "windows")],
    )
    comp = health.probe_agent_binaries()
    assert comp["status"] == "degraded", comp
    assert comp["code"] == "AGENT_DIST_INCOMPLETE"
    assert comp["missing"] == ["darwin/arm64", "linux/arm64"]
    assert "WINDOWS_AGENT_UNAVAILABLE" not in comp["detail"], comp["detail"]


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


# ---------------------------------------------------------------------------
# The guard paths — reached only when a probe THROWS
# ---------------------------------------------------------------------------
#
# These fired for the first time in CI, where pytest runs in the prod image
# against a database whose tables do not exist: every DB-backed probe raised,
# and the resulting components carried a diagnosis with no remediation. Locally
# the tables existed, so the exception branch was never taken and
# test_non_ok_components_carry_a_remediation_line passed while the branch it was
# written to police went unexercised.


def test_a_throwing_sync_probe_still_names_a_remediation():
    from api.health import ERROR, _guard

    def boom():
        raise RuntimeError("no such table: ttps")

    comp = _guard("ttp_catalog", boom)
    assert comp["status"] == ERROR
    assert comp["code"] == "PROBE_FAILED"
    assert "Fix:" in comp["detail"], (
        "a failing component with no remediation is exactly what this health "
        "surface must never emit — it tells a DC something is wrong and nothing "
        "about what to do"
    )


def test_the_async_guard_routes_the_operator_to_the_db_component_first():
    """The DB-backed probes all fail together when the schema is missing, and
    chasing five independent-looking faults wastes the operator's time.

    Asserted on the source because ``_aguard`` is a closure inside
    ``component_health`` with no seam to inject through. A shallower check than the
    sync case above, and honest about being one.
    """
    import inspect

    from api import health

    src = inspect.getsource(health.component_health)
    assert "Fix: check the `db` component first" in src


# ---------------------------------------------------------------------------
# identity_spec — the file every push bundle's run_as arm and S-17 are built from
# ---------------------------------------------------------------------------


@pytest.fixture
def identity_spec_at(monkeypatch, tmp_path):
    """Point the identity-spec loader at a tmp path; nothing is written until asked."""
    from engine import identity_spec

    path = tmp_path / "identity_harness.json"
    monkeypatch.setattr(identity_spec, "_spec_path", lambda: str(path))
    identity_spec._load.cache_clear()
    yield path
    identity_spec._load.cache_clear()


def test_identity_spec_is_a_registered_component():
    from api import health

    assert "identity_spec" in health.COMPONENT_KEYS


def test_an_absent_identity_spec_is_degraded_though_the_bundles_are_right(identity_spec_at):
    """The deployment tests/tools/test_image_ships_runtime_content.py found:
    spec/ never shipped, the fallback answered, and nothing said so. The
    fallback keeps the bytes right; this keeps the deployment honest."""
    from api import health

    comp = health.probe_identity_spec()
    assert comp["status"] == "degraded"
    assert comp["code"] == "IDENTITY_SPEC_ABSENT"
    assert str(identity_spec_at) in comp["detail"]
    assert "COPY" in comp["detail"], "the remediation must name the packaging cause"


def test_a_malformed_identity_spec_is_degraded_and_says_what_is_wrong(identity_spec_at):
    from api import health

    identity_spec_at.write_text('{"service_accounts": "www-data"}')
    comp = health.probe_identity_spec()
    assert comp["status"] == "degraded"
    assert comp["code"] == "IDENTITY_SPEC_MALFORMED"
    assert str(identity_spec_at) in comp["detail"]


def test_a_well_formed_identity_spec_is_ok(identity_spec_at):
    from pathlib import Path

    from api import health

    repo_spec = Path(__file__).resolve().parents[2] / "spec" / "identity_harness.json"
    identity_spec_at.write_text(repo_spec.read_text())
    comp = health.probe_identity_spec()
    assert comp["status"] == "ok"
    assert comp["source"] == "file"


def test_scenario_catalog_counts_the_corpus_apart_from_drafts():
    """The Library lists active rows only (``/api/scenarios`` defaults to
    ``status=active``), so a health count that folds composer drafts in
    disagrees with the Library by exactly the draft count — 194 against 193 on
    a SimCore with one saved draft. ``count`` must be what the Library shows;
    drafts are reported on their own, never added to the corpus."""
    from api import health

    class _Result:
        def all(self):
            return [("active", 193), ("draft", 1)]

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def scalar(self, _stmt):  # the pre-fix total, every row
            return 194

        async def execute(self, _stmt):
            return _Result()

    comp = asyncio.run(health.probe_scenario_catalog(lambda: _Session()))
    assert comp["status"] == "ok"
    assert comp["count"] == 193
    assert comp["drafts"] == 1
    assert comp["by_status"] == {"active": 193, "draft": 1}


def test_scenario_catalog_drafts_alone_is_still_degraded():
    """Drafts are not corpus: a deployment whose only Scenario rows are
    composer drafts has nothing in the Library and nothing launchable from
    it, so drafts must not rescue the probe from SCENARIO_CATALOG_EMPTY."""
    from api import health

    class _Result:
        def all(self):
            return [("draft", 2)]

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def scalar(self, _stmt):
            return 2

        async def execute(self, _stmt):
            return _Result()

    comp = asyncio.run(health.probe_scenario_catalog(lambda: _Session()))
    assert comp["status"] == "degraded"
    assert comp["code"] == "SCENARIO_CATALOG_EMPTY"
    assert comp["count"] == 0
    assert comp["drafts"] == 2


# ---------------------------------------------------------------------------
# RG-INSTALL-05: agents recording a proxy's address as their own
# ---------------------------------------------------------------------------


class _AddrAgent:
    def __init__(self, agent_id, hostname, last_ip, age_s=1):
        from datetime import datetime, timedelta
        self.agent_id, self.hostname, self.last_ip = agent_id, hostname, last_ip
        self.last_seen = datetime.utcnow() - timedelta(seconds=age_s)


def test_an_ignored_x_forwarded_for_is_a_named_degradation(monkeypatch):
    """37dc374 logs the first X-Forwarded-For from each unlisted peer once and
    keeps the peers in api.agents.ignored_forwarders(). A log line is not seen
    by a DC; /api/health is the diagnostic surface."""
    import api.agents as agents_mod
    from api import health

    monkeypatch.setattr(agents_mod, "ignored_forwarders", lambda: ["10.9.9.9"])
    comp = asyncio.run(health.probe_agents(lambda: _Rows([_AddrAgent("a1", "h1", "10.9.9.9")])))
    assert comp["status"] == "degraded", comp
    assert comp["code"] == "XFF_FROM_UNTRUSTED_PEER"
    assert comp["ignored_forwarders"] == ["10.9.9.9"]
    assert "CORTEXSIM_TRUSTED_PROXIES" in comp["detail"]


def test_distinct_hosts_sharing_one_address_is_a_named_degradation(monkeypatch):
    import api.agents as agents_mod
    from api import health

    monkeypatch.setattr(agents_mod, "ignored_forwarders", lambda: [])
    rows = [_AddrAgent("a1", "web-01", "10.0.0.1"), _AddrAgent("a2", "db-01", "10.0.0.1"),
            _AddrAgent("a3", "app-01", "10.0.0.3")]
    comp = asyncio.run(health.probe_agents(lambda: _Rows(rows)))
    assert comp["status"] == "degraded", comp
    assert comp["code"] == "AGENT_ADDRESS_SHARED"
    assert comp["shared_addresses"] == {"10.0.0.1": ["db-01", "web-01"]}


def test_one_host_with_two_agent_rows_is_not_shared(monkeypatch):
    import api.agents as agents_mod
    from api import health

    monkeypatch.setattr(agents_mod, "ignored_forwarders", lambda: [])
    rows = [_AddrAgent("a1", "web-01", "10.0.0.1"), _AddrAgent("a2", "web-01", "10.0.0.1")]
    comp = asyncio.run(health.probe_agents(lambda: _Rows(rows)))
    assert comp["status"] == "ok", comp


def test_an_ignored_forwarder_with_no_agent_enrolled_is_still_named(monkeypatch):
    import api.agents as agents_mod
    from api import health

    monkeypatch.setattr(agents_mod, "ignored_forwarders", lambda: ["10.9.9.9"])
    comp = asyncio.run(health.probe_agents(lambda: _Rows([])))
    assert comp["code"] == "XFF_FROM_UNTRUSTED_PEER", comp


# ---------------------------------------------------------------------------
# mcp component — opt-in /mcp mount, zero outbound calls
# ---------------------------------------------------------------------------


def test_mcp_off_is_ok_with_disabled_code(monkeypatch):
    import config
    from api import health

    monkeypatch.setattr(config.settings, "CORTEXSIM_MCP_HTTP", False)
    comp = health.probe_mcp()
    assert comp["status"] == "ok"
    assert comp["code"] == "MCP_DISABLED"
    assert comp["mounted"] is False
    assert comp["path"] == "/mcp"
    assert comp["detail"] == "opt-in; set CORTEXSIM_MCP_HTTP=1 to mount"


def test_mcp_on_but_sdk_missing_is_degraded(monkeypatch):
    import builtins

    import config
    from api import health

    monkeypatch.setattr(config.settings, "CORTEXSIM_MCP_HTTP", True)
    real_import = builtins.__import__

    def _no_mcp_server(name, *args, **kwargs):
        if name.startswith("mcp_server"):
            raise ImportError("simulated: mcp SDK package absent")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_mcp_server)
    comp = health.probe_mcp()
    assert comp["status"] == "degraded"
    assert comp["code"] == "MCP_SDK_IMPORT_FAILED"
    assert "Fix:" in comp["detail"]


def _app_with_mcp(mcp):
    from types import SimpleNamespace

    return SimpleNamespace(state=SimpleNamespace(mcp=mcp))


def test_mcp_on_reports_tool_count(monkeypatch):
    import config
    from api import health
    from mcp_server.client import SimCoreClient
    from mcp_server.naming import CANONICAL_IDS
    from mcp_server.ratelimit import TokenBucket
    from mcp_server.registry import ToolContext
    from mcp_server.server import build

    monkeypatch.setattr(config.settings, "CORTEXSIM_MCP_HTTP", True)
    mcp = build(ToolContext(client=SimCoreClient("http://simcore.test"), bucket=TokenBucket(30)))
    comp = health.probe_mcp(_app_with_mcp(mcp))
    assert comp["status"] == "ok"
    assert comp["mounted"] is True
    assert comp["tools"] == len(CANONICAL_IDS)


def test_mcp_on_but_not_mounted_is_degraded(monkeypatch):
    """Flag on, nothing mounted (main.py swallowed an SDK ImportError): the
    probe must not infer a mount from the flag."""
    import config
    from api import health

    monkeypatch.setattr(config.settings, "CORTEXSIM_MCP_HTTP", True)
    for app in (None, _app_with_mcp(None)):
        comp = health.probe_mcp(app)
        assert comp["status"] == "degraded"
        assert comp["code"] == "MCP_NOT_MOUNTED"
        assert comp["mounted"] is False
        assert comp["tools"] == 0
        assert "Fix:" in comp["detail"]


def test_mcp_on_with_zero_registered_tools_is_degraded(monkeypatch):
    import config
    from api import health
    from mcp.server.fastmcp import FastMCP

    monkeypatch.setattr(config.settings, "CORTEXSIM_MCP_HTTP", True)
    comp = health.probe_mcp(_app_with_mcp(FastMCP("empty")))
    assert comp["status"] == "degraded"
    assert comp["code"] == "MCP_NO_TOOLS"
    assert comp["mounted"] is True
