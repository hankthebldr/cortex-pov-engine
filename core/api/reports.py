"""
CortexSim API — /api/reports router. What was fired on this engine.

  GET /api/reports/executions   every run on this instance, rolled up and mapped
                                scenario -> technique -> test case -> plane ->
                                detection source -> outcome

Read-only, the local database and local files only: zero outbound calls.

The Runs list is one row per run and the three corpus dashboards count the
library. Neither answers "what has this engine executed since it was deployed,
and what did that touch?". This route does, from ``Run``, ``Result``,
``StepOutcome`` and the loaded scenarios.

Rules that bind this file:

* **SQL aggregates only.** No statement selects ``Run.output`` (megabytes per
  run) or a whole ``Result`` row. Every query names its columns.
* **Not reviewed is not missed.** A detection nothing has judged, on a run
  nobody reviewed, is counted ``not_reviewed``. ``missed`` exists only as an
  engine outcome (``engine.outcome.row_status``).
* **Never run is not 0 %.** A scenario with no run in scope is absent from the
  rows; ``totals.scenarios.corpus_not_run`` counts them.
* **An unrecognised value is named.** A run status, a result outcome or a step
  termination outside its vocabulary lands in an ``unrecognised`` map keyed by
  the value. It is never dropped and never folded into a known bucket.
* **An empty engine is said.** No run in scope answers 200 with
  ``empty: true``.

A run is *reviewed* when SimCore recorded a tenant read on it, the rule the
console uses (``runLifecycle.js::readBackOf``): the last score came from a
reconcile (``tc_verdict_detail.source`` starts ``reconcile:``) or a
verification issued a query (``verification.queries_issued > 0``).
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, Response
from sqlalchemy import Integer, and_, case, cast, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from connectors.base import coerce_utc
from database import get_db
from engine.dataset_requirements import DatasetRequirementsError, load as load_dataset_registry
from engine.door_resolution import DoorResolutionError, detection_requirement
from engine.ingestion_doors import DOORS
from engine.outcome import (
    DETECTED_NOT_PREVENTED,
    OUTCOME_DETECTED,
    OUTCOME_PENDING,
    TERMINATION_EXITED,
    TERMINATION_NOT_RUN,
    TERMINATIONS,
    row_status,
)
from engine.ttp_catalog import catalog
from engine.uctc_registry import registry as uctc_registry
from models import SCENARIO_RETIRED, Result, Run, Scenario, StepOutcome

logger = logging.getLogger("cortexsim.api.reports")

router = APIRouter(prefix="/reports", tags=["reports"])

#: ``Run.status`` vocabulary (models.py). Anything else is reported by name.
RUN_STATUSES: tuple[str, ...] = ("pending", "running", "complete", "failed", "aborted", "staged")
#: Composer drafts carry this id prefix (api/drafts.py); left out by default.
DRAFT_PREFIX = "SIM-DRAFT-"
#: Detection buckets of one reviewed run, in display order.
OUTCOME_KEYS: tuple[str, ...] = (
    "prevented", "detected", "detected_not_prevented", "missed", "pending",
    "not_reached", "not_executed",
)
NOT_REVIEWED = "not_reviewed"
CSV_COLUMNS: tuple[str, ...] = (
    "scenario_id", "name", "plane", "techniques", "uc_ref", "tc_refs", "detection_sources",
    "runs", "first_run_at", "last_run_at", "last_status", "last_run_id",
    "reviewed_runs", "last_reviewed_run_id", "last_reviewed_at", "expected", *OUTCOME_KEYS,
)
_IN_CHUNK = 400

# The SQL twin of runLifecycle.js::readBackOf. CAST keeps a non-numeric
# `queries_issued` from comparing greater than 0 (SQLite orders text above numbers).
_REVIEW_SOURCE = Run.tc_verdict_detail["source"].as_string()
_VERIFY_QUERIES = cast(Run.tc_verdict_detail[("verification", "queries_issued")].as_string(), Integer)
RUN_REVIEWED = case((or_(_REVIEW_SOURCE.like("reconcile:%"), _VERIFY_QUERIES > 0), 1), else_=0)


def _iso(value: Any) -> Optional[str]:
    """A stored naive-UTC timestamp -> ISO-8601 ending ``+00:00``."""
    dt = coerce_utc(value)
    return dt.replace(tzinfo=timezone.utc).isoformat() if dt else None


def _error(status: int, error: str, code: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": error, "code": code, "detail": detail})


def _chunks(items: list[str]) -> Iterable[list[str]]:
    for i in range(0, len(items), _IN_CHUNK):
        yield items[i:i + _IN_CHUNK]


def _scope(*, since: Optional[datetime], until: Optional[datetime], plane: Optional[str],
           status: Optional[str], agent: Optional[str], include_drafts: bool) -> list[Any]:
    """The WHERE clauses every query shares, on ``runs``."""
    conds: list[Any] = []
    if since is not None:
        conds.append(Run.started_at >= since)
    if until is not None:
        conds.append(Run.started_at <= until)
    if status:
        conds.append(Run.status == status)
    if agent:
        conds.append(Run.target == agent)
    if not include_drafts:
        conds.append(not_(Run.scenario_id.startswith(DRAFT_PREFIX, autoescape=True)))
    if plane:
        conds.append(Run.scenario_id.in_(select(Scenario.scenario_id).where(Scenario.plane == plane)))
    return conds


def _new_outcomes(*, with_not_reviewed: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"expected": 0, **{k: 0 for k in OUTCOME_KEYS}}
    if with_not_reviewed:
        out[NOT_REVIEWED] = 0
    out["unrecognised"] = {}
    return out


def _add_outcome(into: dict[str, Any], *, outcome: Optional[str], observed: Any,
                 detected_not_prevented: Any, run_reviewed: Any, n: int) -> None:
    """Count ``n`` Result rows into ``into`` by the one row rule.

    ``row_status`` decides; this adds two things it does not know: a row nothing
    judged on a run nobody reviewed is ``not_reviewed`` (not ``pending``), and a
    block-mode prevention that was only detected is its own bucket.
    """
    into["expected"] += n
    try:
        status = row_status(outcome, bool(observed))
    except ValueError:
        key = str(outcome)
        into["unrecognised"][key] = into["unrecognised"].get(key, 0) + n
        return
    if outcome is None and status == OUTCOME_PENDING and not run_reviewed:
        status = NOT_REVIEWED
    elif status == OUTCOME_DETECTED and detected_not_prevented:
        status = "detected_not_prevented"
    if status not in into:
        # A reviewed run cannot hold a not-reviewed row; if one appears, name it.
        into["unrecognised"][status] = into["unrecognised"].get(status, 0) + n
        return
    into[status] += n


def _merge_outcomes(into: dict[str, Any], other: dict[str, Any]) -> None:
    for key, val in other.items():
        if key == "unrecognised":
            for k, n in val.items():
                into["unrecognised"][k] = into["unrecognised"].get(k, 0) + n
        else:
            into[key] = into.get(key, 0) + val


def _scenario_techniques(primary: Optional[str], steps: Any) -> tuple[list[str], dict[Any, Optional[str]]]:
    """A scenario's techniques (primary first, then its steps', in step order)
    and its ``step_id -> technique`` map. A step that names no technique carries
    the scenario's primary, as ``api/mitre.py`` reads it."""
    techniques: list[str] = []
    step_map: dict[Any, Optional[str]] = {}
    if primary:
        techniques.append(primary)
    for step in steps if isinstance(steps, list) else []:
        if not isinstance(step, dict):
            continue
        tech = step.get("mitre_technique") or primary
        step_map.setdefault(step.get("id"), tech)
        if tech and tech not in techniques:
            techniques.append(tech)
    return techniques, step_map


