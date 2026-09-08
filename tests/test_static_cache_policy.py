"""The console's static mount must state a cache policy, not infer one.

Bare `StaticFiles` sends no `Cache-Control`, which hands the browser RFC 9111
heuristic caching: with no explicit freshness a response may be served from
cache WITHOUT revalidating. `index.html` keeps its name across every deploy
and a rebuild replaces `core/static` wholesale, so a heuristically-fresh
`index.html` keeps requesting the previous build's hashed chunks — which no
longer exist. The symptom is a console that renders the OLD app on a SimCore
serving the new one, with nothing in the failure that names a cache.

These assert the two halves separately because they want opposite policies
and getting either backwards is silent: an `immutable` index.html pins the
old app for a year, and a `no-cache` assets/ re-validates every chunk on
every load.
"""
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    import main  # noqa: PLC0415  — app import is the thing under test

    static_dir = main._static_dir
    if not os.path.isdir(static_dir):
        pytest.skip(f"no built console at {static_dir} — run `npm run build` first")
    with TestClient(main.app) as c:
        yield c


def _asset_urls(html: str):
    import re

    return re.findall(r'(?:src|href)="(/?assets/[^"]+)"', html)


def test_index_html_is_revalidated_every_load(client):
    r = client.get("/")
    assert r.status_code == 200
    cc = r.headers.get("cache-control", "")
    assert "no-cache" in cc, (
        "index.html carried Cache-Control %r — without no-cache the browser may "
        "serve a stale copy that points at deleted chunk hashes" % cc
    )
    # no-store would also defeat the back button; we deliberately do not send it.
    assert "no-store" not in cc


def test_hashed_assets_are_immutable(client):
    urls = _asset_urls(client.get("/").text)
    assert urls, "built index.html referenced no /assets/ URLs — bundle looks wrong"
    for url in urls[:4]:
        r = client.get(url if url.startswith("/") else "/" + url)
        assert r.status_code == 200, url
        cc = r.headers.get("cache-control", "")
        assert "immutable" in cc and "max-age=31536000" in cc, (
            "%s carried Cache-Control %r — content-hashed assets should be "
            "cacheable forever" % (url, cc)
        )


def test_index_still_revalidates_to_304(client):
    """no-cache must cost a conditional request, not a re-download."""
    first = client.get("/")
    etag = first.headers.get("etag")
    assert etag, "index.html sent no ETag — no-cache would then force a full re-fetch"
    second = client.get("/", headers={"If-None-Match": etag})
    assert second.status_code == 304
