"""GET /api/reports/executions — the rollup of every execution on this engine.

Each test names the false reading it guards:

* a detection nobody reviewed counted as a miss;
* a scenario that never ran listed as 0 %, or an empty engine drawn as zeros;
* a Composer draft inflating the totals;
* a status, outcome or termination the vocabulary lacks dropped without a word;
* a filter that silently reports all time;
* the query loading ``Run.output`` (megabytes per run) or whole Result rows.
"""
from __future__ import annotations

import asyncio
import csv
import io
import os
from datetime import datetime

import pytest
from sqlalchemy import event

from config import settings

READ_BACK = {"verdict": "pass", "source": "reconcile:xsiam:lab", "scored_at": "2026-11-10T10:30:00"}
VERIFIED = {"verdict": "pending", "source": "verify",
            "verification": {"queries_issued": 3, "tenant": "lab"}}
NOT_READ = {"verdict": "pending", "source": "complete"}


def _det(description="d"):
    return {"type": "BIOC", "description": description}


def _scenario(sid, *, plane="EDR", technique="T1003.008", status="active", steps=None,
              uc_ref="UCS-EDR-02", tc_ref="TC-EDR-03", tc_refs=None, name=None):
    return dict(
        scenario_id=sid, name=name or f"{sid} name", version="1.0", plane=plane, status=status,
        uc_ref=uc_ref, tc_ref=tc_ref, uc_name="UC", tc_name=f"{tc_ref} title",
        tc_refs=tc_refs if tc_refs is not None else [tc_ref],
        mitre_tactic="TA0006", mitre_tactic_name="Credential Access",
        mitre_technique=technique, mitre_technique_name=f"{technique} name",
        steps=steps if steps is not None else [
            {"id": "step-01", "mitre_technique": technique, "expected_detections": [_det()]}],
    )


def _run(rid, sid, *, status="complete", started, target="agent-a", mode="pull", detail=None,
         output="x" * 64):
    return dict(run_id=rid, scenario_id=sid, mode=mode, status=status, target=target,
                started_at=started, tc_verdict_detail=detail, output=output)


def _result(rid, step_id, *, outcome=None, observed=False, reason=None):
    return dict(run_id=rid, step_id=step_id, plane="EDR", signal_type="BIOC",
                expected_detection="d", observed=observed, outcome=outcome, outcome_reason=reason)


def _step(rid, step_id, termination, exit_code=None, reason=None):
    return dict(run_id=rid, step_id=step_id, termination=termination, exit_code=exit_code, reason=reason)


def _seed(SessionLocal, scenarios=(), runs=(), results=(), steps=()):
    from models import Result, Run, Scenario, StepOutcome

    async def _go():
        async with SessionLocal() as s:
            for model, rows in ((Scenario, scenarios), (Run, runs), (Result, results), (StepOutcome, steps)):
                for kw in rows:
                    s.add(model(**kw))
            await s.commit()
    asyncio.run(_go())


EDR_STEPS = [
    {"id": "step-01", "mitre_technique": "T1087.001", "expected_detections": [_det()]},
    # No technique of its own: it carries the scenario's primary.
    {"id": "step-02", "expected_detections": [_det(), _det()]},
    {"id": "step-03", "mitre_technique": "T1552.001", "expected_detections": [_det()]},
]


