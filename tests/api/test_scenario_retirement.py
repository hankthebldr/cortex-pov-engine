"""A corpus scenario whose YAML is gone is retired at boot, never deleted.

``load_scenarios`` upserts rows from the YAML corpus. A scenario moved out of
the corpus (PR #380 moved 18) used to keep its row as ``active`` on every
database that outlived the move, so the Library, ``/api/health``, UC/TC
evidence and MITRE coverage kept listing it and a DC could launch it.

The row is kept (runs and results point at it), marked ``retired``, left out
of every corpus surface, refused at launch, and made active again when its
file comes back. Composer drafts have no file and are never retired.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# A real FY27 index test case, so the UC/TC evidence surface has something to
# show for the scenario that stays and something to drop for the one that goes.
_REAL_UC = "UCS-DLP-06"
_REAL_TC = "TC-DLP-11"

_TEMPLATE = """
scenario_id: {sid}
name: {sid} scenario
version: "1.0"
status: active
plane: EDR
detection_types: [BIOC]
uc_ref: {uc}
tc_ref: {tc}
uc_name: Use Case
tc_name: Test Case
mitre_tactic: TA0006
mitre_tactic_name: Credential Access
mitre_technique: {technique}
mitre_technique_name: Technique
execution_identity:
  default: www-data
  options: [www-data]
push_supported: true
pull_supported: true
steps:
  - id: step-01
    name: Step
    command: echo step
    identity: www-data
    mitre_technique: {technique}
    expected_detections:
      - plane: EDR
        type: BIOC
        description: step observed
cleanup:
  commands: [echo done]
"""

KEEP = "SIM-EDR-971"
GONE = "SIM-EDR-972"


@pytest.fixture(autouse=True)
def _relax_ref_strictness(monkeypatch):
    """The fixtures bind one real TC but synthetic parents; keep the loader advisory."""
    from config import settings  # noqa: PLC0415

    monkeypatch.setattr(settings, "CORTEXSIM_STRICT_REFS", False)


@pytest.fixture(scope="module", autouse=True)
def _load_registry():
    from engine.uctc_registry import default_index_dir, registry  # noqa: PLC0415

    registry.load(default_index_dir(str(REPO_ROOT)))


def _write(corpus: Path, sid: str, technique: str) -> Path:
    path = corpus / f"{sid.lower()}.yml"
    path.write_text(_TEMPLATE.format(sid=sid, uc=_REAL_UC, tc=_REAL_TC, technique=technique))
    return path


def _boot(session_factory, corpus: Path) -> list[str]:
    from engine.scenario_loader import load_scenarios  # noqa: PLC0415

    async def _run():
        async with session_factory() as db:
            return await load_scenarios(str(corpus), db)

    return asyncio.run(_run())


def _status(session_factory, sid: str):
    from models import Scenario  # noqa: PLC0415

    async def _run():
        async with session_factory() as db:
            row = (await db.execute(
                select(Scenario).where(Scenario.scenario_id == sid))).scalar_one_or_none()
            return None if row is None else row.status

    return asyncio.run(_run())


@pytest.fixture
def corpus(tmp_path, session_factory):
    """Boot once with both scenarios on disk, then remove GONE's file and boot again."""
    _write(tmp_path, KEEP, "T1003")
    gone = _write(tmp_path, GONE, "T1059.004")
    _boot(session_factory, tmp_path)
    gone.unlink()
    _boot(session_factory, tmp_path)
    return tmp_path


def test_scenario_missing_from_disk_is_retired_at_boot(corpus, session_factory):
    assert _status(session_factory, KEEP) == "active"
    assert _status(session_factory, GONE) == "retired"


