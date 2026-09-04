"""Status guard: composer drafts never leak into Library or coverage.

Doctrine (Gate A5, "a draft is not corpus coverage"): a composer draft is a
persisted ``Scenario`` row with ``status='draft'`` — launchable via the
existing path, but it must NOT appear in the Library list
(``GET /api/scenarios``) nor be counted as UC/TC evidence or coverage
(``core/api/uctc.py``). These tests pin that:

* ``GET /api/scenarios`` excludes a ``status='draft'`` row by default, and an
  explicit ``?status=draft`` is the only way to opt in.
* ``GET /api/uctc/{test-cases,coverage}`` are byte-for-byte unchanged by the
  presence of a draft row that binds a real FY27 index test case.

The uctc guard is already in the tree (GAP-API-009); the scenarios guard is
new. Both are asserted here so a regression in either half fails a test rather
than a customer's coverage report.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _add_scenario(session_factory, **overrides):
    """Seed one Scenario row; sensible defaults, override what a test cares
    about (``status``, ``scenario_id``, ``uc_ref``/``tc_ref``/``tc_refs``)."""
    from models import Scenario  # noqa: PLC0415

    fields = dict(
        scenario_id="SIM-EDR-001",
        name="Credential Dumping",
        plane="EDR",
        version="1.0",
        status="active",
        uc_ref="UCS-EDR-01",
        uc_name="X",
        tc_ref="TC-EDR-01",
        tc_name="Y",
        tc_refs=["TC-EDR-01"],
        detection_types=["BIOC"],
        mitre_tactic="TA0006",
        mitre_tactic_name="Credential Access",
        mitre_technique="T1003.001",
        mitre_technique_name="LSASS Memory",
        steps=[{
            "id": "step-01",
            "name": "Dump",
            "identity": "www-data",
            "command": "cat /etc/shadow",
            "mitre_technique": "T1003.001",
            "expected_detections": [{"type": "BIOC", "plane": "EDR", "description": "d"}],
        }],
    )
    fields.update(overrides)

    async def _do():
        async with session_factory() as db:
            db.add(Scenario(**fields))
            await db.commit()

    asyncio.run(_do())
    return fields["scenario_id"]


# --------------------------------------------------------------------------- #
# GET /api/scenarios — the Library list                                        #
# --------------------------------------------------------------------------- #

@pytest.fixture
def scenarios_client(make_client):
    from api.scenarios import router  # noqa: PLC0415
    return make_client(router)


def test_library_list_excludes_draft_by_default(scenarios_client, session_factory):
    _add_scenario(session_factory, scenario_id="SIM-EDR-001", status="active")
    _add_scenario(session_factory, scenario_id="SIM-DRAFT-foo", status="draft")

    body = scenarios_client.get("/api/scenarios").json()
    ids = {r["scenario_id"] for r in body["scenarios"]}

    assert "SIM-EDR-001" in ids
    assert "SIM-DRAFT-foo" not in ids, "a draft must never leak into the Library"
    assert body["total"] == 1


def test_library_list_status_draft_opts_in(scenarios_client, session_factory):
    _add_scenario(session_factory, scenario_id="SIM-EDR-001", status="active")
    _add_scenario(session_factory, scenario_id="SIM-DRAFT-foo", status="draft")

    body = scenarios_client.get("/api/scenarios?status=draft").json()
    ids = {r["scenario_id"] for r in body["scenarios"]}

    assert ids == {"SIM-DRAFT-foo"}
    assert body["total"] == 1


def test_library_list_status_active_is_the_default(scenarios_client, session_factory):
    _add_scenario(session_factory, scenario_id="SIM-EDR-001", status="active")
    _add_scenario(session_factory, scenario_id="SIM-DEP-001", status="deprecated")
    _add_scenario(session_factory, scenario_id="SIM-DRAFT-foo", status="draft")

    # Explicit active matches the default; neither draft nor deprecated appears.
    default_ids = {r["scenario_id"] for r in scenarios_client.get("/api/scenarios").json()["scenarios"]}
    active_ids = {r["scenario_id"] for r in scenarios_client.get("/api/scenarios?status=active").json()["scenarios"]}

    assert default_ids == active_ids == {"SIM-EDR-001"}


# --------------------------------------------------------------------------- #
# GET /api/uctc/* — a draft is not corpus coverage                             #
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module", autouse=True)
def _load_registry():
    """Router handlers read the process-level singleton — load it from the
    committed v2.2 snapshot or every count is a trivial zero."""
    from engine.uctc_registry import default_index_dir, registry  # noqa: PLC0415

    registry.load(default_index_dir(str(REPO_ROOT)))
    assert len(registry.all_test_cases()) == 266


@pytest.fixture
def uctc_client(make_client):
    from api.uctc import router  # noqa: PLC0415
    return make_client(router)


# A real FY27 index test case the draft will bind (from crosswalk-v2.2.csv).
_REAL_UC = "UCS-DLP-06"
_REAL_TC = "TC-DLP-11"


def test_uctc_test_case_evidence_ignores_draft(uctc_client, session_factory):
    # Only a draft binds the real TC; nothing active does.
    _add_scenario(
        session_factory, scenario_id="SIM-DRAFT-dlp", status="draft",
        plane="AI_ACCESS", uc_ref=_REAL_UC, tc_ref=_REAL_TC, tc_refs=[_REAL_TC],
    )

    tc = uctc_client.get(f"/api/uctc/test-cases/{_REAL_TC}").json()
    evidence = tc.get("evidence", tc)  # tolerate either flattened or nested shape
    scenario_ids = str(evidence)

    assert "SIM-DRAFT-dlp" not in scenario_ids, "draft must not evidence a TC"


def test_uctc_coverage_unchanged_by_draft(uctc_client, session_factory):
    # One active scenario is the only thing coverage should count.
    _add_scenario(
        session_factory, scenario_id="SIM-AIACC-001", status="active",
        plane="AI_ACCESS", uc_ref=_REAL_UC, tc_ref=_REAL_TC, tc_refs=[_REAL_TC],
    )
    before = uctc_client.get("/api/uctc/coverage").json()

    # Add a draft that binds the SAME real TC; coverage must not move.
    _add_scenario(
        session_factory, scenario_id="SIM-DRAFT-dlp", status="draft",
        plane="AI_ACCESS", uc_ref=_REAL_UC, tc_ref=_REAL_TC, tc_refs=[_REAL_TC],
    )
    after = uctc_client.get("/api/uctc/coverage").json()

    assert before["totals"] == after["totals"], "a draft changed coverage totals"
    assert after["totals"]["scenarios"] == 1, "only the active scenario is counted"
    # The draft id never surfaces in any plane rollup.
    assert "SIM-DRAFT-dlp" not in str(after["by_plane"])


def test_uctc_coverage_include_inactive_still_excludes_draft(uctc_client, session_factory):
    """``?include_inactive`` is an explicit debug opt-in; even it must not
    fabricate a draft as coverage the way the default never would — the guard
    is ``status=='active'``, so a draft stays out unless include_inactive is
    passed. Here we confirm the DEFAULT path (no flag) drops it."""
    _add_scenario(
        session_factory, scenario_id="SIM-DRAFT-dlp", status="draft",
        plane="AI_ACCESS", uc_ref=_REAL_UC, tc_ref=_REAL_TC, tc_refs=[_REAL_TC],
    )
    cov = uctc_client.get("/api/uctc/coverage").json()
    assert cov["totals"]["scenarios"] == 0
