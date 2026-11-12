"""Run output is bounded (SEC-14 / OPS-06).

``POST /api/runs/{id}/output`` appended whatever arrived, as fast as it
arrived, with no limit per chunk or per run. Any caller that can reach the
route (a misbehaving beacon, a runaway step printing in a loop, anything on
the lab network) could grow one ``runs.output`` TEXT cell without bound — and
``GET /api/runs`` returns every run's whole output on the console's 10 s poll.

Now:

* one chunk stores at most 1 MiB of ordinary text. A bigger chunk is CUT, not
  refused: the beacon sends one chunk per step and never caps it, so a 422 lost
  the step's ``=== STEP`` header, its ``!! IDENTITY NOT HONOURED`` and its
  step-start record together. The cut keeps every step frame and integrity
  line and says ``[OUTPUT CHUNK TRUNCATED: N of M bytes kept ...]``;
* one run's output is at most 32 MiB. Past the cap only INTEGRITY lines are
  still accepted — ``!! `` markers (``!! IDENTITY NOT HONOURED``,
  ``!! TOOL_ABSENT``, ...), ``__CORTEXSIM`` sentinels and ``cortexsim-`` tags,
  plus the step frame that attributes them — because those are what the POV
  report's execution-integrity check reads, and dropping them would turn "this
  run did not execute as authored" into an ordinary-looking run;
  ``[OUTPUT TRUNCATED at N bytes]`` is appended exactly once;
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


_HEADER = "=== STEP 2/3 · step-02 · T1003.001 · identity=svc-backup ==="
_IDENTITY = ("!! IDENTITY NOT HONOURED: windows has no credential-free impersonation; "
             "ran as the beacon's own account")
_LATE_MARKER = "!! RUNTIME_DEPENDENCY_MISSING: python3"
_FOOTER = "--- exit_code=0 duration=93.4s ---"


def _step_chunk(noise_bytes: int) -> str:
    """One step's /output exactly as the beacon formats it
    (agent/beacon/client.go::formatStepOutput): header, identity marker,
    the step's stdout, the exit footer — all in ONE chunk."""
    noise = "linpeas: noise line\n" * (noise_bytes // 20)
    return (f"{_HEADER}\n{_IDENTITY}\n--- STDOUT ---\n{noise}"
            f"{_LATE_MARKER}\n{_FOOTER}")


def _step_rows(session_factory):
    from models import StepOutcome

    async def _rows():
        async with session_factory() as db:
            return (await db.execute(select(StepOutcome))).scalars().all()

    return asyncio.run(_rows())


def test_an_oversized_chunk_keeps_its_step_header_and_integrity_lines(client, session_factory):
    """The beacon sends ONE chunk per step and never caps it, so a 1.5 MiB step
    (linpeas, pspy, a find sweep) used to be refused whole with a 422: its
    `=== STEP` header, its `!! IDENTITY NOT HONOURED` and its step start were
    all lost, and a step whose identity was not honoured read in the report as
    an ordinary miss. Now the chunk is accepted, cut to the per-chunk bound, and
    keeps every frame and integrity line."""
    from api.runs import _identity_degraded_steps

    _seed(session_factory)
    chunk = _step_chunk(int(1.5 * MIB))
    assert len(chunk.encode("utf-8")) > 1.4 * MIB
    r = _post(client, chunk, step_id="step-02")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["chunk_truncated"] is True
    assert body["offered_bytes"] == len(chunk.encode("utf-8"))
    assert body["accepted_bytes"] <= MIB

    out = _output(session_factory)
    assert len(out.encode("utf-8")) <= MIB
    for line in (_HEADER, _IDENTITY, _LATE_MARKER, _FOOTER):
        assert line in out, line
    assert re.search(r"\[OUTPUT CHUNK TRUNCATED: \d+ of \d+ bytes kept", out), out[-600:]
    # The run-cap marker is a different statement and is not written.
    assert "[OUTPUT TRUNCATED at" not in out
    # The order is preserved: the header still attributes the marker.
    assert out.index(_HEADER) < out.index(_IDENTITY) < out.index(_LATE_MARKER)
    assert _identity_degraded_steps(out) == ["step-02"]
    # And the step start — host evidence age_reference reads — is recorded.
    rows = _step_rows(session_factory)
    assert [(r.step_id, r.started_at is not None) for r in rows] == [("step-02", True)]


def test_an_oversized_multibyte_chunk_is_cut_on_a_character_boundary(client, session_factory):
    """The bound is bytes on the wire, not characters: 300 Ki of a 4-byte
    character is 1.2 MiB. The cut never splits a character."""
    _seed(session_factory)
    r = _post(client, "\U0001F600" * (300 * 1024))
    assert r.status_code == 200, r.text
    assert r.json()["chunk_truncated"] is True
    out = _output(session_factory)
    raw = out.encode("utf-8")  # would raise on a lone surrogate
    assert len(raw) <= MIB
    kept = out.split("\n", 1)[0]
    assert kept and set(kept) == {"\U0001F600"}


def test_a_chunk_at_the_limit_is_accepted_whole(client, session_factory):
    _seed(session_factory)
    r = _post(client, "B" * MIB)
    assert r.status_code == 200
    assert "chunk_truncated" not in r.json()
    assert _output(session_factory) == "B" * MIB


def test_past_the_run_cap_a_step_header_still_attributes_its_marker(
        client, session_factory, monkeypatch):
    """Past the run cap only integrity lines used to survive, so the marker of
    a step whose header was dropped read as `(unattributed)` in the report."""
    from api import runs
    from api.runs import _identity_degraded_steps

    monkeypatch.setattr(runs, "RUN_OUTPUT_CAP_BYTES", 1000, raising=False)
    _seed(session_factory)
    assert _post(client, "x" * 900 + "\n").status_code == 200
    assert _post(client, _step_chunk(2000), step_id="step-02").json()["truncated"] is True

    out = _output(session_factory)
    assert _HEADER in out and _IDENTITY in out and _FOOTER in out
    assert "linpeas: noise line" not in out
    assert _identity_degraded_steps(out) == ["step-02"]


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
