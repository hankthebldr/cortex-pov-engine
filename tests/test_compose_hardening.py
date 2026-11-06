"""SEC-03 / OPS-11 / SEC-10 — the shipped container is not a root-on-host gift.

* SEC-03: ``docker-compose.yml`` bind-mounted ``/var/run/docker.sock`` into a
  container that runs as root and exposes an unauthenticated API. Nothing in
  ``core/`` talks to Docker, so the mount bought nothing and handed anyone who
  reached a command-execution path (SEC-02) the host's Docker daemon — i.e.
  root on the DC's jumpbox.
* OPS-11: ``docker compose down`` / ``restart`` sends SIGTERM and then, after
  compose's 10 s default, SIGKILL. uvicorn's default graceful shutdown waits
  for every open connection — and the console holds SSE streams open forever —
  so shutdown never finished and the process was SIGKILLed mid-write. uvicorn
  now gets a 5 s graceful budget and compose a 15 s grace period, so the
  lifespan shutdown (cancel the sweep / reconcile tasks) runs before the kill.
* SEC-10: the publish address was implicit (``"8888:8888"`` == every host
  interface). It is now explicit and operator-settable (``CORTEXSIM_BIND``) so
  a DC can publish on 127.0.0.1 or the lab NIC only.

Parsed with yaml / read as text: there is no ``docker`` on the test host, and
"the file says X" is exactly the property that drifts.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load((REPO / "docker-compose.yml").read_text())


@pytest.fixture(scope="module")
def simcore(compose) -> dict:
    return compose["services"]["simcore"]


def test_docker_socket_is_not_mounted(simcore):
    mounts = [str(v) for v in simcore.get("volumes") or []]
    offenders = [m for m in mounts if "docker.sock" in m]
    assert not offenders, (
        f"simcore mounts the Docker socket ({offenders}). Nothing in core/ uses "
        f"it, and with it any command execution in the container is root on the "
        f"host."
    )


def test_no_service_mounts_the_docker_socket(compose):
    raw = (REPO / "docker-compose.yml").read_text()
    # Comments may explain why it is absent; a mount line may not carry it.
    live = [ln for ln in raw.splitlines()
            if "docker.sock" in ln and not ln.lstrip().startswith("#")]
    assert not live, live


def test_stop_grace_period_is_set(simcore):
    grace = simcore.get("stop_grace_period")
    assert grace, "simcore has no stop_grace_period — compose SIGKILLs after 10s"
    m = re.fullmatch(r"(\d+)s", str(grace))
    assert m, f"stop_grace_period {grace!r} is not a plain '<n>s' duration"
    assert int(m.group(1)) >= 10, (
        "the grace period must exceed uvicorn's --timeout-graceful-shutdown (5s) "
        "plus lifespan shutdown, or SIGKILL still lands mid-shutdown"
    )


def test_port_publish_address_is_explicit_and_operator_settable(simcore):
    ports = [str(p) for p in simcore.get("ports") or []]
    assert ports, "simcore publishes no port"
    for p in ports:
        assert p.startswith("${CORTEXSIM_BIND:-0.0.0.0}:"), (
            f"port mapping {p!r} does not name its bind address — set it via "
            f"CORTEXSIM_BIND so a DC can publish on 127.0.0.1 or one NIC"
        )


def test_bind_and_host_guard_settings_reach_the_container(simcore):
    """The app names the bind in its boot warning and /api/health, and reads
    the host allowlist + CORS opt-in from its own environment — compose must
    pass them through or the .env values never reach the process."""
    env = "\n".join(str(e) for e in simcore.get("environment") or [])
    for var in ("CORTEXSIM_BIND", "CORTEXSIM_ALLOWED_HOSTS", "CORTEXSIM_CORS_ORIGINS"):
        assert f"{var}=" in env, f"{var} is not passed into the simcore container"


def _dockerfile_cmd() -> list[str]:
    lines = (REPO / "core" / "Dockerfile").read_text().splitlines()
    cmds = [ln for ln in lines if ln.startswith("CMD ")]
    assert cmds, "core/Dockerfile has no CMD"
    return json.loads(cmds[-1][len("CMD "):])


def test_dockerfile_cmd_bounds_graceful_shutdown():
    cmd = _dockerfile_cmd()
    assert cmd[0] == "uvicorn", cmd
    assert "--timeout-graceful-shutdown" in cmd, (
        "uvicorn waits forever for open SSE streams on SIGTERM without "
        "--timeout-graceful-shutdown; compose then SIGKILLs it mid-write"
    )
    value = cmd[cmd.index("--timeout-graceful-shutdown") + 1]
    assert value.isdigit() and 1 <= int(value) <= 10, value


def test_installed_uvicorn_accepts_the_flag():
    """The image pins uvicorn in core/requirements.txt; the venv this runs in
    (the prod image, in CI) is that pin. An option the installed uvicorn does
    not know makes the container exit at start, which no other test sees."""
    from uvicorn.main import main as uvicorn_cli

    opts = {o for p in uvicorn_cli.params for o in getattr(p, "opts", [])}
    assert "--timeout-graceful-shutdown" in opts, sorted(opts)
