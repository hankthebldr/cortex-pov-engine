"""`.env.example` and `Settings` must agree on the variable names.

`Settings` reads `.env`, and pydantic-settings defaults `BaseSettings` to
`extra='forbid'`. So any key documented in `.env.example` but not declared as a
field makes `Settings()` raise at construction — which is boot, for anyone who
followed the file's own instructions and runs SimCore on the host.

That is exactly how `CORTEXSIM_VERSION` broke: `.env.example` told operators to
set it, `docker-compose.yml` consumed it, and `config.Settings` never declared
it. The container path hid the failure because `.env` is gitignored and never
COPYed into the image, so compose supplies these as real environment variables
and no `.env` exists to be parsed. Only the documented *local dev* path — the
one a new contributor follows first — hit the ValidationError.

The guard is deliberately name-only. Values are not asserted: `.env.example`
carries placeholders, and this is about the two files describing the same set of
knobs, not about any particular default.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE = REPO_ROOT / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

ENV_EXAMPLE = REPO_ROOT / ".env.example"

# Assignments only; comments and blank lines are ignored. `export FOO=` is
# accepted because .env files are commonly written both ways.
_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=")


def _documented_keys() -> set[str]:
    if not ENV_EXAMPLE.exists():
        pytest.skip(".env.example is absent from this checkout")
    keys: set[str] = set()
    for line in ENV_EXAMPLE.read_text().splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = _ASSIGNMENT.match(line)
        if m:
            keys.add(m.group(1))
    return keys


def test_env_example_documents_something() -> None:
    """Guard the guard: a parser that silently matched nothing would pass."""
    keys = _documented_keys()
    assert keys, ".env.example parsed to zero variables — the regex is wrong"
    assert "CORTEXSIM_SECRET" in keys, (
        "the master-key variable is not in .env.example — either it moved or "
        "the parser is matching the wrong thing"
    )


def test_every_documented_var_is_accepted_by_settings() -> None:
    """No key in `.env.example` may be rejected by `Settings`.

    `extra='forbid'` is the right default and is NOT what is under test — the
    fix for a violation is to declare the field, not to widen the model.
    """
    from config import Settings  # noqa: PLC0415 — needs the sys.path insert above

    declared = set(Settings.model_fields)
    documented = _documented_keys()
    undeclared = sorted(documented - declared)

    assert not undeclared, (
        "these variables are documented in .env.example but are not fields on "
        f"config.Settings: {undeclared}. Because Settings reads .env with "
        "extra='forbid', an operator who follows .env.example and runs SimCore "
        "on the host gets a ValidationError at boot. Declare the field "
        "(deployment metadata is fine as a plain str default) rather than "
        "setting extra='ignore', which would hide real typos too."
    )


def test_settings_constructs_with_the_documented_env_file(tmp_path, monkeypatch) -> None:
    """End-to-end: a `.env` written from `.env.example`'s keys must boot.

    The parity check above compares names; this proves the actual failure mode
    is gone by constructing Settings against a real file containing every
    documented key. Run from a temp cwd so a developer's own `.env` cannot make
    this pass or fail for the wrong reason.
    """
    from config import Settings  # noqa: PLC0415

    env_file = tmp_path / ".env"
    lines = [f"{k}=x" for k in sorted(_documented_keys()) if k != "CORTEXSIM_PORT"]
    # PORT is an int field; give it something coercible rather than "x".
    lines.append("CORTEXSIM_PORT=8888")
    env_file.write_text("\n".join(lines) + "\n")

    monkeypatch.chdir(tmp_path)
    # Env vars take precedence over the file and would mask a rejection.
    for key in _documented_keys():
        monkeypatch.delenv(key, raising=False)

    Settings(_env_file=str(env_file))  # must not raise
