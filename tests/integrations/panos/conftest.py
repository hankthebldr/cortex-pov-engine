"""Fixture router for the NGFW preflight: an httpx.MockTransport that answers each
request by its ``cmd`` (op) or ``xpath`` (config) from a fixture file, and records
every call it saw. An unmapped request answers HTTP 500, so a stage that issues a
call nobody planned for fails loudly instead of reading a default."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Union
from urllib.parse import parse_qs

import httpx
import pytest

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "panos"

# A route value is a fixture file name, raw response bytes, an HTTP status (empty
# body), or a callable(request) -> httpx.Response that may also raise.
Route = Union[str, bytes, int, Callable[[httpx.Request], httpx.Response]]


class FakeFirewall:
    def __init__(self, routes: dict[str, Route]):
        self.routes = dict(routes)
        self.calls: list[tuple[str, str]] = []  # (type, cmd-or-xpath)
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def keys(self) -> list[str]:
        return [k for _, k in self.calls]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode())
        rtype = form.get("type", [""])[0]
        key = form.get("cmd", form.get("xpath", [""]))[0]
        self.calls.append((rtype, key))
        self.requests.append(request)
        route = self.routes.get(key)
        if route is None:
            return httpx.Response(500, content=b"unmapped request")
        if callable(route):
            return route(request)
        if isinstance(route, int):
            return httpx.Response(route, content=b"")
        if isinstance(route, bytes):
            return httpx.Response(200, content=route)
        return httpx.Response(200, content=(FIXTURES / route).read_bytes())


@pytest.fixture
def fake_firewall():
    def make(routes: dict[str, Route]) -> FakeFirewall:
        return FakeFirewall(routes)
    return make
