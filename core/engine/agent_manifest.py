"""Posture profiles for the CortexSim Kubernetes container agent.

Each profile is a :class:`~engine.k8s_manifest.ClusterPosture`; the posture model
and ``findings_for`` remain the only source of truth for what a profile plants and
which consent it needs. Nothing here lists a finding slug or a consent key.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

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
            capabilities_drop=[],
        )
        if lab
        # No drop list: the runtime's default capability set, as an ordinary pod has.
        # apt-get (CHOWN/DAC_OVERRIDE/FOWNER) and useradd/runuser (SETUID/SETGID)
        # need it; dropping ALL would fail prepare and every non-direct identity.
        else SecurityContextSpec(capabilities_drop=[])
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
  # Background + wait so a TERM trap fires now, not after a long apt/apk run.
  if command -v timeout >/dev/null 2>&1; then timeout 120 "$@" & else "$@" & fi
  wait $!
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
@@ACCOUNT_LOOP@@
if [ "$PREP_BAD" = 0 ]; then echo PREPARE_OK >> "$STATUS"; fi
"""

# alpine ships some of these names as a GROUP with no user (www-data, gid 82), and
# useradd refuses to create a user whose name is an existing group unless told to
# join it; so join the group when it exists, otherwise create both.
_ACCOUNT_LOOP = """has_group() {
  if command -v getent >/dev/null 2>&1; then
    getent group "$1" >/dev/null 2>&1
  else
    grep -q "^$1:" /etc/group 2>/dev/null
  fi
}
for u in @@ACCOUNTS@@; do
  id "$u" >/dev/null 2>&1 && continue
  if command -v useradd >/dev/null 2>&1; then
    if has_group "$u"; then
      useradd -m -s /bin/sh -g "$u" "$u" >/dev/null 2>&1 || pf "could not create account $u"
    else
      useradd -m -s /bin/sh -U "$u" >/dev/null 2>&1 || pf "could not create account $u"
    fi
  elif command -v adduser >/dev/null 2>&1; then
    if has_group "$u"; then
      adduser -D -s /bin/sh -G "$u" "$u" >/dev/null 2>&1 || pf "could not create account $u"
    else
      adduser -D -s /bin/sh "$u" >/dev/null 2>&1 || pf "could not create account $u"
    fi
  else
    pf "could not create account $u"
  fi
done
"""


def account_creation_fragment(accounts: list[str]) -> str:
    """The prepare stage's account loop; expects ``pf`` to be defined."""
    for name in accounts:
        if not _SAFE_WORD.match(name):
            raise ValueError(f"service account {name!r} is not shell-safe")
    return _render(_ACCOUNT_LOOP, ACCOUNTS=" ".join(accounts))


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
# Installed before prepare so a TERM during a long package run ends the pod promptly.
# Before the beacon exists the handler just exits 143; afterwards it forwards TERM
# and records that termination was requested, so a clean exit 0 caused by that TERM
# is a shutdown, not a lifetime expiry.
pid=""
TERMED=0
trap 'TERMED=1; if [ -n "$pid" ]; then kill -TERM $pid 2>/dev/null; else exit 143; fi' TERM INT
@@PREPARE@@
AGENT="${CORTEXSIM_AGENT_BIN:-/cortexsim/cortexsim-agent}"
"$AGENT" --server "$(cat /secret/server)" --enroll-token-file /secret/token --state-dir /cortexsim/state --expires-at @@EXPIRES@@ --posture-profile @@PROFILE@@ --prepare-status-file /cortexsim/prepare.status --subreaper --long-poll 30 --stream-output &
pid=$!
wait $pid
rc=$?
while kill -0 $pid 2>/dev/null; do
  wait $pid
  rc=$?
done
if [ "$rc" -eq 0 ] && [ "$TERMED" = 0 ]; then
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
        loop = account_creation_fragment(identity_spec.service_accounts())
        block = _render(_PREPARE_PACKAGES, ACCOUNT_LOOP=loop.rstrip("\n"))
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


# ── Object graph ─────────────────────────────────────────────────────────────

AGENT_APP = "cortexsim-agent"
ENROLL_SECRET = "cortexsim-enroll"
BOOT_CONFIGMAP = "cortexsim-agent-boot"
_AGENT_LABEL = "cortexsim.io/agent"

_INIT_RESOURCES = {
    "limits": {"cpu": "200m", "memory": "128Mi"},
    "requests": {"cpu": "50m", "memory": "32Mi"},
}

