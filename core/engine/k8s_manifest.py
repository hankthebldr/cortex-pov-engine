"""K8s delivery contract — cluster posture, finding vocabulary, manifest builder.

WHY THIS MODULE EXISTS
----------------------
For a cloud-native target THE DEPLOYMENT IS THE AGENT. There is no beacon to
enroll and no binary to drop: ``kubectl apply`` is the delivery, the kubelet
pulls the image, the entrypoint runs the payload.

That makes an intentionally over-permissioned workload **three artifacts at
once**:

1. a POSTURE FINDING — KSPM/CSPM should flag the wildcard RBAC and the
   privileged securityContext;
2. the EXECUTION VEHICLE the runtime TTP needs to be non-vacuous;
3. a real CAUSALITY ANCHOR — one named service process owning one payload
   shell owning every step, instead of N unrelated per-step process trees.

The generator therefore builds a **list of dicts** (the object graph) and only
then serialises. Everything that inspects a manifest — the finding scan, the
consent gate, the drift guard — reads the graph, never the scenario's
declaration. A gate that reads what an author *declared* is a gate on a
comment; a gate that reads ``securityContext.privileged is True`` in the object
about to be written is a gate on reality. This repo has shipped three
declaration-vs-emission false-greens already.

DELIVERY CONTRACT
-----------------
``delivery="embedded"`` (the DEFAULT) keeps the cardinal push-bundle invariant:
the payload travels inside the manifest as a ConfigMap and the pod needs
nothing from SimCore at runtime.

``delivery="served"`` is the ONE documented exception — the pod fetches its
payload (and any staged tool artifacts) from SimCore, so it requires
cluster→SimCore reachability. It is opt-in, it is stated in the manifest
header, in labels, in annotations, and it fails LOUDLY (exit 78,
``Init:CrashLoopBackOff``) rather than running a degraded no-op. A pod that
exits 0 having done nothing is indistinguishable in XSIAM from a pod that did
everything and was missed — that false negative reads as "your detection stack
failed" and is the worst output this engine can produce.

``generate_bash`` and ``generate_powershell`` are untouched by all of this and
keep the strict invariant absolutely.

See ``docs/reference/k8s-delivery.md``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import posixpath
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

#: Every label and annotation this module emits carries this prefix. One
#: vocabulary, one grep. It is a valid DNS-subdomain label prefix and it is the
#: join key POS assertions bind to (``cortexsim.io/posture-finding``), mirroring
#: the ``CortexSimCSPMFinding`` tag convention in infra/modules/aws/cspm.
K8S_PREFIX = "cortexsim.io"

#: EX_CONFIG. The CortexSim "this never ran" exit code, deliberately distinct
#: from anything a TTP step can produce. An initContainer or entrypoint exiting
#: 78 puts the pod in Init:CrashLoopBackOff / CrashLoopBackOff — loudly visible
#: in `kubectl get pods` and in the customer's own cluster monitoring.
EXIT_NEVER_RAN = 78


# ===========================================================================
# 1 · Tiers and consent keys
# ===========================================================================

#: A finding's tier decides which operator consent key unlocks it. The two are
#: ORTHOGONAL, not nested: "you may create a ServiceAccount with cluster-read in
#: a scratch namespace" is a platform-team yes; "you may mount the node
#: filesystem" is a different conversation with a different person. Collapsing
#: them forces the harder approval onto the easier case and trains an operator
#: to tick every box without reading.
TIER_CLUSTER = "cluster_privilege"
TIER_NODE = "node_access"

TIER_CONSENT_KEY: dict[str, str] = {
    TIER_CLUSTER: "cluster_privilege_authorized",
    TIER_NODE: "node_access_authorized",
}

#: Findings with tier ``None`` are BASELINE: real posture findings a KSPM engine
#: should surface, but not privilege this engine has to ask permission for.
#: Running as root inside a container and automounting the default SA token are
#: Kubernetes defaults — gating on them would fire the consent prompt on every
#: download, and after the fourth download the flags are ceremony.


# ===========================================================================
# 2 · The finding vocabulary — DERIVED, never hand-listed
# ===========================================================================

@dataclass(frozen=True)
class PostureFinding:
    """One intentionally-planted misconfiguration.

    ``emitted_when`` is the ONLY source of truth for whether a posture plants
    this finding. The builder may not write a slug literal — it calls
    :func:`findings_for`. That is the anti-drift mechanism the IaC side learned
    the hard way when hand-copied finding lists drifted from what Terraform
    actually tagged.
    """

    slug: str
    title: str
    #: CIS Kubernetes Benchmark control, or "" when none applies.
    cis_control: str
    #: Which emitted object carries the label: namespace | serviceaccount |
    #: clusterrole | clusterrolebinding | podtemplate
    carrier: str
    #: None = baseline/ungated. Otherwise TIER_CLUSTER or TIER_NODE.
    tier: Optional[str]
    emitted_when: Callable[["ClusterPosture"], bool]


def _rules(p: "ClusterPosture") -> list["RbacRule"]:
    return list(p.service_account.cluster_role_rules)


def _rule_grants(rule: "RbacRule", resource: str, verbs: set[str]) -> bool:
    res_hit = resource in rule.resources or "*" in rule.resources
    verb_hit = bool(verbs & set(rule.verbs)) or "*" in rule.verbs
    return res_hit and verb_hit


def _host_paths(p: "ClusterPosture") -> list["HostPathSpec"]:
    return list(p.host_access.host_paths)


#: ORDER IS SEVERITY ORDER. The single-slug label on a multi-finding object
#: carries the first match in this tuple; keep new entries in severity position.
POSTURE_FINDINGS: tuple[PostureFinding, ...] = (
    PostureFinding(
        "pod-privileged", "Container runs privileged",
        "CIS-5.2.2", "podtemplate", TIER_NODE,
        lambda p: p.security_context.privileged,
    ),
    PostureFinding(
        "hostpath-container-socket", "Pod mounts the container runtime socket",
        "CIS-5.2.12", "podtemplate", TIER_NODE,
        lambda p: any(
            h.host_path.rstrip("/").endswith(("docker.sock", "containerd.sock", "crio.sock"))
            for h in _host_paths(p)
        ),
    ),
    PostureFinding(
        "hostpath-root-mount", "Pod mounts the node root filesystem",
        "CIS-5.2.12", "podtemplate", TIER_NODE,
        lambda p: any(h.host_path.rstrip("/") in ("", "/") for h in _host_paths(p)),
    ),
    PostureFinding(
        "pod-host-pid", "Pod shares the host PID namespace",
        "CIS-5.2.3", "podtemplate", TIER_NODE,
        lambda p: p.host_access.host_pid,
    ),
    PostureFinding(
        "pod-host-network", "Pod shares the host network namespace",
        "CIS-5.2.4", "podtemplate", TIER_NODE,
        lambda p: p.host_access.host_network,
    ),
    PostureFinding(
        "pod-host-ipc", "Pod shares the host IPC namespace",
        "CIS-5.2.3", "podtemplate", TIER_NODE,
        lambda p: p.host_access.host_ipc,
    ),
    PostureFinding(
        "clusterrole-wildcard-verbs", "ClusterRole grants wildcard verbs",
        "CIS-5.1.3", "clusterrole", TIER_CLUSTER,
        lambda p: any("*" in r.verbs for r in _rules(p)),
    ),
    PostureFinding(
        "clusterrole-wildcard-resources", "ClusterRole grants wildcard resources",
        "CIS-5.1.3", "clusterrole", TIER_CLUSTER,
        lambda p: any("*" in r.resources for r in _rules(p)),
    ),
    PostureFinding(
        "clusterrole-secrets-read-cluster-wide",
        "ClusterRole can read Secrets in every namespace",
        "CIS-5.1.2", "clusterrole", TIER_CLUSTER,
        lambda p: any(
            _rule_grants(r, "secrets", {"get", "list", "watch"}) for r in _rules(p)
        ),
    ),
    PostureFinding(
        "clusterrole-pod-exec", "ClusterRole can exec into pods cluster-wide",
        "CIS-5.1.3", "clusterrole", TIER_CLUSTER,
        lambda p: any(_rule_grants(r, "pods/exec", {"create"}) for r in _rules(p)),
    ),
    PostureFinding(
        "clusterrole-token-mint", "ClusterRole can mint ServiceAccount tokens",
        "CIS-5.1.3", "clusterrole", TIER_CLUSTER,
        lambda p: any(
            _rule_grants(r, "serviceaccounts/token", {"create"}) for r in _rules(p)
        ),
    ),
    PostureFinding(
        "clusterrole-impersonate", "ClusterRole grants the impersonate verb",
        "CIS-5.1.3", "clusterrole", TIER_CLUSTER,
        # Consistent with its siblings: a wildcard verb on a wildcard resource
        # DOES grant impersonation, so it must fire. A predicate that only
        # matched the literal verb would under-report a `*/*/*` role.
        lambda p: any(
            _rule_grants(r, "users", {"impersonate"})
            or _rule_grants(r, "groups", {"impersonate"})
            or _rule_grants(r, "serviceaccounts", {"impersonate"})
            for r in _rules(p)
        ),
    ),
    PostureFinding(
        "clusterrolebinding-to-workload-sa",
        "ClusterRoleBinding grants a workload ServiceAccount cluster-scoped rights",
        "CIS-5.1.1", "clusterrolebinding", TIER_CLUSTER,
        lambda p: bool(_rules(p)),
    ),
    PostureFinding(
        "pod-added-capabilities", "Container adds Linux capabilities",
        "CIS-5.2.9", "podtemplate", TIER_NODE,
        lambda p: bool(p.security_context.capabilities_add),
    ),
    PostureFinding(
        "pod-host-port", "Container binds a port on the node",
        "CIS-5.2.4", "podtemplate", TIER_NODE,
        lambda p: p.host_access.host_port is not None,
    ),
    PostureFinding(
        "hostpath-mount", "Pod mounts a host path",
        "CIS-5.2.12", "podtemplate", TIER_NODE,
        lambda p: bool(_host_paths(p)),
    ),
    PostureFinding(
        "pod-allow-privilege-escalation", "allowPrivilegeEscalation is true",
        "CIS-5.2.5", "podtemplate", TIER_NODE,
        lambda p: p.security_context.allow_privilege_escalation,
    ),
    PostureFinding(
        "namespace-pod-security-privileged",
        "Namespace opts out of Pod Security Admission (enforce=privileged)",
        "CIS-5.2.1", "namespace", TIER_NODE,
        lambda p: p.requires_privileged_psa(),
    ),
    PostureFinding(
        "serviceaccount-token-automounted",
        "Workload automounts a ServiceAccount token",
        "CIS-5.1.5", "serviceaccount", None,
        lambda p: p.service_account.automount_token,
    ),
    PostureFinding(
        "pod-run-as-root", "Container runs as UID 0",
        "CIS-5.2.6", "podtemplate", None,
        lambda p: p.security_context.run_as_user == 0,
    ),
    PostureFinding(
        "pod-writable-root-filesystem", "Container root filesystem is writable",
        "CIS-5.2.11", "podtemplate", None,
        lambda p: not p.security_context.read_only_root_filesystem,
    ),
    PostureFinding(
        "pod-no-resource-limits", "Container declares no CPU/memory limits",
        "", "podtemplate", None,
        lambda p: p.resources is None,
    ),
)

_BY_SLUG: dict[str, PostureFinding] = {f.slug: f for f in POSTURE_FINDINGS}


def findings_for(posture: "ClusterPosture") -> tuple[PostureFinding, ...]:
    """Every finding this posture plants, in vocabulary (severity) order.

    The ONLY way the builder may learn a slug. A slug literal anywhere in the
    builder is a bug — a drift guard test enforces it.
    """
    return tuple(f for f in POSTURE_FINDINGS if f.emitted_when(posture))


def finding(slug: str) -> Optional[PostureFinding]:
    return _BY_SLUG.get(slug)


def all_slugs() -> tuple[str, ...]:
    return tuple(f.slug for f in POSTURE_FINDINGS)


def vocabulary() -> list[dict[str, Any]]:
    """Serialisable vocabulary — what ``GET /api/k8s/posture-findings`` returns.

    Docs and assertions cite the endpoint. They never copy the list: a
    hand-copied slug that drifts produces an assertion that can never go green
    for a reason invisible to everyone.
    """
    return [
        {
            "slug": f.slug,
            "title": f.title,
            "cis_control": f.cis_control,
            "carrier": f.carrier,
            "tier": f.tier,
            "consent_key": TIER_CONSENT_KEY.get(f.tier or "", None),
        }
        for f in POSTURE_FINDINGS
    ]


# ===========================================================================
# 3 · The posture schema
# ===========================================================================

_HOSTPATH_TYPES = {"", "Directory", "DirectoryOrCreate", "File", "FileOrCreate", "Socket"}
_NS_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")

#: Refused outright, regardless of consent. `default` is on the list because it
#: is where a fat-fingered `-n` lands and where customers already keep
#: workloads — deleting it is not an option, so it can never be safely reaped.
NAMESPACE_DENYLIST = frozenset({
    "default", "kube-system", "kube-public", "kube-node-lease",
    "cert-manager", "istio-system", "cattle-system", "calico-system",
    "tigera-operator", "monitoring", "flux-system", "argocd", "longhorn-system",
})
NAMESPACE_DENY_PREFIXES = ("kube-", "openshift")

#: hostPath roots that may never be mounted writable. Every escape scenario in
#: the corpus produces its telemetry from READING the host and from the mount
#: event itself; a writable node root is how you brick a node you do not own.
HOSTPATH_RW_FORBIDDEN = (
    "/", "/etc", "/boot", "/usr", "/root", "/bin", "/sbin", "/lib",
    "/var/lib/etcd", "/var/lib/kubelet", "/var/lib/rancher", "/var/lib/docker",
)


def _rw_hostpath_forbidden(raw: str) -> Optional[str]:
    """Return the forbidden root ``raw`` reaches when mounted writable, else None.

    Matching MUST normalise and MUST be subtree-aware. The kubelet resolves the
    path it is handed, so a guard that compares the author's *spelling* to a
    literal guards nothing: ``/etc/../`` reaches ``/``, ``//etc`` reaches
    ``/etc``, and ``/etc/cron.d`` — a writable node cron drop-in, the exact
    host-persistence primitive SIM-CDR-003 has to escape a container to reach —
    is not the string ``/etc`` and so matched nothing at all.

    ``/`` is deliberately compared by equality: as a prefix it would swallow
    ``/tmp``, ``/var/tmp`` and ``/var/run/docker.sock``, which the corpus needs.
    """
    norm = posixpath.normpath("/" + raw.lstrip("/"))
    for root in HOSTPATH_RW_FORBIDDEN:
        if root == "/":
            if norm == "/":
                return root
            continue
        if norm == root or norm.startswith(root + "/"):
            return root
    return None


#: Binding one of these takes down the API server, etcd or the kubelet on the node.
HOSTPORT_FORBIDDEN = frozenset({6443, 2379, 2380, 10250, 10257, 10259, 10256})


class RbacRule(BaseModel):
    api_groups: list[str] = Field(default_factory=lambda: [""])
    resources: list[str]
    verbs: list[str]


class ServiceAccountSpec(BaseModel):
    automount_token: bool = False
    #: Empty -> no ClusterRole / ClusterRoleBinding is emitted at all.
    cluster_role_rules: list[RbacRule] = Field(default_factory=list)
    #: Namespace-scoped Role/RoleBinding inside the generated namespace.
    namespace_role_rules: list[RbacRule] = Field(default_factory=list)


class SecurityContextSpec(BaseModel):
    privileged: bool = False
    run_as_user: int = 0
    allow_privilege_escalation: bool = False
    capabilities_add: list[str] = Field(default_factory=list)
    capabilities_drop: list[str] = Field(default_factory=lambda: ["ALL"])
    read_only_root_filesystem: bool = False


class HostPathSpec(BaseModel):
    name: str
    host_path: str
    mount_path: str
    type: str = "Directory"
    read_only: bool = True

    @field_validator("type")
    @classmethod
    def _t(cls, v: str) -> str:
        if v not in _HOSTPATH_TYPES:
            raise ValueError(f"host_paths[].type must be one of {sorted(_HOSTPATH_TYPES)}")
        return v


class HostAccessSpec(BaseModel):
    host_pid: bool = False
    host_network: bool = False
    host_ipc: bool = False
    host_port: Optional[int] = None
    host_paths: list[HostPathSpec] = Field(default_factory=list)


class AppIdentitySpec(BaseModel):
    """What the workload masquerades as, in cluster inventory AND in the process
    tree. ``name`` becomes the workload name, the container name, the
    ``app.kubernetes.io/name`` label AND argv[0]/comm of the anchor process."""

    name: str
    component: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _n(cls, v: str) -> str:
        if not _NS_RE.match(v):
            raise ValueError(
                "app_identity.name must be a DNS-1123 label (lowercase alphanumeric "
                f"and '-'), got {v!r}"
            )
        return v


class ResourcesSpec(BaseModel):
    cpu_limit: str = "500m"
    memory_limit: str = "512Mi"
    cpu_request: str = "100m"
    memory_request: str = "128Mi"


class ClusterPosture(BaseModel):
    """The cluster posture a generated manifest must PLANT.

    Optional and additive: a scenario without this block loads unchanged and
    generates the legacy-equivalent manifest (§5 of the docs).
    """

    workload: Literal["deployment", "job", "daemonset", "auto"] = "auto"
    #: None -> derived from ``libc`` via DEFAULT_IMAGES.
    image: Optional[str] = None
    libc: Literal["glibc", "musl"] = "glibc"
    replicas: int = 1
    #: None -> cortexsim-{scenario_id_lower}. Must start with 'cortexsim-'.
    namespace: Optional[str] = None
    app_identity: Optional[AppIdentitySpec] = None
    service_account: ServiceAccountSpec = Field(default_factory=ServiceAccountSpec)
    security_context: SecurityContextSpec = Field(default_factory=SecurityContextSpec)
    host_access: HostAccessSpec = Field(default_factory=HostAccessSpec)
    #: None -> the pod-no-resource-limits finding is planted deliberately.
    resources: Optional[ResourcesSpec] = Field(default_factory=ResourcesSpec)
    #: Hard self-destruct budget. Drives activeDeadlineSeconds (Job path), the
    #: reaper's sleep, and the cortexsim.io/expires-at annotation.
    ttl_seconds: int = 1800
    #: Extra binaries the payload must find in the image before running a step,
    #: on top of the ones derived from the step commands.
    require_bins: list[str] = Field(default_factory=list)
    #: Tool artifacts SimCore must serve. Non-empty forces delivery="served".
    payloads: list[str] = Field(default_factory=list)

    @field_validator("ttl_seconds")
    @classmethod
    def _ttl(cls, v: int) -> int:
        # A privileged workload with no bound is the failure this block exists
        # to prevent. 12h is generous for a POV and still finite.
        if not (60 <= v <= 43200):
            raise ValueError("cluster_posture.ttl_seconds must be 60..43200")
        return v

    @field_validator("replicas")
    @classmethod
    def _rep(cls, v: int) -> int:
        if not (1 <= v <= 10):
            raise ValueError("cluster_posture.replicas must be 1..10")
        return v

    @field_validator("namespace")
    @classmethod
    def _ns(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not _NS_RE.match(v):
            raise ValueError(f"cluster_posture.namespace must be a DNS-1123 label, got {v!r}")
        if not v.startswith("cortexsim-"):
            # We never adopt or relabel a namespace we did not create.
            raise ValueError("cluster_posture.namespace must start with 'cortexsim-'")
        return v

    @model_validator(mode="after")
    def _normalise_and_check(self) -> "ClusterPosture":
        """Normalise to what the API server will actually accept, then apply the
        structural refusals.

        Refusals are enforced at LOAD time so a content commit can never smuggle
        one in, and they hold regardless of operator consent.
        """
        # Kubernetes rejects privileged=true with allowPrivilegeEscalation=false
        # ("cannot set `allowPrivilegeEscalation` to false and `privileged` to
        # true") — caught by `kubectl apply --dry-run=server` against a real
        # API server. Normalise HERE rather than at emission so the declared
        # model and the emitted object cannot disagree: a privileged container
        # genuinely can escalate, so the pod-allow-privilege-escalation finding
        # SHOULD fire, and silently emitting a value the model does not carry is
        # the declaration-vs-emission drift this design exists to prevent.
        if self.security_context.privileged:
            self.security_context.allow_privilege_escalation = True

        ns = self.namespace
        if ns and (ns in NAMESPACE_DENYLIST or ns.startswith(NAMESPACE_DENY_PREFIXES)):
            raise ValueError(
                f"cluster_posture.namespace {ns!r} is on the hard denylist — "
                "CortexSim never creates or reaps objects in a system namespace"
            )
        for hp in self.host_access.host_paths:
            if hp.read_only:
                continue
            hit = _rw_hostpath_forbidden(hp.host_path)
            if hit is not None:
                raise ValueError(
                    f"host_paths[{hp.name}] requests read_only=false on {hp.host_path!r}, "
                    f"which resolves under {hit!r} and is permanently forbidden. "
                    f"Allowed writable roots are /tmp and /var/tmp. Every escape "
                    f"scenario in the corpus gets its telemetry from READING the host "
                    f"and from the mount event itself."
                )
        hp_port = self.host_access.host_port
        if hp_port is not None:
            if not (1 <= hp_port <= 65535):
                raise ValueError("host_access.host_port must be 1..65535")
            if hp_port in HOSTPORT_FORBIDDEN:
                raise ValueError(
                    f"host_access.host_port={hp_port} collides with a control-plane "
                    f"port ({sorted(HOSTPORT_FORBIDDEN)}); binding it takes down the "
                    f"API server, etcd or the kubelet on that node"
                )
        return self

    # ── derived ────────────────────────────────────────────────────────────

    def requires_privileged_psa(self) -> bool:
        """True when Pod Security Admission ``baseline`` would reject this pod,
        so the namespace we create must carry enforce=privileged.

        Deliberately narrow: we relax PSA only when we have to, and only on our
        own namespace. Mutating a customer namespace's PSA labels silently
        weakens admission for OTHER people's workloads and outlives teardown."""
        return bool(
            self.security_context.privileged
            or self.security_context.capabilities_add
            or self.host_access.host_pid
            or self.host_access.host_network
            or self.host_access.host_ipc
            or self.host_access.host_paths
            or self.host_access.host_port is not None
        )

    def tiers(self) -> tuple[str, ...]:
        """Gated tiers this posture requires, cluster before node."""
        present = {f.tier for f in findings_for(self) if f.tier}
        return tuple(t for t in (TIER_CLUSTER, TIER_NODE) if t in present)

    def is_gated(self) -> bool:
        return bool(self.tiers())

    def risk_tier(self) -> str:
        t = self.tiers()
        if TIER_NODE in t:
            return TIER_NODE
        if TIER_CLUSTER in t:
            return TIER_CLUSTER
        return "baseline"

    def effective_workload(self) -> Literal["deployment", "job", "daemonset"]:
        """``auto`` resolves to deployment when the workload IS a finding.

        WHY the default is derived rather than authored: a Job is batch —
        restartPolicy Never, reaped by ttlSecondsAfterFinished, gone in minutes.
        A KSPM/CSPM engine scans on a cycle measured in minutes to hours. A
        posture finding reaped before the first scan cycle CANNOT be discovered,
        and a POS assertion bound to it goes red for a reason that has nothing
        to do with the customer's posture engine — manufactured failure.

        So: if the workload IS a finding, it must persist like one. A Deployment
        is scheduled, restarts, and appears in `kubectl get deploy`, in the
        inventory a posture engine walks, and in a runtime sensor's workload
        list. Conversely a posture that plants nothing gated has nothing for a
        posture engine to find, and a long-lived Deployment there is litter in a
        customer's cluster. ``auto`` never picks daemonset — per-node execution
        is always a deliberate authoring decision.
        """
        if self.workload != "auto":
            return self.workload  # type: ignore[return-value]
        return "deployment" if self.is_gated() else "job"

    def resolved_image(self) -> str:
        return self.image or DEFAULT_IMAGES[self.libc]


