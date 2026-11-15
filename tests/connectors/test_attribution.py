"""#347: an alert credits a row only inside that row's own run.

Reconcile pulls a host's alerts, and a row's match window (1 h, or the
Analytics / ABIOC window) outlasts its run. On a host that runs scenarios back
to back, SIM-EDR-018's ABIOC row was credited by an alert SIM-MP-003 raised 32
minutes later: a false "detected" and an MTTD of 1,920 s.

These are the rule's own cases, on the pure ``connectors.attribution``:
back-to-back runs, an alert that lags behind its run, an alert carrying an
identity key, an alert between two runs, a run queued behind another, two
hosts, and the matcher's use of it.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from connectors.attribution import (
    AMBIGUOUS_RUNS,
    LATER_RUN_STARTED,
    RUN_MARKER,
    RUN_NOT_BEGUN,
    TIED_TO_ANOTHER_RUN,
    Attribution,
    HostRun,
)
from connectors.base import ObservedAlert
from connectors.matcher import _correlation_keys, reconcile

T0 = datetime(2026, 11, 15, 5, 35, 5)
HOST = "lab-endpoint-01"
ANALYTICS_WINDOW = {"Analytics": 86400, "ABIOC": 86400}


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


class Row:
    """The attributes of a seeded ``Result`` the matcher reads."""

    def __init__(self, rid: int, *, technique: str = "T1059.001", signal_type: str = "ABIOC",
                 executed: float = 0, name: Optional[str] = None,
                 detection_id: Optional[str] = None, expected: str = "expected detection"):
        self.id = rid
        self.mitre_technique = technique
        self.signal_type = signal_type
        self.executed_at = at(executed)
        self.detection_name = name
        self.detection_id = detection_id
        self.expected_detection = expected
        self.observed = False


def run(run_id: str, *, began: float, ended: Optional[float], rows=(), host: Optional[str] = HOST,
        scenario: Optional[str] = None, launched: Optional[float] = None) -> HostRun:
    """``began`` is when the beacon collected the task; ``launched`` (default:
    the same instant) is when the run was created and its rows were seeded."""
    return HostRun(run_id=run_id, began_at=at(began),
                   ended_at=None if ended is None else at(ended),
                   launched_at=None if launched is None else at(launched),
                   host=host, scenario_id=scenario, rows=list(rows))


def alert(seconds: float, *, techniques=("T1059",), source: str = "XDR Analytics BIOC",
          name: str = "A TCP stream was created directly in a shell", ext: str = "A1",
          host: Optional[str] = HOST, detection_id: Optional[str] = None,
          raw: Optional[dict] = None) -> ObservedAlert:
    return ObservedAlert(source="xsiam", observed_at=at(seconds), external_id=ext, name=name,
                         techniques=list(techniques), host=host, alert_source=source,
                         detection_id=detection_id, raw=raw or {})


def attribution(mine: HostRun, *others: HostRun) -> Attribution:
    return Attribution(mine, list(others), window_seconds=3600,
                       window_seconds_by_type=ANALYTICS_WINDOW)


def decide(mine: HostRun, others, row: Row, a: ObservedAlert):
    return attribution(mine, *others).decide(a, _correlation_keys(row, a))


# ── back-to-back runs: the #347 case ────────────────────────────────────────

def test_an_alert_raised_after_a_later_run_began_is_not_the_earlier_runs():
    """SIM-EDR-018 ran 05:35:05-05:37:18. Fifteen more runs followed on the
    host; SIM-MP-003's alert at 06:07:05 shares only the base technique."""
    row = Row(1, technique="T1059.001")
    edr018 = run("edr018", began=2, ended=133, rows=[row], scenario="SIM-EDR-018")
    edr019 = run("edr019", began=135, ended=160, scenario="SIM-EDR-019")
    mp003 = run("mp003", began=1890, ended=1935, scenario="SIM-MP-003")
    a = alert(1920)

    d = decide(edr018, [edr019, mp003], row, a)

    assert not d.allowed
    assert d.reason == LATER_RUN_STARTED
    assert d.run_id == "mp003"  # the run that was on the host when it fired


