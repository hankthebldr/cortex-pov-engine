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


# --- declaration and last_arrival -------------------------------------------

DECL = {"collection": "okta_sso", "fields": ["user", "ip"]}
ARRIVAL = {"state": "arrived", "found": 3, "delivered": 3, "token": "tok-1",
           "checked_at": "2026-11-13T10:00:00Z", "reason": None, "code": None,
           "extra": "must not be kept"}


def _upsert(Session, name="okta-hec", arrival=None, **over):
    """Drive the store directly, as Tasks 4 and 6 will: the PUT takes no declaration yet."""
    from engine import collector_targets as store

    kw = dict(kind="xsiam_http_collector", url=URL, dataset="okta_sso_raw",
              auth_header="Authorization", auth_scheme="", plugins=["idp_signin_emulator"],
              is_default=False, token=None, clear_token=False)
    kw.update(over)

    async def _go():
        async with Session() as s:
            await store.upsert(s, name, **kw)
            if arrival is not None:
                await store.record_arrival(s, name, arrival)
    asyncio.run(_go())


def test_declaration_and_arrival_survive_a_restart(db_file):
    with _app(db_file) as (c, _, Session):
        _put(c)
        _upsert(Session, declaration=DECL, arrival=ARRIVAL)
    with _app(db_file) as (c, _, _s):
        body = c.get("/api/collector-targets/okta-hec").json()
    assert body["declaration"] == DECL
    assert body["last_arrival"] == {k: v for k, v in ARRIVAL.items() if k != "extra"}


def test_a_legacy_row_reads_null(db_file):
    with _app(db_file) as (c, _, _s):
        _put(c)
        body = c.get("/api/collector-targets/okta-hec").json()
    assert body["declaration"] is None
    assert body["last_arrival"] is None


def test_changing_dataset_without_a_declaration_clears_both(db_file):
    with _app(db_file) as (c, _, Session):
        _put(c)
        _upsert(Session, declaration=DECL, arrival=ARRIVAL)
        assert c.get("/api/collector-targets/okta-hec").json()["declaration"] == DECL
        assert _put(c, token=None, dataset="other_raw").status_code == 200
        body = c.get("/api/collector-targets/okta-hec").json()
    assert body["declaration"] is None
    assert body["last_arrival"] is None


def test_an_unchanged_dataset_keeps_the_declaration(db_file):
    with _app(db_file) as (c, _, Session):
        _put(c)
        _upsert(Session, declaration=DECL)
        assert _put(c, token=None).status_code == 200
        body = c.get("/api/collector-targets/okta-hec").json()
    assert body["declaration"] == DECL


def test_a_declaration_given_with_a_new_dataset_is_stored(db_file):
    new = {"collection": "other", "fields": []}
    with _app(db_file) as (c, _, Session):
        _put(c)
        _upsert(Session, declaration=DECL, arrival=ARRIVAL)
        _upsert(Session, dataset="other_raw", declaration=new)
        body = c.get("/api/collector-targets/okta-hec").json()
    assert body["declaration"] == new
    assert body["last_arrival"] is None


def test_changing_the_url_clears_last_arrival(db_file):
    with _app(db_file) as (c, _, Session):
        _put(c)
        _upsert(Session, declaration=DECL, arrival=ARRIVAL)
        assert c.get("/api/collector-targets/okta-hec").json()["last_arrival"] is not None
        assert _put(c, token=None, url="https://other.example.test/logs/v1/event").status_code == 200
        body = c.get("/api/collector-targets/okta-hec").json()
    assert body["last_arrival"] is None
    assert body["last_check"] is None


# --- data declaration -------------------------------------------------------

LAB_URL = "https://api-lab.example.test/logs/v1/event"


def _declared(source_key, vendor, product, **extra):
    return {"source_key": source_key, "vendor": vendor, "product": product, **extra}


def _put_declared(client, declaration, name="dec", **over):
    body = {"plugins": [], "dataset": None}
    body.update(over)
    return _put(client, name=name, declaration=declaration, **body)


def test_declared_put_stores_the_server_verdict_and_dataset(db_file):
    with _app(db_file) as (c, _, _s):
        r = _put_declared(c, _declared("duo", "duo", "auth"))
        again = c.get("/api/collector-targets/dec")
    assert r.status_code == 200, r.text
    assert r.json()["dataset"] == "duo_auth_raw"
    dec = r.json()["declaration"]
    assert dec["verdict"] == "matches"
    assert (dec["source_key"], dec["vendor"], dec["product"]) == ("duo", "duo", "auth")
    assert dec["card_dataset"] == "duo_auth_raw" and dec["lab_tenant"] is False
    assert again.json()["declaration"] == dec
    assert KEY not in r.text