#: WHY debian:12-slim is the glibc default (each verified by `docker run`):
#:   * glibc 2.36 — manylinux wheels and prebuilt ELF tools actually run;
#:   * a REAL /bin/bash that can be COPIED AND RENAMED, which is the CGO anchor
#:     mechanism (`cp $(command -v bash) /usr/local/bin/payments-api && exec …`
#:     yields /proc/self/comm == payments-api). alpine's shell is a busybox
#:     multi-call binary and dispatches on argv[0]: renaming it yields
#:     "applet not found";
#:   * `sha256sum` and a real util-linux `nsenter`.
#: The prior default, ubuntu:22.04, ships NONE of curl / wget / python3 /
#: kubectl — which is why 14 CDR scenarios could not execute a step in any
#: manifest this engine has ever emitted. `require_bin` (§bootstrap) turns that
#: from a silent no-op into exit 78 naming the missing binary.
DEFAULT_IMAGES: dict[str, str] = {
    "glibc": "debian:12-slim",
    "musl": "alpine:3.20",
}

#: Used for the served-delivery preflight/fetch initContainer. busybox userland,
#: so `wget` and `sha256sum` are both present without a package install.
DEFAULT_FETCH_IMAGE = "alpine:3.20"

#: Pinned kubectl image for the reaper Job.
#:
#: The reaper needs BOTH kubectl and a shell — its script sleeps, then issues an
#: ordered sequence of deletes. Two images were tried and rejected against a live
#: kind cluster, each producing the same catastrophic end state:
#:
#:   bitnami/kubectl:1.34      does not exist -> ImagePullBackOff
#:   registry.k8s.io/kubectl   distroless, no /bin/sh -> "exec: /bin/sh: no such
#:                             file or directory" -> BackoffLimitExceeded
#:
#: In both cases the Job died, `ttlSecondsAfterFinished: 0` reaped it instantly,
#: and the privileged workload plus its wildcard ClusterRole/Binding pair
#: persisted in the cluster forever. That is the worst failure this design can
#: produce, so this pin is load-bearing: a test asserts the image resolves AND
#: carries a shell, because "the image exists" was not a sufficient bar.
DEFAULT_REAPER_IMAGE = "alpine/kubectl:1.34.1"


