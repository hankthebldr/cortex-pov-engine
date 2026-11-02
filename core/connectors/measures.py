"""Measured values the read-back loop can derive for ``verifier.score_run``.

Until sprint 2 the scorer had exactly one native input, mean MTTD, so the 47
scenarios whose primary KPI is *Detection Accuracy* and the 21 whose KPI is a
*correlation rate* resolved ``pending`` forever — even though the reconcile
loop already computed both halves of the accuracy ratio and threw it away.

Everything here is pure (no DB, no IO) and guarded by the honesty rules the
audit set out:

* a number derived from an **unscoped** pull (no resolvable target host, so
  any endpoint's alert could have been credited) may never produce a PASS —
  extra alerts can only have raised it, so a FAIL stands;
* a number derived from a **truncated** pull (the tenant held more alerts
  than were fetched) is a floor — a floor that clears the bar is a PASS, a
  floor below it proves nothing, so a FAIL is withheld;
* **zero matched** after a reconcile is "not landed yet", never "missed".
  Giving up is the sweep's attempt cap, and it records ``pending``.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from engine.outcome import EXCLUDED_FROM_DENOMINATOR, is_hit, is_in_denominator
from engine.verifier import FAIL, PASS, evaluate_threshold

MTTD = "mttd"
ACCURACY = "accuracy"
CORRELATION = "correlation"

_ACCURACY_KPIS = ("detection accuracy",)
_CORRELATION_KPIS = ("cross-source correlation rate", "stitch completeness",
                     "correlation coverage")


def kpi_family(primary_kpi: Optional[str], threshold: Any) -> Optional[str]:
    """Which measured value a scenario's primary KPI wants, or None.

    The threshold's own ``kpi`` wins over the scenario field, and the **unit**
    wins over the label: three scenarios write "Detection Accuracy ≤ 300
    seconds", which is an MTTD bar wearing the wrong name.
    """
    kpi = (primary_kpi or "")
    unit = ""
    if isinstance(threshold, dict):
        kpi = threshold.get("kpi") or kpi
        unit = str(threshold.get("unit") or "").strip().lower()
    k = kpi.strip().lower()
    if not k:
        return None
    if unit in ("seconds", "second", "s", "sec", "secs", "minutes", "min"):
        return MTTD
    if "mttd" in k or "time to detect" in k:
        return MTTD
    if unit != "%":
        return None
    if k in _ACCURACY_KPIS:
        return ACCURACY
    if k in _CORRELATION_KPIS:
        return CORRELATION
    return None


def denominator_exclusion(result: Any) -> Optional[str]:
    """Why ``result`` leaves the efficacy denominator, or None when it counts.

    ``not_reached`` / ``not_executed`` — the step never ran, so neither a hit
    nor a miss is evidence; ``soak_pending`` — an Analytics/ABIOC row that
    stays pending for a soak reason. The key is the one ``excluded`` reports.
    """
    outcome = getattr(result, "outcome", None)
    if is_in_denominator(outcome, getattr(result, "outcome_reason", None)):
        return None
    return outcome if outcome in EXCLUDED_FROM_DENOMINATOR else "soak_pending"


def empty_exclusions() -> dict[str, int]:
    return {"not_reached": 0, "not_executed": 0, "soak_pending": 0}


class AccuracyMeasure(tuple):
    """``(percent, observed, seeded)`` that also carries ``.excluded``.

    A 3-tuple so every caller that unpacks it keeps working; ``excluded`` is
    the count of rows taken out of BOTH numerator and denominator, by reason.
    """

    excluded: dict[str, int]

    def __new__(cls, value: Optional[float], observed: int, seeded: int,
                excluded: dict[str, int]) -> "AccuracyMeasure":
        t = super().__new__(cls, (value, observed, seeded))
        t.excluded = excluded
        return t


def detection_accuracy(results: Iterable[Any]) -> AccuracyMeasure:
    """``(percent, observed, seeded)`` over the results that actually executed.

    Rows whose outcome is ``not_reached``/``not_executed``, or which are
    soak-pending, leave numerator and denominator; ``.excluded`` counts them.
    None (not 0.0) when nothing remains: an empty run has no accuracy.
    """
    excluded = empty_exclusions()
    seeded = []
    for r in results:
        if getattr(r, "executed_at", None) is None:
            continue
        why = denominator_exclusion(r)
        if why is not None:
            excluded[why] += 1
            continue
        seeded.append(r)
    if not seeded:
        return AccuracyMeasure(None, 0, 0, excluded)
    # The one predicate (R1): a ``pending`` row is not a hit even when the
    # reconcile set ``observed`` (an alert with no host witness).
    observed = sum(1 for r in seeded
                   if is_hit(getattr(r, "outcome", None), getattr(r, "observed", False)))
    return AccuracyMeasure(round(observed / len(seeded) * 100.0, 1), observed,
                           len(seeded), excluded)


def correlation_rate(alert_incidents: dict[str, Optional[str]]) -> "tuple[Optional[float], int, int]":
    """``(percent, alerts, incidents)`` — how far N alerts collapsed into cases.

    ``alert_incidents`` maps alert id → incident id (None when the tenant put no
    incident on the alert). The collapse ratio is ``(A - k) / (A - 1)``: six
    alerts in one incident is 100 %, six alerts in six incidents is 0 %. One
    alert cannot correlate with anything, so A < 2 is None, not 100 %.
    """
    ids = [v for v in alert_incidents.values() if v]
    a = len(ids)
    if a < 2:
        return None, a, len(set(ids))
    k = len(set(ids))
    return round((a - k) / (a - 1) * 100.0, 1), a, k


def correlation_rate_from_incidents(incidents: Iterable[Any]) -> "tuple[Optional[float], int, int]":
    """Same ratio from ``get_incidents`` rows (``alert_count`` per incident).

    The fallback basis when the tenant does not stamp ``incident_id`` on alert
    objects: it counts every alert the matching incidents hold, not only the
    run's, so the caller labels it ``basis: host_incidents``.
    """
    rows = list(incidents)
    a = sum(int(getattr(r, "alert_count", 0) or 0) for r in rows)
    k = len(rows)
    if a < 2:
        return None, a, k
    return round((a - k) / (a - 1) * 100.0, 1), a, k


def guard_measurement(
    threshold: Any, value: Optional[float], *,
    unscoped: bool, truncated: bool, matched: Optional[int] = None,
) -> "tuple[Optional[float], Optional[str]]":
    """Apply the honesty rules; return ``(value_to_score, withheld_reason)``.

    A withheld value scores ``pending`` with the reason carried into the
    verdict detail, so the console can say *why* rather than showing a bar
    nobody cleared.
    """
    if value is None:
        return None, "no measured value"
    if matched == 0:
        return None, ("no alert matched yet — zero after one reconcile is not a "
                      "miss; the sweep's attempt cap records giving up, as pending")
    provisional = evaluate_threshold(threshold, value).verdict
    if provisional == PASS and unscoped:
        return None, ("pass withheld: the pull was unscoped (no resolvable target "
                      "host), so an alert from any endpoint could have been credited")
    if provisional == FAIL and truncated:
        return None, ("fail withheld: the pull was truncated, so this value is a "
                      "floor — more alerts exist in the tenant than were fetched")
    return value, None


# ---------------------------------------------------------------------------
# One entry point for the service: scenario contract + pull context → value
# ---------------------------------------------------------------------------

def build_measurement(
    primary_kpi: Optional[str], threshold: Any, results: Iterable[Any],
    ctx: Optional[dict[str, Any]],
) -> "tuple[Optional[float], Optional[dict[str, Any]]]":
    """``(measured_value_for_score_run, measurement_record)``.

    ``ctx`` is the pull context the reconcile path knows and the scorer does
    not: ``basis`` (``reconcile`` | ``manual``), ``unscoped``, ``truncated``,
    ``alert_incidents`` (matched alert id → incident id) and ``incidents``
    (host-scoped ``ObservedIncident`` rows, the fallback basis). With no
    context at all (a completion-time score) nothing is measured and the run
    stays ``pending``, exactly as before sprint 2.
    """
    family = kpi_family(primary_kpi, threshold)
    if family in (None, MTTD) or ctx is None:
        return None, None
    results = list(results)
    unscoped = bool(ctx.get("unscoped"))
    truncated = bool(ctx.get("truncated"))
    record: dict[str, Any] = {"family": family, "basis": ctx.get("basis", "reconcile"),
                              "unscoped": unscoped, "truncated": truncated}

    if family == ACCURACY:
        acc = detection_accuracy(results)
        value, observed, seeded = acc
        record.update({"kpi": "Detection Accuracy", "value": value,
                       "observed": observed, "seeded": seeded, "unit": "%",
                       "excluded": dict(acc.excluded)})
        scored, reason = guard_measurement(threshold, value, unscoped=unscoped,
                                           truncated=truncated, matched=observed)
        record["scored"] = scored
        if reason:
            record["withheld"] = reason
        return scored, record

    # correlation — exact basis first (incident ids on the matched alerts),
    # host-scoped incident rows second, nothing third.
    alert_incidents = dict(ctx.get("alert_incidents") or {})
    value, a, k = correlation_rate(alert_incidents)
    if value is not None:
        record.update({"kpi": "Cross-Source Correlation Rate", "value": value,
                       "alerts": a, "incidents": k, "unit": "%",
                       "basis": "alert_incident_ids"})
        matched = a
    else:
        rows = list(ctx.get("incidents") or [])
        value, a, k = correlation_rate_from_incidents(rows)
        record.update({"kpi": "Cross-Source Correlation Rate", "value": value,
                       "alerts": a, "incidents": k, "unit": "%",
                       "basis": "host_incidents" if rows else record["basis"]})
        truncated = truncated or bool(ctx.get("incidents_truncated"))
        record["truncated"] = truncated
        matched = a
        if value is None:
            record["scored"] = None
            record["withheld"] = (
                "fewer than two alerts carried an incident id and no host-scoped "
                "incident held two or more alerts — a correlation rate needs at "
                "least two alerts to mean anything")
            return None, record
    scored, reason = guard_measurement(threshold, value, unscoped=unscoped,
                                       truncated=truncated, matched=matched)
    record["scored"] = scored
    if reason:
        record["withheld"] = reason
    return scored, record
