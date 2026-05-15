"""Tests for the credentials layer.

Exercises both the Python API (CredentialStore) and the REST API
(/api/credentials/*). Uses a temporary SQLite database per test to avoid
touching the real cortexsim.db file.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio


# Ensure config picks up a real master key before anything imports it
os.environ["CORTEXSIM_SECRET"] = "test-master-key-please-ignore-32+chars-of-entropy"
os.environ["CORTEXSIM_ENV"] = "development"


@pytest_asyncio.fixture
async def db_session(tmp_path: Path, monkeypatch):
    """Spin up an isolated async SQLite session per test."""
    db_path = tmp_path / "creds-test.db"
    monkeypatch.setenv("CORTEXSIM_BASE_DIR", str(tmp_path))

    # Re-import database & models with the new BASE_DIR
    for mod in ("database", "models", "config"):
        sys.modules.pop(mod, None)
    for mod in [m for m in sys.modules if m.startswith("security")]:
        sys.modules.pop(mod, None)

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    from database import Base  # noqa: PLC0415
    import models  # noqa: F401, PLC0415

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


# ── CryptoError + key derivation ───────────────────────────────────────────


def test_derive_fernet_key_is_stable():
    from security.crypto import derive_fernet_key
    a = derive_fernet_key("the-quick-brown-fox-jumps-over-the-lazy-dog-1234")
    b = derive_fernet_key("the-quick-brown-fox-jumps-over-the-lazy-dog-1234")
    assert a == b
    assert len(a) == 44  # urlsafe-b64 of 32 bytes


def test_derive_fernet_key_changes_with_input():
    from security.crypto import derive_fernet_key
    a = derive_fernet_key("master-key-aaaaaaaaaaaaaaaaaaaaaa")
    b = derive_fernet_key("master-key-bbbbbbbbbbbbbbbbbbbbbb")
    assert a != b


def test_derive_fernet_key_rejects_empty():
    from security.crypto import CryptoError, derive_fernet_key
    with pytest.raises(CryptoError):
        derive_fernet_key("")


# ── Boot-time validation ───────────────────────────────────────────────────


def test_validate_master_key_rejects_defaults_in_prod():
    from config import MasterKeyError, validate_master_key
    for bad in ["changeme", "", "default", "secret", "short"]:
        with pytest.raises(MasterKeyError):
            validate_master_key(bad, env="production")


def test_validate_master_key_allows_strong_key_in_prod():
    from config import validate_master_key
    validate_master_key("a" * 40, env="production")  # no raise


def test_validate_master_key_warns_but_allows_in_dev(caplog):
    from config import validate_master_key
    import logging
    caplog.set_level(logging.WARNING)
    validate_master_key("changeme", env="development")
    assert any("misconfigured" in rec.message for rec in caplog.records)


# ── Redaction policy ───────────────────────────────────────────────────────


def test_redaction_policy_returns_tail_for_long_values():
    from security.credentials import redaction_policy
    assert redaction_policy("abcdefghijklmnopqrstuvwxyz", "generic") == "...wxyz"


def test_redaction_policy_returns_none_for_short_values():
    from security.credentials import redaction_policy
    assert redaction_policy("short", "generic") is None


# ── CredentialStore: secrets ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_and_get_plaintext_roundtrip(db_session):
    from security.credentials import CredentialStore
    store = CredentialStore(db_session)

    plaintext = "sk-this-is-a-long-fake-api-key-xyz-1234567890"
    await store.put("my-key", plaintext, type_hint="api_key")
    await db_session.commit()

    result = await store.get_plaintext("my-key")
    assert result == plaintext


@pytest.mark.asyncio
async def test_secret_ciphertext_is_actually_encrypted(db_session):
    from security.credentials import CredentialStore
    store = CredentialStore(db_session)

    plaintext = "leak-detector-CORTEXSIM-CANARY-do-not-find-me"
    await store.put("leak-test", plaintext)
    await db_session.commit()

    row = await store._get_secret_row("leak-test")
    assert plaintext not in row.ciphertext
    assert "CORTEXSIM-CANARY" not in row.ciphertext


@pytest.mark.asyncio
async def test_update_existing_secret(db_session):
    from security.credentials import CredentialStore
    store = CredentialStore(db_session)

    await store.put("rotating", "old-value-with-more-than-twenty-chars", type_hint="api_key")
    await store.put("rotating", "new-value-with-more-than-twenty-chars-too", type_hint="api_key")
    await db_session.commit()

    assert await store.get_plaintext("rotating") == "new-value-with-more-than-twenty-chars-too"


@pytest.mark.asyncio
async def test_delete_secret(db_session):
    from security.credentials import CredentialStore
    store = CredentialStore(db_session)
    await store.put("temp", "throwaway-value-with-some-content-bytes")
    await db_session.commit()

    assert await store.delete("temp") is True
    assert await store.delete("temp") is False

    with pytest.raises(KeyError):
        await store.get_plaintext("temp")


@pytest.mark.asyncio
async def test_wrong_master_key_raises_crypto_error(db_session):
    from security.credentials import CredentialStore
    from security.crypto import CryptoError

    writer = CredentialStore(db_session, master_key="master-key-original-very-long-string-here")
    await writer.put("encrypted-once", "the-secret-value-stored-with-original-master-key")
    await db_session.commit()

    reader = CredentialStore(db_session, master_key="master-key-DIFFERENT-very-long-string-here")
    with pytest.raises(CryptoError):
        await reader.get_plaintext("encrypted-once")


@pytest.mark.asyncio
async def test_last_accessed_at_updates_on_read(db_session):
    from security.credentials import CredentialStore
    store = CredentialStore(db_session)
    await store.put("touched", "some-secret-value-of-sufficient-length")
    await db_session.commit()

    row1 = await store._get_secret_row("touched")
    assert row1.last_accessed_at is None

    await store.get_plaintext("touched")
    await db_session.commit()

    row2 = await store._get_secret_row("touched")
    assert row2.last_accessed_at is not None


@pytest.mark.asyncio
async def test_preview_tail_stored_for_long_values(db_session):
    from security.credentials import CredentialStore
    store = CredentialStore(db_session)
    await store.put("longish", "a" * 30, type_hint="api_key")
    await db_session.commit()

    row = await store._get_secret_row("longish")
    assert row.preview_tail == "...aaaa"


# ── CredentialStore: integrations ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_integration_put_get_roundtrip(db_session):
    from security.credentials import CredentialStore
    store = CredentialStore(db_session)

    await store.put_integration(
        name="xsiam-acme-prod",
        kind="xsiam",
        plaintext_secret="api-key-very-long-value-1234567890",
        config={
            "base_url": "https://api-acme.xdr.us.paloaltonetworks.com",
            "auth_mode": "standard",
            "region": "us",
        },
    )
    await db_session.commit()

    row = await store.get_integration("xsiam-acme-prod")
    assert row is not None
    assert row.kind == "xsiam"
    assert row.config["region"] == "us"

    plaintext = await store.get_integration_secret("xsiam-acme-prod")
    assert plaintext == "api-key-very-long-value-1234567890"


@pytest.mark.asyncio
async def test_integration_mark_verified(db_session):
    from security.credentials import CredentialStore
    store = CredentialStore(db_session)

    await store.put_integration(
        name="verify-me",
        kind="xsiam",
        plaintext_secret="some-long-secret-value-123456789",
        config={},
    )
    await db_session.commit()

    await store.mark_integration_verified("verify-me", ok=True)
    await db_session.commit()
    row = await store.get_integration("verify-me")
    assert row.last_verified_ok is True
    assert row.last_verified_error is None

    await store.mark_integration_verified("verify-me", ok=False, error="HTTP 401")
    await db_session.commit()
    row = await store.get_integration("verify-me")
    assert row.last_verified_ok is False
    assert row.last_verified_error == "HTTP 401"


@pytest.mark.asyncio
async def test_integration_delete_removes_backing_secret(db_session):
    from security.credentials import CredentialStore
    store = CredentialStore(db_session)

    await store.put_integration(
        name="to-delete",
        kind="xsiam",
        plaintext_secret="secret-to-be-removed-from-store-soon",
        config={},
    )
    await db_session.commit()

    removed = await store.delete_integration("to-delete")
    await db_session.commit()
    assert removed is True

    assert await store.get_integration("to-delete") is None
    assert await store._get_secret_row("__integration__/to-delete") is None


@pytest.mark.asyncio
async def test_list_integrations_filter_by_kind(db_session):
    from security.credentials import CredentialStore
    store = CredentialStore(db_session)

    await store.put_integration(name="t1", kind="xsiam", plaintext_secret="x" * 30, config={})
    await store.put_integration(name="t2", kind="xsiam", plaintext_secret="y" * 30, config={})
    await store.put_integration(name="a1", kind="aws", plaintext_secret="z" * 30, config={})
    await db_session.commit()

    xsiam = await store.list_integrations(kind="xsiam")
    aws = await store.list_integrations(kind="aws")
    assert {r.name for r in xsiam} == {"t1", "t2"}
    assert {r.name for r in aws} == {"a1"}


# ── REST API roundtrip ─────────────────────────────────────────────────────


@pytest.fixture
def api_client(tmp_path: Path, monkeypatch):
    """Build a minimal FastAPI app with just the credentials router."""
    monkeypatch.setenv("CORTEXSIM_BASE_DIR", str(tmp_path))
    db_path = tmp_path / "api-test.db"

    for mod in ("database", "models", "config"):
        sys.modules.pop(mod, None)
    for mod in [m for m in sys.modules if m.startswith("security") or m == "api.credentials"]:
        sys.modules.pop(mod, None)

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    import asyncio

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    from database import Base  # noqa: PLC0415
    import models  # noqa: F401, PLC0415

    asyncio.get_event_loop().run_until_complete(
        _create_all(engine, Base)
    )

    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_db():
        async with Session() as session:
            yield session

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.credentials import router as creds_router
    from database import get_db

    app = FastAPI()
    app.include_router(creds_router, prefix="/api")
    app.dependency_overrides[get_db] = _override_db

    yield TestClient(app)


async def _create_all(engine, Base):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


class TestCredentialsAPI:
    def test_put_and_list_secret(self, api_client):
        resp = api_client.put(
            "/api/credentials/secrets",
            json={
                "name": "test-api-key",
                "plaintext": "very-long-plaintext-value-for-testing-123",
                "type_hint": "api_key",
                "description": "test secret",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == "test-api-key"
        assert body["preview_tail"] == "...-123"
        assert "ciphertext" not in body
        assert "plaintext" not in body

        list_resp = api_client.get("/api/credentials/secrets")
        assert list_resp.status_code == 200
        names = [s["name"] for s in list_resp.json()["secrets"]]
        assert "test-api-key" in names

    def test_get_secret_meta_no_plaintext(self, api_client):
        api_client.put(
            "/api/credentials/secrets",
            json={"name": "leak-check", "plaintext": "DO-NOT-LEAK-THIS-VALUE-EVER"},
        )
        resp = api_client.get("/api/credentials/secrets/leak-check")
        assert resp.status_code == 200
        body = resp.json()
        assert "DO-NOT-LEAK-THIS-VALUE-EVER" not in str(body)
        assert "ciphertext" not in body

    def test_delete_secret(self, api_client):
        api_client.put(
            "/api/credentials/secrets",
            json={"name": "doomed", "plaintext": "this-will-be-deleted-soon-enough"},
        )
        resp = api_client.delete("/api/credentials/secrets/doomed")
        assert resp.status_code == 204

        resp = api_client.delete("/api/credentials/secrets/doomed")
        assert resp.status_code == 404

    def test_integration_lifecycle(self, api_client):
        # Create
        resp = api_client.put(
            "/api/credentials/integrations",
            json={
                "name": "xsiam-test-tenant",
                "kind": "xsiam",
                "plaintext_secret": "fake-xsiam-api-key-1234567890abcdef",
                "config": {
                    "base_url": "https://api-test.xdr.us.paloaltonetworks.com",
                    "auth_mode": "standard",
                    "region": "us",
                },
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["kind"] == "xsiam"

        # List
        resp = api_client.get("/api/credentials/integrations?kind=xsiam")
        assert resp.status_code == 200
        assert resp.json()["integrations"][0]["name"] == "xsiam-test-tenant"

        # Mark verified
        resp = api_client.post(
            "/api/credentials/integrations/xsiam-test-tenant/verify",
            json={"ok": True},
        )
        assert resp.status_code == 200
        assert resp.json()["last_verified_ok"] is True

        # Delete
        resp = api_client.delete("/api/credentials/integrations/xsiam-test-tenant")
        assert resp.status_code == 204