# ===========================================================================
# 4 · Consent
# ===========================================================================

class ClusterConsentRequired(Exception):
    """Refusal: the manifest would plant gated capabilities without consent."""

    def __init__(
        self,
        scenario_id: str,
        posture: "ClusterPosture",
        missing: list[str],
        *,
        disabled_by_deployment: bool = False,
    ):
        self.scenario_id = scenario_id
        self.posture = posture
        self.missing = list(missing)
        self.disabled_by_deployment = disabled_by_deployment
        self.findings = findings_for(posture)
        super().__init__(
            f"scenario={scenario_id} requires consent {missing} for risk_tier="
            f"{posture.risk_tier()}"
        )

    def to_error(self) -> dict[str, Any]:
        """Structured-JSON error body per the repo convention.

        Every requested capability names the OBJECT it lands on. "This scenario
        is privileged" is not actionable; "the pod template sets privileged:
        true" is. ``consent_key`` is the literal key string the caller must
        send, never prose — this is the field that decides whether the gate is a
        decision or a click.
        """
        gated = [f for f in self.findings if f.tier]
        if self.disabled_by_deployment:
            return {
                "error": "Privileged Kubernetes workloads are disabled on this SimCore",
                "code": "CLUSTER_PRIVILEGE_DISABLED",
                "detail": (
                    f"{self.scenario_id} would plant node-access capabilities "
                    f"[{', '.join(f.slug for f in gated if f.tier == TIER_NODE)}] but "
                    f"CORTEXSIM_ALLOW_PRIVILEGED_K8S is false. This is a deployment "
                    f"setting owned by whoever runs this SimCore, not a consent flag — "
                    f"adding a consent key will not change it."
                ),
                "scenario_id": self.scenario_id,
                "risk_tier": self.posture.risk_tier(),
                "requested_capabilities": [_cap_row(f) for f in gated],
            }
        return {
            "error": "Manifest requests gated cluster capabilities without consent",
            "code": "CLUSTER_CONSENT_REQUIRED",
            "detail": (
                f"{self.scenario_id} plants [{', '.join(f.slug for f in gated)}] into the "
                f"target cluster and creates a {self.posture.effective_workload()} that "
                f"persists for {self.posture.ttl_seconds}s. Re-request with "
                f"{' and '.join(f'{k}=true' for k in self.missing)}."
            ),
            "scenario_id": self.scenario_id,
            "risk_tier": self.posture.risk_tier(),
            "requested_capabilities": [_cap_row(f) for f in gated],
            "missing_consent": self.missing,
        }


def _cap_row(f: PostureFinding) -> dict[str, Any]:
    return {
        "capability": f.slug,
        "tier": f.tier,
        "object": f.carrier,
        "consent_key": TIER_CONSENT_KEY.get(f.tier or "", None),
        "why": f.title,
        "cis_control": f.cis_control,
    }


def required_consent_keys(posture: Optional["ClusterPosture"]) -> tuple[str, ...]:
    """Consent keys a caller must set true to generate this manifest.

    The API layer calls this BEFORE generation to gate the request. The
    generator enforces it again when a consent dict is supplied (defence in
    depth), but the HTTP surface is the real boundary: a generator-level gate is
    bypassable by any in-process caller, and the dangerous artifact is the file
    that leaves SimCore, not the function call.
    """
    if posture is None:
        return ()
    return tuple(TIER_CONSENT_KEY[t] for t in posture.tiers())


def check_cluster_consent(
    scenario_id: str,
    posture: Optional["ClusterPosture"],
    consent: dict[str, Any],
    *,
    allow_privileged_k8s: bool = True,
) -> None:
    """Raise :class:`ClusterConsentRequired` when consent is insufficient.

    ``allow_privileged_k8s`` is the deployment-level kill switch for TIER_NODE
    only. Two principals on two timescales: whoever owns this SimCore sets the
    env flag once on their own jumpbox; the operator supplies consent per
    generation. A shared or demo SimCore cannot emit node-breakout bytes at all,
    no matter what anyone POSTs.
    """
    if posture is None:
        return
    tiers = posture.tiers()
    if TIER_NODE in tiers and not allow_privileged_k8s:
        raise ClusterConsentRequired(
            scenario_id, posture,
            [TIER_CONSENT_KEY[TIER_NODE]],
            disabled_by_deployment=True,
        )
    missing = [
        TIER_CONSENT_KEY[t] for t in tiers if not bool(consent.get(TIER_CONSENT_KEY[t]))
    ]
    if missing:
        raise ClusterConsentRequired(scenario_id, posture, missing)


# ===========================================================================
# 5 · The in-pod payload (bootstrap)
# ===========================================================================

#: Binaries the corpus actually shells out to. Derived per step so a missing one
#: is exit 78 with a named cause instead of a "command not found" that scrolls
#: past and reads, in the POV report, as "the customer's stack missed it".
_KNOWN_BINS = (
    "curl", "wget", "python3", "python", "kubectl", "nsenter", "jq", "openssl",
    "tar", "gzip", "gunzip", "unzip", "git", "nc", "ncat", "socat", "dig",
    "nmap", "crontab", "systemctl", "sudo", "docker", "crictl", "base64",
    "xxd", "ss", "ip", "sha256sum", "make", "gcc", "perl", "ruby", "node",
    "npm", "pip3", "aws", "az", "gcloud", "rclone", "ffuf", "hydra",
)
_BIN_RE = re.compile(r"(?<![\w./-])(" + "|".join(_KNOWN_BINS) + r")(?![\w.-])")


def required_binaries(command: str) -> tuple[str, ...]:
    """Binaries a step command needs, scanned from CODE ONLY.

    Uses ``push_generator.code_only`` (strips heredocs, comments and quoted
    strings) deliberately. The asymmetry matters: a FALSE POSITIVE makes the pod
    exit 78 on a step that would have worked — a manufactured failure — while a
    false negative merely lets the step fail with a non-zero rc that the marker
    line records. So scan conservatively.
    """
    from engine.push_generator import code_only  # noqa: PLC0415  (cycle)

    code = code_only(command)
    seen: list[str] = []
    for m in _BIN_RE.finditer(code):
        b = m.group(1)
        if b not in seen:
            seen.append(b)
    return tuple(seen)


def _sq(text: str) -> str:
    """Single-quote-escape for embedding in a bash single-quoted literal.

    Identical to the escape ``generate_bash`` uses for ``run_as``, so push-bash
    and push-k8s cannot diverge on quoting."""
    return text.replace("'", "'\\''")


_BOOTSTRAP_PREAMBLE = """\
#!/bin/bash
# =============================================================================
# CortexSim in-pod payload — {scenario_id}
# =============================================================================
# Rendered by core/engine/k8s_manifest.py. DETERMINISTIC: the same scenario
# always renders the same bytes, because this file's sha256 is baked into the
# manifest at generation time and verified before it is allowed to run.
# Never add a timestamp, a uuid, or unsorted iteration to this file.
#
# NOT `set -e`: a step that legitimately exits non-zero (an RBAC refusal, a
# denied nsenter) is a RESULT, not a bundle failure. Per-step exit codes are
# recorded on the marker lines below and are what `kubectl logs` shows.
set -uo pipefail

CS_SCENARIO="{scenario_id}"
CS_OK=0
CS_TOTAL={total_steps}

cs_marker() {{ printf '[cortexsim] %s t=%s\\n' "$*" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"; }}

# The identity harness is shared VERBATIM with the bash bundle
# (push_generator._build_identity_harness) and calls `log <LEVEL> <msg>`, which
# the bash bundle defines in its own preamble. This preamble is a different
# preamble, so without this definition every step emits
# "bootstrap.sh: line N: log: command not found" to the log a DC shows the
# customer, and the harness's identity/command INFO lines — the causality
# evidence — never appear at all. Verified on a real cluster before this fix.
log() {{ cs_lvl="$1"; shift; printf '[%s] %s\\n' "$cs_lvl" "$*" >&2; }}

cs_die() {{  # cs_die <stage> <code> <what>
  {{
    echo "=== CortexSim K8s delivery FAILED ==="
    echo "stage : $1"
    echo "code  : $2"
    echo "what  : $3"
    echo "scenario: $CS_SCENARIO"
  }} | tee /dev/termination-log >&2
  exit {exit_never_ran}
}}

# The fetch initContainer writes shelf-backed curl/wget shims into the shared
# volume, but its PATH export died with it. Re-prepend here so a scenario's
# verbatim `curl .../linpeas.sh` resolves to the verified shelf copy — and so
# require_bin below finds them and does NOT hard-stop on an image that ships no
# real curl. Harmless when nothing is staged: the directory simply will not exist.
[ -d /cortexsim/bin ] && PATH="/cortexsim/bin:$PATH" && export PATH

require_bin() {{  # require_bin <binary> <step-id>
  command -v "$1" >/dev/null 2>&1 && return 0
  cs_die "$2" MISSING_BIN \\
    "'$1' is absent from image ${{CORTEXSIM_IMAGE:-unknown}}. NOTHING RAN for this \\
step. A step that silently skips its tool reads in the POV report as 'the \\
customer's stack detected nothing' — so this is a hard stop. Declare \\
cluster_posture.image with a base that carries '$1', or stage it on the \\
SimCore payload shelf."
}}

"""