def test_the_same_alert_is_its_own_runs_to_take():
    row = Row(7, technique="T1059", signal_type="ABIOC", executed=1888)
    edr018 = run("edr018", began=2, ended=133, rows=[Row(1, technique="T1059.001")])
    mp003 = run("mp003", began=1890, ended=1935, rows=[row])

    d = decide(mp003, [edr018], row, alert(1920))

    assert d.allowed and d.reason is None


def test_an_alert_inside_the_run_is_credited_as_before():
    row = Row(1)
    d = decide(run("a", began=2, ended=133, rows=[row]), [], row,
               alert(120, techniques=("T1059.001",)))
    assert d.allowed and "technique" in d.keys


# ── an alert that lags behind its run ───────────────────────────────────────

def test_a_lagging_analytics_alert_is_still_the_runs_while_no_other_run_began():
    """Analytics fires late. Forty minutes after the run ended, with nothing
    else on the host, a technique match is still this run's."""
    row = Row(1)
    d = decide(run("a", began=2, ended=133, rows=[row]), [], row, alert(2400))
    assert d.allowed


def test_a_later_runs_start_caps_the_lag_for_a_technique_match():
    row = Row(1)
    later = run("b", began=600, ended=700)
    assert decide(run("a", began=2, ended=133, rows=[row]), [later], row, alert(599)).allowed
    capped = decide(run("a", began=2, ended=133, rows=[row]), [later], row, alert(600))
    assert not capped.allowed and capped.reason == LATER_RUN_STARTED and capped.run_id == "b"


def test_the_lag_is_bounded_by_the_types_own_window():
    """The allowance is the row's existing window: an alert past it is out of
    the row's reach whatever else is true (the matcher never offers it)."""
    row = Row(1, signal_type="BIOC")
    mine = run("a", began=2, ended=133, rows=[row])
    a = alert(133 + 3601, source="XDR BIOC", techniques=("T1059.001",))
    verdicts = reconcile([row], [a], window_seconds=3600, attribution=attribution(mine))
    assert not verdicts[0].matched


# ── an alert carrying an identity key ───────────────────────────────────────

def test_a_rule_name_ties_a_lagging_alert_to_the_earlier_run():
    """The later run's start does not cap an alert that names this run's rule."""
    row = Row(1, name="Suspicious sshpass command execution", technique="T1110.004")
    a = alert(900, name="Suspicious sshpass command execution", techniques=("T1110.004",))
    later = run("b", began=600, ended=700, rows=[Row(9, technique="T1110.004", executed=598)])

    d = decide(run("a", began=2, ended=133, rows=[row]), [later], row, a)
    assert d.allowed and "rule_name" in d.keys

    # ... and the later run, which matches it only on technique, defers.
    other_row = later.rows[0]
    d2 = decide(later, [run("a", began=2, ended=133, rows=[row])], other_row, a)
    assert not d2.allowed and d2.reason == TIED_TO_ANOTHER_RUN and d2.run_id == "a"


def test_a_detection_id_ties_an_alert_to_its_run():
    row = Row(1, detection_id="bioc-edr-024-ssh-authorized-keys-written", signal_type="BIOC",
              technique="T1098.004")
    a = alert(900, source="XDR BIOC", techniques=("T1098.004",),
              detection_id="bioc-edr-024-ssh-authorized-keys-written")
    later = run("b", began=600, ended=700)
    d = decide(run("a", began=2, ended=133, rows=[row]), [later], row, a)
    assert d.allowed and "detection_id" in d.keys


def test_two_runs_expecting_the_same_rule_the_alert_goes_to_the_one_on_the_host():
    """SIM-EDR-023 and SIM-KOI-012 both expect "Local Analysis Malware". The
    one raised during KOI-012 is KOI-012's, even inside EDR-023's window."""
    first_row = Row(1, name="Local Analysis Malware", signal_type="Prevention", technique="T1204")
    second_row = Row(2, name="Local Analysis Malware", signal_type="Prevention",
                     technique="T1105", executed=1134)
    first = run("edr023", began=2, ended=60, rows=[first_row])
    second = run("koi012", began=1136, ended=1400, rows=[second_row])
    a = alert(1349, name="Local Analysis Malware", source="XDR Agent", techniques=())

    assert decide(second, [first], second_row, a).allowed
    refused = decide(first, [second], first_row, a)
    assert not refused.allowed and refused.reason == TIED_TO_ANOTHER_RUN
    assert refused.run_id == "koi012"