def _seed_engine(SessionLocal):
    """Three reviewed runs, three unreviewed, one draft run, one scenario never run."""
    _seed(
        SessionLocal,
        scenarios=[
            _scenario("SIM-EDR-001", steps=EDR_STEPS, tc_refs=["TC-EDR-03", "TC-EDR-05"]),
            _scenario("SIM-EDR-002", technique="T1059.004", tc_ref="TC-EDR-09"),
            _scenario("SIM-NDR-001", plane="NDR", technique="T1071.001", uc_ref="UCS-NDR-01",
                      tc_ref="TC-NDR-01"),
            _scenario("SIM-ITDR-001", plane="ITDR", technique="T1558.003", tc_ref="TC-ITDR-01"),
            _scenario("SIM-DRAFT-probe", status="draft", technique="T1105", tc_ref="TC-EDR-03"),
        ],
        runs=[
            _run("r-edr-old", "SIM-EDR-001", started=datetime(2026, 11, 1, 10), detail=READ_BACK),
            _run("r-edr-new", "SIM-EDR-001", started=datetime(2026, 11, 10, 10), detail=READ_BACK),
            _run("r-edr-unrev", "SIM-EDR-001", status="failed", started=datetime(2026, 11, 12, 10),
                 target="agent-b", detail=NOT_READ),
            _run("r-ndr-1", "SIM-NDR-001", started=datetime(2026, 11, 11, 10), target="agent-b",
                 detail=VERIFIED),
            _run("r-staged", "SIM-EDR-002", status="staged", mode="push", target=None,
                 started=datetime(2026, 11, 13, 10)),
            _run("r-running", "SIM-NDR-001", status="running", started=datetime(2026, 11, 14, 12),
                 target="agent-b"),
            _run("r-draft", "SIM-DRAFT-probe", started=datetime(2026, 11, 14, 10), detail=NOT_READ),
        ],
        results=[
            _result("r-edr-old", "step-01", outcome="detected", observed=True),
            _result("r-edr-old", "step-02", outcome="detected", observed=True),
            _result("r-edr-old", "step-02", outcome="missed"),
            _result("r-edr-old", "step-03", outcome="not_reached"),
            _result("r-edr-new", "step-01", outcome="detected", observed=True),
            _result("r-edr-new", "step-02", outcome="missed"),
            # Reviewed run, row not judged yet: pending, as the Results page reads it.
            _result("r-edr-new", "step-02"),
            _result("r-edr-new", "step-03", outcome="prevented", observed=True),
            # Never reviewed: the seed value observed=False is not a verdict.
            _result("r-edr-unrev", "step-01"),
            _result("r-edr-unrev", "step-02"),
            _result("r-edr-unrev", "step-02"),
            # The host said the step never ran; that needs no review.
            _result("r-edr-unrev", "step-03", outcome="not_executed", reason="UPSTREAM_FAILED"),
            _result("r-ndr-1", "step-01", outcome="detected", observed=True,
                    reason="DETECTED_NOT_PREVENTED"),
            # A hand validation with no engine outcome reads detected.
            _result("r-ndr-1", "step-01", observed=True),
            _result("r-staged", "step-01"),
            _result("r-staged", "step-01"),
            _result("r-running", "step-01"),
            _result("r-draft", "step-01"),
        ],
        steps=[
            _step("r-edr-unrev", "step-01", "exited", 0),
            _step("r-edr-unrev", "step-02", "exited", 2),
            _step("r-edr-unrev", "step-03", "not_run", None, "UPSTREAM_FAILED"),
            _step("r-ndr-1", "step-01", "exited", 0),
            _step("r-ndr-1", "step-02", "timeout"),
            _step("r-draft", "step-01", "exited", 0),
        ],
    )


class _Index:
    """A stand-in for the process-wide UC/TC index."""

    def __init__(self, rows=None):
        self.loaded = rows is not None
        self._rows = rows or {}

    def tc(self, tc_ref):
        return self._rows.get(tc_ref)


@pytest.fixture(autouse=True)
def _no_index(monkeypatch):
    """The index is a process-wide singleton another test may have loaded; the
    rollup reads test-case names from it, so every test here states its own."""
    import api.reports as reports

    monkeypatch.setattr(reports, "uctc_registry", _Index())


@pytest.fixture
def client(make_client):
    from api.reports import router
    return make_client(router)


@pytest.fixture
def engine_client(client, session_factory):
    _seed_engine(session_factory)
    return client