def _detection_sources(scenario_id: str, steps: Any, registry: Any) -> list[dict[str, str]]:
    """The ingestion doors a scenario's expected detections need, in registry
    order: the same derivation ``GET /api/doors`` counts with."""
    needed: set[str] = set()
    for step in steps if isinstance(steps, list) else []:
        if not isinstance(step, dict):
            raise DoorResolutionError(f"scenario {scenario_id!r}: a step must be a mapping")
        for det in step.get("expected_detections") or []:
            req = detection_requirement(step, det, catalog=catalog, registry=registry)
            needed.update(d for element in req.all_of for d in element)
    return [{"id": d.id, "label": d.label} for d in DOORS if d.id in needed]


def _test_case_meta(tc_ref: str, scenario: Optional[dict[str, Any]]) -> tuple[Optional[str], Optional[str]]:
    """``(name, uc_ref)`` for a test case: the index row when the index is
    loaded, else the binding scenario's own primary pair."""
    row = uctc_registry.tc(tc_ref) if uctc_registry.loaded else None
    if row is not None:
        return row.title or None, row.ucs_id or row.uc_id or None
    if scenario and scenario.get("tc_ref") == tc_ref:
        return scenario.get("tc_name") or None, scenario.get("uc_ref") or None
    return None, None


@router.get("/executions")
async def executions_report(
    since: Optional[str] = Query(None, description="ISO-8601; runs started at or after it. Default: all time."),
    until: Optional[str] = Query(None, description="ISO-8601; runs started at or before it."),
    plane: Optional[str] = Query(None, description="Scenario plane, e.g. EDR."),
    status: Optional[str] = Query(None, description="Run status, e.g. complete."),
    agent: Optional[str] = Query(None, description="Agent id a run was sent to (Run.target)."),
    include_drafts: bool = Query(False, description="Include SIM-DRAFT-* scenarios."),
    format: str = Query("json", pattern="^(json|csv)$"),
    db: AsyncSession = Depends(get_db),
):
    """Roll up every execution on this engine. Read-only, no outbound calls.

    Filters narrow the runs counted: ``since`` / ``until`` on ``started_at``,
    ``plane`` on the scenario's plane, ``status``, ``agent`` and
    ``include_drafts`` (off: ``SIM-DRAFT-*`` scenarios are left out).

    JSON fields (timestamps are UTC, ISO-8601 with ``+00:00``):

    - ``generated_at``; ``window`` ``{since, until, first_run_at, last_run_at}``;
      ``filters`` (the filters as applied); ``filter_options``
      ``{planes, statuses, agents}`` for a filter row, read from the whole
      instance.
    - ``empty``: no run matches. ``engine_runs``: runs on the instance before
      any filter, so "nothing yet" and "nothing matches" are told apart.
    - ``totals.runs``: ``total``, ``by_status`` (every known status, zero
      included), ``unrecognised`` (``{status: n}`` outside the vocabulary),
      ``reviewed``, ``not_reviewed``.
    - ``totals.scenarios``: ``exercised`` (scenarios with a run in scope),
      ``corpus`` (loaded scenarios, drafts excluded, within ``plane``),
      ``corpus_exercised``, ``corpus_not_run`` (never run when no filter is set).
    - ``totals.techniques_exercised``, ``totals.test_cases_touched``: distinct
      techniques and test cases the exercised scenarios map to.
    - ``totals.steps`` from ``step_outcomes``: ``recorded``, ``ran`` (exited 0),
      ``failed`` (exited non-zero), ``did_not_run``, ``interrupted`` with
      ``interrupted_by`` (killed, timed out, aborted, beacon lost),
      ``unrecognised``, ``runs_with_host_record``, ``runs_without_host_record``.
    - ``totals.detections``: ``expected`` and one count per outcome
      (``prevented``, ``detected``, ``detected_not_prevented``, ``missed``,
      ``pending``, ``not_reached``, ``not_executed``), plus ``not_reviewed``
      (no outcome, not observed, on a run nobody reviewed) and ``unrecognised``.
    - ``by_scenario[]``: ``scenario_id``, ``name``, ``plane``, ``techniques``,
      ``uc_ref``, ``tc_refs``, ``detection_sources`` (``[{id, label}]`` ingestion
      doors, ``null`` when they cannot be derived), ``runs``, ``runs_by_status``,
      ``first_run_at``, ``last_run_at``, ``last_status``, ``last_run_id``,
      ``reviewed_runs``, ``last_reviewed_run_id``, ``last_reviewed_at`` and
      ``outcomes``: the detection counts of its most recent reviewed run,
      ``null`` when no run of it was reviewed.
    - ``by_technique[]`` / ``by_test_case[]``: ``technique`` + ``name`` or
      ``tc_ref`` + ``name`` + ``uc_ref``; ``scenarios``, ``runs``,
      ``last_run_at``, ``reviewed_scenarios`` and ``outcomes`` summed over each
      scenario's most recent reviewed run (``null`` when none was reviewed). A
      technique counts only the detections on the steps that carry it.
    - ``by_plane[]`` ``{plane, runs, scenarios}``; ``by_agent[]``
      ``{agent, runs, scenarios, last_run_at}`` (``agent`` is ``null`` for runs
      sent to no agent, such as staged bundles).
    - ``detection_sources``: ``{available, reason, failed}``; ``failed`` lists
      scenarios whose doors could not be derived.

    ``format=csv`` returns one row per scenario with the ``by_scenario``
    columns (lists joined with ``;``) as an attachment.
    """
    since_dt = coerce_utc(since) if since else None
    until_dt = coerce_utc(until) if until else None
    if (since and since_dt is None) or (until and until_dt is None):
        return _error(422, "since and until must be ISO-8601 timestamps", "INVALID_TIME_RANGE",
                      f"since={since!r} until={until!r}")
    scope = _scope(since=since_dt, until=until_dt, plane=plane, status=status, agent=agent,
                   include_drafts=include_drafts)

    # ── runs, per scenario and status ────────────────────────────────────────
    grouped = (await db.execute(
        select(Run.scenario_id, Run.status, func.count(Run.id), func.min(Run.started_at),
               func.max(Run.started_at), func.sum(RUN_REVIEWED))
        .where(*scope).group_by(Run.scenario_id, Run.status))).all()

    by_status = {s: 0 for s in RUN_STATUSES}
    unrecognised_status: dict[str, int] = {}
    per_scenario: dict[str, dict[str, Any]] = {}
    for scenario_id, run_status, n, first, last, reviewed in grouped:
        if run_status in by_status:
            by_status[run_status] += n
        else:
            key = str(run_status)
            unrecognised_status[key] = unrecognised_status.get(key, 0) + n
        row = per_scenario.setdefault(scenario_id, {
            "runs": 0, "runs_by_status": {}, "first": first, "last": last, "reviewed_runs": 0})
        row["runs"] += n
        row["runs_by_status"][str(run_status)] = row["runs_by_status"].get(str(run_status), 0) + n
        row["first"] = min(row["first"], first)
        row["last"] = max(row["last"], last)
        row["reviewed_runs"] += int(reviewed or 0)
    total_runs = sum(r["runs"] for r in per_scenario.values())
    reviewed_runs = sum(r["reviewed_runs"] for r in per_scenario.values())

    # ── each scenario's newest run and newest reviewed run ───────────────────
    newest = (Run.started_at.desc(), Run.id.desc())
    ranked = (select(
        Run.scenario_id, Run.run_id, Run.status, Run.started_at, RUN_REVIEWED.label("reviewed"),
        func.row_number().over(partition_by=Run.scenario_id, order_by=newest).label("rn"),
        func.row_number().over(partition_by=(Run.scenario_id, RUN_REVIEWED), order_by=newest).label("rrn"),
    ).where(*scope)).subquery()
    latest = (await db.execute(
        select(ranked.c.scenario_id, ranked.c.run_id, ranked.c.status, ranked.c.started_at,
               ranked.c.reviewed, ranked.c.rn, ranked.c.rrn)
        .where(or_(ranked.c.rn == 1, and_(ranked.c.reviewed == 1, ranked.c.rrn == 1))))).all()
    last_reviewed_run: dict[str, str] = {}
    for scenario_id, run_id, run_status, started_at, reviewed, rn, rrn in latest:
        row = per_scenario.get(scenario_id)
        if row is None:   # a run launched between the two reads; the next call counts it
            continue
        if rn == 1:
            row["last_run_id"], row["last_status"] = run_id, run_status
        if reviewed and rrn == 1:
            row["last_reviewed_run_id"], row["last_reviewed_at"] = run_id, started_at
            last_reviewed_run[run_id] = scenario_id

    # ── scenario metadata: light columns for all, steps for the exercised ────
    meta_rows = (await db.execute(select(
        Scenario.scenario_id, Scenario.name, Scenario.plane, Scenario.status,
        Scenario.mitre_technique, Scenario.mitre_technique_name, Scenario.additional_techniques,
        Scenario.uc_ref, Scenario.tc_ref, Scenario.tc_name, Scenario.tc_refs))).all()
    meta: dict[str, dict[str, Any]] = {}
    technique_names: dict[str, str] = {}
    for (sid, name, sc_plane, sc_status, technique, technique_name, additional,
         uc_ref, tc_ref, tc_name, tc_refs) in meta_rows:
        meta[sid] = {"name": name, "plane": sc_plane, "status": sc_status, "technique": technique,
                     "uc_ref": uc_ref, "tc_ref": tc_ref, "tc_name": tc_name,
                     "tc_refs": [t for t in (tc_refs or []) if t] or ([tc_ref] if tc_ref else [])}
        if technique and technique_name:
            technique_names.setdefault(technique, technique_name)
        for extra in additional or []:
            if isinstance(extra, dict) and extra.get("technique") and extra.get("name"):
                technique_names.setdefault(extra["technique"], extra["name"])
    corpus_ids = {sid for sid, m in meta.items()
                  if m["status"] not in ("draft", SCENARIO_RETIRED) and not sid.startswith(DRAFT_PREFIX)
                  and (not plane or m["plane"] == plane)}

    exercised = sorted(per_scenario)
    steps_of: dict[str, Any] = {}
    for chunk in _chunks([sid for sid in exercised if sid in meta]):
        for sid, steps in (await db.execute(
                select(Scenario.scenario_id, Scenario.steps).where(Scenario.scenario_id.in_(chunk)))).all():
            steps_of[sid] = steps

    try:
        dataset_registry = load_dataset_registry()
        sources_state: dict[str, Any] = {
            "available": dataset_registry is not None,
            "reason": None if dataset_registry is not None else "REGISTRY_ABSENT", "failed": []}
    except DatasetRequirementsError as exc:
        logger.error("dataset requirement registry malformed: %s", exc)
        dataset_registry = None
        sources_state = {"available": False, "reason": "REGISTRY_MALFORMED", "failed": []}

    techniques_of: dict[str, list[str]] = {}
    step_technique: dict[str, dict[Any, Optional[str]]] = {}
    sources_of: dict[str, Optional[list[dict[str, str]]]] = {}
    for sid in exercised:
        m = meta.get(sid)
        techniques_of[sid], step_technique[sid] = _scenario_techniques(
            m["technique"] if m else None, steps_of.get(sid))
        sources_of[sid] = None
        if m is not None and dataset_registry is not None:
            try:
                sources_of[sid] = _detection_sources(sid, steps_of.get(sid), dataset_registry)
            except DoorResolutionError as exc:
                logger.warning("detection sources not derived for %s: %s", sid, exc)
                sources_state["failed"].append(sid)

    # ── detections: every run in scope, then each newest reviewed run ────────
    is_dnp = case((Result.outcome_reason == DETECTED_NOT_PREVENTED, 1), else_=0)
    detections = _new_outcomes(with_not_reviewed=True)
    for outcome, observed, dnp, run_reviewed, n in (await db.execute(
            select(Result.outcome, Result.observed, is_dnp, RUN_REVIEWED, func.count(Result.id))
            .join(Run, Run.run_id == Result.run_id).where(*scope)
            .group_by(Result.outcome, Result.observed, is_dnp, RUN_REVIEWED))).all():
        _add_outcome(detections, outcome=outcome, observed=observed,
                     detected_not_prevented=dnp, run_reviewed=run_reviewed, n=n)

    scenario_outcomes: dict[str, dict[str, Any]] = {
        sid: _new_outcomes() for sid in last_reviewed_run.values()}
    technique_outcomes: dict[tuple[str, str], dict[str, Any]] = {}
    for chunk in _chunks(sorted(last_reviewed_run)):
        for run_id, step_id, outcome, observed, dnp, n in (await db.execute(
                select(Result.run_id, Result.step_id, Result.outcome, Result.observed, is_dnp,
                       func.count(Result.id))
                .where(Result.run_id.in_(chunk))
                .group_by(Result.run_id, Result.step_id, Result.outcome, Result.observed, is_dnp))).all():
            sid = last_reviewed_run[run_id]
            kw = {"outcome": outcome, "observed": observed, "detected_not_prevented": dnp,
                  "run_reviewed": True, "n": n}
            _add_outcome(scenario_outcomes[sid], **kw)
            tech = step_technique[sid].get(step_id) or (meta.get(sid) or {}).get("technique")
            if tech:
                _add_outcome(technique_outcomes.setdefault((sid, tech), _new_outcomes()), **kw)

    # ── steps: what the host recorded ────────────────────────────────────────
    exit_zero = case((StepOutcome.exit_code.is_(None), None), (StepOutcome.exit_code == 0, 1), else_=0)
    steps: dict[str, Any] = {"recorded": 0, "ran": 0, "failed": 0, "did_not_run": 0,
                             "interrupted": 0, "interrupted_by": {}, "unrecognised": {}}
    for termination, zero, n in (await db.execute(
            select(StepOutcome.termination, exit_zero, func.count(StepOutcome.id))
            .join(Run, Run.run_id == StepOutcome.run_id).where(*scope)
            .group_by(StepOutcome.termination, exit_zero))).all():
        steps["recorded"] += n
        if termination == TERMINATION_EXITED and zero is not None:
            steps["ran" if zero else "failed"] += n
        elif termination == TERMINATION_NOT_RUN:
            steps["did_not_run"] += n
        elif termination in TERMINATIONS and termination != TERMINATION_EXITED:
            steps["interrupted"] += n
            steps["interrupted_by"][termination] = steps["interrupted_by"].get(termination, 0) + n
        else:
            key = "exited (no exit code)" if termination == TERMINATION_EXITED else str(termination)
            steps["unrecognised"][key] = steps["unrecognised"].get(key, 0) + n
    with_host_record = (await db.execute(
        select(func.count(func.distinct(StepOutcome.run_id)))
        .join(Run, Run.run_id == StepOutcome.run_id).where(*scope))).scalar_one()
    steps["runs_with_host_record"] = int(with_host_record or 0)
    steps["runs_without_host_record"] = total_runs - steps["runs_with_host_record"]

    # ── agents ───────────────────────────────────────────────────────────────
    by_agent = [
        {"agent": target, "runs": n, "scenarios": scenarios, "last_run_at": _iso(last)}
        for target, n, scenarios, last in (await db.execute(
            select(Run.target, func.count(Run.id), func.count(func.distinct(Run.scenario_id)),
                   func.max(Run.started_at))
            .where(*scope).group_by(Run.target))).all()]
    by_agent.sort(key=lambda r: (-r["runs"], str(r["agent"] or "")))

    engine_runs = (await db.execute(select(func.count(Run.id)))).scalar_one()
    known_agents = [a for (a,) in (await db.execute(
        select(Run.target).where(Run.target.is_not(None)).distinct())).all()]

    # ── assemble ─────────────────────────────────────────────────────────────
    by_scenario: list[dict[str, Any]] = []
    by_technique: dict[str, dict[str, Any]] = {}
    by_test_case: dict[str, dict[str, Any]] = {}
    by_plane: dict[Optional[str], dict[str, Any]] = {}

    def _rollup(table: dict[str, dict[str, Any]], key: str, seed: dict[str, Any], sid: str,
                agg: dict[str, Any], outcomes: Optional[dict[str, Any]]) -> None:
        row = table.setdefault(key, {**seed, "scenarios": [], "runs": 0, "last": None,
                                     "reviewed_scenarios": 0, "outcomes": None})
        row["scenarios"].append(sid)
        row["runs"] += agg["runs"]
        row["last"] = agg["last"] if row["last"] is None else max(row["last"], agg["last"])
        if agg.get("last_reviewed_run_id"):
            row["reviewed_scenarios"] += 1
            if row["outcomes"] is None:
                row["outcomes"] = _new_outcomes()
            if outcomes is not None:
                _merge_outcomes(row["outcomes"], outcomes)

    for sid in exercised:
        agg, m = per_scenario[sid], meta.get(sid)
        # Seeded for every reviewed scenario, so a reviewed run that expected
        # nothing reads zeros and an unreviewed scenario reads None.
        outcomes = scenario_outcomes.get(sid)
        by_scenario.append({
            "scenario_id": sid,
            "name": m["name"] if m else None,
            "plane": m["plane"] if m else None,
            "techniques": techniques_of[sid],
            "uc_ref": m["uc_ref"] if m else None,
            "tc_refs": m["tc_refs"] if m else [],
            "detection_sources": sources_of[sid],
            "runs": agg["runs"],
            "runs_by_status": agg["runs_by_status"],
            "first_run_at": _iso(agg["first"]),
            "last_run_at": _iso(agg["last"]),
            "last_status": agg.get("last_status"),
            "last_run_id": agg.get("last_run_id"),
            "reviewed_runs": agg["reviewed_runs"],
            "last_reviewed_run_id": agg.get("last_reviewed_run_id"),
            "last_reviewed_at": _iso(agg.get("last_reviewed_at")),
            "outcomes": outcomes,
        })
        for tech in techniques_of[sid]:
            _rollup(by_technique, tech, {"technique": tech, "name": technique_names.get(tech)},
                    sid, agg, technique_outcomes.get((sid, tech)))
        for tc_ref in (m["tc_refs"] if m else []):
            if tc_ref not in by_test_case:
                tc_name, tc_uc = _test_case_meta(tc_ref, m)
                seed = {"tc_ref": tc_ref, "name": tc_name, "uc_ref": tc_uc}
            else:
                seed = {}
            _rollup(by_test_case, tc_ref, seed, sid, agg, outcomes)
        plane_row = by_plane.setdefault(m["plane"] if m else None,
                                        {"plane": m["plane"] if m else None, "runs": 0, "scenarios": 0})
        plane_row["runs"] += agg["runs"]
        plane_row["scenarios"] += 1

    def _finish(rows: Iterable[dict[str, Any]], key: str) -> list[dict[str, Any]]:
        out = []
        for row in rows:
            row["last_run_at"] = _iso(row.pop("last"))
            out.append(row)
        return sorted(out, key=lambda r: r[key])

    by_scenario.sort(key=lambda r: (r["last_run_at"] or "", r["scenario_id"]), reverse=True)
    exercised_corpus = len(corpus_ids.intersection(exercised))
    report = {
        "generated_at": _iso(datetime.utcnow()),
        "window": {
            "since": _iso(since_dt), "until": _iso(until_dt),
            "first_run_at": _iso(min((r["first"] for r in per_scenario.values()), default=None)),
            "last_run_at": _iso(max((r["last"] for r in per_scenario.values()), default=None)),
        },
        "filters": {"plane": plane or None, "status": status or None, "agent": agent or None,
                    "include_drafts": include_drafts},
        "filter_options": {
            "planes": sorted({m["plane"] for m in meta.values() if m["plane"]}),
            "statuses": [*RUN_STATUSES, *sorted(unrecognised_status)],
            "agents": sorted(known_agents),
        },
        "empty": total_runs == 0,
        "engine_runs": int(engine_runs or 0),
        "totals": {
            "runs": {"total": total_runs, "by_status": by_status, "unrecognised": unrecognised_status,
                     "reviewed": reviewed_runs, "not_reviewed": total_runs - reviewed_runs},
            "scenarios": {"exercised": len(exercised), "corpus": len(corpus_ids),
                          "corpus_exercised": exercised_corpus,
                          "corpus_not_run": len(corpus_ids) - exercised_corpus},
            "techniques_exercised": len(by_technique),
            "test_cases_touched": len(by_test_case),
            "steps": steps,
            "detections": detections,
        },
        "by_scenario": by_scenario,
        "by_technique": _finish(by_technique.values(), "technique"),
        "by_test_case": _finish(by_test_case.values(), "tc_ref"),
        "by_plane": sorted(by_plane.values(), key=lambda r: (-r["runs"], str(r["plane"] or ""))),
        "by_agent": by_agent,
        "detection_sources": sources_state,
    }
    logger.info("reports.executions runs=%d scenarios=%d format=%s filters=%s",
                total_runs, len(exercised), format, report["filters"])
    if format == "csv":
        return _csv_response(by_scenario)
    return report


