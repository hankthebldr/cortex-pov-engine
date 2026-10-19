"""The identity-spec fallback must be an exact copy of spec/identity_harness.json.

``engine.identity_spec`` falls back to a hard-coded copy of the spec when the
file is absent, so push-bundle generation survives an odd BASE_DIR. Its
comment says the copy MUST stay in sync with the JSON. Nothing checked that,
and it had drifted: the fallback listed ``node`` before ``nobody`` and the spec
lists ``nobody`` first.

Order is load-bearing. ``push_generator`` joins ``service_accounts()`` into the
bash ``run_as`` case arm, so a transposed fallback changes the bytes of every
bash bundle. That is how the drift surfaced on 2026-10-19: a test leaked a tmp
BASE_DIR (see tests/_core_reimport.py), the fallback fired mid-session, and 174
bash digests in ``test_push_generator_invariant`` changed. The comparison is
therefore a sequence, never a set.

The fallback stays for an ABSENT file, where this test makes it a correct
answer, and /api/health reports it in force. A present-but-malformed spec raises
instead: tests/engine/test_identity_spec_malformed.py.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from engine import identity_spec

SPEC = Path(__file__).resolve().parents[2] / "spec" / "identity_harness.json"

# Every list the spec declares, mapped to the accessor that serves it.
ACCESSORS = {
    "direct_identities": identity_spec.direct_identities,
    "service_accounts": identity_spec.service_accounts,
    "impersonation_platforms": identity_spec.impersonation_platforms,
    "windows_identities": identity_spec.windows_identities,
}


@pytest.fixture
def absent_spec(monkeypatch, tmp_path):
    """Force the fallback, and leave the cache clean for whoever runs next."""
    monkeypatch.setattr(identity_spec, "_spec_path", lambda: str(tmp_path / "absent.json"))
    identity_spec._load.cache_clear()
    yield
    identity_spec._load.cache_clear()


def test_every_list_in_the_spec_has_a_fallback():
    """A list added to the JSON without a fallback would serve nothing when absent,
    and one the loader does not require would go unvalidated when present."""
    spec = json.loads(SPEC.read_text())
    assert {k for k, v in spec.items() if isinstance(v, list)} == set(ACCESSORS)
    assert set(identity_spec._REQUIRED_LISTS) == set(ACCESSORS)


@pytest.mark.parametrize("key", sorted(ACCESSORS))
def test_the_fallback_serves_exactly_what_the_spec_declares(key, absent_spec):
    assert not os.path.exists(identity_spec._spec_path()), "the fallback was not exercised"
    spec = json.loads(SPEC.read_text())
    assert ACCESSORS[key]() == tuple(spec[key]), (
        f"identity_spec's fallback for {key!r} differs from {SPEC.name}; "
        "order included, because the bash run_as case arm renders it"
    )
