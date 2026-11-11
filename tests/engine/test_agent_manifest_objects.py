from datetime import datetime, timedelta, timezone
import subprocess

import pytest

import yaml

from engine import k8s_manifest as km
from engine.agent_manifest import PROFILES, build_agent_objects, generate_agent_manifest

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
    # Baseline still emits the agent ClusterRole, but empty: re-applying it over a
    # cluster-privilege deployment overwrites the wildcard role in place. It
    # grants nothing, and no ClusterRoleBinding to the agent SA is emitted.
    o = objs("baseline")
    rest = [x for x in o if "reaper" not in x["metadata"]["name"]
            and x["kind"] not in ("ClusterRole", "ClusterRoleBinding")]
    text = yaml.safe_dump(rest)
    for needle in ("privileged: true", "hostPID", "hostPath", "ClusterRoleBinding", "ClusterRole"):
        assert needle not in text
    (role,) = [c for c in by_kind(o, "ClusterRole") if "reaper" not in c["metadata"]["name"]]
    assert role["metadata"]["name"] == "cortexsim-agent" and role["rules"] == []
    assert role["metadata"]["annotations"]["cortexsim.io/neutralised"] == "true"
    to_agent_sa = [b for b in by_kind(o, "ClusterRoleBinding")
                   if any(s.get("name") == "cortexsim-agent" for s in b["subjects"])]
    assert to_agent_sa == []


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
                                   ttl_seconds=43200, beacon_sha256="a" * 64, now=NOW, authorized_by=None, consent={}, allow_privileged_k8s=True)
    assert "kind: Secret" not in text and "cxs_" not in text
    assert "restarted pods exit 78 until this manifest is re-applied" in text
    assert list(yaml.safe_load_all(text))   # parses


_REAPER_OWN = {"cortexsim-reaper-agent"}
_FIXED_CLUSTER_NAMES = {("clusterroles", "cortexsim-agent"), ("clusterrolebindings", "cortexsim-agent")}


def _reaper_pins(o):
    role = [r for r in by_kind(o, "ClusterRole") if "reaper" in r["metadata"]["name"]][0]
    return {(rule["resources"][0], n) for rule in role["rules"]
            if rule["resources"][0] in ("clusterroles", "clusterrolebindings")
            for n in rule.get("resourceNames", [])}


@pytest.mark.parametrize("profile", PROFILES)
def test_reaper_pins_the_union_of_every_profiles_cluster_objects(profile):
    union = set()
    for p in PROFILES:
        for x in objs(p, authorized_by="henry"):
            if x["kind"] in ("ClusterRole", "ClusterRoleBinding"):
                union.add((x["kind"].lower() + "s", x["metadata"]["name"]))
    assert {n for _, n in union} == {"cortexsim-agent"} | _REAPER_OWN
    pins = _reaper_pins(objs(profile, authorized_by="henry"))
    assert pins == union
    assert pins == _reaper_pins(objs("baseline"))  # identical for every profile
    script = _reaper(objs(profile, authorized_by="henry"))["spec"]["template"]["spec"]["containers"][0]["args"][0]
    for resource, name in _FIXED_CLUSTER_NAMES:
        assert f"kubectl delete {resource} {name} --ignore-not-found" in script


def test_downgrade_reapply_neutralises_the_wildcard_role():
    first = objs("cluster-privilege", authorized_by="henry")
    (wild,) = [c for c in by_kind(first, "ClusterRole") if c["metadata"]["name"] == "cortexsim-agent"]
    assert wild["rules"]  # the role a downgrade must overwrite
    later = objs("baseline", now=NOW + timedelta(hours=1))
    (role,) = [c for c in by_kind(later, "ClusterRole") if c["metadata"]["name"] == "cortexsim-agent"]
    assert role["rules"] == []
    assert role["apiVersion"] == wild["apiVersion"]  # same object, overwritten in place