def test_retired_scenario_is_not_counted_or_listed(corpus, session_factory, make_client):
    from api.health import probe_scenario_catalog  # noqa: PLC0415
    from api.mitre import router as mitre_router  # noqa: PLC0415
    from api.scenarios import router as scenarios_router  # noqa: PLC0415
    from api.uctc import router as uctc_router  # noqa: PLC0415

    client = make_client(scenarios_router, uctc_router, mitre_router)

    library = client.get("/api/scenarios").json()
    assert [s["scenario_id"] for s in library["scenarios"]] == [KEEP]
    assert library["total"] == 1

    catalog = asyncio.run(probe_scenario_catalog(session_factory))
    assert catalog["count"] == 1

    evidence = str(client.get(f"/api/uctc/test-cases/{_REAL_TC}").json())
    assert KEEP in evidence
    assert GONE not in evidence

    coverage = client.get("/api/mitre/coverage").json()
    covered = {sid for t in coverage["techniques"] for sid in t["scenarios"]}
    assert KEEP in covered
    assert GONE not in covered


def test_launch_of_retired_scenario_is_refused(corpus, make_client):
    from api.runs import router as runs_router  # noqa: PLC0415
    from api.scenarios import router as scenarios_router  # noqa: PLC0415

    client = make_client(runs_router, scenarios_router)

    for response in (
        client.post("/api/runs", json={"scenario_id": GONE, "mode": "pull"}),
        client.post("/api/runs", json={"scenario_id": GONE, "mode": "push"}),
        client.get(f"/api/scenarios/{GONE}/download"),
    ):
        assert response.status_code == 409, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "SCENARIO_RETIRED"
        assert set(detail) == {"error", "code", "detail"}


def test_runs_of_a_retired_scenario_still_open(tmp_path, session_factory, make_client):
    from api.results import router as results_router  # noqa: PLC0415
    from api.runs import router as runs_router  # noqa: PLC0415
    from api.scenarios import router as scenarios_router  # noqa: PLC0415
    from models import Result, Run  # noqa: PLC0415

    _write(tmp_path, KEEP, "T1003")
    gone = _write(tmp_path, GONE, "T1059.004")
    _boot(session_factory, tmp_path)

    async def _seed_run():
        async with session_factory() as db:
            db.add(Run(run_id="r-retired", scenario_id=GONE, mode="push", status="complete",
                       started_at=datetime.utcnow() - timedelta(minutes=5),
                       completed_at=datetime.utcnow() - timedelta(minutes=1)))
            db.add(Result(run_id="r-retired", step_id="step-01", step_name="Step", plane="EDR",
                          signal_type="BIOC", expected_detection="step observed",
                          executed_at=datetime.utcnow() - timedelta(minutes=4)))
            await db.commit()

    asyncio.run(_seed_run())
    gone.unlink()
    _boot(session_factory, tmp_path)
    assert _status(session_factory, GONE) == "retired"

    client = make_client(runs_router, results_router, scenarios_router)

    run = client.get("/api/runs/r-retired")
    assert run.status_code == 200
    assert run.json()["scenario_id"] == GONE
    assert "r-retired" in {r["run_id"] for r in client.get("/api/runs").json()["runs"]}

    results = client.get("/api/results/r-retired")
    assert results.status_code == 200
    assert "step observed" in results.text

    report = client.get("/api/runs/r-retired/report", params={"format": "json"})
    assert report.status_code == 200

    detail = client.get(f"/api/scenarios/{GONE}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "retired"


def test_scenario_back_on_disk_is_active_again(corpus, session_factory):
    assert _status(session_factory, GONE) == "retired"

    _write(corpus, GONE, "T1059.004")
    _boot(session_factory, corpus)

    assert _status(session_factory, GONE) == "active"


def test_drafts_are_never_retired(tmp_path, session_factory):
    from engine.composer_draft_schema import orm_sentinels  # noqa: PLC0415
    from models import Scenario  # noqa: PLC0415

    async def _seed_draft():
        async with session_factory() as db:
            db.add(Scenario(**{**orm_sentinels, "scenario_id": "SIM-DRAFT-kept",
                               "name": "kept draft", "plane": "EDR", "steps": []}))
            await db.commit()

    asyncio.run(_seed_draft())
    _write(tmp_path, KEEP, "T1003")
    _boot(session_factory, tmp_path)

    assert _status(session_factory, "SIM-DRAFT-kept") == "draft"
    assert _status(session_factory, KEEP) == "active"
