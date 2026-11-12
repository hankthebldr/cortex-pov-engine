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

import asyncio
import logging
import re
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import LargeBinary, and_, case, cast, func, not_, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from database import get_db
from engine import efficacy_scorecard
from engine import host_evidence
from engine import report_generator
from engine.outcome import (
    HOST_WITNESS,
    EXCLUDED_FROM_DENOMINATOR,
    OUTCOMES,
    OUTCOME_DETECTED,
    OUTCOME_PREVENTED,
    is_hit,
    is_in_denominator,
    PROFILE_MODES,
    TERMINATIONS,
    TERMINATION_BEACON_LOST,
    TERMINATION_NOT_RUN,
    PLATFORM_UNSATISFIABLE,
    TOOL_ABSENT,
    UPSTREAM_FAILED,
    UPSTREAM_NOT_EXECUTED,
    UPSTREAM_SEVERED,
)
from engine.pairing import build_pair_delta
from engine.orchestrator import orchestrator
from events import event_bus
from models import Agent, Result, Run, RunOutputFrame, RunTaskCompletion, Scenario, StepOutcome
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


#: Markers a run carries when its tooling never reached the target. Emitted
#: identically by the beacon (`agent/beacon/artifact.go`) and by the capability
#: gate in `core/api/agents.py`, so one vocabulary covers both refusal points.
_INTEGRITY_MARKERS: tuple[tuple[str, str], ...] = (
    ("ARTIFACT STAGING FAILED",
     "A staged tool could not be fetched or verified on the target, so the "
     "steps that needed it did not run."),
    ("PAYLOAD_NOT_STAGED_ON_TARGET",
     "A step's preflight found its tool absent on the target and refused to "
     "run the TTP untooled."),
    ("ARTIFACT_SPEC_INVALID",
     "The beacon rejected the artifact plan it was handed, so nothing was "
     "staged."),
    ("RUN FAILED ON RESTART",
     "SimCore restarted before the agent collected this task; the run never "
     "reached the target."),
    ("!! TOOL_ABSENT",
     "A step's tool was not present on the target, so the step did not run "
     "and its absent detections say nothing about Cortex."),
    ("!! STEP NOT RUN: PLATFORM_UNSATISFIABLE",
     "A step cannot run on this endpoint's platform, so the beacon did not run "
     "it and its absent detections say nothing about Cortex."),
    ("!! RUNTIME_DEPENDENCY_MISSING",
     "A runtime dependency the step needs was missing on the target, so the "
     "step could not execute as authored."),
    ("command not found",
     "A step invoked a command the target does not have, so the TTP did not "
     "run as authored."),
    ("BEACON LOST",
     "The beacon stopped reporting mid-run, so later steps may not have run "
     "and the host evidence is incomplete."),
    ("BEACON ABANDONED TASK",
     "The beacon stayed alive but stopped reporting on this run (its "
     "completion was lost, typically across a SimCore restart), so SimCore "
     "closed it without the beacon's step record: later steps may not have "
     "run and the host evidence is incomplete."),
    ("!! TERMINATION_UNKNOWN",
     "The beacon reported a step ending in a way it could not classify, so "
     "whether the agent severed the step is unknown."),
)


def _execution_integrity(run: Any) -> dict[str, Any]:
    """Did this run actually execute as authored?

    Keys on `run.output` because that is where both refusal points already
    write, and both are stable strings. Returns ``ok: True`` for the ordinary
    case so an unremarkable run's report is byte-identical to before.
    """
    output = (getattr(run, "output", "") or "")
    problems = [
        f"**{marker}** — {explanation}"
        for marker, explanation in _INTEGRITY_MARKERS
        if marker in output
    ]
    return {"ok": not problems, "problems": problems}


_STEP_HEADER_RE = __import__("re").compile(r"=== STEP \d+/\d+ · ([^\s·]+) · ")
_PLATFORM_VARIANT_RE = __import__("re").compile(
    r"!! PLATFORM VARIANT: step=(\S+) ran its (\S+) variant")
#: ``engine.orchestrator.posix_family_markers`` (RG-UI-01).
_PLATFORM_MISMATCH_RE = __import__("re").compile(
    r"!! PLATFORM MISMATCH: step=(\S+) is authored for (\S+), not ([^\s;]+);")
_IDENTITY_MARKER = "!! IDENTITY NOT HONOURED"


def _identity_degraded_steps(output: str) -> list[str]:
    """Step ids whose output carries the beacon's identity-degradation marker.

    The beacon writes ``=== STEP n/N · <step-id> · ...`` then, for a step it
    could not run as the declared identity, ``!! IDENTITY NOT HONOURED: ...``
    (``agent/beacon/client.go::formatStepOutput``). Chunks are concatenated
    without a separator, so the header is searched anywhere on a line. A
    marker with no preceding header is reported as ``(unattributed)`` rather
    than dropped.
    """
    steps: list[str] = []
    current: Optional[str] = None
    for line in (output or "").splitlines():
        m = _STEP_HEADER_RE.search(line)
        if m:
            current = m.group(1)
        if _IDENTITY_MARKER in line:
            sid = current or "(unattributed)"
            if sid not in steps:
                steps.append(sid)
    return steps


def _report_caveats(run: Any, scenario: Optional[Any]) -> list[str]:
    """Scoped caveats for the markdown report (UI-09 / CONT-11).

    Unlike the execution-integrity section these do not void the run — each
    names the steps a reader must not over-read. Returns ``[]`` (no section at
    all) for an unremarkable run, so its report is byte-identical to before.
    """
    output = getattr(run, "output", "") or ""
    blocks: list[list[str]] = []

    degraded = _identity_degraded_steps(output)
    if degraded:
        blocks.append([
            "### Identity Not Honoured",
            "",
            "The beacon could not run these steps as their declared identity "
            "(`!! IDENTITY NOT HONOURED` in the run output — e.g. Windows has no "
            "credential-free unattended impersonation, so the step ran as the "
            "beacon's own account): "
            + ", ".join(f"`{s}`" for s in degraded) + ".",
            "",
            "Detections keyed on process lineage or the acting user cannot match "
            "by construction here — lineage-keyed misses on these steps are not a "
            "coverage measurement.",
        ])

    variants = []
    for sid, os_name in _PLATFORM_VARIANT_RE.findall(output):
        if (sid, os_name) not in variants:
            variants.append((sid, os_name))
    if variants:
        blocks.append([
            "### Platform Variants",
            "",
            "These steps ran their `platform_variants` command, not the primary "
            "command their expected detections were authored against, so a miss "
            "on them is unverified for this OS:",
            "",
            *[f"- `{sid}` — {os_name} variant" for sid, os_name in variants],
        ])

    mismatches = []
    for found in _PLATFORM_MISMATCH_RE.findall(output):
        if found not in mismatches:
            mismatches.append(found)
    if mismatches:
        blocks.append([
            "### Platform Mismatch",
            "",
            "These steps are labelled for another POSIX platform than the beacon "
            "that ran them. SimCore resolves POSIX versus Windows only, so it sent "
            "the authored command unchanged; a miss on them is not a coverage "
            "measurement:",
            "",
            *[f"- `{sid}` — authored for {labels}, ran on {os_name}"
              for sid, labels, os_name in mismatches],
        ])

    cleanup = (getattr(scenario, "cleanup", None) or {}) if scenario is not None else {}
    commands = [c for c in (cleanup.get("commands") or [])
                if isinstance(c, str) and c.strip()]
    if getattr(run, "mode", None) == "pull" and commands:
        blocks.append([
            "### Cleanup Not Run",
            "",
            f"pull mode does not execute cleanup.commands: {len(commands)} "
            f"command{'s' if len(commands) != 1 else ''} not run. Run them on the "
            "target by hand, or the scenario's artefacts stay on the endpoint:",
            "",
            "```",
            *commands,
            "```",
        ])

    if not blocks:
        return []
    lines = ["## ⚠ Caveats", ""]
    for block in blocks:
        lines.extend(block)
        lines.append("")
    return lines


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


_OUTCOME_ICON = {
    "prevented": "🛡",
    "detected": "✅",
    "missed": "❌",
    "pending": "⏳",
    "not_reached": "⏭",
    "not_executed": "⚠",
}


