"""SEC-11 — the committed ``.env.example`` secret must not boot, in ANY env.

``.env.example`` ships ``CORTEXSIM_SECRET=replace-me-with-a-generated-32plus-byte-secret``.
It is 46 characters, so it cleared the 32-byte length floor, and it is not in
the forbidden list (``changeme``/``default``/...), so ``validate_master_key``
accepted it. In ``development`` — which ``docker-compose.yml`` defaults to —
even a value the guard DID dislike only logged a warning.

``cp .env.example .env && docker compose up`` therefore booted a SimCore whose
credential vault (customer Cortex tenant API keys) was encrypted with a key
that is public, committed, and identical for every DC. The vault is real in
development too — dev is what compose runs by default — so this placeholder is
refused in BOTH environments. (``changeme`` and short keys keep their existing
dev-only warning; tests/security/test_credentials.py pins that.)

The placeholder is read from ``.env.example`` at test time, so if the template
ever changes its placeholder the guard is re-proven against the new one.

RED on base cc26ddf: both environments accepted the placeholder.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _template_secret() -> str:
    text = (REPO / ".env.example").read_text()
    m = re.search(r"^CORTEXSIM_SECRET=(.*)$", text, re.MULTILINE)
    assert m, ".env.example no longer documents CORTEXSIM_SECRET"
    value = m.group(1).strip()
    assert value, ".env.example ships an empty CORTEXSIM_SECRET"
    return value


@pytest.mark.parametrize("env", ["production", "development"])
def test_the_env_example_secret_is_refused(env):
    from config import MasterKeyError, validate_master_key

    secret = _template_secret()
    with pytest.raises(MasterKeyError) as exc:
        validate_master_key(secret, env=env)
    assert ".env.example" in str(exc.value)


@pytest.mark.parametrize("env", ["production", "development"])
@pytest.mark.parametrize("secret", [
    "replace-me-with-a-generated-32plus-byte-secret",
    "replace-me-please-i-am-a-very-long-placeholder-value",
    "REPLACE-ME-with-a-generated-32plus-byte-secret",
    "  replace-me-with-a-generated-32plus-byte-secret  ",
])
def test_any_replace_me_value_is_refused(env, secret):
    from config import MasterKeyError, validate_master_key

    with pytest.raises(MasterKeyError):
        validate_master_key(secret, env=env)


@pytest.mark.parametrize("env", ["production", "development"])
def test_a_generated_secret_still_boots(env):
    import secrets

    from config import validate_master_key

    validate_master_key(secrets.token_urlsafe(48), env=env)  # no raise


def test_short_keys_keep_their_dev_only_warning(caplog):
    """Unchanged behaviour: the dev escape hatch for weak keys stays, only the
    published placeholder lost it."""
    import logging

    from config import validate_master_key

    caplog.set_level(logging.WARNING)
    validate_master_key("changeme", env="development")
    assert any("misconfigured" in r.getMessage() for r in caplog.records)