_BOOTSTRAP_FOOTER = """\

cs_marker "scenario=$CS_SCENARIO status=complete steps_ok=$CS_OK/$CS_TOTAL"
if [ "$CS_OK" -eq 0 ] && [ "$CS_TOTAL" -gt 0 ]; then
  cs_marker "scenario=$CS_SCENARIO status=warn detail=every-step-failed"
fi
# Hold the process open so the workload stays in cluster inventory for its TTL.
# A posture finding reaped before the customer's KSPM scan cycle cannot be
# discovered. The reaper Job (or activeDeadlineSeconds) is what ends this.
if [ "${CORTEXSIM_HOLD:-0}" = "1" ]; then
  cs_marker "scenario=$CS_SCENARIO status=holding seconds=${CORTEXSIM_TTL:-0}"
  sleep "${CORTEXSIM_TTL:-600}"
fi
"""


def generate_bootstrap(scenario: dict[str, Any]) -> str:
    """Render the in-pod payload for ``scenario``.

    The in-pod analogue of ``generate_bash``, NOT a copy of it: no host
    dependency checks (we choose the image), no systemd/nohup, and a
    machine-parsable marker line per step so a DC reading ``kubectl logs`` gets
    the same per-step ledger the beacon POSTs.

    Every step runs as a SUBSHELL CHILD of this one anchor shell — the identical
    shape the Go beacon uses — so pull-mode and k8s-push produce the same
    causality tree and ``causality_graph`` needs no special case.

    Raises ``BundleTargetUnsatisfiable`` when the scenario has no POSIX
    resolution, exactly as ``format=bash`` does. A pod must never receive a
    script that parses and then dies in front of a customer.
    """
    from engine.push_generator import (  # noqa: PLC0415  (cycle)
        BundleTargetUnsatisfiable, _build_identity_harness, resolve_target,
    )

    scenario_id = scenario.get("scenario_id", "UNKNOWN")
    resolution = resolve_target(scenario, "posix")
    if not resolution.emittable:
        raise BundleTargetUnsatisfiable(scenario_id, "posix", resolution.unresolved)

    posture = parse_posture(scenario)
    extra_bins = tuple(posture.require_bins) if posture else ()

    out = _BOOTSTRAP_PREAMBLE.format(
        scenario_id=scenario_id,
        total_steps=len(resolution.steps),
        exit_never_ran=EXIT_NEVER_RAN,
    )
    # Identity harness verbatim from the bash bundle — identity resolution must
    # not fork a third time (push-bash, pull-beacon, push-k8s all share it).
    out += _build_identity_harness()
    out += "\n"

    steps_by_id = {s.get("id"): s for s in (scenario.get("steps") or [])}
    for res in resolution.steps:
        raw = steps_by_id.get(res.step_id) or {}
        out += f"# --- {res.step_id}: {raw.get('name', '')}\n"
        if raw.get("mitre_technique"):
            out += f"# MITRE: {raw['mitre_technique']}\n"
        for det in raw.get("expected_detections") or []:
            out += (
                f"# Expected: [{det.get('plane', '?')}] {det.get('type', '?')}: "
                f"{det.get('description', '')}\n"
            )
        bins = list(dict.fromkeys(required_binaries(res.command) + extra_bins))
        for b in bins:
            out += f"require_bin {b} {res.step_id}\n"
        out += f'cs_marker "step={res.step_id} status=start identity={res.identity}"\n'
        out += (
            f"( run_as '{_sq(res.identity)}' '{_sq(res.command)}' '{res.step_id}' )"
            "; CS_RC=$?\n"
        )
        out += (
            f'if [ "$CS_RC" -eq 0 ]; then CS_OK=$((CS_OK+1)); '
            f'cs_marker "step={res.step_id} status=ok rc=0"; '
            f'else cs_marker "step={res.step_id} status=fail rc=$CS_RC"; fi\n\n'
        )

    if resolution.cleanup:
        out += "# --- cleanup\n"
        out += 'cs_marker "scenario=$CS_SCENARIO status=cleanup"\n'
        for cmd, _shell in resolution.cleanup:
            out += f"( {cmd} ) || true\n"
        out += "\n"

    out += _BOOTSTRAP_FOOTER
    return out


def bootstrap_digest(scenario: dict[str, Any]) -> str:
    """sha256 of :func:`generate_bootstrap`. ONE definition of "the digest",
    shared by the manifest builder and the serving endpoint."""
    return hashlib.sha256(generate_bootstrap(scenario).encode("utf-8")).hexdigest()


# ===========================================================================
# 6 · Manifest object graph
# ===========================================================================

def parse_posture(scenario: dict[str, Any]) -> Optional[ClusterPosture]:
    """Coerce ``scenario['cluster_posture']`` to a model. Absent -> None."""
    raw = scenario.get("cluster_posture")
    if not raw:
        return None
    if isinstance(raw, ClusterPosture):
        return raw
    return ClusterPosture.model_validate(raw)


def _label(key: str) -> str:
    return f"{K8S_PREFIX}/{key}"


def _base_labels(scenario_id: str, plane: str, delivery: str) -> dict[str, str]:
    """Labels on EVERY object, cluster-scoped included.

    ``managed-by`` is the teardown guarantee that survives everything else:
    `kubectl get ns,clusterrole,clusterrolebinding -l
    cortexsim.io/managed-by=push-generator` finds anything this engine ever left
    in any cluster, across all scenarios.
    """
    return {
        "app.kubernetes.io/managed-by": "cortexsim",
        _label("managed-by"): "push-generator",
        _label("scenario"): scenario_id,
        _label("plane"): (plane or "unknown").lower().replace("_", "-"),
        _label("delivery"): delivery,
    }


def _pod_security_labels() -> dict[str, str]:
    return {
        "pod-security.kubernetes.io/enforce": "privileged",
        "pod-security.kubernetes.io/warn": "privileged",
        "pod-security.kubernetes.io/audit": "privileged",
    }


def _finding_labels(objs_findings: list[PostureFinding]) -> dict[str, str]:
    """A label value holds ONE slug; a pod plants several. The single-slug label
    carries the highest-severity match (vocabulary order IS severity order) and
    the full set lives in the annotation."""
    if not objs_findings:
        return {}
    return {_label("posture-finding"): objs_findings[0].slug}


def _finding_annotations(objs_findings: list[PostureFinding]) -> dict[str, str]:
    if not objs_findings:
        return {}
    cis = sorted({f.cis_control for f in objs_findings if f.cis_control})
    return {
        _label("posture-findings"): ",".join(f.slug for f in objs_findings),
        _label("posture-findings-count"): str(len(objs_findings)),
        **({_label("cis-controls"): ",".join(cis)} if cis else {}),
    }


def _for_carrier(findings: tuple[PostureFinding, ...], carrier: str) -> list[PostureFinding]:
    return [f for f in findings if f.carrier == carrier]


class PayloadNotStaged(Exception):
    """A scenario declares a tool the shelf does not carry.

    Raised at GENERATION time on purpose. The alternative is a manifest that
    applies cleanly and dies in the pod, which costs a DC a cluster round-trip
    and reads — until they open the logs — exactly like a detection failure.
    """


def _resolve_payloads(names: list[str]) -> list[dict[str, str]]:
    """Bind each declared tool to the shelf's digest for it.

    The digest is resolved HERE, on the DC's SimCore, and baked into the
    manifest. The pod therefore verifies against a value it carried in rather
    than one it fetched from the same server it is trusting — the same anchoring
    the bootstrap digest already uses.

    An empty sha256 would make the pod's `[ "$ACT" = "$WANT" ]` compare against
    "" and pass on anything, which is how an integrity check silently becomes a
    no-op. So a name the shelf cannot resolve is a hard refusal, never a blank.
    """
    if not names:
        return []
    from api.payloads import inventory  # noqa: PLC0415

    shelf = {p["name"]: p["sha256"] for p in inventory()}
    missing = [n for n in names if n not in shelf]
    if missing:
        raise PayloadNotStaged(
            f"scenario declares payload(s) {missing} that are not on the shelf. "
            f"Staged: {sorted(shelf) or 'none'}. Run ./scripts/build-payloads.sh "
            f"on the SimCore host and rebuild the image, or drop them from "
            f"cluster_posture.payloads."
        )
    return [{"name": n, "sha256": shelf[n]} for n in names]


def _integrity_env(scenario_id: str, digest: str, payloads: list[dict[str, str]]) -> str:
    """Flat KEY=VALUE integrity data.

    Deliberately NOT JSON on the verification path. Shell JSON parsing with sed
    returns an empty string on a format drift and `[ "" = "" ]` then passes —
    that is exactly how an integrity check quietly becomes a no-op.
    """
    lines = [
        f"BOOTSTRAP_SHA256={digest}",
        f"SCENARIO_ID={scenario_id}",
        f"PAYLOAD_COUNT={len(payloads)}",
    ]
    for i, p in enumerate(payloads):
        lines.append(f"PAYLOAD_{i}_NAME={p['name']}")
        lines.append(f"PAYLOAD_{i}_SHA256={p.get('sha256', '')}")
    return "\n".join(lines) + "\n"


_EMBEDDED_VERIFY = """\
set -u
die() {{
  echo "[CORTEXSIM][FATAL] $*" >&2
  echo "$*" > /dev/termination-log 2>/dev/null || true
  exit {exit_never_ran}
}}
command -v sha256sum >/dev/null 2>&1 \\
  || die "sha256sum absent from ${{CORTEXSIM_IMAGE}} — CortexSim packaging bug, refusing to run unverified payload"
. /cortexsim-integrity/integrity.env
[ -n "${{BOOTSTRAP_SHA256:-}}" ] || die "integrity.env carries no BOOTSTRAP_SHA256 — refusing to run unverified payload"
cp /cortexsim-src/bootstrap.sh /cortexsim/.bootstrap.part || die "embedded payload ConfigMap not mounted"
ACT="$(sha256sum /cortexsim/.bootstrap.part | awk '{{print $1}}')"
[ -n "$ACT" ] || die "sha256sum produced no digest — refusing to run unverified payload"
[ "$ACT" = "$BOOTSTRAP_SHA256" ] \\
  || die "payload sha256 MISMATCH expected=$BOOTSTRAP_SHA256 actual=$ACT — the manifest was edited after generation. Refusing to execute."
chmod 0755 /cortexsim/.bootstrap.part
# .part -> mv only AFTER verification: it is structurally impossible for the
# runner to find a runnable-but-unverified bootstrap.
mv /cortexsim/.bootstrap.part /cortexsim/bootstrap.sh
echo "[CORTEXSIM] payload verified sha256=$ACT (embedded, self-contained)"
"""

