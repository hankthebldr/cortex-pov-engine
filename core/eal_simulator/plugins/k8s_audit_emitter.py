"""
k8s_audit_emitter — analytics log-streamer for the Kubernetes audit data
source (Kubernetes API-server audit log -> ``kubernetes_audit_logs``).

Built on the ``analytics_emitter`` spine, this plugin POSTs shape-true
Kubernetes API-server audit records to an operator-supplied collector so a
customer can validate that their Cortex XSIAM **Analytics / ABIOC**
Kubernetes-audit detections fire — without touching a real cluster or issuing
any real API call. Every record is a JSON blob shaped *like* the Kubernetes
``audit.k8s.io/v1`` ``Event`` schema (verb / objectRef / user / responseStatus
/ annotations) that the API-server audit backend writes.

The plugin implements only ``build_events`` — the shared driver
(``AnalyticsLogEmitter``) owns the transport, the ``ctx.authorise``-before-emit
gate, the dry-run short-circuit, budget charging, batching/gzip and the
``SimulationResult`` aggregation.

Event-shape presets (parameter ``event_pattern``) — each maps to a real
XSIAM Kubernetes-audit analytics alert:

  ============================ ========================================================
  preset                       analytics / ABIOC alert it exercises
  ============================ ========================================================
  pod_exec                     Analytics — "Unusual exec into a Kubernetes Pod"
                                (create pods/exec subresource) — T1609
  unusual_sa_api_call          ABIOC — "A Kubernetes service account executed an
                                unusual API call" (SA creates a clusterrolebinding it
                                never touches in baseline) — T1078.004
  secret_access                Analytics — "Unusual Kubernetes secret access"
                                (get secrets) — T1552.007
  secret_enumeration           Analytics — "Kubernetes secrets enumeration for the
                                first time" (list secrets, then read each) — T1552.007
  permission_enumeration       Analytics — "A Kubernetes service account has
                                enumerated its permissions" (create
                                selfsubjectaccessreviews / selfsubjectrulesreviews) —
                                T1613
  privileged_pod_creation      Analytics — "Kubernetes Privileged Pod Creation"
                                (create pods with a privileged securityContext) — T1610
  ============================ ========================================================

Only one provider ships (``kubernetes``) — the API-server audit shape is
vendor-neutral (EKS / GKE / AKS / vanilla all emit ``audit.k8s.io/v1``), so a
single builder normalises to the ``kubernetes_audit_logs`` dataset in XSIAM.
"""

from __future__ import annotations

import dataclasses
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import Field, field_validator

from ..analytics_emitter import AnalyticsEmitterParams, AnalyticsLogEmitter


logger = logging.getLogger("cortexsim.eal.plugins.k8s_audit_emitter")


# The XSIAM dataset the Kubernetes API-server audit log normalises to.
_DATASET = "kubernetes_audit_logs"

# Synthetic, non-resolving identifiers. The reserved .invalid TLD and the
# documentation IP ranges (RFC 5737) guarantee nothing here touches a real
# cluster or resolves on a real network.
_CANARY_NAMESPACE = "cortexsim-canary"
_CANARY_SA = f"system:serviceaccount:{_CANARY_NAMESPACE}:compromised-sa"
_CANARY_POD = "web-frontend-7d9c8f-abcde"
_ANOMALOUS_IP = "203.0.113.211"
_CANARY_USER_AGENT = "kubectl/v1.29.0 (linux/amd64) kubernetes/3f7a2b1"


# --------------------------------------------------------------------------
# Per-call API-server activity (one Kubernetes audit Event)
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _K8sApiCall:
    """One Kubernetes API-server request, expressed for the audit shape."""
    verb: str
    resource: str
    subresource: str          # "" when the call has no subresource
    name: str                 # objectRef.name ("" for collection verbs like list)
    request_uri: str
    response_code: int
    decision: str = "allow"   # authorization.k8s.io/decision annotation


# --------------------------------------------------------------------------
# Provider event-shape adapter
# --------------------------------------------------------------------------


