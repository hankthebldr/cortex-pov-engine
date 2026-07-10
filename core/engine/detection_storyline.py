"""
CortexSim Detection Storyline — pure assembler for the Detection Proof Layer.

Given a Run, its seeded Result rows, and the scenario's ordered steps, this
module builds an ordered "detection storyline": a kill-chain where each entry
lines up ``step → expected detection → observed Cortex alert → real MTTD``. The
SAME structure feeds two faces — the live presenter timeline and the executive
scorecard — so coverage and MTTD are computed once, here, against a KNOWN
denominator (the orchestrator seeds one Result per expected detection per step).

Design rules (mirrors ``engine.ttp_catalog`` / ``connectors.matcher``):

* **Pure.** No DB, no HTTP, no ORM session. Inputs are dicts (``Result.to_dict``
  / ``Run.to_dict`` / ``scenario.steps`` shape) OR ORM objects exposing
  ``.to_dict()`` — either is accepted so the builder is exhaustively
  unit-testable and directly API-servable.
* **Deterministic order.** Entries follow scenario step order, then the order
  detections were seeded within each step (= ``expected_detections`` order).
* **Explainable status.** ``detected`` when the Result was observed;
  ``pending`` while the run is still in-flight; ``missed`` only once the run
  reaches a terminal state without the detection firing. This is what turns the
  seeded denominator into true per-engine false-NEGATIVE accounting.

The observed alert record (name / id / technique / firing time) is read from the
Result row when present, or from an optional ``observations`` map keyed by
``result_id`` (the shape the connector reconcile path will hand in once the
evidence table is wired). Nothing here writes; callers own persistence.
"""

from __future__ import annotations

import math
from typing import Any, Optional

# Run statuses that mean "the attack is over" — an un-observed detection is now
# a genuine gap (missed), not merely awaiting ingest (pending). ``staged`` is a
# push-mode terminal state where reconciliation has not yet happened, so it is
# deliberately NOT terminal here: its detections read as ``pending`` (proof
# pending) rather than a misleading 0%-missed.
TERMINAL_RUN_STATUSES = frozenset({"complete", "failed", "aborted"})

STATUS_DETECTED = "detected"
STATUS_PENDING = "pending"
STATUS_MISSED = "missed"


# ---------------------------------------------------------------------------
# Normalization helpers — accept ORM objects or plain dicts
# ---------------------------------------------------------------------------


def _as_dict(obj: Any) -> dict[str, Any]:
    """Coerce an ORM row (with ``.to_dict()``) or a mapping to a plain dict.

    Returns an empty dict for ``None`` so downstream ``.get`` calls are safe.
    """
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    # Last-resort: shallow attribute scrape (best effort, never raises).
    return {k: v for k, v in vars(obj).items() if not k.startswith("_")}


def _is_detected(result: dict[str, Any]) -> bool:
    """A detection counts as DETECTED only when the ``observed`` flag is set.

    Canonical convention, shared with ``efficacy_scorecard._status`` and
    ``report_generator._status_from_result`` so the presenter timeline and the
    executive scorecard never report different coverage for the same run: an
    ``observed_at`` timestamp *without* ``observed`` means the DC reviewed the
    detection and it did NOT fire → that is a ``missed`` gap, not a catch.
    """
    return bool(result.get("observed"))


def _detection_type(result: dict[str, Any]) -> Optional[str]:
    """The Cortex-native detection engine for this expected detection.

    Prefers the scenario's declared ``signal_type`` (BIOC | XQL | Analytics |
    Correlation | IOC | ABIOC), falling back to the resolved card
    ``detection_kind`` (bioc | xql | correlation | ioc | analytics | abioc)
    upper-cased so the badge is stable even for un-typed legacy rows.
    """
    sig = result.get("signal_type")
    if sig:
        return sig
    kind = result.get("detection_kind")
    if kind:
        return str(kind).upper()
    return None


# ---------------------------------------------------------------------------
# Observed-alert projection
# ---------------------------------------------------------------------------


