"""Canonical identity-harness spec loader (GAP-PUSH-001).

The push-mode bash generator and the pull-mode Go beacon must resolve a
scenario step's ``identity`` USERNAME string to the SAME execution wrapper, or
a scenario behaves differently depending on how it was launched. To avoid two
hand-copied allowlists drifting apart, both sides derive from one source of
truth: ``spec/identity_harness.json`` at the repo root.

  * Python (this module) reads the JSON and the push generator builds its
    ``run_as`` bash ``case`` arms from :func:`direct_identities` /
    :func:`service_accounts`.
  * Go (agent/identity) hard-codes the same allowlist, and a unit test
    (harness_spec_test.go) asserts the ``serviceAccounts`` map matches the JSON.
    The beacon never reads the file at run time.

If the spec file is ABSENT (a deploy that did not ship ``spec/``, or a
CORTEXSIM_BASE_DIR pointing at the wrong tree) the loader serves a hard-coded
copy, pinned identical to the file by tests/engine/test_identity_spec_fallback.py,
so the output does not change. It does not stay quiet about it:
:func:`spec_source` says the copy is in force and ``GET /api/health`` reports the
``identity_spec`` component degraded, because an absent spec is a broken
deployment even when the bundles are right.

A spec that is PRESENT but malformed raises :class:`IdentitySpecError`. An absent
file has one correct reading; a malformed one has none, and the old tolerant
reading was wrong (a string where a list belongs served its characters, an
empty list silently served the hard-coded one).

Platform note: impersonation is POSIX-only. The spec's ``impersonation_platforms``
key names the host families on which a non-direct identity can actually be
honoured (linux, darwin). On Windows every identity collapses to ``direct`` and
the executing side MUST record that degradation — see :func:`impersonation_platforms`
and the ``windows`` block of the spec.
"""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import NamedTuple

from config import settings

logger = logging.getLogger("cortexsim.identity_spec")

# Hard-coded fallback — MUST stay in sync with spec/identity_harness.json,
# ORDER INCLUDED: the bash run_as case arm renders service_accounts in this
# order, so a transposed copy changes every bash bundle's bytes. The JSON file
# is authoritative; this is served only when the file is ABSENT.
# Guarded by tests/engine/test_identity_spec_fallback.py.
_FALLBACK_DIRECT = ("root", "container-runtime", "direct")
_FALLBACK_WINDOWS_IDENTITIES = ("administrator", "user")

_FALLBACK_SERVICE = (
    "app", "developer", "mysql", "nobody", "node", "postgres", "python3",
    "runner", "svc-backup", "vertex-agent", "www-data",
)
_FALLBACK_IMPERSONATION_PLATFORMS = ("linux", "darwin")

# Every list the spec must declare. Each is served verbatim, order included.
_REQUIRED_LISTS = (
    "direct_identities", "service_accounts", "impersonation_platforms", "windows_identities",
)


class IdentitySpecError(ValueError):
    """spec/identity_harness.json is present but is not a spec this code can read.

    Raised, never defaulted: the bash run_as arm, the PowerShell harness and the
    S-17 load check are all built from this file, and none has a safe reading of
    a spec that exists but is wrong.
    """


class _Spec(NamedTuple):
    data: dict
    path: str
    fallback: bool


def _spec_path() -> str:
    return os.path.join(settings.CORTEXSIM_BASE_DIR, "spec", "identity_harness.json")


def _malformed(path: str, problem: str) -> IdentitySpecError:
    return IdentitySpecError(
        f"identity harness spec {path} is malformed: {problem}. Fix or restore the "
        f"file; a spec that exists but cannot be read has no safe default."
    )


def _validated(data: object, path: str) -> dict:
    if not isinstance(data, dict):
        raise _malformed(path, f"the root is a {type(data).__name__}, not a JSON object")
    for key in _REQUIRED_LISTS:
        vals = data.get(key)
        if not isinstance(vals, list) or not vals:
            raise _malformed(path, f"{key!r} must be a non-empty list, got {vals!r}")
        if not all(isinstance(v, str) and v.strip() for v in vals):
            raise _malformed(path, f"{key!r} holds a blank or non-string entry: {vals!r}")
        if len(set(vals)) != len(vals):
            raise _malformed(path, f"{key!r} lists an entry twice: {vals!r}")
    return data


@lru_cache(maxsize=1)
def _load() -> _Spec:
    path = _spec_path()
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        logger.warning(
            "identity harness spec not found at %s — serving the built-in copy; "
            "GET /api/health reports identity_spec degraded until spec/ is restored",
            path,
        )
        return _Spec(
            {
                "direct_identities": list(_FALLBACK_DIRECT),
                "service_accounts": list(_FALLBACK_SERVICE),
                "impersonation_platforms": list(_FALLBACK_IMPERSONATION_PLATFORMS),
                "windows_identities": list(_FALLBACK_WINDOWS_IDENTITIES),
            },
            path,
            True,
        )
    except OSError as exc:
        raise _malformed(path, f"it is present but cannot be read ({exc})") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _malformed(path, f"it is not valid JSON ({exc})") from exc
    return _Spec(_validated(data, path), path, False)


def spec_source() -> dict[str, str]:
    """Where the identities in force came from: ``{"path", "source"}``.

    ``source`` is ``"file"`` or ``"fallback"``. Read once per process, so this
    reports what is being served, not what is on disk now.
    """
    spec = _load()
    return {"path": spec.path, "source": "fallback" if spec.fallback else "file"}


def direct_identities() -> tuple[str, ...]:
    """Usernames that run with NO impersonation wrapper (direct mode)."""
    return tuple(_load().data["direct_identities"])


def service_accounts() -> tuple[str, ...]:
    """Known service accounts impersonated via runuser/sudo/su."""
    return tuple(_load().data["service_accounts"])


def impersonation_platforms() -> tuple[str, ...]:
    """Host families on which a non-direct identity can actually be honoured.

    A platform absent from this tuple (notably ``windows``) has no unattended
    impersonation primitive, so a step declaring a service account there runs
    as the executing account instead. Any generator or executor targeting such
    a platform must SURFACE that, not swallow it: a run record claiming a step
    ran as ``www-data`` when it ran as the beacon user is a correctness lie.
    """
    return tuple(_load().data["impersonation_platforms"])


def windows_identities() -> tuple[str, ...]:
    """Accounts the corpus declares on windows-ONLY steps.

    They are never impersonated — Windows has no unattended primitive, so
    ``ResolveFor`` collapses them to ``direct`` and records the degradation.
    They are kept OUT of ``service_accounts`` on purpose: a POSIX beacon must
    not treat them as known, because ``runuser -l administrator`` on a Linux
    step is an authoring error rather than something to run best-effort.
    ``S-17`` in the scenario loader accepts them only on a step whose declared
    platforms are windows-only.
    """
    return tuple(_load().data["windows_identities"])


def supports_impersonation(platform: str) -> bool:
    """Whether ``platform`` (a GOOS-style name) can honour an impersonated identity."""
    return platform.strip().lower() in impersonation_platforms()


# Deliberately no module-level values resolved from the spec. Importing this
# module must not read the file: a malformed spec would then raise from inside
# ``import``, and /api/health could not import the module to report it.
# Guarded by tests/engine/test_identity_spec_malformed.py.
