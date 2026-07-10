"""Tier C — isolated-execution asset checks (increment 1, infra).

Phase 3 of the e2e execution methodology
(docs/design/e2e-execution-methodology.md): the container image + auditd +
sinkhole infrastructure that the Tier-C reference scenarios (increment 2)
will drive.

These tests are deliberately **docker-free** — they assert the *assets* are
present and well-formed so a regression in the Tier-C harness is caught on
every PR without paying the cost of building/running the container:

  - all Tier-C files exist under deploy/tier-c/
  - docker-compose.tier-c.yml is valid YAML with an internal (no-egress)
    network and NO host-port exposure on the sinkhole
  - audit.rules captures the key syscalls (execve, setuid, connect) + the
    credential file watches
  - the entrypoint dumps an audit log + observed-signals summary
  - the runner image pre-creates the identity-harness service accounts
  - shell scripts pass `bash -n`
  - the sinkhole HTTP catch-all is valid Python (stdlib only)

Follows the tests/e2e_isolated convention: skip gracefully when a tool is
absent (e.g. shellcheck), hard-assert on asset presence.
"""
from __future__ import annotations

import ast
import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
TIER_C = REPO_ROOT / "deploy" / "tier-c"


# ─── Asset presence ──────────────────────────────────────────────────

EXPECTED_FILES = [
    "Dockerfile",
    "audit.rules",
    "entrypoint.sh",
    "docker-compose.tier-c.yml",
    "run-tier-c.sh",
    "README.md",
    "sinkhole/Dockerfile",
    "sinkhole/dnsmasq.conf",
    "sinkhole/catch_all_http.py",
    "sinkhole/sinkhole-entrypoint.sh",
]


@pytest.mark.parametrize("rel", EXPECTED_FILES)
def test_tier_c_asset_exists(rel: str):
    """Each Tier-C asset must exist and be non-empty."""
    p = TIER_C / rel
    assert p.is_file(), f"missing Tier-C asset: deploy/tier-c/{rel}"
    assert p.stat().st_size > 0, f"empty Tier-C asset: deploy/tier-c/{rel}"


@pytest.mark.parametrize("rel", ["entrypoint.sh", "run-tier-c.sh",
                                 "sinkhole/sinkhole-entrypoint.sh"])
def test_tier_c_shell_has_shebang(rel: str):
    """Tier-C shell scripts must start with a bash shebang."""
    first = (TIER_C / rel).read_text().splitlines()[0]
    assert first.startswith("#!"), f"{rel} missing shebang"
    assert "bash" in first, f"{rel} shebang doesn't reference bash: {first!r}"


# ─── Shell parse (bash -n) ───────────────────────────────────────────

@pytest.mark.parametrize("rel", ["entrypoint.sh", "run-tier-c.sh",
                                 "sinkhole/sinkhole-entrypoint.sh"])