_KUBECTL_COPY = (
    "mkdir -p /cortexsim/bin && cp \"$(command -v kubectl)\" /cortexsim/bin/kubectl "
    "|| { echo KUBECTL_COPY_FAILED > /dev/termination-log; exit %d; }" % km.EXIT_NEVER_RAN
)

_CARRIER_OBJECT = {
    "namespace": "Namespace",
    "serviceaccount": "ServiceAccount",
    "clusterrole": "ClusterRole",
    "clusterrolebinding": "ClusterRoleBinding",
    "podtemplate": "Deployment",
}


def _require_aware(now: datetime) -> None:
    # A naive datetime is read as host-local time by .timestamp(), which would
    # stamp a shifted expiry on every object.
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware (UTC); got a naive datetime")


#: The ONLY accepted shapes for the two caller-controlled free-text inputs.
#: Allowlists, not denylists: ``authorized_by`` is written into a YAML comment
#: header, and U+0085 / U+2028 / U+2029 are YAML line breaks that a control-char
#: filter misses, turning the rest of the value into live YAML documents.
AUTHORIZED_BY_RE = re.compile(r"^[A-Za-z0-9 ._@+'()\-]{1,120}$")
IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@\-]{0,254}$")


def validate_authorized_by(value: str) -> str:
    """Return the stripped value, or raise ValueError."""
    v = value.strip() if isinstance(value, str) else ""
    if not AUTHORIZED_BY_RE.fullmatch(v):
        raise ValueError(
            "authorized_by must be 1-120 characters of letters, digits, space "
            "and . _ @ + ' ( ) -"
        )
    return v


def validate_image(value: str) -> str:
    if not isinstance(value, str) or not IMAGE_RE.fullmatch(value):
        raise ValueError(
            "image must be 1-255 characters of letters, digits and . _ / : @ -, "
            "starting with a letter or digit"
        )
    return value


