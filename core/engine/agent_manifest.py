"""Posture profiles for the CortexSim Kubernetes container agent.

Each profile is a :class:`~engine.k8s_manifest.ClusterPosture`; the posture model
and ``findings_for`` remain the only source of truth for what a profile plants and
which consent it needs. Nothing here lists a finding slug or a consent key.
"""
from __future__ import annotations

import re

from engine import identity_spec
from engine import k8s_manifest as km
from engine.k8s_manifest import (
    ClusterPosture,
    HostAccessSpec,
    HostPathSpec,
    RbacRule,
    ResourcesSpec,
    SecurityContextSpec,
    ServiceAccountSpec,
)

AGENT_NAMESPACE = "cortexsim-agent"
AGENT_SCENARIO_ID = "AGENT"
PROFILES = ("baseline", "cluster-privilege", "lab-privileged")


class UnknownAgentProfile(ValueError):
    """Raised for a profile name outside :data:`PROFILES`."""


def agent_posture(
    profile: str, *, image: str = "debian:12-slim", ttl_seconds: int = 43200
) -> ClusterPosture:
    if profile not in PROFILES:
        raise UnknownAgentProfile(
            f"unknown agent profile {profile!r}; expected one of {list(PROFILES)}"
        )
    cluster = profile in ("cluster-privilege", "lab-privileged")
    lab = profile == "lab-privileged"

    service_account = ServiceAccountSpec(
        automount_token=True,
        cluster_role_rules=(
            [RbacRule(api_groups=["*"], resources=["*"], verbs=["*"])] if cluster else []
        ),
    )
    security_context = (
        SecurityContextSpec(
            privileged=True,
            allow_privilege_escalation=True,
            capabilities_add=["SYS_ADMIN", "NET_ADMIN"],
        )
        if lab
        else SecurityContextSpec()
    )
    host_access = (
        HostAccessSpec(
            host_pid=True,
            host_paths=[
                HostPathSpec(name="host-root", host_path="/", mount_path="/host", read_only=True)
            ],
        )
        if lab
        else HostAccessSpec()
    )
    return ClusterPosture(
        workload="deployment",
        image=image,
        replicas=1,
        namespace=AGENT_NAMESPACE,
        service_account=service_account,
        security_context=security_context,
        host_access=host_access,
        # Same resources on every profile so findings nest by privilege only.
        resources=ResourcesSpec(),
        ttl_seconds=ttl_seconds,
    )


def profile_findings(profile: str) -> frozenset[str]:
    return frozenset(f.slug for f in km.findings_for(agent_posture(profile)))


# ── Pod scripts ──────────────────────────────────────────────────────────────
# Both scripts are POSIX ``sh`` (they run under busybox on alpine as well as dash)
# and are rendered with ``@@NAME@@`` tokens, like ``core/api/agents.py::_render``,
# so shell ``$`` and ``{}`` need no escaping.

PREPARE_MODES = ("packages", "none")
#: The tools a step may call; imported, never copied, so the two lists cannot drift.
PROBE_TOOLS: tuple[str, ...] = km._KNOWN_BINS

_ARCHES = ("amd64", "arm64")
_SAFE_WORD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _render(template: str, **subs: str) -> str:
    out = template
    for key, value in subs.items():
        out = out.replace(f"@@{key}@@", value)
    return out


def _check_arch(arch: str) -> None:
    if arch not in _ARCHES:
        raise ValueError(f"unknown arch {arch!r}; expected one of {list(_ARCHES)}")


_FETCH_TEMPLATE = """#!/bin/sh
# CortexSim agent pod: fetch the beacon. The digest below was computed by SimCore
# from the beacon it serves; it is carried in, never fetched from the server being
# trusted. Any failure exits @@EXIT@@ with a named cause on the termination log.
SECRET="${CORTEXSIM_SECRET_DIR:-/secret}"
TERMLOG="${CORTEXSIM_TERMLOG:-/dev/termination-log}"
fail() {
  printf '%s\n' "$1" > "$TERMLOG" 2>/dev/null
  echo "$1" >&2
  exit @@EXIT@@
}
SERVER=$(cat "$SECRET/server" 2>/dev/null | tr -d ' \r\n')
while [ "${SERVER%/}" != "$SERVER" ]; do SERVER=${SERVER%/}; done
case "$SERVER" in
  http://?*|https://?*) ;;
  *) fail BAD_SERVER_URL ;;
esac
wget -q -T 60 -O /cortexsim/agent.part "$SERVER/api/agents/binary?os=linux&arch=@@ARCH@@" \
  || fail BEACON_FETCH_FAILED
GOT=$(sha256sum /cortexsim/agent.part 2>/dev/null | cut -d' ' -f1)
if [ -z "$GOT" ] || [ "$GOT" != "@@SHA@@" ]; then
  fail BEACON_DIGEST_MISMATCH
fi
chmod 0755 /cortexsim/agent.part
mv /cortexsim/agent.part /cortexsim/cortexsim-agent
"""


