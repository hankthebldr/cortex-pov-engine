from engine import k8s_manifest as km
from engine.agent_manifest import agent_posture, profile_findings, UnknownAgentProfile
import pytest

def test_baseline_is_ungated_and_mounts_token():
    p = agent_posture("baseline")
    assert p.is_gated() is False
    assert p.service_account.automount_token is True
    assert p.service_account.cluster_role_rules == []
    assert p.security_context.privileged is False
    assert p.host_access.host_pid is False and p.host_access.host_paths == []
    assert p.workload == "deployment" and p.replicas == 1
    assert p.namespace == "cortexsim-agent" and p.image == "debian:12-slim"

def test_cluster_privilege_needs_exactly_the_cluster_key():
    p = agent_posture("cluster-privilege")
    assert km.required_consent_keys(p) == ("cluster_privilege_authorized",)
    r = p.service_account.cluster_role_rules[0]
    assert (r.api_groups, r.resources, r.verbs) == (["*"], ["*"], ["*"])

def test_lab_privileged_is_the_cdr_yml_shape_with_readonly_host_root():
    p = agent_posture("lab-privileged")
    assert set(km.required_consent_keys(p)) == {"cluster_privilege_authorized", "node_access_authorized"}
    assert p.security_context.privileged is True
    assert p.security_context.allow_privilege_escalation is True   # privileged + False is rejected by the API server
    assert set(p.security_context.capabilities_add) == {"SYS_ADMIN", "NET_ADMIN"}
    assert p.host_access.host_pid is True
    hp = p.host_access.host_paths[0]
    assert (hp.host_path, hp.mount_path, hp.read_only) == ("/", "/host", True)

def test_profiles_are_nested_by_findings():
    assert profile_findings("baseline") <= profile_findings("cluster-privilege") <= profile_findings("lab-privileged")

def test_ttl_bounds_and_unknown_profile():
    with pytest.raises(Exception):
        agent_posture("baseline", ttl_seconds=43201)
    with pytest.raises(UnknownAgentProfile):
        agent_posture("root-everything")


@pytest.mark.parametrize("profile", ["baseline", "cluster-privilege", "lab-privileged"])
def test_every_profile_keeps_the_runtime_default_capabilities(profile):
    assert agent_posture(profile).security_context.capabilities_drop == []


def test_baseline_needs_no_consent():
    assert km.required_consent_keys(agent_posture("baseline")) == ()