def _build_observed(
    result: dict[str, Any], observation: Optional[dict[str, Any]]
) -> Optional[dict[str, Any]]:
    """Build the ``observed`` sub-object for a detection, or ``None``.

    ``None`` when the detection has not fired. When it has, always returns a
    dict carrying the four contract keys (``alert_name``, ``alert_id``,
    ``technique``, ``observed_at``) plus ``severity`` / ``host`` when the
    connector supplied them. Values come from ``observation`` (a reconciled
    ObservedAlert projection) when present, else from the Result row itself
    (the human-checkbox path has an ``observed_at`` but no alert evidence).
    """
    if not _is_detected(result):
        return None

    obs = observation or {}
    result_technique = result.get("mitre_technique")

    techniques = obs.get("techniques") or []
    technique = techniques[0] if techniques else (
        obs.get("technique") or result_technique
    )

    return {
        "alert_name": obs.get("name") or obs.get("alert_name"),
        "alert_id": obs.get("external_id") or obs.get("alert_id"),
        "technique": technique,
        "observed_at": obs.get("observed_at") or result.get("observed_at"),
        "severity": obs.get("severity") or result.get("detection_severity"),
        "host": obs.get("host"),
    }


def _entry_status(result: dict[str, Any], run_status: str) -> str:
    """Classify one detection: detected / pending / missed.

    Mirrors ``efficacy_scorecard._status`` (detected → observed; a set
    ``observed_at`` without ``observed`` → reviewed-but-unfired = missed) but
    keeps the run-status nuance: an un-reviewed detection is only ``missed``
    once the run has actually reached a terminal state, otherwise ``pending``.
    """
    if _is_detected(result):
        return STATUS_DETECTED
    # DC reviewed this detection and it did not fire → a real gap.
    if result.get("observed_at"):
        return STATUS_MISSED
    if run_status in TERMINAL_RUN_STATUSES:
        return STATUS_MISSED
    return STATUS_PENDING


def _evidence_refs(result: dict[str, Any]) -> list[Any]:
    """Extract evidence row ids the Result carries (populated once the
    ResultEvidence table is wired — module B). Tolerates a list of dicts
    (``[{"id": 1, ...}]``) or a bare list of ids; returns ``[]`` otherwise."""
    ev = result.get("evidence") or result.get("evidence_refs") or []
    refs: list[Any] = []
    for item in ev:
        if isinstance(item, dict):
            if item.get("id") is not None:
                refs.append(item["id"])
        else:
            refs.append(item)
    return refs