_SERVED_FETCH = """\
set -u
die() {{
  echo "[CORTEXSIM][FATAL] $*" >&2
  echo "$*" > /dev/termination-log 2>/dev/null || true
  exit {exit_never_ran}
}}
command -v sha256sum >/dev/null 2>&1 || die "sha256sum absent from the fetch image — CortexSim packaging bug"
. /cortexsim-integrity/integrity.env
[ -n "${{BOOTSTRAP_SHA256:-}}" ] || die "integrity.env carries no BOOTSTRAP_SHA256 — refusing to run unverified payload"
# Reachability first, and reported SEPARATELY from an auth failure: a 403
# misread as "no route" sends a DC to argue with the customer's network team
# over a credential problem.
wget -q -T 10 -O /dev/null "$CORTEXSIM_SERVER/api/k8s/payloads" \\
  || die "SIMCORE_UNREACHABLE cannot reach $CORTEXSIM_SERVER — this manifest declares delivery-contract=cluster-to-simcore and is NOT self-contained. NOTHING RAN. Check NetworkPolicy, CNI egress, DNS and routing from this cluster to SimCore, or re-download with delivery=embedded."
wget -q -T 60 -O /cortexsim/.bootstrap.part \\
  "$CORTEXSIM_SERVER/api/k8s/bootstrap/$CORTEXSIM_SCENARIO" \\
  || die "BOOTSTRAP_FETCH_FAILED SimCore answered the reachability probe but not the payload for $CORTEXSIM_SCENARIO (expired bundle, revoked token, or the scenario is gone). NOTHING RAN."
ACT="$(sha256sum /cortexsim/.bootstrap.part | awk '{{print $1}}')"
[ -n "$ACT" ] || die "sha256sum produced no digest — refusing to run unverified payload"
[ "$ACT" = "$BOOTSTRAP_SHA256" ] \\
  || die "payload sha256 MISMATCH expected=$BOOTSTRAP_SHA256 actual=$ACT — refusing to execute. The digest was baked into this manifest at generation time on the DC's SimCore, so a mismatch means the served bytes changed. Look for an intercepting proxy."
chmod 0755 /cortexsim/.bootstrap.part
mv /cortexsim/.bootstrap.part /cortexsim/bootstrap.sh
echo "[CORTEXSIM] payload verified sha256=$ACT (served from $CORTEXSIM_SERVER)"
# --- staged tools -----------------------------------------------------------
# Each declared payload is fetched and verified against the digest baked into
# this manifest. A tool that arrives corrupted is refused rather than run: a
# truncated linpeas.sh exits non-zero and reads in the POV report as "the TTP
# ran and the customer's stack saw nothing".
mkdir -p /cortexsim/tools
i=0
while [ "$i" -lt "${{PAYLOAD_COUNT:-0}}" ]; do
  eval "PN=\$PAYLOAD_${{i}}_NAME"
  eval "PS=\$PAYLOAD_${{i}}_SHA256"
  [ -n "$PS" ] || die "PAYLOAD_INTEGRITY_MISSING no digest for '$PN' — refusing to stage an unverifiable tool"
  wget -q -T 120 -O "/cortexsim/tools/.$PN.part" "$CORTEXSIM_SERVER/api/k8s/payload/$PN" \\
    || die "PAYLOAD_FETCH_FAILED could not fetch '$PN' from $CORTEXSIM_SERVER. NOTHING RAN."
  PA="$(sha256sum "/cortexsim/tools/.$PN.part" | awk '{{print $1}}')"
  [ "$PA" = "$PS" ] \\
    || die "PAYLOAD_SHA256_MISMATCH '$PN' expected=$PS actual=$PA — refusing to execute a tool that changed in transit."
  chmod 0755 "/cortexsim/tools/.$PN.part"
  mv "/cortexsim/tools/.$PN.part" "/cortexsim/tools/$PN"
  echo "[CORTEXSIM] staged tool $PN sha256=$PA"
  i=$((i+1))
done

# --- shelf-backed fetch shims ------------------------------------------------
# The scenario's commands are emitted VERBATIM — a DC reading `kubectl logs`
# sees the same `curl -sSL .../linpeas.sh` the scenario declares, and the bash
# bundle for the same scenario stays byte-identical. What changes is where that
# curl RESOLVES: these shims sit first on PATH and serve the request from the
# verified shelf instead of the public internet.
#
# That is the whole point of staging. A pod in a customer's cluster reaching
# github.com is the egress problem this design exists to remove, and it is the
# first thing a default-deny CNI blocks.
#
# A URL whose basename is NOT staged is a hard failure, never a silent network
# fallback: falling through to the internet would work on the DC's laptop and
# fail in the customer's cluster, which is the worst possible place to discover
# the difference.
mkdir -p /cortexsim/bin
cat > /cortexsim/bin/curl <<'CS_SHIM_EOF'
#!/bin/sh
# CortexSim shelf shim. Supports the download shapes the corpus uses:
#   curl [flags] <url> -o <dst>   |   curl [flags] -o <dst> <url>
url=""; dst=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o|--output) dst="$2"; shift 2 ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
[ -n "$url" ] || {{ echo "[cortexsim-shim] no URL in curl invocation" >&2; exit 2; }}
name="${{url##*/}}"
src="/cortexsim/tools/$name"
if [ ! -f "$src" ]; then
  echo "[cortexsim-shim][FATAL] '$name' is not on the SimCore shelf and this pod has no internet egress by design." >&2
  echo "[cortexsim-shim] add it to cluster_posture.payloads and re-stage with ./scripts/build-payloads.sh" >&2
  exit 7
fi
echo "[cortexsim-shim] $name served from the verified shelf (requested $url)" >&2
if [ -n "$dst" ]; then cp "$src" "$dst"; else cat "$src"; fi
CS_SHIM_EOF
chmod 0755 /cortexsim/bin/curl
# wget's download shape is `wget [flags] -O <dst> <url>`; same resolution.
sed 's/-o|--output/-O|--output-document/' /cortexsim/bin/curl > /cortexsim/bin/wget
chmod 0755 /cortexsim/bin/wget
PATH="/cortexsim/bin:$PATH"; export PATH
"""

_RUNNER_CMD = """\
set -u
die() {{
  echo "[CORTEXSIM][FATAL] $*" >&2
  echo "$*" > /dev/termination-log 2>/dev/null || true
  exit {exit_never_ran}
}}
[ -x /cortexsim/bootstrap.sh ] \\
  || die "no verified payload at /cortexsim/bootstrap.sh — the verification step did not complete. NOTHING RAN."
# libc assertion: a glibc-linked tool on a musl image fails in ways that look
# exactly like the customer's stack missing the attack.
if ldd --version 2>&1 | grep -qi musl; then ACTUAL=musl; else ACTUAL=glibc; fi
[ "$CORTEXSIM_LIBC" = "$ACTUAL" ] \\
  || die "cluster_posture.libc declares $CORTEXSIM_LIBC but image $CORTEXSIM_IMAGE is $ACTUAL. NOTHING RAN."
# CGO anchor: copy the shell to a service-shaped name and exec it, so PID 1's
# comm/argv[0] is '<app>' rather than 'bash'. Verified mechanism. Best-effort:
# a wrong anchor NAME is cosmetic, a hard failure here would be a total no-op.
ANCHOR="/usr/local/bin/$CORTEXSIM_ANCHOR"
if SH="$(command -v bash || command -v sh)" && cp "$SH" "$ANCHOR" 2>/dev/null; then
  chmod 0755 "$ANCHOR"
  exec "$ANCHOR" /cortexsim/bootstrap.sh
fi
echo "[CORTEXSIM][WARN] could not install anchor '$CORTEXSIM_ANCHOR' (multi-call shell, non-root uid, or read-only /usr/local/bin) — the CGO anchor will appear as the image's own shell" >&2
# MUST be "$SH", never a hard-coded /bin/sh. bootstrap.sh is a #!/bin/bash
# script using `set -o pipefail`, `local` and `&>`; on debian /bin/sh is dash,
# which dies on line 13 with "Illegal option -o pipefail" BEFORE step-01 runs.
# Observed on a real cluster: SIM-CDR-001 (runAsUser 33 cannot write
# /usr/local/bin, so this fallback is taken) exited 2 having executed nothing,
# while `kubectl rollout status` still reported "successfully rolled out".
# That is the exact false negative this module exists to prevent — the safety
# fallback was the thing guaranteeing the no-op.
exec "${{SH:-/bin/bash}}" /cortexsim/bootstrap.sh
"""

#: 900 KiB. Kubernetes caps a ConfigMap at ~1 MiB of etcd value; the budget is
#: measured against the ENCODED length with headroom for metadata.
CONFIGMAP_BUDGET_BYTES = 921_600


class PayloadTooLargeToEmbed(Exception):
    """Refusal: an embedded payload exceeds the ConfigMap budget.

    Refuse; never truncate and never silently omit. A silently-omitted payload
    is the same false-negative shape as a silent no-op, one layer down.
    """

    def __init__(self, scenario_id: str, encoded: int):
        self.scenario_id = scenario_id
        self.encoded = encoded
        super().__init__(f"{scenario_id}: payload {encoded}B exceeds {CONFIGMAP_BUDGET_BYTES}B")

    def to_error(self) -> dict[str, Any]:
        return {
            "error": "Payload too large to embed",
            "code": "PAYLOAD_TOO_LARGE_TO_EMBED",
            "detail": (
                f"{self.scenario_id}'s payload is {self.encoded} B; the per-ConfigMap "
                f"budget is {CONFIGMAP_BUDGET_BYTES} B (Kubernetes caps a ConfigMap at "
                f"~1 MiB). Re-request with delivery=served (needs cluster->SimCore "
                f"reachability), or bake the payload into an image in the customer's "
                f"own registry and set cluster_posture.image."
            ),
        }