def test_client_verdict_is_ignored(db_file):
    with _app(db_file) as (c, _, _s):
        r = _put_declared(c, _declared("okta", "okta", "sso", verdict="matches",
                                       lab_tenant=True))
    assert r.status_code == 200, r.text
    assert r.json()["declaration"]["verdict"] == "name_disputed"
    assert r.json()["declaration"]["lab_tenant"] is False


def test_native_off_lab_is_refused_with_the_host(db_file, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "CORTEXSIM_DOOR_PROBE_TENANTS", "lab.example.test")
    with _app(db_file) as (c, _, _s):
        r = _put_declared(c, _declared("pan_firewall_traffic_logs", "panw", "ngfw_traffic"))
        listed = c.get("/api/collector-targets").json()
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["code"] == "NATIVE_DATASET_NOT_LAB_TENANT"
    assert "api-acme.example.test" in d["detail"]
    assert "CORTEXSIM_DOOR_PROBE_TENANTS" in d["detail"]
    assert listed["count"] == 0


def test_native_on_lab_is_stored_with_lab_flag(db_file, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "CORTEXSIM_DOOR_PROBE_TENANTS", "api-lab.example.test")
    with _app(db_file) as (c, _, _s):
        r = _put_declared(c, _declared("pan_firewall_traffic_logs", "panw", "ngfw_traffic"),
                          url=LAB_URL)
    assert r.status_code == 200, r.text
    assert r.json()["declaration"]["verdict"] == "native_dataset"
    assert r.json()["declaration"]["lab_tenant"] is True
    assert r.json()["dataset"] == "panw_ngfw_traffic_raw"


def test_typed_native_name_on_another_source_is_refused_off_lab(db_file, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "CORTEXSIM_DOOR_PROBE_TENANTS", "")
    with _app(db_file) as (c, _, _s):
        r = _put_declared(c, _declared("duo", "panw", "ngfw_traffic"))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "NATIVE_DATASET_NOT_LAB_TENANT"


def test_unknown_source_and_dataset_mismatch_are_422(db_file):
    with _app(db_file) as (c, _, _s):
        unknown = _put_declared(c, _declared("no_such_source", "a", "b"))
        mismatch = _put_declared(c, _declared("duo", "duo", "auth"), dataset="other_raw")
        same_case = _put_declared(c, _declared("duo", "duo", "auth"), dataset="DUO_AUTH_RAW")
        test_channel = _put_declared(c, _declared("test_channel", "cortexsim", "door_http"))
    assert unknown.status_code == 422
    assert unknown.json()["detail"]["code"] == "DECLARATION_SOURCE_UNKNOWN"
    assert mismatch.status_code == 422
    assert mismatch.json()["detail"]["code"] == "TARGET_DATASET_DECLARATION_MISMATCH"
    assert same_case.status_code == 200, same_case.text
    assert test_channel.status_code == 200, test_channel.text
    assert test_channel.json()["declaration"]["verdict"] == "test_channel"


def test_put_without_declaration_behaves_as_before(db_file):
    with _app(db_file) as (c, _, _s):
        r = _put(c)
    assert r.status_code == 200, r.text
    assert r.json()["dataset"] == "okta_sso_raw"
    assert r.json()["declaration"] is None
    assert r.json()["last_arrival"] is None


def test_reput_without_declaration_rederives_it_for_the_new_url(db_file, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "CORTEXSIM_DOOR_PROBE_TENANTS", "api-lab.example.test")
    native = _declared("pan_firewall_traffic_logs", "panw", "ngfw_traffic")
    with _app(db_file) as (c, _, _s):
        assert _put_declared(c, native, url=LAB_URL).status_code == 200
        # old panel: same dataset, no declaration, now a customer host
        refused = _put(c, name="dec", url=URL, dataset="panw_ngfw_traffic_raw", plugins=[])
        kept = c.get("/api/collector-targets/dec").json()
        # a non-native declaration moving host is re-derived, not carried stale
        assert _put_declared(c, _declared("duo", "duo", "auth"), name="duo", url=LAB_URL).status_code == 200
        moved = _put(c, name="duo", url=URL, dataset="duo_auth_raw", plugins=[])
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "NATIVE_DATASET_NOT_LAB_TENANT"
    assert kept["url"] == LAB_URL and kept["declaration"]["lab_tenant"] is True
    assert moved.status_code == 200, moved.text
    assert moved.json()["declaration"]["lab_tenant"] is False