def _build_entry(
    result: dict[str, Any],
    step: dict[str, Any],
    run_status: str,
    observation: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble one storyline entry (step → expected → observed → MTTD)."""
    observed = _build_observed(result, observation)
    status = _entry_status(result, run_status)
    return {
        "step_id": result.get("step_id") or step.get("id"),
        "step_name": result.get("step_name") or step.get("name") or "",
        "mitre_technique": result.get("mitre_technique") or step.get("mitre_technique"),
        "ttp_ref": result.get("ttp_ref"),
        "expected_detection": {
            "type": _detection_type(result),
            "plane": result.get("plane"),
            "description": result.get("expected_detection") or "",
            "detection_id": result.get("detection_id"),
        },
        "observed": observed,
        "status": status,
        # MTTD is only meaningful once a detection has actually fired.
        "mttd_seconds": result.get("mttd_seconds") if status == STATUS_DETECTED else None,
        "evidence_refs": _evidence_refs(result),
        # Convenience carry-throughs the two faces use directly.
        "result_id": result.get("id"),
        "detection_kind": result.get("detection_kind"),
    }


# ---------------------------------------------------------------------------
# Summary statistics
# ---------------------------------------------------------------------------


def _percentile(values: list[float], pct: float) -> Optional[float]:
    """Linear-interpolation percentile over ``values`` (0 <= pct <= 100).

    Returns ``None`` for an empty list; matches the common
    ``numpy.percentile`` 'linear' method so p50/p90 are defensible in a report.
    """
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(float(ordered[0]), 1)
    rank = (len(ordered) - 1) * (pct / 100.0)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return round(float(ordered[int(rank)]), 1)
    frac = rank - lo
    interp = ordered[lo] + (ordered[hi] - ordered[lo]) * frac
    return round(float(interp), 1)


def build_summary(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Run-level rollup over already-built storyline entries.

    ``{total, detected, pending, missed, coverage_pct, mttd:{min,median,p90,max,count}}``
    where the MTTD distribution is taken over the detected entries only.
    """
    total = len(entries)
    detected = sum(1 for e in entries if e["status"] == STATUS_DETECTED)
    pending = sum(1 for e in entries if e["status"] == STATUS_PENDING)
    missed = sum(1 for e in entries if e["status"] == STATUS_MISSED)

    mttds = [
        float(e["mttd_seconds"])
        for e in entries
        if e["status"] == STATUS_DETECTED and e.get("mttd_seconds") is not None
    ]

    return {
        "total": total,
        "detected": detected,
        "pending": pending,
        "missed": missed,
        "coverage_pct": round(detected / total * 100, 1) if total else 0.0,
        "mttd": {
            "count": len(mttds),
            "min": _percentile(mttds, 0),
            "median": _percentile(mttds, 50),
            "p90": _percentile(mttds, 90),
            "max": _percentile(mttds, 100),
        },
    }


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------


def build_storyline(
    run: Any,
    results: list[Any],
    scenario_steps: list[Any],
    *,
    observations: Optional[dict[Any, dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Assemble the ordered detection storyline for one run.

    Parameters
    ----------
    run:
        A ``Run`` ORM row or its ``to_dict()`` mapping (needs ``run_id``,
        ``scenario_id``, ``status``). ``None`` is tolerated.
    results:
        The run's seeded ``Result`` rows (ORM or ``to_dict()`` mappings), one
        per expected detection per step.
    scenario_steps:
        The scenario's ordered ``steps`` list (each ``{id, name,
        mitre_technique, expected_detections[]}``). Drives kill-chain order and
        backfills step metadata missing on a Result.
    observations:
        Optional map ``{result_id: observed_alert_dict}`` (ObservedAlert
        ``to_dict`` shape) to enrich the ``observed`` sub-object with the real
        firing alert. Absent by default (offline / human-checkbox path).

    Returns
    -------
    dict
        ``{run_id, scenario_id, run_status, steps[], entries[], summary}`` where
        ``entries`` is the flat ordered kill-chain and ``steps`` is the same
        entries grouped per step for the timeline face. Fully JSON-serializable.
    """
    run_d = _as_dict(run)
    run_status = run_d.get("status") or STATUS_PENDING
    obs_map = observations or {}

    # Normalize + index results by step so we can iterate steps in scenario
    # order while preserving intra-step seed order.
    result_dicts = [_as_dict(r) for r in (results or [])]
    by_step: dict[Any, list[dict[str, Any]]] = {}
    for r in result_dicts:
        by_step.setdefault(r.get("step_id"), []).append(r)

    step_dicts = [_as_dict(s) for s in (scenario_steps or [])]

    entries: list[dict[str, Any]] = []
    step_groups: list[dict[str, Any]] = []
    consumed_step_ids: set[Any] = set()

    def _emit_step(step: dict[str, Any], index: int, step_results: list[dict[str, Any]]) -> None:
        group_entries: list[dict[str, Any]] = []
        for result in step_results:
            observation = obs_map.get(result.get("id"))
            entry = _build_entry(result, step, run_status, observation)
            entry["step_index"] = index
            entries.append(entry)
            group_entries.append(entry)
        step_groups.append({
            "step_id": step.get("id"),
            "step_index": index,
            "step_name": step.get("name") or "",
            "mitre_technique": step.get("mitre_technique"),
            "detections": group_entries,
        })

    index = 0
    for step in step_dicts:
        step_id = step.get("id")
        consumed_step_ids.add(step_id)
        _emit_step(step, index, by_step.get(step_id, []))
        index += 1

    # Defensive: Result rows whose step_id has no matching scenario step
    # (schema drift / renamed step). Append them in a stable order so nothing
    # silently vanishes from the coverage denominator.
    orphan_step_ids = [sid for sid in by_step if sid not in consumed_step_ids]
    for step_id in sorted(orphan_step_ids, key=lambda s: (s is None, str(s))):
        step_results = by_step[step_id]
        synthetic_name = step_results[0].get("step_name") if step_results else ""
        _emit_step(
            {"id": step_id, "name": synthetic_name, "mitre_technique": None},
            index,
            step_results,
        )
        index += 1

    return {
        "run_id": run_d.get("run_id"),
        "scenario_id": run_d.get("scenario_id"),
        "run_status": run_status,
        "steps": step_groups,
        "entries": entries,
        "summary": build_summary(entries),
    }
