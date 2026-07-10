"""
CortexSim API — /api/mitre router.

Endpoints:
  GET /api/mitre/coverage  — MITRE ATT&CK technique coverage across all scenarios
                             and runs, fused with the authored TTP card corpus.

Coverage model (GAP-6 / GAP-5 / GAP-API-009)
--------------------------------------------
The heatmap used to be computed *only* from the thin ``Scenario`` ORM view
(primary technique + per-step techniques), which made the customer-facing
coverage view the LEAST complete view that existed — it ignored the 82-technique
TTP card corpus, the scenario ``additional_techniques`` field, and correlation /
analytics-mapped content.

This router now fuses four sources into one technique map:

  1. Scenario **primary** technique (``Scenario.mitre_technique``).
  2. Scenario **per-step** techniques (``steps[].mitre_technique``).
  3. Scenario **additional_techniques** (``Scenario.additional_techniques`` —
     the field that previously dropped at load; see GAP-5).
  4. The **TTP card corpus** (``mitre_attack.techniques[]``), joined to scenarios
     through every ``expected_detections[].ttp_ref``. This pulls in the full
     authored multi-technique surface (sub-techniques and correlated families)
     that the scenario-level mapping flattens away.

The response shape is **backward-compatible** with the existing UI heatmap —
every previously-emitted key is preserved. New, additive fields per technique:
``sources`` (which of scenario/step/additional/card contributed it),
``card_refs`` (the TTP cards covering it), ``in_card_corpus`` (bool), and
``detection_kinds`` (per-kind tally of deployable card detections, including
``correlation`` and named ``analytics`` modules).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from engine.ttp_catalog import catalog
from models import Result, Run, Scenario

logger = logging.getLogger("cortexsim.api.mitre")

router = APIRouter(prefix="/mitre", tags=["mitre"])


@router.get("/atlas/coverage")
async def get_atlas_coverage(db: AsyncSession = Depends(get_db)):
    """MITRE ATLAS coverage for the AI planes (GAP-9).

    The AI planes (AIRS / AI Access / AI-SPM) carry threats ATT&CK has no native
    home for — prompt injection, jailbreak, model data leakage, cost harvesting.
    Those are mapped on the TTP cards as ``mitre_attack.atlas_techniques[]``.
    ATT&CK coverage (``/api/mitre/coverage``) is blind to them, so this endpoint
    aggregates the ATLAS surface separately: per ATLAS technique, which cards and
    scenarios reach it and on which planes.

    Additive, never replaces ATT&CK — a card may (and the AI cards do) carry
    both an ATT&CK technique AND an ATLAS technique for the same step.
    """
    scenarios = list((await db.execute(select(Scenario))).scalars().all())

    # ttp_ref -> set of (scenario_id, plane) that reach the card.
    ref_to_scenarios: dict[str, set] = {}
    for s in scenarios:
        for ref in _scenario_ttp_refs(s):
            ref_to_scenarios.setdefault(ref, set()).add((s.scenario_id, s.plane))

    atlas: dict[str, dict] = {}
    for ref, scen_pairs in ref_to_scenarios.items():
        for at in catalog.card_atlas_techniques(ref):
            aid = at["atlas_id"]
            entry = atlas.setdefault(aid, {
                "atlas_id": aid,
                "name": at["name"],
                "atlas_tactic": at["atlas_tactic"],
                "scenarios": set(),
                "planes": set(),
                "card_refs": set(),
            })
            if not entry["name"] and at["name"]:
                entry["name"] = at["name"]
            entry["card_refs"].add(ref)
            for scen_id, plane in scen_pairs:
                entry["scenarios"].add(scen_id)
                entry["planes"].add(plane)

    techniques = [
        {
            "atlas_id": e["atlas_id"],
            "name": e["name"],
            "atlas_tactic": e["atlas_tactic"],
            "scenarios": sorted(e["scenarios"]),
            "planes": sorted(e["planes"]),
            "card_refs": sorted(e["card_refs"]),
        }
        for e in sorted(atlas.values(), key=lambda x: x["atlas_id"])
    ]

    by_tactic: dict[str, dict] = {}
    for t in techniques:
        key = t["atlas_tactic"] or "(unspecified)"
        by_tactic.setdefault(key, {"atlas_tactic": key, "techniques": []})
        by_tactic[key]["techniques"].append(t)

    planes_covered = sorted({p for t in techniques for p in t["planes"]})
    logger.info("atlas_coverage atlas_techniques=%d planes=%s",
                len(techniques), planes_covered)
    return {
        "atlas_techniques": techniques,
        "by_tactic": sorted(by_tactic.values(), key=lambda x: x["atlas_tactic"]),
        "summary": {
            "total_atlas_techniques": len(techniques),
            "planes_covered": planes_covered,
            "cards_with_atlas": len({r for t in techniques for r in t["card_refs"]}),
        },
    }


def _scenario_ttp_refs(scenario: Scenario) -> set[str]:
    """Every distinct ``ttp_ref`` a scenario references through its steps'
    ``expected_detections[]``. This is the join key into the TTP card corpus."""
    refs: set[str] = set()
    for step in (scenario.steps or []):
        if not isinstance(step, dict):
            continue
        for ed in (step.get("expected_detections") or []):
            if isinstance(ed, dict) and ed.get("ttp_ref"):
                refs.add(ed["ttp_ref"])
    return refs


def _additional_technique_ids(scenario: Scenario) -> list[str]:
    """Normalized list of technique ids from ``Scenario.additional_techniques``.

    Lane A persists this as ``[{"technique": <id>, "name": <str>}, ...]`` but we
    defensively accept a bare-string element too (older/hand-edited rows)."""
    out: list[str] = []
    for entry in (scenario.additional_techniques or []):
        if isinstance(entry, dict):
            tid = entry.get("technique")
        elif isinstance(entry, str):
            tid = entry
        else:
            tid = None
        if tid:
            out.append(tid)
    return out


@router.get("/coverage")
async def get_mitre_coverage(
    db: AsyncSession = Depends(get_db),
    include_inactive: bool = False,
):
    """
    Returns MITRE ATT&CK coverage data for the heatmap visualization.

    Coverage is fused from the scenario ORM (primary + step + additional
    techniques) AND the authored TTP card corpus (joined via each scenario's
    ``expected_detections[].ttp_ref``). See module docstring for the model.

    Query params:
      - ``include_inactive`` (bool, default ``False``) — GAP-API-009. By
        default only ``status == 'active'`` scenarios contribute, matching the
        historical behavior. Pass ``true`` to count authored-but-not-active
        scenarios (draft/deprecated) so the heatmap reflects the full authored
        corpus rather than just the launchable subset.

    For each technique referenced by scenarios + their cards:
      - scenarios: list of scenario IDs that target this technique
      - planes: detection planes that reach this technique
      - has_run: true if at least one targeting scenario has a run
      - observed_detections / total_detections: from validated Result rows
      - status: "detected" | "run_not_detected" | "not_run" | "no_scenario"
      - sources: which of {scenario, step, additional, card} contributed it
      - card_refs: TTP cards (by ttp_ref) whose corpus covers this technique
      - in_card_corpus: true if the card corpus documents this technique
      - detection_kinds: {bioc, xql, correlation, ioc, analytics} card tallies
    """
    # Collect scenarios — active-only by default (GAP-API-009).
    scen_stmt = select(Scenario)
    if not include_inactive:
        scen_stmt = scen_stmt.where(Scenario.status == "active")
    scen_result = await db.execute(scen_stmt)
    scenarios = scen_result.scalars().all()

    # Collect all runs
    runs_result = await db.execute(select(Run))
    runs = runs_result.scalars().all()
    run_scenario_ids = {r.scenario_id for r in runs}

    # Collect all results
    results_result = await db.execute(select(Result))
    all_results = results_result.scalars().all()

    # Build technique -> data mapping
    techniques: dict[str, dict] = {}

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
                # Additive (GAP-6): provenance + card-corpus enrichment.
                "sources": set(),
                "card_refs": set(),
                "detection_kinds": {
                    "bioc": 0, "xql": 0, "correlation": 0,
                    "ioc": 0, "analytics": 0,
                    # Plan 01 ABIOC (validated behavioral-ML) +
                    # Plan 02 modeling substrate (informational only).
                    "abioc": 0, "modeling": 0,
                },
            }
        else:
            # Backfill a friendlier name/tactic if the first sighting was a
            # bare id (e.g. a step technique with no name) and a later source
            # carries the human label.
            entry = techniques[tid]
            if (entry["technique_name"] == tid or not entry["technique_name"]) and tname and tname != tid:
                entry["technique_name"] = tname
            if not entry["tactic_id"] and tactic_id:
                entry["tactic_id"] = tactic_id
            if not entry["tactic_name"] and tactic_name:
                entry["tactic_name"] = tactic_name

    def _attach_scenario(tid: str, scenario: Scenario, source: str):
        entry = techniques[tid]
        if scenario.scenario_id not in entry["scenarios"]:
            entry["scenarios"].append(scenario.scenario_id)
        entry["planes"].add(scenario.plane)
        entry["sources"].add(source)
        if scenario.scenario_id in run_scenario_ids:
            entry["has_run"] = True

    for s in scenarios:
        # (1) primary technique
        _ensure(s.mitre_technique, s.mitre_technique_name, s.mitre_tactic, s.mitre_tactic_name)
        _attach_scenario(s.mitre_technique, s, "scenario")

        # (2) per-step techniques (may differ from the primary)
        for step in (s.steps or []):
            if not isinstance(step, dict):
                continue
            step_tech = step.get("mitre_technique")
            if step_tech and step_tech != s.mitre_technique:
                _ensure(step_tech, step_tech, s.mitre_tactic, s.mitre_tactic_name)
                _attach_scenario(step_tech, s, "step")

        # (3) additional_techniques (GAP-5 — previously dropped at load)
        for entry in (s.additional_techniques or []):
            if isinstance(entry, dict):
                tid = entry.get("technique")
                tname = entry.get("name") or tid
            elif isinstance(entry, str):
                tid, tname = entry, entry
            else:
                continue
            if not tid:
                continue
            _ensure(tid, tname, s.mitre_tactic, s.mitre_tactic_name)
            _attach_scenario(tid, s, "additional")

        # (4) TTP card corpus fusion (GAP-6) — pull the authoritative
        #     multi-technique view + detection-kind tally for every card the
        #     scenario references, joined via expected_detections[].ttp_ref.
        for ttp_ref in _scenario_ttp_refs(s):
            kind_counts = catalog.card_detection_kind_counts(ttp_ref)
            for ct in catalog.card_techniques(ttp_ref):
                tid = ct["technique"]
                _ensure(tid, ct["name"], ct["tactic_id"], ct["tactic_name"])
                _attach_scenario(tid, s, "card")
                entry = techniques[tid]
                entry["card_refs"].add(ttp_ref)
                # Fold the card's deployable-detection tally into the
                # technique so the heatmap can surface correlation / analytics
                # depth (which the scenario layer is blind to — GAP-2/GAP-11).
                for k, v in kind_counts.items():
                    entry["detection_kinds"][k] += v

    # From results: count detections per technique
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
            if isinstance(step, dict) and step.get("id") == result.step_id:
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
            # Additive (GAP-6) — provenance + card-corpus enrichment.
            "sources": sorted(data["sources"]),
            "card_refs": sorted(data["card_refs"]),
            "in_card_corpus": len(data["card_refs"]) > 0,
            "detection_kinds": data["detection_kinds"],
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
    # Additive — how much of the surface is card-corpus-backed vs scenario-only.
    in_card_corpus = sum(1 for t in output if t["in_card_corpus"])
    with_correlation = sum(1 for t in output if t["detection_kinds"]["correlation"] > 0)
    # ABIOC counts as validated behavioral-ML depth (Plan 01); modeling is the
    # XDM normalization substrate present beneath a technique (Plan 02,
    # informational — NOT validated detection).
    with_abioc = sum(1 for t in output if t["detection_kinds"]["abioc"] > 0)
    with_modeling = sum(1 for t in output if t["detection_kinds"]["modeling"] > 0)

    logger.info(
        "mitre_coverage techniques=%d detected=%d in_card_corpus=%d include_inactive=%s",
        total_techniques, detected_count, in_card_corpus, include_inactive,
    )

    return {
        "techniques": output,
        "by_tactic": sorted(by_tactic.values(), key=lambda x: x["tactic_id"]),
        "summary": {
            "total_techniques": total_techniques,
            "detected": detected_count,
            "run_not_detected": run_not_detected,
            "not_run": not_run,
            # Additive — does not break the existing UI which reads the 4 keys above.
            "in_card_corpus": in_card_corpus,
            "with_correlation": with_correlation,
            "with_abioc": with_abioc,
            "with_modeling": with_modeling,
            "include_inactive": include_inactive,
        },
    }
