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
  Giving up is the sweep's attempt cap, and it records ``pending``;
* a **verify-only XQL row** (:func:`is_verify_only`) raises no alert, so while
  it is unmatched it is not a Detection Accuracy miss; with no alert-backed
  row left there is no accuracy at all, never a PASS.
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


#: Card kinds that are queries, not alert-raising objects. A saved XQL query
#: (``xql_queries[]``) and an XDM modeling rule (``modeling_rules[]``) raise no
#: alert in XSIAM by themselves; a BIOC, correlation rule, IOC, ABIOC,
#: analytics module or prevention module does.
QUERY_CARD_KINDS = frozenset({"xql", "modeling"})

ALL_VERIFY_ONLY_REASON = (
    "no alert-backed row to measure: every Detection Accuracy row that ran is a "
    "verify-only XQL row (a query raises no alert), so the ratio would be matched "
    "over matched. These rows are read through the Tier-2 verify verdict")


def is_verify_only(result: Any) -> bool:
    """True for a Tier-2-verify-only row: evidence that a step ran, not a detection.

    The rule (issue #272): the row's declared detection type (``signal_type``)
    is ``XQL``, and no alert-raising card object backs it. Its card object is
    ``Result.detection_kind`` when the row carries one, else the catalog entry
    for ``ttp_ref`` + ``detection_id`` (EAL-seeded rows carry no kind). It is
    verify-only when that kind is in :data:`QUERY_CARD_KINDS` or there is no
    card at all; an XQL-typed row whose card is a correlation rule or a BIOC is
    alert-backed.

    Such a row is credited only when a correlation alert sharing its technique
    lands in its window (the matcher reads XQL as the correlation family), so on
    a healthy tenant it is written ``missed``. Its own verdict is Tier 2's
    (``POST /api/runs/{id}/verify`` runs its ``verification_xql``).
    ``verification_xql`` is not part of the rule: most XQL rows in the corpus do
    not carry one, and those rows raise no alert either.
    """
    if str(getattr(result, "signal_type", None) or "").strip().upper() != "XQL":
        return False
    kind = getattr(result, "detection_kind", None)
    if kind is None:
        from engine.ttp_catalog import catalog  # noqa: PLC0415
        card = catalog.find(getattr(result, "ttp_ref", None),
                            getattr(result, "detection_id", None))
        kind = card.kind if card is not None else None
    return kind is None or kind in QUERY_CARD_KINDS


def empty_verify_only() -> dict[str, int]:
    return {"excluded": 0, "credited": 0, "no_verification_xql": 0}


class AccuracyMeasure(tuple):
    """``(percent, observed, seeded)`` that also carries ``.excluded``.

    A 3-tuple so every caller that unpacks it keeps working; ``excluded`` is
    the count of rows taken out of BOTH numerator and denominator, by reason.
    ``verify_only`` counts the verify-only rows (:func:`is_verify_only`):
    ``excluded`` left both sides unmatched, ``credited`` were matched and
    counted on both sides, ``no_verification_xql`` is how many of the excluded
    carry no query Tier 2 could run.
    """

    excluded: dict[str, int]
    verify_only: dict[str, int]

    def __new__(cls, value: Optional[float], observed: int, seeded: int,
                excluded: dict[str, int], verify_only: dict[str, int]) -> "AccuracyMeasure":
        t = super().__new__(cls, (value, observed, seeded))
        t.excluded = excluded
        t.verify_only = verify_only
        return t


def detection_accuracy(results: Iterable[Any]) -> AccuracyMeasure:
    """``(percent, observed, seeded)`` over the results that actually executed.

    Rows whose outcome is ``not_reached``/``not_executed``, or which are
    soak-pending, leave numerator and denominator; ``.excluded`` counts them.

    A verify-only XQL row (:func:`is_verify_only`) leaves both sides while it
    is unmatched. Once an alert matched it, it is detected and counts once, in
    the numerator and the denominator, as it did before issue #272: it is never
    in one without the other, so the value cannot exceed 100 %.

    None (not 0.0) when no alert-backed row remains: an empty run has no
    accuracy, and with only verify-only rows a matched row would be its own
    denominator, 100 % by construction.
    """
    excluded = empty_exclusions()
    verify_only = empty_verify_only()
    seeded = []
    alert_backed = 0
    for r in results:
        if getattr(r, "executed_at", None) is None:
            continue
        why = denominator_exclusion(r)
        if why is not None:
            excluded[why] += 1
            continue
        # The one predicate (R1): a ``pending`` row is not a hit even when the
        # reconcile set ``observed`` (an alert with no host witness).
        hit = is_hit(getattr(r, "outcome", None), getattr(r, "observed", False))
        if not is_verify_only(r):
            alert_backed += 1
        elif hit:
            verify_only["credited"] += 1
        else:
            verify_only["excluded"] += 1
            if not getattr(r, "verification_xql", None):
                verify_only["no_verification_xql"] += 1
            continue
        seeded.append(hit)
    if not alert_backed:
        return AccuracyMeasure(None, 0, 0, excluded, verify_only)
    observed = sum(seeded)
    return AccuracyMeasure(round(observed / len(seeded) * 100.0, 1), observed,
                           len(seeded), excluded, verify_only)


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


