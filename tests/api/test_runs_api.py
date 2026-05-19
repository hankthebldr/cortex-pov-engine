"""Direct router tests for /api/runs (focus on list + detail + report shape).

Launch path is exercised via the smoke harness against a live orchestrator
— here we focus on read endpoints and error paths that don't need the
queue.
"""
from __future__ import annotations

import asyncio
import gzip
import io
import json
import tarfile
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def client(make_client):
    from api.runs import router
    return make_client(router)


@pytest.fixture
def seeded_run(session_factory):
    """Insert one complete Run + Scenario + 2 observed Results.  Returns run_id."""
    from models import Run, Result, Scenario

    async def _seed():
        async with session_factory() as db:
            s = Scenario(
                scenario_id="SIM-EDR-001",
                name="Credential Dumping",
                plane="EDR",
                version="1.0",
                status="active",
                uc_ref="UCS-EDR-01",
                uc_name="Endpoint Credential Theft",
                tc_ref="TC-EDR-01",
                tc_name="Linux Credential Harvest",
                mitre_tactic="TA0006",
                mitre_tactic_name="Credential Access",
                mitre_technique="T1003.008",
                mitre_technique_name="OS Credential Dumping",
                steps=[
                    {"id": "step-01", "name": "Read passwd", "mitre_technique": "T1087.001"},
                    {"id": "step-02", "name": "Read shadow", "mitre_technique": "T1003.008"},
                ],
            )
            db.add(s)

            run = Run(
                run_id="r-1",
                scenario_id="SIM-EDR-001",
                mode="push",
                status="complete",
                started_at=datetime.utcnow() - timedelta(minutes=5),
                completed_at=datetime.utcnow() - timedelta(minutes=2),
            )
            db.add(run)

            for i, (sig, det) in enumerate(
                [("Analytics", "passwd read"), ("BIOC", "shadow read")], start=1
            ):
                db.add(
                    Result(
                        run_id="r-1",
                        step_id=f"step-0{i}",
                        step_name=f"step {i}",
                        plane="EDR",
                        signal_type=sig,
                        expected_detection=det,
                        observed=True,
                        observed_at=datetime.utcnow() - timedelta(minutes=1),
                        executed_at=datetime.utcnow() - timedelta(minutes=3),
                    )
                )
            await db.commit()

    asyncio.get_event_loop().run_until_complete(_seed())
    return "r-1"


def test_list_runs_empty(client):
    r = client.get("/api/runs")
    assert r.status_code == 200
    assert r.json() == {"runs": [], "total": 0}


def test_get_unknown_run_404(client):
    r = client.get("/api/runs/no-such-run")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "RUN_NOT_FOUND"


def test_get_run_detail(client, seeded_run):
    r = client.get(f"/api/runs/{seeded_run}")
    assert r.status_code == 200
    body = r.json()
    assert body["run_id"] == seeded_run
    assert body["status"] == "complete"


def test_report_json_shape(client, seeded_run):
    r = client.get(f"/api/runs/{seeded_run}/report", params={"format": "json"})
    assert r.status_code == 200
    rep = r.json()
    assert rep["run"]["run_id"] == seeded_run
    assert rep["coverage"]["total"] == 2
    assert rep["coverage"]["observed"] == 2
    assert rep["coverage"]["pct"] == 100.0


def test_report_markdown_renders_pov_header(client, seeded_run):
    r = client.get(f"/api/runs/{seeded_run}/report", params={"format": "markdown"})
    assert r.status_code == 200
    body = r.text
    assert "POV Detection Validation Report" in body
    assert "MITRE ATT&CK Mapping" in body
    assert "Detection Coverage Summary" in body
    # Markdown headers for both planes/types appear
    assert "Credential Dumping" in body


def test_report_format_validation(client, seeded_run):
    r = client.get(f"/api/runs/{seeded_run}/report", params={"format": "pdf"})
    assert r.status_code == 422  # Pydantic regex rejects


def test_report_matrix_csv(client, seeded_run):
    r = client.get(f"/api/runs/{seeded_run}/report/matrix")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    # At least one data row beyond the header
    assert r.text.count("\n") >= 2


def test_report_navigator_layer_json(client, seeded_run):
    r = client.get(f"/api/runs/{seeded_run}/report/navigator")
    assert r.status_code == 200
    layer = r.json()
    assert layer["domain"]
    assert layer["techniques"]


def test_report_bundle_is_gzip_tar(client, seeded_run):
    r = client.get(f"/api/runs/{seeded_run}/report/bundle")
    assert r.status_code == 200
    assert r.content[:2] == b"\x1f\x8b"
    # Decompress + verify the expected three artifacts live inside
    with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as tf:
        names = tf.getnames()
    assert any("detection_matrix.csv" in n for n in names)
    assert any("attack_navigator_layer.json" in n for n in names)
    assert any("exec_summary.md" in n for n in names)


def test_run_output_append_appends(client, seeded_run):
    client.post(
        f"/api/runs/{seeded_run}/output",
        json={"output": "line A\n"},
    ).raise_for_status()
    client.post(
        f"/api/runs/{seeded_run}/output",
        json={"output": "line B\n"},
    ).raise_for_status()
    detail = client.get(f"/api/runs/{seeded_run}").json()
    assert "line A" in detail["output"]
    assert "line B" in detail["output"]


def test_run_complete_transitions_status(client, session_factory):
    """Complete with non-zero exit_code → failed; zero → complete."""
    from models import Run

    async def _seed():
        async with session_factory() as db:
            db.add(
                Run(
                    run_id="r-fail",
                    scenario_id="SIM-X",
                    mode="pull",
                    status="running",
                    started_at=datetime.utcnow(),
                )
            )
            await db.commit()

    asyncio.get_event_loop().run_until_complete(_seed())

    r = client.post(
        "/api/runs/r-fail/complete",
        json={"exit_code": 127, "summary": "command not found"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "failed"
    detail = client.get("/api/runs/r-fail").json()
    assert "COMPLETION SUMMARY" in detail["output"]
    assert "127" in detail["output"]
