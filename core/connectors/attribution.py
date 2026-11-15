"""Which run an alert belongs to (#347).

Reconcile pulls a HOST's alerts, and a seeded row's match window (one hour, or
the Analytics / ABIOC window) outlasts its own run. On a host that runs
scenarios back to back, a row's window therefore also holds the alerts later
runs raised there, and a technique key credited the row with one of them: a
false ``detected`` and a false MTTD.

The rule, as one pure function of the runs on the host:

* A run's CLAIM on an alert is the best identity key one of its rows has with
  it while the alert is inside that row's window. ``strong`` is the rule name,
  the detection id, or the run's own id in the alert record; ``weak`` is a
  technique or shared name words.
* A weak claim holds only while the run is the one on the host. It lapses
  once a later run has taken the host over (``LATER_RUN_STARTED``), and it has
  not started while the run's task is still queued behind a run that is
  executing (``RUN_NOT_BEGUN``).
* A strong claim outlasts the run: an Analytics alert that lags behind the
  run and names the run's rule is still the run's own. Two strong claims go
  to the run that was on the host when the alert fired.
* An alert goes to at most one run. One that no single run can be shown to
  own is UNATTRIBUTED (``AMBIGUOUS_RUNS``): it credits nothing.

The same timeline answers #346's other question: which alerts did the run
raise that it does NOT expect (:meth:`Attribution.additional`)? Those are
listed on the run as additional detections and credit no row.

Every run's reconcile evaluates this same function over the same runs, so two
runs can never both take one alert, whichever is reconciled first.

Pure: no DB, no I/O. ``connectors.service.run_attribution`` builds the
:class:`HostRun` s from ``Run.started_at`` / ``delivered_at`` /
``completed_at`` and the host records.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Mapping, Optional, Sequence

from .base import ObservedAlert
from .matcher import (
    DEFAULT_WINDOW_SECONDS,
    _keys_for,
    _prepare_alert,
    _prepare_result,
    _short_host,
)

#: The run's own id appears in the alert's record (a command line, a tag).
RUN_MARKER = "run_marker"

#: The keys that tie an alert to ONE run whatever the clock says.
STRONG_KEYS = frozenset({"rule_name", "detection_id", RUN_MARKER})

#: A stored credit whose identity keys can no longer be recomputed from the
#: record kept with it. Weighed as a weak claim: only the clock can refuse it.
STORED_KEY = "stored"

# ── why a run may not take an alert ─────────────────────────────────────────
#: A later run had begun on the host before the alert fired, and nothing but a
#: technique or name ties the alert to this run.
LATER_RUN_STARTED = "LATER_RUN_STARTED"
#: The alert fired before this run began on the host, while another run was
#: executing there (this run's task was still queued).
RUN_NOT_BEGUN = "RUN_NOT_BEGUN"
#: Another run holds the rule name, the detection id or the run marker.
TIED_TO_ANOTHER_RUN = "TIED_TO_ANOTHER_RUN"
#: Rows of more than one run match and nothing singles one out. Unattributed.
AMBIGUOUS_RUNS = "AMBIGUOUS_RUNS"

_STRONG, _WEAK = 2, 1

#: A run id shorter than this is not looked for in alert records: a short test
#: id ("r1") would be found in any record by accident. Run ids are UUIDs.
_MIN_MARKER_LENGTH = 16


@dataclass
class HostRun:
    """One run's place on its host's timeline, with the rows it expects."""

    run_id: str
    #: When the run began on the host: a pull run's ``delivered_at`` (the
    #: beacon collected its task), else the launch.
    began_at: datetime
    #: ``Run.completed_at``; None while the run is in flight.
    ended_at: Optional[datetime] = None
    #: ``Run.started_at``, the launch its rows' ``executed_at`` carries. None
    #: reads as ``began_at``.
    launched_at: Optional[datetime] = None
    #: Short host name (first DNS label, case-folded); None when unknown.
    host: Optional[str] = None
    scenario_id: Optional[str] = None
    #: The run's seeded rows (``Result`` rows or anything with the same
    #: identity attributes and ``executed_at``).
    rows: Sequence[Any] = ()
    #: ``{result_id: window end}`` for the run under review -- the matcher's
    #: own upper bounds. A sibling's rows end ``window`` after its run ended.
    row_ends: Mapping[Any, datetime] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.host = _short_host(self.host) or None
        if self.launched_at is None or self.launched_at > self.began_at:
            self.launched_at = self.began_at


@dataclass(frozen=True)
class Decision:
    """May the run under review credit one of its rows with this alert?"""

    allowed: bool
    keys: list[str]
    reason: Optional[str] = None
    #: The run the alert belongs to instead, when one can be named.
    run_id: Optional[str] = None
    #: Every run whose rows match it, when nothing singles one out.
    runs: tuple[str, ...] = ()


class Attribution:
    """The run under review, the other runs near it, and the one rule."""

    def __init__(
        self,
        run: HostRun,
        others: Sequence[HostRun] = (),
        *,
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
        window_seconds_by_type: Optional[Mapping[str, int]] = None,
    ) -> None:
        self.run = run
        self.others = [o for o in others if o.run_id != run.run_id]
        self._window = int(window_seconds)
        self._by_type = dict(window_seconds_by_type or {})
        self._all = [run, *self.others]
        self._by_id = {r.run_id: r for r in self._all}
        self._by_host: dict[str, list[HostRun]] = {}
        for r in self._all:
            if r.host:
                self._by_host.setdefault(r.host, []).append(r)
        self._rows: dict[str, list[tuple[Any, datetime, datetime]]] = {}
        self._held: dict[str, datetime] = {}
        # Per-alert memos are keyed by id(); ``_seen`` keeps every alert alive
        # for as long as they are, so an id is never reused under them.
        self._seen: dict[int, ObservedAlert] = {}
        self._claims: dict[tuple[str, int], int] = {}
        self._raw_text: dict[int, str] = {}

    # ── the timeline ────────────────────────────────────────────────────────

    def held_from(self, run: HostRun) -> datetime:
        """When ``run`` took over its host.

        Its launch, the instant its rows' windows open, unless another run was
        still executing there then: a run queued behind one takes over only
        when the beacon collects its task. Launch and not delivery in the
        plain case because the alert's clock is not SimCore's: an alert a
        second ahead of ``delivered_at`` is still this run's.
        """
        held = self._held.get(run.run_id)
        if held is None:
            held = run.launched_at
            for other in self._by_host.get(run.host or "", ()):
                if other is run or other.began_at >= run.began_at:
                    continue
                if other.ended_at is None or other.ended_at > run.launched_at:
                    held = run.began_at
                    break
            self._held[run.run_id] = held
        return held

    def displaced_by(self, run: HostRun, when: datetime) -> Optional[HostRun]:
        """The run that held ``run``'s host at ``when`` in its place, or None.

        A later run that had taken the host over by ``when``; or, when ``run``
        had not taken it over yet, a run that was executing there.
        """
        if not run.host:
            return None
        mine = self.held_from(run)
        found: Optional[HostRun] = None
        for other in self._by_host.get(run.host, ()):
            if other is run:
                continue
            theirs = self.held_from(other)
            if theirs > when:
                continue
            later = theirs > mine
            queued_behind = mine > when and (
                other.ended_at is None or other.ended_at >= when)
            if (later or queued_behind) and (
                    found is None or theirs > self.held_from(found)):
                found = other
        return found

    # ── claims ──────────────────────────────────────────────────────────────

    def _prepared_rows(self, run: HostRun) -> list[tuple[Any, datetime, datetime]]:
        rows = self._rows.get(run.run_id)
        if rows is None:
            rows = []
            for row in run.rows:
                executed_at = getattr(row, "executed_at", None)
                if executed_at is None:
                    continue
                end = run.row_ends.get(getattr(row, "id", None))
                if end is None:
                    window = self._by_type.get(getattr(row, "signal_type", None), self._window)
                    last = run.ended_at if run.ended_at and run.ended_at > executed_at \
                        else executed_at
                    end = last + timedelta(seconds=window)
                rows.append((_prepare_result(row), executed_at, end))
            self._rows[run.run_id] = rows
        return rows

    def _claim(self, run: HostRun, alert: ObservedAlert) -> int:
        """``run``'s best claim on ``alert``: strong, weak or 0 (none)."""
        self._seen[id(alert)] = alert
        key = (run.run_id, id(alert))
        if key not in self._claims:
            best = 0
            side = _prepare_alert(alert)
            when = alert.observed_at
            for prepared, lower, upper in self._prepared_rows(run):
                if not lower <= when <= upper:
                    continue
                keys = _keys_for(prepared, side)
                if not keys:
                    continue
                if STRONG_KEYS.intersection(keys):
                    best = _STRONG
                    break
                best = _WEAK
            self._claims[key] = best
        return self._claims[key]

    def _marked(self, alert: ObservedAlert) -> list[HostRun]:
        """The runs whose own id the alert's record carries."""
        raw = getattr(alert, "raw", None)
        if not raw:
            return []
        self._seen[id(alert)] = alert
        text = self._raw_text.get(id(alert))
        if text is None:
            try:
                text = json.dumps(raw, default=str).lower()
            except (TypeError, ValueError):
                text = ""
            self._raw_text[id(alert)] = text
        return [r for r in self._all
                if len(r.run_id) >= _MIN_MARKER_LENGTH and r.run_id.lower() in text]

    def _contenders(self, alert: ObservedAlert) -> list[HostRun]:
        """The runs that could own ``alert``: this run and the others on its
        host. An alert that names no host could be any host's, so every run
        near it contends."""
        if not getattr(alert, "host", None):
            return list(self._all)
        return [self.run, *(o for o in self.others
                            if o.host and o.host == self.run.host)]

    # ── the rule ────────────────────────────────────────────────────────────

    def decide(self, alert: ObservedAlert, keys: Sequence[str]) -> Decision:
        """May a row of this run that matched ``alert`` on ``keys`` take it?"""
        keys = list(keys)
        when = alert.observed_at
        mine = self.run
        marked = self._marked(alert)
        if len(marked) == 1:
            if marked[0] is not mine:
                return Decision(False, keys, TIED_TO_ANOTHER_RUN, marked[0].run_id)
            if RUN_MARKER not in keys:
                keys.append(RUN_MARKER)
            return Decision(True, keys)

        strong_keys = bool(STRONG_KEYS.intersection(keys))
        claims = {mine.run_id: max(self._claim(mine, alert),
                                   _STRONG if strong_keys else _WEAK)}
        for other in self._contenders(alert)[1:]:
            claim = self._claim(other, alert)
            if claim:
                claims[other.run_id] = claim

        def on_host(run_id: str) -> bool:
            return self.displaced_by(self._by_id[run_id], when) is None

        strong = [rid for rid, c in claims.items() if c == _STRONG]
        if len(strong) > 1:
            current = [rid for rid in strong if on_host(rid)]
            owner = current[0] if len(current) == 1 else None
        elif strong:
            owner = strong[0]
        else:
            current = [rid for rid in claims if on_host(rid)]
            owner = current[0] if len(current) == 1 else None
            if owner is None and not current:
                # Every weak claim has lapsed: the alert belongs, by the
                # clock, to whichever run displaced this one.
                by = self.displaced_by(mine, when)
                return Decision(False, keys, self._lapse_reason(when),
                                by.run_id if by else None)

        if owner is None:
            return Decision(False, keys, AMBIGUOUS_RUNS, None, tuple(sorted(claims)))
        if owner != mine.run_id:
            if claims[owner] == _STRONG:
                return Decision(False, keys, TIED_TO_ANOTHER_RUN, owner)
            return Decision(False, keys, self._lapse_reason(when), owner)
        # This run owns the alert. A row holding only a weak key still needs
        # the run to be the one on the host.
        if not strong_keys:
            by = self.displaced_by(mine, when)
            if by is not None:
                return Decision(False, keys, self._lapse_reason(when), by.run_id)
        return Decision(True, keys)

    def _lapse_reason(self, when: datetime) -> str:
        return RUN_NOT_BEGUN if self.held_from(self.run) > when else LATER_RUN_STARTED

    # ── alerts the run raised that it did not expect (#346) ─────────────────

    def additional(self, alerts: Sequence[ObservedAlert]) -> list[ObservedAlert]:
        """The alerts that are this run's and match none of its expected rows.

        An alert qualifies when it names the run's host, fell inside the run's
        window (its launch to its end plus the default match window) while the
        run held the host, has no identity key with ANY expected row of the
        run (one that has is a credit or a repeat firing of one), and is not
        tied to another run by a rule name, detection id or run marker. An
        alert that names no host is never listed: nothing ties it to this
        endpoint. Time order.

        The settle time is the DEFAULT window for every alert family, not the
        longer Analytics one. A row may wait a day for its Analytics alert
        because an identity key still has to agree; here only the clock ties
        the alert to the run, and a day of an endpoint's unrelated Analytics
        alerts listed under a run would be a false attribution.
        """
        mine = self.run
        if not mine.host:
            return []
        rows = [prepared for prepared, _lower, _upper in self._prepared_rows(mine)]
        last = (mine.ended_at or mine.began_at) + timedelta(seconds=self._window)
        out: list[ObservedAlert] = []
        for alert in alerts:
            when = alert.observed_at
            if when is None or _short_host(getattr(alert, "host", None)) != mine.host:
                continue
            if not mine.launched_at <= when <= last:
                continue
            if self.displaced_by(mine, when) is not None:
                continue
            side = _prepare_alert(alert)
            if any(_keys_for(row, side) for row in rows):
                continue
            marked = self._marked(alert)
            if marked and mine not in marked:
                continue
            if any(self._claim(other, alert) == _STRONG for other in self._contenders(alert)[1:]):
                continue
            out.append(alert)
        return sorted(out, key=lambda a: a.observed_at)

    def known(self, run_id: Optional[str]) -> Optional[HostRun]:
        """The run with this id, when it is this run or one near it."""
        return self._by_id.get(run_id) if run_id else None

    def describe(self, run_id: Optional[str]) -> str:
        """``<short id> (<scenario>)`` for a run this attribution knows."""
        if not run_id:
            return "another run"
        run = self._by_id.get(run_id)
        label = run_id[:8]
        if run is not None and run.scenario_id:
            label += f" ({run.scenario_id})"
        return label
