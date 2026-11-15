"""`.env.example` and `core/config.py::Settings` must agree on the variable names.

`Settings` reads `.env`, and pydantic-settings defaults `BaseSettings` to
`extra='forbid'`. So any key documented in `.env.example` but not declared as a
field makes `Settings()` raise at construction — which is boot, for anyone who
followed the file's own instructions and runs SimCore on the host.

That is exactly how `CORTEXSIM_VERSION` broke: `841add5` put it in
`.env.example` (it drives the docker image tag and container name),
`docker-compose.yml` consumed it, and `Settings` never declared it. The
container path hid the failure because `.env` is gitignored and never COPYed
into the image, so compose supplies these as real environment variables and no
`.env` exists to be parsed. Only the documented *local dev* path — the one a new
contributor follows first, `cp .env.example .env` — hit the ValidationError, and
the message pointed at pydantic rather than at the missing declaration.

The fix direction these tests pin down is deliberate. `.env` is a **superset**
shared with docker compose, so compose-only variables get **declared** rather
than waved through with `extra='ignore'`. Declaring keeps the quick-start
working *and* keeps a typo'd setting name failing loudly. That matters because
the two settings sources are not symmetric: pydantic-settings already ignores an
unknown `os.environ` var (`EnvSettingsSource` looks up declared names only),
while `DotEnvSettingsSource` hands every key in the file to the model. So
`extra='forbid'` buys typo protection in exactly one place — the `.env` file,
which is where a DC actually sets things. See
`test_unknown_key_in_dotenv_is_still_rejected`, which exists so nobody can
quietly trade that away to make the other tests pass.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from config import Settings

# Assignments only; comments and blank lines are ignored. `export FOO=` is
# accepted because .env files are commonly written both ways.
_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=")


@pytest.fixture
def env_example(repo_root: Path) -> Path:
    path = repo_root / ".env.example"
    # Deliberately a failure, not a skip. A skipped guard reads exactly like a
    # passing one, and this file is committed — if it is absent, something is
    # wrong that we want to hear about.
    assert path.is_file(), (
        f"{path} is missing — the quick-start's first line is `cp .env.example .env`"
    )
    return path


@pytest.fixture
def documented_keys(env_example: Path) -> set[str]:
    return {
        match.group(1)
        for line in env_example.read_text().splitlines()
        if not line.lstrip().startswith("#") and (match := _ASSIGNMENT.match(line))
    }


@pytest.fixture
def dotenv_is_sole_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip ambient CORTEXSIM_* vars so the dotenv file is the only source.

    `EnvSettingsSource` outranks the dotenv, so a developer (or CI runner) with
    `CORTEXSIM_ENV` exported would otherwise silently mask what the file says.
    """
    for name in [key for key in os.environ if key.startswith("CORTEXSIM_")]:
        monkeypatch.delenv(name, raising=False)


def test_env_example_documents_something(documented_keys: set[str]) -> None:
    """Guard the guard: a parser that silently matched nothing would pass."""
    assert documented_keys, ".env.example parsed to zero variables — the regex is wrong"
    assert "CORTEXSIM_SECRET" in documented_keys, (
        "the master-key variable is not in .env.example — either it moved or "
        "the parser is matching the wrong thing"
    )


def test_every_documented_var_is_accepted_by_settings(documented_keys: set[str]) -> None:
    """No key in `.env.example` may be rejected by `Settings`.

    `extra='forbid'` is the right default and is NOT what is under test — the fix
    for a violation is to declare the field, not to widen the model.
    """
    undeclared = sorted(documented_keys - set(Settings.model_fields))

    assert not undeclared, (
        "these variables are documented in .env.example but are not fields on "
        f"config.Settings: {undeclared}. Because Settings reads .env with "
        "extra='forbid', an operator who follows .env.example and runs SimCore "
        "on the host gets a ValidationError at boot. Declare the field "
        "(deployment metadata is fine as a plain str default) rather than "
        "setting extra='ignore', which would hide real typos too."
    )


