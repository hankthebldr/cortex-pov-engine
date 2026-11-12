"""Config-only integration credential update: never touches the secret or verification."""
from __future__ import annotations

import pytest

from tests.security.test_credentials import db_session  # noqa: F401  (shared fixture)

SECRET = "some-long-secret-value-123456789"


async def _seed(db_session, name="cfg-update"):
    from security.credentials import CredentialStore
    store = CredentialStore(db_session)
    await store.put_integration(
        name=name, kind="panos_ngfw", plaintext_secret=SECRET,
        config={"host": "192.0.2.1", "vsys": "vsys1", "drop": "me"})
    await db_session.commit()
    await store.mark_integration_verified(name, ok=True)
    await db_session.commit()
    return store


@pytest.mark.asyncio
async def test_update_config_merges_and_preserves_verification_and_secret(db_session):
    store = await _seed(db_session)
    before = await store.get_integration("cfg-update")
    secret_id, verified_at = before.secret_id, before.last_verified_at

    row = await store.update_integration_config(
        "cfg-update", {"vsys": "vsys2", "drop": None, "new": 1})
    await db_session.commit()

    row = await store.get_integration("cfg-update")
    assert row.config == {"host": "192.0.2.1", "vsys": "vsys2", "new": 1}
    assert row.last_verified_ok is True
    assert row.last_verified_at == verified_at
    assert row.secret_id == secret_id
    assert await store.get_integration_secret("cfg-update") == SECRET


@pytest.mark.asyncio
async def test_update_config_returns_none_when_absent(db_session):
    from security.credentials import CredentialStore
    store = CredentialStore(db_session)
    assert await store.update_integration_config("nope", {"a": 1}) is None


@pytest.mark.asyncio
async def test_update_config_persists_across_reload(db_session):
    store = await _seed(db_session, "persist")
    await store.update_integration_config("persist", {"vsys": "vsys9"})
    await db_session.commit()
    db_session.expire_all()
    assert (await store.get_integration("persist")).config["vsys"] == "vsys9"
