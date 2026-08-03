"""
CortexSim API — /api/runs router.

Endpoints:
  POST /api/runs                                — launch a scenario run
  POST /api/run                                 — launch alias (deprecated; backward compat)
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
from engine import efficacy_scorecard
from engine import report_generator
from engine.orchestrator import orchestrator
from events import event_bus
from models import Result, Run, Scenario
from tools.adapter_catalog import catalog as adapter_catalog

logger = logging.getLogger("cortexsim.api.runs")

# GAP-API-008 — the runs router now carries the conventional ``/runs`` prefix so
# every endpoint lives under ``/api/runs/...`` (consistent with every other
# router). The launch path is ``POST /api/runs``. A backward-compat alias
# ``POST /api/run`` (singular, the historical path) is preserved on a separate
# unprefixed router (``compat_router``) mounted under ``/api`` so existing
# clients/UI builds keep working during the transition.
router = APIRouter(prefix="/runs", tags=["runs"])
compat_router = APIRouter(tags=["runs"])


def _build_tools_used_rows(external_tools: Optional[list]) -> list[dict]:
    """Resolve a scenario's external_tools[] entries into report-row dicts.

    For each entry with an ``adapter_ref``, look up the adapter in the
    catalog and emit a fully-populated row (name, version, tier, category,
    safety, licence, upstream attribution). For legacy entries without an
    adapter_ref — or with a stale adapter_ref the catalog can't resolve —
    fall back to the bare ``name`` / ``type`` from the YAML so the report
    never drops a tool the scenario declared.

    Pure function; keeps the markdown generator readable + unit-testable.
    """
    if not external_tools:
        return []

    rows: list[dict] = []
    for entry in external_tools:
        if not isinstance(entry, dict):
            continue
        adapter_ref = entry.get("adapter_ref")
        name = entry.get("name") or "—"
        adapter = adapter_catalog.find(adapter_ref) if adapter_ref else None
        if adapter is not None:
            rows.append({
                "name":     adapter.name,
                "version":  adapter.version,
                "tier":     str(adapter.tier),
                "category": adapter.category,
                "safety":   adapter.safety_class,
                "license":  adapter.upstream.license,
                "upstream": adapter.upstream.attribution,
            })
        else:
            # Either no adapter_ref (legacy scenario shape) or a stale ref
            # the catalog rejected. Emit a row that surfaces the gap rather
            # than hiding it — auditors need to see what ran.
            rows.append({
                "name":     name,
                "version":  "—",
                "tier":     "—",
                "category": entry.get("type") or "—",
                "safety":   "unresolved" if adapter_ref else "legacy",
                "license":  "—",
                "upstream": "—",
            })
    return rows


def _verdict_section(run: Run) -> list[str]:
    """Markdown block for a run's test-case verdict. Empty when unscored.

    Reuses ``report_generator._VERDICT_LABEL`` rather than restating the four
    labels — the wording of "NOT SCOREABLE" in particular is load-bearing and
    must not fork between the two renderers.
    """
    if not run.tc_verdict:
        return []
    detail = run.tc_verdict_detail if isinstance(run.tc_verdict_detail, dict) else {}
    label = report_generator._VERDICT_LABEL.get(run.tc_verdict, run.tc_verdict)

    out = ["## Test-Case Verdict", "", f"**{label}**", ""]
    if detail.get("detail"):
        out.append(f"{detail['detail']}.")
        out.append("")

    primary = detail.get("primary")
    if isinstance(primary, dict) and primary.get("op"):
        out.append("| Primary KPI | Verdict | Measured | Threshold |")
        out.append("|-------------|---------|----------|-----------|")
        actual = primary.get("actual")
        out.append(
            f"| {primary.get('detail') or '—'} | {primary.get('verdict') or '—'} | "
            f"{'—' if actual is None else actual} | "
            f"{primary.get('op')} {primary.get('expected')} |"
        )
        out.append("")

    unscoreable = detail.get("unscoreable") or []
    if unscoreable:
        out.append(
            f"> {len(unscoreable)} detection(s) carry no measurable threshold and "
            f"are reported as evidence, never as a scored pass: "
            f"{', '.join(str(u) for u in unscoreable[:8])}"
            + (" …" if len(unscoreable) > 8 else "")
        )
        out.append("")
    return out


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class LaunchRequest(BaseModel):
    scenario_id: str
    mode: str  # "pull" | "push"
    target_agent_id: Optional[str] = None
    identity: Optional[str] = None
    # Launch-time consent for gated tool adapters. Keys: simulation_authorized
    # (dual-use-lab-only) and c2_authorized (c2-framework). The orchestrator
    # refuses to create a Run for a gated adapter without the matching consent.
    consent: Optional[dict[str, bool]] = None


class OutputRequest(BaseModel):
    output: str
    # Optional step id (e.g. "step-02") so per-step output can be grouped in
    # the live SSE stream. Backward compatible — older agents omit it.
    step_id: Optional[str] = None


class CompleteRequest(BaseModel):
    exit_code: int
    summary: str


# Terminal Run states — reaching any of these stops the agent and makes
# /abort an idempotent no-op. ``staged`` is the terminal state for a push-mode
# run (GAP-API-004): the bundle was generated and SimCore's role is done.
_TERMINAL_STATES = {"complete", "failed", "aborted", "staged"}


async def _safe_publish(run_id: Optional[str], event: dict) -> None:
    """Publish to the event bus without ever letting a bus error propagate
    into the mutation path that triggered it."""
    try:
        await event_bus.publish(run_id, event)
    except Exception:  # pragma: no cover - defensive
        logger.exception("event_bus publish failed run_id=%s", run_id)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


async def _launch_run_impl(
    body: LaunchRequest,
    db: AsyncSession,
) -> dict:
    """Shared launch implementation behind both ``POST /api/runs`` and the
    deprecated ``POST /api/run`` alias."""
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
        consent=body.consent,
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


@router.post("")
async def launch_run(
    body: LaunchRequest,
    db: AsyncSession = Depends(get_db),
):
    """Launch a scenario run in pull or push mode. (POST /api/runs)"""
    return await _launch_run_impl(body, db)


@compat_router.post("/run", deprecated=True)
async def launch_run_compat(
    body: LaunchRequest,
    db: AsyncSession = Depends(get_db),
):
    """Deprecated launch alias — use ``POST /api/runs`` instead.

    Kept for backward compatibility with older UI builds / clients that POST
    to the singular ``/api/run`` path (GAP-API-008)."""
    return await _launch_run_impl(body, db)


@router.get("")
async def list_runs(db: AsyncSession = Depends(get_db)):
    """List all run records."""
    stmt = select(Run).order_by(Run.started_at.desc())
    result = await db.execute(stmt)
    runs = result.scalars().all()
    logger.info("list_runs count=%d", len(runs))
    return {"runs": [r.to_dict() for r in runs], "total": len(runs)}


@router.get("/{run_id}")
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


@router.get("/{run_id}/report")
async def get_report(
    run_id: str,
    format: str = Query("markdown", pattern="^(markdown|json|scorecard|scorecard-html)$"),
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

    # Executive efficacy scorecard (Detection Proof Layer) — the CISO one-pager
    # companion to the per-detection report. Short-circuits before the
    # markdown/json assembly since it reads the same Result rows.
    if format in ("scorecard", "scorecard-html"):
        scorecard = efficacy_scorecard.build_efficacy_scorecard(
            [r.to_dict() for r in results],
            run_ids=[run_id],
            title=f"Cortex POV Efficacy Scorecard — {scenario.name if scenario else run.scenario_id}",
            # Coverage says how much of what we expected we saw; the verdict says
            # whether the test case actually met its bar. A CISO one-pager that
            # reports only the first invites "94% covered" to be read as "94%
            # passed", which the two numbers do not support.
            tc_verdicts=[run.tc_verdict],
        )
        if format == "scorecard-html":
            return Response(
                content=efficacy_scorecard.render_html(scorecard),
                media_type="text/html",
            )
        return PlainTextResponse(efficacy_scorecard.render_markdown(scorecard))

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

    tools_used = _build_tools_used_rows(scenario.external_tools if scenario else None)

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
            "tools_used": tools_used,
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

    # Tools used — per-run attribution + licence audit trail derived from
    # the scenario's external_tools[] block. Each entry that carries an
    # adapter_ref is resolved against the in-process adapter catalog so
    # the customer-facing report cites the exact version / licence /
    # upstream project. Legacy entries (no adapter_ref) are still listed
    # by name so the report never silently drops a tool the run used.
    if tools_used:
        lines.append("## Tools Used")
        lines.append("")
        lines.append(
            "Adapters referenced by this scenario, resolved against the "
            "Tool Adapter catalog at run time. Customers should treat this "
            "table as the licence + attribution audit trail for the run."
        )
        lines.append("")
        lines.append("| Tool | Version | Tier | Category | Safety | Licence | Upstream |")
        lines.append("|------|---------|------|----------|--------|---------|----------|")
        for row in tools_used:
            lines.append(
                f"| {row['name']} | {row['version']} | {row['tier']} | "
                f"{row['category']} | {row['safety']} | {row['license']} | "
                f"{row['upstream']} |"
            )
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

    # Test-case verdict — the answer coverage % cannot give. A coverage
    # percentage with no verdict is exactly the inflation the verifier exists
    # to prevent. Rendered only when a verdict exists: an unscored historical
    # run must not grow a fake section.
    lines.extend(_verdict_section(run))

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
        # Phase 1 — when the orchestrator enriched this row from the TTP
        # catalog, embed the deployable query so the DC can paste it into
        # the XSIAM console during the demo. Indented under the bullet so
        # markdown viewers render the code block inline.
        if r.detection_logic:
            ref_bits = []
            if r.ttp_ref:
                ref_bits.append(f"`{r.ttp_ref}`")
            if r.detection_id:
                ref_bits.append(f"`{r.detection_id}`")
            kind_label = (r.detection_kind or "detection").upper()
            ref_label = " · ".join(ref_bits) if ref_bits else kind_label
            sev_label = f" · severity: **{r.detection_severity}**" if r.detection_severity else ""
            lines.append(f"  - {kind_label} — {ref_label}{sev_label}")
            fence_lang = "xql" if (r.detection_kind or "").lower() in ("bioc", "xql", "correlation") else ""
            lines.append(f"    ```{fence_lang}")
            for logic_line in (r.detection_logic or "").splitlines():
                lines.append(f"    {logic_line}")
            lines.append("    ```")

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


@router.get("/{run_id}/report/matrix")
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


@router.get("/{run_id}/report/navigator")
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


@router.get("/{run_id}/report/bundle")
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


@router.post("/{run_id}/output")
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

    await _safe_publish(
        run_id,
        {"type": "run.output", "run_id": run_id,
         "data": {"step_id": body.step_id, "chunk": body.output}},
    )

    logger.info("output appended run_id=%s bytes=%d step=%s", run_id, len(body.output), body.step_id)
    return {"status": "ok", "run_id": run_id}


@router.post("/{run_id}/complete")
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

    # An operator abort wins over a late completion callback: if the run was
    # already aborted, keep it aborted (the agent reports exit 130 on abort).
    if run.status == "aborted":
        logger.info("run_complete ignored — run already aborted run_id=%s", run_id)
        # Run is already terminal — drop it from the orchestrator's aborted set
        # so the in-memory set stays bounded even on the abort→late-complete path.
        orchestrator.clear_aborted(run_id)
        return {"status": run.status, "run_id": run_id}

    run.status = "complete" if body.exit_code == 0 else "failed"
    run.completed_at = datetime.utcnow()

    # Append summary to output
    summary_text = f"\n--- COMPLETION SUMMARY ---\nExit code: {body.exit_code}\n{body.summary}\n"
    run.output = (run.output or "") + summary_text

    await db.commit()

    # Run reached a terminal state — drop it from the orchestrator's aborted
    # set so the in-memory set stays bounded.
    orchestrator.clear_aborted(run_id)

    await _safe_publish(
        run_id,
        {"type": "run.status", "run_id": run_id, "data": {"status": run.status, "step_id": None}},
    )

    # Seed the test-case verdict at completion. At t=0 nothing is observed, so
    # a threshold-carrying scenario lands on `pending` ("declared but not
    # measured") — which is the point: every finished run now carries an
    # explicit verdict, so /api/uctc stops conflating "never scored" with
    # "scored pending". Offline only; no tenant call on an agent callback.
    from connectors.service import score_run_safely  # noqa: PLC0415
    tc_verdict = await score_run_safely(db, run, source="complete")

    logger.info(
        "run_complete run_id=%s exit_code=%d status=%s tc_verdict=%s",
        run_id,
        body.exit_code,
        run.status,
        tc_verdict,
    )
    return {"status": run.status, "run_id": run_id, "tc_verdict": tc_verdict}


# ---------------------------------------------------------------------------
# Phase 2 — operator abort + agent control channel
# ---------------------------------------------------------------------------


@router.post("/{run_id}/abort")
async def abort_run(run_id: str, db: AsyncSession = Depends(get_db)):
    """Operator-initiated abort.

    Transitions a ``pending``/``running`` run to ``aborted`` and signals the
    in-flight agent (via the orchestrator's aborted set, polled at
    ``/control``). Idempotent: a run already in a terminal state returns 200
    with its existing status and ``was_terminal: true`` — never an error.
    """
    result = await db.execute(select(Run).where(Run.run_id == run_id))
    run: Optional[Run] = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Run not found", "code": "RUN_NOT_FOUND", "detail": f"run_id='{run_id}'"},
        )

    if run.status in _TERMINAL_STATES:
        logger.info("abort_run idempotent no-op run_id=%s status=%s", run_id, run.status)
        return {"status": run.status, "run_id": run_id, "was_terminal": True}

    run.status = "aborted"
    run.completed_at = datetime.utcnow()
    run.output = (run.output or "") + "\n--- RUN ABORTED BY OPERATOR ---\n"
    await db.commit()

    # Drop any queued task (in-memory + durable) + record the id so the agent's
    # /control poll stops it.
    await orchestrator.abort_persisted(run_id, db)

    await _safe_publish(
        run_id,
        {"type": "run.status", "run_id": run_id, "data": {"status": "aborted", "step_id": None}},
    )

    logger.info("abort_run run_id=%s -> aborted", run_id)
    return {"status": "aborted", "run_id": run_id, "was_terminal": False}


@router.post("/{run_id}/verify")
async def verify_run_endpoint(
    run_id: str,
    integration: Optional[str] = Query(None, description="xsiam_tenant integration name (defaults to the first registered)"),
    timeframe_seconds: Optional[int] = Query(None, ge=60, le=86400, description="XQL lookback per query"),
    force: bool = Query(False, description="Re-verify a run that already reached a terminal verdict"),
    db: AsyncSession = Depends(get_db),
):
    """Verify this run's ``verification_xql`` detections against a tenant, then
    score the run's test-case verdict.

    Not flag-gated — an explicit POST naming the action is its own consent,
    exactly like ``POST /api/runs/{id}/reconcile``. The response always names
    the tenant it queried and how many queries it issued, so nobody has to
    discover after the fact which customer environment was touched.

    Returns **200** with ``tc_verdict: "pending"`` and
    ``reason: "no_tenant_integration"`` when no credential is registered — the
    same contract the assertion surface uses. "No tenant wired" is an honest
    verdict, not a client error; only a *named* integration that does not exist
    is a 404.
    """
    from connectors.service import VerifyError, verify_run_now  # noqa: PLC0415

    result = await db.execute(select(Run).where(Run.run_id == run_id))
    run: Optional[Run] = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Run not found", "code": "RUN_NOT_FOUND", "detail": f"run_id='{run_id}'"},
        )

    try:
        outcome = await verify_run_now(
            db, run, integration=integration, timeframe_seconds=timeframe_seconds,
            force=force, source="verify",
        )
    except VerifyError as e:
        raise HTTPException(status_code=404, detail={
            "error": str(e), "code": e.code, "detail": e.detail})

    logger.info("verify_run run_id=%s verdict=%s tenant=%s queries=%d reason=%s",
                run_id, outcome.tc_verdict, outcome.tenant,
                outcome.queries_issued, outcome.reason)
    return outcome.to_dict()


@router.get("/{run_id}/control")
async def run_control(run_id: str, db: AsyncSession = Depends(get_db)):
    """Lightweight stop-signal poll for the in-flight agent.

    Returns ``abort=true`` when the run was aborted (orchestrator set) OR has
    reached any terminal status in the DB (covers a SimCore restart that lost
    the in-memory aborted set). A vanished run (DB reset) also returns
    ``abort=true`` so the agent halts rather than spins.
    """
    abort = orchestrator.is_aborted(run_id)

    result = await db.execute(select(Run).where(Run.run_id == run_id))
    run: Optional[Run] = result.scalar_one_or_none()
    if run is None:
        return {"abort": True, "run_id": run_id, "status": "unknown"}

    if run.status in _TERMINAL_STATES:
        abort = True

    return {"abort": abort, "run_id": run_id, "status": run.status}
