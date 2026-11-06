"""OPS-05 — a tool install must not freeze the whole server.

``POST /api/tools/{tool}/install`` called ``instantiator.install`` — a blocking
``subprocess.run(..., timeout=600)`` that can be a ten-minute ``cargo build`` —
directly inside an ``async def`` route. That runs on the event loop, so for the
length of the build EVERY other request (the console, ``/api/health``, every
beacon's task poll) waited behind it. A DC who clicked Install mid-POV took the
whole SimCore off the air, and the beacons read it as an outage.

These tests need a SHARED event loop to mean anything: Starlette's TestClient
gives each request its own portal unless it is used as a context manager, in
which case a blocking route on one loop could never delay a request on another.
"""
from __future__ import annotations

import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

SLOW_INSTALL_SECONDS = 1.5


@pytest.fixture
def slow_install(monkeypatch):
    from tools.instantiator import InstallResult, instantiator

    started = threading.Event()

    def _slow(tool_name: str) -> InstallResult:
        started.set()
        time.sleep(SLOW_INSTALL_SECONDS)
        return InstallResult(tool_name=tool_name, success=True,
                             message="built", install_path="/tmp/x")

    monkeypatch.setattr(instantiator, "install", _slow)
    return started


@pytest.fixture
def shared_loop_client(memory_db):
    from api.tools import router
    from database import get_db

    get_db_override, _ = memory_db
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_db] = get_db_override
    with TestClient(app) as client:
        yield client


def test_install_does_not_block_other_requests(shared_loop_client, slow_install):
    results: dict[str, object] = {}

    def _install():
        results["install"] = shared_loop_client.post("/api/tools/xdrtop/install")

    t = threading.Thread(target=_install)
    t.start()
    assert slow_install.wait(5), "the install never started"

    t0 = time.monotonic()
    other = shared_loop_client.get("/api/tools/adapters")
    elapsed = time.monotonic() - t0
    t.join(10)

    assert other.status_code == 200, other.text
    assert elapsed < SLOW_INSTALL_SECONDS * 0.6, (
        f"an unrelated request waited {elapsed:.2f}s behind a "
        f"{SLOW_INSTALL_SECONDS}s install — the build is running on the event loop"
    )
    assert results["install"].status_code == 200, results["install"].text


def test_a_concurrent_install_is_refused_with_409(shared_loop_client, slow_install):
    results: dict[str, object] = {}

    def _install():
        results["first"] = shared_loop_client.post("/api/tools/xdrtop/install")

    t = threading.Thread(target=_install)
    t.start()
    assert slow_install.wait(5), "the install never started"

    second = shared_loop_client.post("/api/tools/signalbench/install")
    t.join(10)

    assert second.status_code == 409, second.text
    detail = second.json()["detail"]
    assert detail["code"] == "INSTALL_IN_PROGRESS"
    assert "xdrtop" in detail["detail"]
    assert results["first"].status_code == 200

    # The slot is released afterwards — a refused second install is not a
    # permanently wedged one.
    third = shared_loop_client.post("/api/tools/signalbench/install")
    assert third.status_code == 200, third.text


def test_the_slot_is_released_when_install_raises(shared_loop_client, monkeypatch):
    from tools.instantiator import instantiator

    def _boom(tool_name):
        raise RuntimeError("disk full")

    monkeypatch.setattr(instantiator, "install", _boom)
    with pytest.raises(RuntimeError):
        shared_loop_client.post("/api/tools/xdrtop/install")

    from tools.instantiator import InstallResult

    monkeypatch.setattr(
        instantiator, "install",
        lambda name: InstallResult(tool_name=name, success=True, message="ok"),
    )
    again = shared_loop_client.post("/api/tools/xdrtop/install")
    assert again.status_code == 200, again.text
