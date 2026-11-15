"""Live runtime log for pull runs: the server half.

The beacon used to post a step's output once, after the step ended, and never
said which command it ran. ``POST /api/runs/{id}/output`` now takes two more
frame kinds beside today's final per-step frame:

* ``kind: "start"``: the step is about to run; carries the command. Recorded
  in ``step_starts`` and published as ``run.step``. It does not create a
  ``step_outcomes`` row (``StepOutcome.started_at`` means "finished by then").
* ``kind: "chunk"``: output while the step runs. Published as ``run.output``
  with ``live: true`` and kept in an in-memory tail; never stored on
  ``Run.output``.

A frame with no ``kind`` is the final per-step frame and behaves as before.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from sqlalchemy import select


@pytest.fixture
def client(make_client):
    from api.runs import router as runs_router

    return make_client(runs_router)


@pytest.fixture
def published(monkeypatch):
    """Every event handed to the bus, as ``(run_id, event)``."""
    from events import event_bus

    seen: list[tuple[str, dict]] = []

    async def _capture(run_id, event):
        seen.append((run_id, event))

    monkeypatch.setattr(event_bus, "publish", _capture)
    return seen


@pytest.fixture(autouse=True)
def _fresh_tail():
    """The live tail is process memory keyed by run id; each test's database is
    new, so the tail must be too."""
    from engine import live_output

    for run_id in ("lv-1", "lv-a", "lv-b"):
        live_output.tail.drop_run(run_id)
    yield


def _seed(session_factory, run_id="lv-1"):
    from models import Run

    async def _go():
        async with session_factory() as db:
            db.add(Run(run_id=run_id, scenario_id="SIM-EDR-001", mode="pull",
                       target="agent-1", status="running", open_tasks=1,
                       started_at=datetime.utcnow(), delivered_at=datetime.utcnow()))
            await db.commit()

    asyncio.run(_go())
    return run_id


def _output(session_factory, run_id="lv-1") -> str:
    from models import Run

    async def _go():
        async with session_factory() as db:
            return (await db.execute(
                select(Run.output).where(Run.run_id == run_id))).scalar_one() or ""

    return asyncio.run(_go())


def _step_outcome_rows(session_factory, run_id="lv-1") -> list:
    from models import StepOutcome

    async def _go():
        async with session_factory() as db:
            return (await db.execute(
                select(StepOutcome).where(StepOutcome.run_id == run_id))).scalars().all()

    return asyncio.run(_go())


def _events(published, etype):
    return [e for _, e in published if e.get("type") == etype]


FRAME = "=== STEP 1/2 · step-01 · T1059 · identity=root ===\ntick 1\ntick 2\n--- exit_code=0 duration=2s ---\n"


def _start(client, run_id="lv-1", step_id="step-01", command="for i in 1 2; do echo tick $i; sleep 1; done"):
    return client.post(f"/api/runs/{run_id}/output", json={
        "kind": "start", "seq": 0, "step_id": step_id, "output": "",
        "command": command, "identity": "www-data", "index": 1, "total": 2})


def _chunk(client, text, seq, run_id="lv-1", step_id="step-01"):
    return client.post(f"/api/runs/{run_id}/output", json={
        "kind": "chunk", "seq": seq, "step_id": step_id, "output": text})


def test_live_chunk_is_published_not_stored(client, session_factory, published):
    _seed(session_factory)
    r = _chunk(client, "tick 1\n", 1)
    assert r.status_code == 200, r.text

    assert _output(session_factory) == "", "a live chunk must never reach Run.output"
    outs = _events(published, "run.output")
    assert len(outs) == 1, published
    data = outs[0]["data"]
    assert data["chunk"] == "tick 1\n" and data["step_id"] == "step-01"
    assert data["live"] is True and data["seq"] == 1

    body = client.get("/api/runs/lv-1").json()
    assert body["output"] in (None, "")
    assert body["live_tail"] == [
        {"step_id": "step-01", "text": "tick 1\n", "seq": 1, "truncated": False}]
    # A chunk is output, not host evidence.
    assert _step_outcome_rows(session_factory) == []


def test_start_frame_records_command_without_step_outcome(client, session_factory, published):
    _seed(session_factory)
    r = _start(client)
    assert r.status_code == 200, r.text

    assert _step_outcome_rows(session_factory) == [], (
        "a start frame must not create a step_outcomes row: StepOutcome.started_at "
        "means the step had finished by then")
    assert _output(session_factory) == ""

    body = client.get("/api/runs/lv-1").json()
    assert body["step_outcomes"] == []
    starts = body["step_starts"]
    assert [(s["step_id"], s["command"], s["identity"]) for s in starts] == [
        ("step-01", "for i in 1 2; do echo tick $i; sleep 1; done", "www-data")]
    assert starts[0]["started_at"]

    steps = _events(published, "run.step")
    assert len(steps) == 1, published
    data = steps[0]["data"]
    assert data["step_id"] == "step-01" and data["phase"] == "start"
    assert data["command"] == "for i in 1 2; do echo tick $i; sleep 1; done"
    assert data["identity"] == "www-data"
    assert (data["index"], data["total"]) == (1, 2)


def test_final_frame_stores_same_blob_as_today(client, session_factory, published):
    # Run A: a beacon that sends only the final frame (today's behaviour).
    _seed(session_factory, "lv-a")
    plain = client.post("/api/runs/lv-a/output",
                        json={"output": FRAME, "step_id": "step-01", "frame_id": "t:1"})
    # Run B: the same step, with a start frame and live chunks before it.
    _seed(session_factory, "lv-b")
    _start(client, "lv-b")
    _chunk(client, "tick 1\n", 1, "lv-b")
    _chunk(client, "tick 2\n", 2, "lv-b")
    live = client.post("/api/runs/lv-b/output",
                       json={"output": FRAME, "step_id": "step-01", "frame_id": "t:1"})

    assert plain.status_code == 200 and live.status_code == 200, (plain.text, live.text)
    assert plain.json() == {"status": "ok", "run_id": "lv-a"}
    assert live.json() == {"status": "ok", "run_id": "lv-b"}
    assert _output(session_factory, "lv-a") == FRAME
    assert _output(session_factory, "lv-b") == FRAME

    # The final frame is still what stamps the step_outcomes row.
    for run_id in ("lv-a", "lv-b"):
        rows = _step_outcome_rows(session_factory, run_id)
        assert [(r.step_id, r.termination) for r in rows] == [("step-01", None)]
        assert rows[0].started_at is not None

    # ... and it is published exactly as before: no `live` flag on a stored frame.
    stored = [e for rid, e in published
              if rid == "lv-b" and e["type"] == "run.output" and not e["data"].get("live")]
    assert [e["data"] for e in stored] == [{"step_id": "step-01", "chunk": FRAME}]


def test_live_tail_dropped_when_final_frame_arrives(client, session_factory, published):
    _seed(session_factory)
    _start(client)
    _chunk(client, "tick 1\n", 1)
    _chunk(client, "tick 2\n", 2)
    tail = client.get("/api/runs/lv-1").json()["live_tail"]
    assert tail == [{"step_id": "step-01", "text": "tick 1\ntick 2\n", "seq": 2, "truncated": False}]

    client.post("/api/runs/lv-1/output",
                json={"output": FRAME, "step_id": "step-01", "frame_id": "t:1"})
    assert client.get("/api/runs/lv-1").json()["live_tail"] == []

    # A chunk that was already in flight when the step ended does not bring the
    # tail back, and is not published after the stored frame.
    late = _chunk(client, "late\n", 3)
    assert late.status_code == 200, late.text
    assert client.get("/api/runs/lv-1").json()["live_tail"] == []
    assert [e["data"]["chunk"] for e in _events(published, "run.output")][-1] == FRAME

    # The next step streams; the whole tail goes when the run ends.
    _start(client, step_id="step-02", command="id")
    _chunk(client, "uid=0\n", 1, step_id="step-02")
    assert [t["step_id"] for t in client.get("/api/runs/lv-1").json()["live_tail"]] == ["step-02"]

    done = client.post("/api/runs/lv-1/complete",
                       json={"exit_code": 0, "summary": "ok", "steps": []})
    assert done.status_code == 200, done.text
    body = client.get("/api/runs/lv-1").json()
    assert "live_tail" not in body
    # The commands stay readable on the finished run.
    assert [s["step_id"] for s in body["step_starts"]] == ["step-01", "step-02"]
    assert _output(session_factory).startswith(FRAME)


def test_live_tail_keeps_the_last_max_step_bytes(client, session_factory, published):
    from engine import live_output

    _seed(session_factory)
    big = "x" * (live_output.MAX_STEP_BYTES - 4)
    _chunk(client, big, 1)
    _chunk(client, "0123456789", 2)
    tail = client.get("/api/runs/lv-1").json()["live_tail"][0]
    assert len(tail["text"].encode("utf-8")) == live_output.MAX_STEP_BYTES
    assert tail["text"].endswith("0123456789") and tail["truncated"] is True


def test_a_frame_of_a_kind_this_simcore_does_not_know_is_not_stored(client, session_factory, published):
    # A newer beacon may send a kind this build has never heard of. It said it
    # is not a final frame, so it must not land in the stored transcript.
    _seed(session_factory)
    r = client.post("/api/runs/lv-1/output",
                    json={"kind": "heartbeat", "step_id": "step-01", "output": "x\n"})
    assert r.status_code == 200, r.text
    assert r.json()["dropped"] is True
    assert _output(session_factory) == ""
    assert _step_outcome_rows(session_factory) == []
    assert published == []


def test_a_chunk_for_a_finished_run_goes_nowhere(client, session_factory, published):
    _seed(session_factory)
    client.post("/api/runs/lv-1/complete", json={"exit_code": 0, "summary": "ok", "steps": []})
    published.clear()
    r = _chunk(client, "late\n", 1)
    assert r.status_code == 200 and r.json()["dropped"] is True, r.text
    assert published == []
    assert "live_tail" not in client.get("/api/runs/lv-1").json()


def test_live_frame_for_an_unknown_run_is_404(client, session_factory, published):
    r = _chunk(client, "x\n", 1, run_id="nope")
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["code"] == "RUN_NOT_FOUND"
    assert published == []


def test_pull_task_carries_live_output():
    from engine.orchestrator import Task, _task_from_payload

    task = Task(task_id="t-1", run_id="r-1", scenario_id="SIM-EDR-001",
                steps=[{"id": "step-01", "command": "id"}], identity_context=None)
    payload = task.to_dict()
    assert payload["live_output"] == {
        "flush_ms": 1000, "max_chunk_bytes": 16384, "max_step_bytes": 262144}
    # A task restored from the durable queue after a restart still carries it.
    assert _task_from_payload(payload).to_dict()["live_output"] == payload["live_output"]
