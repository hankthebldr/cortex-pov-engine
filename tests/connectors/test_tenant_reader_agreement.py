"""Agreement between the matcher's rows and the tenant reader's issues (spec §7.3).

Two match classes, counted separately and never summed:

* ``id`` — the id probe read the matcher row's ``external_id`` back from
  ``issue/search`` AND the hit issue is attributed to the run host;
* ``name_host_window`` — the weaker fallback, tried only by a row the probe did
  not find (or by every row when the probe failed): same name under the
  matcher's ``_norm``, both on the run host, within the tolerance.

A probe miss is ``no_hit``: it proves nothing about the ids. Issue rows are the
2026-11-12 spike's redacted reply (``tests/fixtures/tenant_reader/``), projected:
1001 (``synthetic-3``, 12:26:45) and 1002 (``synthetic-38``, 12:27:20) are on
``target-01``; 1003 (``synthetic-73``, 12:27:55) is a ``FW`` row with no host.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta

from connectors.tenant_reader import agreement, attribute, project_issue
from tests.connectors.tenant_fakes import fixture

ISSUES = fixture("issue_search_reply.json")["reply"]["DATA"]
RUN_HOST = "target-01"
TOL = 300

OBSERVED = {           # each fixture issue's observation_time, naive UTC
    "1001": datetime(2025, 11, 1, 12, 26, 45),
    "1002": datetime(2025, 11, 1, 12, 27, 20),
    "1003": datetime(2025, 11, 1, 12, 27, 55),
}


def _issue(i: int, **kw) -> dict:
    """Fixture issue ``i`` projected, with raw fields replaced. ``host`` sets
    ``xdm.source.host.hostname`` (``None`` removes every host field)."""
    row = copy.deepcopy(ISSUES[i])
    host = kw.pop("host", ...)
    if host is not ...:
        nf = row["normalized_fields"]
        nf.pop("xdm.target.host.hostname", None)
        if host is None:
            nf.pop("xdm.source.host.hostname", None)
        else:
            nf["xdm.source.host.hostname"] = host
    row.update(kw)
    return project_issue(row)


def _window() -> list[dict]:
    return [project_issue(r) for r in ISSUES]


def _attributed(issues: list[dict] | None = None, run_host: str | None = RUN_HOST) -> list[dict]:
    return attribute(_window() if issues is None else issues, run_host)["attributed"]


def _mrow(result_id: int, *, external_id=None, name=None, observed_at=None,
          host=RUN_HOST) -> dict:
    """One matcher row: an ``Observation`` linked to a ``Result``."""
    return {"result_id": result_id, "external_id": external_id, "name": name,
            "observed_at": observed_at, "host": host,
            "executed_at": datetime(2025, 11, 1, 12, 26, 0)}


def _class_pairs(result: dict) -> list[tuple]:
    return [(p["result_id"], p["issue_id"], p["class"]) for p in result["pairs"]]


def test_probe_hit_on_attributed_issue_joins_in_class_id():
    attributed = _attributed()                                    # 1001, 1002
    beyond_window = _issue(0, id=1009)                            # on target-01, not in the window read
    probe = [_issue(0), beyond_window]
    rows = [_mrow(1, external_id="1001", name="unrelated name", observed_at=OBSERVED["1001"]),
            _mrow(2, external_id=1009)]                           # an int id still joins
    out = agreement(rows, attributed, probe, RUN_HOST, tolerance_seconds=TOL)
    assert _class_pairs(out) == [(1, "1001", "id"), (2, "1009", "id")]
    assert out["both"] == {"id": 2, "name_host_window": 0}
    assert out["id_space"] == {"probed": 2, "hit": 2, "no_hit": 0}
    assert out["matcher_only"] == 0
    assert out["reader_only"] == 1                                # 1002: no matcher row
    assert out["unjoinable"] == 0 and out["ambiguous"] == 0


def test_probe_miss_falls_back_to_name_host_window_counted_separately():
    attributed = _attributed()
    rows = [
        _mrow(1, external_id="1001"),                             # hit: class id
        _mrow(2, external_id="alert-77", name="  SYNTHETIC-38 ",  # miss: name + host + window
              observed_at=OBSERVED["1002"] + timedelta(seconds=40), host="TARGET-01.corp.example"),
    ]
    out = agreement(rows, attributed, [_issue(0)], RUN_HOST, tolerance_seconds=TOL)
    assert _class_pairs(out) == [(1, "1001", "id"), (2, "1002", "name_host_window")]
    assert out["both"] == {"id": 1, "name_host_window": 1}
    assert out["id_space"] == {"probed": 2, "hit": 1, "no_hit": 1}
    assert out["matcher_only"] == 0 and out["reader_only"] == 0


def test_probe_miss_is_no_hit_never_ids_differ():
    rows = [_mrow(1, external_id="alert-77", name="no such issue", observed_at=OBSERVED["1001"])]
    out = agreement(rows, _attributed(), [], RUN_HOST, tolerance_seconds=TOL)
    assert out["id_space"] == {"probed": 1, "hit": 0, "no_hit": 1}
    assert out["both"] == {"id": 0, "name_host_window": 0}
    assert out["matcher_only"] == 1
    assert "differ" not in json.dumps(out)


def test_probe_hit_on_unattributed_issue_never_enters_both():
    elsewhere = _issue(1, id=1009, host="web-02")                 # named like 1002, another host
    # The hit proves which issue the alert is; it is not this run's, so the
    # row never falls back to the same-named attributed issue 1002.
    rows = [_mrow(1, external_id="1009", name="synthetic-38", observed_at=OBSERVED["1002"])]
    out = agreement(rows, _attributed(), [elsewhere], RUN_HOST, tolerance_seconds=TOL)
    assert out["id_space"] == {"probed": 1, "hit": 1, "no_hit": 0}
    assert out["both"] == {"id": 0, "name_host_window": 0}
    assert out["pairs"] == []
    assert out["matcher_only"] == 1
    assert out["reader_only"] == 2

    rows = [_mrow(1, external_id="1002")]
    unscoped = agreement(rows, _attributed(run_host=None), [_issue(1)], None, tolerance_seconds=TOL)
    assert unscoped["id_space"]["hit"] == 1
    assert unscoped["both"] == {"id": 0, "name_host_window": 0}


def test_issue_without_host_never_in_both():
    hostless = _issue(2)                                          # 1003, the FW row: no hostname
    assert hostless["hosts"] == []
    rows = [
        _mrow(1, external_id="1003"),                             # probe hit on the hostless issue
        _mrow(2, name="synthetic-73", observed_at=OBSERVED["1003"]),  # name + window on it
    ]
    # Even handed in as "attributed", an issue with no host is never credited (A4).
    out = agreement(rows, [hostless], [hostless], RUN_HOST, tolerance_seconds=TOL)
    assert out["id_space"] == {"probed": 1, "hit": 1, "no_hit": 0}
    assert out["both"] == {"id": 0, "name_host_window": 0}
    assert out["pairs"] == []
    assert out["matcher_only"] == 2
    assert out["reader_only"] == 0


def test_two_name_candidates_are_ambiguous_not_resolved():
    twin = _issue(0, id=1010, observation_time=1762000065000)     # synthetic-3 again, 80 s later
    attributed = _attributed([_issue(0), twin, _issue(1)])
    rows = [_mrow(1, name="synthetic-3", observed_at=OBSERVED["1001"] + timedelta(seconds=30))]
    out = agreement(rows, attributed, [], RUN_HOST, tolerance_seconds=TOL)
    assert out["ambiguous"] == 1
    assert out["both"] == {"id": 0, "name_host_window": 0}
    assert out["pairs"] == []
    assert out["matcher_only"] == 0
    assert out["reader_only"] == 1          # 1002 only: the two candidates are not "reader only"

    # One candidate claimed by two matcher rows is ambiguous for both.
    rows = [_mrow(1, name="synthetic-38", observed_at=OBSERVED["1002"]),
            _mrow(2, name="Synthetic-38", observed_at=OBSERVED["1002"] + timedelta(seconds=5))]
    out = agreement(rows, _attributed(), [], RUN_HOST, tolerance_seconds=TOL)
    assert out["ambiguous"] == 2
    assert out["pairs"] == []
    assert out["both"]["name_host_window"] == 0


def test_row_without_id_or_name_is_unjoinable():
    rows = [_mrow(1, external_id=None, name=None, observed_at=OBSERVED["1001"]),
            _mrow(2, external_id="  ", name="   ", observed_at=OBSERVED["1002"])]
    out = agreement(rows, _attributed(), [_issue(0)], RUN_HOST, tolerance_seconds=TOL)
    assert out["unjoinable"] == 2
    assert out["matcher_only"] == 0
    assert out["id_space"] == {"probed": 0, "hit": 0, "no_hit": 0}
    assert out["pairs"] == []


def test_301_seconds_apart_at_tolerance_300_is_matcher_only():
    at = OBSERVED["1001"]
    late = [_mrow(1, name="synthetic-3", observed_at=at + timedelta(seconds=301))]
    out = agreement(late, _attributed(), [], RUN_HOST, tolerance_seconds=TOL)
    assert out["matcher_only"] == 1
    assert out["both"]["name_host_window"] == 0
    assert out["reader_only"] == 2

    edge = [_mrow(1, name="synthetic-3", observed_at=at - timedelta(seconds=300))]
    out = agreement(edge, _attributed(), [], RUN_HOST, tolerance_seconds=TOL)
    assert _class_pairs(out) == [(1, "1001", "name_host_window")]


def test_failed_probe_reports_null_id_counts():
    rows = [_mrow(1, external_id="1001", name="synthetic-3", observed_at=OBSERVED["1001"]),
            _mrow(2, external_id="1002", name="synthetic-38", observed_at=OBSERVED["1002"],
                  host="web-02")]
    out = agreement(rows, _attributed(), None, RUN_HOST, tolerance_seconds=TOL)
    assert out["both"] == {"id": None, "name_host_window": 1}
    assert out["id_space"] == {"probed": 2, "hit": None, "no_hit": None}
    # Every row tried the name fallback: 1001 joined; row 2 sits on another host.
    assert _class_pairs(out) == [(1, "1001", "name_host_window")]
    assert out["matcher_only"] == 1
    assert out["reader_only"] == 1


def test_matcher_name_compares_as_the_projection_stores_it():
    # Alert and issue carry the same tenant string. The issue's tenant_text.name
    # is neutralised (zero-width stripped, capped at 200), so the matcher's
    # name must be read the same way or a long name never joins.
    raw = "Suspicious​ process " + "x" * 300
    rows = [_mrow(1, name=raw, observed_at=OBSERVED["1001"])]
    out = agreement(rows, _attributed([_issue(0, name=raw)]), [], RUN_HOST, tolerance_seconds=TOL)
    assert _class_pairs(out) == [(1, "1001", "name_host_window")]


def test_one_alert_linked_to_three_results_joins_in_both_classes():
    # The matcher records one Observation per (result, alert) and does not
    # consume technique/name matches, so one alert behind three Results is the
    # normal case: it is ONE alert, joined once, with one pair per Result.
    def fanned(external_id, name, observed_at):
        return [_mrow(r, external_id=external_id, name=name, observed_at=observed_at)
                for r in (1, 2, 3)]

    by_id = agreement(fanned("1001", "synthetic-3", OBSERVED["1001"]), _attributed(), [_issue(0)],
                      RUN_HOST, tolerance_seconds=TOL)
    assert by_id["both"] == {"id": 1, "name_host_window": 0}
    assert _class_pairs(by_id) == [(1, "1001", "id"), (2, "1001", "id"), (3, "1001", "id")]
    assert by_id["id_space"] == {"probed": 1, "hit": 1, "no_hit": 0}
    assert by_id["ambiguous"] == 0 and by_id["matcher_only"] == 0
    assert by_id["reader_only"] == 1                              # 1002

    by_name = agreement(fanned("alert-77", "synthetic-38", OBSERVED["1002"]), _attributed(), [],
                        RUN_HOST, tolerance_seconds=TOL)
    assert by_name["both"] == {"id": 0, "name_host_window": 1}
    assert _class_pairs(by_name) == [(1, "1002", "name_host_window"),
                                     (2, "1002", "name_host_window"),
                                     (3, "1002", "name_host_window")]
    assert by_name["id_space"] == {"probed": 1, "hit": 0, "no_hit": 1}
    assert by_name["ambiguous"] == 0

    no_probe = agreement(fanned("1001", "synthetic-3", OBSERVED["1001"]), _attributed(), None,
                         RUN_HOST, tolerance_seconds=TOL)
    assert no_probe["both"] == {"id": None, "name_host_window": 1}
    assert len(no_probe["pairs"]) == 3 and no_probe["ambiguous"] == 0
    assert no_probe["id_space"] == {"probed": 1, "hit": None, "no_hit": None}

    no_id = agreement(fanned(None, "synthetic-38", OBSERVED["1002"]), _attributed(), [],
                      RUN_HOST, tolerance_seconds=TOL)
    assert no_id["both"]["name_host_window"] == 1
    assert len(no_id["pairs"]) == 3 and no_id["ambiguous"] == 0
