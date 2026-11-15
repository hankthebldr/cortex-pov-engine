"""The ★ on scenario rows: the system's best scenarios.

Henry (2026-11-13): favorites are "the best in the system", measured rather than
hand-picked. ``engine.lab_readiness.favorite_reasons`` is the rule; the list and
detail routes stamp ``favorite`` / ``favorite_why`` from it, and the Composer
palette's ★ tab reads those fields.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from engine.lab_readiness import favorite_reasons, favorites

REPO_ROOT = Path(__file__).resolve().parents[2]

BEST = {
    "status": "active",
    "tier": "GREEN",
    "signal_ratio": 1.0,
    "detection_steps_without_signal": 0,
    "detection_types": ["BIOC", "XQL", "Correlation", "ABIOC"],
}


def test_a_scenario_meeting_every_condition_is_starred_with_its_reasons():
    assert favorite_reasons(BEST) == [
        "Runs offline on a lab target",
        "Every step makes real telemetry",
        "Raises a Correlation",
        "4 detection types: BIOC, XQL, Correlation, ABIOC",
    ]


@pytest.mark.parametrize("change", [
    {"tier": "YELLOW"},                                   # needs egress or consent
    {"tier": "RED"},                                      # signal-free
    {"signal_ratio": 0.8},                                # a narration-only step
    {"detection_steps_without_signal": 1},
    {"detection_types": ["BIOC", "XQL", "IOC", "ABIOC"]},  # no Correlation
    {"detection_types": ["BIOC", "XQL", "Correlation"]},   # three types
    {"status": "draft"},
])
def test_missing_any_condition_removes_the_star(change):
    assert favorite_reasons({**BEST, **change}) == []


def test_runtime_stars_match_the_committed_manifest():
    """SimCore stars exactly what the lab-readiness manifest's rows earn."""
    manifest = json.loads((REPO_ROOT / "docs/reference/lab-readiness.json").read_text())
    expected = {r["scenario_id"] for r in manifest["scenarios"] if favorite_reasons(r)}
    assert expected, "the corpus has no favorite at all"
    assert set(favorites(str(REPO_ROOT))) == expected


@pytest.fixture
def client(make_client):
    from api.scenarios import router  # noqa: PLC0415
    return make_client(router)


def _seed(session_factory, *ids):
    from models import Scenario  # noqa: PLC0415

    async def _do():
        async with session_factory() as db:
            for sid in ids:
                db.add(Scenario(
                    scenario_id=sid, name=sid, plane="EDR", version="1.0", status="active",
                    uc_ref="UCS-EDR-01", uc_name="X", tc_ref="TC-EDR-01", tc_name="Y",
                    mitre_tactic="TA0006", mitre_tactic_name="Credential Access",
                    mitre_technique="T1003.001", mitre_technique_name="LSASS Memory",
                    steps=[{"id": "step-01", "command": "true"}],
                ))
            await db.commit()

    asyncio.run(_do())


def test_list_and_detail_rows_carry_the_star(client, session_factory):
    # SIM-EDR-006 earns the star in the corpus; SIM-ASM-005 is signal-free (RED).
    _seed(session_factory, "SIM-EDR-006", "SIM-ASM-005")

    rows = {r["scenario_id"]: r for r in client.get("/api/scenarios").json()["scenarios"]}
    assert rows["SIM-EDR-006"]["favorite"] is True
    assert rows["SIM-EDR-006"]["favorite_why"][0] == "Runs offline on a lab target"
    assert rows["SIM-ASM-005"]["favorite"] is False
    assert rows["SIM-ASM-005"]["favorite_why"] == []

    detail = client.get("/api/scenarios/SIM-EDR-006").json()
    assert detail["favorite"] is True
    assert detail["favorite_why"] == rows["SIM-EDR-006"]["favorite_why"]