def build_agent_objects(
    *,
    profile: str,
    image: str,
    arch: str,
    prepare: str,
    ttl_seconds: int,
    beacon_sha256: str,
    now: datetime,
    authorized_by: Optional[str],
) -> list[dict[str, Any]]:
    """The agent manifest as a list of dicts; serialisation adds nothing."""
    _require_aware(now)
    validate_image(image)
    if authorized_by is not None:
        authorized_by = validate_authorized_by(authorized_by)
    posture = agent_posture(profile, image=image, ttl_seconds=ttl_seconds)
    findings = km.findings_for(posture)
    ns = AGENT_NAMESPACE
    expires = km._iso(now.timestamp() + ttl_seconds)

    # Validates arch / prepare / expiry before anything is built.
    fetch_sh = fetch_script(arch=arch, beacon_sha256=beacon_sha256)
    start_sh = start_script(arch=arch, prepare=prepare, expires_at=expires, profile=profile)

    base = {**km._base_labels(AGENT_SCENARIO_ID, "agent", "pull"), _AGENT_LABEL: "true"}
    common = {
        "cortexsim.io/expires-at": expires,
        "cortexsim.io/agent-profile": profile,
        **({"cortexsim.io/authorized-by": authorized_by} if authorized_by else {}),
    }

    def carried(carrier: str) -> list[km.PostureFinding]:
        return km._for_carrier(findings, carrier)

    objects: list[dict[str, Any]] = []

    ns_labels = {**base, **km._finding_labels(carried("namespace"))}
    if posture.requires_privileged_psa():
        ns_labels.update(km._pod_security_labels())
    objects.append({
        "apiVersion": "v1", "kind": "Namespace",
        "metadata": {
            "name": ns, "labels": ns_labels,
            "annotations": {**common, **km._finding_annotations(list(findings))},
        },
    })

    sa_findings = carried("serviceaccount")
    objects.append({
        "apiVersion": "v1", "kind": "ServiceAccount",
        "metadata": {
            "name": AGENT_APP, "namespace": ns,
            "labels": {**base, **km._finding_labels(sa_findings)},
            "annotations": km._finding_annotations(sa_findings),
        },
        "automountServiceAccountToken": posture.service_account.automount_token,
    })

    cluster_scoped: list[tuple[str, str]] = []
    if posture.service_account.cluster_role_rules:
        cr_findings, crb_findings = carried("clusterrole"), carried("clusterrolebinding")
        objects.append({
            "apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole",
            "metadata": {
                "name": AGENT_APP,
                "labels": {**base, **km._finding_labels(cr_findings)},
                "annotations": km._finding_annotations(cr_findings),
            },
            "rules": [km._rbac(r) for r in posture.service_account.cluster_role_rules],
        })
        objects.append({
            "apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRoleBinding",
            "metadata": {
                "name": AGENT_APP,
                "labels": {**base, **km._finding_labels(crb_findings)},
                "annotations": km._finding_annotations(crb_findings),
            },
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "ClusterRole", "name": AGENT_APP,
            },
            "subjects": [{"kind": "ServiceAccount", "name": AGENT_APP, "namespace": ns}],
        })
        cluster_scoped = [("clusterroles", AGENT_APP), ("clusterrolebindings", AGENT_APP)]

    objects.append({
        "apiVersion": "v1", "kind": "ConfigMap",
        "metadata": {"name": BOOT_CONFIGMAP, "namespace": ns, "labels": dict(base)},
        "data": {"fetch.sh": fetch_sh, "start.sh": start_sh},
    })

    pod_findings = carried("podtemplate")
    pod_annotations = {**common, **km._finding_annotations(pod_findings)}
    objects.append({
        "apiVersion": "apps/v1", "kind": "Deployment",
        "metadata": {
            "name": AGENT_APP, "namespace": ns,
            "labels": {**base, "app.kubernetes.io/name": AGENT_APP},
            "annotations": dict(pod_annotations),
        },
        "spec": {
            "replicas": posture.replicas,
            "selector": {"matchLabels": {"app.kubernetes.io/name": AGENT_APP, _AGENT_LABEL: "true"}},
            "template": {
                "metadata": {
                    "labels": {
                        **base, "app.kubernetes.io/name": AGENT_APP,
                        **km._finding_labels(pod_findings),
                    },
                    "annotations": pod_annotations,
                },
                "spec": _agent_pod_spec(posture, image=image),
            },
        },
    })

    # The reaper deletes the agent namespace itself (the push path relies on the
    # same call), plus exactly the cluster-scoped objects emitted above. Here it
    # runs as a Deployment, not the push path's Job: see _reaper_as_deployment.
    objects.extend(_reaper_as_deployment(
        km._reaper(
            scenario_id=AGENT_SCENARIO_ID, sid_lower="agent", ns=ns, base=base,
            ttl=ttl_seconds, image=km.DEFAULT_REAPER_IMAGE, cluster_scoped=cluster_scoped,
        ),
        expires_epoch=int(now.timestamp()) + ttl_seconds,
    ))

    # Every object expires and names who authorised it, reaper objects included.
    for obj in objects:
        meta = obj["metadata"]
        meta["annotations"] = {**common, **meta.get("annotations", {})}
    return objects


# The push path's reaper waits a RELATIVE ttl. In a Job that ran once that was the
# expiry; in a Deployment a restarted reaper would start the clock again. This
# replaces exactly that wait with one to the absolute expiry, using only what the
# pinned busybox image is known to carry (sh, date +%s, sleep).
_REAPER_RELATIVE_WAIT = (
    'echo "[cortexsim-reaper] sleeping ${TTL}s before reaping ${NS}"\n'
    'sleep "${TTL}"\n'
)
_REAPER_ABSOLUTE_WAIT = """\
echo "[cortexsim-reaper] waiting until epoch ${EXPIRES_AT_EPOCH} before reaping ${NS}"
while :; do
  NOW_S=$(date +%s 2>/dev/null)
  case "$NOW_S" in
    ''|*[!0-9]*)
      # Never worse than the Job it replaces: fall back to the relative wait.
      echo "[cortexsim-reaper] REAPER_CLOCK_UNAVAILABLE: sleeping ${TTL}s instead"
      sleep "${TTL}"
      break ;;
  esac
  LEFT=$((EXPIRES_AT_EPOCH - NOW_S))
  if [ "$LEFT" -le 0 ]; then break; fi
  # Short naps, so a clock step or a long suspend cannot overshoot by hours.
  if [ "$LEFT" -gt 300 ]; then sleep 300; else sleep "$LEFT"; fi
done
"""


