"""OPS-12 — a changed master key must be visible BEFORE the POV, not during it.

Every stored credential is a Fernet token under a key derived from
``CORTEXSIM_SECRET``. Regenerate ``.env`` (``rm .env && scripts/dev-up.sh``,
the remediation dev-up.sh itself prints) while the ``simcore_db`` volume
survives, and every customer tenant key in the vault becomes undecryptable.
Nothing said so: SimCore booted, ``/api/health`` reported ``integrations: ok``
with N configured, and the first sign was a ``CRYPTO_ERROR`` 500 when the DC
clicked "test tenant" in front of the customer.

The fix is a key-check: on first boot SimCore stores a Fernet token of a
constant under the current key. At boot and in ``/api/health`` it decrypts that
token; failure is ``degraded MASTER_KEY_MISMATCH`` with the two remediations
that actually work (restore the old ``.env``, or re-enter the credentials). It
is a local decrypt — no tenant is contacted.

A deployment that predates the key-check has no token. Stamping one blindly
under whatever key it booted with would bless a key that may already be wrong,
so with no token the check first tries the existing secrets: if any fails to
decrypt it reports the mismatch and stamps NOTHING.

The health side is its own component, ``master_key``, rather than a field of
``integrations``: vault integrity is not tenant configuration, and a mismatch
has to name itself in ``degraded_components``.

RED on base cc26ddf: ``check_master_key`` / ``probe_master_key`` did not exist,
and the only credential component, ``integrations``, read ``ok`` for a vault no
key could open.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

KEY_A = "a-key-the-vault-was-written-with-" + "a" * 16
KEY_B = "a-different-key-after-env-was-regenerated-" + "b" * 8


@pytest.fixture
def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    async def _init():
        from database import Base
        import models  # noqa: F401

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    return async_sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def secret_is(monkeypatch):
    import security.credentials as cred_mod

    def _set(value: str) -> None:
        monkeypatch.setattr(cred_mod.settings, "CORTEXSIM_SECRET", value)

    return _set


async def _seed_tenant(factory, key: str) -> None:
    from security.credentials import CredentialStore

    async with factory() as s:
        store = CredentialStore(s, master_key=key)
        await store.put_integration(
            name="acme", kind="xsiam_tenant", plaintext_secret="tenant-api-key-" + "x" * 30,
            config={"base_url": "https://api-acme.xdr.us.paloaltonetworks.com"},
        )
        await s.commit()


async def _check(factory, key: str, *, stamp: bool):
    from security.credentials import check_master_key

    async with factory() as s:
        result = await check_master_key(s, master_key=key, stamp=stamp)
        await s.commit()
        return result


def test_first_boot_stamps_and_later_boots_verify(session_factory):
    first = asyncio.run(_check(session_factory, KEY_A, stamp=True))
    assert first.status == "stamped"
    assert first.code == "OK"
    again = asyncio.run(_check(session_factory, KEY_A, stamp=True))
    assert again.status == "ok"


def test_store_under_a_boot_under_b_is_a_mismatch(session_factory):
    asyncio.run(_seed_tenant(session_factory, KEY_A))
    assert asyncio.run(_check(session_factory, KEY_A, stamp=True)).status == "stamped"

    under_b = asyncio.run(_check(session_factory, KEY_B, stamp=True))
    assert under_b.status == "mismatch"
    assert under_b.code == "MASTER_KEY_MISMATCH"

    # The mismatch must not overwrite the evidence: going back to key A is
    # still recognised as the right key.
    assert asyncio.run(_check(session_factory, KEY_A, stamp=True)).status == "ok"


def test_health_reports_master_key_mismatch(session_factory, secret_is):
    from api.health import COMPONENT_KEYS, probe_master_key

    assert "master_key" in COMPONENT_KEYS

    asyncio.run(_seed_tenant(session_factory, KEY_A))
    asyncio.run(_check(session_factory, KEY_A, stamp=True))

    secret_is(KEY_B)
    comp = asyncio.run(probe_master_key(session_factory))
    assert comp["status"] == "degraded", comp
    assert comp["code"] == "MASTER_KEY_MISMATCH"
    detail = comp["detail"]
    assert "Fix:" in detail
    assert ".env" in detail and "re-enter" in detail
    assert "no tenant call" in detail.lower()

    secret_is(KEY_A)
    comp = asyncio.run(probe_master_key(session_factory))
    assert comp["status"] == "ok", comp
    assert comp["key_check"] == "ok"


def test_health_reports_a_pre_keycheck_vault_under_the_wrong_key(session_factory, secret_is):
    from api.health import probe_master_key

    asyncio.run(_seed_tenant(session_factory, KEY_A))   # never stamped
    secret_is(KEY_B)
    comp = asyncio.run(probe_master_key(session_factory))
    assert comp["status"] == "degraded"
    assert comp["code"] == "MASTER_KEY_MISMATCH"
    assert comp["secrets_checked"] == 1


def test_a_pre_keycheck_vault_under_the_wrong_key_is_caught_and_not_stamped(session_factory):
    asyncio.run(_seed_tenant(session_factory, KEY_A))   # no key-check ever written

    under_b = asyncio.run(_check(session_factory, KEY_B, stamp=True))
    assert under_b.status == "mismatch"
    assert under_b.code == "MASTER_KEY_MISMATCH"

    # KEY_B was NOT blessed: the right key still verifies, and stamps now.
    assert asyncio.run(_check(session_factory, KEY_A, stamp=True)).status == "stamped"
    assert asyncio.run(_check(session_factory, KEY_B, stamp=False)).status == "mismatch"


def test_re_entering_every_credential_clears_the_mismatch(session_factory, secret_is):
    """The remediation the detail offers must actually work: after a mismatch,
    re-entering every credential under the current key leaves nothing
    unreadable, so the stale key-check is replaced at the next boot."""
    from api.health import probe_master_key

    asyncio.run(_seed_tenant(session_factory, KEY_A))
    asyncio.run(_check(session_factory, KEY_A, stamp=True))
    assert asyncio.run(_check(session_factory, KEY_B, stamp=True)).status == "mismatch"

    asyncio.run(_seed_tenant(session_factory, KEY_B))   # put_integration replaces it

    secret_is(KEY_B)
    assert asyncio.run(probe_master_key(session_factory))["key_check"] == "stale"
    assert asyncio.run(_check(session_factory, KEY_B, stamp=True)).status == "restamped"
    assert asyncio.run(_check(session_factory, KEY_B, stamp=True)).status == "ok"
    comp = asyncio.run(probe_master_key(session_factory))
    assert comp["status"] == "ok" and comp["key_check"] == "ok"


def test_a_new_key_over_an_empty_vault_is_not_an_alarm(session_factory):
    """Regenerating .env over a volume that holds no credentials loses nothing;
    raising MASTER_KEY_MISMATCH there would be a banner nobody can act on."""
    asyncio.run(_check(session_factory, KEY_A, stamp=True))
    result = asyncio.run(_check(session_factory, KEY_B, stamp=True))
    assert result.status == "restamped"
    assert result.code == "OK"


def test_health_never_stamps(session_factory, secret_is):
    from api.health import probe_master_key
    from security.credentials import KEY_CHECK_NAME

    secret_is(KEY_A)
    comp = asyncio.run(probe_master_key(session_factory))
    assert comp["status"] == "ok"
    assert comp["key_check"] == "not_stamped"

    async def _row_exists():
        from sqlalchemy import select
        from models import Secret

        async with session_factory() as s:
            return (await s.execute(
                select(Secret).where(Secret.name == KEY_CHECK_NAME))).scalar_one_or_none()

    assert asyncio.run(_row_exists()) is None


def test_the_key_check_is_not_an_operator_secret(session_factory):
    from security.credentials import KEY_CHECK_NAME, CredentialStore

    asyncio.run(_check(session_factory, KEY_A, stamp=True))

    async def _probe():
        async with session_factory() as s:
            store = CredentialStore(s, master_key=KEY_A)
            listed = [r.name for r in await store.list()]
            with pytest.raises(ValueError):
                await store.put(KEY_CHECK_NAME, "overwrite-me")
            with pytest.raises(ValueError):
                await store.delete(KEY_CHECK_NAME)
            return listed

    assert KEY_CHECK_NAME not in asyncio.run(_probe())


def test_boot_runs_the_key_check():
    import main

    src = inspect.getsource(main.lifespan)
    assert "check_master_key" in src and "stamp=True" in src
