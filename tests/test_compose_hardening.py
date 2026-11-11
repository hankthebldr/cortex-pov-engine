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
* RG-GUARD-09 / RG-INSTALL-06: SEC-10 spelled the default as
  ``${CORTEXSIM_BIND:-0.0.0.0}``. Docker publishes a mapping that names no host
  address on IPv4 AND IPv6, and one that names 0.0.0.0 on IPv4 only, so the
  default silently stopped answering beacons that reach the jumpbox over IPv6.
  The mapping is now the long form with ``host_ip: ${CORTEXSIM_BIND:-}``: unset
  means no host address (both families, as before SEC-10), set means that
  address.
* OPS-14: the json-file log driver grows without limit unless told otherwise.
* OPS-16: nothing told Docker how to ask SimCore whether it is alive.
* OPS-21: the orchestrator's queue, the SSE bus and the rehydrate pass are per
  process. uvicorn reads ``WEB_CONCURRENCY`` when ``--workers`` is not given,
  so one environment variable forked N SimCores that each rehydrated and
  delivered the same queued tasks.

Parsed with yaml / read as text: there is no ``docker`` on the test host, and
"the file says X" is exactly the property that drifts. NOTHING here ran a
container: what Docker does with these files is authored, not observed.
"""
from __future__ import annotations

import asyncio
import json
import re
import shlex
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent

_INTERP = re.compile(r"\$\{([^}]*)\}")


def _interpolate(value: str, env: dict[str, str]) -> str:
    """Compose's ``${VAR}`` / ``${VAR:-default}`` / ``${VAR-default}`` /
    ``${VAR:?error}`` forms, which every Compose release reads. Any other form
    raises: ``${VAR:+x}`` needs a newer Compose than some jumpboxes carry, and
    nobody can run ``docker compose config`` on the test host to find out."""
    def _one(match: re.Match) -> str:
        expr = match.group(1)
        m = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)(?:(:-|-|:\?|\?)(.*))?", expr, re.S)
        if not m:
            raise AssertionError(f"unsupported compose interpolation: ${{{expr}}}")
        name, op, arg = m.group(1), m.group(2), m.group(3) or ""
        cur = env.get(name)
        if op in (":-",):
            return cur if cur else arg
        if op == "-":
            return arg if cur is None else cur
        if op in (":?", "?"):
            assert cur, f"compose would refuse to start: {arg}"
            return cur
        return cur or ""
    return _INTERP.sub(_one, value)


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))


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
    raw = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
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


def _only_port(simcore) -> dict:
    ports = simcore.get("ports") or []
    assert len(ports) == 1, f"simcore should publish exactly one port, got {ports}"
    port = ports[0]
    assert isinstance(port, dict), (
        f"port mapping {port!r} is the short string form. It cannot express 'no "
        f"host address unless CORTEXSIM_BIND is set' without ${{VAR:+...}}, and a "
        f"literal default address makes the publish IPv4-only"
    )
    return port


def test_compose_port_publish_address_is_operator_settable(simcore):
    """SEC-10: a DC can publish on 127.0.0.1 or one NIC through CORTEXSIM_BIND."""
    port = _only_port(simcore)
    assert port.get("target") == 8888, port
    assert port.get("protocol", "tcp") == "tcp", port
    assert "CORTEXSIM_BIND" in str(port.get("host_ip", "")), (
        f"port mapping {port!r} does not take its bind address from CORTEXSIM_BIND"
    )
    for bind in ("127.0.0.1", "10.20.30.40", "::1", "fd00::10"):
        assert _interpolate(str(port["host_ip"]), {"CORTEXSIM_BIND": bind}) == bind
    assert _interpolate(str(port["published"]), {}) == "8888"
    assert _interpolate(str(port["published"]), {"CORTEXSIM_PORT": "9443"}) == "9443"


def test_compose_default_publish_is_not_ipv4_only(simcore):
    """RG-GUARD-09 / RG-INSTALL-06. With CORTEXSIM_BIND unset (and with the
    empty value .env.example ships) the mapping must name NO host address, so
    Docker publishes on IPv4 and IPv6 as the bare "8888:8888" did before SEC-10.
    A default of 0.0.0.0 is IPv4-only."""
    port = _only_port(simcore)
    for env in ({}, {"CORTEXSIM_BIND": ""}):
        host_ip = _interpolate(str(port.get("host_ip", "")), env)
        assert host_ip == "", (
            f"with CORTEXSIM_BIND={env.get('CORTEXSIM_BIND', '<unset>')!r} the port is "
            f"published on {host_ip!r} only; an endpoint that reaches the jumpbox "
            f"over IPv6 is refused the connection"
        )


def test_compose_reports_an_unset_bind_to_the_app_as_unset(simcore):
    """/api/health and the boot warning name the exposure from CORTEXSIM_BIND.
    Passing a literal 0.0.0.0 when nothing is set would have them name an
    IPv4-only publish that is no longer what compose does."""
    entries = [str(e) for e in simcore.get("environment") or []]
    bind = [e for e in entries if e.startswith("CORTEXSIM_BIND=")]
    assert len(bind) == 1, bind
    assert _interpolate(bind[0], {}) == "CORTEXSIM_BIND="
    assert _interpolate(bind[0], {"CORTEXSIM_BIND": "127.0.0.1"}) == "CORTEXSIM_BIND=127.0.0.1"


@pytest.mark.parametrize("name", ["docker-compose.yml", "docker-compose.dev.yml"])
def test_compose_files_use_only_interpolation_every_compose_reads(name):
    raw = (REPO / name).read_text(encoding="utf-8")
    live = "\n".join(ln for ln in raw.splitlines() if not ln.lstrip().startswith("#"))
    env = {"CORTEXSIM_SECRET": "x" * 40}
    _interpolate(live, env)  # raises on ${VAR:+...} and anything else unknown


def test_compose_container_logs_are_rotated(simcore):
    """OPS-14. json-file is Docker's default driver and keeps every line ever
    written unless max-size/max-file say otherwise; the app's own rotating file
    handler caps only its file copy, not the container's stdout."""
    logging_cfg = simcore.get("logging") or {}
    assert logging_cfg.get("driver") == "json-file", (
        f"simcore sets no log driver options ({logging_cfg!r}): container stdout "
        f"grows under /var/lib/docker until the jumpbox's disk is full"
    )
    opts = logging_cfg.get("options") or {}
    size = re.fullmatch(r"(\d+)([km])", str(opts.get("max-size", "")))
    assert size, f"max-size {opts.get('max-size')!r} is not '<n>k' or '<n>m'"
    megabytes = int(size.group(1)) / (1024 if size.group(2) == "k" else 1)
    files = int(str(opts.get("max-file", "0")))
    assert files >= 2, "max-file below 2 keeps no rotated history at all"
    assert 0 < megabytes * files <= 500, f"log cap of {megabytes * files:.0f} MB is not a cap"
    # Compose passes driver options through as strings; a bare YAML integer is
    # refused by some releases.
    assert all(isinstance(v, str) for v in opts.values()), opts