def test_header_states_the_profile_change_behaviour():
    text = generate_agent_manifest(profile="baseline", image="debian:12-slim", arch="amd64", prepare="packages",
                                   ttl_seconds=43200, beacon_sha256="a" * 64, now=NOW, authorized_by=None,
                                   consent={}, allow_privileged_k8s=True)
    header = text.split("\n---\n")[0]
    assert "kubectl delete clusterrolebinding cortexsim-agent" in header
    assert "downgrade" in header


def test_gated_profiles_refuse_without_consent():
    import pytest
    from engine import k8s_manifest as km
    kw = dict(image="debian:12-slim", arch="amd64", prepare="packages", ttl_seconds=43200,
              beacon_sha256="a" * 64, now=NOW, authorized_by="henry", allow_privileged_k8s=True)
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
    reaper = [d for d in by_kind(o, "Deployment") if "reaper" in d["metadata"]["name"]][0]
    assert 'kubectl delete namespace "${NS}"' in reaper["spec"]["template"]["spec"]["containers"][0]["args"][0]


def test_header_names_the_agents_objects_not_the_reapers():
    text = generate_agent_manifest(
        profile="cluster-privilege", image="debian:12-slim", arch="amd64", prepare="packages",
        ttl_seconds=43200, beacon_sha256="a" * 64, now=NOW, authorized_by="henry",
        consent={"cluster_privilege_authorized": True}, allow_privileged_k8s=True)
    header = text.split("\n---\n")[0]
    assert "-> ClusterRole/cortexsim-agent" in header and "reaper" not in header.replace("A reaper Deployment", "")


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
                                ttl_seconds=43200, beacon_sha256="a" * 64, now=naive, authorized_by=None, consent={}, allow_privileged_k8s=True)


# --- injection guards and the kill switch at the sink -----------------------

_KW = dict(image="debian:12-slim", arch="amd64", prepare="packages", ttl_seconds=43200,
           beacon_sha256="a" * 64, now=NOW)


@pytest.mark.parametrize("who", ["x\u2028---\u2028apiVersion: v1", "x\u0085y", "x\u2029y", "x\ny",
                                 "x\ry", "a" * 121, "", "  ", "x#y", "x:y"])
def test_authorized_by_that_fails_the_allowlist_is_refused_by_the_builder(who):
    with pytest.raises(ValueError):
        generate_agent_manifest(profile="baseline", authorized_by=who, consent={},
                                allow_privileged_k8s=True, **_KW)
    with pytest.raises(ValueError):
        build_agent_objects(profile="baseline", authorized_by=who, **_KW)


@pytest.mark.parametrize("img", ["a b", "a\nb", "a\u2028b", "a$(id)", "-lead", "", "a" * 256])
def test_image_that_fails_the_allowlist_is_refused_by_the_builder(img):
    kw = {**_KW, "image": img}
    with pytest.raises(ValueError):
        generate_agent_manifest(profile="baseline", authorized_by=None, consent={},
                                allow_privileged_k8s=True, **kw)


@pytest.mark.parametrize("profile,consent", [
    ("baseline", {}),
    ("cluster-privilege", {"cluster_privilege_authorized": True}),
    ("lab-privileged", {"cluster_privilege_authorized": True, "node_access_authorized": True}),
])
def test_emitted_yaml_contains_exactly_the_built_objects(profile, consent):
    text = generate_agent_manifest(
        profile=profile, authorized_by="Henry R. (lab)", consent=consent,
        allow_privileged_k8s=True,
        image="registry.example.com:5000/team/img:1.2@sha256:" + "a" * 64,
        **{k: v for k, v in _KW.items() if k != "image"})
    built = build_agent_objects(
        profile=profile, authorized_by="Henry R. (lab)",
        image="registry.example.com:5000/team/img:1.2@sha256:" + "a" * 64,
        **{k: v for k, v in _KW.items() if k != "image"})
    key = lambda o: (o["kind"], o["metadata"]["name"])
    assert sorted(key(o) for o in yaml.safe_load_all(text) if o) == sorted(key(o) for o in built)


