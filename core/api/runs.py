"""
CortexSim API — /api/runs router.

Endpoints:
  POST /api/run                                 — launch a scenario run
  GET  /api/runs                                — list all runs
  GET  /api/runs/{run_id}                       — run detail + status
  GET  /api/runs/{run_id}/report                — POV report (markdown or JSON)
  GET  /api/runs/{run_id}/report/matrix         — detection_matrix.csv (Phase 8)
  GET  /api/runs/{run_id}/report/navigator      — ATT&CK Navigator layer JSON
  GET  /api/runs/{run_id}/report/bundle         — tar.gz of all three artifacts
  POST /api/runs/{run_id}/output                — agent streams output
  POST /api/runs/{run_id}/complete              — agent reports completion
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from engine import report_generator
from engine.orchestrator import orchestrator
from models import Result, Run, Scenario

logger = logging.getLogger("cortexsim.api.runs")

router = APIRouter(tags=["runs"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class LaunchRequest(BaseModel):
    scenario_id: str
    mode: str  # "pull" | "push"
    target_agent_id: Optional[str] = None
    identity: Optional[str] = None


class OutputRequest(BaseModel):
    output: str


class CompleteRequest(BaseModel):
    exit_code: int
    summary: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/run")
async def launch_run(
    body: LaunchRequest,
    db: AsyncSession = Depends(get_db),
):
    """Launch a scenario run in pull or push mode."""
    if body.mode not in ("pull", "push"):
        raise HTTPException(
            status_code=400,
            detail={"error": "Invalid mode", "code": "INVALID_MODE", "detail": "mode must be 'pull' or 'push'"},
        )

    logger.info(
        "launch_run scenario=%s mode=%s target=%s identity=%s",
        body.scenario_id,
        body.mode,
        body.target_agent_id,
        body.identity,
    )

    result = await orchestrator.launch(
        scenario_id=body.scenario_id,
        mode=body.mode,
        db=db,
        target_agent_id=body.target_agent_id,
        identity=body.identity,
    )

    if not result.success:
        raise HTTPException(
            status_code=422,
            detail={"error": result.error, "code": "LAUNCH_FAILED", "detail": ""},
        )

    response: dict = {
        "run_id": result.run_id,
        "mode": result.mode,
        "message": result.message,
    }
    if result.download_url:
        response["download_url"] = result.download_url

    return response


@router.get("/runs")
async def list_runs(db: AsyncSession = Depends(get_db)):
    """List all run records."""
    stmt = select(Run).order_by(Run.started_at.desc())
    result = await db.execute(stmt)
    runs = result.scalars().all()
    logger.info("list_runs count=%d", len(runs))
    return {"runs": [r.to_dict() for r in runs], "total": len(runs)}


@router.get("/runs/{run_id}")
async def get_run(run_id: str, db: AsyncSession = Depends(get_db)):
    """Return detail and current status for a single run."""
    result = await db.execute(select(Run).where(Run.run_id == run_id))
    run: Optional[Run] = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Run not found", "code": "RUN_NOT_FOUND", "detail": f"run_id='{run_id}'"},
        )
    logger.info("get_run run_id=%s status=%s", run_id, run.status)
    return run.to_dict()


@router.get("/runs/{run_id}/report")
async def get_report(
    run_id: str,
    format: str = Query("markdown", regex="^(markdown|json)$"),
    db: AsyncSession = Depends(get_db),
):
    """
    Generate a POV report for a completed run.
    Returns Cortex-branded markdown (for customer delivery) or structured JSON.
    """
    # Fetch run
    run_result = await db.execute(select(Run).where(Run.run_id == run_id))
    run: Optional[Run] = run_result.scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Run not found", "code": "RUN_NOT_FOUND", "detail": f"run_id='{run_id}'"},
        )

    # Fetch scenario
    scen_result = await db.execute(select(Scenario).where(Scenario.scenario_id == run.scenario_id))
    scenario: Optional[Scenario] = scen_result.scalar_one_or_none()

    # Fetch results
    results_result = await db.execute(
        select(Result).where(Result.run_id == run_id).order_by(Result.step_id, Result.id)
    )
    results = results_result.scalars().all()

    # Compute stats
    total = len(results)
    observed = sum(1 for r in results if r.observed)
    coverage_pct = round((observed / total * 100), 1) if total > 0 else 0.0

    by_type = {}
    for r in results:
        st = r.signal_type
        if st not in by_type:
            by_type[st] = {"total": 0, "observed": 0}
        by_type[st]["total"] += 1
        if r.observed:
            by_type[st]["observed"] += 1

    mttd_values = [r.mttd_seconds for r in results if r.mttd_seconds is not None]
    mttd_avg = round(sum(mttd_values) / len(mttd_values), 1) if mttd_values else None
    mttd_min = round(min(mttd_values), 1) if mttd_values else None
    mttd_max = round(max(mttd_values), 1) if mttd_values else None

    if format == "json":
        return {
            "run": run.to_dict(),
            "scenario": scenario.to_dict() if scenario else None,
            "results": [r.to_dict() for r in results],
            "coverage": {
                "observed": observed, "total": total, "pct": coverage_pct,
                "by_type": {k: {**v, "pct": round(v["observed"] / v["total"] * 100, 1) if v["total"] > 0 else 0} for k, v in by_type.items()},
            },
            "mttd": {"avg_seconds": mttd_avg, "min_seconds": mttd_min, "max_seconds": mttd_max, "count": len(mttd_values)} if mttd_values else None,
        }

    # --- Generate Markdown report ---
    s = scenario
    lines = []
    lines.append("# CortexSim — POV Detection Validation Report")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"**Scenario:** {s.name if s else run.scenario_id}  ")
    lines.append(f"**Scenario ID:** `{run.scenario_id}`  ")
    lines.append(f"**Detection Plane:** {s.plane if s else '—'}  ")
    lines.append(f"**Execution Mode:** {run.mode}  ")
    if run.identity_context:
        lines.append(f"**Identity Context:** `{run.identity_context}`  ")
    lines.append(f"**Run ID:** `{run.run_id}`  ")
    lines.append(f"**Started:** {run.started_at.strftime('%Y-%m-%d %H:%M UTC') if run.started_at else '—'}  ")
    lines.append(f"**Completed:** {run.completed_at.strftime('%Y-%m-%d %H:%M UTC') if run.completed_at else 'In Progress'}  ")
    lines.append(f"**Status:** {run.status}  ")
    lines.append("")

    if s:
        lines.append("## MITRE ATT&CK Mapping")
        lines.append("")
        lines.append(f"| Field | Value |")
        lines.append(f"|-------|-------|")
        lines.append(f"| Tactic | {s.mitre_tactic} — {s.mitre_tactic_name} |")
        lines.append(f"| Technique | {s.mitre_technique} — {s.mitre_technique_name} |")
        lines.append(f"| UC Reference | {s.uc_ref} — {s.uc_name} |")
        lines.append(f"| TC Reference | {s.tc_ref} — {s.tc_name} |")
        if s.threat_report:
            lines.append(f"| Threat Intel | {s.threat_report} |")
        lines.append("")

    # Coverage summary
    lines.append("## Detection Coverage Summary")
    lines.append("")
    lines.append(f"**Overall: {observed}/{total} detections confirmed ({coverage_pct}%)**")
    lines.append("")
    lines.append("| Detection Type | Observed | Total | Coverage |")
    lines.append("|----------------|----------|-------|----------|")
    for dt, stats in by_type.items():
        pct = round(stats["observed"] / stats["total"] * 100, 1) if stats["total"] > 0 else 0
        lines.append(f"| {dt} | {stats['observed']} | {stats['total']} | {pct}% |")
    lines.append("")

    # MTTD
    if mttd_values:
        lines.append("## Mean Time to Detect (MTTD)")
        lines.append("")

        def _fmt_mttd(secs):
            if secs is None: return "—"
            if secs < 60: return f"{secs}s"
            if secs < 3600: return f"{int(secs // 60)}m {int(secs % 60)}s"
            return f"{int(secs // 3600)}h {int((secs % 3600) // 60)}m"

        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Average MTTD | **{_fmt_mttd(mttd_avg)}** |")
        lines.append(f"| Fastest Detection | {_fmt_mttd(mttd_min)} |")
        lines.append(f"| Slowest Detection | {_fmt_mttd(mttd_max)} |")
        lines.append(f"| Detections Measured | {len(mttd_values)} |")
        lines.append("")

    # Per-step results
    lines.append("## Detailed Detection Results")
    lines.append("")

    current_step = None
    for r in results:
        if r.step_id != current_step:
            current_step = r.step_id
            lines.append(f"### {r.step_id}: {r.step_name or '—'}")
            lines.append("")

        status_icon = "✅" if r.observed else "❌"
        mttd_str = f" (MTTD: {_fmt_mttd(r.mttd_seconds)})" if r.mttd_seconds is not None else "" if mttd_values else ""
        lines.append(f"- {status_icon} **[{r.signal_type}]** {r.expected_detection}{mttd_str}")
        if r.notes:
            lines.append(f"  - *Notes: {r.notes}*")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*Generated by CortexSim v1.0 — Palo Alto Networks Cortex Detection Simulation Engine*")
    lines.append("")

    markdown = "\n".join(lines)

    logger.info("report generated run_id=%s format=%s lines=%d", run_id, format, len(lines))

    return PlainTextResponse(
        content=markdown,
        media_type="text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="cortexsim-report-{run.scenario_id}-{run_id[:8]}.md"',
        },
    )


# ---------------------------------------------------------------------------
# Phase 8 — POV report artifacts (detection matrix, Navigator layer, bundle)
# ---------------------------------------------------------------------------
#
# Shape modelled on the worked example under lab_cortex_analytics_pov/.
# All three endpoints are read-only and sourced from existing Run / Result /
# Scenario rows — no schema changes.


async def _load_report_inputs(run_id: str, db: AsyncSession):
    """Shared loader for the three Phase 8 endpoints."""
    run_result = await db.execute(select(Run).where(Run.run_id == run_id))
    run: Optional[Run] = run_result.scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Run not found", "code": "RUN_NOT_FOUND",
                    "detail": f"run_id='{run_id}'"},
        )
    scen_result = await db.execute(
        select(Scenario).where(Scenario.scenario_id == run.scenario_id),
    )
    scenario: Optional[Scenario] = scen_result.scalar_one_or_none()
    res_result = await db.execute(
        select(Result).where(Result.run_id == run_id)
                       .order_by(Result.step_id, Result.id),
    )
    results = res_result.scalars().all()
    return run, scenario, results


@router.get("/runs/{run_id}/report/matrix")
async def get_report_matrix(run_id: str, db: AsyncSession = Depends(get_db)):
    """Detection matrix CSV — one row per expected detection.

    Header matches the worked example at
    ``lab_cortex_analytics_pov/detection_matrix.csv``.
    """
    run, scenario, results = await _load_report_inputs(run_id, db)
    rows = report_generator.build_detection_matrix(
        run.to_dict(),
        scenario.to_dict() if scenario else None,
        [r.to_dict() for r in results],
    )
    csv_text = report_generator.render_detection_matrix_csv(rows)
    logger.info("report.matrix run_id=%s rows=%d", run_id, len(rows))
    return PlainTextResponse(
        content=csv_text,
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f'attachment; filename="cortexsim-detection-matrix-{run_id[:8]}.csv"'
            ),
        },
    )


@router.get("/runs/{run_id}/report/navigator")
async def get_report_navigator(run_id: str, db: AsyncSession = Depends(get_db)):
    """ATT&CK Navigator v4.5 layer JSON for this run.

    Importable directly into https://mitre-attack.github.io/attack-navigator/
    — DETECTED techniques colour-coded red, missed / pending grey.
    """
    run, scenario, results = await _load_report_inputs(run_id, db)
    layer = report_generator.render_attack_navigator_layer(
        run.to_dict(),
        scenario.to_dict() if scenario else None,
        [r.to_dict() for r in results],
    )
    logger.info("report.navigator run_id=%s techniques=%d",
                run_id, len(layer.get("techniques", [])))
    return Response(
        content=__import__("json").dumps(layer, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="cortexsim-navigator-{run_id[:8]}.json"'
            ),
        },
    )


@router.get("/runs/{run_id}/report/bundle")
async def get_report_bundle(run_id: str, db: AsyncSession = Depends(get_db)):
    """All three POV artifacts in one gzipped tarball.

    Layout (matches ``lab_cortex_analytics_pov/`` example):

        detection_matrix.csv
        attack_navigator_layer.json
        pov_narrative/exec_summary.md
    """
    run, scenario, results = await _load_report_inputs(run_id, db)
    blob = report_generator.build_bundle(
        run.to_dict(),
        scenario.to_dict() if scenario else None,
        [r.to_dict() for r in results],
    )
    logger.info("report.bundle run_id=%s bytes=%d", run_id, len(blob))
    return Response(
        content=blob,
        media_type="application/gzip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="cortexsim-pov-bundle-{run_id[:8]}.tar.gz"'
            ),
        },
    )


@router.post("/runs/{run_id}/output")
async def append_output(
    run_id: str,
    body: OutputRequest,
    db: AsyncSession = Depends(get_db),
):
    """Agent streams execution output back to SimCore."""
    result = await db.execute(select(Run).where(Run.run_id == run_id))
    run: Optional[Run] = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Run not found", "code": "RUN_NOT_FOUND", "detail": f"run_id='{run_id}'"},
        )

    existing = run.output or ""
    run.output = existing + body.output
    await db.commit()

    logger.info("output appended run_id=%s bytes=%d", run_id, len(body.output))
    return {"status": "ok", "run_id": run_id}


@router.post("/runs/{run_id}/complete")
async def complete_run(
    run_id: str,
    body: CompleteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Agent reports that execution is complete."""
    result = await db.execute(select(Run).where(Run.run_id == run_id))
    run: Optional[Run] = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Run not found", "code": "RUN_NOT_FOUND", "detail": f"run_id='{run_id}'"},
        )

    run.status = "complete" if body.exit_code == 0 else "failed"
    run.completed_at = datetime.utcnow()

    # Append summary to output
    summary_text = f"\n--- COMPLETION SUMMARY ---\nExit code: {body.exit_code}\n{body.summary}\n"
    run.output = (run.output or "") + summary_text

    await db.commit()

    logger.info(
        "run_complete run_id=%s exit_code=%d status=%s",
        run_id,
        body.exit_code,
        run.status,
    )
    return {"status": run.status, "run_id": run_id}