def build_objects(
    scenario: dict[str, Any],
    *,
    posture: Optional[ClusterPosture],
    delivery: str,
    simcore_url: str,
    now: datetime,
    fetch_image: str = DEFAULT_FETCH_IMAGE,
    reaper_image: str = DEFAULT_REAPER_IMAGE,
) -> list[dict[str, Any]]:
    """Build the manifest as a list of dicts.

    Everything that inspects a manifest reads THIS, not the scenario's
    declaration. Serialisation happens afterwards and adds nothing.
    """
    scenario_id = scenario.get("scenario_id", "UNKNOWN")
    plane = scenario.get("plane", "")
    sid_lower = scenario_id.lower().replace("_", "-")

    # A scenario with no posture still gets a first-class object graph — the
    # legacy per-step-Job shape is replaced, not preserved. See docs §5.
    eff = posture or ClusterPosture(workload="job", resources=ResourcesSpec())
    findings = findings_for(eff)

    ns = (posture.namespace if posture and posture.namespace else f"cortexsim-{sid_lower}")
    app = (eff.app_identity.name if eff.app_identity else None) or _anchor_name(scenario)
    component = eff.app_identity.component if eff.app_identity else None
    kind = eff.effective_workload()
    image = eff.resolved_image()

    base = _base_labels(scenario_id, plane, delivery)
    expires = _iso(now.timestamp() + eff.ttl_seconds)
    digest = bootstrap_digest(scenario)
    bootstrap = generate_bootstrap(scenario)

    if delivery == "embedded":
        encoded = len(bootstrap.encode("utf-8"))
        if encoded > CONFIGMAP_BUDGET_BYTES:
            raise PayloadTooLargeToEmbed(scenario_id, encoded)

    manifest_file = f"cortexsim-{scenario_id}-k8s.yaml"
    teardown = (
        f"kubectl delete -f {manifest_file} --ignore-not-found && "
        f"kubectl delete clusterrole,clusterrolebinding "
        f"-l {_label('scenario')}={scenario_id} --ignore-not-found"
    )

    common_annotations = {
        _label("delivery-contract"): (
            "cluster-to-simcore" if delivery == "served" else "self-contained"
        ),
        _label("scenario"): scenario_id,
        _label("uc-ref"): scenario.get("uc_ref", ""),
        _label("tc-ref"): scenario.get("tc_ref", ""),
        _label("mitre-technique"): scenario.get("mitre_technique", ""),
        _label("bootstrap-sha256"): digest,
        _label("risk-tier"): eff.risk_tier(),
        _label("expires-at"): expires,
        _label("ttl-seconds"): str(eff.ttl_seconds),
        _label("teardown"): teardown,
        _label("manifest-file"): manifest_file,
    }
    if delivery == "served":
        common_annotations[_label("simcore-url")] = simcore_url
        if _is_loopback(simcore_url):
            # A pod resolves localhost to ITSELF. This is the single most likely
            # way this feature fails in the field.
            common_annotations[_label("simcore-url-suspect")] = "true"
    if eff.libc == "musl":
        common_annotations[_label("musl-caveat")] = (
            "alpine's shell is a busybox multi-call binary and cannot be renamed; "
            "the CGO anchor will appear as 'busybox', and glibc-linked tools and "
            "Python wheels will not run"
        )

    objects: list[dict[str, Any]] = []

    # ── Namespace ──────────────────────────────────────────────────────────
    ns_findings = _for_carrier(findings, "namespace")
    ns_labels = {**base, **_finding_labels(ns_findings)}
    if eff.requires_privileged_psa():
        # Only on the namespace WE create, and only when PSA baseline would
        # actually reject the pod. Never relax PSA gratuitously.
        ns_labels.update(_pod_security_labels())
    objects.append({
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": ns,
            "labels": ns_labels,
            "annotations": {
                **common_annotations,
                **_finding_annotations(list(findings)),
            },
        },
    })

    # ── ServiceAccount ─────────────────────────────────────────────────────
    sa_findings = _for_carrier(findings, "serviceaccount")
    objects.append({
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {
            "name": app,
            "namespace": ns,
            "labels": {**base, **_finding_labels(sa_findings)},
            "annotations": _finding_annotations(sa_findings),
        },
        "automountServiceAccountToken": eff.service_account.automount_token,
    })

    # ── namespace-scoped Role / RoleBinding ────────────────────────────────
    if eff.service_account.namespace_role_rules:
        objects.append({
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "Role",
            "metadata": {"name": app, "namespace": ns, "labels": dict(base)},
            "rules": [_rbac(r) for r in eff.service_account.namespace_role_rules],
        })
        objects.append({
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "RoleBinding",
            "metadata": {"name": app, "namespace": ns, "labels": dict(base)},
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": app,
            },
            "subjects": [{"kind": "ServiceAccount", "name": app, "namespace": ns}],
        })

    # ── cluster-scoped ClusterRole / ClusterRoleBinding ────────────────────
    cluster_scoped_names: list[tuple[str, str]] = []
    if eff.service_account.cluster_role_rules:
        cr_name = f"cortexsim-{sid_lower}"
        cr_findings = _for_carrier(findings, "clusterrole")
        crb_findings = _for_carrier(findings, "clusterrolebinding")
        objects.append({
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRole",
            "metadata": {
                "name": cr_name,
                "labels": {**base, **_finding_labels(cr_findings)},
                "annotations": _finding_annotations(cr_findings),
            },
            "rules": [_rbac(r) for r in eff.service_account.cluster_role_rules],
        })
        objects.append({
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRoleBinding",
            "metadata": {
                "name": cr_name,
                "labels": {**base, **_finding_labels(crb_findings)},
                "annotations": _finding_annotations(crb_findings),
            },
            # Bind ONLY to a ClusterRole this manifest emitted. A binding to the
            # real `cluster-admin` is not ours to delete cleanly and is
            # indistinguishable from a genuine admin grant in the customer's own
            # review. The posture FINDING shape is identical either way.
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "ClusterRole",
                "name": cr_name,
            },
            "subjects": [{"kind": "ServiceAccount", "name": app, "namespace": ns}],
        })
        cluster_scoped_names = [("clusterroles", cr_name), ("clusterrolebindings", cr_name)]

    # ── integrity ConfigMap ────────────────────────────────────────────────
    payload_entries = _resolve_payloads(eff.payloads)
    integrity_data = {
        "integrity.env": _integrity_env(scenario_id, digest, payload_entries),
        # JSON is for humans and the console, never for the verification path.
        "integrity.json": json.dumps(
            {
                "scenario_id": scenario_id,
                "bootstrap_sha256": digest,
                "payloads": payload_entries,
                "delivery": delivery,
                "simcore_url": simcore_url if delivery == "served" else None,
                "expires_at": expires,
            },
            indent=2, sort_keys=True,
        ) + "\n",
    }
    objects.append({
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": f"{app}-integrity", "namespace": ns, "labels": dict(base),
        },
        "data": integrity_data,
    })

    # ── embedded payload ConfigMap ─────────────────────────────────────────
    if delivery == "embedded":
        objects.append({
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": f"{app}-payload", "namespace": ns, "labels": dict(base),
            },
            "data": {"bootstrap.sh": bootstrap},
        })

    # ── the workload ───────────────────────────────────────────────────────
    pod_findings = _for_carrier(findings, "podtemplate")
    pod_labels = {
        **base,
        "app.kubernetes.io/name": app,
        **({"app.kubernetes.io/component": component} if component else {}),
        **_finding_labels(pod_findings),
    }
    pod_annotations = {**common_annotations, **_finding_annotations(pod_findings)}
    pod_spec = _pod_spec(
        scenario_id=scenario_id, app=app, ns=ns, posture=eff, image=image,
        delivery=delivery, simcore_url=simcore_url, fetch_image=fetch_image,
        kind=kind,
    )
    objects.append(_workload(
        kind=kind, app=app, ns=ns, base=base, pod_labels=pod_labels,
        pod_annotations=pod_annotations, pod_spec=pod_spec, posture=eff,
        annotations={**common_annotations, **_finding_annotations(pod_findings)},
    ))

    # ── reaper ─────────────────────────────────────────────────────────────
    # Mandatory whenever teardown cannot be guaranteed by the API server alone:
    # a Deployment/DaemonSet has no native TTL, and namespace deletion never
    # reaps cluster-scoped objects.
    if kind in ("deployment", "daemonset") or cluster_scoped_names:
        objects.extend(_reaper(
            scenario_id=scenario_id, sid_lower=sid_lower, ns=ns, base=base,
            ttl=eff.ttl_seconds, image=reaper_image,
            cluster_scoped=cluster_scoped_names,
        ))

    return objects


def _rbac(rule: RbacRule) -> dict[str, Any]:
    return {
        "apiGroups": list(rule.api_groups),
        "resources": list(rule.resources),
        "verbs": list(rule.verbs),
    }


def _anchor_name(scenario: dict[str, Any]) -> str:
    """The CGO anchor process name.

    ``cgo_anchor.image_name`` is the GRAPH label; ``app_identity.name`` is the
    RUNTIME fact. When only the graph label exists we take it, so the Causality
    View never shows a process the cluster did not run.
    """
    anchor = (scenario.get("cgo_anchor") or {}).get("image_name")
    if isinstance(anchor, str) and _NS_RE.match(anchor):
        return anchor
    return "cortexsim-runner"


def _pod_spec(
    *, scenario_id: str, app: str, ns: str, posture: ClusterPosture, image: str,
    delivery: str, simcore_url: str, fetch_image: str, kind: str,
) -> dict[str, Any]:
    sc = posture.security_context
    security_context: dict[str, Any] = {
        "privileged": sc.privileged,
        "runAsUser": sc.run_as_user,
        "allowPrivilegeEscalation": sc.allow_privilege_escalation,
        "readOnlyRootFilesystem": sc.read_only_root_filesystem,
        "capabilities": {
            **({"add": list(sc.capabilities_add)} if sc.capabilities_add else {}),
            **({"drop": list(sc.capabilities_drop)} if sc.capabilities_drop else {}),
        },
    }
    if not security_context["capabilities"]:
        security_context.pop("capabilities")

    volumes: list[dict[str, Any]] = [
        {"name": "cortexsim-payload", "emptyDir": {}},
        {"name": "cortexsim-integrity",
         "configMap": {"name": f"{app}-integrity"}},
    ]
    mounts: list[dict[str, Any]] = [
        {"name": "cortexsim-payload", "mountPath": "/cortexsim"},
        {"name": "cortexsim-integrity", "mountPath": "/cortexsim-integrity",
         "readOnly": True},
    ]
    if delivery == "embedded":
        volumes.append({"name": "cortexsim-src",
                        "configMap": {"name": f"{app}-payload"}})
        mounts.append({"name": "cortexsim-src", "mountPath": "/cortexsim-src",
                       "readOnly": True})
    if sc.read_only_root_filesystem:
        # The anchor rename writes to /usr/local/bin. A read-only rootfs would
        # silently downgrade the CGO anchor, so give it a writable overlay.
        volumes.append({"name": "cortexsim-bin", "emptyDir": {}})
        mounts.append({"name": "cortexsim-bin", "mountPath": "/usr/local/bin"})

    for hp in posture.host_access.host_paths:
        volumes.append({
            "name": hp.name,
            "hostPath": {"path": hp.host_path, **({"type": hp.type} if hp.type else {})},
        })
        mounts.append({
            "name": hp.name, "mountPath": hp.mount_path, "readOnly": hp.read_only,
        })

    env = [
        {"name": "CORTEXSIM_SCENARIO", "value": scenario_id},
        {"name": "CORTEXSIM_ANCHOR", "value": app},
        {"name": "CORTEXSIM_LIBC", "value": posture.libc},
        {"name": "CORTEXSIM_IMAGE", "value": image},
        {"name": "CORTEXSIM_TTL", "value": str(posture.ttl_seconds)},
        # A Deployment/DaemonSet must persist in cluster inventory for its TTL
        # or a KSPM scan cycle can never see the planted findings. A Job must
        # terminate.
        {"name": "CORTEXSIM_HOLD", "value": "0" if kind == "job" else "1"},
    ]

    resources = (
        {
            "limits": {
                "cpu": posture.resources.cpu_limit,
                "memory": posture.resources.memory_limit,
            },
            "requests": {
                "cpu": posture.resources.cpu_request,
                "memory": posture.resources.memory_request,
            },
        }
        if posture.resources else {}
    )

    spec: dict[str, Any] = {
        "serviceAccountName": app,
        "automountServiceAccountToken": posture.service_account.automount_token,
        "restartPolicy": "Never" if kind == "job" else "Always",
        "terminationGracePeriodSeconds": 5,
        "enableServiceLinks": False,
        "volumes": volumes,
    }
    if posture.host_access.host_pid:
        spec["hostPID"] = True
    if posture.host_access.host_network:
        spec["hostNetwork"] = True
    if posture.host_access.host_ipc:
        spec["hostIPC"] = True

    # Verified on a real 3-node k3s cluster: the control-plane node carries NO
    # taints and `default` carries NO PSA labels, so a privileged pod with no
    # tolerations schedules onto the control plane and admission does not
    # object. "We don't emit tolerations" is therefore NOT a mitigation — node
    # access needs a POSITIVE anti-affinity. A privileged pod on a control-plane
    # node owns etcd, the cluster CA and every secret in the cluster; the escape
    # scenario is exactly as valid on a worker.
    if TIER_NODE in posture.tiers():
        spec["affinity"] = {
            "nodeAffinity": {
                "requiredDuringSchedulingIgnoredDuringExecution": {
                    "nodeSelectorTerms": [{
                        "matchExpressions": [
                            {"key": "node-role.kubernetes.io/control-plane",
                             "operator": "DoesNotExist"},
                            {"key": "node-role.kubernetes.io/master",
                             "operator": "DoesNotExist"},
                        ],
                    }],
                },
            },
        }

    container: dict[str, Any] = {
        "name": app,
        "image": image,
        "command": ["/bin/sh", "-c"],
        "args": [_RUNNER_CMD.format(exit_never_ran=EXIT_NEVER_RAN)],
        "env": env,
        "securityContext": security_context,
        "volumeMounts": mounts,
        "terminationMessagePolicy": "File",
        "terminationMessagePath": "/dev/termination-log",
    }
    if resources:
        container["resources"] = resources
    if posture.host_access.host_port is not None:
        container["ports"] = [{
            "containerPort": posture.host_access.host_port,
            "hostPort": posture.host_access.host_port,
        }]

    init_env = [
        {"name": "CORTEXSIM_SCENARIO", "value": scenario_id},
        {"name": "CORTEXSIM_IMAGE", "value": image if delivery == "embedded" else fetch_image},
    ]
    if delivery == "served":
        init_env.append({"name": "CORTEXSIM_SERVER", "value": simcore_url})

    spec["initContainers"] = [{
        "name": "cortexsim-payload-verify" if delivery == "embedded" else "cortexsim-payload-fetch",
        "image": image if delivery == "embedded" else fetch_image,
        "command": ["/bin/sh", "-c"],
        "args": [
            (_EMBEDDED_VERIFY if delivery == "embedded" else _SERVED_FETCH).format(
                exit_never_ran=EXIT_NEVER_RAN
            )
        ],
        "env": init_env,
        "volumeMounts": [m for m in mounts if m["name"] != "cortexsim-bin"],
        "terminationMessagePolicy": "File",
        "terminationMessagePath": "/dev/termination-log",
        "resources": {
            "limits": {"cpu": "200m", "memory": "128Mi"},
            "requests": {"cpu": "50m", "memory": "32Mi"},
        },
    }]
    spec["containers"] = [container]
    return spec


