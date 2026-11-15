"""#346 on the surfaces a DC reads: the results API, the run, the report.

A run's alerts that match none of its expected detections are listed, not
credited: ``GET /api/results/{run_id}`` (what the console's Results page
reads) carries them as ``additional_detections``, and the report has them
under "Other alerts Cortex raised for this run". A BIOC row credited by an
Analytics BIOC alert says so (``matched_via``, and in the row's note).

Driven through the real routes; the alerts arrive by ``/observations``, which
shares the reconcile funnel with the connector pull.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tests.api import _reconcile_flow as flow

AGENT, HOST = "ag-346", "lab-endpoint-01"
HEADING = "## Other alerts Cortex raised for this run"


@pytest.fixture
def client(make_client, session_factory):
    flow.seed_scenarios(session_factory)
    return flow.build_client(make_client)


@pytest.fixture(autouse=True)
def _clean():
    flow.reset_orchestrator()
    yield
    flow.reset_orchestrator()


def _executed_run(client) -> str:
    run_id = flow.launch(client, agent_id=AGENT)
    flow.collect_task(client, AGENT)
    done = client.post(f"/api/runs/{run_id}/complete", json={
        "exit_code": 0, "summary": "ok",
        "steps": [{"step_id": "step-01", "exit_code": 0, "termination": "exited"}]})
    assert done.status_code == 200, done.text
    return run_id


def _alert(ext: str, name: str, source: str, techniques: list[str], *, plus: float = 5) -> dict:
    return {"external_id": ext, "name": name, "alert_source": source, "techniques": techniques,
            "host": HOST,
            "observed_at": (datetime.utcnow() + timedelta(seconds=plus)).isoformat() + "Z"}


def _agent_alert(ext: str = "1502466", name: str = "Local Analysis Malware") -> dict:
    return _alert(ext, name, "XDR Agent", [])


def _report(client, run_id: str, fmt: str = "markdown"):
    r = client.get(f"/api/runs/{run_id}/report", params={"format": fmt})
    assert r.status_code == 200, r.text
    return r.text if fmt == "markdown" else r.json()


@pytest.fixture
def run_id(client) -> str:
    flow.register(client, AGENT, HOST)
    return _executed_run(client)


# ── GET /api/results/{run_id} ───────────────────────────────────────────────

def test_a_run_nobody_reconciled_reads_not_reviewed(client, run_id):
    body = flow.results_body(client, run_id)
    assert body["additional_detections"] == [] and body["unattributed_alerts"] == []
    assert body["alerts_reviewed_at"] is None       # not "reviewed, none found"


def test_the_results_api_lists_the_alert_and_credits_nothing(client, run_id):
    summary = flow.observe(client, run_id, [_agent_alert()]).json()
    assert [e["alert_external_id"] for e in summary["additional_detections"]] == ["1502466"]

    body = flow.results_body(client, run_id)
    assert [(e["alert_external_id"], e["alert_name"], e["alert_source"], e["step_id"])
            for e in body["additional_detections"]] == [
        ("1502466", "Local Analysis Malware", "XDR Agent", "step-01")]
    assert body["alerts_reviewed_at"]
    # The expected row is untouched and coverage still measures expected rows.
    assert flow.view(body["results"][0])[0] is False
    assert body["coverage"]["observed"] == 0 and body["coverage"]["total"] == 1
    assert body["total"] == 1


def test_a_run_id_that_does_not_exist_is_still_404(client):
    r = client.get("/api/results/no-such-run")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "RUN_NOT_FOUND"


# ── the run ─────────────────────────────────────────────────────────────────

def test_the_run_carries_its_alert_attribution_and_the_list_does_not(client, run_id):
    flow.observe(client, run_id, [_agent_alert()])
    run = client.get(f"/api/runs/{run_id}").json()
    assert [e["alert_external_id"] for e in run["alert_attribution"]["additional"]] == ["1502466"]
    assert run["alert_attribution"]["host"] == HOST
    listed = client.get("/api/runs").json()["runs"]
    assert listed and all("alert_attribution" not in row for row in listed)


# ── the report ──────────────────────────────────────────────────────────────

def test_the_markdown_report_lists_them_under_their_own_heading(client, run_id):
    flow.observe(client, run_id, [_agent_alert()])
    report = _report(client, run_id)
    assert HEADING in report
    section = report.split(HEADING, 1)[1]
    assert "| Time \\(UTC\\) |" not in section and "| Time (UTC) | Alert | Source |" in section
    assert "| Local Analysis Malware | XDR Agent |" in section
    assert "| step-01 |" in section
    assert "not counted in the coverage" in section
    # ... after the per-detection results, and the coverage line is unmoved.
    assert report.index("## Detailed Detection Results") < report.index(HEADING)
    assert "**Overall: 0/1 detections confirmed (0.0%)" in report


def test_the_report_has_no_such_section_for_a_run_with_none(client, run_id):
    assert HEADING not in _report(client, run_id)
    flow.observe(client, run_id, [flow.alert("own", "Shadow file read", "T1003", "d-1", HOST)])
    assert HEADING not in _report(client, run_id)


def test_the_json_report_carries_them_outside_coverage(client, run_id):
    flow.observe(client, run_id, [_agent_alert()])
    report = _report(client, run_id, "json")
    assert [e["alert_external_id"] for e in report["additional_detections"]] == ["1502466"]
    assert report["unattributed_alerts"] == []
    assert report["coverage"]["total"] == 1 and report["coverage"]["observed"] == 0


def test_an_alert_name_cannot_break_the_report_table(client, run_id):
    """The name is the tenant's text. A pipe or a link in it stays a cell."""
    flow.observe(client, run_id, [_agent_alert(
        "evil", "Malware | injected [link](http://x.invalid) <b>cell</b>")])
    section = _report(client, run_id).split(HEADING, 1)[1]
    row = next(line for line in section.splitlines() if "injected" in line)
    assert row.count(" | ") == 5                       # six cells, as the header has
    assert "\\|" in row and "\\[link\\]" in row and "\\<b\\>" in row


# ── a BIOC row credited by an Analytics BIOC ────────────────────────────────

def test_a_cross_label_credit_says_how_it_was_made(client, run_id):
    """The row expects a BIOC on T1003; the tenant raised an Analytics BIOC
    on T1003. Credited, and every surface says by what."""
    summary = flow.observe(client, run_id, [
        _alert("1502502", "Credential file read by an uncommon process",
               "XDR Analytics BIOC", ["T1003"])]).json()
    assert summary["observed"] == 1
    verdict = summary["verdicts"][0]
    assert verdict["matched_on"] == ["technique", "cross-family"]
    assert verdict["matched_via"] == "abioc"
    assert summary["additional_detections"] == []      # a credit, not an extra

    row = flow.rows(client, run_id)["step-01"]
    assert flow.view(row)[:2] == (True, "detected")
    assert row["matched_via"] == "abioc" and row["alert_source"] == "XDR Analytics BIOC"
    assert "credited by an Analytics BIOC" in row["notes"]
    assert "credited by an Analytics BIOC" in _report(client, run_id)


def test_a_same_label_credit_has_no_matched_via(client, run_id):
    flow.observe(client, run_id, [
        _alert("b-1", "Shadow file read by cat", "XDR BIOC", ["T1003"])])
    row = flow.rows(client, run_id)["step-01"]
    assert flow.view(row)[:2] == (True, "detected") and row["matched_via"] is None
    assert "credited by" not in row["notes"]
