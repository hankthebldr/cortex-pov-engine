"""The smoke suite must run against a SimCore that answers (RG-GUARD-02 / RG-CI-01).

``tests/smoke/conftest.py`` waited for ``/api/health`` to say ``status: "ok"``
and skipped the whole suite otherwise. Round 1 added the ``api_auth`` component,
which is ``degraded`` on every deployment by design, so ``status`` is never
``ok`` again. Observed on c852eac against a fully staged SimCore:

    $ CORTEXSIM_SMOKE_URL=http://127.0.0.1:18961 pytest tests/smoke -q -rs
    16 skipped            (reason: "SimCore not reachable ... within 3.0s")
    $ scripts/smoke/lab-smoke.sh --target=jumpbox --url=...
    [lab-smoke] ✓ all smoke tests passed

A check that executes nothing and prints success, in the script TESTING.md
gives a DC to verify a lab before a POV.

These run the REAL smoke conftest in a child pytest against a stdlib HTTP stub,
so the backend job (which ``--ignore``s ``tests/smoke``) still guards the gate.
They live here, not in ``tests/smoke/``, for that reason.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

HEALTH_OK = "tests/smoke/test_health_and_catalog.py::test_health_ok"
#: Skips on its own when SimCore reports zero scenarios (``known_scenario_id``).
NEEDS_A_SCENARIO = "tests/smoke/test_run_lifecycle_push.py::test_launch_invalid_mode_rejected"


def _health(*degraded: str, codes: Optional[dict[str, str]] = None,
            status: Optional[str] = None) -> dict:
    codes = {"api_auth": "API_UNAUTHENTICATED", **(codes or {})}
    components = {name: {"status": "degraded", "code": codes.get(name, "X"),
                         "detail": "…", "declared": 8, "staged": 0}
                  for name in degraded}
    return {"status": status or ("degraded" if degraded else "ok"),
            "version": "0.0.0-stub", "components": components,
            "degraded_components": sorted(degraded)}


class _Stub:
    """A stand-in SimCore: ``GET /api/health`` plus any extra JSON routes."""

    def __init__(self, health: dict, routes: Optional[dict[str, dict]] = None) -> None:
        table = {"/api/health": health, **(routes or {})}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 — http.server's name
                body = table.get(self.path.split("?", 1)[0])
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                payload = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):  # silence
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "_Stub":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()


def _closed_port_url() -> str:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{s.getsockname()[1]}"


def _smoke(*node_ids: str, url: Optional[str], timeout: str = "2") -> subprocess.CompletedProcess:
    """Run smoke tests in a child pytest, the way lab-smoke.sh does."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("CORTEXSIM_SMOKE")}
    env["CORTEXSIM_SMOKE_TIMEOUT"] = timeout
    if url is not None:
        env["CORTEXSIM_SMOKE_URL"] = url
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider", *node_ids],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120)


# -- the gate: SimCore answers, so the suite runs --------------------------


def test_a_degraded_simcore_is_reachable_and_the_suite_runs():
    """``api_auth`` alone is the standing state of every deployment."""
    with _Stub(_health("api_auth")) as stub:
        out = _smoke(HEALTH_OK, url=stub.url)
    assert "1 passed" in out.stdout, out.stdout + out.stderr
    assert "skipped" not in out.stdout, out.stdout
    assert out.returncode == 0


def test_the_probe_reads_degraded_as_reachable():
    import tests.smoke.conftest as smoke_conftest

    with _Stub(_health("api_auth")) as stub:
        assert smoke_conftest.probe_health(stub.url).reachable is True
        assert smoke_conftest._wait_for_healthy(stub.url, 2.0) is True


def test_an_unrecognised_health_status_is_not_reachable_and_says_what_it_saw():
    """A status this suite does not know is not silently read as healthy — and
    the reason names it, instead of claiming SimCore is unreachable."""
    import tests.smoke.conftest as smoke_conftest

    with _Stub(_health(status="starting")) as stub:
        probe = smoke_conftest.probe_health(stub.url)
    assert probe.reachable is False
    assert "starting" in probe.reason


# -- zero tests executed is never a pass when the SimCore was named --------


def test_a_named_simcore_that_does_not_answer_is_a_failure_not_a_skip():
    """lab-smoke.sh and the CI job always name the URL."""
    out = _smoke(HEALTH_OK, url=_closed_port_url(), timeout="1")
    assert out.returncode != 0, out.stdout + out.stderr
    assert "no answer" in out.stdout, out.stdout
    assert "passed" not in out.stdout.splitlines()[-1], out.stdout


def test_all_skipped_with_a_named_simcore_exits_non_zero():
    """Any OTHER skip path too: SimCore answers but has no scenarios, the one
    selected test skips, nothing passed — and the session must not exit 0."""
    with _Stub(_health("api_auth"), routes={"/api/scenarios": {"scenarios": []}}) as stub:
        out = _smoke(NEEDS_A_SCENARIO, url=stub.url)
    assert "1 skipped" in out.stdout, out.stdout + out.stderr
    assert out.returncode != 0, out.stdout
    assert "no smoke test passed" in out.stdout, out.stdout


def test_an_unnamed_default_simcore_that_is_down_still_skips():
    """No ``CORTEXSIM_SMOKE_URL``: the documented "safe to run anywhere" skip."""
    import tests.smoke.conftest as smoke_conftest

    probe = smoke_conftest.probe_health(_closed_port_url())
    assert probe.reachable is False
    assert "no answer" in probe.reason
    out = _smoke(HEALTH_OK, url=None, timeout="1")
    if "1 skipped" in out.stdout:           # nothing on localhost:8888
        assert out.returncode == 0, out.stdout
    else:                                   # a dev SimCore happens to be up
        assert "skipped" not in out.stdout.splitlines()[-1], out.stdout


# -- what test_health_ok tolerates -----------------------------------------


def test_an_unstaged_payload_shelf_does_not_fail_the_health_smoke():
    """A fresh checkout has an empty shelf (gitignored build artifacts). That
    is a legitimate state, and it is SAID, not hidden."""
    body = _health("api_auth", "payload_shelf",
                   codes={"payload_shelf": "SHELF_ARTIFACTS_MISSING"})
    with _Stub(body) as stub:
        out = _smoke(HEALTH_OK, url=stub.url)
    assert "1 passed" in out.stdout, out.stdout + out.stderr
    assert "SHELF_ARTIFACTS_MISSING" in out.stdout, out.stdout


@pytest.mark.parametrize("body, named", [
    (_health("api_auth", "payload_shelf", codes={"payload_shelf": "SHELF_ARTIFACT_UNPINNED"}),
     "payload_shelf (SHELF_ARTIFACT_UNPINNED)"),
    (_health("api_auth", "scenario_catalog", codes={"scenario_catalog": "SCENARIO_CATALOG_EMPTY"}),
     "scenario_catalog (SCENARIO_CATALOG_EMPTY)"),
    # The allowance is the (component, code) pair, never the component name.
    (_health("api_auth", codes={"api_auth": "SOMETHING_ELSE"}), "api_auth (SOMETHING_ELSE)"),
], ids=["shelf-unpinned", "catalog-empty", "api-auth-other-code"])
def test_any_other_degraded_component_still_fails_the_health_smoke(body, named):
    with _Stub(body) as stub:
        out = _smoke(HEALTH_OK, url=stub.url)
    assert out.returncode != 0, out.stdout
    assert "unexpected degraded components" in out.stdout, out.stdout
    assert named in out.stdout, out.stdout