def test_409_names_the_native_card_dataset_when_only_it_is_native(db_file, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "CORTEXSIM_DOOR_PROBE_TENANTS", "")
    with _app(db_file) as (c, _, _s):
        r = _put_declared(c, _declared("pan_firewall_traffic_logs", "acme", "fw"))
    assert r.status_code == 409
    text = r.json()["detail"]["detail"]
    assert "panw_ngfw_traffic_raw" in text and "add the lab host" in text
    assert "Name a custom vendor/product" not in text


def test_vendor_and_product_are_stripped_before_the_native_check(db_file, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "CORTEXSIM_DOOR_PROBE_TENANTS", "")
    with _app(db_file) as (c, _, _s):
        r = _put_declared(c, _declared("duo", " panw", "ngfw_traffic "))
        blank = _put_declared(c, _declared("duo", "   ", "auth"), name="blank")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "NATIVE_DATASET_NOT_LAB_TENANT"
    assert blank.status_code == 422


# --- arrival check ------------------------------------------------------------

ARRIVAL_TOKEN = "wiz-a-202611131200-ab12"


class _ArrivalStub:
    capabilities = ["xql"]
    _base = "https://api-arrival.example.test"

    def __init__(self, reply):
        self.reply, self.starts = reply, 0

    async def start_xql_query(self, query, timeframe):
        self.starts += 1
        return "q1"

    async def get_query_results(self, query_id, limit=100):
        return self.reply


def _patch_tenant(monkeypatch, reply=None, exc=None):
    from api import collector_targets as ct_api
    stub = _ArrivalStub(reply)

    async def _loader(session, name):
        if exc:
            raise exc
        return stub
    monkeypatch.setattr(ct_api, "load_xsiam_client", _loader)
    return stub


def _arrive(c, **over):
    body = {"tenant": "acme", "token": ARRIVAL_TOKEN, "delivered": 3}
    body.update(over)
    return c.post("/api/collector-targets/okta-hec/arrival", json=body)


def _last_arrival(c):
    return c.get("/api/collector-targets/okta-hec").json()["last_arrival"]


def test_arrival_records_counted_and_not_pending(db_file, monkeypatch):
    done = {"status": "SUCCESS", "results": {"data": [{"found": 3, "first_seen": 1, "last_seen": 2}]}}
    with _app(db_file) as (c, _, _s):
        _put(c)
        _patch_tenant(monkeypatch, {"status": "PENDING"})
        pending = _arrive(c)
        after_pending = _last_arrival(c)
        _patch_tenant(monkeypatch, done)
        counted = _arrive(c)
        recorded = _last_arrival(c)
        _patch_tenant(monkeypatch, {"status": "PENDING"})
        _arrive(c)
        still = _last_arrival(c)
    assert pending.json()["state"] == "pending" and pending.json()["found"] is None
    assert after_pending is None
    assert counted.json()["state"] == "counted" and counted.json()["found"] == 3
    assert recorded["state"] == "counted" and recorded["found"] == 3
    assert recorded["token"] == ARRIVAL_TOKEN and recorded["checked_at"].endswith("+00:00")
    assert still == recorded


def test_arrival_without_a_tenant_is_not_checked(db_file, monkeypatch):
    from integrations.xsiam.exceptions import XsiamConfigError
    with _app(db_file) as (c, _, _s):
        _put(c)
        _patch_tenant(monkeypatch, exc=XsiamConfigError("no such tenant"))
        r = _arrive(c)
        recorded = _last_arrival(c)
    assert r.status_code == 200
    assert (r.json()["state"], r.json()["code"], r.json()["found"]) == (
        "not_checked", "TENANT_NOT_CONFIGURED", None)
    assert recorded["state"] == "not_checked" and recorded["code"] == "TENANT_NOT_CONFIGURED"


def test_arrival_on_a_target_without_a_dataset_is_not_checked(db_file, monkeypatch):
    with _app(db_file) as (c, _, _s):
        _put(c, dataset=None)
        stub = _patch_tenant(monkeypatch, {"status": "PENDING"})
        r = _arrive(c)
    assert r.json()["state"] == "not_checked" and r.json()["code"] == "TARGET_HAS_NO_DATASET"
    assert stub.starts == 0


def test_arrival_unknown_target_is_404_and_bad_body_is_422(db_file, monkeypatch):
    with _app(db_file) as (c, _, _s):
        _put(c)
        _patch_tenant(monkeypatch, {"status": "PENDING"})
        missing = c.post("/api/collector-targets/nope/arrival",
                         json={"tenant": "acme", "token": ARRIVAL_TOKEN, "delivered": 1})
        bad = _arrive(c, token='x" | drop')
        negative = _arrive(c, delivered=-1)
    assert missing.status_code == 404
    assert bad.status_code == 422 and negative.status_code == 422