def _reaper_as_deployment(reaper: list[dict[str, Any]], *, expires_epoch: int) -> list[dict[str, Any]]:
    """The push path's reaper objects, with its Job turned into a Deployment.

    A Job's ``spec.template`` is immutable and the reaper's template changes with
    every generation (the expiry), so re-applying the agent manifest — the path
    after a SimCore upgrade or a profile change — was rejected (kind run, D1).
    A Deployment's template is mutable. Name, ServiceAccount, pinned RBAC, image
    and script are kept; only the wait becomes absolute, because a Deployment
    restarts its pod where a ``backoffLimit: 0`` Job did not.
    """
    jobs = [o for o in reaper if o["kind"] == "Job"]
    if len(jobs) != 1:
        raise RuntimeError(f"expected exactly one reaper Job from km._reaper, got {len(jobs)}")
    job = jobs[0]
    name = job["metadata"]["name"]
    pod_spec = {**job["spec"]["template"]["spec"], "restartPolicy": "Always"}
    (container,) = pod_spec["containers"]
    (script,) = container["args"]
    if script.count(_REAPER_RELATIVE_WAIT) != 1:
        raise RuntimeError("the reaper script no longer has the relative wait this replaces")
    container = {
        **container,
        "args": [script.replace(_REAPER_RELATIVE_WAIT, _REAPER_ABSOLUTE_WAIT)],
        "env": [*container["env"], {"name": "EXPIRES_AT_EPOCH", "value": str(expires_epoch)}],
    }
    pod_spec["containers"] = [container]
    selector = {"app.kubernetes.io/name": name, _AGENT_LABEL: "true"}
    deployment = {
        "apiVersion": "apps/v1", "kind": "Deployment",
        "metadata": dict(job["metadata"]),
        "spec": {
            "replicas": 1,
            # Never two reapers at once, never a gap filled by a stale one.
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": selector},
            "template": {
                "metadata": {"labels": {**job["spec"]["template"]["metadata"]["labels"], **selector}},
                "spec": pod_spec,
            },
        },
    }
    return [deployment if o is job else o for o in reaper]


#: Ready means enrolled: the beacon writes its id only after SimCore assigned it,
#: so a pod still installing packages or retrying enrollment is not Ready. No
#: liveness or startup probe: either would kill a slow package install.
_READINESS_PROBE = {
    "exec": {"command": ["/bin/sh", "-c", "test -s /cortexsim/state/agent-id"]},
    "periodSeconds": 5,
    "timeoutSeconds": 2,
    "failureThreshold": 3,
}


def _agent_pod_spec(posture: ClusterPosture, *, image: str) -> dict[str, Any]:
    volumes: list[dict[str, Any]] = [
        {"name": "cortexsim", "emptyDir": {}},
        {"name": "cortexsim-boot", "configMap": {"name": BOOT_CONFIGMAP}},
        {"name": "cortexsim-enroll",
         "secret": {"secretName": ENROLL_SECRET, "optional": False}},
    ]
    state = {"name": "cortexsim", "mountPath": "/cortexsim"}
    boot = {"name": "cortexsim-boot", "mountPath": "/cortexsim-boot", "readOnly": True}
    secret = {"name": "cortexsim-enroll", "mountPath": "/secret", "readOnly": True}
    hp_volumes, hp_mounts = km._host_path_volumes(posture)
    volumes.extend(hp_volumes)

    termination = {
        "terminationMessagePolicy": "File",
        "terminationMessagePath": "/dev/termination-log",
    }
    downward = lambda path: {"valueFrom": {"fieldRef": {"fieldPath": path}}}  # noqa: E731

    spec: dict[str, Any] = {
        "serviceAccountName": AGENT_APP,
        "automountServiceAccountToken": posture.service_account.automount_token,
        "restartPolicy": "Always",
        "terminationGracePeriodSeconds": 30,
        "enableServiceLinks": False,
        "volumes": volumes,
    }
    spec.update(km._host_namespace_flags(posture))
    affinity = km._node_access_affinity(posture)
    if affinity:
        spec["affinity"] = affinity

    spec["initContainers"] = [
        {
            "name": "fetch",
            "image": km.DEFAULT_FETCH_IMAGE,
            "command": ["/bin/sh", "/cortexsim-boot/fetch.sh"],
            # fetch.sh reads /secret/server.
            "volumeMounts": [dict(state), dict(boot), dict(secret)],
            **termination,
            "resources": _INIT_RESOURCES,
        },
        {
            # How kubectl reaches an arbitrary image with no egress.
            "name": "kubectl",
            "image": km.DEFAULT_REAPER_IMAGE,
            "command": ["/bin/sh", "-c", _KUBECTL_COPY],
            "volumeMounts": [dict(state)],
            **termination,
            "resources": _INIT_RESOURCES,
        },
    ]
    container: dict[str, Any] = {
        "name": "agent",
        "image": image,
        "command": ["/bin/sh", "/cortexsim-boot/start.sh"],
        "env": [
            {"name": "POD_NAME", **downward("metadata.name")},
            {"name": "POD_NAMESPACE", **downward("metadata.namespace")},
            {"name": "NODE_NAME", **downward("spec.nodeName")},
        ],
        "securityContext": km._container_security_context(posture.security_context),
        "volumeMounts": [dict(state), dict(boot), dict(secret), *hp_mounts],
        "readinessProbe": _READINESS_PROBE,
        **termination,
    }
    resources = km._resources_block(posture)
    if resources:
        container["resources"] = resources
    spec["containers"] = [container]
    return spec


