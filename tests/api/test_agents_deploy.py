"""Agent deployment surface: prebuilt-beacon distribution, the compiler-free
one-line installer, supervised install, and install telemetry.

These guard the three properties that make onboarding work on a real customer
endpoint: (1) no Go toolchain / public-Go-proxy dependency on the target,
(2) the beacon is installed under a supervisor so it outlives the SSH session,
(3) every failure carries a stable code that reaches the operator's console.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess

import pytest


@pytest.fixture
def client(make_client):
    from api.agents import router
    return make_client(router)


@pytest.fixture
def dist(tmp_path, monkeypatch):
    """Point the binary shelf at a hermetic temp dir with two fake beacons."""
    monkeypatch.setenv("CORTEXSIM_AGENT_DIST", str(tmp_path))
    (tmp_path / "cortexsim-agent-linux-amd64").write_bytes(b"\x7fELF-fake-linux-amd64")
    (tmp_path / "cortexsim-agent-darwin-arm64").write_bytes(b"\xcf\xfa\xed\xfe-fake-darwin-arm64")
    return tmp_path


@pytest.fixture
def empty_dist(tmp_path, monkeypatch):
    monkeypatch.setenv("CORTEXSIM_AGENT_DIST", str(tmp_path / "nothing-here"))
    return tmp_path


# ---------------------------------------------------------------------------
# Prebuilt beacon distribution
# ---------------------------------------------------------------------------


def test_binaries_inventory_lists_what_is_on_the_shelf(client, dist):
    body = client.get("/api/agents/binaries").json()
    pairs = {(b["os"], b["arch"]) for b in body["binaries"]}
    assert pairs == {("linux", "amd64"), ("darwin", "arm64")}
    assert body["total"] == 2
    entry = next(b for b in body["binaries"] if b["os"] == "linux")
    assert entry["sha256"] == hashlib.sha256(b"\x7fELF-fake-linux-amd64").hexdigest()
    assert entry["size_bytes"] == len(b"\x7fELF-fake-linux-amd64")


def test_binary_download_serves_bytes_and_echoes_digest(client, dist):
    r = client.get("/api/agents/binary?os=linux&arch=amd64")
    assert r.status_code == 200
    assert r.content == b"\x7fELF-fake-linux-amd64"
    assert r.headers["X-CortexSim-Agent-SHA256"] == hashlib.sha256(r.content).hexdigest()


def test_binary_sha256_endpoint_matches_the_served_bytes(client, dist):
    served = client.get("/api/agents/binary?os=linux&arch=amd64").content
    digest = client.get("/api/agents/binary/sha256?os=linux&arch=amd64").text.strip()
    assert digest == hashlib.sha256(served).hexdigest()


def test_uname_spellings_resolve_to_goos_goarch(client, dist):
    """`uname -s`/`uname -m` give Darwin/arm64 and x86_64 — the installer must
    not have to translate."""
    r = client.get("/api/agents/binary?os=macos&arch=aarch64")
    assert r.status_code == 200
    assert r.content == b"\xcf\xfa\xed\xfe-fake-darwin-arm64"
    assert client.get("/api/agents/binary?os=linux&arch=x86_64").status_code == 200


def test_missing_target_is_a_structured_404_naming_the_remedy(client, dist):
    r = client.get("/api/agents/binary?os=linux&arch=arm64")
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert detail["code"] == "AGENT_BINARY_UNAVAILABLE"
    assert {"error", "code", "detail"} <= set(detail)
    # The operator must learn what to run, not just that it is missing.
    assert "make agent-dist" in detail["detail"]
    assert "linux/amd64" in detail["detail"]  # what IS available


def test_unknown_arch_is_a_structured_400(client, dist):
    r = client.get("/api/agents/binary?os=linux&arch=sparc")
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "BAD_ARCH"


def test_unknown_os_is_a_structured_400(client, dist):
    r = client.get("/api/agents/binary?os=plan9&arch=amd64")
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "BAD_OS"


def test_empty_shelf_is_a_valid_state(client, empty_dist):
    body = client.get("/api/agents/binaries").json()
    assert body == {"binaries": [], "total": 0, "dist_dir": body["dist_dir"]}


# ---------------------------------------------------------------------------
# The installer: no compiler, no public-internet egress
# ---------------------------------------------------------------------------


def test_installer_never_requires_go_or_the_public_module_proxy(client, dist):
    script = client.get("/api/agents/install?os=linux").text
    # The old path shelled out to `go install github.com/hankthebldr/cortexsim/agent@latest`
    # — a module path that does not exist, from a proxy a hardened network blocks.
    assert "go install" not in script
    assert "github.com/hankthebldr/cortexsim/agent" not in script
    assert "proxy.golang.org" not in script
    assert "go.dev/dl" not in script
    # It fetches the prebuilt beacon from SimCore and verifies it instead.
    assert "/api/agents/binary?os=$OS_ID&arch=$ARCH_ID" in script
    assert "/api/agents/binary/sha256" in script
    assert "CHECKSUM_MISMATCH" in script


def test_installer_keeps_a_source_build_escape_hatch(client, dist):
    """A dev box with the repo checked out must still be able to build."""
    script = client.get("/api/agents/install?os=linux").text
    assert "CORTEXSIM_SRC" in script
    assert "CORTEXSIM_BIN" in script  # air-gapped: scp the binary in


def test_installer_detects_arch_so_one_url_serves_every_endpoint(client, dist):
    script = client.get("/api/agents/install?os=linux").text
    assert "uname -m" in script and "aarch64" in script and "x86_64" in script


# ---------------------------------------------------------------------------
# Supervised install — surviving the SSH session
# ---------------------------------------------------------------------------


def test_service_mode_installs_a_supervisor_not_a_foreground_process(client, dist):
    script = client.get("/api/agents/install?os=linux").text  # service is the default
    assert "/etc/systemd/system/$SVC.service" in script
    assert "Restart=always" in script
    assert "systemctl enable --now" in script
    assert "journalctl -u $SVC -f" in script
    # macOS supervision ships in the same script (it branches on uname).
    assert "LaunchAgents" in script and "KeepAlive" in script
    # And a last-resort detach when neither supervisor exists.
    assert "setsid nohup" in script


def test_foreground_mode_is_still_available_for_a_throwaway_demo(client, dist):
    script = client.get("/api/agents/install?os=linux&mode=foreground").text
    assert 'exec "$BIN" --server "$SERVER"' in script
    assert "dies with this session" in script


def test_bad_mode_is_a_structured_400(client, dist):
    r = client.get("/api/agents/install?os=linux&mode=daemonize-please")
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "BAD_MODE"


def test_uninstall_returns_a_removal_script(client, dist):
    r = client.get("/api/agents/install?uninstall=1")
    assert r.status_code == 200
    script = r.text
    assert "systemctl disable --now" in script
    assert "launchctl bootout" in script
    assert "/usr/local/bin/cortexsim-agent" in script
    if shutil.which("bash"):
        p = subprocess.run(["bash", "-n", "/dev/stdin"], input=script,
                           capture_output=True, text=True, timeout=10)
        assert p.returncode == 0, p.stderr


def test_generated_installer_is_valid_bash_in_every_mode(client, dist):
    if not shutil.which("bash"):
        pytest.skip("bash unavailable")
    for url in (
        "/api/agents/install?os=linux",
        "/api/agents/install?os=linux&mode=foreground",
        "/api/agents/install?os=macos&token=cxs_abc123def456",
    ):
        script = client.get(url).text
        p = subprocess.run(["bash", "-n", "/dev/stdin"], input=script,
                           capture_output=True, text=True, timeout=10)
        assert p.returncode == 0, f"{url} failed bash -n:\n{p.stderr}"


def test_macos_is_a_first_class_target(client, dist):
    r = client.get("/api/agents/install?os=macos")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/x-shellscript")
    assert "Darwin) OS_ID=\"darwin\"" in r.text


def test_unsupported_os_is_a_structured_400(client, dist):
    r = client.get("/api/agents/install?os=solaris")
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "BAD_OS"


# ---------------------------------------------------------------------------
# Windows: honest about a beacon that does not exist yet
# ---------------------------------------------------------------------------


def test_windows_installer_refuses_before_enrolling_when_no_beacon_is_served(client, dist):
    script = client.get("/api/agents/install?os=windows&token=cxs_tok").text
    assert "WINDOWS_AGENT_UNAVAILABLE" in script
    # It must bail BEFORE the enroll call, or a target that can never run the
    # beacon leaves a phantom agent row in the console.
    assert script.index("WINDOWS_AGENT_UNAVAILABLE") < script.index("/api/agents/enroll")
    assert "PUSH mode" in script


def test_windows_installer_goes_live_the_day_a_windows_beacon_is_shipped(client, dist):
    (dist / "cortexsim-agent-windows-amd64.exe").write_bytes(b"MZ-fake-windows")
    script = client.get("/api/agents/install?os=windows&token=cxs_tok").text
    assert "WINDOWS_AGENT_UNAVAILABLE" not in script
    assert "sc.exe create CortexSimAgent" in script
    assert "CHECKSUM_MISMATCH" in script
    assert "go install" not in script


# ---------------------------------------------------------------------------
# Install telemetry — a failed install is visible in SimCore, not just on the target
# ---------------------------------------------------------------------------


def test_install_failure_is_recorded_and_readable_from_the_console(client, dist):
    r = client.post("/api/agents/install/telemetry", json={
        "stage": "enroll", "code": "ENROLL_DENIED",
        "detail": "HTTP 403 from /api/agents/enroll",
        "hostname": "cust-jumpbox-01", "os": "linux", "arch": "amd64"})
    assert r.status_code == 200
    assert r.json() == {"status": "recorded", "stage": "enroll", "code": "ENROLL_DENIED"}

    attempts = client.get("/api/agents/install/attempts").json()["attempts"]
    assert attempts[0]["code"] == "ENROLL_DENIED"
    assert attempts[0]["hostname"] == "cust-jumpbox-01"
    assert attempts[0]["reported_at"]


def test_attempts_are_newest_first_and_bounded(client, dist):
    from api.agents import _INSTALL_ATTEMPTS_MAX, _install_attempts

    _install_attempts.clear()
    for i in range(_INSTALL_ATTEMPTS_MAX + 10):
        client.post("/api/agents/install/telemetry",
                    json={"stage": "detect", "code": f"C{i}", "hostname": "h"})
    body = client.get("/api/agents/install/attempts?limit=3").json()
    assert body["total"] == _INSTALL_ATTEMPTS_MAX  # bounded — cannot grow unboundedly
    assert [a["code"] for a in body["attempts"]] == [
        f"C{_INSTALL_ATTEMPTS_MAX + 9}", f"C{_INSTALL_ATTEMPTS_MAX + 8}", f"C{_INSTALL_ATTEMPTS_MAX + 7}"]


def test_telemetry_strips_control_characters_before_an_operator_sees_it(client, dist):
    """The reporter is an unauthenticated endpoint host — its text must not be
    able to carry escape sequences into a console."""
    from api.agents import _install_attempts

    _install_attempts.clear()
    client.post("/api/agents/install/telemetry", json={
        "stage": "verify", "code": "CHECKSUM_MISMATCH",
        "detail": "boom\x1b[2J\x00 tail", "hostname": "h\x07"})
    entry = client.get("/api/agents/install/attempts").json()["attempts"][0]
    assert "\x1b" not in entry["detail"] and "\x00" not in entry["detail"]
    assert entry["hostname"] == "h"
    assert "boom" in entry["detail"] and "tail" in entry["detail"]


def test_telemetry_payload_is_length_capped(client, dist):
    r = client.post("/api/agents/install/telemetry",
                    json={"stage": "detect", "code": "X", "detail": "z" * 5000})
    assert r.status_code == 422  # pydantic max_length — no unbounded operator-visible blob
