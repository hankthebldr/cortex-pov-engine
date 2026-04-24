"""
CortexSim API — /api/mitre router.

Endpoints:
  GET /api/mitre/coverage  — MITRE ATT&CK technique coverage across all scenarios and runs
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Result, Run, Scenario

logger = logging.getLogger("cortexsim.api.mitre")

router = APIRouter(prefix="/mitre", tags=["mitre"])


@router.get("/coverage")
async def get_mitre_coverage(db: AsyncSession = Depends(get_db)):
    """
    Returns MITRE ATT&CK coverage data for the heatmap visualization.

    For each technique referenced in scenarios + results:
      - has_scenario: true if at least one scenario targets this technique
      - scenarios: list of scenario IDs that target it
      - has_run: true if at least one run has been launched
      - observed: number of detections confirmed for this technique
      - total: total expected detections for this technique
      - status: "detected" | "run_not_detected" | "not_run" | "no_scenario"
    """
    # Collect all scenarios
    scen_result = await db.execute(select(Scenario).where(Scenario.status == "active"))
    scenarios = scen_result.scalars().all()

    # Collect all runs
    runs_result = await db.execute(select(Run))
    runs = runs_result.scalars().all()
    run_scenario_ids = {r.scenario_id for r in runs}

    # Collect all results
    results_result = await db.execute(select(Result))
    all_results = results_result.scalars().all()

    # Build technique -> data mapping
    techniques = {}

    def _ensure(tid, tname, tactic_id, tactic_name):
        if tid not in techniques:
            techniques[tid] = {
                "technique_id": tid,
                "technique_name": tname,
                "tactic_id": tactic_id,
                "tactic_name": tactic_name,
                "scenarios": [],
                "planes": set(),
                "has_run": False,
                "total_detections": 0,
                "observed_detections": 0,
            }

    # From scenarios: primary technique
    for s in scenarios:
        _ensure(s.mitre_technique, s.mitre_technique_name, s.mitre_tactic, s.mitre_tactic_name)
        techniques[s.mitre_technique]["scenarios"].append(s.scenario_id)
        techniques[s.mitre_technique]["planes"].add(s.plane)
        if s.scenario_id in run_scenario_ids:
            techniques[s.mitre_technique]["has_run"] = True

    # From scenario steps: per-step technique (may differ from scenario-level)
    for s in scenarios:
        for step in (s.steps or []):
            step_tech = step.get("mitre_technique")
            if step_tech and step_tech != s.mitre_technique:
                _ensure(step_tech, step_tech, s.mitre_tactic, s.mitre_tactic_name)
                if s.scenario_id not in techniques[step_tech]["scenarios"]:
                    techniques[step_tech]["scenarios"].append(s.scenario_id)
                techniques[step_tech]["planes"].add(s.plane)
                if s.scenario_id in run_scenario_ids:
                    techniques[step_tech]["has_run"] = True

    # From results: count detections per technique
    # Map run_id -> scenario steps for technique lookup
    run_to_scenario = {r.run_id: r.scenario_id for r in runs}
    scenario_map = {s.scenario_id: s for s in scenarios}

    for result in all_results:
        scen_id = run_to_scenario.get(result.run_id)
        scen = scenario_map.get(scen_id) if scen_id else None
        if not scen:
            continue

        # Find the technique for this result's step
        tech_id = scen.mitre_technique  # fallback
        for step in (scen.steps or []):
            if step.get("id") == result.step_id:
                tech_id = step.get("mitre_technique", tech_id)
                break

        if tech_id in techniques:
            techniques[tech_id]["total_detections"] += 1
            if result.observed:
                techniques[tech_id]["observed_detections"] += 1

    # Compute status and serialize
    output = []
    for tid, data in sorted(techniques.items()):
        has_scenario = len(data["scenarios"]) > 0
        has_run = data["has_run"]
        observed = data["observed_detections"]
        total = data["total_detections"]

        if observed > 0:
            status = "detected"
        elif has_run and total > 0:
            status = "run_not_detected"
        elif has_scenario:
            status = "not_run"
        else:
            status = "no_scenario"

        coverage_pct = round(observed / total * 100, 1) if total > 0 else 0.0

        output.append({
            "technique_id": tid,
            "technique_name": data["technique_name"],
            "tactic_id": data["tactic_id"],
            "tactic_name": data["tactic_name"],
            "scenarios": data["scenarios"],
            "planes": sorted(data["planes"]),
            "status": status,
            "total_detections": total,
            "observed_detections": observed,
            "coverage_pct": coverage_pct,
        })

    # Group by tactic for the matrix layout
    by_tactic = {}
    for t in output:
        tid = t["tactic_id"]
        if tid not in by_tactic:
            by_tactic[tid] = {"tactic_id": tid, "tactic_name": t["tactic_name"], "techniques": []}
        by_tactic[tid]["techniques"].append(t)

    # Summary stats
    total_techniques = len(output)
    detected_count = sum(1 for t in output if t["status"] == "detected")
    run_not_detected = sum(1 for t in output if t["status"] == "run_not_detected")
    not_run = sum(1 for t in output if t["status"] == "not_run")

    logger.info("mitre_coverage techniques=%d detected=%d", total_techniques, detected_count)

    return {
        "techniques": output,
        "by_tactic": sorted(by_tactic.values(), key=lambda x: x["tactic_id"]),
        "summary": {
            "total_techniques": total_techniques,
            "detected": detected_count,
            "run_not_detected": run_not_detected,
            "not_run": not_run,
        },
    }
