"""GET /api/scenarios/staged — the extended library on the Scenarios page.

Each test names the failure it guards: a route swallowed by ``/{scenario_id}``,
an absent staging directory read as an empty library, a staged pair given a
UC/TC mapping it does not carry, a detection source that silently reads empty
when it could not be derived, and a manifest pair with no scenario file.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from config import settings
from engine import dataset_requirements, staged_library

REPO = Path(__file__).resolve().parents[2]
REAL = REPO / staged_library.STAGING_RELPATH


@pytest.fixture(autouse=True)
def _fresh_cache():
    # Both reads are cached per process; a test that moves the base dir must not
    # see, or leave behind, another base dir's answer.
    staged_library._read.cache_clear()
    dataset_requirements.load.cache_clear()
    yield
    staged_library._read.cache_clear()
    dataset_requirements.load.cache_clear()


@pytest.fixture
def client(make_client):
    from api.scenarios import router as scenarios_router
    return make_client(scenarios_router)


def _real_pair(scenario_id: str):
    manifest = json.loads((REAL / "MANIFEST.json").read_text())
    pair = next(p for p in manifest["pairs"] if p["scenario"] == scenario_id)
    scenario = next(p for p in (REAL / "scenarios").glob("*.yml")
                    if yaml.safe_load(p.read_text())["scenario_id"] == scenario_id)
    card = next((REAL / "cards").glob(f"{pair['card']}-*.json"))
    return pair, scenario, card


@pytest.fixture
def staged_base(tmp_path, monkeypatch):
    """A base dir holding two staged pairs and the real dataset registry.

    SIM-DRAFT-015 is a real pair (a BIOC on xdr_data). SIM-DRAFT-901 is the same
    scenario with every detection pointed at a detection id its card does not
    have, so none of them can be derived.
    """
    pair, scenario_path, card_path = _real_pair("SIM-DRAFT-015")
    root = tmp_path / staged_library.STAGING_RELPATH
    (root / "scenarios").mkdir(parents=True)
    (root / "cards").mkdir()
    shutil.copy(card_path, root / "cards" / card_path.name)
    shutil.copy(scenario_path, root / "scenarios" / scenario_path.name)

    broken = yaml.safe_load(scenario_path.read_text())
    broken["scenario_id"] = "SIM-DRAFT-901"
    for step in broken["steps"]:
        for det in step.get("expected_detections") or []:
            det["detection_id"] = "no-such-detection"
    (root / "scenarios" / "sim-draft-901-underived.yml").write_text(yaml.safe_dump(broken))

    manifest = {"generated_by": "test", "summary": {}, "pairs": [pair, {**pair, "scenario": "SIM-DRAFT-901"}]}
    (root / "MANIFEST.json").write_text(json.dumps(manifest))
    (tmp_path / "spec").mkdir()
    shutil.copy(REPO / "spec" / "dataset_requirements.yml", tmp_path / "spec" / "dataset_requirements.yml")
    monkeypatch.setattr(settings, "CORTEXSIM_BASE_DIR", str(tmp_path))
    return tmp_path


def test_staged_route_is_not_swallowed_by_the_scenario_detail_route(client, staged_base):
    r = client.get("/api/scenarios/staged")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["loaded"] is True and body["code"] is None
    assert body["launchable"] is False and body["counted_as_coverage"] is False
    assert body["total"] == 2 and [s["scenario_id"] for s in body["scenarios"]] == ["SIM-DRAFT-015", "SIM-DRAFT-901"]


def test_a_staged_row_carries_its_own_mapping_and_nothing_else(client, staged_base):
    rows = {s["scenario_id"]: s for s in client.get("/api/scenarios/staged").json()["scenarios"]}
    row = rows["SIM-DRAFT-015"]
    # MITRE as the file states it, in the corpus row's shape.
    assert row["mitre_technique"] == "T1036.005"
    assert all(set(st) <= {"id", "name", "mitre_technique", "expected_detections"} for st in row["steps"])
    # A staged pair is generated without UC/TC refs: they read null, never filled in.
    assert row["uc_ref"] is None and row["tc_ref"] is None and row["tc_refs"] == []
    # Detection source: derived through the door resolution the corpus uses.
    assert row["doors"] == ["xdr_agent"] and row["doors_code"] is None
    assert row["staged"]["card"] == "TTP-0000-0015"
    assert row["staged"]["launchable"] is False
    assert row["staged"]["platform"] == "windows"


def test_no_derivable_detection_reads_as_no_source_not_as_an_error(client, staged_base):
    rows = {s["scenario_id"]: s for s in client.get("/api/scenarios/staged").json()["scenarios"]}
    assert rows["SIM-DRAFT-901"]["doors"] == [] and rows["SIM-DRAFT-901"]["doors_code"] is None


def test_without_the_registry_the_source_is_not_checked_rather_than_empty(client, staged_base):
    (staged_base / "spec" / "dataset_requirements.yml").unlink()
    body = client.get("/api/scenarios/staged").json()
    assert body["registry_loaded"] is False
    for row in body["scenarios"]:
        assert row["doors"] is None and row["doors_code"] == "REGISTRY_ABSENT"


def test_an_absent_staging_directory_is_degraded_not_an_empty_library(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CORTEXSIM_BASE_DIR", str(tmp_path))
    r = client.get("/api/scenarios/staged")
    assert r.status_code == 200
    body = r.json()
    assert body["loaded"] is False and body["code"] == "STAGED_LIBRARY_ABSENT"
    assert body["scenarios"] == [] and body["total"] == 0


def test_a_manifest_pair_with_no_scenario_file_raises(client, staged_base):
    root = staged_base / staged_library.STAGING_RELPATH
    (root / "scenarios" / "sim-draft-901-underived.yml").unlink()
    r = client.get("/api/scenarios/staged")
    assert r.status_code == 500
    assert r.json()["detail"]["code"] == "STAGED_LIBRARY_UNREADABLE"


@pytest.mark.skipif(not (REAL / "MANIFEST.json").is_file(), reason="no staged library in this tree")
def test_the_real_staged_library_lists_every_manifest_pair(client):
    manifest = json.loads((REAL / "MANIFEST.json").read_text())
    body = client.get("/api/scenarios/staged").json()
    assert body["loaded"] is True
    assert body["total"] == len(manifest["pairs"])
    assert {s["scenario_id"] for s in body["scenarios"]} == {p["scenario"] for p in manifest["pairs"]}
    assert all(s["staged"]["launchable"] is False for s in body["scenarios"])
