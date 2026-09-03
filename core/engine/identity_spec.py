"""Canonical identity-harness spec loader (GAP-PUSH-001).

The push-mode bash generator and the pull-mode Go beacon must resolve a
scenario step's ``identity`` USERNAME string to the SAME execution wrapper, or
a scenario behaves differently depending on how it was launched. To avoid two
hand-copied allowlists drifting apart, both sides derive from one source of
truth: ``spec/identity_harness.json`` at the repo root.

  * Python (this module) reads the JSON and the push generator builds its
    ``run_as`` bash ``case`` arms from :data:`DIRECT_IDENTITIES` /
    :data:`SERVICE_ACCOUNTS`.
  * Go (agent/identity) embeds the same JSON and a unit test
    (harness_spec_test.go) asserts the Go ``serviceAccounts`` map matches it.

If the spec file is missing (e.g. running from an odd CWD), the loader falls
back to a hard-coded copy so push-bundle generation never breaks — the Go-side
test still guards against drift.

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

from config import settings

logger = logging.getLogger("cortexsim.identity_spec")

# Hard-coded fallback — MUST stay in sync with spec/identity_harness.json. The
# JSON file is authoritative; this only guards generation when the file is
# unreadable.
_FALLBACK_DIRECT = ("root", "container-runtime", "direct")
_FALLBACK_WINDOWS_IDENTITIES = ("administrator", "user")

_FALLBACK_SERVICE = (
    "app", "developer", "mysql", "node", "nobody", "postgres", "python3",
    "runner", "svc-backup", "vertex-agent", "www-data",
)
_FALLBACK_IMPERSONATION_PLATFORMS = ("linux", "darwin")


def _spec_path() -> str:
    return os.path.join(settings.CORTEXSIM_BASE_DIR, "spec", "identity_harness.json")


@lru_cache(maxsize=1)
def _load() -> dict:
    path = _spec_path()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("identity_harness.json is not a JSON object")
        return data
    except (OSError, ValueError, json.JSONDecodeError):
        logger.warning(
            "identity harness spec unreadable at %s — using hard-coded fallback",
            path,
        )
        return {
            "direct_identities": list(_FALLBACK_DIRECT),
            "service_accounts": list(_FALLBACK_SERVICE),
            "impersonation_platforms": list(_FALLBACK_IMPERSONATION_PLATFORMS),
        }


def direct_identities() -> tuple[str, ...]:
    """Usernames that run with NO impersonation wrapper (direct mode)."""
    vals = _load().get("direct_identities") or list(_FALLBACK_DIRECT)
    return tuple(vals)


def service_accounts() -> tuple[str, ...]:
    """Known service accounts impersonated via runuser/sudo/su."""
    vals = _load().get("service_accounts") or list(_FALLBACK_SERVICE)
    return tuple(vals)


def impersonation_platforms() -> tuple[str, ...]:
    """Host families on which a non-direct identity can actually be honoured.

    A platform absent from this tuple (notably ``windows``) has no unattended
    impersonation primitive, so a step declaring a service account there runs
    as the executing account instead. Any generator or executor targeting such
    a platform must SURFACE that, not swallow it: a run record claiming a step
    ran as ``www-data`` when it ran as the beacon user is a correctness lie.
    """
    vals = _load().get("impersonation_platforms") or list(_FALLBACK_IMPERSONATION_PLATFORMS)
    return tuple(vals)


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
    vals = _load().get("windows_identities") or list(_FALLBACK_WINDOWS_IDENTITIES)
    return tuple(vals)


def supports_impersonation(platform: str) -> bool:
    """Whether ``platform`` (a GOOS-style name) can honour an impersonated identity."""
    return platform.strip().lower() in impersonation_platforms()


# Module-level convenience constants (resolved once at import).
DIRECT_IDENTITIES = direct_identities()
SERVICE_ACCOUNTS = service_accounts()
IMPERSONATION_PLATFORMS = impersonation_platforms()