def _workload(
    *, kind: str, app: str, ns: str, base: dict[str, str],
    pod_labels: dict[str, str], pod_annotations: dict[str, str],
    pod_spec: dict[str, Any], posture: ClusterPosture,
    annotations: dict[str, str],
) -> dict[str, Any]:
    meta = {
        "name": app, "namespace": ns,
        "labels": {**base, "app.kubernetes.io/name": app},
        "annotations": annotations,
    }
    template = {
        "metadata": {"labels": pod_labels, "annotations": pod_annotations},
        "spec": pod_spec,
    }
    selector = {
        "matchLabels": {
            "app.kubernetes.io/name": app,
            _label("scenario"): base[_label("scenario")],
        }
    }
    if kind == "job":
        return {
            "apiVersion": "batch/v1", "kind": "Job", "metadata": meta,
            "spec": {
                # An API-server-enforced bound that needs no image and no RBAC —
                # the only teardown guarantee available without the reaper.
                "activeDeadlineSeconds": posture.ttl_seconds,
                "backoffLimit": 0,
                # ttlSecondsAfterFinished is DELIBERATELY ABSENT. The shipped
                # generator set 300, which deletes the evidence of a failure
                # five minutes after it happens — and deletes a posture finding
                # before any KSPM scan cycle can discover it.
                "template": template,
            },
        }
    if kind == "daemonset":
        return {
            "apiVersion": "apps/v1", "kind": "DaemonSet", "metadata": meta,
            "spec": {"selector": selector, "template": template},
        }
    return {
        "apiVersion": "apps/v1", "kind": "Deployment", "metadata": meta,
        "spec": {
            "replicas": posture.replicas, "selector": selector, "template": template,
        },
    }


# ORDER IS LOAD-BEARING. RBAC is evaluated per request, so the instant the reaper
# deletes its own ClusterRole it loses every permission that role granted —
# including the one it needs to delete the namespace. Reaping its own RBAC before
# the namespace therefore disarms the reaper and STRANDS the privileged workload,
# which is precisely the outcome this Job exists to prevent.
#
# So: the scenario's cluster-scoped objects, then the namespace (this is what
# actually kills the workload), and only then the reaper's own grant — the
# ClusterRole first, which makes the binding inert the moment it lands, then the
# binding as tidy-up. If that last delete fails because the reaper just revoked
# itself, what survives is an unbound ClusterRole that grants nothing to nobody.
_REAPER_SCRIPT = """\
set -u
echo "[cortexsim-reaper] sleeping ${{TTL}}s before reaping ${{NS}}"
sleep "${{TTL}}"
echo "[cortexsim-reaper] reaping the scenario's cluster-scoped objects"
{deletes}
echo "[cortexsim-reaper] deleting namespace ${{NS}} — this is what kills the workload"
kubectl delete namespace "${{NS}}" --ignore-not-found --wait=false
echo "[cortexsim-reaper] revoking the reaper's own grant (last: this disarms us)"
{own_deletes}
"""


def _reaper(
    *, scenario_id: str, sid_lower: str, ns: str, base: dict[str, str],
    ttl: int, image: str, cluster_scoped: list[tuple[str, str]],
) -> list[dict[str, Any]]:
    """A self-destructing Job that reaps what namespace deletion cannot.

    `kubectl delete ns` does NOT reap a ClusterRole or ClusterRoleBinding. A DC
    who tears down "the usual way" would leave a wildcard-verb,
    cluster-wide-secrets-read ClusterRole permanently bound to a ServiceAccount
    in a customer's production cluster. That is the most serious failure
    available in this design, so it gets a dedicated actor.

    The reaper's own ClusterRole is ``resourceNames``-PINNED to exactly the
    objects this manifest emits. It can never delete anything else in the
    customer's cluster — a resource-type-scoped rule here would itself be a
    cluster-wide RBAC deletion primitive.
    """
    name = f"cortexsim-reaper-{sid_lower}"
    own = [("clusterroles", name), ("clusterrolebindings", name)]
    targets = cluster_scoped + own

    rules: list[dict[str, Any]] = []
    for resource in ("clusterroles", "clusterrolebindings"):
        names = sorted({n for r, n in targets if r == resource})
        if names:
            rules.append({
                "apiGroups": ["rbac.authorization.k8s.io"],
                "resources": [resource],
                "resourceNames": names,
                "verbs": ["delete", "get"],
            })
    rules.append({
        "apiGroups": [""], "resources": ["namespaces"], "resourceNames": [ns],
        "verbs": ["delete", "get"],
    })

    def _lines(pairs: list[tuple[str, str]]) -> str:
        return "\n".join(
            f'kubectl delete {resource} {n} --ignore-not-found'
            for resource, n in pairs
        ) or "true"

    deletes = _lines(cluster_scoped)
    own_deletes = _lines(own)

    return [
        {
            "apiVersion": "v1", "kind": "ServiceAccount",
            "metadata": {"name": name, "namespace": ns, "labels": dict(base)},
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole",
            "metadata": {"name": name, "labels": dict(base)},
            "rules": rules,
        },
        {
            # NOTE: the reaper cannot delete both its own ClusterRole and its own
            # binding — deleting the role revokes the permission needed for the
            # next call, so whichever goes second survives. Verified live on
            # kind: the role went, the binding stayed. That residue is INERT (a
            # binding referencing a nonexistent role grants nothing), so the
            # safety goal is met; the header's ownerReferences patch is the
            # optional way to leave nothing at all.
            "apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRoleBinding",
            "metadata": {"name": name, "labels": dict(base)},
            "roleRef": {"apiGroup": "rbac.authorization.k8s.io",
                        "kind": "ClusterRole", "name": name},
            "subjects": [{"kind": "ServiceAccount", "name": name, "namespace": ns}],
        },
        {
            "apiVersion": "batch/v1", "kind": "Job",
            "metadata": {"name": name, "namespace": ns, "labels": dict(base)},
            "spec": {
                "activeDeadlineSeconds": ttl + 300,
                "backoffLimit": 0,
                "ttlSecondsAfterFinished": 0,
                "template": {
                    "metadata": {"labels": dict(base)},
                    "spec": {
                        "serviceAccountName": name,
                        "restartPolicy": "Never",
                        "containers": [{
                            "name": "reaper",
                            "image": image,
                            "command": ["/bin/sh", "-c"],
                            "args": [_REAPER_SCRIPT.format(deletes=deletes,
                                                           own_deletes=own_deletes)],
                            "env": [
                                {"name": "NS", "value": ns},
                                {"name": "TTL", "value": str(ttl)},
                            ],
                            "resources": {
                                "limits": {"cpu": "100m", "memory": "64Mi"},
                                "requests": {"cpu": "10m", "memory": "32Mi"},
                            },
                        }],
                    },
                },
            },
        },
    ]


# ===========================================================================
# 7 · Scanning the built graph (anti-drift + consent)
# ===========================================================================

def emitted_finding_slugs(objects: list[dict[str, Any]]) -> tuple[str, ...]:
    """Every finding slug actually present in the built object graph.

    The anti-drift probe: a test asserts this equals
    ``{f.slug for f in findings_for(posture)}``. A slug the vocabulary knows but
    the builder never labels, or a label the vocabulary does not know, both fail
    here. That is the mechanism the IaC side lacked when its hand-copied lists
    drifted from what Terraform actually tagged.
    """
    found: set[str] = set()
    for obj in objects:
        meta = obj.get("metadata") or {}
        one = (meta.get("labels") or {}).get(_label("posture-finding"))
        if one:
            found.add(one)
        many = (meta.get("annotations") or {}).get(_label("posture-findings"))
        if many:
            found.update(s for s in many.split(",") if s)
        tmpl = ((obj.get("spec") or {}).get("template") or {}).get("metadata") or {}
        one = (tmpl.get("labels") or {}).get(_label("posture-finding"))
        if one:
            found.add(one)
        many = (tmpl.get("annotations") or {}).get(_label("posture-findings"))
        if many:
            found.update(s for s in many.split(",") if s)
    order = {s: i for i, s in enumerate(all_slugs())}
    return tuple(sorted(found, key=lambda s: order.get(s, 10_000)))


def unmanaged_objects(objects: list[dict[str, Any]]) -> tuple[str, ...]:
    """Objects missing the managed-by label — i.e. objects the label sweep would
    not find. Must always be empty; a test asserts it."""
    bad = []
    for obj in objects:
        labels = (obj.get("metadata") or {}).get("labels") or {}
        if labels.get(_label("managed-by")) != "push-generator":
            bad.append(f'{obj.get("kind")}/{(obj.get("metadata") or {}).get("name")}')
    return tuple(bad)


