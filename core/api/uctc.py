"""
CortexSim API — /api/uctc router. The FY27 v2.2 master index as a read surface.

The index answers "what should a POV prove"; the engine answers "what does it
actually prove". Until now those two lived in different files and only met at
boot, inside the scenario loader's foreign-key check. This router joins them so
a DC can see, per test case, whether any engine scenario evidences it — and
whether that test case can be *licensed* and *machine-scored* at all.

Three joins carry the whole surface:

* **evidence** — keyed on ``Scenario.tc_refs`` and nothing else. Joining
  through ``pov_scenario_id`` would over-claim wildly: ``POV-SC-001`` binds 21
  test cases and is flagged ``SPLIT REQUIRED``, so one scenario would appear to
  prove 21 things it fires all at once.
* **entitlement** — always via ``registry.entitlements_for``, which resolves
  through the use case and drops capacity SKUs. Reading ``tc.required_addon``
  raw would contradict ``POST /api/pov/scope`` on the same test case.
* **scoreability** — ``TestCase.is_scoreable``. 57 of the 107 detection-backable
  rows carry no measurable threshold, so a verdict of ``pass`` is impossible for
  them by construction. Surfacing that is the point, not a footnote.

Endpoints:
  GET /api/uctc/summary                  — header counts, one call
  GET /api/uctc/use-cases                — UC list with per-UC coverage
  GET /api/uctc/use-cases/{uc_id}        — one UC + its UCS groups + its TCs
  GET /api/uctc/test-cases               — the main table (no pagination)
  GET /api/uctc/test-cases/{tc_id}       — full detail: measurement contract,
                                           entitlement, payload, evidence, runs
  GET /api/uctc/coverage                 — every rollup in one payload
  GET /api/uctc/gaps                     — the unevidenced list
  GET /api/uctc/payloads                 — POV-SC payloads + engine usage
  GET /api/uctc/by-scenario/{id}         — the forward view for a deep link

Read-only by design. The index is a versioned file artifact under
``docs/uc_tc_mapping/``; edits land there and in the crosswalk script, never
through the API.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from engine.uctc_registry import registry
from models import Run, Scenario

logger = logging.getLogger("cortexsim.api.uctc")

router = APIRouter(prefix="/uctc", tags=["uctc"])


# ---------------------------------------------------------------------------
# Envelope + small helpers
# ---------------------------------------------------------------------------


def _envelope() -> dict[str, Any]:
    """Every response states whether the snapshot is present on this box.

    The registry is deliberately fail-soft — the prod image has historically
    shipped without ``docs/`` — so a stripped deploy must render as an explicit
    degraded state. A caller that cannot tell "index missing" from "266 rows,
    none evidenced" will report a zero as a coverage finding.
    """
    return {"index_loaded": registry.loaded, "index_version": registry.version or None}


def _split_caps(value: Optional[str]) -> list[str]:
    """Split the index's ' · '-delimited capability strings. '—' means none."""
    if not value:
        return []
    return [p.strip() for p in value.split("·") if p.strip() and p.strip() != "—"]


def _csv_set(value: Optional[str]) -> Optional[set[str]]:
    """Parse a comma-list filter (``DET,HNT``) into an upper-cased set.

    ``None`` means "no filter" — distinct from an empty set, which would match
    nothing. Comparison sites upper-case the field so mixed-case index values
    (``SecOps``) still match.
    """
    if not value:
        return None
    return {v.strip().upper() for v in value.split(",") if v.strip()}


def _pct(numerator: int, denominator: int) -> float:
    return round(numerator / denominator * 100, 1) if denominator else 0.0