def test_compose_does_not_disable_the_image_healthcheck(compose):
    for name, svc in compose["services"].items():
        hc = svc.get("healthcheck") or {}
        assert not hc.get("disable"), f"{name} disables the image HEALTHCHECK"
        assert hc.get("test") not in (["NONE"], "NONE"), f"{name} disables the image HEALTHCHECK"


def test_bind_and_host_guard_settings_reach_the_container(simcore):
    """The app names the bind in its boot warning and /api/health, and reads
    the host allowlist + CORS opt-in from its own environment — compose must
    pass them through or the .env values never reach the process."""
    env = "\n".join(str(e) for e in simcore.get("environment") or [])
    for var in ("CORTEXSIM_BIND", "CORTEXSIM_ALLOWED_HOSTS", "CORTEXSIM_CORS_ORIGINS"):
        assert f"{var}=" in env, f"{var} is not passed into the simcore container"


def _dockerfile_cmd() -> list[str]:
    lines = (REPO / "core" / "Dockerfile").read_text(encoding="utf-8").splitlines()
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
    assert "--workers" in opts, sorted(opts)


# --------------------------------------------------------------------------
# OPS-21 — one SimCore process, whatever the environment says
# --------------------------------------------------------------------------

def _effective_workers(argv: list[str], monkeypatch, web_concurrency: str | None) -> int:
    """Parse ``argv`` with the installed uvicorn's own CLI, then build its
    Config: the number uvicorn would actually fork."""
    import uvicorn
    from uvicorn.main import main as uvicorn_cli

    if web_concurrency is None:
        monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    else:
        monkeypatch.setenv("WEB_CONCURRENCY", web_concurrency)
    ctx = uvicorn_cli.make_context("uvicorn", list(argv[1:]))
    return uvicorn.Config(app=ctx.params["app"], workers=ctx.params["workers"],
                          log_config=None).workers


def test_dockerfile_cmd_pins_a_single_worker():
    cmd = _dockerfile_cmd()
    assert "--workers" in cmd, (
        "the CMD does not pin --workers, so uvicorn takes the count from "
        "$WEB_CONCURRENCY: each worker gets its own task queue and SSE bus and "
        "rehydrates the same queued_tasks rows"
    )
    assert cmd[cmd.index("--workers") + 1] == "1", cmd


def test_dockerfile_cmd_runs_one_process_even_with_web_concurrency_set(monkeypatch):
    cmd = _dockerfile_cmd()
    assert _effective_workers(cmd, monkeypatch, None) == 1
    assert _effective_workers(cmd, monkeypatch, "4") == 1, (
        "WEB_CONCURRENCY=4 in the container's environment forks 4 SimCore processes"
    )