def _k8s_audit_event(
    *,
    call: _K8sApiCall,
    marker: str,
    sim_run_id: str,
    source_ip: str = _ANOMALOUS_IP,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Shape a Kubernetes API-server audit Event (subset of audit.k8s.io/v1)."""
    now = datetime.now(timezone.utc).isoformat()
    object_ref: dict[str, Any] = {
        "resource": call.resource,
        "namespace": _CANARY_NAMESPACE,
        "apiVersion": "v1",
    }
    if call.subresource:
        object_ref["subresource"] = call.subresource
    if call.name:
        object_ref["name"] = call.name

    body: dict[str, Any] = {
        "dataset": _DATASET,
        "kind": "Event",
        "apiVersion": "audit.k8s.io/v1",
        "level": "RequestResponse",
        "auditID": secrets.token_hex(16),
        "stage": "ResponseComplete",
        "requestURI": call.request_uri,
        "verb": call.verb,
        "user": {
            "username": _CANARY_SA,
            "uid": secrets.token_hex(8),
            "groups": [
                "system:serviceaccounts",
                f"system:serviceaccounts:{_CANARY_NAMESPACE}",
                "system:authenticated",
            ],
        },
        "sourceIPs": [source_ip],
        "userAgent": _CANARY_USER_AGENT,
        "objectRef": object_ref,
        "responseStatus": {"metadata": {}, "code": call.response_code},
        "requestReceivedTimestamp": now,
        "stageTimestamp": now,
        "annotations": {
            "authorization.k8s.io/decision": call.decision,
            "authorization.k8s.io/reason": (
                "" if call.decision == "allow"
                else "no RBAC policy matched"
            ),
        },
        marker: True,
        "cortexsim_run_id": sim_run_id,
    }
    if extra:
        body.update(extra)
    return body


_PROVIDER_BUILDERS = {
    "kubernetes": _k8s_audit_event,
}


def _list_providers() -> list[str]:
    return sorted(_PROVIDER_BUILDERS)


# --------------------------------------------------------------------------
# Event patterns — the API-server activity each preset emits + its marker
# --------------------------------------------------------------------------


# The privileged PodSpec fragment surfaced in the create-pod request object so
# the analytics rule sees the privileged securityContext.
_PRIVILEGED_POD_SPEC = {
    "kind": "Pod",
    "metadata": {"name": "priv-pod", "namespace": _CANARY_NAMESPACE},
    "spec": {
        "hostPID": True,
        "hostNetwork": True,
        "containers": [{
            "name": "priv",
            "image": "busybox:latest",
            "securityContext": {
                "privileged": True,
                "allowPrivilegeEscalation": True,
                "runAsUser": 0,
                "capabilities": {"add": ["SYS_ADMIN", "NET_ADMIN"]},
            },
            "volumeMounts": [{"name": "host", "mountPath": "/host"}],
        }],
        "volumes": [{"name": "host", "hostPath": {"path": "/"}}],
    },
}


# Each pattern -> (marker, [calls...]). Multi-call patterns emit one record per
# call; secret_enumeration + permission_enumeration additionally fan out
# ``burst_count`` records (handled in build_events).
_PATTERN_CALLS: dict[str, tuple[str, list[_K8sApiCall]]] = {
    "pod_exec": (
        "pod_exec_marker",
        [
            _K8sApiCall(
                verb="create",
                resource="pods",
                subresource="exec",
                name=_CANARY_POD,
                request_uri=(
                    f"/api/v1/namespaces/{_CANARY_NAMESPACE}/pods/{_CANARY_POD}"
                    "/exec?command=%2Fbin%2Fbash&container=web&stdin=true"
                    "&stdout=true&tty=true"
                ),
                response_code=101,  # Switching Protocols (exec upgrade)
            ),
        ],
    ),
    "unusual_sa_api_call": (
        "unusual_sa_api_call_marker",
        [
            _K8sApiCall(
                verb="create",
                resource="clusterrolebindings",
                subresource="",
                name="cortexsim-escalate",
                request_uri="/apis/rbac.authorization.k8s.io/v1/clusterrolebindings",
                response_code=201,
            ),
        ],
    ),
    "secret_access": (
        "secret_access_marker",
        [
            _K8sApiCall(
                verb="get",
                resource="secrets",
                subresource="",
                name="db-credentials",
                request_uri=(
                    f"/api/v1/namespaces/{_CANARY_NAMESPACE}/secrets/db-credentials"
                ),
                response_code=200,
            ),
        ],
    ),
    "secret_enumeration": (
        "secret_enumeration_marker",
        [
            _K8sApiCall(
                verb="list",
                resource="secrets",
                subresource="",
                name="",
                request_uri=(
                    f"/api/v1/namespaces/{_CANARY_NAMESPACE}/secrets?limit=500"
                ),
                response_code=200,
            ),
        ],
    ),
    "permission_enumeration": (
        "permission_enumeration_marker",
        [
            _K8sApiCall(
                verb="create",
                resource="selfsubjectrulesreviews",
                subresource="",
                name="",
                request_uri=(
                    "/apis/authorization.k8s.io/v1/selfsubjectrulesreviews"
                ),
                response_code=201,
            ),
        ],
    ),
    "privileged_pod_creation": (
        "privileged_pod_creation_marker",
        [
            _K8sApiCall(
                verb="create",
                resource="pods",
                subresource="",
                name="priv-pod",
                request_uri=f"/api/v1/namespaces/{_CANARY_NAMESPACE}/pods",
                response_code=201,
            ),
        ],
    ),
}

_EVENT_PATTERNS = tuple(_PATTERN_CALLS)


def _list_event_patterns() -> list[str]:
    return sorted(_EVENT_PATTERNS)


# The per-secret read that follows a list during secret_enumeration.
_SECRET_GET = _K8sApiCall(
    verb="get",
    resource="secrets",
    subresource="",
    name="db-credentials",
    request_uri=f"/api/v1/namespaces/{_CANARY_NAMESPACE}/secrets/db-credentials",
    response_code=200,
)

# The individual access-review probe fanned out during permission_enumeration.
_ACCESS_REVIEW = _K8sApiCall(
    verb="create",
    resource="selfsubjectaccessreviews",
    subresource="",
    name="",
    request_uri="/apis/authorization.k8s.io/v1/selfsubjectaccessreviews",
    response_code=201,
)


# --------------------------------------------------------------------------
# Params — subclass the shared base, add only provider + event_pattern.
# --------------------------------------------------------------------------


class K8sAuditEmitterParams(AnalyticsEmitterParams):
    provider: str = Field(
        default="kubernetes",
        description="API-server audit shape (only 'kubernetes' — the "
                    "audit.k8s.io/v1 Event schema is vendor-neutral across "
                    "EKS / GKE / AKS / vanilla).",
    )
    event_pattern: str = Field(
        default="pod_exec",
        description="Analytics alert to exercise: pod_exec | unusual_sa_api_call | "
                    "secret_access | secret_enumeration | permission_enumeration | "
                    "privileged_pod_creation.",
    )

    @property
    def dataset(self) -> str:
        """The XSIAM dataset the API-server audit log normalises to."""
        return _DATASET

    @field_validator("provider")
    @classmethod
    def _provider_known(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in _PROVIDER_BUILDERS:
            raise ValueError(
                f"provider must be one of {sorted(_PROVIDER_BUILDERS)}, got '{v}'"
            )
        return v

    @field_validator("event_pattern")
    @classmethod
    def _pattern_known(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in _EVENT_PATTERNS:
            raise ValueError(
                f"event_pattern must be one of {sorted(_EVENT_PATTERNS)}, got '{v}'"
            )
        return v


# --------------------------------------------------------------------------
# Plugin
# --------------------------------------------------------------------------


class K8sAuditEmitter(AnalyticsLogEmitter):
    # Ingestion round-trip plant. ``user.username`` is the deliberately NESTED
    # one: Kubernetes audit is the most structurally hostile source in the
    # family (deep JSON, no flat identity field), so it is where a parser that
    # only lifts top-level keys silently loses the principal. ``userAgent`` is
    # the flat control on the same record — raw hit + flat hit + nested miss is
    # a nested-field normalization gap, not a transport gap.
    CANARY_FIELDS = {
        "user.username": "{value}-{account}",
        "objectRef.name": "{value}-{account}",
        "userAgent": "{value} canary/{token}",
    }

    class Meta:
        name = "k8s_audit_emitter"
        version = "1.0.0"
        description = (
            "Emits shape-true Kubernetes API-server audit records "
            "(audit.k8s.io/v1 Event shape, kubernetes_audit_logs dataset) into "
            "an operator-supplied collector so Cortex XSIAM exercises its "
            "Kubernetes Analytics / ABIOC detections (unusual pod exec, unusual "
            "service-account API call, secret access + enumeration, permission "
            "enumeration, privileged pod creation) without touching a real "
            "cluster."
        )
        # Drawn from the analytics alerts this plugin fires: container-admin
        # exec (T1609), deploy privileged container (T1610), container API
        # credentials / secret theft (T1552.007), container & resource
        # discovery (T1613), cloud/SA account abuse (T1078.004). None are
        # Command-and-Control (TA0011) techniques, so live campaigns need only
        # simulation_authorized (not c2_authorized) per the SafetyPolicy.
        mitre_techniques = ["T1609", "T1610", "T1552.007", "T1613", "T1078.004"]
        eal_targets = [
            "Analytics — unusual exec into a Kubernetes pod (pods/exec)",
            "ABIOC — service account executed an unusual API call",
            "Analytics — unusual Kubernetes secret access (get secrets)",
            "Analytics — Kubernetes secrets enumeration for the first time",
            "Analytics — service account enumerated its permissions (access reviews)",
            "Analytics — Kubernetes privileged pod creation",
            "NGFW EAL — outbound POST to XSIAM log-collector App-ID match",
        ]
        ecs_category = "configuration"
        params_model = K8sAuditEmitterParams

    def build_events(
        self, params: K8sAuditEmitterParams, *, sim_run_id: str, iteration: int,
    ) -> list[dict[str, Any]]:
        builder = _PROVIDER_BUILDERS[params.provider]
        marker, calls = _PATTERN_CALLS[params.event_pattern]

        events: list[dict[str, Any]] = []

        if params.event_pattern == "secret_enumeration":
            # The enumeration itself (list secrets) followed by a burst of
            # per-secret reads — the "enumerate then harvest" shape the
            # first-time-enumeration analytic keys on.
            events.append(builder(
                call=calls[0], marker=marker, sim_run_id=sim_run_id,
            ))
            for _ in range(params.burst_count):
                events.append(builder(
                    call=_SECRET_GET, marker=marker, sim_run_id=sim_run_id,
                ))
            return events

        if params.event_pattern == "permission_enumeration":
            # A burst of granular access-review probes (can I get/list/create X?)
            # then the summarising selfsubjectrulesreviews call.
            for _ in range(params.burst_count):
                events.append(builder(
                    call=_ACCESS_REVIEW, marker=marker, sim_run_id=sim_run_id,
                ))
            events.append(builder(
                call=calls[0], marker=marker, sim_run_id=sim_run_id,
            ))
            return events

        if params.event_pattern == "privileged_pod_creation":
            # Surface the privileged PodSpec in the audit requestObject so the
            # rule sees securityContext.privileged=true.
            events.append(builder(
                call=calls[0], marker=marker, sim_run_id=sim_run_id,
                extra={"requestObject": _PRIVILEGED_POD_SPEC},
            ))
            return events

        for call in calls:
            events.append(builder(
                call=call, marker=marker, sim_run_id=sim_run_id,
            ))
        return events
