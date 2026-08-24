"""Task.artifacts — the beacon half of the payload shelf, on the wire.

The Go beacon fetches and verifies; this module proves the CONTRACT it decodes:
the key is omitted when empty (so today's 169 scenarios are byte-identical), it
survives the durable-queue round trip (so a restart cannot strand a task that has
forgotten its tooling), and it is what the capability gate keys on.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


ARTIFACT = {
    "name": "linpeas.sh",
    "sha256": "0ea7e9ce5fcca464b5998cb73930e36647ebf9b38590fe250bbd5664fe8670a5",
    "size_bytes": 1106683,
    "path": "/api/shelf/payload/linpeas.sh",
    "dest": "/tmp/.cache/sysinfo.sh",
    "mode": "0755",
    "steps": ["step-05"],
}


@pytest.fixture
def session_factory() -> async_sessionmaker:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _init() -> None:
        from database import Base
        import models  # noqa: F401

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    return SessionLocal


def _task(**kw):
    from engine.orchestrator import Task

    base = dict(
        task_id="t-1", run_id="r-1", scenario_id="SIM-EDR-999",
        steps=[{"id": "step-05", "command": "bash /tmp/.cache/sysinfo.sh"}],
        identity_context=None,
    )
    base.update(kw)
    return Task(**base)


# ---------------------------------------------------------------------------
# The key is absent unless there is something to say
# ---------------------------------------------------------------------------


def test_task_without_artifacts_emits_no_artifacts_key():
    """Byte-identical to what shipped before staging existed.

    A task for any of today's 169 scenarios must not grow a key: a new beacon
    reading `artifacts: []` and a new beacon reading nothing behave the same, but
    the DURABLE payloads in queued_tasks and every fixture asserting on the wire
    shape would churn for no benefit.
    """
    payload = _task().to_dict()
    assert "artifacts" not in payload
    assert set(payload) == {
        "task_id", "run_id", "scenario_id", "steps",
        "identity_context", "identity_default", "created_at",
    }


def test_task_with_artifacts_emits_them_verbatim():
    payload = _task(artifacts=[ARTIFACT]).to_dict()
    assert payload["artifacts"] == [ARTIFACT]
    # The beacon joins `path` onto its OWN ServerURL. An absolute URL here would
    # let task data redirect the fetch to an origin the operator never named.
    assert payload["artifacts"][0]["path"].startswith("/")
    assert "://" not in payload["artifacts"][0]["path"]


def test_task_payload_is_json_serialisable():
    """queued_tasks.payload is a JSON column; a non-serialisable artifact entry
    would only fail at persist time, after the Run row exists."""
    json.dumps(_task(artifacts=[ARTIFACT]).to_dict())


def test_requires_artifact_fetch_is_the_gate_predicate():
    assert _task().requires_artifact_fetch() is False
    assert _task(artifacts=[]).requires_artifact_fetch() is False
    assert _task(artifacts=[ARTIFACT]).requires_artifact_fetch() is True


# ---------------------------------------------------------------------------
# The durable round trip
# ---------------------------------------------------------------------------


def test_task_from_payload_reads_artifacts_back():
    """Without this, a SimCore restart rehydrates a task that has FORGOTTEN its
    tooling. The beacon would then run every step without the tool it needs and
    report green — the manufactured false negative arriving by a side door."""
    from engine.orchestrator import _task_from_payload

    restored = _task_from_payload(_task(artifacts=[ARTIFACT]).to_dict())
    assert restored is not None
    assert restored.artifacts == [ARTIFACT]


def test_task_from_payload_without_artifacts_is_an_empty_list():
    from engine.orchestrator import _task_from_payload

    restored = _task_from_payload(_task().to_dict())
    assert restored is not None
    assert restored.artifacts == []
    assert restored.requires_artifact_fetch() is False


def test_artifacts_survive_persist_and_rehydrate(session_factory):
    """Full durable round trip through the queued_tasks table."""
    from engine.orchestrator import Orchestrator

    async def _run():
        orch = Orchestrator()
        task = _task(artifacts=[ARTIFACT])
        async with session_factory() as db:
            orch._enqueue("agent-1", task)
            await orch._persist_task(db, "agent-1", task)

            from models import QueuedTask

            row = (await db.execute(select(QueuedTask))).scalar_one()
            assert row.payload["artifacts"] == [ARTIFACT]

        # A restart: brand-new orchestrator with an empty in-memory queue.
        orch2 = Orchestrator()
        async with session_factory() as db:
            await orch2.rehydrate(db)
            delivered = await orch2.dequeue_for_agent("agent-1", db)
            assert delivered is not None
            assert delivered.artifacts == [ARTIFACT]
            assert delivered.requires_artifact_fetch() is True

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# The projection — Python producer vs Go consumer
# ---------------------------------------------------------------------------
#
# These two halves were built by separate tracks and shipped disagreeing:
# ``Composition.to_dict()`` emits ``url``/``dest_path``; the beacon's
# ``Artifact`` struct unmarshals ``path``/``dest``. Feeding one into the other
# made the beacon refuse every artifact with ARTIFACT_SPEC_INVALID. Nothing
# could catch it because the only caller that joins the halves did not exist.
# Parse the Go source rather than restating the field names here — a test that
# hard-codes the contract drifts with the same freedom the code did.


def _go_artifact_json_tags() -> set[str]:
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "agent" / "beacon" / "artifact.go"
    body = re.search(r"type Artifact struct \{(.*?)\n\}", src.read_text(), re.S)
    assert body, "agent/beacon/artifact.go no longer declares `type Artifact struct`"
    tags = set()
    for tag in re.findall(r'json:"([^"]+)"', body.group(1)):
        name = tag.split(",")[0]
        if name and name != "-":
            tags.add(name)
    return tags


def test_beacon_projection_matches_go_struct_tags():
    """Every key the projection emits is a field the beacon actually reads."""
    from engine.payload_shelf import (
        ComposedArtifact, Composition, BEACON_PATH_PREFIX,
    )

    art = ComposedArtifact(
        name="linpeas.sh", sha256="a" * 64, size_bytes=1234,
        url="http://simcore:8888/api/shelf/payload/linpeas.sh",
        dest_path="/tmp/.cache/sysinfo.sh", dest_basename="sysinfo.sh",
        mode="0755", kind="file", renamed=True, adapter_id="TOOL-LINPEAS",
        origin="adapter_ref", source_url="https://example.invalid/linpeas.sh",
        license="MIT", pinned=True,
    )
    plan = Composition(
        composition_id="cmp_test", scenario_id="SIM-EDR-022", consumer="beacon",
        server_url="http://simcore:8888", artifacts=(art,),
        unstaged_adapters=(), warnings=(),
    )
    emitted = plan.to_beacon_artifacts()
    assert len(emitted) == 1
    keys = set(emitted[0])
    go_tags = _go_artifact_json_tags()

    unknown = keys - go_tags
    assert not unknown, (
        f"projection emits {sorted(unknown)}, which agent/beacon/artifact.go "
        f"does not unmarshal — the beacon would silently ignore them"
    )
    # The two the beacon hard-refuses on if absent.
    assert {"path", "dest", "name", "sha256"} <= keys


def test_beacon_projection_path_is_server_relative():
    """A task must not be able to point a target host at another server.

    The beacon joins ``path`` onto the ServerURL it was ENROLLED against, so a
    relative path is the control. An absolute URL here would let whoever can
    write a task choose where a customer's endpoint downloads a binary from.
    """
    from engine.payload_shelf import ComposedArtifact, Composition

    art = ComposedArtifact(
        name="pspy64", sha256="b" * 64, size_bytes=9, url="http://evil.invalid/x",
        dest_path="/tmp/pspy64", dest_basename="pspy64", mode="0755", kind="file",
        renamed=False, adapter_id="TOOL-PSPY", origin="adapter_ref",
        source_url=None, license=None, pinned=True,
    )
    plan = Composition(
        composition_id="cmp_test", scenario_id=None, consumer="beacon",
        server_url="http://simcore:8888", artifacts=(art,),
        unstaged_adapters=(), warnings=(),
    )
    path = plan.to_beacon_artifacts()[0]["path"]
    assert path.startswith("/api/shelf/"), path
    assert "://" not in path
