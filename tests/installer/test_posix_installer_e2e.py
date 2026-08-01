"""End-to-end execution of the generated POSIX installer.

Unlike the router tests, these actually RUN the script against a live SimCore
(uvicorn in a thread) or a fault-injecting stub, on a host with **no Go
toolchain** — which is the whole point of the redesign. A `go` shim is planted
on PATH that records any invocation, so "the installer never needs a compiler"
is proven rather than asserted about the script's text.
"""
from __future__ import annotations

import http.server
import json
import os
import shutil
import socket
import subprocess
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("curl") is None,
                                reason="installer e2e needs bash + curl")


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for(url: str, timeout: float = 15.0) -> None:
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except urllib.error.HTTPError:
            return  # server is up; status doesn't matter
        except Exception:
            time.sleep(0.1)
    raise RuntimeError(f"server never came up at {url}")


@pytest.fixture
def dist_dir(tmp_path):
    """A beacon shelf holding a stand-in binary that prints its argv and exits.

    Using a stub instead of the real 5 MB Go binary keeps the test hermetic (no
    build step, no lingering process) while exercising the identical download →
    checksum → install → launch path.
    """
    d = tmp_path / "agent-dist"
    d.mkdir()
    (d / "cortexsim-agent-linux-amd64").write_text(
        "#!/bin/sh\necho \"beacon-started $*\"\n")
    (d / "cortexsim-agent-linux-arm64").write_text(
        "#!/bin/sh\necho \"beacon-started $*\"\n")
    (d / "cortexsim-agent-darwin-amd64").write_text(
        "#!/bin/sh\necho \"beacon-started $*\"\n")
    (d / "cortexsim-agent-darwin-arm64").write_text(
        "#!/bin/sh\necho \"beacon-started $*\"\n")
    return d


@pytest.fixture
def simcore(tmp_path, dist_dir, monkeypatch):
    """Real /api/agents router served over a real socket by uvicorn."""
    import uvicorn
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    os.environ["CORTEXSIM_AGENT_DIST"] = str(dist_dir)

    from api.agents import router
    from database import Base, get_db
    import models  # noqa: F401 — register tables

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/e2e.db")
    SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)

    app = FastAPI()
    app.include_router(router, prefix="/api")

    async def _get_db():
        async with SessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db

    @app.on_event("startup")
    async def _create_tables():  # schema work runs on uvicorn's own loop
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    _wait_for(f"{base}/api/agents")
    try:
        yield base
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        os.environ.pop("CORTEXSIM_AGENT_DIST", None)


@pytest.fixture
def sandbox(tmp_path):
    """Env for running the installer: hermetic install prefix + a `go` tripwire."""
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    tripwire = tmp_path / "go-was-called"
    go_shim = fakebin / "go"
    go_shim.write_text(f"#!/bin/sh\necho called >> {tripwire}\nexit 1\n")
    go_shim.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{fakebin}:{env['PATH']}"
    env["HOME"] = str(tmp_path / "home")
    env["CORTEXSIM_BIN_DIR"] = str(tmp_path / "prefix" / "bin")
    env["CORTEXSIM_LOG"] = str(tmp_path / "beacon.log")
    (tmp_path / "home").mkdir()
    return {"env": env, "tripwire": tripwire,
            "bin": tmp_path / "prefix" / "bin" / "cortexsim-agent",
            "log": tmp_path / "beacon.log"}


def _fetch(url: str) -> str:
    import urllib.request

    with urllib.request.urlopen(url, timeout=10) as r:
        return r.read().decode()


def _post(url: str, payload: dict) -> dict:
    import urllib.request

    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def _run_installer(script: str, sandbox, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-s"], input=script, capture_output=True,
                          text=True, env=sandbox["env"], timeout=timeout)


# ---------------------------------------------------------------------------
# Happy path — a customer endpoint with no compiler
# ---------------------------------------------------------------------------


def test_install_succeeds_with_no_go_toolchain_on_the_target(simcore, sandbox):
    token = _post(f"{simcore}/api/agents/enroll/tokens", {"label": "e2e"})["token"]
    script = _fetch(f"{simcore}/api/agents/install?token={token}&mode=foreground&interval=7")

    p = _run_installer(script, sandbox)
    assert p.returncode == 0, f"stdout:\n{p.stdout}\nstderr:\n{p.stderr}"

    # Downloaded, verified, installed, and launched — without a compiler.
    assert not sandbox["tripwire"].exists(), "the installer shelled out to `go`"
    assert "checksum OK" in p.stdout
    assert sandbox["bin"].exists() and os.access(sandbox["bin"], os.X_OK)
    assert "beacon-started" in p.stdout
    assert f"--server {simcore}" in p.stdout
    assert "--interval 7" in p.stdout

    # SimCore assigned the id, the script used it, and the agent is on the roster.
    agents = json.loads(_fetch(f"{simcore}/api/agents"))["agents"]
    assert len(agents) == 1
    assert f"--id {agents[0]['agent_id']}" in p.stdout

    # And the operator can see the successful install from the console.
    attempts = json.loads(_fetch(f"{simcore}/api/agents/install/attempts"))["attempts"]
    assert attempts[0]["code"] == "OK"
    assert attempts[0]["os"] == "linux"


def test_service_mode_detaches_when_no_supervisor_is_present(simcore, sandbox):
    """python:slim has no systemd — the installer must still leave the beacon
    running detached rather than dying with the shell, and must SAY it is
    unsupervised instead of implying a service was installed."""
    token = _post(f"{simcore}/api/agents/enroll/tokens", {})["token"]
    script = _fetch(f"{simcore}/api/agents/install?token={token}")  # service is the default

    p = _run_installer(script, sandbox)
    assert p.returncode == 0, f"stdout:\n{p.stdout}\nstderr:\n{p.stderr}"
    assert "setsid" in p.stdout or "detaching" in p.stdout
    assert "unsupervised" in p.stdout
    assert "uninstall:" in p.stdout

    attempts = json.loads(_fetch(f"{simcore}/api/agents/install/attempts"))["attempts"]
    assert attempts[0]["code"] == "DEGRADED_NO_SUPERVISOR"