def test_web_concurrency_really_is_read_when_workers_is_not_pinned(monkeypatch):
    """The control for the test above: the hazard exists in the installed
    uvicorn, so pinning --workers is what removes it."""
    unpinned = ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8888"]
    assert _effective_workers(unpinned, monkeypatch, "4") == 4


def test_compose_dev_overlay_cannot_fork_workers():
    """The dev overlay replaces the CMD. --reload makes uvicorn ignore the
    worker count; without it the overlay would need its own --workers 1."""
    overlay = yaml.safe_load((REPO / "docker-compose.dev.yml").read_text(encoding="utf-8"))
    command = overlay["services"]["simcore"].get("command")
    assert command, "the dev overlay no longer overrides the command"
    argv = command if isinstance(command, list) else shlex.split(command)
    pinned = "--workers" in argv and argv[argv.index("--workers") + 1] == "1"
    assert "--reload" in argv or pinned, argv


# --------------------------------------------------------------------------
# OPS-16 — the image says how to ask SimCore whether it is alive
# --------------------------------------------------------------------------

def _dockerfile_final_stage() -> str:
    text = (REPO / "core" / "Dockerfile").read_text(encoding="utf-8")
    joined = re.sub(r"\\\n", " ", text)  # fold line continuations
    stages = re.split(r"(?m)^FROM\s", joined)
    return stages[-1]


def _healthcheck() -> tuple[dict[str, str], list[str]]:
    lines = [ln for ln in _dockerfile_final_stage().splitlines() if ln.startswith("HEALTHCHECK ")]
    assert lines, (
        "core/Dockerfile's runtime stage has no HEALTHCHECK: a wedged SimCore "
        "reads 'Up' in `docker ps` while every beacon poll times out"
    )
    assert len(lines) == 1, lines
    head, sep, tail = lines[0][len("HEALTHCHECK "):].partition(" CMD ")
    assert sep, f"HEALTHCHECK has no CMD: {lines[0]!r}"
    options = dict(tok.lstrip("-").split("=", 1) for tok in head.split())
    probe = json.loads(tail)  # exec form: no shell between Docker and curl
    assert isinstance(probe, list), "HEALTHCHECK CMD is not the exec (JSON array) form"
    return options, probe


def _seconds(value: str) -> int:
    m = re.fullmatch(r"(\d+)s", value)
    assert m, f"{value!r} is not a plain '<n>s' duration"
    return int(m.group(1))


def test_dockerfile_healthcheck_probes_health_on_the_cmd_port():
    options, probe = _healthcheck()
    cmd = _dockerfile_cmd()
    port = cmd[cmd.index("--port") + 1]
    assert probe[0] == "curl", probe
    urls = [a for a in probe if a.startswith("http")]
    assert urls == [f"http://127.0.0.1:{port}/api/health"], (
        f"the probe must ask the port uvicorn is started on ({port}) over a "
        f"loopback IP literal, which the Host guard always admits: {urls}"
    )
    # -f: a non-2xx is a failure. /api/health is 200 whether ok or degraded, so
    # this measures 'the event loop answers', never 'nothing is degraded'.
    assert any(a.startswith("-") and not a.startswith("--") and "f" in a for a in probe), probe


def test_dockerfile_healthcheck_is_bounded():
    options, probe = _healthcheck()
    assert "--max-time" in probe, "curl has no --max-time: a wedged loop hangs the probe"
    max_time = int(probe[probe.index("--max-time") + 1])
    assert max_time < _seconds(options["timeout"]), (
        "curl must give up before Docker's --timeout kills it, or the failure "
        "is reported as a timeout with no output"
    )
    assert _seconds(options["interval"]) <= 60
    assert _seconds(options["start-period"]) >= 30, (
        "boot loads the scenario corpus before uvicorn listens; without a start "
        "period the container is 'unhealthy' during every normal start"
    )
    assert int(options["retries"]) >= 2


def test_dockerfile_runtime_stage_installs_the_probe_binary():
    stage = _dockerfile_final_stage()
    installs = [ln for ln in stage.splitlines() if "apt-get install" in ln]
    assert any(re.search(r"\bcurl\b", ln) for ln in installs), (
        "the HEALTHCHECK runs curl, which the runtime stage does not install"
    )


def test_dockerfile_healthcheck_host_is_admitted_with_an_empty_allowlist():
    """The probe runs inside the container, where the hostname is the container
    id and CORTEXSIM_ALLOWED_HOSTS may be empty. A 400 HOST_NOT_ALLOWED would
    mark every healthy SimCore unhealthy."""
    from urllib.parse import urlsplit

    from security.host_guard import HostGuardMiddleware

    _, probe = _healthcheck()
    url = next(a for a in probe if a.startswith("http"))
    guard = HostGuardMiddleware(app=None, allowed_hosts=[],
                                hostname_fn=lambda: "3f2a9c1d7b6e",
                                fqdn_fn=lambda: "3f2a9c1d7b6e")
    assert asyncio.run(guard.is_allowed(urlsplit(url).netloc))