#: Text that starts with one of these is read as a formula by Excel and Sheets.
_CSV_FORMULA_LEAD = ("=", "+", "-", "@")


def _csv_cell(value: Any) -> Any:
    """One CSV cell, safe to open in a spreadsheet.

    Scenario and draft names are operator-written text. Text that starts like a
    formula, after any leading whitespace, or that starts with a tab or a
    carriage return, gets a leading ``'`` so the spreadsheet shows it instead
    of running it; the text after the quote is unchanged. Numbers pass through;
    ``None`` is an empty cell.
    """
    if value is None:
        return ""
    if isinstance(value, str) and (
            value.startswith(("\t", "\r")) or value.lstrip().startswith(_CSV_FORMULA_LEAD)):
        return "'" + value
    return value


def _csv_list(items: Iterable[Any]) -> str:
    """A list as one ``;``-joined cell. Each element is made safe on its own:
    a spreadsheet that splits on ``;`` would put a later element at the start
    of a cell."""
    return ";".join(str(_csv_cell(item)) for item in items)


def _csv_response(rows: list[dict[str, Any]]) -> Response:
    """One row per scenario; lists joined with ``;``. A scenario with no
    reviewed run leaves its outcome cells blank, never 0. Every cell goes
    through :func:`_csv_cell`, and every element of a joined cell too."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_ALL)
    writer.writerow(CSV_COLUMNS)
    for row in rows:
        outcomes = row["outcomes"]
        sources = row["detection_sources"]
        flat = {
            **row,
            "techniques": _csv_list(row["techniques"]),
            "tc_refs": _csv_list(row["tc_refs"]),
            "detection_sources": "" if sources is None else _csv_list(s["label"] for s in sources),
            **{k: ("" if outcomes is None else outcomes[k]) for k in ("expected", *OUTCOME_KEYS)},
        }
        writer.writerow([_csv_cell(flat.get(col)) for col in CSV_COLUMNS])
    stamp = datetime.utcnow().strftime("%Y%m%d")
    return Response(
        content=buf.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="cortexsim-executions-{stamp}.csv"'})
