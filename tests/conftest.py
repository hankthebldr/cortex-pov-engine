"""Shared pytest fixtures for CortexSim tests."""
from __future__ import annotations

import ipaddress
import os
import socket
import sys
from pathlib import Path

import pytest

# Ensure core/ is on sys.path so we can import like production
REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_DIR = REPO_ROOT / "core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

# Point CORTEXSIM_BASE_DIR at the repo root before `config.settings` is
# first imported, so module-level path derivations (e.g. api.infra) resolve
# to the real infra/ tree rather than the Docker default of /app.
os.environ.setdefault("CORTEXSIM_BASE_DIR", str(REPO_ROOT))


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def fixtures_dir() -> Path:
    return REPO_ROOT / "tests" / "fixtures"


# ---------------------------------------------------------------------------
# Hermetic payload shelf
# ---------------------------------------------------------------------------
# The K8s delivery stages third-party tools (deepce.sh, linpeas.sh) that
# payloads/.gitignore deliberately excludes — you do not commit 1.1 MB of
# someone else's GPL/MIT code, and scripts/build-payloads.sh fetches them on
# the DC's own host instead.
#
# The consequence is that a fresh clone has an EMPTY shelf, and the generator
# (correctly) refuses to emit a manifest for a scenario declaring payloads it
# cannot digest. So without this, `make test` fails on a clean checkout and in
# CI — not because anything is broken, but because the test suite depended on
# an artifact the repo does not carry.
#
# Tests therefore get their OWN shelf, always, even on a machine where the real
# one is populated. Hermetic beats convenient: a suite whose behaviour changes
# depending on whether someone ran build-payloads.sh is a suite that passes on
# your laptop and fails in CI. The stub bytes differ from the real tools, which
# is fine — what is under test is that a declared payload RESOLVES to a digest
# and gets baked into the manifest, never the content of linpeas itself.
def _declared_payload_names() -> set[str]:
    """Every payload name the scenario corpus declares."""
    import yaml  # noqa: PLC0415

    names: set[str] = set()
    for path in (REPO_ROOT / "scenarios").rglob("*.yml"):
        if path.name.startswith("_"):
            continue
        try:
            doc = yaml.safe_load(path.read_text())
        except Exception:  # noqa: BLE001 — a malformed file is another test's problem
            continue
        if isinstance(doc, dict):
            posture = doc.get("cluster_posture") or {}
            if isinstance(posture, dict):
                names.update(posture.get("payloads") or [])
    return names


@pytest.fixture(scope="session", autouse=True)
def _hermetic_payload_shelf(tmp_path_factory):
    """Stage a deterministic stub for every payload the corpus declares."""
    shelf = tmp_path_factory.mktemp("payload-shelf")
    # Declare the shelf a test double. Stub bytes can never satisfy a pack's
    # real sha256 pin, and the alternative — downloading 8 live offensive tools
    # to run the suite — is worse. payload_shelf honours this marker only when
    # CORTEXSIM_PAYLOAD_DIST is set explicitly and the env is not production, so
    # neither the repo's payloads/ nor the image's baked-in shelf can be marked,
    # and every composition off it carries a STUB_SHELF warning.
    (shelf / ".cortexsim-stub-shelf").write_text("")
    for name in sorted(_declared_payload_names()):
        # Deterministic content -> deterministic digest, so a manifest rendered
        # twice in one session is byte-identical (the export-determinism gate
        # and the bundle-equivalence guard both rely on that).
        (shelf / name).write_text(
            f"#!/bin/sh\n# CortexSim test stub for {name} — not the real tool.\nexit 0\n"
        )
    os.environ["CORTEXSIM_PAYLOAD_DIST"] = str(shelf)
    yield shelf
    os.environ.pop("CORTEXSIM_PAYLOAD_DIST", None)


# ---------------------------------------------------------------------------
# No external network in the unit suite
# ---------------------------------------------------------------------------
# Same doctrine as the hermetic shelf above, and this one has already cost a
# day. One test launched a LIVE c2_http_beacon at testmynids.org — a real
# IDS-trigger host — with three consequences, in increasing order of how long
# they take to diagnose:
#
#   1. The suite needs the internet. Offline, or on the air-gapped customer
#      jumpbox this product is built for, it fails for reasons that have
#      nothing to do with the code.
#   2. `pytest` emits real C2-shaped egress from whatever laptop runs it. At
#      this company that is traffic a DC ends up explaining.
#   3. On macOS, resolving an external host initialises Network.framework,
#      which registers a pthread_atfork child handler. From that moment every
#      subprocess the suite forks dies with SIGSEGV *before exec* — the child
#      never starts, so it returns no stdout, no stderr and no traceback, just
#      returncode -11. That surfaced as 25 failures across 6 unrelated files
#      (offline bundle, installer e2e, xlsx export, rust-dist), every one of
#      which passes in isolation. Nothing in those failures points back here.
#      Only the OS crash report does: nw_settings_child_has_forked.
#
# Blocking the call is not sufficient on its own — production code catches
# connection errors and carries on, which is exactly how (1) and (2) stayed
# invisible. So the reach is RECORDED and the test is failed at teardown even
# if the exception was swallowed.


class ExternalNetworkBlocked(RuntimeError):
    """A test reached for a host outside loopback."""


def _needs_real_dns(host) -> bool:
    """True when resolving ``host`` would put a query on the wire.

    An IP literal resolves locally and is none of this guard's business — the
    SSRF gate in api.payloads legitimately calls getaddrinfo("169.254.169.254")
    to prove a hop is link-local, and that must keep working offline.
    """
    if isinstance(host, bytes):
        host = host.decode("utf-8", "replace")
    if not host or host in ("localhost", "localhost.localdomain"):
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return True          # a name -> needs DNS
    return False             # a literal -> resolved locally


def _is_external_addr(host) -> bool:
    """True when connecting to ``host`` leaves this machine."""
    if isinstance(host, bytes):
        host = host.decode("utf-8", "replace")
    if not host or host in ("localhost", "localhost.localdomain"):
        return False
    try:
        return not ipaddress.ip_address(host).is_loopback
    except ValueError:
        return True


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "allow_external_network: this test may reach a non-loopback host "
        "(nothing does today — adding one re-arms the macOS fork crash)",
    )


@pytest.fixture(autouse=True)
def _no_external_network(request, monkeypatch):
    if request.node.get_closest_marker("allow_external_network"):
        yield
        return

    reached: list[str] = []
    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect

    def guarded_getaddrinfo(host, port, *args, **kwargs):
        if _needs_real_dns(host):
            reached.append(f"resolved {host!r}")
            raise ExternalNetworkBlocked(f"DNS lookup for {host!r}")
        return real_getaddrinfo(host, port, *args, **kwargs)

    def guarded_connect(self, address, *args, **kwargs):
        if isinstance(address, tuple) and address and _is_external_addr(address[0]):
            reached.append(f"connected to {address[0]}")
            raise ExternalNetworkBlocked(f"connect to {address!r}")
        return real_connect(self, address, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    yield
    if reached:
        pytest.fail(
            "this test reached outside loopback: "
            + "; ".join(sorted(set(reached)))
            + ". Point it at a local sink or stub the resolver; see the "
              "comment above _no_external_network in tests/conftest.py."
        )