def test_the_runs_own_id_in_the_alert_record_is_a_marker():
    run_id = "0033b4b4-1111-4222-8333-444455556666"
    other_id = "981df947-1111-4222-8333-444455556666"
    row = Row(1)
    mine = run(run_id, began=2, ended=133, rows=[row])
    later = run(other_id, began=600, ended=700, rows=[Row(9, technique="T1059", executed=598)])
    marked = alert(900, raw={"actor_process_command_line": f"sh -c 'tag={run_id}; id'"})

    d = decide(mine, [later], row, marked)
    assert d.allowed and RUN_MARKER in d.keys

    d2 = decide(later, [mine], later.rows[0], marked)
    assert not d2.allowed and d2.reason == TIED_TO_ANOTHER_RUN and d2.run_id == run_id


# ── an alert between two runs ───────────────────────────────────────────────

def test_an_alert_between_two_runs_is_the_earlier_runs_inside_its_window():
    row = Row(1)
    later = run("b", began=900, ended=1000, rows=[Row(9, executed=898)])
    d = decide(run("a", began=2, ended=133, rows=[row]), [later], row, alert(500))
    assert d.allowed


def test_an_alert_between_two_runs_and_past_the_earlier_window_credits_neither():
    """Past the BIOC row's hour and before the next run was launched: the
    matcher offers it to neither run's row."""
    a_row = Row(1, signal_type="BIOC")
    b_row = Row(2, signal_type="BIOC", executed=8000)
    run_a = run("a", began=2, ended=133, rows=[a_row])
    run_b = run("b", began=8002, ended=8100, rows=[b_row])
    between = alert(5000, source="XDR BIOC", techniques=("T1059.001",))

    for mine, other, row in ((run_a, run_b, a_row), (run_b, run_a, b_row)):
        verdicts = reconcile([row], [between], window_seconds=3600,
                             attribution=attribution(mine, other))
        assert not verdicts[0].matched


# ── a run queued behind another ─────────────────────────────────────────────

def test_a_queued_run_is_not_credited_with_the_running_runs_alert():
    """B was launched while A was executing; its rows carry the launch time,
    so A's alerts sit inside their windows. B had not begun on the host."""
    a_row = Row(1)
    b_row = Row(2, executed=30)          # launched at +30 s
    run_a = run("a", began=2, ended=300, rows=[a_row])
    run_b = run("b", launched=30, began=305, ended=400, rows=[b_row])  # delivered at +305 s
    during_a = alert(120, techniques=("T1059.001",))

    refused = decide(run_b, [run_a], b_row, during_a)
    assert not refused.allowed and refused.reason == RUN_NOT_BEGUN and refused.run_id == "a"
    assert decide(run_a, [run_b], a_row, during_a).allowed


def test_an_alert_a_moment_before_delivery_is_still_the_runs():
    """The alert's clock is not SimCore's. The previous run had ended, so an
    alert between this run's launch and its delivery is this run's."""
    row = Row(2, executed=200)
    earlier = run("a", began=2, ended=133, rows=[Row(1)])
    mine = run("b", launched=200, began=206, ended=300, rows=[row])
    a = alert(203, techniques=("T1059.001",))
    assert decide(mine, [earlier], row, a).allowed
    # ... and the run that had ended does not take it as well.
    lapsed = decide(earlier, [mine], earlier.rows[0], a)
    assert not lapsed.allowed and lapsed.reason == LATER_RUN_STARTED and lapsed.run_id == "b"


# ── at most one run ─────────────────────────────────────────────────────────

def test_a_hostless_alert_matching_runs_on_two_hosts_is_unattributed():
    """An NGFW alert names no host. Two runs on two endpoints were executing
    and both expect the technique: it credits neither."""
    a_row, b_row = Row(1, signal_type="XQL"), Row(2, signal_type="XQL")
    run_a = run("a", began=2, ended=300, rows=[a_row], host="endpoint-a")
    run_b = run("b", began=5, ended=300, rows=[b_row], host="endpoint-b")
    hostless = alert(120, host=None, source="PAN NGFW", techniques=("T1059.001",))

    for mine, other, row in ((run_a, run_b, a_row), (run_b, run_a, b_row)):
        d = decide(mine, [other], row, hostless)
        assert not d.allowed and d.reason == AMBIGUOUS_RUNS
        assert d.run_id is None and set(d.runs) == {"a", "b"}


