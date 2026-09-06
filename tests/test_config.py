"""Guards the contract between the shipped ``.env.example`` and ``core/config.py``.

The documented quick-start starts with ``cp .env.example .env``. pydantic-settings
v2 defaults a model to ``extra="forbid"``, and its ``DotEnvSettingsSource`` hands
**every** key in the dotenv file to the model — unlike ``EnvSettingsSource``, which
only looks up declared field names in ``os.environ``. So a variable that lives in
``.env.example`` without a matching field on ``Settings`` does not get ignored: it
makes ``Settings()`` raise, which breaks the quick-start, the entire pytest suite,
and any local process constructing ``Settings`` from the repo root.

That is not hypothetical. ``841add5`` added ``CORTEXSIM_VERSION`` — which drives the
docker image tag and container name and is read by ``docker-compose.yml``, never by
the app — to ``.env.example``. From that commit until this file existed, ``dev``
could not construct ``Settings`` at all, and the failure pointed at pydantic rather
than at the missing declaration.

The fix direction these tests pin down is deliberate. ``.env`` is a **superset**
shared with docker compose, so compose-only variables must be **declared** on
``Settings`` rather than waved through with ``extra="ignore"``. Declaring them keeps
the quick-start working *and* keeps a typo'd setting name failing loudly, which is
the only typo protection this config has (see
``test_unknown_key_in_dotenv_is_still_rejected``).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from config import Settings

# KEY=value, ignoring comments, blank lines, and `export ` prefixes.
_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


@pytest.fixture
def env_example(repo_root: Path) -> Path:
    path = repo_root / ".env.example"
    # Without this, a moved or renamed template would make `_env_file` a silent
    # no-op and every assertion below would pass while proving nothing.
    assert path.is_file(), f"{path} is missing — the quick-start's first line is `cp .env.example .env`"
    return path


@pytest.fixture
def env_example_keys(env_example: Path) -> list[str]:
    return [
        match.group(1)
        for line in env_example.read_text().splitlines()
        if (match := _ASSIGNMENT.match(line))
    ]


@pytest.fixture
def dotenv_is_sole_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip ambient CORTEXSIM_* vars so the dotenv file is the only source.

    ``EnvSettingsSource`` outranks the dotenv, so a developer (or CI runner) with
    ``CORTEXSIM_ENV`` exported would otherwise silently mask what the file says.
    """
    for name in [key for key in os.environ if key.startswith("CORTEXSIM_")]:
        monkeypatch.delenv(name, raising=False)


def test_settings_constructs_from_shipped_env_example(
    env_example: Path, env_example_keys: list[str], dotenv_is_sole_source: None
) -> None:
    """`cp .env.example .env` then boot must work — the documented first step."""
    settings = Settings(_env_file=str(env_example))

    # Assert a value that came out of the file, so this cannot pass vacuously on a
    # build where the dotenv was never read.
    assert "CORTEXSIM_ENV" in env_example_keys
    assert settings.CORTEXSIM_ENV == "development"


def test_every_env_example_key_is_a_declared_settings_field(
    env_example_keys: list[str],
) -> None:
    """The diagnostic twin of the test above: name the offending variable.

    Same defect, but the failure says *which* key is undeclared instead of leaving
    a reader to decode a pydantic ``extra_forbidden`` trace.
    """
    undeclared = sorted(set(env_example_keys) - set(Settings.model_fields))

    assert not undeclared, (
        f"{'; '.join(undeclared)} appears in .env.example but is not a field on "
        f"core/config.py::Settings. Because .env is a superset shared with docker "
        f"compose, a compose-only variable must still be DECLARED on Settings "
        f"(pydantic-settings rejects unknown dotenv keys). Declare it — do not set "
        f"extra='ignore', which would also swallow typo'd setting names."
    )


def test_unknown_key_in_dotenv_is_still_rejected(
    tmp_path: Path, dotenv_is_sole_source: None
) -> None:
    """A typo'd setting name must fail loudly, not read as absent.

    This is the guard against 'fixing' the above by relaxing to ``extra='ignore'``.
    ``CORTEXSIM_STRICT_REFSS`` is a realistic slip: silently ignored, it would leave
    the UC/TC foreign-key gate on when an operator believed they had turned it off —
    or, in the mirror case, off when they believed it was on.
    """
    dotenv = tmp_path / ".env"
    dotenv.write_text("CORTEXSIM_ENV=development\nCORTEXSIM_STRICT_REFSS=false\n")

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=str(dotenv))

    assert "cortexsim_strict_refss" in str(excinfo.value).lower()
