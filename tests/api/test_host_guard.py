"""SEC-07 — the Host guard: DNS rebinding cannot reach the unauthenticated API.

Removing ``Access-Control-Allow-Origin: *`` (test_cors.py) stops a hostile page
from READING the API cross-origin. It does not stop DNS rebinding: the attacker
serves a page from ``rebind.attacker.example``, then re-points that name at the
DC's SimCore (``127.0.0.1`` or the jumpbox's lab address). The browser now
treats SimCore as same-origin with the attacker's page and hands it every
response — no CORS involved, because as far as the browser knows there is no
cross-origin request. The one thing that page cannot forge is the ``Host``
header: it is the attacker's name.

So ``core/security/host_guard.py`` is a pure ASGI middleware that answers 400
``HOST_NOT_ALLOWED`` unless Host is: ``localhost`` / ``127.0.0.1`` / ``::1``,
any IP literal (an IP cannot be rebound), this machine's own hostname or FQDN,
or a name in ``CORTEXSIM_ALLOWED_HOSTS`` (comma list; ``*`` disables the check,
``*.lab.example`` matches subdomains).

Still open, and said so in ``/api/health``: a body-less cross-site POST to an IP
literal is a *simple* request the browser delivers without asking, so routes
that act on POST alone remain forgeable until SEC-01 adds authentication.

RED on base cc26ddf: ``test_main_app_refuses_a_rebinding_host`` got 200 (no
guard existed).
"""
from __future__ import annotations

import asyncio

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient


def _app(allowed_hosts=(), *, hostname="simcore-box", fqdn="simcore-box.lab.example",
         calls=None):
    from security import host_guard

    async def ok(request):  # noqa: ANN001, ARG001
        return PlainTextResponse("ok")

    def _fqdn():
        if calls is not None:
            calls.append("fqdn")
        return fqdn

    inner = Starlette(routes=[Route("/", ok), Route("/api/x", ok, methods=["GET", "POST"])])
    return host_guard.HostGuardMiddleware(
        inner, allowed_hosts=list(allowed_hosts),
        hostname_fn=lambda: hostname, fqdn_fn=_fqdn,
    )


def _get(app, host: str | None, path: str = "/api/x"):
    client = TestClient(app)
    headers = {} if host is None else {"host": host}
    return client.get(path, headers=headers)


@pytest.mark.parametrize("host", [
    "localhost", "localhost:8888", "LOCALHOST:8888", "localhost.",
    "127.0.0.1", "127.0.0.1:8888",
    "[::1]", "[::1]:8888",
    "10.20.30.40:8888", "192.0.2.10", "[fe80::1]:8888", "[2001:db8::5]",
    "simcore-box", "SIMCORE-BOX:8888", "simcore-box.lab.example:8888",
])
def test_local_and_ip_literal_hosts_are_allowed(host):
    resp = _get(_app(), host)
    assert resp.status_code == 200, (host, resp.text)


@pytest.mark.parametrize("host", [
    "rebind.attacker.example",
    "rebind.attacker.example:8888",
    "localhost.attacker.example",
    "127.0.0.1.nip.io",
    "simcore-box.attacker.example",
    "",
    "a b",
    "[::1",
    "evil.example:8888:9",
])
def test_foreign_or_malformed_hosts_are_refused(host):
    resp = _get(_app(), host)
    assert resp.status_code == 400, (host, resp.text)
    body = resp.json()
    assert body["code"] == "HOST_NOT_ALLOWED"
    assert set(body) >= {"error", "code", "detail"}
    assert "CORTEXSIM_ALLOWED_HOSTS" in body["detail"]


def test_post_is_refused_too():
    resp = TestClient(_app()).post("/api/x", headers={"host": "rebind.attacker.example"})
    assert resp.status_code == 400


def test_allowlist_admits_a_named_host_and_its_wildcard():
    app = _app(["simcore.customer.lab", "*.pov.example"])
    assert _get(app, "simcore.customer.lab:8888").status_code == 200
    assert _get(app, "jump01.pov.example").status_code == 200
    assert _get(app, "pov.example").status_code == 400, "wildcard is subdomains only"
    assert _get(app, "rebind.attacker.example").status_code == 400


def test_star_disables_the_check():
    app = _app(["*"])
    assert _get(app, "anything.example").status_code == 200


def test_parse_allowed_hosts():
    from security.host_guard import parse_allowed_hosts

    assert parse_allowed_hosts("") == []
    assert parse_allowed_hosts(" A.example , *.Lab.example ,,") == ["a.example", "*.lab.example"]


def test_cheap_hosts_never_touch_reverse_dns():
    """getfqdn() is a reverse-DNS lookup: on a host with broken DNS it blocks
    for seconds, and on macOS it initialises Network.framework (the fork-crash
    tests/conftest.py documents). It is consulted only for a Host that nothing
    cheaper explains, then cached."""
    calls: list[str] = []
    app = _app(["simcore.customer.lab"], calls=calls)
    for host in ("localhost:8888", "10.0.0.5", "[::1]:8888", "simcore-box",
                 "simcore.customer.lab"):
        assert _get(app, host).status_code == 200
    assert calls == []

    assert _get(app, "rebind.attacker.example").status_code == 400
    assert _get(app, "rebind2.attacker.example").status_code == 400
    assert calls == ["fqdn"], "fqdn is resolved once and cached"


def test_lifespan_scope_passes_through():
    from security import host_guard

    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])

    guard = host_guard.HostGuardMiddleware(inner, hostname_fn=lambda: "h",
                                           fqdn_fn=lambda: "h")
    asyncio.run(guard({"type": "lifespan"}, None, None))
    assert seen == ["lifespan"]


# ---------------------------------------------------------------------------
# The real app
# ---------------------------------------------------------------------------


@pytest.fixture
def no_reverse_dns(monkeypatch):
    try:
        from security import host_guard
    except ImportError:  # base cc26ddf: no guard, so nothing to stub
        return
    monkeypatch.setattr(host_guard, "_default_fqdn", lambda: "simcore-test.invalid")


def test_main_app_refuses_a_rebinding_host(no_reverse_dns):
    import main

    client = TestClient(main.app, base_url="http://rebind.attacker.example:8888")
    resp = client.get("/api/openapi.json")
    assert resp.status_code == 400, (
        "main.app answered a request whose Host is an attacker's DNS name — "
        "a rebinding page can read the whole unauthenticated API"
    )
    assert resp.json()["code"] == "HOST_NOT_ALLOWED"


def test_main_app_accepts_an_ip_literal_and_localhost(no_reverse_dns):
    import main

    for base in ("http://192.0.2.10:8888", "http://localhost:8888", "http://127.0.0.1"):
        resp = TestClient(main.app, base_url=base).get("/api/openapi.json")
        assert resp.status_code == 200, (base, resp.text[:200])


def test_main_app_mounts_the_guard_by_default():
    import main
    from security.host_guard import HostGuardMiddleware

    assert main.app.user_middleware[0].cls is HostGuardMiddleware
