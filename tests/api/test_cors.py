"""SEC-07 — the operator API is same-origin by default, CORS is opt-in.

History. ``core/main.py`` mounted ``CORSMiddleware(allow_origins=["*"])``. The
earlier fix in this file made that spec-valid (credentials off), which stopped
the reflected-origin footgun but left the real exposure: SimCore has no
authentication, so ``Access-Control-Allow-Origin: *`` let ANY web page a DC
happened to have open read every response from the API — run history, the
credential inventory, scenario content, agent ids — straight into the page's
JavaScript.

Nothing needs it. The console is served by SimCore itself and calls the API at
``window.location.origin`` (``ui/src/api/client.js`` BASE_URL), i.e. it is
same-origin and needs no CORS at all; the Vite dev server proxies ``/api`` so it
is same-origin too. So CORS is now OFF unless an operator names the origins that
may read the API in ``CORTEXSIM_CORS_ORIGINS`` (comma list, empty default), and
even then credentials stay off.

What CORS-off does NOT stop: a cross-site *simple* request (a form POST, or a
``fetch`` with ``mode: 'no-cors'``) is still delivered — the browser only
withholds the response. Body-less POST routes therefore remain forgeable from a
hostile page until SEC-01 adds authentication; ``/api/health`` reports that as
``api_auth: API_UNAUTHENTICATED``. The host guard (``test_host_guard.py``) closes
the DNS-rebinding variant, which CORS never did.

RED on base cc26ddf: ``test_foreign_origin_preflight_gets_no_allow_origin`` and
``test_foreign_origin_simple_get_gets_no_allow_origin`` both received
``access-control-allow-origin: *``.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.cors import CORSMiddleware

FOREIGN = "https://evil-or-not-doesnt-matter.example"


@pytest.fixture(scope="module")
def main_client():
    import main

    # localhost: the host guard (SEC-07) accepts it; this file is about CORS.
    return TestClient(main.app, base_url="http://localhost")


def test_cors_is_not_mounted_by_default():
    import main

    mounted = [m for m in main.app.user_middleware if m.cls is CORSMiddleware]
    assert mounted == [], (
        "CORSMiddleware is mounted on main.app with no CORTEXSIM_CORS_ORIGINS set "
        f"— kwargs={[m.kwargs for m in mounted]}"
    )


def test_foreign_origin_preflight_gets_no_allow_origin(main_client):
    resp = main_client.options(
        "/api/scenarios",
        headers={"Origin": FOREIGN, "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in resp.headers, (
        f"a preflight from {FOREIGN} was granted "
        f"{resp.headers.get('access-control-allow-origin')!r}"
    )


def test_foreign_origin_simple_get_gets_no_allow_origin(main_client):
    resp = main_client.get("/api/openapi.json", headers={"Origin": FOREIGN})
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in resp.headers, resp.headers


# ---------------------------------------------------------------------------
# Opt-in
# ---------------------------------------------------------------------------


def test_parse_cors_origins():
    from main import parse_cors_origins

    assert parse_cors_origins("") == []
    assert parse_cors_origins("   ") == []
    assert parse_cors_origins(
        "https://a.example, https://b.example:8443 ,"
    ) == ["https://a.example", "https://b.example:8443"]


def _opted_in_app(origins):
    import main

    app = FastAPI()

    @app.get("/probe")
    def probe():
        return {"ok": True}

    main.install_http_security(app, cors_origins=origins, allowed_hosts=["*"])
    return TestClient(app, base_url="http://localhost")


def test_opt_in_grants_only_the_named_origin():
    client = _opted_in_app(["https://console.example"])

    ok = client.get("/probe", headers={"Origin": "https://console.example"})
    assert ok.headers.get("access-control-allow-origin") == "https://console.example"
    assert ok.headers.get("access-control-allow-credentials") is None

    foreign = client.get("/probe", headers={"Origin": FOREIGN})
    assert "access-control-allow-origin" not in foreign.headers

    pre = client.options("/probe", headers={
        "Origin": FOREIGN, "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in pre.headers


def test_opt_in_keeps_credentials_off():
    import main

    app = FastAPI()
    main.install_http_security(app, cors_origins=["https://console.example"],
                               allowed_hosts=["*"])
    [cors] = [m for m in app.user_middleware if m.cls is CORSMiddleware]
    assert cors.kwargs["allow_credentials"] is False
    assert cors.kwargs["allow_origins"] == ["https://console.example"]


def test_host_guard_is_outermost():
    """A rebinding request must be refused before any other middleware —
    including CORS — gets to answer it."""
    import main
    from security.host_guard import HostGuardMiddleware

    app = FastAPI()
    main.install_http_security(app, cors_origins=["https://console.example"],
                               allowed_hosts=[])
    assert app.user_middleware[0].cls is HostGuardMiddleware


# ---------------------------------------------------------------------------
# Kept from the earlier pass: the reflected-origin footgun, reproduced in
# isolation. It is why credentials stay off even when CORS is opted into.
# ---------------------------------------------------------------------------


def _preflight_response(*, allow_credentials: bool, origin: str):
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    async def homepage(request):  # noqa: ANN001, ARG001
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/probe", homepage)])
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return TestClient(app).get("/probe", headers={"Origin": origin})


def test_cors_wildcard_plus_credentials_reflects_the_caller_origin():
    """Starlette never sends the literal ``*`` + ``credentials: true`` pair; it
    REFLECTS the caller's Origin and marks credentials allowed for it — every
    origin is individually trusted. Documented so nobody re-enables it."""
    for origin in (FOREIGN, "https://a-completely-different-origin.example"):
        resp = _preflight_response(allow_credentials=True, origin=origin)
        assert resp.headers.get("access-control-allow-origin") == origin
        assert resp.headers.get("access-control-allow-credentials") == "true"
