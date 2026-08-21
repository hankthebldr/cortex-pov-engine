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

from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Optional

from .base import ObservedAlert

DEFAULT_WINDOW_SECONDS = 3600

# Tokens too generic to carry evidence — a shared "alert" or "cortex" must
# never be one of the two overlapping words that credits a detection.
_STOPWORDS = frozenset({
    "the", "and", "for", "from", "with", "that", "this", "via", "into",
    "alert", "detection", "cortex", "xsiam", "xdr",
})


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


def _evidence_tokens(s: str) -> set[str]:
    """The stopword-filtered, length->=4 token set used for name overlap.

    Hoisted out of :func:`_name_overlap` because it is loop-invariant: the same
    result description used to be re-tokenized once per candidate alert, making
    reconcile O(results x alerts x len(text)). ``reconcile`` now computes this
    once per string.
    """
    return {t for t in _split_words(s) if len(t) >= 4 and t not in _STOPWORDS}


def _name_overlap(result_desc: str, alert_name: str) -> bool:
    """True when the alert name shares a meaningful multi-word span with the
    expected-detection description. Requires >=2 shared tokens of length >=4 to
    avoid spurious single-word matches."""
    return len(_evidence_tokens(result_desc) & _evidence_tokens(alert_name)) >= 2


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


@dataclass(frozen=True)
class _ResultSide:
    """Loop-invariant projection of a Result's identity dimensions."""

    technique: str          # normalized, "" when absent
    technique_base: str     # technique with the sub-id stripped
    detection_id: str       # normalized, "" when absent
    tokens: set[str]        # evidence tokens of expected_detection


@dataclass(frozen=True)
class _AlertSide:
    """Loop-invariant projection of an ObservedAlert's identity dimensions."""

    techniques: set[str]        # normalized
    technique_bases: set[str]   # sub-id stripped, empties dropped
    detection_id: str           # normalized, "" when absent
    has_name: bool
    tokens: set[str]            # evidence tokens of the alert name


def _prepare_result(result: Any) -> _ResultSide:
    technique = _norm(getattr(result, "mitre_technique", None))
    return _ResultSide(
        technique=technique,
        technique_base=technique.split(".")[0] if technique else "",
        detection_id=_norm(getattr(result, "detection_id", None)),
        tokens=_evidence_tokens(getattr(result, "expected_detection", "") or ""),
    )


def _prepare_alert(alert: ObservedAlert) -> _AlertSide:
    techniques = {_norm(t) for t in alert.techniques}
    return _AlertSide(
        techniques=techniques,
        technique_bases={t.split(".")[0] for t in techniques if t},
        detection_id=_norm(alert.detection_id),
        has_name=bool(alert.name),
        tokens=_evidence_tokens(alert.name) if alert.name else set(),
    )


def _keys_for(rs: _ResultSide, als: _AlertSide) -> list[str]:
    """The identity dimensions on which a prepared result and alert agree.

    Single source of truth for the correlation rules — :func:`_correlation_keys`
    is a thin adapter over it so the object and prepared paths can never drift.
    """
    keys: list[str] = []

    # MITRE technique (exact, or base technique without sub-id).
    if rs.technique:
        if rs.technique in als.techniques:
            keys.append("technique")
        elif rs.technique_base in als.technique_bases:
            keys.append("technique-base")

    # Source rule / detection id.
    r_det, a_det = rs.detection_id, als.detection_id
    if r_det and a_det and (r_det == a_det or r_det in a_det or a_det in r_det):
        keys.append("detection_id")

    # Name/description overlap.
    if als.has_name and len(rs.tokens & als.tokens) >= 2:
        keys.append("name")

    return keys


def _correlation_keys(result: Any, alert: ObservedAlert) -> list[str]:
    """Return the list of identity dimensions on which ``result`` and ``alert``
    agree. Empty list ⇒ no identity match (time alone is insufficient)."""
    return _keys_for(_prepare_result(result), _prepare_alert(alert))


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
    window = timedelta(seconds=window_seconds)

    # Candidates first, so a caller with nothing to reconcile does no alert
    # work at all (and cannot trip over a malformed observation list).
    candidates: list[tuple[int, Any, _ResultSide]] = []
    for result in results:
        executed_at = getattr(result, "executed_at", None)
        rid = getattr(result, "id", None)
        if executed_at is None or rid is None:
            continue
        if only_unobserved and getattr(result, "observed", False):
            continue
        candidates.append((rid, executed_at, _prepare_result(result)))

    if not candidates:
        return []

    # Tokenize each alert ONCE, then order by fire time. The sort is stable, so
    # alerts sharing a timestamp keep their original relative order — which is
    # exactly the alert the old "strictly-earlier wins" scan would have kept.
    # Ordering also lets each result bisect straight to its window instead of
    # rescanning the whole feed.
    timed = [a for a in observations if a.observed_at is not None]
    timed.sort(key=lambda a: a.observed_at)
    alert_times = [a.observed_at for a in timed]
    prepared = [_prepare_alert(a) for a in timed]

    verdicts: list[MatchVerdict] = []
    for rid, executed_at, rs in candidates:
        # Window is [executed_at, executed_at + window]; anything earlier can't
        # be evidence and anything later is out of scope.
        upper = executed_at + window
        idx = bisect_left(alert_times, executed_at)

        verdict = MatchVerdict(result_id=rid, matched=False)
        while idx < len(timed):
            observed_at = alert_times[idx]
            if observed_at > upper:
                break
            keys = _keys_for(rs, prepared[idx])
            if keys:
                alert = timed[idx]
                verdict = MatchVerdict(
                    result_id=rid,
                    matched=True,
                    observed_at=observed_at,
                    mttd_seconds=round((observed_at - executed_at).total_seconds(), 1),
                    matched_on=keys,
                    alert_external_id=alert.external_id,
                    alert_name=alert.name,
                )
                break  # ascending order ⇒ first hit is the earliest (lowest MTTD)
            idx += 1

        verdicts.append(verdict)

    return verdicts