def _get(client, **params):
    r = client.get("/api/reports/executions", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _row(rows, key, value):
    return next(r for r in rows if r[key] == value)


# ─── Honesty ────────────────────────────────────────────────────────────────

def test_an_empty_engine_says_so_and_lists_nothing(client, session_factory):
    _seed(session_factory, scenarios=[_scenario("SIM-EDR-001"), _scenario("SIM-NDR-001", plane="NDR")])
    body = _get(client)
    assert body["empty"] is True
    assert body["engine_runs"] == 0
    assert body["totals"]["runs"]["total"] == 0
    assert body["by_scenario"] == [] and body["by_technique"] == [] and body["by_test_case"] == []
    assert body["by_plane"] == [] and body["by_agent"] == []
    # The two loaded scenarios are "not run", never two rows at 0 %.
    assert body["totals"]["scenarios"] == {
        "exercised": 0, "corpus": 2, "corpus_exercised": 0, "corpus_not_run": 2}


def test_a_detection_nobody_reviewed_is_not_reviewed_never_missed(engine_client):
    det = _get(engine_client)["totals"]["detections"]
    assert det == {
        "expected": 17, "prevented": 1, "detected": 4, "detected_not_prevented": 1,
        "missed": 2, "pending": 1, "not_reached": 1, "not_executed": 1,
        "not_reviewed": 6, "unrecognised": {},
    }
    # Every row lands in exactly one bucket.
    assert det["expected"] == sum(v for k, v in det.items() if k not in ("expected", "unrecognised"))


def test_only_unreviewed_runs_report_no_miss_at_all(engine_client):
    """The failed run was never reviewed: three unjudged rows and one the host
    excluded. A naive `observed is False -> missed` would report three misses."""
    body = _get(engine_client, status="failed")
    det = body["totals"]["detections"]
    assert (det["missed"], det["not_reviewed"], det["not_executed"], det["expected"]) == (0, 3, 1, 4)
    assert body["totals"]["runs"]["reviewed"] == 0
    row = _row(body["by_scenario"], "scenario_id", "SIM-EDR-001")
    assert row["reviewed_runs"] == 0 and row["outcomes"] is None and row["last_reviewed_run_id"] is None


def test_an_unjudged_row_on_a_reviewed_run_is_pending_not_not_reviewed(engine_client):
    row = _row(_get(engine_client)["by_scenario"], "scenario_id", "SIM-EDR-001")
    assert row["last_reviewed_run_id"] == "r-edr-new"
    assert row["outcomes"] == {
        "expected": 4, "prevented": 1, "detected": 1, "detected_not_prevented": 0,
        "missed": 1, "pending": 1, "not_reached": 0, "not_executed": 0, "unrecognised": {},
    }


def test_a_scenario_that_never_ran_is_counted_not_listed(engine_client):
    body = _get(engine_client)
    assert "SIM-ITDR-001" not in [r["scenario_id"] for r in body["by_scenario"]]
    assert body["totals"]["scenarios"] == {
        "exercised": 3, "corpus": 4, "corpus_exercised": 3, "corpus_not_run": 1}
    # Launched and never reviewed: no outcome counts, not a row of zeros.
    staged = _row(body["by_scenario"], "scenario_id", "SIM-EDR-002")
    assert staged["outcomes"] is None and staged["last_status"] == "staged"


def test_drafts_are_left_out_unless_asked_for(engine_client):
    body = _get(engine_client)
    assert body["filters"]["include_drafts"] is False
    assert body["totals"]["runs"]["total"] == 6
    assert all(not r["scenario_id"].startswith("SIM-DRAFT-") for r in body["by_scenario"])
    # The draft's step record is left out with its run.
    assert body["totals"]["steps"]["recorded"] == 5

    with_drafts = _get(engine_client, include_drafts="true")
    assert with_drafts["totals"]["runs"]["total"] == 7
    assert "SIM-DRAFT-probe" in [r["scenario_id"] for r in with_drafts["by_scenario"]]
    # A draft is never corpus, even when its runs are counted.
    assert with_drafts["totals"]["scenarios"]["corpus"] == 4
    assert with_drafts["totals"]["scenarios"]["exercised"] == 4
    assert with_drafts["totals"]["scenarios"]["corpus_exercised"] == 3


def test_an_unrecognised_status_outcome_or_termination_is_named_not_dropped(client, session_factory):
    _seed(
        session_factory,
        scenarios=[_scenario("SIM-EDR-001")],
        runs=[
            _run("r-1", "SIM-EDR-001", status="completed", started=datetime(2026, 11, 1), detail=READ_BACK),
            _run("r-2", "SIM-EDR-001", status="complete", started=datetime(2026, 11, 2), detail=READ_BACK),
        ],
        results=[
            _result("r-2", "step-01", outcome="blocked"),
            _result("r-2", "step-01", outcome="detected", observed=True),
        ],
        steps=[
            _step("r-2", "step-01", "vaporised"),
            _step("r-2", "step-02", "exited", None),
            _step("r-2", "step-03", "exited", 0),
        ],
    )
    body = _get(client)
    runs = body["totals"]["runs"]
    assert runs["unrecognised"] == {"completed": 1}
    assert runs["by_status"]["complete"] == 1
    assert runs["total"] == sum(runs["by_status"].values()) + sum(runs["unrecognised"].values())
    assert "completed" in body["filter_options"]["statuses"]

    det = body["totals"]["detections"]
    assert det["unrecognised"] == {"blocked": 1}
    assert (det["expected"], det["detected"], det["missed"]) == (2, 1, 0)
    assert _row(body["by_scenario"], "scenario_id", "SIM-EDR-001")["outcomes"]["unrecognised"] == {"blocked": 1}

    steps = body["totals"]["steps"]
    assert steps["unrecognised"] == {"vaporised": 1, "exited (no exit code)": 1}
    assert (steps["recorded"], steps["ran"], steps["failed"]) == (3, 1, 0)


# ─── Totals and rows ────────────────────────────────────────────────────────

def test_runs_by_status_and_review(engine_client):
    runs = _get(engine_client)["totals"]["runs"]
    assert runs == {
        "total": 6,
        "by_status": {"pending": 0, "running": 1, "complete": 3, "failed": 1, "aborted": 0, "staged": 1},
        "unrecognised": {}, "reviewed": 3, "not_reviewed": 3,
    }


def test_steps_come_from_the_host_record(engine_client):
    assert _get(engine_client)["totals"]["steps"] == {
        "recorded": 5, "ran": 2, "failed": 1, "did_not_run": 1,
        "interrupted": 1, "interrupted_by": {"timeout": 1}, "unrecognised": {},
        "runs_with_host_record": 2, "runs_without_host_record": 4,
    }


def test_the_scenario_row_maps_and_dates_its_runs(engine_client):
    body = _get(engine_client)
    row = _row(body["by_scenario"], "scenario_id", "SIM-EDR-001")
    assert row["name"] == "SIM-EDR-001 name" and row["plane"] == "EDR"
    assert row["techniques"] == ["T1003.008", "T1087.001", "T1552.001"]
    assert row["uc_ref"] == "UCS-EDR-02" and row["tc_refs"] == ["TC-EDR-03", "TC-EDR-05"]
    assert row["runs"] == 3 and row["runs_by_status"] == {"complete": 2, "failed": 1}
    assert row["first_run_at"] == "2026-11-01T10:00:00+00:00"
    assert row["last_run_at"] == "2026-11-12T10:00:00+00:00"
    assert (row["last_status"], row["last_run_id"]) == ("failed", "r-edr-unrev")
    assert row["reviewed_runs"] == 2
    assert row["last_reviewed_at"] == "2026-11-10T10:00:00+00:00"
    # Newest run first.
    assert [r["scenario_id"] for r in body["by_scenario"]] == ["SIM-NDR-001", "SIM-EDR-002", "SIM-EDR-001"]
    assert body["window"]["first_run_at"] == "2026-11-01T10:00:00+00:00"
    assert body["window"]["last_run_at"] == "2026-11-14T12:00:00+00:00"


def test_a_verification_that_issued_a_query_counts_as_reviewed(engine_client):
    row = _row(_get(engine_client)["by_scenario"], "scenario_id", "SIM-NDR-001")
    assert row["reviewed_runs"] == 1 and row["last_reviewed_run_id"] == "r-ndr-1"
    # A prevention that was only detected is its own bucket, never a plain Detected.
    assert (row["outcomes"]["detected_not_prevented"], row["outcomes"]["detected"]) == (1, 1)


def test_the_technique_view_counts_only_the_steps_that_carry_it(engine_client):
    body = _get(engine_client)
    assert body["totals"]["techniques_exercised"] == 5
    techs = {r["technique"]: r for r in body["by_technique"]}
    assert sorted(techs) == ["T1003.008", "T1059.004", "T1071.001", "T1087.001", "T1552.001"]
    discovery = techs["T1087.001"]
    assert discovery["scenarios"] == ["SIM-EDR-001"] and discovery["runs"] == 3
    assert discovery["last_run_at"] == "2026-11-12T10:00:00+00:00"
    assert discovery["reviewed_scenarios"] == 1
    assert (discovery["outcomes"]["expected"], discovery["outcomes"]["detected"]) == (1, 1)
    # step-02 names no technique, so its two detections are the primary's.
    primary = techs["T1003.008"]
    assert primary["name"] == "T1003.008 name"
    assert (primary["outcomes"]["expected"], primary["outcomes"]["missed"], primary["outcomes"]["pending"]) == (2, 1, 1)
    assert techs["T1552.001"]["outcomes"]["prevented"] == 1
    # Launched, never reviewed: no counts.
    assert techs["T1059.004"]["outcomes"] is None and techs["T1059.004"]["reviewed_scenarios"] == 0


def test_the_test_case_view_follows_tc_refs(engine_client):
    body = _get(engine_client)
    assert body["totals"]["test_cases_touched"] == 4
    tcs = {r["tc_ref"]: r for r in body["by_test_case"]}
    assert sorted(tcs) == ["TC-EDR-03", "TC-EDR-05", "TC-EDR-09", "TC-NDR-01"]
    assert tcs["TC-EDR-03"]["name"] == "TC-EDR-03 title" and tcs["TC-EDR-03"]["uc_ref"] == "UCS-EDR-02"
    for tc in ("TC-EDR-03", "TC-EDR-05"):
        assert tcs[tc]["scenarios"] == ["SIM-EDR-001"] and tcs[tc]["runs"] == 3
        assert tcs[tc]["outcomes"]["expected"] == 4
    assert tcs["TC-EDR-09"]["outcomes"] is None
    # No index loaded: a test case that is not a scenario's primary has no name to give.
    assert tcs["TC-EDR-05"]["name"] is None and tcs["TC-EDR-05"]["uc_ref"] is None


def test_the_test_case_view_names_rows_from_the_index_when_it_is_loaded(engine_client, monkeypatch):
    from types import SimpleNamespace

    import api.reports as reports

    monkeypatch.setattr(reports, "uctc_registry", _Index({
        "TC-EDR-05": SimpleNamespace(title="Behavioural detection", ucs_id="UCS-EDR-03", uc_id="UC-EDR"),
        "TC-EDR-03": SimpleNamespace(title="Index title", ucs_id="", uc_id="UC-EDR"),
    }))
    tcs = {r["tc_ref"]: r for r in _get(engine_client)["by_test_case"]}
    assert (tcs["TC-EDR-05"]["name"], tcs["TC-EDR-05"]["uc_ref"]) == ("Behavioural detection", "UCS-EDR-03")
    # The index wins over the scenario's own copy of the title.
    assert (tcs["TC-EDR-03"]["name"], tcs["TC-EDR-03"]["uc_ref"]) == ("Index title", "UC-EDR")
    # A test case the index does not hold falls back to the scenario that binds it.
    assert tcs["TC-NDR-01"]["name"] == "TC-NDR-01 title"


def test_planes_and_agents_count_runs(engine_client):
    body = _get(engine_client)
    assert body["by_plane"] == [
        {"plane": "EDR", "runs": 4, "scenarios": 2}, {"plane": "NDR", "runs": 2, "scenarios": 1}]
    agents = {r["agent"]: r for r in body["by_agent"]}
    assert {k: v["runs"] for k, v in agents.items()} == {"agent-a": 2, "agent-b": 3, None: 1}
    assert agents["agent-b"]["scenarios"] == 2
    assert agents["agent-b"]["last_run_at"] == "2026-11-14T12:00:00+00:00"
    assert body["filter_options"]["agents"] == ["agent-a", "agent-b"]
    assert body["filter_options"]["planes"] == ["EDR", "ITDR", "NDR"]


# ─── Filters ────────────────────────────────────────────────────────────────

def test_plane_status_and_agent_filters(engine_client):
    ndr = _get(engine_client, plane="NDR")
    assert ndr["totals"]["runs"]["total"] == 2
    assert [r["scenario_id"] for r in ndr["by_scenario"]] == ["SIM-NDR-001"]
    assert ndr["totals"]["scenarios"]["corpus"] == 1 and ndr["filters"]["plane"] == "NDR"

    failed = _get(engine_client, status="failed")
    assert failed["totals"]["runs"]["by_status"]["failed"] == failed["totals"]["runs"]["total"] == 1

    agent = _get(engine_client, agent="agent-a")
    assert agent["totals"]["runs"]["total"] == 2
    assert [r["agent"] for r in agent["by_agent"]] == ["agent-a"]
    # The filter row still offers every agent and plane.
    assert agent["filter_options"]["agents"] == ["agent-a", "agent-b"]


def test_a_filter_that_matches_nothing_is_empty_on_a_busy_engine(engine_client):
    body = _get(engine_client, plane="CSPM")
    assert body["empty"] is True and body["engine_runs"] == 7
    assert body["by_scenario"] == []


def test_the_time_window_is_utc_whatever_the_offset_says(engine_client):
    # 02:00 at -08:00 is 10:00 UTC, the instant r-ndr-1 started.
    body = _get(engine_client, since="2026-11-11T02:00:00-08:00")
    assert body["window"]["since"] == "2026-11-11T10:00:00+00:00"
    assert body["totals"]["runs"]["total"] == 4
    assert sorted(r["scenario_id"] for r in body["by_scenario"]) == ["SIM-EDR-001", "SIM-EDR-002", "SIM-NDR-001"]
    # In that window SIM-EDR-001 has only its unreviewed run.
    assert _row(body["by_scenario"], "scenario_id", "SIM-EDR-001")["runs"] == 1

    bounded = _get(engine_client, since="2026-11-10T00:00:00Z", until="2026-11-11T10:00:00Z")
    assert bounded["totals"]["runs"]["total"] == 2
    assert bounded["window"]["until"] == "2026-11-11T10:00:00+00:00"


def test_an_unreadable_time_is_refused_not_ignored(engine_client):
    r = engine_client.get("/api/reports/executions", params={"since": "last tuesday"})
    assert r.status_code == 422
    assert r.json()["code"] == "INVALID_TIME_RANGE"
    assert engine_client.get("/api/reports/executions", params={"format": "xlsx"}).status_code == 422


# ─── CSV ────────────────────────────────────────────────────────────────────

def test_csv_is_one_row_per_scenario_as_an_attachment(engine_client):
    from api.reports import CSV_COLUMNS

    r = engine_client.get("/api/reports/executions", params={"format": "csv"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"].startswith('attachment; filename="cortexsim-executions-')
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert list(rows[0]) == list(CSV_COLUMNS)
    assert [row["scenario_id"] for row in rows] == ["SIM-NDR-001", "SIM-EDR-002", "SIM-EDR-001"]
    edr = rows[2]
    assert edr["techniques"] == "T1003.008;T1087.001;T1552.001"
    assert edr["tc_refs"] == "TC-EDR-03;TC-EDR-05"
    assert (edr["runs"], edr["reviewed_runs"], edr["last_reviewed_run_id"]) == ("3", "2", "r-edr-new")
    assert (edr["expected"], edr["detected"], edr["missed"], edr["pending"]) == ("4", "1", "1", "1")
    # Never reviewed: blank cells, not zeros.
    staged = rows[1]
    assert staged["last_status"] == "staged"
    assert (staged["expected"], staged["detected"], staged["missed"]) == ("", "", "")


def test_csv_text_that_starts_like_a_formula_is_quoted(client, session_factory):
    """A DC opens this file in a spreadsheet, and a scenario or draft name is
    operator-written text: a cell starting = + - @ would run as a formula."""
    link = '=HYPERLINK("http://203.0.113.9/x","open")'
    _seed(
        session_factory,
        scenarios=[
            _scenario("SIM-DRAFT-link", status="draft", name=link),
            _scenario("SIM-EDR-001", name="-1+1", tc_ref="=1+1", tc_refs=["=1+1", "TC-EDR-05"]),
            _scenario("SIM-EDR-002", name="Reverse shell", uc_ref="@SUM(1)"),
            _scenario("SIM-EDR-003", name=" =SUM(A1:A9)", tc_ref="TC-EDR-03",
                      tc_refs=["TC-EDR-03", "=2+2", "TC-EDR-05"]),
        ],
        runs=[
            _run("r-1", "SIM-DRAFT-link", started=datetime(2026, 11, 3)),
            _run("r-2", "SIM-EDR-001", started=datetime(2026, 11, 2)),
            _run("r-3", "SIM-EDR-002", started=datetime(2026, 11, 1), detail=READ_BACK),
            _run("r-4", "SIM-EDR-003", started=datetime(2026, 10, 31)),
        ],
        results=[_result("r-3", "step-01", outcome="detected", observed=True)],
    )
    params = {"format": "csv", "include_drafts": "true"}
    text = client.get("/api/reports/executions", params=params).text
    rows = {row["scenario_id"]: row for row in csv.DictReader(io.StringIO(text))}
    assert rows["SIM-DRAFT-link"]["name"] == "'" + link
    assert rows["SIM-EDR-001"]["name"] == "'-1+1"
    # Leading whitespace does not hide a formula; the text after the quote is as written.
    assert rows["SIM-EDR-003"]["name"] == "' =SUM(A1:A9)"
    # A joined cell is made safe element by element: a spreadsheet that splits
    # on ';' would put the second element at the start of a cell.
    assert rows["SIM-EDR-001"]["tc_refs"] == "'=1+1;TC-EDR-05"
    assert rows["SIM-EDR-003"]["tc_refs"] == "TC-EDR-03;'=2+2;TC-EDR-05"
    # Every field is quoted, the header and the numbers included.
    assert text.splitlines()[0].startswith('"scenario_id","name","plane"')
    assert all(line.startswith('"') and line.endswith('"') for line in text.splitlines())
    assert rows["SIM-EDR-002"]["uc_ref"] == "'@SUM(1)"
    # Plain text and numbers are left as they are.
    assert rows["SIM-EDR-002"]["name"] == "Reverse shell"
    assert (rows["SIM-EDR-002"]["runs"], rows["SIM-EDR-002"]["detected"]) == ("1", "1")
    assert rows["SIM-EDR-001"]["expected"] == ""
    # The JSON body carries the text as written.
    body = _get(client, include_drafts="true")
    assert _row(body["by_scenario"], "scenario_id", "SIM-DRAFT-link")["name"] == link
    assert _row(body["by_scenario"], "scenario_id", "SIM-EDR-001")["tc_refs"] == ["=1+1", "TC-EDR-05"]
    assert _row(body["by_scenario"], "scenario_id", "SIM-EDR-003")["tc_refs"] == [
        "TC-EDR-03", "=2+2", "TC-EDR-05"]


# ─── The query ──────────────────────────────────────────────────────────────

def test_the_query_never_loads_run_output_or_whole_result_rows(engine_client, session_factory):
    statements = []
    engine = session_factory.kw["bind"].sync_engine

    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        _get(engine_client)
        engine_client.get("/api/reports/executions", params={"format": "csv"})
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert statements, "the capture saw no SQL"
    sql = "\n".join(statements).lower()
    assert "output" not in sql
    # Whole Result rows carry the detection logic; an aggregate never names it.
    assert "detection_logic" not in sql and "verification_xql" not in sql
    # Bounded by tables, not by rows: no statement per run or per scenario.
    assert len(statements) <= 2 * 12


def test_the_statement_count_does_not_grow_with_the_run_count(client, session_factory):
    engine = session_factory.kw["bind"].sync_engine
    counts = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        counts[-1] += 1

    def _measure():
        counts.append(0)
        _get(client)
        return counts[-1]

    _seed(session_factory, scenarios=[_scenario(f"SIM-EDR-{i:03d}") for i in range(1, 31)],
          runs=[_run("r-0", "SIM-EDR-001", started=datetime(2026, 11, 1), detail=READ_BACK)])
    event.listen(engine, "before_cursor_execute", _count)
    try:
        few = _measure()
        _seed(session_factory,
              runs=[_run(f"r-{i}", f"SIM-EDR-{(i % 30) + 1:03d}", started=datetime(2026, 11, 2, i % 24),
                         detail=READ_BACK if i % 2 else NOT_READ) for i in range(1, 121)],
              results=[_result(f"r-{i}", "step-01", outcome="detected" if i % 2 else None)
                       for i in range(1, 121)])
        many = _measure()
    finally:
        event.remove(engine, "before_cursor_execute", _count)
    assert many == few


# ─── Detection sources ──────────────────────────────────────────────────────

def test_detection_sources_are_the_doors_the_scenario_needs(make_client, session_factory):
    from api.doors import router as doors_router
    from api.reports import router as reports_router
    from engine.scenario_loader import _parse_and_validate, _schema_to_orm_kwargs
    from engine.ttp_catalog import catalog, default_corpus_dir
    from models import Scenario

    catalog.load(default_corpus_dir(settings.CORTEXSIM_BASE_DIR))
    edr_dir = os.path.join(settings.CORTEXSIM_BASE_DIR, "scenarios", "edr")
    path = os.path.join(edr_dir, sorted(
        f for f in os.listdir(edr_dir) if f.endswith(".yml") and not f.startswith("_"))[0])
    schema, err = _parse_and_validate(path)
    assert err is None, err
    kwargs = _schema_to_orm_kwargs(schema)
    sid = kwargs["scenario_id"]

    async def _add():
        async with session_factory() as s:
            s.add(Scenario(**kwargs))
            await s.commit()
    asyncio.run(_add())
    _seed(session_factory, runs=[_run("r-1", sid, started=datetime(2026, 11, 1))])

    client = make_client(doors_router, reports_router)
    body = _get(client)
    assert body["detection_sources"] == {"available": True, "reason": None, "failed": []}
    row = _row(body["by_scenario"], "scenario_id", sid)
    derived = client.get(f"/api/scenarios/{sid}/doors").json()["detections"]
    expected = {d for det in derived for element in det["requirement"]["all_of"] for d in element}
    assert expected, "the fixture scenario derives no door; pick another"
    assert {s["id"] for s in row["detection_sources"]} == expected
    assert all(s["label"] for s in row["detection_sources"])


def test_an_absent_registry_reads_unavailable_not_no_sources(engine_client, monkeypatch):
    import api.reports as reports

    monkeypatch.setattr(reports, "load_dataset_registry", lambda: None)
    body = _get(engine_client)
    assert body["detection_sources"] == {"available": False, "reason": "REGISTRY_ABSENT", "failed": []}
    assert all(r["detection_sources"] is None for r in body["by_scenario"])
    csv_rows = list(csv.DictReader(io.StringIO(
        engine_client.get("/api/reports/executions", params={"format": "csv"}).text)))
    assert {row["detection_sources"] for row in csv_rows} == {""}
