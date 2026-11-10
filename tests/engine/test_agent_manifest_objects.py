from datetime import datetime, timezone

import yaml

from engine.agent_manifest import build_agent_objects, generate_agent_manifest

NOW = datetime(2026, 11, 10, tzinfo=timezone.utc)


def objs(profile, **kw):
    a = dict(profile=profile, image="debian:12-slim", arch="amd64", prepare="packages",
             ttl_seconds=43200, beacon_sha256="a" * 64, now=NOW, authorized_by=None)
    a.update(kw)
    return build_agent_objects(**a)


def by_kind(o, kind):
    return [x for x in o if x["kind"] == kind]


def pod(o):
    return by_kind(o, "Deployment")[0]["spec"]["template"]["spec"]


def test_baseline_has_no_privileged_field_anywhere():
    # The reaper always emits its own ClusterRole/Binding; every other object must be clean.
    rest = [o for o in objs("baseline") if "reaper" not in o["metadata"]["name"]]
    text = yaml.safe_dump(rest)
    for needle in ("privileged: true", "hostPID", "hostPath", "ClusterRoleBinding", "ClusterRole"):
        assert needle not in text
    assert not [c for c in by_kind(objs("baseline"), "ClusterRole") if "reaper" not in c["metadata"]["name"]]


def test_lab_privileged_matches_the_lab_shape():
    p = pod(objs("lab-privileged", authorized_by="henry"))
    sc = p["containers"][0]["securityContext"]
    assert sc["privileged"] is True and set(sc["capabilities"]["add"]) == {"SYS_ADMIN", "NET_ADMIN"}
    assert p["hostPID"] is True
    hp = [v for v in p["volumes"] if "hostPath" in v][0]
    assert hp["hostPath"]["path"] == "/"
    m = [m for m in p["containers"][0]["volumeMounts"] if m["mountPath"] == "/host"][0]
    assert m["readOnly"] is True


def test_every_object_is_labelled_and_expiring():
    for o in objs("cluster-privilege", authorized_by="henry"):
        assert o["metadata"]["labels"]["cortexsim.io/agent"] == "true"
        assert o["metadata"]["annotations"]["cortexsim.io/expires-at"] == "2026-11-10T12:00:00Z"
        assert o["metadata"]["annotations"]["cortexsim.io/authorized-by"] == "henry"


def test_pod_wiring():
    p = pod(objs("baseline"))
    assert [c["name"] for c in p["initContainers"]] == ["fetch", "kubectl"]
    assert p["containers"][0]["command"] == ["/bin/sh", "/cortexsim-boot/start.sh"]
    assert {e["name"] for e in p["containers"][0]["env"]} >= {"POD_NAME", "POD_NAMESPACE", "NODE_NAME"}
    assert all(c["terminationMessagePolicy"] == "File" for c in p["initContainers"] + p["containers"])
    sec = [v for v in p["volumes"] if "secret" in v][0]["secret"]
    assert sec["secretName"] == "cortexsim-enroll"


def test_fetch_init_container_can_read_the_secret():
    fetch = pod(objs("baseline"))["initContainers"][0]
    assert {"/secret", "/cortexsim"} <= {m["mountPath"] for m in fetch["volumeMounts"]}


def test_no_secret_object_and_no_token_in_the_manifest():
    text = generate_agent_manifest(profile="baseline", image="debian:12-slim", arch="amd64", prepare="packages",
                                   ttl_seconds=43200, beacon_sha256="a" * 64, now=NOW, authorized_by=None, consent={})
    assert "kind: Secret" not in text and "cxs_" not in text
    assert "restarted pods exit 78 until this manifest is re-applied" in text
    assert list(yaml.safe_load_all(text))   # parses


def test_reaper_is_pinned_to_exactly_what_was_emitted():
    o = objs("cluster-privilege")
    emitted = {x["metadata"]["name"] for x in by_kind(o, "ClusterRole") + by_kind(o, "ClusterRoleBinding")}
    reaper_role = [r for r in by_kind(o, "ClusterRole") if "reaper" in r["metadata"]["name"]][0]
    names = {n for rule in reaper_role["rules"] for n in rule.get("resourceNames", [])}
    assert names == emitted


def test_gated_profiles_refuse_without_consent():
    import pytest
    from engine import k8s_manifest as km
    kw = dict(image="debian:12-slim", arch="amd64", prepare="packages", ttl_seconds=43200,
              beacon_sha256="a" * 64, now=NOW, authorized_by="henry")
    with pytest.raises(km.ClusterConsentRequired):
        generate_agent_manifest(profile="lab-privileged", consent={}, **kw)
    text = generate_agent_manifest(
        profile="lab-privileged",
        consent={"cluster_privilege_authorized": True, "node_access_authorized": True}, **kw)
    assert "# Authorized : henry" in text and "node_access_authorized" in text


def test_expiry_in_start_script_matches_the_annotation_and_reaper_removes_the_namespace():
    o = objs("baseline")
    start = by_kind(o, "ConfigMap")[0]["data"]["start.sh"]
    expires = by_kind(o, "Deployment")[0]["metadata"]["annotations"]["cortexsim.io/expires-at"]
    assert f"--expires-at {expires}" in start
    job = by_kind(o, "Job")[0]
    assert 'kubectl delete namespace "${NS}"' in job["spec"]["template"]["spec"]["containers"][0]["args"][0]


def test_header_names_the_agents_objects_not_the_reapers():
    text = generate_agent_manifest(
        profile="cluster-privilege", image="debian:12-slim", arch="amd64", prepare="packages",
        ttl_seconds=43200, beacon_sha256="a" * 64, now=NOW, authorized_by="henry",
        consent={"cluster_privilege_authorized": True})
    header = text.split("\n---\n")[0]
    assert "-> ClusterRole/cortexsim-agent" in header and "reaper" not in header.replace("A reaper Job", "")


import pytest

from engine import k8s_manifest as km
from engine.agent_manifest import PROFILES, agent_posture


@pytest.mark.parametrize("profile", PROFILES)
def test_main_container_never_drops_all_capabilities(profile):
    sc = pod(objs(profile, authorized_by="henry"))["containers"][0]["securityContext"]
    assert "ALL" not in sc.get("capabilities", {}).get("drop", [])


def test_baseline_has_no_privilege_or_added_capabilities():
    sc = pod(objs("baseline"))["containers"][0]["securityContext"]
    assert sc["privileged"] is False and sc["allowPrivilegeEscalation"] is False
    assert "add" not in sc.get("capabilities", {})


@pytest.mark.parametrize("profile", PROFILES)
def test_emitted_findings_equal_the_posture_findings(profile):
    o = objs(profile, authorized_by="henry")
    assert set(km.emitted_finding_slugs(o)) == {f.slug for f in km.findings_for(agent_posture(profile))}
    assert km.unmanaged_objects(o) == ()


def test_naive_now_is_rejected():
    naive = datetime(2026, 11, 10)
    with pytest.raises(ValueError, match="now"):
        objs("baseline", now=naive)
    with pytest.raises(ValueError, match="now"):
        generate_agent_manifest(profile="baseline", image="debian:12-slim", arch="amd64", prepare="packages",
                                ttl_seconds=43200, beacon_sha256="a" * 64, now=naive, authorized_by=None, consent={})