SEALED_SPLIT_REASON = ("fail withheld: a case was sealed by the response layer before "
                       "the chain finished — the split is not the detection engine's")
SEALED_SPLIT_NOT_COVERED = (
    "the case ledger reads SPLIT_SEALED but did not classify every alert this KPI "
    "counted (in the same case), so the FAIL is not withheld")


def ledger_covers(alert_incidents: dict[str, Optional[str]], ledger_alerts: Any) -> bool:
    """Did the case ledger classify every cased alert the correlation KPI counted?

    ``alert_incidents`` is the KPI's alert id -> incident id; ``ledger_alerts``
    is the ledger's alert id -> case id for every alert it classified. Covered
    means each KPI alert that carries an incident id is in the ledger, in the
    same case. A subset is enough: the ledger is rebuilt from every alert it has
    seen, so it may hold more than this reconcile counted, and when every
    boundary is a seal the cases are time-disjoint, so any subset of its alerts
    crosses only seals.

    Anything that is not a mapping is "not covered": the FAIL stands. Raising
    here would leave the verdict pending, the wrong direction for a guard whose
    only effect is to withhold a FAIL.
    """
    if not isinstance(ledger_alerts, dict):
        return False
    cased = {str(k): str(v) for k, v in alert_incidents.items() if v}
    if not cased:
        return False
    return all(ledger_alerts.get(aid) is not None and str(ledger_alerts[aid]) == cid
               for aid, cid in cased.items())


def guard_measurement(
    threshold: Any, value: Optional[float], *,
    unscoped: bool, truncated: bool, matched: Optional[int] = None,
    sealed_split: bool = False,
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
    if provisional == FAIL and sealed_split:
        return None, SEALED_SPLIT_REASON
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
                       "excluded": dict(acc.excluded),
                       "verify_only": dict(acc.verify_only)})
        scored, reason = guard_measurement(threshold, value, unscoped=unscoped,
                                           truncated=truncated, matched=observed)
        if value is None and (acc.verify_only["excluded"] or acc.verify_only["credited"]):
            reason = ALL_VERIFY_ONLY_REASON
        record["scored"] = scored
        if reason:
            record["withheld"] = reason
        return scored, record

    # correlation — exact basis first (incident ids on the matched alerts),
    # host-scoped incident rows second, nothing third.
    alert_incidents = dict(ctx.get("alert_incidents") or {})
    value, a, k = correlation_rate(alert_incidents)
    sealed_split = False
    if value is not None:
        record.update({"kpi": "Cross-Source Correlation Rate", "value": value,
                       "alerts": a, "incidents": k, "unit": "%",
                       "basis": "alert_incident_ids"})
        matched = a
        if ctx.get("merge_class") == "SPLIT_SEALED":
            sealed_split = ledger_covers(alert_incidents, ctx.get("case_ledger_alerts"))
            if not sealed_split:
                record["sealed_split_not_applied"] = SEALED_SPLIT_NOT_COVERED
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
    # A split the SOC Framework made (a case sealed before the chain finished)
    # is not the detection engine's: only this family reads merge_class, and
    # only on the alert_incident_ids basis, where the ledger and the KPI count
    # the same alerts. The host_incidents fallback never withholds.
    scored, reason = guard_measurement(threshold, value, unscoped=unscoped,
                                       truncated=truncated, matched=matched,
                                       sealed_split=sealed_split)
    record["scored"] = scored
    if reason:
        record["withheld"] = reason
    return scored, record