def test_a_hosted_alert_ignores_runs_on_other_hosts():
    row = Row(1)
    elsewhere = run("b", began=5, ended=300, rows=[Row(2)], host="endpoint-b")
    assert decide(run("a", began=2, ended=300, rows=[row]), [elsewhere], row,
                  alert(120, techniques=("T1059.001",))).allowed


def test_two_runs_that_began_at_the_same_instant_credit_neither():
    a_row, b_row = Row(1), Row(2)
    run_a = run("a", began=2, ended=300, rows=[a_row])
    run_b = run("b", began=2, ended=300, rows=[b_row])
    a = alert(120, techniques=("T1059.001",))
    assert decide(run_a, [run_b], a_row, a).reason == AMBIGUOUS_RUNS
    assert decide(run_b, [run_a], b_row, a).reason == AMBIGUOUS_RUNS


def test_whichever_run_is_reconciled_first_the_alert_has_one_owner():
    """The rule is a function of the runs, not of what was credited so far."""
    rows = {name: Row(i, technique="T1059.001", executed=start)
            for i, (name, start) in enumerate((("a", 0), ("b", 300), ("c", 600)), start=1)}
    runs = {"a": run("a", began=2, ended=250, rows=[rows["a"]]),
            "b": run("b", began=302, ended=550, rows=[rows["b"]]),
            "c": run("c", began=602, ended=850, rows=[rows["c"]])}
    for seconds, owner in ((100, "a"), (400, "b"), (700, "c"), (2000, "c")):
        a = alert(seconds, techniques=("T1059.001",))
        takers = [name for name, mine in runs.items()
                  if decide(mine, [o for n, o in runs.items() if n != name], rows[name], a).allowed]
        assert takers == [owner], (seconds, takers)


# ── the matcher under the rule ──────────────────────────────────────────────

def test_the_matcher_skips_a_refused_alert_and_reports_it():
    row = Row(1)
    mine = run("edr018", began=2, ended=133, rows=[row])
    later = run("mp003", began=1890, ended=1935)
    foreign = alert(1920, ext="1502502")
    refused: list = []

    verdicts = reconcile([row], [foreign], window_seconds=3600,
                         window_seconds_by_type=ANALYTICS_WINDOW,
                         attribution=attribution(mine, later), refused=refused)

    assert not verdicts[0].matched and verdicts[0].mttd_seconds is None
    assert [(r["result_id"], r["alert"].external_id, r["reason"], r["run_id"])
            for r in refused] == [(1, "1502502", LATER_RUN_STARTED, "mp003")]


def test_without_the_rule_the_matcher_takes_the_foreign_alert():
    """What 1.2.0 did, kept as the control: the same inputs with no
    attribution credit the row, 1,920 s after it ran."""
    row = Row(1)
    verdicts = reconcile([row], [alert(1920, ext="1502502")], window_seconds=3600,
                         window_seconds_by_type=ANALYTICS_WINDOW)
    assert verdicts[0].matched and verdicts[0].mttd_seconds == 1920.0
    assert verdicts[0].matched_on == ["technique-base", "source"]


def test_a_row_takes_its_own_alert_past_a_refused_one():
    """A refusal is not the end of the row's window: its own, later alert
    (tied by rule name) is still found."""
    row = Row(1, name="Rapid privilege escalation", technique="T1059.001")
    mine = run("a", began=2, ended=133, rows=[row])
    later = run("b", began=600, ended=700)
    foreign = alert(650, ext="foreign")
    own = alert(900, ext="own", name="Rapid privilege escalation", techniques=("T1059.001",))

    verdicts = reconcile([row], [foreign, own], window_seconds=3600,
                         window_seconds_by_type=ANALYTICS_WINDOW,
                         attribution=attribution(mine, later))
    assert verdicts[0].matched and verdicts[0].alert_external_id == "own"
