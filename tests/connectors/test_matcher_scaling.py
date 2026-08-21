"""Guards for the reconcile() hot path.

``reconcile`` is the measurement loop's correlation core — it turns a POV's
seeded Result rows into evidence-backed MTTD. It used to re-tokenize the same
strings once per (result, alert) pair, making it O(R x A x len(text)).

These tests pin the two things the optimisation must never trade away:

  1. **Complexity** — tokenization is linear in (results + alerts), not
     quadratic. This fails on the pre-optimisation implementation.
  2. **Verdicts** — the fast path agrees with a naive reference matcher on
     randomised input, including the tie-breaking rule (earliest alert wins;
     ties resolve to the earlier entry in the caller's list).
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from types import SimpleNamespace

from connectors import matcher as m
from connectors.base import ObservedAlert
from connectors.matcher import reconcile

T0 = datetime(2026, 6, 10, 12, 0, 0)


def _result(rid, *, executed_at, technique=None, detection_id=None,
            expected="", observed=False):
    return SimpleNamespace(
        id=rid, executed_at=executed_at, mitre_technique=technique,
        detection_id=detection_id, expected_detection=expected, observed=observed,
    )


def _alert(*, observed_at, techniques=None, detection_id=None, name=None, ext="A1"):
    return ObservedAlert(
        source="test", observed_at=observed_at, external_id=ext,
        name=name, techniques=techniques or [], detection_id=detection_id,
    )


# --------------------------------------------------------------------------
# 1. Complexity guard
# --------------------------------------------------------------------------


def test_tokenization_is_linear_not_quadratic(monkeypatch):
    """Each result description and alert name is tokenized ONCE per reconcile.

    The old implementation called _split_words inside the per-alert loop, so a
    50x50 reconcile cost 2 x 50 x 50 = 5000 calls. Precomputing the token sets
    makes it 50 + 50. Anything super-linear here means the quadratic
    tokenization has crept back in.
    """
    calls = {"n": 0}
    real = m._split_words

    def counting(s):
        calls["n"] += 1
        return real(s)

    monkeypatch.setattr(m, "_split_words", counting)

    n = 50
    results = [
        _result(i, executed_at=T0, technique="T1003",
                expected=f"credential dumping memory access variant {i}")
        for i in range(n)
    ]
    alerts = [
        _alert(observed_at=T0 + timedelta(seconds=i + 1), techniques=["T1003"],
               name=f"credential dumping detected variant {i}", ext=f"A{i}")
        for i in range(n)
    ]

    verdicts = reconcile(results, alerts)
    assert all(v.matched for v in verdicts)  # the work actually happened

    # One tokenization per result + one per alert. Generous ceiling so the test
    # pins the ORDER, not an exact call count.
    assert calls["n"] <= 2 * n + 10, (
        f"tokenized {calls['n']} times for {n} results x {n} alerts — "
        "expected ~linear, quadratic tokenization has regressed"
    )


def test_alerts_are_not_rescanned_per_result(monkeypatch):
    """A result whose window is empty must not walk the whole alert feed.

    Ordering + bisect means each result only touches alerts inside its own
    window. Correlation-key evaluation is the proxy for that walk.
    """
    calls = {"n": 0}
    real = m._keys_for

    def counting(rs, als):
        calls["n"] += 1
        return real(rs, als)

    monkeypatch.setattr(m, "_keys_for", counting)

    # 200 alerts far in the future; 20 results whose 60s windows are all empty.
    results = [_result(i, executed_at=T0, technique="T1003") for i in range(20)]
    alerts = [_alert(observed_at=T0 + timedelta(days=1, seconds=i),
                     techniques=["T1003"], ext=f"A{i}") for i in range(200)]

    verdicts = reconcile(results, alerts, window_seconds=60)
    assert not any(v.matched for v in verdicts)
    # Old code: 20 x 200 = 4000 window checks. New code: zero key evaluations,
    # because bisect lands past the window for every result.
    assert calls["n"] == 0, f"evaluated {calls['n']} correlation keys for an empty window"


# --------------------------------------------------------------------------
# 2. Differential correctness vs a naive reference
# --------------------------------------------------------------------------


def _reference_reconcile(results, observations, *, window_seconds=3600,
                         only_unobserved=True):
    """Deliberately naive O(R x A) matcher — the shape of the original code.

    Kept independent of the optimised implementation (it calls only the public
    _correlation_keys helper) so it can serve as an oracle.
    """
    out = []
    window = timedelta(seconds=window_seconds)
    for result in results:
        executed_at = getattr(result, "executed_at", None)
        rid = getattr(result, "id", None)
        if executed_at is None or rid is None:
            continue
        if only_unobserved and getattr(result, "observed", False):
            continue
        best = None
        for alert in observations:
            if alert.observed_at is None:
                continue
            delta = (alert.observed_at - executed_at).total_seconds()
            if delta < 0 or delta > window.total_seconds():
                continue
            keys = m._correlation_keys(result, alert)
            if not keys:
                continue
            if best is None or alert.observed_at < best[0]:
                best = (alert.observed_at, keys, alert)
        if best is None:
            out.append((rid, False, None, None, [], None))
        else:
            observed_at, keys, alert = best
            out.append((rid, True, observed_at,
                        round((observed_at - executed_at).total_seconds(), 1),
                        keys, alert.external_id))
    return out


def _as_tuples(verdicts):
    return [(v.result_id, v.matched, v.observed_at, v.mttd_seconds,
             v.matched_on, v.alert_external_id) for v in verdicts]


WORDS = ["lsass", "memory", "dump", "credential", "access", "process", "kerberos",
         "lateral", "movement", "beacon", "tunnel", "exfil", "container",
         "escape", "the", "and", "alert", "cortex", "xdr", "abc"]
TECHS = ["T1003", "T1003.001", "T1003.002", "T1021", "T1021.002", "T1071", ""]


def test_matches_naive_reference_on_randomised_corpus():
    """Fuzz the optimised matcher against the naive oracle.

    Covers ties on observed_at, alerts before/after the window, missing
    techniques, substring detection_id matches and name-overlap edges.
    """
    for seed in range(25):
        rnd = random.Random(seed)
        results = [
            SimpleNamespace(
                id=i + 1,
                executed_at=T0 + timedelta(seconds=rnd.randint(0, 300)),
                observed=rnd.random() < 0.15,
                mitre_technique=rnd.choice(TECHS) or None,
                detection_id=rnd.choice(["bioc-001", "bioc-0011", "", None]),
                expected_detection=" ".join(rnd.sample(WORDS, rnd.randint(2, 7))),
            )
            for i in range(30)
        ]
        alerts = [
            ObservedAlert(
                source="t", external_id=f"A{i}",
                # Coarse timestamps deliberately force ties on observed_at.
                observed_at=T0 + timedelta(seconds=rnd.randint(-60, 900) // 30 * 30),
                name=(" ".join(rnd.sample(WORDS, rnd.randint(1, 5)))
                      if rnd.random() < 0.85 else None),
                techniques=[rnd.choice(TECHS) for _ in range(rnd.randint(0, 3))],
                detection_id=rnd.choice(["bioc-001", "bioc-0011", "", None]),
            )
            for i in range(40)
        ]

        for kwargs in ({}, {"only_unobserved": False}, {"window_seconds": 120}):
            assert _as_tuples(reconcile(results, alerts, **kwargs)) == \
                _reference_reconcile(results, alerts, **kwargs), \
                f"seed={seed} kwargs={kwargs}"


def test_tie_on_observed_at_resolves_to_first_in_input_order():
    """Two equally-early qualifying alerts → the caller's first one wins.

    The pre-optimisation scan kept the first because it compared strictly
    (`<`). The sort must therefore be stable; an unstable sort would silently
    change which alert id is cited as evidence in a customer report.
    """
    ts = T0 + timedelta(seconds=30)
    results = [_result(1, executed_at=T0, technique="T1003")]
    alerts = [
        _alert(observed_at=ts, techniques=["T1003"], ext="first"),
        _alert(observed_at=ts, techniques=["T1003"], ext="second"),
    ]
    [v] = reconcile(results, alerts)
    assert v.alert_external_id == "first"

    # ...and the same holds when a strictly-later alert is listed ahead of them.
    alerts2 = [
        _alert(observed_at=ts + timedelta(seconds=5), techniques=["T1003"], ext="later"),
        _alert(observed_at=ts, techniques=["T1003"], ext="first"),
        _alert(observed_at=ts, techniques=["T1003"], ext="second"),
    ]
    [v2] = reconcile(results, alerts2)
    assert v2.alert_external_id == "first"


def test_window_boundaries_are_inclusive_both_ends():
    """delta == 0 and delta == window both still match (>= / <=, not > / <)."""
    results = [_result(1, executed_at=T0, technique="T1003")]
    [v0] = reconcile(results, [_alert(observed_at=T0, techniques=["T1003"])],
                     window_seconds=60)
    assert v0.matched and v0.mttd_seconds == 0.0

    edge = T0 + timedelta(seconds=60)
    [v1] = reconcile(results, [_alert(observed_at=edge, techniques=["T1003"])],
                     window_seconds=60)
    assert v1.matched and v1.mttd_seconds == 60.0

    past = T0 + timedelta(seconds=61)
    [v2] = reconcile(results, [_alert(observed_at=past, techniques=["T1003"])],
                     window_seconds=60)
    assert not v2.matched


def test_alerts_without_timestamps_are_ignored_not_sorted():
    """A feed carrying untimed alerts must not break the ordering step."""
    results = [_result(1, executed_at=T0, technique="T1003")]
    alerts = [
        ObservedAlert(source="t", observed_at=None, external_id="untimed",
                      techniques=["T1003"]),
        _alert(observed_at=T0 + timedelta(seconds=5), techniques=["T1003"], ext="timed"),
    ]
    [v] = reconcile(results, alerts)
    assert v.matched and v.alert_external_id == "timed"


def test_no_candidates_short_circuits_before_touching_alerts():
    """Nothing to reconcile ⇒ no alert-side work at all."""
    alerts = [_alert(observed_at=T0, techniques=["T1003"])]
    assert reconcile([], alerts) == []
    # Every result already observed → also no candidates.
    observed_only = [_result(1, executed_at=T0, technique="T1003", observed=True)]
    assert reconcile(observed_only, alerts) == []


def test_evidence_tokens_drop_stopwords_and_short_tokens():
    """The token set backing name-overlap keeps its filtering rules."""
    toks = m._evidence_tokens("The Cortex XDR alert for LSASS memory dump")
    assert "lsass" in toks and "memory" in toks and "dump" in toks
    for noise in ("the", "cortex", "xdr", "alert", "for"):
        assert noise not in toks


def test_name_overlap_still_requires_two_meaningful_tokens():
    """One shared token is not evidence — that would over-credit coverage."""
    results = [_result(1, executed_at=T0, expected="credential dumping")]
    one_token = [_alert(observed_at=T0 + timedelta(seconds=1),
                        name="credential stuffing burst")]
    [v] = reconcile(results, one_token)
    assert not v.matched

    two_tokens = [_alert(observed_at=T0 + timedelta(seconds=1),
                         name="credential dumping observed")]
    [v2] = reconcile(results, two_tokens)
    assert v2.matched and v2.matched_on == ["name"]