def _result_icon(r: Result) -> str:
    """Icon mirroring the stored outcome. An outcome-less row is ✅ when
    validated, else awaiting reconcile -- never ❌ (residual R4)."""
    if r.outcome is None:
        return "✅" if r.observed else "⏳ (awaiting reconcile)"
    if r.outcome not in OUTCOMES:
        raise ValueError(f"unrecognised result outcome {r.outcome!r}")
    icon = _OUTCOME_ICON[r.outcome]
    if r.outcome == "pending" and r.outcome_reason:
        icon += f" ({r.outcome_reason})"
    return icon


def _coverage_stats(results: list[Result]) -> dict:
    """The one coverage computation: the headline and the by_type table share it."""
    total = len(results)
    in_den = [r for r in results if is_in_denominator(r.outcome, r.outcome_reason)]
    denominator = len(in_den)
    # Hits through the one predicate (R1): pending is never a hit.
    observed = sum(1 for r in results if is_hit(r.outcome, r.observed))
    observed_in_den = sum(1 for r in in_den if is_hit(r.outcome, r.observed))
    prevented = sum(1 for r in results if r.outcome == "prevented")
    pct = round(observed_in_den / denominator * 100, 1) if denominator > 0 else 0.0
    by_type: dict = {}
    for r in in_den:
        t = by_type.setdefault(r.signal_type, {"total": 0, "observed": 0, "prevented": 0})
        t["total"] += 1
        if is_hit(r.outcome, r.observed):
            t["observed"] += 1
        if r.outcome == "prevented":
            t["prevented"] += 1
    return {
        "total": total, "denominator": denominator, "observed": observed,
        "observed_in_denominator": observed_in_den,
        "prevented": prevented, "excluded": total - denominator, "pct": pct,
        "by_type": by_type,
    }


def _fmt_secs(secs) -> str:
    return f"{secs}s" if secs is not None else ""


def _chain_integrity_section(run: Run, scenario: Optional[Scenario], results: list[Result]) -> list[str]:
    not_reached = [r for r in results if r.outcome == "not_reached"]
    not_executed = [r for r in results if r.outcome == "not_executed"]
    if not (run.severed_at_step or not_reached or not_executed):
        return []

    def _uniq(seq):
        return list(dict.fromkeys(seq))

    out = ["## Chain Integrity", ""]
    if run.severed_at_step:
        n = len(scenario.steps) if scenario is not None and scenario.steps else None
        of_n = f" of {n}" if n else ""
        out.append(f"- severed at {run.severed_at_step}{of_n}")
    if not_reached:
        out.append(f"- not_reached: [{', '.join(_uniq(r.step_id for r in not_reached))}]")
    if not_executed:
        ne = _uniq(f"{r.step_id} ({r.outcome_reason})" if r.outcome_reason else r.step_id
                   for r in not_executed)
        out.append(f"- not_executed: [{', '.join(ne)}]")
    out.append("")
    return out


async def _resolve_pair_partner(db: AsyncSession, run: Run) -> Optional[Run]:
    """Forward via ``paired_with_run_id``, else the newest run pointing at this one."""
    partner: Optional[Run] = None
    if run.paired_with_run_id:
        partner = (await db.execute(
            select(Run).where(Run.run_id == run.paired_with_run_id)
        )).scalar_one_or_none()
    if partner is None:
        partner = (await db.execute(
            select(Run).where(Run.paired_with_run_id == run.run_id)
            .order_by(Run.started_at.desc()).limit(1)
        )).scalar_one_or_none()
    return partner


async def _run_result_dicts(db: AsyncSession, rid: str) -> list[dict]:
    rows = (await db.execute(
        select(Result).where(Result.run_id == rid).order_by(Result.id)
    )).scalars().all()
    return [r.to_dict() for r in rows]


def _prevention_delta_section(run: Run, run_results: list[Result], partner: Optional[Run],
                              partner_results: list[dict]) -> list[str]:
    if partner is None:
        return []
    delta = build_pair_delta(run.to_dict(), [r.to_dict() for r in run_results],
                             partner.to_dict(), partner_results)

    def _cell(side, with_reason):
        if side is None:
            return "—"
        text = str(side.get("outcome") or "—")
        if with_reason and side.get("outcome_reason"):
            text += f" ({side['outcome_reason']})"
        if side.get("mttd_seconds") is not None:
            text += f" {_fmt_secs(side['mttd_seconds'])}"
        return text

    out = ["## Prevention Delta (report → block)", "",
           "| Step | Detection | Type | Module | Report-mode | Block-mode |",
           "|------|-----------|------|--------|-------------|------------|"]
    for step in delta["steps"]:
        for d in step["detections"]:
            out.append(
                f"| {step['step_id']} | {d['detection_id'] or '—'} | {d['signal_type'] or '—'} | "
                f"{d['prevention_module'] or '—'} | {_cell(d['report'], False)} | "
                f"{_cell(d['block'], True)} |"
            )
    out.append("")
    return out


_ATT = " — attested"


def _attestations_section(run: Run) -> list[str]:
    if not (run.agent_profile_mode != "unknown" or run.bas_mode_attested is not None
            or run.soakless_attested is not None or run.attested_by):
        return []

    def _flag(v):
        return "not attested" if v is None else ("enabled" if v else "disabled")

    at = run.attested_at.isoformat() + "Z" if run.attested_at else "unknown"  # naive UTC by convention
    out = [
        "## Attestations", "",
        f"- agent profile mode: {run.agent_profile_mode} — attested by "
        f"{run.attested_by or 'unknown'} at {at}, not verified",
        f"- BAS mode: {_flag(run.bas_mode_attested)}{_ATT if run.bas_mode_attested is not None else ''}",
        f"- soakless: {_flag(run.soakless_attested)}{_ATT if run.soakless_attested is not None else ''}",
    ]
    if run.block_mode_status and run.canary_run_id:
        out.append(f"- canary: {run.block_mode_status} (run {run.canary_run_id})")
    elif run.block_mode_status:
        out.append(f"- canary: {run.block_mode_status}")
    elif run.canary_run_id:
        out.append(f"- canary: run {run.canary_run_id} (status unknown)")
    else:
        out.append("- canary: not run")
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
    # Launch-time consent. Two families, checked by the ONE launch gate
    # (engine.orchestrator._check_launch_consent):
    #
    #   tool adapters   simulation_authorized (dual-use-lab-only)
    #                   c2_authorized         (c2-framework)
    #   cluster posture cluster_privilege_authorized (cluster-scoped/wildcard RBAC)
    #                   node_access_authorized       (privileged · hostPID ·
    #                                                 hostNetwork · hostPath ·
    #                                                 hostPort), push mode only,
    #                                                 and additionally requires
    #                                                 CORTEXSIM_ALLOW_PRIVILEGED_K8S
    #                                                 on the deployment
    #
    # The orchestrator refuses to create a Run without the matching consent.
    # A launch does NOT authorise the manifest itself — generating that is
    # POST /api/scenarios/{id}/bundle, which gates independently and also
    # demands authorized_by. See docs/reference/k8s-delivery.md.
    consent: Optional[dict[str, bool]] = None
    # Per-run authorization for the beacon to attempt a package-manager
    # install when a step's declared `requires_interpreters` is absent on the
    # target (docs/design/agent-runtime-dependencies.md). Mirrors
    # CORTEXSIM_XSIAM_ALLOW_WRITE's two-key posture: this flag alone does
    # nothing without CORTEXSIM_AGENT_ALLOW_RUNTIME_INSTALL also being set on
    # the deployment. Default False — nothing is ever installed on a target
    # implicitly.
    allow_runtime_install: bool = False
    # Prevention-efficacy launch context. Operator-declared: the engine never
    # touches the agent profile, it records what the operator says it is.
    agent_profile_mode: str = "unknown"
    bas_mode_attested: Optional[bool] = None
    soakless_attested: Optional[bool] = None
    attested_by: Optional[str] = None
    # Links this launch (the block-mode run) to its completed report-mode run.
    paired_with_run_id: Optional[str] = None
    canary_run_id: Optional[str] = None

    @field_validator("agent_profile_mode")
    @classmethod
    def _profile_mode_in_vocabulary(cls, v: str) -> str:
        if v not in PROFILE_MODES:
            raise ValueError(f"agent_profile_mode must be one of {sorted(PROFILE_MODES)}")
        return v


