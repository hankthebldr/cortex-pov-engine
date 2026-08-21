"""Cluster posture model, finding vocabulary, consent gate, refusals.

The drift guards live here. Everything else in the K8s track is authored
against this contract, so a change that breaks one of these tests is a change
to the contract and needs the doc updated in the same commit.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from engine import k8s_manifest as km
from engine.k8s_manifest import (
    TIER_CLUSTER,
    TIER_CONSENT_KEY,
    TIER_NODE,
    ClusterConsentRequired,
    ClusterPosture,
    all_slugs,
    findings_for,
    vocabulary,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def posture(**kw) -> ClusterPosture:
    return ClusterPosture.model_validate(kw)


WILDCARD_RBAC = {
    "service_account": {
        "automount_token": True,
        "cluster_role_rules": [{"api_groups": ["*"], "resources": ["*"], "verbs": ["*"]}],
    }
}
NODE_BREAKOUT = {
    "security_context": {"privileged": True},
    "host_access": {
        "host_pid": True,
        "host_paths": [
            {"name": "hostroot", "host_path": "/", "mount_path": "/host",
             "read_only": True}
        ],
    },
}


# ---------------------------------------------------------------------------
# 1 · vocabulary
# ---------------------------------------------------------------------------


def test_slugs_are_unique_and_kebab_case() -> None:
    slugs = all_slugs()
    assert len(slugs) == len(set(slugs)), "duplicate finding slug"
    for s in slugs:
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)+", s), f"non-kebab slug {s!r}"


def test_every_finding_is_reachable_by_some_posture() -> None:
    """A vocabulary entry no posture can plant is drift, inverted: an assertion
    could bind it and never go green for a reason no one can see."""
    reachable: set[str] = set()
    for p in (
        posture(),
        posture(resources=None),
        posture(**WILDCARD_RBAC),
        posture(**NODE_BREAKOUT),
        posture(security_context={"read_only_root_filesystem": True, "run_as_user": 1000,
                                  "allow_privilege_escalation": True,
                                  "capabilities_add": ["SYS_ADMIN"]}),
        posture(host_access={"host_network": True, "host_ipc": True, "host_port": 8080,
                             "host_paths": [{"name": "sock",
                                             "host_path": "/run/containerd/containerd.sock",
                                             "mount_path": "/sock", "type": "Socket"}]}),
        posture(service_account={"cluster_role_rules": [
            {"api_groups": [""], "resources": ["secrets"], "verbs": ["get", "list"]},
            {"api_groups": [""], "resources": ["pods/exec"], "verbs": ["create"]},
            {"api_groups": [""], "resources": ["serviceaccounts/token"], "verbs": ["create"]},
            {"api_groups": [""], "resources": ["users"], "verbs": ["impersonate"]},
        ]}),
    ):
        reachable.update(f.slug for f in findings_for(p))
    assert set(all_slugs()) - reachable == set(), (
        "unreachable vocabulary entries: "
        f"{sorted(set(all_slugs()) - reachable)}"
    )


def test_vocabulary_endpoint_shape() -> None:
    rows = vocabulary()
    assert len(rows) == len(all_slugs())
    for r in rows:
        assert set(r) == {"slug", "title", "cis_control", "carrier", "tier", "consent_key"}
        assert r["carrier"] in {
            "namespace", "serviceaccount", "clusterrole", "clusterrolebinding", "podtemplate"
        }
        assert r["tier"] in (None, TIER_CLUSTER, TIER_NODE)
        assert r["consent_key"] == TIER_CONSENT_KEY.get(r["tier"] or "")


# ---------------------------------------------------------------------------
# 2 · derivation
# ---------------------------------------------------------------------------


def test_baseline_posture_is_ungated() -> None:
    """If baseline ever became gated, the consent prompt would fire on every
    k8s download and an operator would learn to click through it."""
    p = posture()
    assert p.tiers() == ()
    assert p.risk_tier() == "baseline"
    assert km.required_consent_keys(p) == ()
    assert p.effective_workload() == "job"


def test_wildcard_rbac_is_cluster_privilege_only() -> None:
    p = posture(**WILDCARD_RBAC)
    assert p.tiers() == (TIER_CLUSTER,)
    assert km.required_consent_keys(p) == ("cluster_privilege_authorized",)
    assert p.effective_workload() == "deployment"
    slugs = {f.slug for f in findings_for(p)}
    assert "clusterrole-wildcard-verbs" in slugs
    assert "clusterrole-secrets-read-cluster-wide" in slugs  # wildcard resources cover secrets
    assert "clusterrolebinding-to-workload-sa" in slugs


def test_node_breakout_is_node_access_only() -> None:
    """The two tiers are ORTHOGONAL, not nested — a privileged pod that touches
    no RBAC must not require the cluster-privilege key."""
    p = posture(**NODE_BREAKOUT)
    assert p.tiers() == (TIER_NODE,)
    assert km.required_consent_keys(p) == ("node_access_authorized",)


def test_both_tiers_name_both_keys() -> None:
    p = posture(**{**WILDCARD_RBAC, **NODE_BREAKOUT})
    assert p.tiers() == (TIER_CLUSTER, TIER_NODE)
    assert set(km.required_consent_keys(p)) == {
        "cluster_privilege_authorized", "node_access_authorized"
    }


def test_explicit_workload_overrides_derivation() -> None:
    assert posture(workload="job", **WILDCARD_RBAC).effective_workload() == "job"
    assert posture(workload="deployment").effective_workload() == "deployment"
    # `auto` never picks daemonset — per-node execution is always deliberate.
    assert posture(workload="auto", **NODE_BREAKOUT).effective_workload() == "deployment"


def test_privileged_forces_allow_privilege_escalation() -> None:
    """Kubernetes rejects privileged=true with allowPrivilegeEscalation=false
    (caught by --dry-run=server against a real API server). Normalising at the
    model keeps the declaration and the emission in agreement."""
    p = posture(security_context={"privileged": True, "allow_privilege_escalation": False})
    assert p.security_context.allow_privilege_escalation is True
    assert "pod-allow-privilege-escalation" in {f.slug for f in findings_for(p)}


def test_psa_relaxed_only_when_necessary() -> None:
    assert posture().requires_privileged_psa() is False
    assert posture(**WILDCARD_RBAC).requires_privileged_psa() is False
    assert posture(**NODE_BREAKOUT).requires_privileged_psa() is True


def test_default_images() -> None:
    assert posture().resolved_image() == "debian:12-slim"
    assert posture(libc="musl").resolved_image() == "alpine:3.20"
    assert posture(image="registry.customer/ci:1").resolved_image() == "registry.customer/ci:1"


# ---------------------------------------------------------------------------
# 3 · consent
# ---------------------------------------------------------------------------


def test_no_posture_needs_no_consent() -> None:
    km.check_cluster_consent("SIM-X", None, {})  # must not raise
    assert km.required_consent_keys(None) == ()


def test_missing_consent_raises_with_actionable_body() -> None:
    p = posture(**WILDCARD_RBAC)
    with pytest.raises(ClusterConsentRequired) as ei:
        km.check_cluster_consent("SIM-CDR-004", p, {})
    body = ei.value.to_error()
    assert body["code"] == "CLUSTER_CONSENT_REQUIRED"
    assert body["missing_consent"] == ["cluster_privilege_authorized"]
    assert body["risk_tier"] == TIER_CLUSTER
    # Every capability names its object and its literal consent key — "this
    # scenario is privileged" is not actionable.
    for row in body["requested_capabilities"]:
        assert row["object"]
        assert row["consent_key"] in TIER_CONSENT_KEY.values()
        assert row["why"]
    assert "cluster_privilege_authorized=true" in body["detail"]


def test_each_key_unlocks_only_its_own_tier() -> None:
    p = posture(**{**WILDCARD_RBAC, **NODE_BREAKOUT})
    with pytest.raises(ClusterConsentRequired) as ei:
        km.check_cluster_consent("SIM-X", p, {"cluster_privilege_authorized": True})
    assert ei.value.to_error()["missing_consent"] == ["node_access_authorized"]

    with pytest.raises(ClusterConsentRequired) as ei:
        km.check_cluster_consent("SIM-X", p, {"node_access_authorized": True})
    assert ei.value.to_error()["missing_consent"] == ["cluster_privilege_authorized"]

    km.check_cluster_consent("SIM-X", p, {
        "cluster_privilege_authorized": True, "node_access_authorized": True,
    })


def test_deployment_kill_switch_is_a_distinct_code() -> None:
    """The fix is a different action by a different person. Telling a DC to add
    a consent key when the fix is an env var ends with someone editing the wrong
    file."""
    p = posture(**NODE_BREAKOUT)
    with pytest.raises(ClusterConsentRequired) as ei:
        km.check_cluster_consent(
            "SIM-X", p, {"node_access_authorized": True}, allow_privileged_k8s=False
        )
    body = ei.value.to_error()
    assert body["code"] == "CLUSTER_PRIVILEGE_DISABLED"
    assert "CORTEXSIM_ALLOW_PRIVILEGED_K8S" in body["detail"]

    # The kill switch is TIER_NODE only — cluster privilege still works.
    km.check_cluster_consent(
        "SIM-X", posture(**WILDCARD_RBAC),
        {"cluster_privilege_authorized": True}, allow_privileged_k8s=False,
    )


# ---------------------------------------------------------------------------
# 4 · outright refusals — no consent key can unlock these
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ns", ["default", "kube-system", "kube-public", "cert-manager",
                                "monitoring", "argocd", "kube-anything", "openshift-x"])
def test_system_namespaces_are_refused(ns: str) -> None:
    with pytest.raises(ValidationError, match="denylist|cortexsim-"):
        posture(namespace=ns)


def test_namespace_must_be_ours() -> None:
    with pytest.raises(ValidationError, match="must start with 'cortexsim-'"):
        posture(namespace="my-lab")
    posture(namespace="cortexsim-lab-01")  # accepted


@pytest.mark.parametrize("path", ["/", "/etc", "/usr", "/var/lib/kubelet", "/var/lib/etcd"])
def test_writable_hostpath_on_system_roots_is_refused(path: str) -> None:
    with pytest.raises(ValidationError, match="permanently forbidden"):
        posture(host_access={"host_paths": [
            {"name": "h", "host_path": path, "mount_path": "/h", "read_only": False}
        ]})


@pytest.mark.parametrize("path", [
    # Not normalised — the kubelet resolves these, the guard must too.
    "/etc/../", "//etc", "/usr/./bin", "/var/lib/kubelet/../kubelet",
    # Not the literal root — a subtree of one. Every entry here is a node
    # persistence or node-brick primitive strictly worse than the exact root:
    # a writable /etc/cron.d is the host-persistence SIM-CDR-003 has to escape
    # a container to reach, and mounting it would make the escape moot.
    "/etc/cron.d", "/etc/systemd/system", "/root/.ssh", "/usr/bin",
    "/boot/grub", "/lib/systemd", "/var/lib/kubelet/pods", "/var/lib/docker/overlay2",
])
def test_writable_hostpath_matching_is_normalised_and_subtree_aware(path: str) -> None:
    """Regression: the denylist was an exact string match on the authored
    spelling, so every path here was ACCEPTED writable."""
    with pytest.raises(ValidationError, match="permanently forbidden"):
        posture(host_access={"host_paths": [
            {"name": "h", "host_path": path, "mount_path": "/h", "read_only": False}
        ]})


@pytest.mark.parametrize("path", [
    "/var/tmp", "/tmp", "/tmp/cortexsim", "/var/log",
    # The container-runtime socket is a planted finding in its own right and is
    # not under any forbidden root; tightening the guard must not break it.
    "/var/run/docker.sock", "/run/containerd/containerd.sock",
])
def test_writable_hostpath_outside_the_forbidden_roots_is_allowed(path: str) -> None:
    posture(host_access={"host_paths": [
        {"name": "h", "host_path": path, "mount_path": "/h", "read_only": False}
    ]})


@pytest.mark.parametrize("port", [6443, 2379, 2380, 10250])
def test_control_plane_hostports_are_refused(port: int) -> None:
    with pytest.raises(ValidationError, match="control-plane port"):
        posture(host_access={"host_port": port})


def test_ttl_is_bounded() -> None:
    with pytest.raises(ValidationError):
        posture(ttl_seconds=10)
    with pytest.raises(ValidationError):
        posture(ttl_seconds=99_999)
    posture(ttl_seconds=43_200)


# ---------------------------------------------------------------------------
# 5 · anti-drift — the emitted labels ARE the vocabulary
# ---------------------------------------------------------------------------


def test_no_slug_literal_outside_the_vocabulary(repo_root: Path) -> None:
    """A slug literal in a builder is a bug — that is exactly how the IaC side's
    hand-copied finding lists drifted from what Terraform actually tagged.

    Each slug may appear EXACTLY ONCE in k8s_manifest.py (its PostureFinding
    definition) and never in push_generator.py.
    """
    km_src = (repo_root / "core" / "engine" / "k8s_manifest.py").read_text()
    pg_src = (repo_root / "core" / "engine" / "push_generator.py").read_text()
    for slug in all_slugs():
        assert km_src.count(f'"{slug}"') == 1, (
            f"slug {slug!r} appears {km_src.count(chr(34) + slug + chr(34))} times in "
            "k8s_manifest.py — the builder must obtain slugs from findings_for()"
        )
        assert slug not in pg_src, f"slug {slug!r} is hard-coded in push_generator.py"