# ===========================================================================
# 8 · Rendering
# ===========================================================================

def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_loopback(url: str) -> bool:
    return any(h in url for h in ("localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal"))


class _Dumper(yaml.SafeDumper):
    """Block scalars for multi-line strings; two-space list indentation."""

    def increase_indent(self, flow=False, indentless=False):  # noqa: ANN001
        return super().increase_indent(flow, False)


def _str_representer(dumper: yaml.Dumper, data: str):  # noqa: ANN201
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_Dumper.add_representer(str, _str_representer)


_HEADER_RULE = "# " + "=" * 75 + "\n"


def build_header(
    scenario: dict[str, Any],
    *,
    posture: Optional[ClusterPosture],
    objects: list[dict[str, Any]],
    delivery: str,
    simcore_url: str,
    now: datetime,
    reaper_image: str = DEFAULT_REAPER_IMAGE,
    consent_recorded: Optional[dict[str, Any]] = None,
) -> str:
    """The comment block a DC reads before `kubectl apply`.

    Everything load-bearing is stated HERE as well as in labels, annotations and
    response headers, because the header is the only one of the four a human
    actually reads.
    """
    sid = scenario.get("scenario_id", "UNKNOWN")
    eff = posture or ClusterPosture(workload="job")
    findings = findings_for(eff)
    ns = next(o["metadata"]["name"] for o in objects if o["kind"] == "Namespace")
    workload = next(
        o for o in objects
        if o["kind"] in ("Deployment", "Job", "DaemonSet")
        and o["metadata"]["name"] != f"cortexsim-reaper-{sid.lower().replace('_', '-')}"
    )
    app = workload["metadata"]["name"]
    manifest_file = f"cortexsim-{sid}-k8s.yaml"
    has_cluster_scoped = any(
        o["kind"] in ("ClusterRole", "ClusterRoleBinding") for o in objects
    )
    has_reaper = any(
        o["kind"] == "Job" and o["metadata"]["name"].startswith("cortexsim-reaper-")
        for o in objects
    )

    L: list[str] = [_HEADER_RULE]
    L.append("# CortexSim Kubernetes Bundle\n")
    L.append(_HEADER_RULE)
    L.append(f"# Scenario   : {sid} — {scenario.get('name', '')}\n")
    L.append(
        f"# Plane      : {scenario.get('plane', '')}   "
        f"UC/TC: {scenario.get('uc_ref', '')} / {scenario.get('tc_ref', '')}   "
        f"MITRE: {scenario.get('mitre_technique', '')}\n"
    )
    L.append(
        f"# Workload   : {workload['kind']}/{app} in namespace {ns}"
        f"{'  (derived: posture is gated)' if eff.is_gated() and eff.workload == 'auto' else ''}\n"
    )
    L.append(
        f"# Image      : {eff.resolved_image()} ({eff.libc})   "
        f"CGO anchor process: {app}\n"
    )
    L.append(f"# Risk tier  : {eff.risk_tier()}   TTL: {eff.ttl_seconds}s   "
             f"Expires: {_iso(now.timestamp() + eff.ttl_seconds)}\n")
    if consent_recorded is not None:
        granted = sorted(k for k, v in consent_recorded.items() if v)
        L.append(f"# Consent    : {', '.join(granted) if granted else '(none required)'}\n")
    L.append("#\n")

    # ── delivery contract ──────────────────────────────────────────────────
    if delivery == "served":
        L.append("# ── DELIVERY CONTRACT: cluster-to-simcore — NOT self-contained ──────────────\n")
        L.append("# Unlike the bash and PowerShell bundles, this manifest REQUIRES network\n")
        L.append("# reachability from the cluster to SimCore at:\n")
        L.append(f"#     {simcore_url}\n")
        L.append("# The pod fetches and sha256-verifies its payload from that URL at start.\n")
        L.append(f"# If SimCore is unreachable the init container exits {EXIT_NEVER_RAN} and the pod\n")
        L.append("# enters Init:CrashLoopBackOff. It NEVER runs a degraded subset — a manifest\n")
        L.append("# that looked like it ran and emitted nothing would read in the POV report as\n")
        L.append('# "the customer\'s stack detected nothing", the worst outcome this engine can\n')
        L.append("# produce.\n")
        L.append("# Preflight BEFORE you apply:\n")
        L.append("#   kubectl run cortexsim-preflight --rm -it --restart=Never \\\n")
        L.append(f"#     --image={DEFAULT_FETCH_IMAGE} -- wget -q -T5 -O- {simcore_url}/api/k8s/payloads\n")
        if _is_loopback(simcore_url):
            L.append("#\n")
            L.append("# !! WARNING: the SimCore URL above is a LOOPBACK address. Inside a pod it\n")
            L.append("# !! resolves to the POD ITSELF, so every pod will fail identically. Re-download\n")
            L.append("# !! with an explicit cluster-reachable URL, or set CORTEXSIM_PUBLIC_URL.\n")
    else:
        L.append("# ── DELIVERY CONTRACT: self-contained ───────────────────────────────────────\n")
        L.append("# This manifest carries its payload inline (a ConfigMap) and needs NOTHING\n")
        L.append("# from SimCore at runtime — the same invariant the bash and PowerShell\n")
        L.append("# bundles hold. It does pull its container image from a registry.\n")
        L.append("# The payload is still sha256-verified before it is allowed to run, so a\n")
        L.append("# hand-edited manifest fails loudly rather than executing something else.\n")
    L.append("#\n")

    # ── planted findings ───────────────────────────────────────────────────
    if findings:
        title = f"# ── PLANTED POSTURE FINDINGS ({len(findings)}) "
        L.append(title + "─" * max(3, 79 - len(title)) + "\n")
        for f in findings:
            gate = f" [needs {TIER_CONSENT_KEY[f.tier]}]" if f.tier else ""
            L.append(f"#   {f.slug:<42} {f.cis_control or '-':<12}{gate}".rstrip() + "\n")
        L.append("# Vocabulary: GET /api/k8s/posture-findings\n")
        L.append("#\n")

    if eff.libc == "musl":
        L.append("# ── NOTE: libc=musl ─────────────────────────────────────────────────────────\n")
        L.append(f"# The CGO anchor process will be named 'busybox', NOT '{app}' — alpine's\n")
        L.append("# shell is a multi-call binary and cannot be renamed. Causality cards keying\n")
        L.append("# on causality_actor_process_image_name will see 'busybox'. Python wheels and\n")
        L.append("# prebuilt glibc ELFs will NOT run.\n")
        L.append("#\n")

    # ── apply ──────────────────────────────────────────────────────────────
    L.append("# ── APPLY ───────────────────────────────────────────────────────────────────\n")
    L.append(f"#   kubectl apply --dry-run=server -f {manifest_file}   # exercises admission, creates nothing\n")
    L.append(f"#   kubectl apply -f {manifest_file}\n")
    if workload["kind"] == "Deployment":
        L.append(f"#   kubectl -n {ns} rollout status deploy/{app} --timeout=120s\n")
    L.append(f"#   kubectl -n {ns} logs -l app.kubernetes.io/name={app} --all-containers -f\n")
    L.append("# A rollout that is not Ready inside the timeout is the loud signal: the pod\n")
    L.append("# was rejected by admission, could not pull, or could not verify its payload.\n")
    if has_cluster_scoped:
        # Derive the names from the object graph. Hard-coding a scenario-shaped
        # name would emit a patch for an object this manifest did not emit
        # whenever the only cluster-scoped objects are the reaper's own.
        pairs = sorted({
            (o["kind"].lower(), o["metadata"]["name"]) for o in objects
            if o["kind"] in ("ClusterRole", "ClusterRoleBinding")
        })
        L.append("#\n")
        L.append("# Optional, recommended — makes `kubectl delete ns` sufficient forever by\n")
        L.append("# adopting the cluster-scoped objects to the namespace. The namespace UID\n")
        L.append("# does not exist at generation time, so this cannot be baked into the file.\n")
        L.append("# VERIFIED on kind (k8s v1.34): a Namespace is itself cluster-scoped, so it\n")
        L.append("# MAY own a ClusterRole, and the API server's garbage collector really does\n")
        L.append("# reap the dependent when the namespace is deleted. Applying this patch makes\n")
        L.append("# `kubectl delete ns` sufficient on its own, with no debris left behind.\n")
        L.append(f"#   NS_UID=$(kubectl get ns {ns} -o jsonpath='{{.metadata.uid}}')\n")
        L.append("#   OWNER=\"{\\\"metadata\\\":{\\\"ownerReferences\\\":[{\\\"apiVersion\\\":\\\"v1\\\",\\\n")
        L.append("#     \\\"kind\\\":\\\"Namespace\\\",\\\"name\\\":\\\"" + ns + "\\\",\\\n")
        L.append("#     \\\"uid\\\":\\\"$NS_UID\\\",\\\"blockOwnerDeletion\\\":false}]}}\"\n")
        for kind, name in pairs:
            L.append(f"#   kubectl patch {kind} {name} --type=merge -p \"$OWNER\"\n")
    L.append("#\n")

    # ── teardown ───────────────────────────────────────────────────────────
    L.append("# ── TEARDOWN — run this; do NOT rely on the reaper ──────────────────────────\n")
    L.append(f"#   kubectl delete -f {manifest_file} --ignore-not-found\n")
    if has_cluster_scoped:
        L.append("#   kubectl delete clusterrole,clusterrolebinding \\\n")
        L.append(f"#     -l {_label('scenario')}={sid} --ignore-not-found\n")
        L.append("#\n")
        L.append("# `kubectl delete ns` alone is NOT sufficient: namespace deletion garbage-\n")
        L.append("# collects namespaced objects only. This manifest emits a ClusterRole and a\n")
        L.append("# ClusterRoleBinding, and they are named in this file so `delete -f` reaps\n")
        L.append("# them. Tearing down 'the usual way' without the label sweep above would\n")
        L.append("# leave cluster-scoped RBAC in a customer cluster.\n")
    L.append("#\n")
    L.append("# Find ANYTHING this engine ever left behind, in any cluster, all scenarios:\n")
    L.append(f"#   kubectl get ns,clusterrole,clusterrolebinding -l {_label('managed-by')}=push-generator\n")
    L.append("#   # 'No resources found' is the clean state. Screenshot it for the customer.\n")
    L.append("#\n")

    if has_reaper:
        L.append("# ── AUTOMATIC TEARDOWN (best-effort) ────────────────────────────────────────\n")
        L.append(f"# A reaper Job deletes everything above after {eff.ttl_seconds}s. It must pull\n")
        L.append(f"# {reaper_image}. In an air-gapped or registry-mirrored cluster that pull\n")
        L.append("# FAILS, the reaper sits in ImagePullBackOff, and the workload persists until\n")
        L.append("# you delete it — while the annotations above still claim it expires.\n")
        L.append("# The reaper is a SAFETY NET, NOT THE PLAN. Run the teardown command.\n")
        L.append(f"# Verify it landed:  kubectl get ns {ns}\n")
        L.append("#\n")
    L.append(_HEADER_RULE)
    return "".join(L)


def render(objects: list[dict[str, Any]], header: str) -> str:
    docs = [
        yaml.dump(o, Dumper=_Dumper, sort_keys=False, default_flow_style=False,
                  width=10_000)
        for o in objects
    ]
    return header + "---\n" + "---\n".join(docs)