#: SEC-14 / OPS-06 — run output is bounded. One /output chunk STORES at most
#: 1 MiB of ordinary text, one run's stored output at most 32 MiB. Past the run
#: cap only lines that survive truncation (below) are still accepted, within a
#: further 1 MiB reserve, so a runaway step can neither fill the disk nor push
#: the markers the report's integrity check reads out of the record.
#:
#: An oversized chunk is CUT, never refused. The beacon sends ONE chunk per step
#: and does not cap it (agent/beacon/client.go::formatStepOutput), so a 422 here
#: dropped a large step's `=== STEP` header, its `!! IDENTITY NOT HONOURED` and
#: its step-start record together — and the step read in the report as an
#: ordinary miss. Bounding what a request may make the process hold in memory
#: is a whole-app request-body concern, not this route's.
RUN_OUTPUT_CHUNK_MAX_BYTES = 1 << 20
RUN_OUTPUT_CAP_BYTES = 32 << 20
RUN_OUTPUT_INTEGRITY_RESERVE_BYTES = 1 << 20
#: Line prefixes that survive the cap: `!! ` markers (IDENTITY NOT HONOURED,
#: TOOL_ABSENT, RUNTIME_DEPENDENCY_MISSING, PLATFORM VARIANT, ...),
#: `__CORTEXSIM` sentinels and `cortexsim-` tags.
_INTEGRITY_LINE_PREFIXES = ("!! ", "__CORTEXSIM", "cortexsim-")
#: The beacon's step frame: the header that ATTRIBUTES every marker after it to
#: a step (`_identity_degraded_steps`), and the exit footer. A marker whose
#: header was dropped reads as `(unattributed)`.
_STEP_FRAME_PREFIXES = ("=== STEP ", "=== ARTIFACT STAGING", "--- exit_code=")
_TRUNCATION_MARKER = "[OUTPUT TRUNCATED at "
_CHUNK_TRUNCATION_MARKER = "[OUTPUT CHUNK TRUNCATED: "
#: Room left in a cut chunk for its own truncation marker line.
_CHUNK_MARKER_RESERVE_BYTES = 256


#: One pass per line: an integrity / frame prefix at the start, or any
#: execution-integrity marker anywhere. Compiled once — a 32 MiB chunk is
#: about 1.6 M lines.
_SURVIVES_RE = re.compile(
    "^(?:" + "|".join(re.escape(p) for p in _INTEGRITY_LINE_PREFIXES + _STEP_FRAME_PREFIXES)
    + ")|" + "|".join(re.escape(m) for m, _ in _INTEGRITY_MARKERS))


def _survives_truncation(line: str) -> bool:
    """A line no truncation may drop: an integrity line, a step frame, or any
    line carrying a string the report's execution-integrity check reads."""
    return _SURVIVES_RE.search(line) is not None


def _bound_chunk(chunk: str) -> tuple[str, Optional[int]]:
    """Cut one /output chunk to :data:`RUN_OUTPUT_CHUNK_MAX_BYTES`.

    Returns ``(text, offered_bytes)``; ``offered_bytes`` is ``None`` when the
    chunk fit and ``text`` is the chunk unchanged. Otherwise every line that
    :func:`_survives_truncation` is kept, in order, ordinary lines are kept
    from the top until the budget is spent (a line that straddles it is cut on
    a character boundary), and one ``[OUTPUT CHUNK TRUNCATED: ...]`` line says
    how much was kept. Surviving lines are never dropped here, even past the
    bound; the run cap and its integrity reserve bound them.
    """
    raw = chunk.encode("utf-8")
    if len(raw) <= RUN_OUTPUT_CHUNK_MAX_BYTES:
        return chunk, None
    lines = chunk.splitlines(keepends=True)
    keep = [_survives_truncation(ln) for ln in lines]
    frame_bytes = sum(len(ln.encode("utf-8")) + 1 for ln, k in zip(lines, keep) if k)
    budget = RUN_OUTPUT_CHUNK_MAX_BYTES - frame_bytes - _CHUNK_MARKER_RESERVE_BYTES
    out: list[str] = []
    for ln, k in zip(lines, keep):
        if k:
            out.append(ln if ln.endswith("\n") else ln + "\n")
            continue
        if budget <= 0:
            continue
        b = ln.encode("utf-8")
        if len(b) <= budget:
            out.append(ln)
            budget -= len(b)
            continue
        part = b[:max(budget - 1, 0)].decode("utf-8", "ignore")
        if part:
            out.append(part if part.endswith("\n") else part + "\n")
        budget = 0
    kept = "".join(out)
    if kept and not kept.endswith("\n"):
        kept += "\n"
    kept_bytes = len(kept.encode("utf-8"))
    marker = (f"{_CHUNK_TRUNCATION_MARKER}{kept_bytes} of {len(raw)} bytes kept — "
              "ordinary lines past the per-chunk bound were dropped; every step "
              "header and integrity line was kept]\n")
    return kept + marker, len(raw)


class OutputRequest(BaseModel):
    # Deliberately no length limit: an oversized chunk is cut by the route
    # (`_bound_chunk`), keeping its step frame and integrity lines. A 422 here
    # lost them together with the step-start record.
    output: str
    # Optional step id (e.g. "step-02") so per-step output can be grouped in
    # the live SSE stream. Backward compatible — older agents omit it.
    step_id: Optional[str] = None
    # The beacon's key for a retried frame (RG-AGENT-06): "<task_id>:<n>",
    # the same on every attempt. A frame already stored for this run is not
    # appended again. Absent from an older beacon and from live streaming
    # chunks, which append every time exactly as before.
    frame_id: Optional[str] = Field(default=None, max_length=128)


_PROTECTED_TERMINATIONS = tuple(HOST_WITNESS | {TERMINATION_BEACON_LOST})
_STEP_REASONS = frozenset({UPSTREAM_SEVERED, UPSTREAM_NOT_EXECUTED, UPSTREAM_FAILED, TOOL_ABSENT,
                           PLATFORM_UNSATISFIABLE})


class StepOutcomeIn(BaseModel):
    """One step's host evidence, as the beacon reports it on /complete."""

    step_id: str
    exit_code: Optional[int] = None
    termination: str
    signal: Optional[int] = None
    reason: Optional[str] = None

    @field_validator("termination")
    @classmethod
    def _termination_in_vocabulary(cls, v: str) -> str:
        if v not in TERMINATIONS:
            raise ValueError(f"termination must be one of {sorted(TERMINATIONS)}")
        return v

    @model_validator(mode="after")
    def _reason_in_vocabulary(self) -> "StepOutcomeIn":
        # Out-of-vocabulary input is rejected here (422), never tolerated: a
        # `not_run` step with a reason nobody recognises would otherwise reach
        # host_outcome_for_step and surface as a 500 mid-/complete.
        if self.reason is not None and self.reason not in _STEP_REASONS:
            raise ValueError(f"reason must be one of {sorted(_STEP_REASONS)}")
        if self.termination == TERMINATION_NOT_RUN and self.reason is None:
            raise ValueError("a not_run step must carry a reason")
        return self


class CompleteRequest(BaseModel):
    exit_code: int
    summary: str
    # Per-step host evidence. Absent from an old beacon, which must behave
    # exactly as before: no step_outcomes rows, status rule unchanged.
    steps: list[StepOutcomeIn] = []
    # Which task this completion is for (RG-AGENT-03). The beacon retries
    # /complete when the reply is lost; with an identity a repeat is counted
    # once. Absent from an old beacon, which is counted per POST as before.
    task_id: Optional[str] = Field(None, max_length=200)
    agent_id: Optional[str] = Field(None, max_length=200)

    def completion_key(self) -> Optional[str]:
        """The idempotency key for this completion, or None when the beacon
        sent no identity. ``task_id`` wins; a fan-out carries one task per
        distinct agent, so ``agent_id`` identifies the task just as well."""
        task = (self.task_id or "").strip()
        if task:
            return f"task:{task}"
        agent = (self.agent_id or "").strip()
        return f"agent:{agent}" if agent else None


# Terminal Run states — reaching any of these stops the agent and makes
# /abort an idempotent no-op. ``staged`` is the terminal state for a push-mode
# run (GAP-API-004): the bundle was generated and SimCore's role is done.
_TERMINAL_STATES = {"complete", "failed", "aborted", "staged"}