def _tally(values: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        if v:
            out[v] = out.get(v, 0) + 1
    return out


# ---------------------------------------------------------------------------
# The reverse index — which engine scenarios evidence which test case
# ---------------------------------------------------------------------------


def _scenario_tc_refs(scenario: Scenario) -> list[str]:
    """A scenario's full evidence set, mirroring ``Scenario.to_dict()``.

    The fallback matters: rows written before the ``tc_refs`` column existed
    carry an empty list and a valid ``tc_ref``, and dropping those would silently
    remove them from coverage.
    """
    refs = list(scenario.tc_refs or [])
    if scenario.tc_ref and scenario.tc_ref not in refs:
        refs.insert(0, scenario.tc_ref)
    return refs


async def _evidence_index(
    db: AsyncSession, include_inactive: bool = False
) -> dict[str, list[Scenario]]:
    """Build ``tc_id -> [Scenario]`` in one pass over the catalog.

    ``tc_refs`` is a JSON column and SQLite has no portable accessor for it, so
    the containment test happens in Python after a full select — the same
    trade-off ``api/scenarios._scenario_cites_ttp`` makes, and the catalog is
    small enough (161 rows) that a scan is cheaper than the alternative.
    """
    stmt = select(Scenario)
    if not include_inactive:
        stmt = stmt.where(Scenario.status == "active")   # active-only default (GAP-API-009)
    idx: dict[str, list[Scenario]] = {}
    for s in (await db.execute(stmt)).scalars().all():
        for tc_id in _scenario_tc_refs(s):
            idx.setdefault(tc_id, []).append(s)
    return idx


def _evidence_block(tc, scenarios: list[Scenario]) -> dict[str, Any]:
    """The per-TC evidence sub-object shared by every list endpoint.

    ``tier_disagreement`` is the S-13 signal (scenario ``moat_tier`` vs the
    index's ``differentiation_tier``). It is informational only — 101 of these
    are deliberate positioning calls, so rendering them as defects would make
    the surface read as 101 bugs.
    """
    return {
        "evidenced": bool(scenarios),
        "scenario_count": len(scenarios),
        "scenario_ids": sorted(s.scenario_id for s in scenarios),
        "planes": sorted({s.plane for s in scenarios if s.plane}),
        "scenario_moat_tiers": sorted({s.moat_tier for s in scenarios if s.moat_tier}),
        "tier_disagreement": any(
            s.moat_tier and tc.differentiation_tier and s.moat_tier != tc.differentiation_tier
            for s in scenarios
        ),
    }


def _tc_summary(tc, scenarios: list[Scenario]) -> dict[str, Any]:
    """Row shape for the main table.

    Built here rather than from ``TestCase.to_dict()`` because that method omits
    nine fields this surface needs, and the dataclass is frozen and shared with
    the scenario loader's FK check — widening it is somebody else's decision.
    """
    return {
        "tc_id": tc.tc_id,
        "ucs_id": tc.ucs_id,
        "ucs_name": tc.scenario,
        "uc_id": tc.uc_id,
        "use_case": tc.use_case,
        "title": tc.title,
        "tc_sheet": tc.tc_sheet,
        "status": tc.status,
        "validation_class": tc.validation_class,
        "priority": tc.priority,
        "differentiation_tier": tc.differentiation_tier,
        "moat_classification": tc.moat_classification,
        "primary_kpi": tc.primary_kpi,
        "threshold": tc.threshold,
        "is_scoreable": tc.is_scoreable,
        "needs_detection_content": tc.needs_detection_content,
        "detection_source": tc.detection_source,
        "detection_type": tc.detection_type,
        "target_dataset": tc.target_dataset,
        "pov_scenario_id": tc.pov_scenario_id,
        "evidence": _evidence_block(tc, scenarios),
    }


def _uc_entry(uc, tcs: list, evidence: dict[str, list[Scenario]]) -> dict[str, Any]:
    """One use-case row: the index fields plus what the engine proves of it."""
    evidenced = [tc for tc in tcs if evidence.get(tc.tc_id)]
    det = [tc for tc in tcs if tc.needs_detection_content]
    det_evidenced = [tc for tc in det if evidence.get(tc.tc_id)]
    scenarios = {s.scenario_id: s for tc in tcs for s in evidence.get(tc.tc_id, [])}

    return {
        **uc.to_dict(),
        # The license contract is authored as one ' · '-joined string; the UI
        # renders chips, so split once here rather than in every consumer.
        "base_platform_list": _split_caps(uc.base_platform),
        "addons_list": _split_caps(uc.addon),
        "counts": {
            "ucs_groups": len({tc.ucs_id for tc in tcs}),
            "test_cases": len(tcs),
            "detection_backable": len(det),
            "scoreable": len([tc for tc in tcs if tc.is_scoreable]),
            "evidenced": len(evidenced),
            "evidenced_detection_backable": len(det_evidenced),
            "by_validation_class": _tally([tc.validation_class for tc in tcs]),
            "by_tier": _tally([tc.differentiation_tier for tc in tcs]),
        },
        "coverage_pct": _pct(len(evidenced), len(tcs)),
        "det_coverage_pct": _pct(len(det_evidenced), len(det)),
        "scenario_count": len(scenarios),
        "planes": sorted({s.plane for s in scenarios.values() if s.plane}),
    }


def _tcs_by_uc() -> dict[str, list]:
    out: dict[str, list] = {}
    for tc in registry.all_test_cases():
        out.setdefault(tc.uc_id, []).append(tc)
    return out


# ---------------------------------------------------------------------------
# Routes — every segment after /uctc is literal. Declaring a bare GET /{x} on
# this router would shadow the collections under first-match-wins ordering
# (the same trap /api/ttps/_schema had to be hoisted above).
# ---------------------------------------------------------------------------


@router.get("/summary")
async def get_summary(
    include_inactive: bool = Query(False, description="Count draft/deprecated scenarios as evidence"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Header numbers for the whole surface in one call."""
    tcs = registry.all_test_cases()
    evidence = await _evidence_index(db, include_inactive)

    det = [tc for tc in tcs if tc.needs_detection_content]
    evidenced = [tc for tc in tcs if evidence.get(tc.tc_id)]
    det_evidenced = [tc for tc in det if evidence.get(tc.tc_id)]
    scenarios = {s.scenario_id: s for rows in evidence.values() for s in rows}

    logger.info(
        "uctc summary loaded=%s test_cases=%d evidenced=%d det_evidenced=%d",
        registry.loaded, len(tcs), len(evidenced), len(det_evidenced),
    )
    return {
        **_envelope(),
        "totals": {
            "use_cases": len(registry.all_use_cases()),
            "ucs_groups": len({tc.ucs_id for tc in tcs}),
            "test_cases": len(tcs),
            "payloads": len(registry.all_payloads()),
            "skus": len(registry.all_skus()),
        },
        "by_validation_class": _tally([tc.validation_class for tc in tcs]),
        "by_tier": _tally([tc.differentiation_tier for tc in tcs]),
        "by_priority": _tally([tc.priority for tc in tcs]),
        "by_sheet": _tally([tc.tc_sheet for tc in tcs]),
        "evidence": {
            "scenarios": len(scenarios),
            "evidenced": len(evidenced),
            "evidenced_pct": _pct(len(evidenced), len(tcs)),
            "detection_backable": len(det),
            "evidenced_detection_backable": len(det_evidenced),
            "detection_backable_pct": _pct(len(det_evidenced), len(det)),
            "unscoreable": len(registry.unscoreable()),
            "planes": sorted({s.plane for s in scenarios.values() if s.plane}),
        },
    }


@router.get("/use-cases")
async def list_use_cases(
    sheet: Optional[str] = Query(None, description="Keep UCs holding a TC on this sheet (SecOps|Cloud)"),
    subdomain: Optional[str] = Query(None, description="Filter by UC fy27_subdomain"),
    evidenced: str = Query("all", description="all | yes (fully) | no (none) | partial"),
    include_inactive: bool = Query(False),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """The 49 use cases, each carrying its own coverage arithmetic."""
    evidence = await _evidence_index(db, include_inactive)
    by_uc = _tcs_by_uc()
    sheets = _csv_set(sheet)

    entries: list[dict[str, Any]] = []
    for uc in registry.all_use_cases():
        tcs = by_uc.get(uc.uc_id, [])
        if sheets is not None:
            tcs = [tc for tc in tcs if (tc.tc_sheet or "").upper() in sheets]
            if not tcs:
                continue
        if subdomain and uc.fy27_subdomain != subdomain:
            continue

        entry = _uc_entry(uc, tcs, evidence)
        n_ev = entry["counts"]["evidenced"]
        n_tc = entry["counts"]["test_cases"]
        # "yes" means fully covered, not merely touched — a UC with 1 of 21 TCs
        # evidenced is the gap this surface exists to expose, not a win.
        if evidenced == "yes" and not (n_tc and n_ev == n_tc):
            continue
        if evidenced == "no" and n_ev:
            continue
        if evidenced == "partial" and not (0 < n_ev < n_tc):
            continue
        entries.append(entry)

    entries.sort(key=lambda e: e["uc_id"])
    logger.info(
        "uctc use_cases count=%d filters=sheet=%s subdomain=%s evidenced=%s",
        len(entries), sheet, subdomain, evidenced,
    )
    return {**_envelope(), "use_cases": entries, "total": len(entries)}


@router.get("/use-cases/{uc_id}")
async def get_use_case(
    uc_id: str,
    include_inactive: bool = Query(False),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """One use case, its UCS groups, and every test case underneath it."""
    uc = registry.uc(uc_id)
    if uc is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "Use case not found",
                "code": "UC_NOT_FOUND",
                "detail": f"uc_id='{uc_id}'",
            },
        )

    evidence = await _evidence_index(db, include_inactive)
    tcs = sorted(_tcs_by_uc().get(uc_id, []), key=lambda tc: tc.tc_id)

    groups: dict[str, dict[str, Any]] = {}
    for tc in tcs:
        g = groups.setdefault(tc.ucs_id, {
            "ucs_id": tc.ucs_id,
            "ucs_name": tc.scenario,
            "test_case_count": 0,
            "evidenced_count": 0,
            "test_case_ids": [],
        })
        g["test_case_count"] += 1
        g["test_case_ids"].append(tc.tc_id)
        if evidence.get(tc.tc_id):
            g["evidenced_count"] += 1

    logger.info("uctc use_case uc_id=%s ucs_groups=%d test_cases=%d",
                uc_id, len(groups), len(tcs))
    return {
        **_envelope(),
        "use_case": _uc_entry(uc, tcs, evidence),
        "ucs_groups": sorted(groups.values(), key=lambda g: g["ucs_id"]),
        "test_cases": [_tc_summary(tc, evidence.get(tc.tc_id, [])) for tc in tcs],
    }


@router.get("/test-cases")
async def list_test_cases(
    uc_id: Optional[str] = Query(None),
    ucs_id: Optional[str] = Query(None),
    validation_class: Optional[str] = Query(None, description="Comma-list, e.g. DET,HNT"),
    tier: Optional[str] = Query(None, description="Comma-list, e.g. MOAT,LEAD"),
    priority: Optional[str] = Query(None, description="Comma-list, e.g. P1,P2"),
    sheet: Optional[str] = Query(None, description="SecOps | Cloud"),
    pov_scenario_id: Optional[str] = Query(None),
    evidenced: Optional[bool] = Query(None),
    scoreable: Optional[bool] = Query(None),
    plane: Optional[str] = Query(None, description="Detection plane — implies evidenced=true"),
    include_inactive: bool = Query(False),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """The main table. Filters compose with logical AND.

    No pagination: 266 rows is one payload and the UI filters client-side, so
    the server params exist for deep links and other callers rather than for the
    interactive path. Unknown filter values quietly return an empty list rather
    than 400 — the same defensiveness against UI taxonomy drift that
    ``/api/ttps`` applies.
    """
    evidence = await _evidence_index(db, include_inactive)
    classes = _csv_set(validation_class)
    tiers = _csv_set(tier)
    priorities = _csv_set(priority)
    sheets = _csv_set(sheet)

    rows: list[dict[str, Any]] = []
    for tc in registry.all_test_cases():
        if uc_id and tc.uc_id != uc_id:
            continue
        if ucs_id and tc.ucs_id != ucs_id:
            continue
        if classes is not None and (tc.validation_class or "").upper() not in classes:
            continue
        if tiers is not None and (tc.differentiation_tier or "").upper() not in tiers:
            continue
        if priorities is not None and (tc.priority or "").upper() not in priorities:
            continue
        if sheets is not None and (tc.tc_sheet or "").upper() not in sheets:
            continue
        if pov_scenario_id and tc.pov_scenario_id != pov_scenario_id:
            continue
        if scoreable is not None and tc.is_scoreable is not scoreable:
            continue

        hits = evidence.get(tc.tc_id, [])
        if evidenced is not None and bool(hits) is not evidenced:
            continue
        # Plane is not an attribute of the index — it exists only on the
        # scenario side, so filtering by it necessarily implies evidenced.
        if plane:
            hits = [s for s in hits if (s.plane or "").upper() == plane.upper()]
            if not hits:
                continue
        rows.append(_tc_summary(tc, hits))

    rows.sort(key=lambda r: r["tc_id"])
    logger.info(
        "uctc test_cases count=%d filters=uc=%s class=%s tier=%s priority=%s "
        "plane=%s evidenced=%s scoreable=%s",
        len(rows), uc_id, validation_class, tier, priority, plane, evidenced, scoreable,
    )
    return {
        **_envelope(),
        "test_cases": rows,
        "total": len(rows),
        "index_total": len(registry.all_test_cases()),
    }


@router.get("/test-cases/{tc_id}")
async def get_test_case(
    tc_id: str,
    include_inactive: bool = Query(False),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Full detail — the endpoint that turns "mapped" into "proven".

    Carries the measurement contract, the licence gate, the payload this test
    case shares with its siblings, every scenario that evidences it, and the run
    verdicts those scenarios actually produced.
    """
    tc = registry.tc(tc_id)
    if tc is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "Test case not found",
                "code": "TC_NOT_FOUND",
                "detail": f"tc_id='{tc_id}'",
            },
        )

    evidence = await _evidence_index(db, include_inactive)
    scenarios = evidence.get(tc_id, [])
    base, addons = registry.entitlements_for(tc_id)

    evidenced_by = [
        {
            "scenario_id": s.scenario_id,
            "name": s.name,
            "plane": s.plane,
            "status": s.status,
            # A trailing tc_refs entry is a weaker claim than the primary
            # binding; the UI leans on this to separate evidence from decoration.
            "is_primary": s.tc_ref == tc_id,
            "uc_ref": s.uc_ref,
            "moat_tier": s.moat_tier,
            "pov_scenario_id": s.pov_scenario_id,
            "validation_methodology": s.validation_methodology,
            "methodology_family": s.methodology_family,
            "primary_kpi": s.primary_kpi,
            "threshold": s.threshold,
        }
        for s in sorted(scenarios, key=lambda x: x.scenario_id)
    ]

    payload = registry.payload(tc.pov_scenario_id)
    ucs_siblings = [t.tc_id for t in registry.ucs(tc.ucs_id) if t.tc_id != tc_id]
    uc = registry.uc(tc.uc_id)

    runs_block = await _runs_for(db, [s.scenario_id for s in scenarios])

    logger.info(
        "uctc test_case tc_id=%s evidenced_by=%d runs=%d scoreable=%s",
        tc_id, len(evidenced_by), runs_block["total"], tc.is_scoreable,
    )
    return {
        **_envelope(),
        **_tc_summary(tc, scenarios),
        "description": tc.description,
        "validation_methodology": tc.validation_methodology,
        "measurement_method": tc.measurement_method,
        "success_criteria": tc.success_criteria,
        "expected_signal": tc.expected_signal,
        "simulation_input": tc.simulation_input,
        "authoring_gap": tc.authoring_gap,
        # Resolved through the use case, never read off the TC row: the raw
        # columns include capacity SKUs and would contradict /api/pov/scope.
        "entitlements": {"base_platform": base, "addons": addons},
        "use_case": uc.to_dict() if uc else None,
        "ucs": {
            "ucs_id": tc.ucs_id,
            "ucs_name": tc.scenario,
            "sibling_tc_ids": sorted(ucs_siblings),
        },
        "payload": {
            "pov_scenario_id": payload.pov_scenario_id,
            "payload": payload.payload,
            "bound_tc_count": len(payload.bound_tc_ids),
            "reuse_flag": payload.reuse_flag,
            "needs_split": payload.needs_split,
            "mitre_techniques": payload.mitre_techniques,
        } if payload else None,
        "evidenced_by": evidenced_by,
        "runs": runs_block,
    }


async def _runs_for(db: AsyncSession, scenario_ids: list[str]) -> dict[str, Any]:
    """Run history rolled up across every scenario that evidences a test case.

    ``tc_verdict`` is the verifier's answer to "did this PASS its threshold",
    which is a different question from ``status`` ("did it execute"). A NULL
    verdict is reported as ``pending`` rather than dropped — an unscored run is
    a gap in the proof, not an absence of one.
    """
    verdicts = {"pass": 0, "fail": 0, "pending": 0, "not_applicable": 0}
    if not scenario_ids:
        return {"total": 0, "verdicts": verdicts, "latest": None}

    stmt = (
        select(Run)
        .where(Run.scenario_id.in_(scenario_ids))
        .order_by(Run.started_at.desc(), Run.id.desc())
    )
    runs = list((await db.execute(stmt)).scalars().all())
    for r in runs:
        key = r.tc_verdict or "pending"
        verdicts[key] = verdicts.get(key, 0) + 1

    latest = runs[0] if runs else None
    return {
        "total": len(runs),
        "verdicts": verdicts,
        "latest": {
            "run_id": latest.run_id,
            "scenario_id": latest.scenario_id,
            "status": latest.status,
            "tc_verdict": latest.tc_verdict,
            "started_at": latest.started_at.isoformat() if latest.started_at else None,
        } if latest else None,
    }


@router.get("/coverage")
async def get_coverage(
    include_inactive: bool = Query(False),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Every rollup in one payload — the UI wants them simultaneously, so a
    ``group_by`` param would just mean four round-trips for one screen."""
    evidence = await _evidence_index(db, include_inactive)
    tcs = registry.all_test_cases()
    by_uc = _tcs_by_uc()

    det = [tc for tc in tcs if tc.needs_detection_content]
    evidenced = [tc for tc in tcs if evidence.get(tc.tc_id)]
    det_evidenced = [tc for tc in det if evidence.get(tc.tc_id)]
    scenarios = {s.scenario_id: s for rows in evidence.values() for s in rows}

    use_cases: list[dict[str, Any]] = []
    for uc in registry.all_use_cases():
        e = _uc_entry(uc, by_uc.get(uc.uc_id, []), evidence)
        use_cases.append({
            "uc_id": uc.uc_id,
            "use_case": uc.use_case,
            "fy27_subdomain": uc.fy27_subdomain,
            "test_cases": e["counts"]["test_cases"],
            "detection_backable": e["counts"]["detection_backable"],
            "evidenced": e["counts"]["evidenced"],
            "evidenced_detection_backable": e["counts"]["evidenced_detection_backable"],
            "coverage_pct": e["coverage_pct"],
            "det_coverage_pct": e["det_coverage_pct"],
            "scenario_count": e["scenario_count"],
            "planes": e["planes"],
        })
    # Worst-covered first: row 1 is the biggest gap, which is the only ordering
    # a DC reads this table for.
    use_cases.sort(key=lambda u: (u["coverage_pct"], u["uc_id"]))

    by_plane: dict[str, dict[str, Any]] = {}
    for tc in tcs:
        for s in evidence.get(tc.tc_id, []):
            bucket = by_plane.setdefault(s.plane, {
                "plane": s.plane,
                "scenario_ids": set(),
                "test_case_ids": set(),
                "use_cases": set(),
            })
            bucket["scenario_ids"].add(s.scenario_id)
            bucket["test_case_ids"].add(tc.tc_id)
            bucket["use_cases"].add(tc.uc_id)
    plane_rows = sorted(
        (
            {
                "plane": b["plane"],
                "scenarios": len(b["scenario_ids"]),
                "test_cases_evidenced": len(b["test_case_ids"]),
                "test_case_ids": sorted(b["test_case_ids"]),
                "use_cases": sorted(b["use_cases"]),
            }
            for b in by_plane.values()
        ),
        key=lambda r: r["plane"],
    )

    def _dimension(key: str, attr: str) -> list[dict[str, Any]]:
        buckets: dict[str, list] = {}
        for tc in tcs:
            buckets.setdefault(getattr(tc, attr) or "", []).append(tc)
        return [
            {
                key: name,
                "total": len(rows),
                "evidenced": len([tc for tc in rows if evidence.get(tc.tc_id)]),
                "coverage_pct": _pct(
                    len([tc for tc in rows if evidence.get(tc.tc_id)]), len(rows)
                ),
            }
            for name, rows in sorted(buckets.items())
        ]

    logger.info(
        "uctc coverage use_cases=%d planes=%d evidenced=%d/%d",
        len(use_cases), len(plane_rows), len(evidenced), len(tcs),
    )
    return {
        **_envelope(),
        "totals": {
            "scenarios": len(scenarios),
            "evidenced": len(evidenced),
            "evidenced_pct": _pct(len(evidenced), len(tcs)),
            "detection_backable": len(det),
            "evidenced_detection_backable": len(det_evidenced),
            "detection_backable_pct": _pct(len(det_evidenced), len(det)),
            "unscoreable": len(registry.unscoreable()),
            "planes": sorted({s.plane for s in scenarios.values() if s.plane}),
        },
        "by_use_case": use_cases,
        "by_plane": plane_rows,
        "by_validation_class": _dimension("validation_class", "validation_class"),
        "by_tier": _dimension("tier", "differentiation_tier"),
        "by_priority": _dimension("priority", "priority"),
        "by_sheet": _dimension("sheet", "tc_sheet"),
    }


@router.get("/gaps")
async def list_gaps(
    validation_class: Optional[str] = Query("DET,HNT", description="Comma-list; DET,HNT is the actionable gap"),
    priority: Optional[str] = Query(None, description="Comma-list, e.g. P1"),
    uc_id: Optional[str] = Query(None),
    include_unscoreable: bool = Query(True, description="Keep TCs the index cannot score"),
    include_inactive: bool = Query(False),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Test cases no engine scenario evidences — the build backlog.

    Defaults to DET/HNT because those are the only classes a signal-generation
    engine can close; POS/PLT/AUT rows assert platform or posture state and are
    unreachable here by construction.
    """
    evidence = await _evidence_index(db, include_inactive)
    classes = _csv_set(validation_class)
    priorities = _csv_set(priority)

    gaps: list[dict[str, Any]] = []
    for tc in registry.all_test_cases():
        if evidence.get(tc.tc_id):
            continue
        if classes is not None and (tc.validation_class or "").upper() not in classes:
            continue
        if priorities is not None and (tc.priority or "").upper() not in priorities:
            continue
        if uc_id and tc.uc_id != uc_id:
            continue
        if not include_unscoreable and not tc.is_scoreable:
            continue
        gaps.append(_tc_summary(tc, []))

    # P1 first — a P3 qualitative gap and a P1 detection gap are not the same
    # backlog item, and the flat list would hide that.
    gaps.sort(key=lambda g: (g["priority"] or "P9", g["uc_id"], g["tc_id"]))

    by_uc: dict[str, dict[str, Any]] = {}
    for g in gaps:
        bucket = by_uc.setdefault(g["uc_id"], {
            "uc_id": g["uc_id"], "use_case": g["use_case"],
            "gap_count": 0, "p1_gap_count": 0,
        })
        bucket["gap_count"] += 1
        if g["priority"] == "P1":
            bucket["p1_gap_count"] += 1

    logger.info(
        "uctc gaps count=%d filters=class=%s priority=%s uc=%s unscoreable=%s",
        len(gaps), validation_class, priority, uc_id, include_unscoreable,
    )
    return {
        **_envelope(),
        "gaps": gaps,
        "total": len(gaps),
        "by_use_case": sorted(
            by_uc.values(), key=lambda b: (-b["gap_count"], b["uc_id"])
        ),
        "by_priority": _tally([g["priority"] for g in gaps]),
        "scope": {
            "validation_class": validation_class,
            "priority": priority,
            "uc_id": uc_id,
            "include_unscoreable": include_unscoreable,
            "include_inactive": include_inactive,
        },
    }


@router.get("/payloads")
async def list_payloads(
    in_use: bool = Query(False, description="Only payloads an engine scenario declares"),
    include_inactive: bool = Query(False),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """The POV-SC simulation payloads, annotated with engine usage.

    ``needs_split`` is the number that matters: a payload backing 8+ test cases
    fires them all at once, so none of them can be independently validated no
    matter how many scenarios declare it.
    """
    stmt = select(Scenario)
    if not include_inactive:
        stmt = stmt.where(Scenario.status == "active")
    by_payload: dict[str, list[str]] = {}
    for s in (await db.execute(stmt)).scalars().all():
        if s.pov_scenario_id:
            by_payload.setdefault(s.pov_scenario_id, []).append(s.scenario_id)

    entries: list[dict[str, Any]] = []
    for p in registry.all_payloads():
        used = sorted(by_payload.get(p.pov_scenario_id, []))
        if in_use and not used:
            continue
        entries.append({
            "pov_scenario_id": p.pov_scenario_id,
            "payload": p.payload,
            "bound_tc_ids": p.bound_tc_ids,
            "bound_tc_count": len(p.bound_tc_ids),
            "mitre_techniques": p.mitre_techniques,
            "reuse_flag": p.reuse_flag,
            "needs_split": p.needs_split,
            "scenario_ids": used,
            "scenario_count": len(used),
        })

    entries.sort(key=lambda e: e["pov_scenario_id"])
    logger.info("uctc payloads count=%d in_use=%s", len(entries), in_use)
    return {**_envelope(), "payloads": entries, "total": len(entries)}


@router.get("/by-scenario/{scenario_id}")
async def get_by_scenario(
    scenario_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """The forward view: what this one scenario claims to evidence.

    ``unresolved`` should always be empty while ``CORTEXSIM_STRICT_REFS``
    defaults true — it exists so a degraded deploy (registry empty, loader
    disarmed) is visible here rather than silently green.
    """
    result = await db.execute(
        select(Scenario).where(Scenario.scenario_id == scenario_id)
    )
    scenario: Optional[Scenario] = result.scalar_one_or_none()
    if scenario is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "Scenario not found",
                "code": "SCENARIO_NOT_FOUND",
                "detail": f"scenario_id='{scenario_id}'",
            },
        )

    refs = _scenario_tc_refs(scenario)
    resolved = [registry.tc(r) for r in refs]
    unresolved = [r for r, tc in zip(refs, resolved) if tc is None]
    hits = [tc for tc in resolved if tc is not None]
    base, addons = registry.entitlements_for_many(refs)

    index_tiers = {tc.tc_id: tc.differentiation_tier for tc in hits}
    logger.info(
        "uctc by_scenario scenario_id=%s refs=%d resolved=%d unresolved=%d",
        scenario_id, len(refs), len(hits), len(unresolved),
    )
    return {
        **_envelope(),
        "scenario_id": scenario_id,
        "uc_ref": scenario.uc_ref,
        "tc_ref": scenario.tc_ref,
        "tc_refs": refs,
        "pov_scenario_id": scenario.pov_scenario_id,
        "resolved": [_tc_summary(tc, [scenario]) for tc in hits],
        "unresolved": unresolved,
        "entitlements": {"base_platform": base, "addons": addons},
        "tier_delta": {
            "scenario_moat_tier": scenario.moat_tier,
            "index_tiers": index_tiers,
            "disagrees": any(
                scenario.moat_tier and t and scenario.moat_tier != t
                for t in index_tiers.values()
            ),
        },
    }
