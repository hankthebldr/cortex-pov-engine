"""Correlate observed external alerts to a run's seeded Result rows.

This is the heart of the measurement loop: given the ``Result`` rows CortexSim
seeded for a run (each carrying ``executed_at``, ``mitre_technique``,
``detection_id``, ``plane``) and a list of :class:`ObservedAlert` pulled from an
external system, decide which expected detections were actually observed and
when — yielding a real, evidence-backed MTTD instead of a human checkbox.

Matching is deliberately conservative and explainable. An observation matches a
Result when ALL of:

  1. **Time**: the alert fired at or after the step executed, within a window
     (default 1h). An alert before execution can't be evidence for it.
  2. **Identity**: at least one correlation key lines up — the MITRE technique
     id, OR the source rule/detection id, OR a strong name overlap. A bare
     time-only match is never accepted (it would over-credit coverage).

Each Result takes its **earliest** qualifying observation (lowest MTTD). The
matcher is pure (no DB/IO) so it is exhaustively unit-tested; the API layer
applies the verdicts to the ORM and emits SSE.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Optional

from .base import ObservedAlert

DEFAULT_WINDOW_SECONDS = 3600


@dataclass
class MatchVerdict:
    """One Result's reconciliation outcome."""

    result_id: int
    matched: bool
    observed_at: Optional[Any] = None       # datetime when matched
    mttd_seconds: Optional[float] = None
    matched_on: list[str] = field(default_factory=list)   # which keys lined up
    alert_external_id: Optional[str] = None
    alert_name: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id": self.result_id,
            "matched": self.matched,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "mttd_seconds": self.mttd_seconds,
            "matched_on": list(self.matched_on),
            "alert_external_id": self.alert_external_id,
            "alert_name": self.alert_name,
        }


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def _name_overlap(result_desc: str, alert_name: str) -> bool:
    """True when the alert name shares a meaningful multi-word span with the
    expected-detection description. Requires >=2 shared tokens of length >=4 to
    avoid spurious single-word matches."""
    stop = {"the", "and", "for", "from", "with", "that", "this", "via", "into",
            "alert", "detection", "cortex", "xsiam", "xdr"}
    rt = {t for t in _split_words(result_desc) if len(t) >= 4 and t not in stop}
    at = {t for t in _split_words(alert_name) if len(t) >= 4 and t not in stop}
    return len(rt & at) >= 2


def _split_words(s: str) -> list[str]:
    out, buf = [], []
    for ch in (s or "").lower():
        if ch.isalnum():
            buf.append(ch)
        elif buf:
            out.append("".join(buf))
            buf = []
    if buf:
        out.append("".join(buf))
    return out


def _correlation_keys(result: Any, alert: ObservedAlert) -> list[str]:
    """Return the list of identity dimensions on which ``result`` and ``alert``
    agree. Empty list ⇒ no identity match (time alone is insufficient)."""
    keys: list[str] = []

    # MITRE technique (exact, or base technique without sub-id).
    r_tech = _norm(getattr(result, "mitre_technique", None))
    if r_tech:
        a_techs = {_norm(t) for t in alert.techniques}
        if r_tech in a_techs:
            keys.append("technique")
        else:
            r_base = r_tech.split(".")[0]
            if any(t.split(".")[0] == r_base for t in a_techs if t):
                keys.append("technique-base")

    # Source rule / detection id.
    r_det = _norm(getattr(result, "detection_id", None))
    a_det = _norm(alert.detection_id)
    if r_det and a_det and (r_det == a_det or r_det in a_det or a_det in r_det):
        keys.append("detection_id")

    # Name/description overlap.
    if alert.name and _name_overlap(getattr(result, "expected_detection", "") or "", alert.name):
        keys.append("name")

    return keys


def reconcile(
    results: list[Any],
    observations: list[ObservedAlert],
    *,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
    only_unobserved: bool = True,
) -> list[MatchVerdict]:
    """Match observations to results and return a verdict per *candidate* result.

    Only results with an ``executed_at`` are candidates (MTTD needs a start). By
    default already-observed results are skipped (``only_unobserved``); pass
    False to re-evaluate every result. Each verdict names the earliest matching
    alert and the keys it matched on.
    """
    verdicts: list[MatchVerdict] = []
    window = timedelta(seconds=window_seconds)

    for result in results:
        executed_at = getattr(result, "executed_at", None)
        rid = getattr(result, "id", None)
        if executed_at is None or rid is None:
            continue
        if only_unobserved and getattr(result, "observed", False):
            continue

        best: Optional[tuple[Any, list[str], ObservedAlert]] = None
        for alert in observations:
            if alert.observed_at is None:
                continue
            # 1. Time window: alert at/after execution, within window.
            delta = (alert.observed_at - executed_at).total_seconds()
            if delta < 0 or delta > window.total_seconds():
                continue
            # 2. Identity correlation.
            keys = _correlation_keys(result, alert)
            if not keys:
                continue
            if best is None or alert.observed_at < best[0]:
                best = (alert.observed_at, keys, alert)

        if best is None:
            verdicts.append(MatchVerdict(result_id=rid, matched=False))
        else:
            observed_at, keys, alert = best
            mttd = (observed_at - executed_at).total_seconds()
            verdicts.append(MatchVerdict(
                result_id=rid,
                matched=True,
                observed_at=observed_at,
                mttd_seconds=round(mttd, 1),
                matched_on=keys,
                alert_external_id=alert.external_id,
                alert_name=alert.name,
            ))

    return verdicts