def _is_terminal(run: Run) -> bool:
    """Is this run finished — i.e. must the beacon stop and is /abort a no-op?

    ``status`` alone is not enough. ``complete_run`` makes failure sticky the
    moment ANY endpoint of a multi-endpoint fan-out reports non-zero, so a run
    can read ``failed`` while it still waits on other endpoints (``open_tasks``
    > 0, ``completed_at`` unset). Treating that as terminal made the control
    channel stop every surviving endpoint within one 2 s poll — the beacon then
    reported "aborted by operator" for an abort nobody issued, and that
    endpoint's detections never happened. Every path that genuinely finishes a
    run stamps ``completed_at``; a ``failed`` run without it is still in flight.
    """
    if run.status not in _TERMINAL_STATES:
        return False
    if run.status == "failed" and run.completed_at is None and (run.open_tasks or 0) > 0:
        return False
    return True


def _in_flight_clause():
    """SQL twin of ``not _is_terminal(run)`` — for a guarded atomic UPDATE."""
    return or_(
        not_(Run.status.in_(tuple(_TERMINAL_STATES))),
        and_(Run.status == "failed", Run.completed_at.is_(None),
             func.coalesce(Run.open_tasks, 0) > 0),
    )


def _run_not_found(run_id: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"error": "Run not found", "code": "RUN_NOT_FOUND", "detail": f"run_id='{run_id}'"},
    )


async def _reload_run(db: AsyncSession, run_id: str) -> Optional[Run]:
    """Re-read a Run after an atomic UPDATE, refreshing any identity-mapped copy."""
    return (await db.execute(
        select(Run).where(Run.run_id == run_id).execution_options(populate_existing=True)
    )).scalar_one_or_none()


#: Refusal codes that mean "the request was fine, a precondition is not met".
#: These get 409 (same posture as PAYLOAD_NOT_STAGED) rather than 422, which
#: says the body was malformed and sends the operator looking at their JSON.
_LAUNCH_PRECONDITION_CODES = frozenset({
    "CONSENT_REQUIRED",
    "ADAPTER_MISSING_CLEANUP",
    # A composed multi-channel run named a per-step `target` agent that is not
    # enrolled. The request body is well-formed; the precondition (the second
    # endpoint exists) is not met — 409, not a 422 that sends the operator
    # hunting a JSON typo. Refused all-or-nothing before any task is enqueued.
    "TARGET_AGENT_NOT_ENROLLED",
    # paired_with_run_id names a run that cannot be this launch's partner
    # (different scenario/target, same mode, not terminal, or unknown).
    "PAIR_INVALID",
    # canary_run_id names a run that cannot prove this launch's profile
    # (unknown, wrong scenario/target, or not terminal).
    "CANARY_INVALID",
    # The target agent's OS cannot run some step of this scenario (the pull
    # twin of BUNDLE_TARGET_UNSATISFIABLE), or names no OS we can target. Both
    # also carry http_status=409 from the orchestrator; listed so the mapping
    # stays right for any caller that drops it.
    "PULL_TARGET_UNSATISFIABLE",
    "PULL_TARGET_OS_UNRECOGNISED",
})


async def _safe_publish(run_id: Optional[str], event: dict) -> None:
    """Publish to the event bus without ever letting a bus error propagate
    into the mutation path that triggered it."""
    try:
        await event_bus.publish(run_id, event)
    except Exception:  # pragma: no cover - defensive
        logger.exception("event_bus publish failed run_id=%s", run_id)


async def _draft_launch_gate(scenario_id: str, db: AsyncSession) -> None:
    """D5 launch gate (docs/superpowers/specs/2026-09-04-composer-workflow-design.md
    §6.3). A ``status='draft'`` Composer row saves and edits freely, but it is
    NOT launchable until it is (a) a valid chain — every step has a command and
    at least one expected detection — and (b) bound to a real FY27 UC/TC index
    test case. An UNBOUND draft that launched would seed Result rows whose
    absent detections read as a manufactured false negative on the customer's
    stack: this is an honesty gate (Gate A5), so it refuses regardless of
    ``CORTEXSIM_STRICT_REFS``.

    NON-draft scenarios (``status != 'draft'``) bypass this gate entirely — the
    corpus launch path is untouched. A missing scenario_id is left for
    ``orchestrator.launch`` to report (it owns the not-found path); this gate
    only ever fetches to decide whether the draft predicates apply.

    Raises ``HTTPException`` 409 with a structured ``{error, code, detail}``:
      * ``DRAFT_CHAIN_INVALID`` — a step has no command or no expected detection
        (detail names the offending step ids).
      * ``DRAFT_NOT_TC_BOUND``  — the draft's ``tc_ref``/``tc_refs`` do not
        resolve to a real index test case (the ``UNBOUND`` sentinel, an FK
        error, or an absent snapshot — binding cannot be *proven* without the
        index, so the honest verdict is "not bound", never a permissive pass).

    Predicates are the ONE implementation shared with the read-only mirror
    ``GET /api/scenarios/drafts/{id}/launchable`` (``api.drafts._chain_valid`` /
    ``_tc_bound``); a second, weaker predicate path is how a launch gate and its
    own console preview silently disagree.
    """
    # In the real request path ``db`` is always a live session (Depends(get_db)).
    # A ``None`` here means a unit test drove ``_launch_run_impl`` with a mocked
    # orchestrator and no DB; there is nothing to fetch, so the draft predicates
    # do not apply and the mocked launch owns the outcome.
    if db is None:
        return

    # Local import: keep one predicate implementation (the drafts router) and
    # avoid any import-order coupling at module load. Matches the repo's
    # local-import house style for cross-router reuse.
    from api.drafts import _chain_valid, _tc_bound  # noqa: PLC0415

    row = (
        await db.execute(
            select(Scenario).where(Scenario.scenario_id == scenario_id)
        )
    ).scalar_one_or_none()

    # Not a draft (or not found): the existing launch path handles it. Active
    # and deprecated corpus rows never see this gate.
    if row is None or row.status != "draft":
        return

    chain_ok, chain_reasons = _chain_valid(row)
    if not chain_ok:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "draft chain is not launchable",
                "code": "DRAFT_CHAIN_INVALID",
                "detail": "; ".join(chain_reasons),
            },
        )

    tc_ok, _tc_reasons = _tc_bound(row)
    if not tc_ok:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "draft is not launchable",
                "code": "DRAFT_NOT_TC_BOUND",
                "detail": "bind a real test case from the UC/TC Index before launching",
            },
        )


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

    # D5 launch gate (§6.3): refuse an unbound / chain-invalid Composer draft
    # BEFORE the orchestrator seeds anything. NON-draft scenarios bypass this.
    await _draft_launch_gate(body.scenario_id, db)

    result = await orchestrator.launch(
        scenario_id=body.scenario_id,
        mode=body.mode,
        db=db,
        target_agent_id=body.target_agent_id,
        identity=body.identity,
        consent=body.consent,
        allow_runtime_install=body.allow_runtime_install,
        agent_profile_mode=body.agent_profile_mode,
        bas_mode_attested=body.bas_mode_attested,
        soakless_attested=body.soakless_attested,
        attested_by=body.attested_by,
        paired_with_run_id=body.paired_with_run_id,
        canary_run_id=body.canary_run_id,
    )

    if not result.success:
        # Two sources, in priority order.
        #
        # 1. A refusal that ALREADY knows its status says so. The payload-shelf
        #    family carries its own `http_status` and the shelf route has always
        #    honoured it; this route used to drop it and answer a flat 422,
        #    telling the operator their launch body was malformed when the body
        #    was fine and the fix was ./scripts/build-payloads.sh. Note the
        #    family is NOT uniformly 409 — PayloadDestRefused is 400 — which is
        #    why the status is carried rather than re-derived from a list of
        #    codes here that would have to be kept in step by hand.
        # 2. Otherwise, the code decides. A missing consent flag is not a
        #    malformed request — the body was perfectly valid, the operator
        #    simply has not authorised the action yet. 409 puts it in the same
        #    posture as every other precondition a launch can fail on, and the
        #    structured detail means a console offers the exact checkbox
        #    instead of regexing the sentence for a key name.
        status = result.http_status or (
            409 if result.error_code in _LAUNCH_PRECONDITION_CODES else 422
        )
        raise HTTPException(
            status_code=status,
            detail={
                "error": result.error,
                "code": result.error_code,
                "detail": result.error_detail or "",
            },
        )

    response: dict = {
        "run_id": result.run_id,
        "mode": result.mode,
        "message": result.message,
    }
    if result.download_url:
        response["download_url"] = result.download_url
    # Present only when there is something to say (an offline target, an
    # unknown agent OS, a step running a platform variant), so the response for
    # an unremarkable launch keeps its exact historical shape.
    if result.warnings:
        response["warnings"] = list(result.warnings)

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


