"""Guards for /api/collector-targets.

Targets are instance configuration: they survive a SimCore restart, their key
lives in the encrypted vault, and no response ever carries the key.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

KEY = "xsiam-collector-key-must-not-leak-41c0"
URL = "https://api-acme.example.test/logs/v1/event"


@contextmanager
def _app(db_file):
    """One SimCore 'boot' against a SQLite file; leaving the block is a shutdown."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    Session = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _init():
        from database import Base
        import models  # noqa: F401
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    asyncio.run(_init())

    async def _get_db():
        async with Session() as s:
            yield s

    from api import collector_targets as ct_api
    from database import get_db
    app = FastAPI()
    app.include_router(ct_api.router, prefix="/api")
    app.dependency_overrides[get_db] = _get_db
    client = TestClient(app)
    try:
        yield client, app, Session
    finally:
        asyncio.run(engine.dispose())


@pytest.fixture
def db_file(tmp_path):
    return tmp_path / "simcore.db"


def _put(client, name="okta-hec", **over):
    body = {"kind": "xsiam_http_collector", "url": URL, "dataset": "okta_sso_raw",
            "plugins": ["idp_signin_emulator"], "token": KEY}
    body.update(over)
    return client.put(f"/api/collector-targets/{name}", json=body)


def test_empty_instance_reads_degraded_not_ok(db_file):
    with _app(db_file) as (c, _, _s):
        body = c.get("/api/collector-targets").json()
    assert body["count"] == 0
    assert body["status"] == "degraded" and body["code"] == "NO_COLLECTOR_TARGETS"
    assert "idp_signin_emulator" in body["unbound_plugins"]


def test_create_then_read_never_returns_the_key(db_file):
    with _app(db_file) as (c, _, _s):
        created = _put(c)
        listed = c.get("/api/collector-targets")
        one = c.get("/api/collector-targets/okta-hec")
    for resp in (created, listed, one):
        assert resp.status_code == 200, resp.text
        assert KEY not in resp.text
    assert one.json()["has_key"] is True
    assert "idp_signin_emulator" not in listed.json()["unbound_plugins"]


def test_key_is_stored_encrypted_in_the_vault(db_file):
    with _app(db_file) as (c, _, Session):
        _put(c)

        async def _rows():
            from models import Secret
            async with Session() as s:
                return (await s.execute(select(Secret))).scalars().all()
        secrets = asyncio.run(_rows())
    assert len(secrets) == 1
    assert secrets[0].name == "collector-target:okta-hec"
    assert KEY not in secrets[0].ciphertext


def test_targets_and_keys_survive_a_restart(db_file):
    with _app(db_file) as (c, _, _s):
        assert _put(c).status_code == 200
    # New engine, new app, same file: a SimCore restart inside the POV.
    with _app(db_file) as (c, app, _s):
        one = c.get("/api/collector-targets/okta-hec").json()
        assert one["url"] == URL and one["has_key"] is True

        seen = []

        def factory(timeout, verify_tls):
            def handler(req):
                seen.append(req.headers.get("authorization"))
                return httpx.Response(202)
            return httpx.AsyncClient(transport=httpx.MockTransport(handler))

        async def ok_connect(*a):
            return None

        from api import collector_targets as ct_api
        app.dependency_overrides[ct_api.preflight_transports] = lambda: (ok_connect, factory)
        out = c.post("/api/collector-targets/okta-hec/preflight", json={"write_canary": True})
    assert out.status_code == 200, out.text
    assert out.json()["verdict"] == "accepted"
    assert seen == [KEY]  # decrypted with the same master key after the restart
    assert KEY not in out.text


def test_preflight_default_does_not_write(db_file):
    posted = []

    def factory(timeout, verify_tls):
        def handler(req):
            posted.append(req)
            return httpx.Response(202)
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def ok_connect(*a):
        return None

    with _app(db_file) as (c, app, _s):
        _put(c)
        from api import collector_targets as ct_api
        app.dependency_overrides[ct_api.preflight_transports] = lambda: (ok_connect, factory)
        out = c.post("/api/collector-targets/okta-hec/preflight", json={}).json()
        stored = c.get("/api/collector-targets/okta-hec").json()
    assert out["verdict"] == "reachable" and posted == []
    assert stored["last_check"]["verdict"] == "reachable"


def test_changing_the_url_or_key_discards_the_old_check(db_file):
    async def ok_connect(*a):
        return None

    with _app(db_file) as (c, app, _s):
        _put(c)
        from api import collector_targets as ct_api
        app.dependency_overrides[ct_api.preflight_transports] = lambda: (ok_connect, None)
        c.post("/api/collector-targets/okta-hec/preflight", json={})
        _put(c, url="https://api-other.example.test/logs/v1/event")
        stored = c.get("/api/collector-targets/okta-hec").json()
    assert stored["last_check"] is None


def test_omitting_the_key_on_edit_keeps_it(db_file):
    with _app(db_file) as (c, _, _s):
        _put(c)
        body = {"kind": "xsiam_http_collector", "url": URL, "plugins": ["idp_signin_emulator"]}
        c.put("/api/collector-targets/okta-hec", json=body)
        assert c.get("/api/collector-targets/okta-hec").json()["has_key"] is True
        body["clear_token"] = True
        c.put("/api/collector-targets/okta-hec", json=body)
        assert c.get("/api/collector-targets/okta-hec").json()["has_key"] is False


@pytest.mark.parametrize("over,code", [
    ({"kind": "syslog_udp"}, "TARGET_INVALID"),
    ({"url": "ftp://x.example.test/"}, "TARGET_INVALID"),
    ({"plugins": ["c2_http_beacon"]}, "TARGET_PLUGIN_NOT_COLLECTOR"),
    ({"plugins": ["no_such_plugin"]}, "TARGET_PLUGIN_NOT_COLLECTOR"),
    ({"token": KEY, "clear_token": True}, "TARGET_INVALID"),
])
def test_bad_input_is_refused_with_a_code(db_file, over, code):
    with _app(db_file) as (c, _, _s):
        resp = _put(c, **over)
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == code
    assert KEY not in resp.text


def test_bad_name_is_refused(db_file):
    with _app(db_file) as (c, _, _s):
        resp = _put(c, name="Bad_Name")
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "TARGET_NAME_INVALID"


def test_only_one_default_and_the_move_is_reported(db_file):
    with _app(db_file) as (c, _, _s):
        _put(c, name="first", plugins=[], is_default=True)
        moved = _put(c, name="second", plugins=[], is_default=True).json()
        listed = c.get("/api/collector-targets").json()
    assert moved["previous_default"] == "first"
    assert [t["name"] for t in listed["targets"] if t["is_default"]] == ["second"]


def test_two_targets_binding_one_plugin_is_refused_at_write(db_file):
    with _app(db_file) as (c, _, _s):
        _put(c, name="okta-a")
        resp = _put(c, name="okta-b")
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "TARGET_PLUGIN_ALREADY_BOUND"


def test_delete_removes_the_row_and_its_key(db_file):
    with _app(db_file) as (c, _, Session):
        _put(c)
        assert c.delete("/api/collector-targets/okta-hec").status_code == 204
        assert c.get("/api/collector-targets/okta-hec").status_code == 404

        async def _count():
            from models import Secret
            async with Session() as s:
                return len((await s.execute(select(Secret))).scalars().all())
        assert asyncio.run(_count()) == 0