def test_pre_staged_binary_needs_no_network_fetch(simcore, sandbox, tmp_path):
    """The air-gapped path: scp the beacon over, set CORTEXSIM_BIN."""
    staged = tmp_path / "staged-agent"
    staged.write_text("#!/bin/sh\necho \"beacon-started $*\"\n")
    staged.chmod(0o755)
    sandbox["env"]["CORTEXSIM_BIN"] = str(staged)

    token = _post(f"{simcore}/api/agents/enroll/tokens", {})["token"]
    script = _fetch(f"{simcore}/api/agents/install?token={token}&mode=foreground")
    p = _run_installer(script, sandbox)

    assert p.returncode == 0, p.stderr
    assert "using pre-staged binary" in p.stdout
    assert "fetching prebuilt beacon" not in p.stdout
    assert "beacon-started" in p.stdout


# ---------------------------------------------------------------------------
# Failure paths — every one exits with a stable code the operator can read
# ---------------------------------------------------------------------------


def test_enroll_denial_surfaces_the_servers_own_structured_detail(simcore, sandbox):
    """Regression: the old installer used `curl -f`, which discarded the body and
    replaced the server's ENROLL_DENIED detail with a guess."""
    script = _fetch(f"{simcore}/api/agents/install?token=cxs_not_a_real_token&mode=foreground")
    p = _run_installer(script, sandbox)

    assert p.returncode == 1
    assert "code=ENROLL_DENIED" in p.stderr
    assert "HTTP 403" in p.stderr
    assert "mint a fresh token" in p.stderr           # remediation, not just a symptom
    assert "POST /api/agents/enroll/tokens" in p.stderr

    attempts = json.loads(_fetch(f"{simcore}/api/agents/install/attempts"))["attempts"]
    assert attempts[0]["code"] == "ENROLL_DENIED"     # visible in SimCore, not only on the target
    assert attempts[0]["stage"] == "enroll"


def test_empty_shelf_fails_with_the_command_that_fixes_it(simcore, sandbox, dist_dir):
    for f in dist_dir.iterdir():
        f.unlink()
    token = _post(f"{simcore}/api/agents/enroll/tokens", {})["token"]
    script = _fetch(f"{simcore}/api/agents/install?token={token}&mode=foreground")

    p = _run_installer(script, sandbox)
    assert p.returncode == 1
    assert "code=BINARY_UNAVAILABLE" in p.stderr
    assert "make agent-dist" in p.stderr
    assert "CORTEXSIM_BIN" in p.stderr                # the air-gapped alternative
    assert not sandbox["tripwire"].exists()           # never silently fell back to a compiler

    attempts = json.loads(_fetch(f"{simcore}/api/agents/install/attempts"))["attempts"]
    assert attempts[0]["code"] == "BINARY_UNAVAILABLE"


def test_unreachable_server_is_distinguished_from_a_rejected_token(sandbox, simcore):
    script = _fetch(f"{simcore}/api/agents/install?token=cxs_x&mode=foreground")
    # Repoint the script at a closed port: same script, different failure mode.
    sandbox["env"]["CORTEXSIM_SERVER"] = f"http://127.0.0.1:{_free_port()}"
    sandbox["env"]["CORTEXSIM_TELEMETRY"] = "0"  # nothing is listening to report to
    p = _run_installer(script, sandbox)

    assert p.returncode == 1
    assert "code=SERVER_UNREACHABLE" in p.stderr
    assert "firewall" in p.stderr


# ---------------------------------------------------------------------------
# Integrity — a tampered binary must never be executed
# ---------------------------------------------------------------------------


class _TamperingHandler(http.server.BaseHTTPRequestHandler):
    """Serves a beacon whose bytes do NOT match the advertised digest."""

    reports: list = []

    def log_message(self, *args):  # silence the default stderr spam
        pass

    def do_GET(self):
        if self.path.startswith("/api/agents/binary/sha256"):
            self._send(200, b"0" * 64 + b"\n")
        elif self.path.startswith("/api/agents/binary"):
            self._send(200, b"#!/bin/sh\necho TAMPERED\n")
        else:
            self._send(404, b"{}")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        if self.path.startswith("/api/agents/install/telemetry"):
            type(self).reports.append(json.loads(body))
        self._send(200, b'{"status":"ok"}')

    def _send(self, code: int, body: bytes):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_checksum_mismatch_aborts_before_the_binary_is_installed(simcore, sandbox):
    _TamperingHandler.reports = []
    port = _free_port()
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), _TamperingHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        # No token → the script skips enrollment and goes straight to the fetch,
        # so the stub only has to model the binary endpoints.
        script = _fetch(f"{simcore}/api/agents/install?id=box-1&mode=foreground")
        sandbox["env"]["CORTEXSIM_SERVER"] = f"http://127.0.0.1:{port}"
        p = _run_installer(script, sandbox)
    finally:
        httpd.shutdown()

    assert p.returncode == 1
    assert "code=CHECKSUM_MISMATCH" in p.stderr
    assert "do NOT run this binary" in p.stderr
    assert "TAMPERED" not in p.stdout                  # it was never executed
    assert not sandbox["bin"].exists()                 # nor installed
    assert any(r["code"] == "CHECKSUM_MISMATCH" for r in _TamperingHandler.reports)
