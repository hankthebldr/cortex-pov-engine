"""`.env.example` documents every setting, truthfully (DOCS-19).

`tests/test_config.py` holds one direction: every key in `.env.example` is a
field `Settings` accepts. This file holds the others.

* **Every `Settings` field is in the template**, active or commented. It
  documented 10 of 28: the two Cortex write switches, the auto-reconcile and
  auto-verify opt-ins (which make outbound calls to a customer tenant) and the
  strict-refs gate were absent, so a DC hardening a lab could not see from the
  template that they exist or that they default off.
* **A commented default is the real default.** Uncommenting every documented
  line must still construct `Settings`, to the same values `Settings` has with
  no file at all. A template that shows `=300` for a setting whose default is
  600 is worse than one that omits it.
* **The template says where a value takes effect.** Under `docker compose` the
  container never sees `.env`: compose reads it to fill in `${VAR}` in
  `docker-compose.yml`, and passes on only what that file names. A key the
  compose file does not reference changes nothing there. The template is split
  at one marked line, and each side is held to what `docker-compose.yml`
  really references, so a setting cannot be documented as settable where it is
  not.
* **A key `Settings` does not declare says so**, because uncommenting it in a
  host-run `.env` makes `Settings()` refuse to boot.

ADDING A FIELD TO `Settings`? Add a commented `# NAME=<default>` line to
`.env.example`, below the marked line unless `docker-compose.yml` passes it.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from config import Settings

REPO = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = REPO / ".env.example"
COMPOSE = REPO / "docker-compose.yml"

#: The line that splits the template. Text, not position, so the file can grow.
DIVIDER = "docker-compose.yml DOES NOT PASS"

_ACTIVE = re.compile(r"^(?:export\s+)?([A-Z][A-Z0-9_]*)=(.*)$")
#: A documented-but-off assignment: '#', at most one space, NAME=value. Prose
#: examples are indented further and are not matched.
_COMMENTED = re.compile(r"^# ?([A-Z][A-Z0-9_]*)=(.*)$")
#: Wording that tells the reader a key is not a Settings field.
_NOT_A_SETTING = ("not declared on", "compose only", "compose-only",
                  "environment only", "environment-only")


def _lines() -> list[str]:
    return ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()


def _entries() -> list[tuple[int, str, str, bool]]:
    """(line index, key, value, active) for every documented assignment."""
    out = []
    for i, line in enumerate(_lines()):
        if m := _ACTIVE.match(line):
            out.append((i, m.group(1), m.group(2), True))
        elif m := _COMMENTED.match(line):
            out.append((i, m.group(1), m.group(2), False))
    return out


def _divider_index() -> int:
    hits = [i for i, line in enumerate(_lines()) if DIVIDER in line]
    assert len(hits) == 1, (
        f".env.example must carry exactly one line containing {DIVIDER!r}, splitting "
        f"what docker-compose.yml reads from what only SimCore reads; found {len(hits)}"
    )
    return hits[0]


def _block_above(index: int) -> str:
    """The comment block that documents the assignment at ``index``."""
    lines = _lines()
    block = []
    i = index - 1
    while i >= 0 and lines[i].startswith("#") and not _COMMENTED.match(lines[i]):
        block.append(lines[i])
        i -= 1
    return "\n".join(reversed(block)).lower()


def _compose_variables() -> set[str]:
    live = "\n".join(ln for ln in COMPOSE.read_text(encoding="utf-8").splitlines()
                     if not ln.lstrip().startswith("#"))
    return set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", live))


@pytest.fixture
def dotenv_is_sole_source(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [key for key in os.environ if key.startswith("CORTEXSIM_")]:
        monkeypatch.delenv(name, raising=False)


def test_env_example_parser_finds_both_kinds_of_line():
    """Guard the guard: a regex that matched nothing would pass everything."""
    entries = _entries()
    assert any(active for _, _, _, active in entries)
    assert any(not active for _, _, _, active in entries)
    assert "CORTEXSIM_SECRET" in {key for _, key, _, _ in entries}


def test_env_example_documents_every_settings_field():
    documented = {key for _, key, _, _ in _entries()}
    missing = sorted(set(Settings.model_fields) - documented)
    assert not missing, (
        f".env.example does not mention these settings: {missing}. Add a commented "
        f"'# NAME=<default>' line with one line of purpose for each, below the "
        f"{DIVIDER!r} line unless docker-compose.yml passes it into the container."
    )


def test_env_example_documents_each_key_once():
    keys = [key for _, key, _, _ in _entries()]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    assert not dupes, f"documented twice, and the two can disagree: {dupes}"


def test_env_example_commented_defaults_are_the_real_defaults(tmp_path, dotenv_is_sole_source):
    """Uncomment every documented setting: Settings must construct, and each
    value must equal the default Settings has with no file at all."""
    declared = set(Settings.model_fields)
    commented = {key: value for _, key, value, active in _entries()
                 if not active and key in declared}
    assert commented, "no commented Settings field found: the parser or the file is wrong"

    uncommented = []
    for line in _lines():
        m = _COMMENTED.match(line)
        uncommented.append(f"{m.group(1)}={m.group(2)}" if m and m.group(1) in declared else line)
    dotenv = tmp_path / ".env"
    dotenv.write_text("\n".join(uncommented) + "\n", encoding="utf-8")

    settings = Settings(_env_file=str(dotenv))
    wrong = {
        key: (shown, Settings.model_fields[key].default)
        for key, shown in commented.items()
        if getattr(settings, key) != Settings.model_fields[key].default
    }
    assert not wrong, (
        f".env.example shows a default that is not the real one (shown, real): {wrong}"
    )


def test_env_example_says_so_when_a_key_is_not_a_settings_field():
    """Uncommenting an undeclared key in a host-run .env makes Settings() raise
    (extra='forbid'), so the template has to warn before the operator does it."""
    declared = set(Settings.model_fields)
    unmarked = [key for index, key, _, _ in _entries()
                if key not in declared
                and not any(phrase in _block_above(index) for phrase in _NOT_A_SETTING)]
    assert not unmarked, (
        f"{unmarked} are documented in .env.example, are not fields on config.Settings, "
        f"and their comment does not say so (one of {_NOT_A_SETTING})"
    )


def test_env_example_keys_above_the_divider_are_the_ones_compose_reads():
    divider = _divider_index()
    above = {key for index, key, _, _ in _entries() if index < divider}
    compose = _compose_variables()
    assert compose, "found no ${VAR} in docker-compose.yml: the parser is wrong"
    assert above == compose, (
        f"the first part of .env.example must list exactly the variables "
        f"docker-compose.yml references. Only in .env.example: {sorted(above - compose)}. "
        f"Only in docker-compose.yml: {sorted(compose - above)}."
    )


def test_env_example_keys_below_the_divider_are_not_read_by_compose():
    """Under docker compose the container never sees .env. A key documented as
    a plain setting that compose does not pass is a line the operator edits to
    no effect, so those keys sit under a heading that says exactly that."""
    divider = _divider_index()
    below = {key for index, key, _, _ in _entries() if index > divider}
    assert below, "nothing is documented below the divider"
    leaked = sorted(below & _compose_variables())
    assert not leaked, (
        f"{leaked} sit under {DIVIDER!r} but docker-compose.yml does reference them; "
        f"move them above the line"
    )
    section = "\n".join(_lines()[divider:divider + 25]).lower()
    assert "environment:" in section and "default" in section, (
        "the heading must say what happens under compose (the default stays) and "
        "where the value would have to go (the service's environment: list)"
    )


@pytest.mark.parametrize("key", ["CORTEXSIM_XSIAM_ALLOW_WRITE", "CORTEXSIM_XSIAM_ALLOW_DESTRUCTIVE"])
def test_env_example_shows_the_cortex_write_switches_off(key):
    found = [(index, value, active) for index, k, value, active in _entries() if k == key]
    assert found, f"{key} is not documented"
    index, value, active = found[0]
    assert not active, f"{key} must be shipped commented out"
    assert value.strip().lower() == "false", f"{key} is shown as {value!r}"
    assert "write" in _block_above(index) or "destructive" in _block_above(index)


@pytest.mark.parametrize("key", ["CORTEXSIM_AUTO_RECONCILE", "CORTEXSIM_AUTO_VERIFY"])
def test_env_example_says_which_switches_call_the_tenant(key):
    found = [(index, value) for index, k, value, _ in _entries() if k == key]
    assert found, f"{key} is not documented"
    index, value = found[0]
    assert value.strip().lower() == "false", f"{key} is shown as {value!r}"
    assert "outbound" in _block_above(index), (
        f"the comment on {key} does not say it makes outbound calls to the tenant"
    )