def test_settings_constructs_from_shipped_env_example(
    env_example: Path, documented_keys: set[str], dotenv_is_sole_source: None
) -> None:
    """`cp .env.example .env` then boot must work — the documented first step.

    The parity check above compares names; this one uses the shipped file
    verbatim, so a documented *value* that cannot coerce to its field's type is
    caught too, not just a missing declaration.
    """
    settings = Settings(_env_file=str(env_example))

    # Assert a value that came out of the file, so this cannot pass vacuously on
    # a build where the dotenv was never read at all.
    assert "CORTEXSIM_ENV" in documented_keys
    assert settings.CORTEXSIM_ENV == "development"


# A commented example line: `# CORTEXSIM_FOO=value` (or `# export CORTEXSIM_...`).
# `.env.example` documents optional settings this way, and the first thing an
# operator does with one is delete the `#`.
_COMMENTED_EXAMPLE = re.compile(r"^(\s*)#\s*((?:export\s+)?CORTEXSIM_[A-Z0-9_]*\s*=.*)$")


def _uncomment_examples(text: str) -> tuple[str, set[str]]:
    """Return ``text`` with every commented CORTEXSIM_*= example uncommented,
    and the names that were uncommented."""
    names: set[str] = set()
    out = []
    for line in text.splitlines():
        m = _COMMENTED_EXAMPLE.match(line)
        if m and (name := _ASSIGNMENT.match(m.group(2))):
            names.add(name.group(1))
            line = m.group(1) + m.group(2)
        out.append(line)
    return "\n".join(out) + "\n", names


def test_the_uncommenter_finds_commented_examples() -> None:
    """Guard the guard, on a literal: it must not depend on what the shipped
    file happens to keep commented today."""
    text, names = _uncomment_examples(
        "# prose about CORTEXSIM_FOO, not an assignment\n"
        "# CORTEXSIM_FOO=1\n"
        "#   export CORTEXSIM_BAR=two\n"
        "CORTEXSIM_BAZ=3\n"
        "# OTHER=4\n")
    assert names == {"CORTEXSIM_FOO", "CORTEXSIM_BAR"}
    assert text.splitlines() == [
        "# prose about CORTEXSIM_FOO, not an assignment",
        "CORTEXSIM_FOO=1", "export CORTEXSIM_BAR=two", "CORTEXSIM_BAZ=3", "# OTHER=4"]


def test_settings_constructs_with_every_commented_example_uncommented(
    env_example: Path, documented_keys: set[str], tmp_path: Path, dotenv_is_sole_source: None
) -> None:
    """Uncommenting an example `.env.example` ships must never stop the boot.

    `CORTEXSIM_TRUSTED_PROXIES` was documented as a commented line, read from
    `os.environ` by `core/api/agents.py`, and not declared on `Settings`. So the
    file had to carry a warning NOT to uncomment its own example: doing so made
    a host-run SimCore refuse to boot with `extra_forbidden`.
    """
    text, uncommented = _uncomment_examples(env_example.read_text())
    assert "CORTEXSIM_TRUSTED_PROXIES" in (uncommented | documented_keys), (
        "CORTEXSIM_TRUSTED_PROXIES is no longer in .env.example at all, commented or not")

    undeclared = sorted(uncommented - set(Settings.model_fields))
    assert not undeclared, (
        f"commented examples in .env.example that are not fields on config.Settings: "
        f"{undeclared}. An operator who uncomments one and runs SimCore on the host gets "
        "a ValidationError at boot. Declare the field.")

    dotenv = tmp_path / ".env"
    dotenv.write_text(text)
    settings = Settings(_env_file=str(dotenv))
    assert settings.CORTEXSIM_ENV == "development"   # the file was really read


def test_unknown_key_in_dotenv_is_still_rejected(
    tmp_path: Path, dotenv_is_sole_source: None
) -> None:
    """A typo'd setting name must fail loudly, not read as absent.

    This is the guard against 'fixing' a future breakage by relaxing to
    `extra='ignore'`. That change makes every other test in this file pass while
    removing the only typo protection the config has, so without this test the
    tempting one-line fix would land green.

    `CORTEXSIM_STRICT_REFSS` is a realistic slip: silently ignored, it would
    leave the UC/TC foreign-key gate on when an operator believed they had turned
    it off — or, in the mirror case, off when they believed it was on.
    """
    dotenv = tmp_path / ".env"
    dotenv.write_text("CORTEXSIM_ENV=development\nCORTEXSIM_STRICT_REFSS=false\n")

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=str(dotenv))

    assert "cortexsim_strict_refss" in str(excinfo.value).lower()
