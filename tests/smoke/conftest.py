"""Shared fixtures for end-to-end smoke tests.

Smoke tests assume a *running* SimCore reachable at ``CORTEXSIM_SMOKE_URL``.

Two cases, kept apart on purpose:

* **No ``CORTEXSIM_SMOKE_URL``** (the default ``http://localhost:8888``): the
  suite skips when nothing answers, so the same files are safe to run in a
  matrix where SimCore is not always up.
* **``CORTEXSIM_SMOKE_URL`` is set** — ``scripts/smoke/lab-smoke.sh`` always
  exports it and so does the CI job: the caller has said a SimCore is there.
  If it does not answer, that is a **failure**, and a session in which no smoke
  test passed exits non-zero. A lab check that executed nothing must never
  print success.

"Answers" means HTTP 200 from ``/api/health`` with ``status`` ``ok`` or
``degraded``. ``degraded`` is the standing state of every deployment
(``api_auth`` / ``API_UNAUTHENTICATED`` until per-request authentication
lands), so waiting for ``ok`` — as this file used to — skipped every test on
every deployment while reporting "SimCore not reachable". Which components may
be degraded is ``test_health_ok``'s question, not the gate's.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Iterator

import httpx
import pytest


SMOKE_URL = os.environ.get("CORTEXSIM_SMOKE_URL", "http://localhost:8888")
SMOKE_TIMEOUT = float(os.environ.get("CORTEXSIM_SMOKE_TIMEOUT", "60"))
#: The caller named a SimCore, so "it is not there" is a failure, not a skip.
SMOKE_URL_EXPLICIT = bool(os.environ.get("CORTEXSIM_SMOKE_URL", "").strip())

#: ``/api/health`` statuses that mean "SimCore is up and answering".
REACHABLE_STATUSES = frozenset({"ok", "degraded"})

_SMOKE_DIR = os.path.dirname(os.path.abspath(__file__))


@dataclass(frozen=True)
class HealthProbe:
    reachable: bool
    reason: str


def probe_health(url: str) -> HealthProbe:
    """One ``GET /api/health``. Never raises; ``reason`` says what was seen."""
    try:
        r = httpx.get(f"{url}/api/health", timeout=2.0)
    except Exception as e:  # noqa: BLE001 — any transport failure is "no answer"
        return HealthProbe(False, f"no answer from {url}/api/health ({type(e).__name__}: {e})")
    if r.status_code != 200:
        return HealthProbe(False, f"{url}/api/health answered HTTP {r.status_code}")
    try:
        status = r.json().get("status")
    except Exception:  # noqa: BLE001 — a 200 that is not SimCore's JSON
        return HealthProbe(False, f"{url}/api/health answered 200 with a body that is not "
                                  f"a JSON object")
    if status in REACHABLE_STATUSES:
        return HealthProbe(True, f"status {status!r}")
    # An unrecognised status is NOT read as healthy, and is not reported as
    # "unreachable" either: SimCore (or something) answered, and this is what
    # it said.
    return HealthProbe(False, f"{url}/api/health answered 200 with status {status!r}, "
                              f"which this suite does not recognise "
                              f"(expected one of {sorted(REACHABLE_STATUSES)})")


def wait_for_simcore(url: str, timeout: float) -> HealthProbe:
    """Poll :func:`probe_health` until SimCore answers or ``timeout`` expires."""
    deadline = time.time() + timeout
    probe = probe_health(url)
    while not probe.reachable and time.time() < deadline:
        time.sleep(1)
        probe = probe_health(url)
    return probe


def _wait_for_healthy(url: str, timeout: float) -> bool:
    """Is SimCore answering at ``url`` (``ok`` or ``degraded``) within ``timeout``?"""
    return wait_for_simcore(url, timeout).reachable


@pytest.fixture(scope="session")
def simcore_url() -> str:
    """SimCore base URL. Unreachable: a failure when the URL was named, else a skip."""
    probe = wait_for_simcore(SMOKE_URL, SMOKE_TIMEOUT)
    if probe.reachable:
        return SMOKE_URL
    message = (
        f"SimCore did not answer at {SMOKE_URL} within {SMOKE_TIMEOUT}s: {probe.reason}. "
        "Start it via `docker compose up -d --build` or run "
        "`scripts/smoke/lab-smoke.sh` which handles the lifecycle."
    )
    if SMOKE_URL_EXPLICIT:
        pytest.fail(f"CORTEXSIM_SMOKE_URL names this SimCore, so this is a failure, "
                    f"not a skip. {message}", pytrace=False)
    pytest.skip(message)


# ---------------------------------------------------------------------------
# A named SimCore and zero smoke tests passed is never exit 0.
# ---------------------------------------------------------------------------

_outcomes = {"passed": 0, "failed": 0, "skipped": 0}
_smoke_nodeids: set[str] = set()


def pytest_collection_modifyitems(session, config, items) -> None:
    # By path, not by nodeid prefix: nodeids are relative to the rootdir, which
    # depends on where pytest was started from.
    for item in items:
        try:
            inside = os.path.commonpath(
                [_SMOKE_DIR, os.path.abspath(str(item.path))]) == _SMOKE_DIR
        except ValueError:  # different drives on Windows
            inside = False
        if inside:
            _smoke_nodeids.add(item.nodeid)


def pytest_runtest_logreport(report) -> None:
    if report.nodeid not in _smoke_nodeids:
        return
    if report.when == "call" and report.passed:
        _outcomes["passed"] += 1
    elif report.failed:
        _outcomes["failed"] += 1
    elif report.skipped:
        _outcomes["skipped"] += 1


def _nothing_ran() -> bool:
    return (SMOKE_URL_EXPLICIT and _outcomes["passed"] == 0
            and (_outcomes["skipped"] + _outcomes["failed"]) > 0)


def pytest_sessionfinish(session, exitstatus) -> None:
    if _nothing_ran() and session.exitstatus == 0:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    if _nothing_ran() and _outcomes["failed"] == 0:
        terminalreporter.section("smoke gate")
        terminalreporter.write_line(
            f"FAILED: CORTEXSIM_SMOKE_URL={SMOKE_URL} was named and no smoke test "
            f"passed ({_outcomes['skipped']} skipped). A smoke run that executes "
            f"nothing is not a pass.")


@pytest.fixture(scope="session")
def client(simcore_url: str) -> Iterator[httpx.Client]:
    """HTTPX client preconfigured against SimCore."""
    with httpx.Client(base_url=simcore_url, timeout=30.0) as c:
        yield c


@pytest.fixture(scope="session")
def known_scenario_id(client: httpx.Client) -> str:
    """Return a scenario_id that is guaranteed to be loaded.

    Prefers ``SIM-EDR-001`` (credential dumping — present in seed data) but
    falls back to the first scenario the API reports if the catalogue moves.
    """
    preferred = "SIM-EDR-001"
    r = client.get("/api/scenarios")
    r.raise_for_status()
    scenarios = r.json().get("scenarios", [])
    ids = {s["scenario_id"] for s in scenarios}
    if preferred in ids:
        return preferred
    if not scenarios:
        pytest.skip("SimCore has zero scenarios loaded — nothing to smoke-test")
    return scenarios[0]["scenario_id"]