def fetch_script(*, arch: str, beacon_sha256: str) -> str:
    """Init-container script: download the beacon and pin it to ``beacon_sha256``."""
    _check_arch(arch)
    if not _SHA256.match(beacon_sha256):
        raise ValueError("beacon_sha256 must be 64 lowercase hex characters")
    return _render(
        _FETCH_TEMPLATE, EXIT=str(km.EXIT_NEVER_RAN), ARCH=arch, SHA=beacon_sha256
    )


_PREPARE_PACKAGES = """STATUS=/cortexsim/prepare.status
: > "$STATUS"
PREP_BAD=0
pf() { echo "PREPARE_FAILED $*" >> "$STATUS"; PREP_BAD=1; }
bound() {
  if command -v timeout >/dev/null 2>&1; then timeout 120 "$@"; else "$@"; fi
}
if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  bound apt-get update -qq >/dev/null 2>&1 || pf "apt-get update failed or timed out"
  bound apt-get install -y -qq --no-install-recommends \
    bash curl wget python3 jq openssl tar ca-certificates util-linux cron >/dev/null 2>&1 \
    || pf "apt-get install failed or timed out"
elif command -v apk >/dev/null 2>&1; then
  bound apk add --no-cache \
    bash curl wget python3 jq openssl tar ca-certificates util-linux runuser shadow >/dev/null 2>&1 \
    || pf "apk add failed or timed out"
else
  pf "no supported package manager (apt-get or apk)"
fi
for u in @@ACCOUNTS@@; do
  id "$u" >/dev/null 2>&1 && continue
  if command -v useradd >/dev/null 2>&1; then
    useradd -m -s /bin/sh "$u" >/dev/null 2>&1 || pf "could not create account $u"
  elif command -v adduser >/dev/null 2>&1; then
    adduser -D -s /bin/sh "$u" >/dev/null 2>&1 || pf "could not create account $u"
  else
    pf "could not create account $u"
  fi
done
if [ "$PREP_BAD" = 0 ]; then echo PREPARE_OK >> "$STATUS"; fi
"""

_PREPARE_NONE = """STATUS=/cortexsim/prepare.status
echo PREPARE_SKIPPED > "$STATUS"
"""

_START_TEMPLATE = """#!/bin/sh
# CortexSim agent pod: prepare the image, then run the beacon as a supervised child.
TERMLOG="${CORTEXSIM_TERMLOG:-/dev/termination-log}"
case "$(uname -m)" in
  x86_64) HOST_ARCH=amd64 ;;
  aarch64) HOST_ARCH=arm64 ;;
  *) HOST_ARCH=$(uname -m) ;;
esac
if [ "$HOST_ARCH" != "@@ARCH@@" ]; then
  printf '%s\n' ARCH_MISMATCH > "$TERMLOG" 2>/dev/null
  echo "ARCH_MISMATCH: node is $HOST_ARCH, beacon is @@ARCH@@" >&2
  exit @@EXIT@@
fi
export PATH=/cortexsim/bin:$PATH
@@PREPARE@@
/cortexsim/cortexsim-agent --server "$(cat /secret/server)" --enroll-token-file /secret/token --state-dir /cortexsim/state --expires-at @@EXPIRES@@ --posture-profile @@PROFILE@@ --prepare-status-file /cortexsim/prepare.status --subreaper --long-poll 30 --stream-output &
pid=$!
trap 'kill -TERM $pid 2>/dev/null' TERM INT
wait $pid
rc=$?
while kill -0 $pid 2>/dev/null; do
  wait $pid
  rc=$?
done
if [ "$rc" -eq 0 ]; then
  echo LIFETIME_EXPIRED
  trap 'exit 0' TERM INT
  while :; do
    sleep 3600 &
    wait $!
  done
fi
exit $rc
"""


def start_script(*, arch: str, prepare: str, expires_at: str, profile: str) -> str:
    """Main-container script: arch guard, optional prepare, supervised beacon."""
    _check_arch(arch)
    if prepare not in PREPARE_MODES:
        raise ValueError(f"unknown prepare mode {prepare!r}; expected one of {list(PREPARE_MODES)}")
    if profile not in PROFILES:
        raise UnknownAgentProfile(
            f"unknown agent profile {profile!r}; expected one of {list(PROFILES)}"
        )
    if not _SAFE_WORD.match(expires_at):
        raise ValueError(f"expires_at {expires_at!r} is not shell-safe")
    if prepare == "packages":
        accounts = identity_spec.service_accounts()
        for name in accounts:
            if not _SAFE_WORD.match(name):
                raise ValueError(f"service account {name!r} is not shell-safe")
        block = _render(_PREPARE_PACKAGES, ACCOUNTS=" ".join(accounts))
    else:
        block = _PREPARE_NONE
    return _render(
        _START_TEMPLATE,
        EXIT=str(km.EXIT_NEVER_RAN),
        ARCH=arch,
        PREPARE=block.rstrip("\n"),
        EXPIRES=expires_at,
        PROFILE=profile,
    )
