"""
CortexSim API — ingestion doors, read side.

  GET /api/doors                          — every door (per scope instance) with its state and
                                            how many corpus detections name it (Composer drafts
                                            excluded; collector-route detections, route-blocked or
                                            not, count under http_collector)
  GET /api/doors/readiness?agent_id=      — door readiness of every loaded scenario, in one fetch
  GET /api/scenarios/{scenario_id}/doors  — one scenario's readiness plus each detection's requirement

Read-only, local files and the database only: zero outbound calls, and no tenant claim
(``tenant_verified`` is 0). An absent dataset requirement registry answers 200 with
``registry_loaded: false`` — nothing can be derived without it, and that is degraded, never
"no requirements". Shapes this module does not recognise raise (a structured 500).

Mount this router BEFORE ``scenarios_router`` in main.py so the sub-path is not swallowed.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from engine import door_state
from engine.dataset_requirements import DatasetRequirementsError, load
from engine.door_resolution import DoorResolutionError, detection_requirement, scenario_readiness
from engine.ingestion_doors import DOORS, door_key
from engine.ttp_catalog import catalog
from models import Scenario

logger = logging.getLogger("cortexsim.api.doors")

router = APIRouter(tags=["doors"])


def _error(status: int, error: str, code: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": error, "code": code, "detail": detail})


def _malformed(exc: Exception) -> JSONResponse:
    logger.error("dataset requirement registry malformed: %s", exc)
    return _error(500, "Dataset requirement registry is malformed", "DATASET_REGISTRY_MALFORMED", str(exc))


def _derivation(exc: Exception) -> JSONResponse:
    logger.error("door derivation failed: %s", exc)
    return _error(500, "A detection card or scenario step has an unrecognised shape",
                  "DOOR_DERIVATION_FAILED", str(exc))


def _not_found(scenario_id: str) -> JSONResponse:
    return _error(404, "Scenario not found", "SCENARIO_NOT_FOUND", f"scenario_id='{scenario_id}'")


async def _open_keys(db: AsyncSession) -> set[str]:
    state = await door_state.current(db)
    return {k for k, v in state.items() if v["state"] == "open"}


def _iso(value) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _detections(scenario: dict):
    """Every (step, detection) pair of a scenario, shapes checked."""
    steps = scenario.get("steps")
    if not isinstance(steps, list):
        raise DoorResolutionError(f"scenario {scenario.get('scenario_id')!r}: steps must be a list")
    for step in steps:
        if not isinstance(step, dict):
            raise DoorResolutionError(f"scenario {scenario.get('scenario_id')!r}: a step must be a mapping")
        dets = step.get("expected_detections")
        if dets is None:
            continue
        if not isinstance(dets, list):
            raise DoorResolutionError(
                f"scenario {scenario.get('scenario_id')!r} step {step.get('id')!r}: "
                f"expected_detections must be a list")
        for det in dets:
            yield step, det


def _door_rows(state: dict[str, dict], facts: dict[str, str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for door in DOORS:
        keys = sorted({k for k in (*facts, *state)
                       if k == door.id or k.startswith(door.id + ":")})
        if door.scope == "tenant":
            keys = [door_key(door.id, None)]
        elif not keys:
            keys = [door.id]  # an agent- or target-scoped door with no instance yet
        for key in keys:
            ev = state.get(key)
            fact = facts.get(key, "authored")
            if ev is not None and ev["state"] != "unknown":
                st, basis, rung = ev["state"], ev["basis"], ev["rung"]
            else:
                st = "unknown"
                basis, rung = None, fact
            rows.append({
                "key": key, "door_id": door.id, "label": door.label, "scope": door.scope,
                "state": st, "basis": basis, "rung": rung,
                "last_conclusive_at": _iso(ev["last_conclusive_at"]) if ev else None,
                "age_seconds": ev["age_seconds"] if ev else None,
                "wizard": door.wizard, "detections_needing": 0,
            })
    return rows


@router.get("/doors")
async def list_doors(db: AsyncSession = Depends(get_db)):
    try:
        registry = load()
    except DatasetRequirementsError as exc:
        return _malformed(exc)
    rows = _door_rows(await door_state.current(db), await door_state.configured_facts(db))
    needing: Optional[dict[str, int]] = None
    if registry is not None:
        needing = {}
        # A Composer draft is not corpus coverage, so it never adds to a door's count.
        scenarios = (await db.execute(select(Scenario).where(Scenario.status != "draft"))).scalars().all()
        try:
            for sc in scenarios:
                for step, det in _detections(sc.to_dict()):
                    req = detection_requirement(step, det, catalog=catalog, registry=registry)
                    for door_id in {d for el in req.all_of for d in el}:
                        needing[door_id] = needing.get(door_id, 0) + 1
        except DoorResolutionError as exc:
            return _derivation(exc)
    for row in rows:
        row["detections_needing"] = None if needing is None else needing.get(row["door_id"], 0)
    return {"doors": rows, "registry_loaded": registry is not None,
            "tenant_verified": 0, "outbound_calls": 0}


@router.get("/doors/readiness")
async def doors_readiness(agent_id: Optional[str] = Query(None), db: AsyncSession = Depends(get_db)):
    try:
        registry = load()
    except DatasetRequirementsError as exc:
        return _malformed(exc)
    scenarios = (await db.execute(select(Scenario).order_by(Scenario.scenario_id))).scalars().all()
    if registry is None:
        return {"registry_loaded": False, "agent_scoped_evaluated": agent_id is not None,
                "scenarios": {sc.scenario_id: {"scenario_id": sc.scenario_id, "state": "not_evaluated",
                                               "reason": "REGISTRY_ABSENT"} for sc in scenarios},
                "tenant_verified": 0, "outbound_calls": 0}
    open_keys = await _open_keys(db)
    try:
        out = {sc.scenario_id: scenario_readiness(sc.to_dict(), open_keys, agent_id=agent_id,
                                                  catalog=catalog, registry=registry)
               for sc in scenarios}
    except DoorResolutionError as exc:
        return _derivation(exc)
    return {"registry_loaded": True, "agent_scoped_evaluated": agent_id is not None,
            "scenarios": out, "tenant_verified": 0, "outbound_calls": 0}


@router.get("/scenarios/{scenario_id}/doors")
async def scenario_doors(scenario_id: str, agent_id: Optional[str] = Query(None),
                         db: AsyncSession = Depends(get_db)):
    sc: Optional[Scenario] = (await db.execute(
        select(Scenario).where(Scenario.scenario_id == scenario_id))).scalar_one_or_none()
    if sc is None:
        return _not_found(scenario_id)
    try:
        registry = load()
    except DatasetRequirementsError as exc:
        return _malformed(exc)
    if registry is None:
        return {"scenario_id": scenario_id, "registry_loaded": False,
                "code": "REGISTRY_ABSENT", "reason": "REGISTRY_ABSENT",
                "readiness": {"scenario_id": scenario_id, "state": "not_evaluated",
                              "reason": "REGISTRY_ABSENT"},
                "detections": [], "tenant_verified": 0, "outbound_calls": 0}
    open_keys = await _open_keys(db)
    data = sc.to_dict()
    try:
        readiness = scenario_readiness(data, open_keys, agent_id=agent_id, catalog=catalog, registry=registry)
        dets = [{"step_id": step.get("id"), "detection_id": det.get("detection_id"),
                 "requirement": detection_requirement(step, det, catalog=catalog,
                                                      registry=registry).to_json()}
                for step, det in _detections(data)]
    except DoorResolutionError as exc:
        return _derivation(exc)
    return {"scenario_id": scenario_id, "registry_loaded": True, "readiness": readiness,
            "detections": dets, "tenant_verified": 0, "outbound_calls": 0}