def test_kill_switch_is_enforced_inside_the_builder():
    from engine import k8s_manifest as km
    with pytest.raises(km.ClusterConsentRequired) as ei:
        generate_agent_manifest(
            profile="lab-privileged", authorized_by="henry",
            consent={"cluster_privilege_authorized": True, "node_access_authorized": True},
            allow_privileged_k8s=False, **_KW)
    assert ei.value.disabled_by_deployment is True


# --- re-apply: every generation must be applicable over the previous one -----

from datetime import timedelta

#: Kinds whose changed fields in this manifest are all mutable on apply. A Job is
#: absent on purpose: its spec.template is immutable, so a re-applied manifest
#: whose reaper template changed was rejected (kind run, D1).
_MUTABLE_ON_APPLY = {"ConfigMap", "Deployment", "Namespace", "ServiceAccount",
                     "ClusterRole", "ClusterRoleBinding"}


@pytest.mark.parametrize("profile", PROFILES)
def test_agent_manifest_has_no_job(profile):
    assert by_kind(objs(profile, authorized_by="henry"), "Job") == []


@pytest.mark.parametrize("profile", PROFILES)
def test_two_generations_differ_only_in_objects_that_can_be_reapplied(profile):
    first = {(o["kind"], o["metadata"]["name"]): o for o in objs(profile, authorized_by="henry")}
    later = {(o["kind"], o["metadata"]["name"]): o
             for o in objs(profile, authorized_by="henry", now=NOW + timedelta(hours=3))}
    assert first.keys() == later.keys()
    changed = {k for k in first if first[k] != later[k]}
    assert changed, "the expiry moved, so something must change"
    assert {kind for kind, _ in changed} <= _MUTABLE_ON_APPLY
    # The fields apply cannot change are byte-equal across generations.
    for k in first:
        if k[0] == "Deployment":
            assert yaml.safe_dump(first[k]["spec"]["selector"]) == yaml.safe_dump(later[k]["spec"]["selector"])
        if k[0] == "ClusterRoleBinding":
            assert yaml.safe_dump(first[k]["roleRef"]) == yaml.safe_dump(later[k]["roleRef"])


def test_immutable_fields_are_byte_equal_across_profiles():
    sel, ref = {}, {}
    for p in PROFILES:
        for x in objs(p, authorized_by="henry"):
            key = x["metadata"]["name"]
            if x["kind"] == "Deployment":
                sel.setdefault(key, set()).add(yaml.safe_dump(x["spec"]["selector"]))
            if x["kind"] == "ClusterRoleBinding":
                ref.setdefault(key, set()).add(yaml.safe_dump(x["roleRef"]))
    assert set(sel) == {"cortexsim-agent", "cortexsim-reaper-agent"}
    assert all(len(v) == 1 for v in sel.values()), sel
    assert "cortexsim-agent" in ref and all(len(v) == 1 for v in ref.values()), ref


def _reaper(o):
    return [d for d in by_kind(o, "Deployment") if "reaper" in d["metadata"]["name"]][0]


def test_reaper_is_a_single_recreate_deployment_on_its_pinned_service_account():
    o = objs("cluster-privilege", authorized_by="henry")
    r = _reaper(o)
    assert r["metadata"]["name"] == "cortexsim-reaper-agent"
    assert r["metadata"]["namespace"] == "cortexsim-agent"
    spec = r["spec"]
    assert spec["replicas"] == 1 and spec["strategy"] == {"type": "Recreate"}
    sel = spec["selector"]["matchLabels"]
    tmpl_labels = spec["template"]["metadata"]["labels"]
    assert sel and all(tmpl_labels.get(k) == v for k, v in sel.items())
    # The agent Deployment must not select the reaper's pod, nor the reverse.
    agent = [d for d in by_kind(o, "Deployment") if d["metadata"]["name"] == "cortexsim-agent"][0]
    agent_sel = agent["spec"]["selector"]["matchLabels"]
    assert not all(tmpl_labels.get(k) == v for k, v in agent_sel.items())
    agent_tmpl = agent["spec"]["template"]["metadata"]["labels"]
    assert not all(agent_tmpl.get(k) == v for k, v in sel.items())
    pod_spec = spec["template"]["spec"]
    assert pod_spec["restartPolicy"] == "Always"
    assert pod_spec["serviceAccountName"] == "cortexsim-reaper-agent"
    sas = {s["metadata"]["name"] for s in by_kind(o, "ServiceAccount")}
    assert "cortexsim-reaper-agent" in sas
    (c,) = pod_spec["containers"]
    assert c["image"] == km.DEFAULT_REAPER_IMAGE
    for job_only in ("activeDeadlineSeconds", "backoffLimit", "ttlSecondsAfterFinished"):
        assert job_only not in spec


