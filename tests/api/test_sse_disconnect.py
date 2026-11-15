"""An SSE subscription ends when its client goes away (OPS-10).

``api/events.py::_stream`` waited on its subscriber queue for up to the 15 s
keepalive and never asked whether the client was still there. Under uvicorn
(ASGI spec 2.4) Starlette does not listen for ``http.disconnect`` on a
streaming response; it relies on the next ``send`` failing. So a closed tab
kept its subscription — and a 1000-slot queue filling with every run's
output — until the next keepalive frame failed to send, and every
EventSource auto-reconnect on a flapping network added another.

The generator now polls ``request.is_disconnected()`` every second (and after
every event) and breaks; its ``finally`` unsubscribes. This drives a REAL
uvicorn server over a real socket, because TestClient/ASGITransport never
produce a client disconnect mid-stream.
"""
from __future__ import annotations

import socket
import threading
import time

import pytest

#: Well under the 15 s keepalive: on base the subscription survives this long.
RELEASE_DEADLINE_SECONDS = 5.0


@pytest.fixture
def sse_server():
    import uvicorn
    from fastapi import FastAPI

    from api.events import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(
        app, log_level="warning", lifespan="off", timeout_graceful_shutdown=2))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    assert server.started, "uvicorn did not start"
    yield port
    server.should_exit = True
    thread.join(timeout=10)
    sock.close()


def _open_stream(port: int, path: str) -> socket.socket:
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nAccept: text/event-stream\r\n\r\n"
              .encode())
    buf = b""
    deadline = time.time() + 5
    while b": connected" not in buf and time.time() < deadline:
        chunk = s.recv(4096)
        if not chunk:
            break
        buf += chunk
    assert b"200 OK" in buf and b": connected" in buf, buf
    return s


def _wait_for(predicate, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


@pytest.mark.parametrize("path, run_id", [("/api/events", None),
                                          ("/api/runs/sse-run-1/events", "sse-run-1")])
def test_subscription_is_released_when_the_client_disconnects(sse_server, path, run_id):
    from events import event_bus

    before = event_bus.subscriber_count(run_id)
    s = _open_stream(sse_server, path)
    assert _wait_for(lambda: event_bus.subscriber_count(run_id) == before + 1, 2.0)

    s.shutdown(socket.SHUT_RDWR)
    s.close()

    released = _wait_for(lambda: event_bus.subscriber_count(run_id) == before,
                         RELEASE_DEADLINE_SECONDS)
    assert released, (
        f"subscriber still registered {RELEASE_DEADLINE_SECONDS}s after the client "
        f"disconnected (count={event_bus.subscriber_count(run_id)}, before={before})")


def test_a_live_client_keeps_its_subscription(sse_server):
    """The negative control: an open stream is not reaped by the poll."""
    from events import event_bus

    before = event_bus.subscriber_count(None)
    s = _open_stream(sse_server, "/api/events")
    try:
        time.sleep(2.5)  # > two disconnect-poll intervals
        assert event_bus.subscriber_count(None) == before + 1
    finally:
        s.close()
    assert _wait_for(lambda: event_bus.subscriber_count(None) == before,
                     RELEASE_DEADLINE_SECONDS)