def _agent_header(
    *,
    profile: str,
    posture: ClusterPosture,
    objects: list[dict[str, Any]],
    expires: str,
    authorized_by: Optional[str],
    consent: dict[str, bool],
) -> str:
    findings = km.findings_for(posture)
    kinds: dict[str, str] = {}
    for o in objects:  # first of each kind is the agent's; the reaper's come last
        kinds.setdefault(o["kind"], o["metadata"]["name"])
    granted = sorted(k for k, v in consent.items() if v)
    rule = km._HEADER_RULE
    out = [rule, "# CortexSim Kubernetes Container Agent\n", rule]
    out.append(f"# Profile    : {profile}   Risk tier: {posture.risk_tier()}\n")
    out.append(f"# Namespace  : {AGENT_NAMESPACE}   Expires: {expires}\n")
    out.append(f"# Consent    : {', '.join(granted) if granted else '(none required)'}\n")
    out.append(f"# Authorized : {authorized_by or '(not recorded)'}\n")
    out.append("#\n")
    out.append(
        "# The pod enrolls with, and obeys, whichever SimCore the cortexsim-enroll\n"
        "# Secret names. This file carries no token and no Secret object.\n"
    )
    out.append("# After a SimCore upgrade that changes the beacon, restarted pods exit 78 until this manifest is re-applied.\n")
    out.append("#\n")
    if findings:
        out.append(f"# PLANTED POSTURE FINDINGS ({len(findings)})\n")
        for f in findings:
            obj = _CARRIER_OBJECT.get(f.carrier, f.carrier)
            out.append(f"#   {f.slug:<42} -> {obj}/{kinds.get(obj, '?')}\n")
    else:
        out.append("# PLANTED POSTURE FINDINGS: none beyond Kubernetes defaults\n")
    out.append("#\n")
    out.append(
        f"# A reaper Deployment deletes namespace {AGENT_NAMESPACE} and the cluster-scoped objects\n"
        "# above at expiry. It is a safety net: delete the manifest yourself when done.\n"
    )
    out.append(rule)
    return "".join(out)


def generate_agent_manifest(
    *,
    profile: str,
    image: str,
    arch: str,
    prepare: str,
    ttl_seconds: int,
    beacon_sha256: str,
    now: datetime,
    authorized_by: Optional[str],
    consent: dict[str, bool],
    allow_privileged_k8s: bool,
) -> str:
    """Refuse without consent, then render the agent manifest as multi-doc YAML.

    ``allow_privileged_k8s`` is required, not defaulted: the deployment's kill
    switch must be an explicit decision by every caller.
    """
    _require_aware(now)
    validate_image(image)
    if authorized_by is not None:
        authorized_by = validate_authorized_by(authorized_by)
    posture = agent_posture(profile, image=image, ttl_seconds=ttl_seconds)
    km.check_cluster_consent(
        AGENT_SCENARIO_ID, posture, consent, allow_privileged_k8s=allow_privileged_k8s,
    )
    objects = build_agent_objects(
        profile=profile, image=image, arch=arch, prepare=prepare,
        ttl_seconds=ttl_seconds, beacon_sha256=beacon_sha256, now=now,
        authorized_by=authorized_by,
    )
    header = _agent_header(
        profile=profile, posture=posture, objects=objects,
        expires=km._iso(now.timestamp() + ttl_seconds),
        authorized_by=authorized_by, consent=consent,
    )
    return km.render(objects, header)
