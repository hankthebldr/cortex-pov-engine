"""A malformed identity spec must raise. An absent one is served and reported.

``engine.identity_spec`` used to answer every failure to read
spec/identity_harness.json with its hard-coded copy, and every empty key with
the same copy. Since PR #152 that copy is pinned identical to the file, so for
an ABSENT file it is a correct answer: it stays, and GET /api/health reports it
(tests/api/test_health.py). A file that is PRESENT but malformed has no correct
answer, and the tolerant one was wrong in ways no log line showed:

* ``"service_accounts": "www-data"`` served ``('w', 'w', 'w', '-', ...)``, a
  bash ``run_as`` arm of single letters and an S-17 that rejects every
  service-account step;
* ``"service_accounts": []`` served the eleven hard-coded accounts, so an edit
  that emptied the list changed nothing and said so nowhere;
* invalid JSON, or a list at the root, was served the copy as if absent.

Gate A5: an unrecognised shape must raise, not read as a default. Each case
raises and names the file, so a bad edit or a wrong CORTEXSIM_BASE_DIR is
reported where it is, not as 174 bundle digests three suites away.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from engine import identity_spec

SPEC = Path(__file__).resolve().parents[2] / "spec" / "identity_harness.json"

# Every accessor, because the bash run_as arm reads two of them and S-17 reads
# three: a spec that is wrong anywhere is not partly right.
ACCESSORS = (
    identity_spec.direct_identities,
    identity_spec.service_accounts,
    identity_spec.impersonation_platforms,
    identity_spec.windows_identities,
)

_DROP = object()


def _real_spec_with(**changes) -> str:
    """The shipped spec with exactly one thing wrong."""
    spec = json.loads(SPEC.read_text())
    for key, value in changes.items():
        if value is _DROP:
            del spec[key]
        else:
            spec[key] = value
    return json.dumps(spec)


MALFORMED = {
    "not JSON": "{",
    "root is a list": "[]",
    "a string where a list belongs": _real_spec_with(service_accounts="www-data"),
    "an empty list": _real_spec_with(service_accounts=[]),
    "a list missing": _real_spec_with(windows_identities=_DROP),
    "a blank entry": _real_spec_with(direct_identities=["root", " "]),
    "a non-string entry": _real_spec_with(service_accounts=["www-data", 42]),
    "an entry listed twice": _real_spec_with(service_accounts=["www-data", "www-data"]),
}


@pytest.fixture
def spec_file(monkeypatch, tmp_path):
    """Point the loader at a tmp spec path; nothing is written until asked."""
    path = tmp_path / "identity_harness.json"
    monkeypatch.setattr(identity_spec, "_spec_path", lambda: str(path))
    identity_spec._load.cache_clear()
    yield path
    identity_spec._load.cache_clear()


def _assert_raises_naming(path: Path, accessor) -> None:
    # ValueError rather than the new class, so this observes the old loader
    # returning a value instead of failing on a missing attribute.
    with pytest.raises(ValueError, match=re.escape(str(path))) as exc:
        accessor()
    assert type(exc.value).__name__ == "IdentitySpecError", accessor.__name__


@pytest.mark.parametrize("text", MALFORMED.values(), ids=MALFORMED.keys())
def test_a_malformed_spec_raises_and_names_the_file(text, spec_file):
    spec_file.write_text(text)
    for accessor in ACCESSORS:
        _assert_raises_naming(spec_file, accessor)


def test_a_spec_that_is_present_but_unreadable_raises(spec_file):
    spec_file.mkdir()  # something is there; it is not a file the loader can read
    _assert_raises_naming(spec_file, identity_spec.service_accounts)


def test_an_absent_spec_is_served_from_the_fallback_and_says_so(spec_file):
    spec = json.loads(SPEC.read_text())
    assert identity_spec.service_accounts() == tuple(spec["service_accounts"])
    assert identity_spec.spec_source() == {"path": str(spec_file), "source": "fallback"}


def test_a_well_formed_spec_is_reported_as_the_file(spec_file):
    spec_file.write_text(SPEC.read_text())
    assert identity_spec.spec_source() == {"path": str(spec_file), "source": "file"}


def test_importing_the_module_reads_no_spec(restore_core_modules, monkeypatch, tmp_path):
    """A value resolved at import would raise from inside ``import``.

    Then /api/health could not import the module to report the problem, and
    every lazy importer would fail mid-import instead of at the call that
    needed the spec. Resolve on use, never at import.
    """
    import config  # noqa: PLC0415
    from tests._core_reimport import reimport_core  # noqa: PLC0415

    (tmp_path / "spec").mkdir()
    (tmp_path / "spec" / "identity_harness.json").write_text("{")
    monkeypatch.setattr(config.settings, "CORTEXSIM_BASE_DIR", str(tmp_path))
    reimport_core("engine.identity_spec")

    from engine import identity_spec as fresh  # noqa: PLC0415  (must not raise)

    with pytest.raises(fresh.IdentitySpecError):
        fresh.direct_identities()