def test_reaper_waits_to_the_absolute_expiry_not_a_relative_ttl():
    o = objs("baseline")
    c = _reaper(o)["spec"]["template"]["spec"]["containers"][0]
    script = c["args"][0]
    env = {e["name"]: e["value"] for e in c["env"]}
    expires = _reaper(o)["metadata"]["annotations"]["cortexsim.io/expires-at"]
    want = int(datetime.strptime(expires, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
    assert env["EXPIRES_AT_EPOCH"] == str(want)
    # The unconditional relative wait is gone; it survives only as the
    # clock-unavailable fallback (indented, inside the loop).
    assert '\nsleep "${TTL}"\n' not in script
    assert "date +%s" in script and "EXPIRES_AT_EPOCH" in script
    assert subprocess.run(["sh", "-n"], input=script, text=True).returncode == 0


def _run_reaper_wait(tmp_path, epoch_now, expires_epoch, clock_works=True):
    """Run the reaper script with stub date/sleep/kubectl; return (log, rc)."""
    o = objs("baseline")
    c = _reaper(o)["spec"]["template"]["spec"]["containers"][0]
    script = c["args"][0]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    clock = tmp_path / "clock"
    clock.write_text(str(epoch_now))
    log = tmp_path / "log"
    stubs = {
        # date +%s reads the fake clock; any other use fails loudly.
        "date": (f'[ "$1" = "+%s" ] || exit 9; cat "{clock}"\n' if clock_works
                 else 'echo "date: invalid format"; exit 1\n'),
        # sleep advances the fake clock instead of waiting.
        "sleep": f'echo "sleep $1" >> "{log}"; n=$(cat "{clock}"); echo $((n + $1)) > "{clock}"\n',
        "kubectl": f'echo "kubectl $*" >> "{log}"\n',
    }
    for name, body in stubs.items():
        p = bin_dir / name
        p.write_text("#!/bin/sh\n" + body)
        p.chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "NS": "cortexsim-agent",
           "TTL": "43200", "EXPIRES_AT_EPOCH": str(expires_epoch)}
    p = subprocess.run(["sh", "-c", script], env=env, capture_output=True, text=True, timeout=20)
    return (log.read_text() if log.exists() else ""), p.returncode, p.stdout + p.stderr


def test_a_restarted_reaper_sleeps_only_the_remaining_time(tmp_path):
    # Restarted 100s before expiry: it must reap after ~100s, not after a fresh TTL.
    log, rc, out = _run_reaper_wait(tmp_path, epoch_now=1_000_000, expires_epoch=1_000_100)
    sleeps = [int(line.split()[1]) for line in log.splitlines() if line.startswith("sleep ")]
    assert sum(sleeps) == 100, (log, out)
    assert "kubectl delete namespace cortexsim-agent" in log, (log, out)


def test_a_reaper_started_after_expiry_reaps_at_once(tmp_path):
    log, rc, out = _run_reaper_wait(tmp_path, epoch_now=2_000_000, expires_epoch=1_000_000)
    assert not [line for line in log.splitlines() if line.startswith("sleep ")], (log, out)
    assert "kubectl delete namespace cortexsim-agent" in log, (log, out)


def test_agent_readiness_needs_a_stored_agent_id_and_nothing_kills_a_slow_prepare():
    c = pod(objs("baseline"))["containers"][0]
    probe = c["readinessProbe"]
    assert probe["exec"]["command"] == ["/bin/sh", "-c", "test -s /cortexsim/state/agent-id"]
    assert probe["periodSeconds"] == 5
    # A failing liveness or startup probe restarts the container mid package install.
    assert "livenessProbe" not in c and "startupProbe" not in c


def test_a_reaper_without_a_usable_clock_exits_non_zero_and_never_waits_relative(tmp_path):
    # A relative fallback restarts on every pod restart: an unbounded deferral.
    # A visible CrashLoop is better.
    log, rc, out = _run_reaper_wait(tmp_path, epoch_now=1, expires_epoch=10, clock_works=False)
    assert rc != 0
    assert "REAPER_CLOCK_UNAVAILABLE" in out
    assert not [line for line in log.splitlines() if line.startswith("sleep ")], (log, out)
    assert "kubectl delete" not in log, (log, out)


def test_reaper_pod_has_a_short_grace_period():
    # A Recreate rollout must not leave the old-expiry pod alive for 30 s.
    assert _reaper(objs("baseline"))["spec"]["template"]["spec"]["terminationGracePeriodSeconds"] == 1


# ── I5: the header is the one thing a customer reads ─────────────────────────

_REMOVAL_1 = ("# REMOVAL: delete cluster-scoped objects FIRST:  "
              "kubectl delete clusterrole,clusterrolebinding -l cortexsim.io/agent=true")
_REMOVAL_2 = ("#          then:  kubectl delete namespace cortexsim-agent   "
              "(deleting only the namespace leaves the ClusterRole/Binding behind)")
_CONSENT = {"baseline": {}, "cluster-privilege": {"cluster_privilege_authorized": True},
            "lab-privileged": {"cluster_privilege_authorized": True,
                               "node_access_authorized": True}}


def _header(profile="baseline", image="debian:12-slim", prepare="packages"):
    text = generate_agent_manifest(
        profile=profile, image=image, arch="amd64", prepare=prepare, ttl_seconds=43200,
        beacon_sha256="a" * 64, now=NOW, authorized_by="henry",
        consent=_CONSENT[profile], allow_privileged_k8s=True)
    return text.split("\n---\n")[0]


@pytest.mark.parametrize("profile", PROFILES)
def test_header_states_removal_order_verbatim_for_every_profile(profile):
    lines = _header(profile).splitlines()
    assert _REMOVAL_1 in lines and _REMOVAL_2 in lines
    assert lines.index(_REMOVAL_2) == lines.index(_REMOVAL_1) + 1


@pytest.mark.parametrize("prepare", ["packages", "none"])
def test_header_states_image_and_prepare(prepare):
    lines = _header(image="alpine:3.20", prepare=prepare).splitlines()
    assert "# Image      : alpine:3.20" in lines
    assert f"# Prepare    : {prepare}" in lines


@pytest.mark.parametrize("image", [
    "a",
    "registry.example.com:5000/team/img@sha256:" + "f" * 64,
    "A" + "._/:@-" * 42,                      # every punctuation the allowlist takes
    "z" * 255,                                # the longest value it accepts
])
def test_no_image_the_allowlist_accepts_can_break_the_header_line(image):
    from engine.agent_manifest import validate_image
    assert validate_image(image) == image
    header = _header(image=image)
    (line,) = [ln for ln in header.splitlines() if ln.startswith("# Image")]
    assert line == f"# Image      : {image}"


@pytest.mark.parametrize("bad", ["debian\n# injected", "debian\r", "debian 12", "debian#x", ""])
def test_image_values_that_could_break_the_header_are_refused(bad):
    from engine.agent_manifest import validate_image
    with pytest.raises(ValueError):
        validate_image(bad)
