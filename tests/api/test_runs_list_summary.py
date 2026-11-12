"""GET /api/runs is bounded, with a summary projection for the console poll (OPS-06).

The Runs page polls ``GET /api/runs`` every 10 s, and every row carried the
run's WHOLE ``output`` — up to the 32 MiB per-run cap, times every run ever
launched, on every poll. ``?fields=summary`` drops ``output`` and carries
``output_bytes`` plus ``output_tail`` (the last 2 KB) instead; ``limit`` /
``offset`` page the list (default limit 500). Without ``fields`` the row
shape is exactly what it was, so the console can switch its poll at its own
pace.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def client(make_client):
    from api.runs import router

    return make_client(router)


def _seed(session_factory, n=3, output_len=5000):
    from models import Result, Run

    base = datetime(2026, 11, 5, 12, 0, 0)

    async def _go():
        async with session_factory() as db:
            for i in range(n):
                db.add(Run(run_id=f"r-{i}", scenario_id="SIM-EDR-001", mode="pull",
                           status="complete", started_at=base + timedelta(minutes=i),
                           output=(f"run {i} " + "y" * output_len + f" END-{i}")))
                db.add(Result(run_id=f"r-{i}", step_id="step-01", plane="EDR",
                              signal_type="BIOC", expected_detection="d",
                              observed=True, outcome="detected"))
            db.add(Run(run_id="r-none", scenario_id="SIM-EDR-001", mode="push",
                       status="staged", started_at=base - timedelta(days=1), output=None))
            await db.commit()

    asyncio.run(_go())


def test_summary_rows_drop_output_and_carry_size_and_tail(client, session_factory):
    _seed(session_factory)
    r = client.get("/api/runs", params={"fields": "summary"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 4
    assert body["fields"] == "summary"
    assert body["limit"] == 500 and body["offset"] == 0
    rows = {row["run_id"]: row for row in body["runs"]}
    full_len = len("run 2 " + "y" * 5000 + " END-2")
    row = rows["r-2"]
    assert "output" not in row
    assert row["output_bytes"] == full_len
    assert len(row["output_tail"]) == 2048
    assert row["output_tail"].endswith(" END-2")
    # Every other field and the aggregates survive.
    assert row["status"] == "complete" and row["expected_detections"] == 1
    assert row["detected_count"] == 1
    assert rows["r-none"]["output_bytes"] == 0 and rows["r-none"]["output_tail"] == ""


def test_the_default_shape_is_unchanged(client, session_factory):
    _seed(session_factory, n=1, output_len=10)
    body = client.get("/api/runs").json()
    assert set(body) == {"runs", "total"}
    row = next(r for r in body["runs"] if r["run_id"] == "r-0")
    assert row["output"] == "run 0 " + "y" * 10 + " END-0"
    assert "output_bytes" not in row and "output_tail" not in row


def test_limit_and_offset_page_newest_first(client, session_factory):
    _seed(session_factory, n=5, output_len=1)
    page = client.get("/api/runs", params={"fields": "summary", "limit": 2,
                                           "offset": 1}).json()
    assert page["total"] == 6
    assert [r["run_id"] for r in page["runs"]] == ["r-3", "r-2"]
    default = client.get("/api/runs", params={"limit": 2}).json()
    assert [r["run_id"] for r in default["runs"]] == ["r-4", "r-3"]
    assert default["total"] == 6


def test_unknown_fields_value_is_rejected(client):
    assert client.get("/api/runs", params={"fields": "everything"}).status_code == 422