class CanaryRequest(BaseModel):
    target_agent_id: str


@router.post("/canary")
async def launch_canary(
    body: CanaryRequest,
    db: AsyncSession = Depends(get_db),
):
    """Launch the block-mode canary against an agent. (POST /api/runs/canary)

    Defined before the ``/{run_id}`` routes so "canary" is never read as a run id.
    """
    from engine import canary  # noqa: PLC0415

    scenario_id = canary.canary_scenario_id("linux")
    if scenario_id is None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "Canary not available",
                "code": "CANARY_NOT_AVAILABLE",
                "detail": "CORTEXSIM_CANARY_SCENARIO_LINUX is not set; "
                          "block mode will be attested-only",
            },
        )
    result = await orchestrator.launch(
        scenario_id=scenario_id,
        mode="pull",
        db=db,
        target_agent_id=body.target_agent_id,
        agent_profile_mode="block",
    )
    if not result.success:
        status = result.http_status or (
            409 if result.error_code in _LAUNCH_PRECONDITION_CODES else 422
        )
        raise HTTPException(
            status_code=status,
            detail={
                "error": result.error,
                "code": result.error_code,
                "detail": result.error_detail or "",
            },
        )
    return {"run_id": result.run_id, "scenario_id": scenario_id}


# The SQL twin of ``engine.outcome.row_status`` for the list aggregate below:
# an engine outcome of detected / prevented is a hit; any other outcome
# (``pending`` included, even when the reconcile set ``observed=True``) is
# not; a row with NO outcome has never been judged and is a hit only when a
# DC validated it (``observed``). Kept beside ``is_hit`` on purpose — the two
# must say the same thing, and ``tests/api/test_runs_api.py`` pins the cases.
_RESULT_IS_HIT = case(
    (Result.outcome.in_((OUTCOME_DETECTED, OUTCOME_PREVENTED)), 1),
    (and_(Result.outcome.is_(None), Result.observed.is_(True)), 1),
    else_=0,
)


async def _detection_aggregates(
    db: AsyncSession, run_ids: Optional[list[str]] = None,
) -> dict[str, tuple[int, int]]:
    """``{run_id: (expected_detections, detected_count)}`` in ONE grouped query.

    The Runs page lists every run on every poll; a per-run Result query here
    would be N+1 on the hottest read in the console. ``run_ids`` narrows it to
    the page being returned.
    """
    stmt = (select(Result.run_id, func.count(Result.id), func.sum(_RESULT_IS_HIT))
            .group_by(Result.run_id))
    if run_ids is not None:
        stmt = stmt.where(Result.run_id.in_(run_ids))
    rows = await db.execute(stmt)
    return {rid: (int(total or 0), int(hits or 0)) for rid, total, hits in rows.all()}


#: Default page for GET /api/runs. The list used to be unbounded.
LIST_RUNS_DEFAULT_LIMIT = 500
LIST_RUNS_MAX_LIMIT = 5000
#: Characters of output a summary row carries (the tail the console shows).
LIST_RUNS_OUTPUT_TAIL_CHARS = 2048


