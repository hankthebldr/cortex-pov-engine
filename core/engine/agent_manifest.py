"""Posture profiles for the CortexSim Kubernetes container agent.

Each profile is a :class:`~engine.k8s_manifest.ClusterPosture`; the posture model
and ``findings_for`` remain the only source of truth for what a profile plants and
which consent it needs. Nothing here lists a finding slug or a consent key.
"""
from __future__ import annotations

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
