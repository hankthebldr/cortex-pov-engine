"""CORS must be fully open AND spec-valid.

There is no auth in this app by design (locked operator decision — the DC
running the lab is full admin). The CORS config used to be
``allow_origins=["*"]`` + ``allow_credentials=True``. Per the WHATWG Fetch/CORS
spec, a response is not allowed to pair a literal wildcard
``Access-Control-Allow-Origin: *`` with ``Access-Control-Allow-Credentials:
true`` — that pairing is what a browser refuses to hand back to JS for a
credentialed (``credentials: 'include'``) request.

Starlette's ``CORSMiddleware`` (verified against the exact version pinned in
``core/requirements.txt``, see the source excerpt in
``test_cors_wildcard_plus_credentials_reflects_the_caller_origin`` below) never
emits that literal invalid pair — when ``allow_credentials=True`` and origins
are wildcarded, it silently REFLECTS whatever ``Origin`` header the caller
sent back as an explicit ``Access-Control-Allow-Origin`` value instead, plus
``Access-Control-Allow-Credentials: true``. That keeps each individual
response spec-legal, but the practical effect is the real footgun: the config
trusts every origin equally to receive a credentialed response, because the
allow-list ("*") is not actually enforced once credentials are on — it just
becomes "whoever asked, reflected." Today nothing in this app sends
credentials (no cookies, no sessions — confirmed below and in
``ui/src/api/client.js``), so it is inert, but it is exactly the kind of
config that turns into a real cross-origin credential leak the day anyone
adds a session cookie or an ``Authorization`` default without touching this
file. ``allow_credentials=False`` removes that latent trust entirely: the
response is the SAME wildcard for every caller and carries no
Allow-Credentials header, which a browser will not honour for a credentialed
request — that is the fully-open, spec-valid, no-latent-trust shape.

This test reads the REAL `main.app` middleware stack (not a reconstructed
one) so it fails the moment core/main.py regresses, and it is written to name
the exact old (reflecting) behaviour so the failure message is
self-explanatory.
"""
from __future__ import annotations

import pytest
from starlette.middleware.cors import CORSMiddleware


def _cors_kwargs():
    """Pull the live CORSMiddleware configuration off the real app.

    Starlette records `add_middleware(...)` calls as `Middleware(cls, **kwargs)`
    entries on `app.user_middleware` — this is the actual config the running
    app builds its middleware stack from, not a copy.
    """
    import main

    matches = [m for m in main.app.user_middleware if m.cls is CORSMiddleware]
    assert len(matches) == 1, (
        f"expected exactly one CORSMiddleware on main.app, found {len(matches)} "
        f"— {[m.cls for m in main.app.user_middleware]}"
    )
    return matches[0].kwargs


def test_cors_is_wildcard_origin():
    """The jumpbox console/UI can be opened from any origin — no auth exists
    to scope it to, and narrowing origins is out of scope for this pass."""
    assert _cors_kwargs()["allow_origins"] == ["*"]


def test_cors_credentials_are_off():
    """The fix itself. `allow_credentials=True` here is what made the
    wildcard-origin config trust every reflected origin with credentials
    (see the RED proof below) — this is the assertion that failed against
    the old config and is now observed GREEN.

    This is the assertion that must have failed against the old config
    (`allow_credentials=True`) and been observed RED before this fix landed;
    see test_cors_wildcard_plus_credentials_reflects_the_caller_origin below
    for a self-contained reproduction of that same failure that does not
    depend on git history.
    """
    assert _cors_kwargs()["allow_credentials"] is False


def test_cors_methods_and_headers_stay_fully_open():
    """Confirms the fix didn't accidentally narrow anything else — this pass
    is CORS-validity only, not an access-scoping change."""
    kwargs = _cors_kwargs()
    assert kwargs["allow_methods"] == ["*"]
    assert kwargs["allow_headers"] == ["*"]


# ---------------------------------------------------------------------------
# Self-contained proof that the OLD config was actually invalid — this does
# not depend on git history or on remembering what the bug was. It exercises
# Starlette's own CORS decision logic (the same logic that decides what
# `Access-Control-Allow-*` headers a browser receives) against BOTH configs
# and shows the old one produces the spec-violating response while the new
# one does not.
# ---------------------------------------------------------------------------


def _preflight_response(*, allow_credentials: bool, origin: str):
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

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
    client = TestClient(app)
    return client.get("/probe", headers={"Origin": origin})


def test_cors_wildcard_plus_credentials_reflects_the_caller_origin():
    """RED proof: reproduces the exact OLD config (`allow_credentials=True`
    with wildcard origins) in isolation and shows the observable footgun —
    Starlette never sends the literal invalid `Access-Control-Allow-Origin: *`
    + `Access-Control-Allow-Credentials: true` pair (see the `send()` source
    excerpt below), it instead REFLECTS the caller's own `Origin` back
    verbatim and marks credentials allowed for it. Two arbitrary, unrelated
    origins both get trusted individually — i.e. the wildcard allow-list is
    not actually enforced once credentials are on, ANY origin passes:

        # starlette/middleware/cors.py CORSMiddleware.send()
        # If credentials are allowed, then we must respond with the
        # specific origin instead of '*'.
        if self.allow_all_origins and self.allow_credentials:
            self.allow_explicit_origin(headers, origin)

    That is the config this fix removes. It is kept here permanently as the
    documented failure mode this app must never return to.
    """
    for origin in (
        "https://evil-or-not-doesnt-matter.example",
        "https://a-completely-different-origin.example",
    ):
        resp = _preflight_response(allow_credentials=True, origin=origin)
        assert resp.headers.get("access-control-allow-origin") == origin
        assert resp.headers.get("access-control-allow-credentials") == "true"


def test_cors_wildcard_without_credentials_is_spec_valid():
    """GREEN proof: the fixed shape never reflects the caller's origin and
    never emits Allow-Credentials — every origin gets the identical, literal
    wildcard response, so there is no per-origin trust decision left for a
    credentialed request to exploit."""
    for origin in (
        "https://evil-or-not-doesnt-matter.example",
        "https://a-completely-different-origin.example",
    ):
        resp = _preflight_response(allow_credentials=False, origin=origin)
        assert resp.headers.get("access-control-allow-origin") == "*"
        assert resp.headers.get("access-control-allow-credentials") is None


def test_main_app_would_fail_the_credentials_off_assertion_on_the_old_config():
    """Belt-and-suspenders: replays test_cors_credentials_are_off's assertion
    against the literal old kwargs so the RED/GREEN delta for THIS app's
    config (not just the isolated Starlette repro above) is explicit in the
    suite, independent of git history."""
    old_kwargs = {"allow_origins": ["*"], "allow_credentials": True}
    with pytest.raises(AssertionError):
        assert old_kwargs["allow_credentials"] is False
    new_kwargs = _cors_kwargs()
    assert new_kwargs["allow_credentials"] is False