def test_shell_parses(rel: str):
    """Every Tier-C shell script must parse under ``bash -n``."""
    if shutil.which("bash") is None:
        pytest.skip("bash not available")
    result = subprocess.run(
        ["bash", "-n", str(TIER_C / rel)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"bash -n failed for deploy/tier-c/{rel}:\n{result.stderr.strip()}"
    )


@pytest.mark.skipif(shutil.which("shellcheck") is None,
                    reason="shellcheck not installed")
@pytest.mark.parametrize("rel", ["entrypoint.sh", "run-tier-c.sh",
                                 "sinkhole/sinkhole-entrypoint.sh"])
def test_shell_shellcheck(rel: str):
    """Tier-C shell scripts should pass shellcheck severity=warning."""
    result = subprocess.run(
        ["shellcheck", "--severity=warning", "--shell=bash",
         "--format=tty", str(TIER_C / rel)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"shellcheck failed for deploy/tier-c/{rel}:\n"
        f"{result.stdout.strip() or result.stderr.strip()}"
    )


# ─── Compose file ────────────────────────────────────────────────────

def _load_compose() -> dict[str, Any]:
    with open(TIER_C / "docker-compose.tier-c.yml") as f:
        return yaml.safe_load(f)


def test_compose_is_valid_yaml():
    """The compose file must parse as YAML and define both services."""
    doc = _load_compose()
    assert isinstance(doc, dict), "compose file is not a YAML mapping"
    services = doc.get("services", {})
    assert "runner" in services, "compose missing 'runner' service"
    assert "sinkhole" in services, "compose missing 'sinkhole' service"


def test_compose_network_is_internal():
    """The Tier-C network must be `internal: true` — the no-egress guarantee.

    `internal: true` tells Docker to create no gateway to the host/internet,
    which is the whole point of the isolated tier. If this regresses, a
    C2/exfil step could reach a real service.
    """
    doc = _load_compose()
    networks = doc.get("networks", {})
    assert networks, "compose declares no networks"
    isolated = [n for n, cfg in networks.items()
                if isinstance(cfg, dict) and cfg.get("internal") is True]
    assert isolated, (
        "no network with `internal: true` — the no-egress isolation "
        "guarantee is missing"
    )


def test_compose_services_use_only_isolated_network():
    """Both services must attach ONLY to the internal network.

    Attaching to the default bridge as well would give the container a route
    to the outside world, defeating isolation.
    """
    doc = _load_compose()
    networks = doc.get("networks", {})
    internal_nets = {n for n, cfg in networks.items()
                     if isinstance(cfg, dict) and cfg.get("internal") is True}
    for svc_name in ("runner", "sinkhole"):
        svc = doc["services"][svc_name]
        svc_nets = svc.get("networks")
        assert svc_nets, f"{svc_name} attaches to no explicit network"
        # networks may be a list or a mapping
        attached = set(svc_nets.keys()) if isinstance(svc_nets, dict) else set(svc_nets)
        assert attached <= internal_nets, (
            f"{svc_name} attaches to a non-internal network: "
            f"{attached - internal_nets}"
        )


def test_sinkhole_has_no_host_port_exposure():
    """The sinkhole must NOT publish any ports to the host.

    A published port would let traffic in/out via the host, breaking the
    closed-loop isolation. The sinkhole is reachable only from inside the
    internal network.
    """
    doc = _load_compose()
    sinkhole = doc["services"]["sinkhole"]
    assert not sinkhole.get("ports"), (
        "sinkhole publishes host ports — that breaks isolation; "
        f"found: {sinkhole.get('ports')}"
    )


def test_runner_has_no_host_port_exposure():
    """The runner must not publish ports to the host either."""
    doc = _load_compose()
    runner = doc["services"]["runner"]
    assert not runner.get("ports"), (
        f"runner publishes host ports: {runner.get('ports')}"
    )


def test_runner_grants_audit_caps():
    """The runner must add the audit capabilities auditd needs."""
    doc = _load_compose()
    runner = doc["services"]["runner"]
    cap_add = set(runner.get("cap_add", []))
    assert "AUDIT_CONTROL" in cap_add, "runner missing CAP_AUDIT_CONTROL"
    assert "AUDIT_WRITE" in cap_add, "runner missing CAP_AUDIT_WRITE"
    # And it must be able to switch identity via the harness.
    assert "SETUID" in cap_add, "runner missing CAP_SETUID (identity harness)"
    assert "SETGID" in cap_add, "runner missing CAP_SETGID (identity harness)"


# ─── Audit ruleset ───────────────────────────────────────────────────

def test_audit_rules_capture_key_syscalls():
    """audit.rules must capture execve, a setuid-family syscall, and connect.

    These three are the spine of "the right binary fired under the right
    identity, and tried to reach the network." Losing any one silently
    blinds Tier-C to a class of regression.
    """
    text = (TIER_C / "audit.rules").read_text()
    assert "-S execve" in text, "audit.rules does not capture execve"
    # setuid family — at least the bare setuid + one of the res/re variants.
    assert "-S setuid" in text, "audit.rules does not capture setuid"
    assert ("-S setresuid" in text or "-S setreuid" in text), (
        "audit.rules does not capture the setresuid/setreuid identity "
        "transition"
    )
    assert "-S connect" in text, "audit.rules does not capture connect"


def test_audit_rules_watch_credential_files():
    """audit.rules must watch the canonical credential artifacts."""
    text = (TIER_C / "audit.rules").read_text()
    for path in ("/etc/shadow", "/root/.aws/credentials"):
        assert path in text, f"audit.rules does not watch {path}"


def test_audit_rules_define_stable_keys():
    """The audit keys are a contract the entrypoint + tests grep for."""
    text = (TIER_C / "audit.rules").read_text()
    for key in ("cortexsim_exec", "cortexsim_setid",
                "cortexsim_net", "cortexsim_creds"):
        assert key in text, f"audit.rules missing -k {key}"


# ─── Entrypoint behaviour (static) ───────────────────────────────────

def test_entrypoint_dumps_audit_and_summary():
    """The runner entrypoint must emit both the raw audit log and the
    structured observed-signals JSON summary."""
    text = (TIER_C / "entrypoint.sh").read_text()
    assert "observed-signals.json" in text, (
        "entrypoint does not emit observed-signals.json"
    )
    assert "audit.log" in text, "entrypoint does not dump audit.log"
    # Must actually run the mounted bundle.
    assert "bash \"$BUNDLE\"" in text or 'bash "$BUNDLE"' in text, (
        "entrypoint does not execute the mounted push bundle"
    )


# ─── Runner image — identity-harness service accounts ────────────────

# Mirror the harness allowlist in core/engine/push_generator.py:
#   www-data | postgres | mysql | node | python3 | nobody | svc-backup
HARNESS_ACCOUNTS = ["www-data", "postgres", "mysql", "node",
                    "python3", "nobody", "svc-backup"]


@pytest.mark.parametrize("user", HARNESS_ACCOUNTS)
def test_dockerfile_provisions_harness_account(user: str):
    """The runner image must reference every identity-harness service account.

    If the image doesn't create the account a scenario step declares, the
    `runuser -l <user>` fork fails and the run can't prove identity causality.
    """
    text = (TIER_C / "Dockerfile").read_text()
    assert user in text, (
        f"runner Dockerfile never references harness account '{user}' — "
        f"steps with identity: {user} cannot fork under it"
    )


def test_dockerfile_installs_auditd():
    """The runner image must install auditd (the ground-truth source)."""
    text = (TIER_C / "Dockerfile").read_text()
    assert "auditd" in text, "runner Dockerfile does not install auditd"


# ─── Sinkhole ────────────────────────────────────────────────────────

def test_sinkhole_http_is_valid_python():
    """The HTTP catch-all must be syntactically valid Python."""
    src = (TIER_C / "sinkhole" / "catch_all_http.py").read_text()
    ast.parse(src)  # raises SyntaxError on failure


def test_sinkhole_http_is_stdlib_only():
    """The catch-all must not import third-party packages (runs in a bare
    Ubuntu image with python3 and no pip installs)."""
    src = (TIER_C / "sinkhole" / "catch_all_http.py").read_text()
    tree = ast.parse(src)
    stdlib_allow = {
        "json", "os", "ssl", "threading", "datetime",
        "http", "http.server", "__future__", "socket",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                assert alias.name in stdlib_allow or top in stdlib_allow, (
                    f"non-stdlib import in catch_all_http.py: {alias.name}"
                )
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            top = mod.split(".")[0]
            assert mod in stdlib_allow or top in stdlib_allow, (
                f"non-stdlib import in catch_all_http.py: {mod}"
            )


def test_dnsmasq_wildcards_every_name():
    """dnsmasq must answer every name with the sinkhole (no upstream forward).

    `address=/#/...` is the dnsmasq wildcard for "every domain". Combined with
    `no-resolv`, no query escapes the isolated network.
    """
    text = (TIER_C / "sinkhole" / "dnsmasq.conf").read_text()
    assert "address=/#/" in text, (
        "dnsmasq.conf has no wildcard address record — names could leak"
    )
    assert "no-resolv" in text, (
        "dnsmasq.conf does not set no-resolv — queries could forward upstream"
    )
