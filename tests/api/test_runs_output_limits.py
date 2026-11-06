"""Run output is bounded (SEC-14 / OPS-06).

``POST /api/runs/{id}/output`` appended whatever arrived, as fast as it
arrived, with no limit per chunk or per run. Any caller that can reach the
route (a misbehaving beacon, a runaway step printing in a loop, anything on
the lab network) could grow one ``runs.output`` TEXT cell without bound — and
``GET /api/runs`` returns every run's whole output on the console's 10 s poll.

Now:

* one chunk is at most 1 MiB (422 past it, nothing appended);
* one run's output is at most 32 MiB. Past the cap only INTEGRITY lines are
  still accepted — ``!! `` markers (``!! IDENTITY NOT HONOURED``,
  ``!! TOOL_ABSENT``, ...), ``__CORTEXSIM`` sentinels and ``cortexsim-`` tags —
  because those are what the POV report's execution-integrity check reads, and
  dropping them would turn "this run did not execute as authored" into an
  ordinary-looking run; ``[OUTPUT TRUNCATED at N bytes]`` is appended exactly
  once;
* a manual observations batch is at most 5000 alerts.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime

import pytest
from sqlalchemy import select

MIB = 1 << 20


@pytest.fixture
def client(make_client):
    from api.connectors import runs_reconcile_router
    from api.runs import router as runs_router

    return make_client(runs_router, runs_reconcile_router)


def _seed(session_factory, run_id="out-1"):
    from models import Run

    async def _go():
        async with session_factory() as db:
            db.add(Run(run_id=run_id, scenario_id="SIM-EDR-001", mode="pull",
                       target="agent-1", status="running", open_tasks=1,
                       started_at=datetime.utcnow(), delivered_at=datetime.utcnow()))
            await db.commit()

    asyncio.run(_go())
    return run_id


def _output(session_factory, run_id="out-1") -> str:
    from models import Run

    async def _go():
        async with session_factory() as db:
            return (await db.execute(
                select(Run.output).where(Run.run_id == run_id))).scalar_one() or ""

    return asyncio.run(_go())


def _post(client, text, run_id="out-1", step_id=None):
    body = {"output": text}
    if step_id:
        body["step_id"] = step_id
    return client.post(f"/api/runs/{run_id}/output", json=body)


def test_the_documented_limits():
    from api import runs

    assert runs.RUN_OUTPUT_CHUNK_MAX_BYTES == MIB
    assert runs.RUN_OUTPUT_CAP_BYTES == 32 * MIB


def test_an_oversized_chunk_is_refused_and_nothing_is_appended(client, session_factory):
    _seed(session_factory)
    r = _post(client, "A" * (MIB + 1))
    assert r.status_code in (413, 422), r.status_code
    assert _output(session_factory) == ""


def test_a_chunk_over_the_byte_limit_in_multibyte_text_is_refused(client, session_factory):
    """The limit is bytes on the wire, not characters: 300 Ki of a 4-byte
    character is 1.2 MiB."""
    _seed(session_factory)
    r = _post(client, "\U0001F600" * (300 * 1024))
    assert r.status_code in (413, 422), r.status_code
    assert _output(session_factory) == ""


def test_a_chunk_at_the_limit_is_accepted(client, session_factory):
    _seed(session_factory)
    assert _post(client, "B" * MIB).status_code == 200
    assert len(_output(session_factory)) == MIB


def test_past_the_run_cap_only_integrity_lines_land_and_the_marker_is_written_once(
        client, session_factory, monkeypatch):
    from api import runs

    monkeypatch.setattr(runs, "RUN_OUTPUT_CAP_BYTES", 1000, raising=False)
    _seed(session_factory)

    assert _post(client, "x" * 900 + "\n").status_code == 200
    # This chunk does not fit: its ordinary lines are dropped, its integrity
    # lines are kept, and the truncation marker is written.
    r = _post(client, "ordinary noise 1\n"
                      "!! IDENTITY NOT HONOURED: could not impersonate www-data\n"
                      "more noise\n"
                      "__CORTEXSIM_STEP_END__ step-02\n", step_id="step-02")
    assert r.status_code == 200
    assert r.json().get("truncated") is True
    # And again: still truncated, the marker is NOT repeated.
    assert _post(client, "noise\ncortexsim-tag: kept\n!! TOOL_ABSENT: nmap\n").status_code == 200

    out = _output(session_factory)
    assert len(re.findall(r"\[OUTPUT TRUNCATED at \d+ bytes\]", out)) == 1, out[-400:]
    assert "!! IDENTITY NOT HONOURED: could not impersonate www-data" in out
    assert "__CORTEXSIM_STEP_END__ step-02" in out
    assert "cortexsim-tag: kept" in out
    assert "!! TOOL_ABSENT: nmap" in out
    assert "ordinary noise 1" not in out and "more noise" not in out and "\nnoise\n" not in out
    # The marker comes before the integrity lines that arrived after it.
    assert out.index("[OUTPUT TRUNCATED at") < out.index("!! IDENTITY NOT HONOURED")


def test_the_step_start_is_still_recorded_past_the_cap(client, session_factory, monkeypatch):
    """Host evidence is not output: a step's first /output still stamps its
    StepOutcome start even when its text no longer fits."""
    from api import runs
    from models import StepOutcome

    monkeypatch.setattr(runs, "RUN_OUTPUT_CAP_BYTES", 10, raising=False)
    _seed(session_factory)
    _post(client, "0123456789")
    _post(client, "does not fit\n", step_id="step-03")

    async def _rows():
        async with session_factory() as db:
            return (await db.execute(select(StepOutcome))).scalars().all()

    rows = asyncio.run(_rows())
    assert [r.step_id for r in rows] == ["step-03"]
    assert rows[0].started_at is not None


def test_output_for_an_unknown_run_is_still_a_404(client):
    r = _post(client, "hello", run_id="nope")
    assert r.status_code == 404


def test_an_observations_batch_is_bounded(client, session_factory):
    _seed(session_factory)
    alert = {"name": "x", "technique": "T1003", "observed_at": "2026-11-05T12:00:00Z"}
    r = client.post("/api/runs/out-1/observations",
                    json={"observations": [alert] * 5001})
    assert r.status_code == 422, r.status_code
    ok = client.post("/api/runs/out-1/observations",
                     json={"observations": [alert] * 5000})
    assert ok.status_code == 200, ok.text