@router.get("")
async def list_runs(
    fields: Optional[str] = Query(
        None, pattern="^summary$",
        description="`summary` drops `output` and adds `output_bytes` + `output_tail` "
                    "(the last 2 KB). Omit for the full historical row shape."),
    limit: int = Query(LIST_RUNS_DEFAULT_LIMIT, ge=1, le=LIST_RUNS_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List run records, newest first, ``limit`` / ``offset`` paged.

    Each row carries ``expected_detections`` (seeded Result rows) and
    ``detected_count`` (hits by the one numerator rule) so the Runs list can
    show ``detected / expected`` without opening the run. A run with no Result
    rows reports ``0 / 0`` — the console renders that as a dash, never as a
    measured zero.

    ``total`` is the number of runs that exist, not the length of this page.
    Without ``fields`` the row and response shape are exactly the historical
    ones (OPS-06: the console's 10 s poll used to pull every run's whole
    output on every tick; it switches to ``fields=summary``).
    """
    total = (await db.execute(select(func.count(Run.id)))).scalar_one()
    page = select(Run).order_by(Run.started_at.desc()).limit(limit).offset(offset)
    summary = fields == "summary"
    if summary:
        stmt = (
            page.options(defer(Run.output))
            .add_columns(
                func.length(cast(func.coalesce(Run.output, ""), LargeBinary)),
                func.substr(func.coalesce(Run.output, ""), -LIST_RUNS_OUTPUT_TAIL_CHARS),
            )
        )
        fetched = (await db.execute(stmt)).all()
        runs = [row[0] for row in fetched]
        extras = {row[0].run_id: (int(row[1] or 0), row[2] or "") for row in fetched}
    else:
        runs = (await db.execute(page)).scalars().all()
        extras = {}
    agg = await _detection_aggregates(db, [r.run_id for r in runs])
    logger.info("list_runs count=%d total=%d fields=%s", len(runs), total, fields)
    rows = []
    for r in runs:
        expected, detected = agg.get(r.run_id, (0, 0))
        row = {**r.to_dict(include_output=not summary),
               "expected_detections": expected, "detected_count": detected}
        if summary:
            row["output_bytes"], row["output_tail"] = extras[r.run_id]
        rows.append(row)
    if summary:
        return {"runs": rows, "total": total, "fields": "summary",
                "limit": limit, "offset": offset}
    return {"runs": rows, "total": total}


async def _step_outcomes_view(db: AsyncSession, run: Run) -> list[dict]:
    """The one place host evidence is projected for reads: StepOutcome rows in
    scenario order (rows for steps the scenario no longer lists go last, by id)."""
    rows = (await db.execute(
        select(StepOutcome).where(StepOutcome.run_id == run.run_id)
        .order_by(StepOutcome.id))).scalars().all()
    # Only the `steps` column: this runs on every GET (the harness polls every 3 s).
    steps = (await db.execute(
        select(Scenario.steps).where(Scenario.scenario_id == run.scenario_id))).scalar_one_or_none()
    order = [st.get("id") for st in (steps or []) if isinstance(st, dict)]
    rank = {sid: i for i, sid in enumerate(order)}
    rows = sorted(rows, key=lambda r: rank.get(r.step_id, len(rank)))
    return [{
        "step_id": r.step_id, "termination": r.termination, "signal": r.signal,
        "rc": r.exit_code, "reason": r.reason,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "recorded_at": r.recorded_at.isoformat() if r.recorded_at else None,
    } for r in rows]


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
    return {**run.to_dict(), "step_outcomes": await _step_outcomes_view(db, run)}


@router.get("/{run_id}/pair")
async def get_run_pair(run_id: str, db: AsyncSession = Depends(get_db)):
    """Return this run, its report/block partner, and the per-detection delta."""
    run = (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one_or_none()
    if run is None:
        raise _run_not_found(run_id)

    partner = await _resolve_pair_partner(db, run)
    if partner is None:
        raise HTTPException(
            status_code=409,
            detail={"error": "Run is not paired", "code": "RUN_NOT_PAIRED",
                    "detail": f"run_id='{run_id}' has no paired_with_run_id and no run points at it"},
        )

    a, b = run.to_dict(), partner.to_dict()
    return {
        "runs": [a, b],
        "delta": build_pair_delta(a, await _run_result_dicts(db, run.run_id), b,
                                  await _run_result_dicts(db, partner.run_id)),
    }


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
            integrity=_execution_integrity(run),
        )
        if format == "scorecard-html":
            return Response(
                content=efficacy_scorecard.render_html(scorecard),
                media_type="text/html",
            )
        return PlainTextResponse(efficacy_scorecard.render_markdown(scorecard))

    # Compute stats
    cov = _coverage_stats(results)
    total, observed, coverage_pct = cov["total"], cov["observed"], cov["pct"]
    by_type = cov["by_type"]

    # Same exclusion as scoring: a row whose step never ran contributes no MTTD.
    mttd_values = [r.mttd_seconds for r in results
                   if r.mttd_seconds is not None and r.outcome not in EXCLUDED_FROM_DENOMINATOR]
    mttd_avg = round(sum(mttd_values) / len(mttd_values), 1) if mttd_values else None
    mttd_min = round(min(mttd_values), 1) if mttd_values else None
    mttd_max = round(max(mttd_values), 1) if mttd_values else None

    tools_used = _build_tools_used_rows(scenario.external_tools if scenario else None)
    integrity = _execution_integrity(run)

    if format == "json":
        return {
            "run": run.to_dict(),
            "scenario": scenario.to_dict() if scenario else None,
            "results": [r.to_dict() for r in results],
            "execution_integrity": integrity,
            "coverage": {
                # observed: raw count of observed rows; total: raw row count;
                # denominator: rows that can be scored; observed_in_denominator: observed
                # rows among those; pct: observed_in_denominator / denominator;
                # prevented: outcome == "prevented"; excluded: total - denominator.
                "observed": observed, "total": total, "pct": coverage_pct,
                "observed_in_denominator": cov["observed_in_denominator"],
                "denominator": cov["denominator"], "prevented": cov["prevented"], "excluded": cov["excluded"],
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

    # Execution integrity FIRST — before MITRE, tools and coverage. A run whose
    # tooling never reached the target would otherwise render as an ordinary
    # miss: 0/14 detections, every row ❌, and a "Tools Used" table introduced as
    # the run's audit trail listing three tools that never arrived. That is the
    # manufactured false negative relocated out of the API and into the one
    # artifact a DC actually puts in front of a customer, where it reads as a gap
    # in THEIR coverage. The beacon already refuses correctly and says so in
    # run.output; the report simply has to stop throwing that away.
    if not integrity["ok"]:
        lines.append("## ⚠ Execution Integrity — READ THIS FIRST")
        lines.append("")
        lines.append(
            "**The detection results below are NOT evidence about this "
            "environment's coverage.** This run did not execute as authored:"
        )
        lines.append("")
        for problem in integrity["problems"]:
            lines.append(f"- {problem}")
        lines.append("")
        lines.append(
            "A step whose tool could not be staged did not run. Absent "
            "detections therefore say nothing about whether Cortex would have "
            "caught the behaviour — treat the coverage numbers below as void "
            "and re-run once staging is fixed."
        )
        lines.append("")

    # Scoped caveats next — still above the coverage numbers they qualify.
    lines.extend(_report_caveats(run, s))

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
    lines.append(
        f"**Overall: {cov['observed_in_denominator']}/{cov['denominator']} detections confirmed ({coverage_pct}%) — "
        f"{cov['prevented']} prevented · {cov['excluded']} excluded "
        f"(not reached / not executed / soak-pending)**"
    )
    lines.append("")
    if cov["denominator"] == 0:
        lines.append("> Denominator is 0 — nothing in this run can be scored (see Chain Integrity).")
        lines.append("")
    lines.append("| Detection Type | Observed | Prevented | Total | Coverage |")
    lines.append("|----------------|----------|-----------|-------|----------|")
    for dt, stats in by_type.items():
        pct = round(stats["observed"] / stats["total"] * 100, 1) if stats["total"] > 0 else 0
        lines.append(f"| {dt} | {stats['observed']} | {stats['prevented']} | {stats['total']} | {pct}% |")
    lines.append("")

    lines.extend(_chain_integrity_section(run, scenario, results))
    partner = await _resolve_pair_partner(db, run)
    if partner is not None:
        lines.extend(_prevention_delta_section(
            run, results, partner, await _run_result_dicts(db, partner.run_id)))

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

    lines.extend(_attestations_section(run))

    # Per-step results
    lines.append("## Detailed Detection Results")
    lines.append("")

    current_step = None
    for r in results:
        if r.step_id != current_step:
            current_step = r.step_id
            lines.append(f"### {r.step_id}: {r.step_name or '—'}")
            lines.append("")

        status_icon = _result_icon(r)
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
    """Agent streams execution output back to SimCore.

    The append is ONE SQL statement (``output = coalesce(output,'') || chunk``),
    never read-in-Python-then-write: the beacon's stdout and stderr writers POST
    concurrently, and a read-modify-write dropped every chunk but the last —
    including the refusal markers the POV report's integrity check reads.

    Bounded (SEC-14 / OPS-06): a chunk over :data:`RUN_OUTPUT_CHUNK_MAX_BYTES`
    is first cut by :func:`_bound_chunk` (accepted, never refused — its step
    frame and integrity lines survive). The append is then guarded IN THE SAME
    STATEMENT by the run's stored byte length, so concurrent writers cannot
    jointly exceed :data:`RUN_OUTPUT_CAP_BYTES`. A chunk that does not fit
    keeps only the lines that :func:`_survives_truncation`, and
    ``[OUTPUT TRUNCATED at N bytes]`` is appended exactly once (the guard is
    in SQL too). The step-start record below is host evidence, not output,
    and is written either way.
    """
    frame_id = (body.frame_id or "").strip()
    if frame_id:
        # Claimed FIRST, in the same transaction as the append: a conflict
        # means this frame was stored by an earlier attempt whose reply was
        # lost, so nothing below runs. A refusal further down (run not found)
        # rolls the claim back with everything else.
        claimed = await db.execute(
            sqlite_insert(RunOutputFrame)
            .values(run_id=run_id, frame_id=frame_id, recorded_at=datetime.utcnow())
            .on_conflict_do_nothing(index_elements=["run_id", "frame_id"])
        )
        if claimed.rowcount == 0:
            await db.rollback()
            logger.info("output duplicate frame skipped run_id=%s frame_id=%s step=%s",
                        run_id, frame_id, body.step_id)
            return {"status": "ok", "run_id": run_id, "duplicate": True}
    if len(body.output.encode("utf-8")) > RUN_OUTPUT_CHUNK_MAX_BYTES:
        # Off the event loop: cutting a tens-of-MiB chunk is CPU work every
        # other request (other beacons' /control polls included) would wait on.
        chunk, offered_bytes = await asyncio.to_thread(_bound_chunk, body.output)
    else:
        chunk, offered_bytes = body.output, None
    stored = func.length(cast(func.coalesce(Run.output, ""), LargeBinary))
    res = await db.execute(
        update(Run)
        .where(Run.run_id == run_id,
               stored + len(chunk.encode("utf-8")) <= RUN_OUTPUT_CAP_BYTES)
        .values(output=func.coalesce(Run.output, "") + chunk)
        .execution_options(synchronize_session=False)
    )
    appended = chunk
    truncated = False
    if res.rowcount == 0:
        exists = (await db.execute(
            select(Run.id).where(Run.run_id == run_id))).scalar_one_or_none()
        if exists is None:
            await db.rollback()
            raise _run_not_found(run_id)
        truncated = True
        appended = await _append_past_cap(db, run_id, chunk, stored)
    if body.step_id:
        # First output for a step marks when it started. One INSERT .. ON
        # CONFLICT DO NOTHING in the same transaction as the append: concurrent
        # stdout/stderr writers race here, and a read-then-insert would either
        # violate the unique key or overwrite the earliest started_at.
        await db.execute(
            sqlite_insert(StepOutcome)
            .values(run_id=run_id, step_id=body.step_id,
                    started_at=datetime.utcnow(), recorded_at=datetime.utcnow())
            .on_conflict_do_nothing(index_elements=["run_id", "step_id"])
        )
    await db.commit()

    if appended:
        await _safe_publish(
            run_id,
            {"type": "run.output", "run_id": run_id,
             "data": {"step_id": body.step_id, "chunk": appended}},
        )

    reply: dict[str, Any] = {"status": "ok", "run_id": run_id}
    if offered_bytes is not None:
        logger.warning(
            "output chunk over the per-chunk bound run_id=%s offered=%d kept=%d step=%s",
            run_id, offered_bytes, len(chunk.encode("utf-8")), body.step_id)
        reply.update(chunk_truncated=True, offered_bytes=offered_bytes,
                     accepted_bytes=len(appended.encode("utf-8")))
    if truncated:
        logger.warning(
            "output over cap run_id=%s offered=%d kept=%d step=%s",
            run_id, len(chunk), len(appended), body.step_id)
        reply.update(truncated=True, accepted_bytes=len(appended.encode("utf-8")))
    if offered_bytes is None and not truncated:
        logger.info("output appended run_id=%s bytes=%d step=%s",
                    run_id, len(chunk), body.step_id)
    return reply


async def _append_past_cap(db: AsyncSession, run_id: str, chunk: str, stored) -> str:
    """The over-cap path of :func:`append_output`. Returns what was appended.

    1. ``[OUTPUT TRUNCATED at N bytes]`` (N = the stored length when it was
       written), guarded by ``instr`` so concurrent writers add it once.
    2. The chunk's lines that :func:`_survives_truncation` — integrity lines
       and the step frame that attributes them — within the integrity reserve.

    Both are single guarded UPDATEs, like the fast path.
    """
    appended = ""
    n_stored = (await db.execute(
        select(stored).where(Run.run_id == run_id))).scalar_one()
    marker = f"\n{_TRUNCATION_MARKER}{int(n_stored or 0)} bytes]\n"
    res = await db.execute(
        update(Run)
        .where(Run.run_id == run_id,
               func.instr(func.coalesce(Run.output, ""), _TRUNCATION_MARKER) == 0)
        .values(output=func.coalesce(Run.output, "") + marker)
        .execution_options(synchronize_session=False)
    )
    if res.rowcount:
        appended += marker
    kept = "".join(
        ln if ln.endswith("\n") else ln + "\n"
        for ln in chunk.splitlines(keepends=True)
        if _survives_truncation(ln)
    )
    if kept:
        res = await db.execute(
            update(Run)
            .where(Run.run_id == run_id,
                   stored + len(kept.encode("utf-8"))
                   <= RUN_OUTPUT_CAP_BYTES + RUN_OUTPUT_INTEGRITY_RESERVE_BYTES)
            .values(output=func.coalesce(Run.output, "") + kept)
            .execution_options(synchronize_session=False)
        )
        if res.rowcount:
            appended += kept
        else:
            logger.warning(
                "integrity reserve exhausted run_id=%s — %d marker byte(s) dropped",
                run_id, len(kept))
    return appended


async def _severed_prevent_ok(db: AsyncSession, run_id: str, severed: Optional[str]) -> bool:
    """Was the non-zero exit the agent doing its job, not a failure?

    True only when the host witnessed a sever on ``severed``, the run was
    launched in block mode, and that step carries a ``prevent`` expectation:
    then the kill is the expected result and the run is not ``failed``.
    """
    if severed is None:
        return False
    mode = (await db.execute(
        select(Run.agent_profile_mode).where(Run.run_id == run_id)
    )).scalar_one_or_none()
    if mode != "block":
        return False
    hit = (await db.execute(
        select(Result.id).where(
            Result.run_id == run_id, Result.step_id == severed,
            Result.expected_outcome == "prevent",
        ).limit(1)
    )).first()
    return hit is not None


async def _apply_host_evidence(db: AsyncSession, run_id: str, steps: list,
                               agent_profile_mode: str) -> None:
    """Project reported step outcomes onto the run's Results from the stored StepOutcome rows (caller commits)."""
    if not steps:
        return
    # Project from the STORED rows, not the request: they already honour the
    # witness guard, so a later endpoint cannot erase an earlier host witness.
    stored = (await db.execute(
        select(StepOutcome).where(StepOutcome.run_id == run_id,
                                  StepOutcome.termination.is_not(None))
    )).scalars().all()
    rows = (await db.execute(
        select(Result).where(Result.run_id == run_id,
                             Result.step_id.in_([s.step_id for s in stored]))
    )).scalars().all()
    host_evidence.apply_host_outcomes(rows, stored, agent_profile_mode=agent_profile_mode)
    await db.flush()


async def _record_step_outcomes(db: AsyncSession, run_id: str, steps: list) -> None:
    """Upsert the beacon's per-step host evidence (caller commits).

    A duplicate ``/complete`` upserts the same rows (idempotent); ``started_at``,
    set by ``/output``, is deliberately not in the update set.
    """
    for st in steps:
        vals = dict(exit_code=st.exit_code, termination=st.termination,
                    signal=st.signal or None, reason=st.reason,
                    recorded_at=datetime.utcnow())
        stmt = sqlite_insert(StepOutcome).values(run_id=run_id, step_id=st.step_id, **vals)
        # Another endpoint may report the same step. A host witness (or a lost
        # beacon) already on the row is never downgraded by a non-witness
        # termination; a witness may replace anything.
        guard = None
        if st.termination not in HOST_WITNESS:
            col = StepOutcome.__table__.c.termination
            guard = or_(col.is_(None), col.notin_(_PROTECTED_TERMINATIONS))
        await db.execute(stmt.on_conflict_do_update(
            index_elements=["run_id", "step_id"], set_=vals, where=guard))


async def _record_late_abort_evidence(db: AsyncSession, run: Run, steps: list) -> int:
    """Keep ``steps[]`` from a ``/complete`` that arrived after an operator abort.

    RG-RUNS-01. The abort is a guarded UPDATE that wins the race with the
    beacon by design, so the beacon's own ``/complete`` — the one message that
    says which steps ran before the kill reached them — always lands on a run
    that is already terminal. Dropping it left every Result of the aborted run
    at ``outcome`` NULL ("pending, awaiting reconcile"), and the next alert on
    that host inside the window was credited, with an MTTD, to a step that
    never executed.

    Only host evidence is taken, and only for a run that is ``aborted`` AND was
    delivered: ``not_run`` steps become ``not_reached`` / ``not_executed`` on
    their Results, which ``apply_verdicts`` never credits. The run's status,
    ``completed_at``, output, verdict and SSE stream are not touched, and the
    caller still answers 409. A run aborted before delivery ran nothing and is
    already stamped ``ABORTED_BEFORE_DELIVERY``; every other terminal state has
    a closed record (its own ``/complete`` or a classified sweep wrote it).

    Returns how many step records of this request were recorded.
    """
    if not steps or run.status != "aborted" or run.mode != "pull" or run.delivered_at is None:
        return 0
    await _record_step_outcomes(db, run.run_id, steps)
    await _apply_host_evidence(db, run.run_id, steps, run.agent_profile_mode)
    await db.commit()
    logger.info(
        "run_complete after abort — host evidence kept run_id=%s steps=%s",
        run.run_id, ",".join(f"{s.step_id}:{s.termination}" for s in steps))
    return len(steps)


async def _refuse_terminal_complete(db: AsyncSession, run: Run,
                                    body: "CompleteRequest") -> HTTPException:
    """The 409 for a ``/complete`` that reached a finished run (caller raises it).

    409, not 200 (OPS-03). The beacon RETRIES ``/complete``, so this answer is
    what tells it to stop: the run was already finished (by an earlier
    ``/complete``, an operator abort, a staging refusal, or the beacon-lost /
    abandoned-task sweep). Its summary and exit code are never applied, and
    neither is ``steps[]`` beyond what ``host_evidence_recorded`` counts. A 200
    here read as "accepted", which a retrying client cannot tell apart from
    success.
    """
    run_id = run.run_id
    logger.info(
        "run_complete refused — run already terminal run_id=%s status=%s exit_code=%d",
        run_id, run.status, body.exit_code,
    )
    # Run is already terminal — drop it from the orchestrator's aborted set
    # so the in-memory set stays bounded even on the abort→late-complete path.
    orchestrator.clear_aborted(run_id)
    orchestrator.forget_control_poll(run_id)
    # The one thing a late /complete may still add: after an operator abort of
    # a delivered run, which steps the host says ran (RG-RUNS-01).
    kept = await _record_late_abort_evidence(db, run, body.steps)
    return HTTPException(
        status_code=409,
        detail={
            "error": "Run already terminal",
            "code": "RUN_ALREADY_TERMINAL",
            "detail": {"run_id": run_id, "status": run.status,
                       "already_terminal": True,
                       "host_evidence_recorded": kept},
        },
    )


@router.post("/{run_id}/complete")
async def complete_run(
    run_id: str,
    body: CompleteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Agent reports that execution is complete.

    Every mutation here is an atomic SQL expression inside ONE write
    transaction — no value is read into Python and written back. A
    multi-endpoint fan-out POSTs /complete once per endpoint, and two endpoints
    finishing together used to both read ``open_tasks=2`` and both write 1, so
    the run never terminalised, was never scored and never sent its terminal
    SSE frame. The same read-modify-write let a racing operator abort be
    overwritten by ``complete`` (or lose this summary).
    """
    # Multi-endpoint fan-out: a run may carry N beacon tasks (one per distinct
    # target agent), each POSTing /complete. Terminalise the run only when the
    # LAST endpoint reports — not the first — or a two-endpoint run would flip
    # to complete the instant one host finished while the other kept running.
    # `open_tasks` is NULL on a legacy single-task run ⇒ treated as 1, so the
    # single-endpoint path stays byte-identical to today.
    #
    # Every endpoint's summary is appended. Sticky failure — once any endpoint
    # fails, the run's verdict is failed.
    #
    # A finished run stays finished. The WHERE clause matches only a run still
    # in flight (`_in_flight_clause`, the guard abort_run uses), so a late or
    # duplicate callback cannot re-stamp `completed_at`, flip `complete` to
    # `failed` (or `staged` to `complete`), re-publish the terminal frame or
    # re-score the run. That covers an operator abort (the agent reports exit
    # 130 on abort) and a staging refusal, which stops the surviving endpoints
    # the same way. A sticky-failed fan-out still owed endpoints is in flight.
    #
    # A RETRIED /complete counts once (RG-AGENT-03). The beacon re-sends the
    # same body when the reply is lost, and the decrement below cannot tell a
    # repeat from the fan-out's next endpoint — so one endpoint's retry used to
    # terminalise the run, the control channel stopped the other endpoint
    # mid-step, and its evidence was refused while the run read `complete`.
    # When the body names its task, that identity is inserted FIRST, in this
    # same write transaction: a conflict means this completion was already
    # applied, and nothing below runs. A refusal further down rolls the insert
    # back with everything else.
    key = body.completion_key()
    if key is not None:
        claimed = await db.execute(
            sqlite_insert(RunTaskCompletion)
            .values(run_id=run_id, task_key=key,
                    agent_id=(body.agent_id or "").strip() or None,
                    exit_code=body.exit_code, completed_at=datetime.utcnow())
            .on_conflict_do_nothing(index_elements=["run_id", "task_key"])
        )
        if claimed.rowcount == 0:
            await db.rollback()
            run = await _reload_run(db, run_id)
            if run is None:
                raise _run_not_found(run_id)
            if not _is_terminal(run):
                logger.info(
                    "run_complete duplicate — already applied run_id=%s key=%s "
                    "remaining_endpoints=%s", run_id, key, run.open_tasks)
                return {"status": run.status, "run_id": run_id,
                        "pending_endpoints": run.open_tasks or 0, "duplicate": True}
            # Finished since: one rule for a terminal run, whoever asks.
            raise await _refuse_terminal_complete(db, run, body)

    summary_text = f"\n--- COMPLETION SUMMARY ---\nExit code: {body.exit_code}\n{body.summary}\n"
    values: dict = {
        "open_tasks": func.max(func.coalesce(Run.open_tasks, 1) - 1, 0),
        "output": func.coalesce(Run.output, "") + summary_text,
    }
    severed = host_evidence.severed_step(body.steps)
    if severed is not None:
        # Never clear a severance an earlier endpoint already recorded.
        values["severed_at_step"] = func.coalesce(Run.severed_at_step, severed)
    if body.exit_code != 0 and not await _severed_prevent_ok(db, run_id, severed):
        values["status"] = "failed"
    res = await db.execute(
        update(Run)
        .where(Run.run_id == run_id, _in_flight_clause())
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if res.rowcount == 0:
        await db.rollback()
        # No writer moves a run out of a terminal state, so this re-read
        # reports the verdict the run finished with.
        run = await _reload_run(db, run_id)
        if run is None:
            raise _run_not_found(run_id)
        raise await _refuse_terminal_complete(db, run, body)

    # Host evidence for each step, in the same write transaction.
    await _record_step_outcomes(db, run_id, body.steps)

    # Still inside the write transaction that the UPDATE opened, so this read
    # sees exactly the decrement this request made — and the status that goes
    # with it.
    run = await _reload_run(db, run_id)
    remaining, status = run.open_tasks, run.status
    profile_mode = run.agent_profile_mode
    # Project the host evidence onto the Results INSIDE this write transaction,
    # before any terminalising commit: a crash must not leave a terminal run
    # with unprojected Results that a duplicate /complete cannot repair.
    await _apply_host_evidence(db, run_id, body.steps, profile_mode)

    if remaining > 0:
        # Not the last endpoint — hold; do NOT terminalise, publish a terminal
        # frame, or score yet. The run stays 'running' (or 'failed' if an
        # endpoint has already failed) until the final callback.
        await db.commit()
        logger.info(
            "run_complete partial run_id=%s exit_code=%d remaining_endpoints=%d",
            run_id, body.exit_code, remaining,
        )
        # Reply with the status read inside the transaction, never a re-read
        # after commit: the last endpoint can terminalise the run in between,
        # and `complete` alongside `pending_endpoints: 1` contradicts itself.
        return {"status": status, "run_id": run_id, "pending_endpoints": remaining}

    # Last endpoint in — terminalise with the aggregate verdict, in the same
    # transaction as the decrement.
    await db.execute(
        update(Run)
        .where(Run.run_id == run_id)
        .values(
            status=case((Run.status == "failed", "failed"), else_="complete"),
            completed_at=datetime.utcnow(),
        )
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    run = await _reload_run(db, run_id)

    # Run reached a terminal state — drop it from the orchestrator's aborted
    # set (and the control-poll record) so the in-memory state stays bounded.
    orchestrator.clear_aborted(run_id)
    orchestrator.forget_control_poll(run_id)

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
    # One guarded UPDATE: only an in-flight run is aborted, and the marker is
    # appended in SQL. Read-then-write here let a racing /complete land
    # `complete` over `aborted` (or this marker over its summary).
    res = await db.execute(
        update(Run)
        .where(Run.run_id == run_id, _in_flight_clause())
        .values(
            status="aborted",
            completed_at=datetime.utcnow(),
            output=func.coalesce(Run.output, "") + "\n--- RUN ABORTED BY OPERATOR ---\n",
        )
        .execution_options(synchronize_session=False)
    )
    if res.rowcount == 0:
        await db.rollback()
        run = await _reload_run(db, run_id)
        if run is None:
            raise _run_not_found(run_id)
        logger.info("abort_run idempotent no-op run_id=%s status=%s", run_id, run.status)
        return {"status": run.status, "run_id": run_id, "was_terminal": True}
    await db.commit()
    aborted = await _reload_run(db, run_id)

    # OPS-01: a pull run aborted before any beacon collected its task ran
    # nothing on the target — its rows can never be credited an alert. A
    # delivered run is left alone HERE: which of its steps ran is the host's
    # to say, and the beacon says it on the /complete that follows this abort
    # (kept by `_record_late_abort_evidence`, RG-RUNS-01). Until that arrives —
    # and for good if the beacon is gone or predates steps[] — the rows stay
    # unjudged, and `reconcile_candidates` credits an alert only to a step the
    # host reported (RG-RUNS-01 residual).
    if aborted is not None and aborted.mode == "pull" and aborted.delivered_at is None:
        from engine.orchestrator import (  # noqa: PLC0415
            REASON_ABORTED_BEFORE_DELIVERY,
            in_process_steps,
            stamp_not_executed,
        )

        await stamp_not_executed(db, run_id, REASON_ABORTED_BEFORE_DELIVERY,
                                 exclude_steps=in_process_steps(aborted))

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

    if _is_terminal(run):
        abort = True
        orchestrator.forget_control_poll(run_id)
    else:
        # OPS-03: the one signal that a beacon is executing THIS run (the
        # agent's last_seen only says the agent is alive). The abandoned-run
        # sweep reads it; in memory on purpose, measured from boot after a
        # restart.
        orchestrator.note_control_poll(run_id)

    # A beacon polling here is alive: refresh its liveness so the heartbeat
    # sweep never mistakes a long-running silent step for a lost beacon. One
    # atomic UPDATE; the response is unchanged.
    if run.target:
        now = datetime.utcnow()
        await db.execute(
            update(Agent).where(Agent.agent_id == run.target)
            .values(last_seen=now, status="online")
            .execution_options(synchronize_session=False))
        await db.commit()

    return {"abort": abort, "run_id": run_id, "status": run.status}
