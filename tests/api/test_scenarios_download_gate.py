"""The cluster-consent gate on bundle generation.

The load-bearing test in this file is
``test_no_path_yields_an_ungated_privileged_manifest``: it sweeps every
format × every consent subset across both HTTP surfaces and asserts that no
response body ever contains a privileged pod spec unless the request carried
the matching consent AND the deployment kill switch was on.

Everything else here exists to keep that test honest — that the gate is silent
on ordinary manifests (so it is not ceremony), that its refusals name the
capability and the literal consent key, and that it reads the bytes it is about
to return rather than only the declaration those bytes came from.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest
import yaml


SID = "SIM-CDR-999"

#: Nothing gated. Every scenario in the corpus today is effectively this.
POSTURE_BASELINE = {"app_identity": {"name": "ci-runner"}}

#: Wildcard ClusterRole — cluster_privilege only, touches no node.
POSTURE_TIER1 = {
    "app_identity": {"name": "ci-runner"},
    "service_account": {
        "automount_token": True,
        "cluster_role_rules": [
            {"api_groups": ["*"], "resources": ["*"], "verbs": ["*"]},
        ],
    },
}

#: privileged + hostPID + hostPath:/ — node_access only, needs no RBAC.
POSTURE_TIER2 = {
    "app_identity": {"name": "ci-runner"},
    "security_context": {"privileged": True},
    "host_access": {
        "host_pid": True,
        "host_paths": [{
            "name": "hostroot", "host_path": "/", "mount_path": "/host",
            "type": "Directory", "read_only": True,
        }],
    },
}

#: YAML shapes that only ever appear in a real privileged pod spec. Slugs like
#: ``pod-privileged`` appear in refusal bodies by design, so they are useless as
#: leak markers — these are not.
PRIVILEGED_MARKERS = ("privileged: true", "hostPID: true", "hostPath:")
TIER1_MARKERS = ("kind: ClusterRole", 'verbs:\n      - \'*\'')


@pytest.fixture
def client(make_client):
    from api.scenarios import router  # noqa: PLC0415
    return make_client(router)


def _seed(session_factory, scenario_id: str = SID) -> None:
    from models import Scenario  # noqa: PLC0415

    async def _do():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id=scenario_id,
                name="K8s posture scenario",
                plane="CDR",
                version="1.0",
                status="active",
                uc_ref="UCS-CDR-01", uc_name="X",
                tc_ref="TC-CDR-01", tc_name="Y",
                mitre_tactic="TA0004", mitre_tactic_name="Privilege Escalation",
                mitre_technique="T1611", mitre_technique_name="Escape to Host",
                execution_identity={"default": "root"},
                push_supported=True, pull_supported=True,
                steps=[{
                    "id": "step-01", "name": "probe", "identity": "root",
                    "command": "echo hello", "platforms": ["k8s", "container"],
                    "expected_detections": [],
                }],
            ))
            await db.commit()

    asyncio.run(_do())


@pytest.fixture
def posture(monkeypatch):
    """Install a declared cluster_posture for SID.

    Patches the disk-hydration seam rather than the ORM because the
    ``cluster_posture`` column is owned by another track; the gate consumes the
    posture from whichever of the two sources has it (see
    ``api.scenarios._scenario_document``).
    """
    def _install(block):
        monkeypatch.setattr(
            "api.scenarios._disk_posture",
            lambda sid: block if sid == SID else None,
        )
    return _install


@pytest.fixture
def kill_switch(monkeypatch):
    """Control CORTEXSIM_ALLOW_PRIVILEGED_K8S at the single resolver."""
    def _set(value: bool):
        monkeypatch.setattr("engine.orchestrator.allow_privileged_k8s", lambda: value)
    _set(False)          # default OFF, as shipped
    return _set


# ===========================================================================
# 1 · The gate is silent on ordinary manifests
# ===========================================================================

def test_baseline_k8s_download_is_ungated(client, session_factory):
    """No cluster_posture at all — today's entire corpus. Must not gate."""
    _seed(session_factory)
    r = client.get(f"/api/scenarios/{SID}/download?format=k8s")
    assert r.status_code == 200, r.text
    assert r.headers["X-CortexSim-Bundle-Risk-Tier"] == "baseline"
    # The two ungated findings a Kubernetes default plants are reported, not gated.
    caps = r.headers["X-CortexSim-Bundle-Capabilities"].split(",")
    assert "pod-run-as-root" in caps
    assert all(m not in r.text for m in PRIVILEGED_MARKERS)


def test_declared_but_ungated_posture_still_downloads_over_get(
    client, session_factory, posture,
):
    """Declaring the block is not itself privilege. A gate that fires on every
    k8s download trains the operator to click through."""
    _seed(session_factory)
    posture(POSTURE_BASELINE)
    r = client.get(f"/api/scenarios/{SID}/download?format=k8s")
    assert r.status_code == 200, r.text
    assert r.headers["X-CortexSim-Bundle-Risk-Tier"] == "baseline"


def test_bash_and_powershell_are_never_gated(client, session_factory, posture):
    """A self-contained script a DC runs on a host they own is not a cluster
    object. The gate must not leak onto it even for a tier-2 scenario."""
    _seed(session_factory)
    posture(POSTURE_TIER2)
    assert client.get(f"/api/scenarios/{SID}/download?format=bash").status_code == 200
    r = client.post(f"/api/scenarios/{SID}/bundle", json={"format": "bash"})
    assert r.status_code == 200, r.text
    assert r.headers["X-CortexSim-Bundle-Risk-Tier"] == "baseline"


def test_auto_never_resolves_to_k8s(client, session_factory, posture):
    """`auto` must not become a side door: it resolves POSIX-first and never
    selects k8s, so it can never hand back a manifest nobody asked for."""
    _seed(session_factory)
    posture(POSTURE_TIER2)
    for r in (
        client.get(f"/api/scenarios/{SID}/download"),
        client.post(f"/api/scenarios/{SID}/bundle", json={"format": "auto"}),
    ):
        assert r.status_code == 200, r.text
        assert r.headers["X-CortexSim-Bundle-Target"] == "posix"
        assert all(m not in r.text for m in PRIVILEGED_MARKERS)


# ===========================================================================
# 2 · THE PROOF — an ungated privileged manifest cannot be obtained
# ===========================================================================

def test_no_path_yields_an_ungated_privileged_manifest(
    client, session_factory, posture, kill_switch,
):
    """Sweep every surface × format × consent subset for a node-access scenario.

    Any 200 whose body contains a privileged pod spec must have been asked for
    with node_access_authorized AND a named operator AND the deployment kill
    switch on. Anything else that returns privileged bytes is a hole.
    """
    _seed(session_factory)
    posture(POSTURE_TIER2)
    kill_switch(False)

    consent_subsets = [
        {},
        {"cluster_privilege_authorized": True},
        {"node_access_authorized": True},
        {"cluster_privilege_authorized": True, "node_access_authorized": True},
        {"simulation_authorized": True, "c2_authorized": True},   # wrong keys
        {"node_access_authorized": False},
    ]
    responses = []
    for fmt in ("auto", "bash", "powershell", "k8s"):
        responses.append(client.get(f"/api/scenarios/{SID}/download?format={fmt}"))
        for consent in consent_subsets:
            for who in ("", "h.reed"):
                responses.append(client.post(
                    f"/api/scenarios/{SID}/bundle",
                    json={"format": fmt, "consent": consent, "authorized_by": who},
                ))

    leaked = [
        r for r in responses
        if r.status_code == 200 and any(m in r.text for m in PRIVILEGED_MARKERS)
    ]
    assert leaked == [], (
        f"{len(leaked)} response(s) returned a privileged manifest without "
        f"consent + operator + kill switch"
    )

    # ...and the same sweep with the kill switch ON still refuses everything
    # that did not name node_access_authorized and an operator.
    kill_switch(True)
    for consent in consent_subsets:
        r = client.post(
            f"/api/scenarios/{SID}/bundle",
            json={"format": "k8s", "consent": consent, "authorized_by": ""},
        )
        assert r.status_code != 200, "blank authorized_by must never yield bytes"
    r = client.post(
        f"/api/scenarios/{SID}/bundle",
        json={"format": "k8s", "consent": {"cluster_privilege_authorized": True},
              "authorized_by": "h.reed"},
    )
    assert r.status_code == 409, "the wrong tier's key must not unlock node access"


def test_the_authorised_path_does_produce_the_privileged_manifest(
    client, session_factory, posture, kill_switch,
):
    """The negative sweep above is only meaningful if the positive path works —
    otherwise the gate would 'pass' by making the feature impossible."""
    _seed(session_factory)
    posture(POSTURE_TIER2)
    kill_switch(True)
    r = client.post(
        f"/api/scenarios/{SID}/bundle",
        json={"format": "k8s", "consent": {"node_access_authorized": True},
              "authorized_by": "h.reed", "reason": "ACME POV day 2"},
    )
    assert r.status_code == 200, r.text
    assert all(m in r.text for m in PRIVILEGED_MARKERS)
    assert r.headers["X-CortexSim-Bundle-Risk-Tier"] == "node_access"
    assert r.headers["X-CortexSim-Bundle-Authorized-By"] == "h.reed"
    assert r.headers["X-CortexSim-Bundle-Consent-Keys"] == "node_access_authorized"
    assert "pod-privileged" in r.headers["X-CortexSim-Bundle-Capabilities"]
    assert len(r.headers["X-CortexSim-Manifest-SHA256"]) == 64


# ===========================================================================
# 3 · Refusals are actionable
# ===========================================================================

def test_get_on_a_gated_scenario_names_the_capability_and_the_post(
    client, session_factory, posture,
):
    _seed(session_factory)
    posture(POSTURE_TIER1)
    r = client.get(f"/api/scenarios/{SID}/download?format=k8s")
    assert r.status_code == 409
    body = r.json()["detail"]
    assert body["code"] == "GATED_FORMAT_REQUIRES_POST"
    assert body["risk_tier"] == "cluster_privilege"
    assert body["missing_consent"] == ["cluster_privilege_authorized"]
    caps = {c["capability"]: c for c in body["requested_capabilities"]}
    assert "clusterrole-wildcard-verbs" in caps
    # names the OBJECT, the literal consent key, and a plain-English why
    row = caps["clusterrole-wildcard-verbs"]
    assert row["object"] == "clusterrole"
    assert row["consent_key"] == "cluster_privilege_authorized"
    assert row["why"]
    assert f"POST /api/scenarios/{SID}/bundle" in body["remedy"]
    # and it is a refusal, NOT a silent downgrade to a baseline manifest
    assert "kind: ClusterRole" not in r.text


def test_post_without_consent_names_the_key_to_send(client, session_factory, posture):
    _seed(session_factory)
    posture(POSTURE_TIER1)
    r = client.post(f"/api/scenarios/{SID}/bundle",
                    json={"format": "k8s", "authorized_by": "h.reed"})
    assert r.status_code == 409
    body = r.json()["detail"]
    assert body["code"] == "CLUSTER_CONSENT_REQUIRED"
    assert body["missing_consent"] == ["cluster_privilege_authorized"]
    assert "consent.cluster_privilege_authorized=true" in body["remedy"]
    assert body["docs"] == "docs/reference/k8s-delivery.md"


def test_blank_authorized_by_is_refused_and_says_so(client, session_factory, posture):
    """An audit row with no named operator proves nothing."""
    _seed(session_factory)
    posture(POSTURE_TIER1)
    r = client.post(f"/api/scenarios/{SID}/bundle", json={
        "format": "k8s",
        "consent": {"cluster_privilege_authorized": True},
        "authorized_by": "   ",
    })
    assert r.status_code == 409
    body = r.json()["detail"]
    assert body["missing_consent"] == ["authorized_by"]
    assert "kind: ClusterRole" not in r.text


def test_missing_consent_and_missing_operator_arrive_in_one_refusal(
    client, session_factory, posture,
):
    """One round trip, one complete list — not a game of twenty questions."""
    _seed(session_factory)
    posture(POSTURE_TIER1)
    r = client.post(f"/api/scenarios/{SID}/bundle", json={"format": "k8s"})
    assert r.status_code == 409
    body = r.json()["detail"]
    assert set(body["missing_consent"]) == {"cluster_privilege_authorized", "authorized_by"}


def test_kill_switch_off_is_403_not_409(client, session_factory, posture, kill_switch):
    """403 vs 409 is not cosmetic: the fix is a different action by a different
    person. Telling a DC to add a consent key when the fix is an env var is how
    someone edits the wrong file."""
    _seed(session_factory)
    posture(POSTURE_TIER2)
    kill_switch(False)
    r = client.post(f"/api/scenarios/{SID}/bundle", json={
        "format": "k8s",
        "consent": {"node_access_authorized": True},
        "authorized_by": "h.reed",
    })
    assert r.status_code == 403
    body = r.json()["detail"]
    assert body["code"] == "CLUSTER_PRIVILEGE_DISABLED"
    assert "CORTEXSIM_ALLOW_PRIVILEGED_K8S" in body["remedy"]
    assert all(m not in r.text for m in PRIVILEGED_MARKERS)


# ===========================================================================
# 4 · The two tiers are orthogonal, not nested
# ===========================================================================

def test_tier1_consent_does_not_unlock_tier2(client, session_factory, posture, kill_switch):
    _seed(session_factory)
    posture(POSTURE_TIER2)
    kill_switch(True)
    r = client.post(f"/api/scenarios/{SID}/bundle", json={
        "format": "k8s",
        "consent": {"cluster_privilege_authorized": True},
        "authorized_by": "h.reed",
    })
    assert r.status_code == 409
    assert r.json()["detail"]["missing_consent"] == ["node_access_authorized"]


def test_tier2_consent_does_not_unlock_tier1(client, session_factory, posture, kill_switch):
    _seed(session_factory)
    posture(POSTURE_TIER1)
    kill_switch(True)
    r = client.post(f"/api/scenarios/{SID}/bundle", json={
        "format": "k8s",
        "consent": {"node_access_authorized": True},
        "authorized_by": "h.reed",
    })
    assert r.status_code == 409
    assert r.json()["detail"]["missing_consent"] == ["cluster_privilege_authorized"]


def test_tier1_alone_needs_only_its_own_key(client, session_factory, posture, kill_switch):
    """A wildcard-RBAC manifest touches no node. Demanding the node key too
    would fire the gate on a case it does not describe."""
    _seed(session_factory)
    posture(POSTURE_TIER1)
    kill_switch(False)   # deliberately OFF — tier 1 must not depend on it
    r = client.post(f"/api/scenarios/{SID}/bundle", json={
        "format": "k8s",
        "consent": {"cluster_privilege_authorized": True},
        "authorized_by": "h.reed",
    })
    assert r.status_code == 200, r.text
    assert "kind: ClusterRole" in r.text
    assert r.headers["X-CortexSim-Bundle-Risk-Tier"] == "cluster_privilege"
    assert all(m not in r.text for m in PRIVILEGED_MARKERS)


# ===========================================================================
# 5 · Refusals no consent key unlocks
# ===========================================================================

@pytest.mark.parametrize("namespace", ["default", "kube-system", "monitoring", "argocd"])
def test_system_namespace_posture_is_refused_regardless_of_consent(
    client, session_factory, posture, kill_switch, namespace,
):
    _seed(session_factory)
    posture({**POSTURE_TIER2, "namespace": namespace})
    kill_switch(True)
    r = client.post(f"/api/scenarios/{SID}/bundle", json={
        "format": "k8s",
        "consent": {"node_access_authorized": True, "cluster_privilege_authorized": True},
        "authorized_by": "h.reed",
    })
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "CLUSTER_CAPABILITY_FORBIDDEN"


def test_writable_node_root_is_refused_regardless_of_consent(
    client, session_factory, posture, kill_switch,
):
    _seed(session_factory)
    posture({
        "app_identity": {"name": "ci-runner"},
        "host_access": {"host_paths": [{
            "name": "hostroot", "host_path": "/", "mount_path": "/host",
            "type": "Directory", "read_only": False,
        }]},
    })
    kill_switch(True)
    r = client.post(f"/api/scenarios/{SID}/bundle", json={
        "format": "k8s",
        "consent": {"node_access_authorized": True},
        "authorized_by": "h.reed",
    })
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "CLUSTER_CAPABILITY_FORBIDDEN"


# ===========================================================================
# 6 · The gate reads the EMITTED bytes, not only the declaration
# ===========================================================================

def _manifest(objects) -> str:
    return "\n---\n".join(yaml.safe_dump(o) for o in objects)


def _obj(kind, name, *, labels=None, namespace="cortexsim-sim-cdr-999"):
    meta = {"name": name, "labels": {
        "cortexsim.io/managed-by": "push-generator", **(labels or {}),
    }}
    if namespace is not None:
        meta["namespace"] = namespace
    return {"apiVersion": "v1", "kind": kind, "metadata": meta}


def test_emitted_privileged_finding_without_consent_is_discarded(caplog):
    """The drift guard. If a template ever emits a capability the declaration
    did not predict, the bytes are scanned and thrown away — a declaration is a
    comment, an emission is what lands in the customer's cluster."""
    from api.scenarios import _verify_emitted  # noqa: PLC0415
    from fastapi import HTTPException  # noqa: PLC0415

    content = _manifest([
        _obj("Deployment", "ci-runner",
             labels={"cortexsim.io/posture-finding": "pod-privileged"}),
    ])
    with caplog.at_level(logging.CRITICAL):
        with pytest.raises(HTTPException) as ei:
            _verify_emitted("SIM-CDR-999", content, {},
                            allow_privileged_k8s=True, authorized_by="h.reed")
    assert ei.value.status_code == 409
    assert ei.value.detail["code"] == "CLUSTER_CONSENT_REQUIRED"
    assert ei.value.detail["missing_consent"] == ["node_access_authorized"]
    assert "DISCARDED" in caplog.text


def test_emitted_privileged_finding_is_403_when_the_kill_switch_is_off():
    from api.scenarios import _verify_emitted  # noqa: PLC0415
    from fastapi import HTTPException  # noqa: PLC0415

    content = _manifest([
        _obj("Deployment", "ci-runner",
             labels={"cortexsim.io/posture-finding": "pod-privileged"}),
    ])
    with pytest.raises(HTTPException) as ei:
        _verify_emitted("SIM-CDR-999", content, {"node_access_authorized": True},
                        allow_privileged_k8s=False, authorized_by="h.reed")
    assert ei.value.status_code == 403
    assert ei.value.detail["code"] == "CLUSTER_PRIVILEGE_DISABLED"


@pytest.mark.parametrize("namespace", ["default", "kube-system", "customer-apps", ""])
def test_emitted_object_outside_an_owned_namespace_is_refused(namespace):
    """Requirement: an absolute refusal to target kube-system or an unnamed
    namespace, checked on the bytes rather than on the declaration."""
    from api.scenarios import _verify_emitted  # noqa: PLC0415
    from fastapi import HTTPException  # noqa: PLC0415

    content = _manifest([_obj("ConfigMap", "payload", namespace=namespace)])
    with pytest.raises(HTTPException) as ei:
        _verify_emitted("SIM-CDR-999", content, {},
                        allow_privileged_k8s=True, authorized_by="h.reed")
    assert ei.value.status_code == 422
    assert ei.value.detail["code"] == "CLUSTER_NAMESPACE_FORBIDDEN"


def test_emitted_object_the_teardown_sweep_cannot_find_is_refused():
    """A DC must be able to prove the workload went away. An object without the
    sweep label cannot be found by the documented teardown command."""
    from api.scenarios import _verify_emitted  # noqa: PLC0415
    from fastapi import HTTPException  # noqa: PLC0415

    orphan = _obj("ConfigMap", "payload")
    orphan["metadata"]["labels"].pop("cortexsim.io/managed-by")
    with pytest.raises(HTTPException) as ei:
        _verify_emitted("SIM-CDR-999", _manifest([orphan]), {},
                        allow_privileged_k8s=True, authorized_by="h.reed")
    assert ei.value.status_code == 409
    assert ei.value.detail["code"] == "MANIFEST_NOT_SWEEPABLE"
    assert "ConfigMap/payload" in ei.value.detail["detail"]


def test_ungated_findings_pass_verification():
    """pod-run-as-root is a real finding with no tier. It must not gate."""
    from api.scenarios import _verify_emitted  # noqa: PLC0415

    content = _manifest([
        _obj("Deployment", "ci-runner",
             labels={"cortexsim.io/posture-finding": "pod-run-as-root"}),
    ])
    slugs, tier = _verify_emitted("SIM-CDR-999", content, {},
                                  allow_privileged_k8s=False, authorized_by="")
    assert slugs == ["pod-run-as-root"]
    assert tier == "baseline"


# ===========================================================================
# 7 · Posture hydration — the gate must actually receive its input
# ===========================================================================

def test_disk_posture_index_reads_the_declared_block_from_yaml(tmp_path, monkeypatch):
    """If the posture never reaches the API the gate is vacuous and a privileged
    scenario silently emits a baseline manifest — the false-negative shape this
    engine exists to refuse. The `cluster_posture` ORM column is owned by
    another track, so until it lands the block is hydrated from YAML (which
    CLAUDE.md already names the source of truth)."""
    import api.scenarios as mod  # noqa: PLC0415
    from config import settings  # noqa: PLC0415

    (tmp_path / "scenarios" / "cdr").mkdir(parents=True)
    (tmp_path / "scenarios" / "cdr" / "x.yml").write_text(yaml.safe_dump({
        "scenario_id": SID, "cluster_posture": POSTURE_TIER2,
    }), encoding="utf-8")
    (tmp_path / "scenarios" / "cdr" / "plain.yml").write_text(yaml.safe_dump({
        "scenario_id": "SIM-EDR-001",
    }), encoding="utf-8")

    monkeypatch.setattr(settings, "CORTEXSIM_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "CORTEXSIM_SCENARIOS_DIR", "scenarios")
    monkeypatch.setattr(mod, "_DISK_POSTURE_INDEX", None)

    assert mod._disk_posture(SID) == POSTURE_TIER2
    assert mod._disk_posture("SIM-EDR-001") is None


def test_orm_column_wins_over_disk_when_it_exists(session_factory, monkeypatch):
    """Once the ORM carries the column the disk path must never be consulted."""
    import api.scenarios as mod  # noqa: PLC0415

    _seed(session_factory)
    monkeypatch.setattr(mod, "_disk_posture", lambda sid: POSTURE_TIER2)

    class _Row:
        def to_dict(self):
            return {"scenario_id": SID, "cluster_posture": None}

    assert mod._scenario_document(_Row())["cluster_posture"] is None


#: The scenarios in the shipped corpus whose manifest genuinely warrants asking
#: an operator. Pinned so that a content commit which makes a NEW scenario
#: privileged is a reviewed change rather than a silent one — the gate's whole
#: credibility rests on it firing rarely and for a reason.
CORPUS_GATED = {
    "SIM-CDR-003": ("node_access", ["node_access_authorized"]),
    "SIM-CDR-004": ("cluster_privilege", ["cluster_privilege_authorized"]),
    "SIM-CDR-012": ("node_access", ["node_access_authorized"]),
}


def test_only_the_scenarios_that_warrant_it_are_gated():
    """Requirement 2, measured against the real corpus.

    Of 169 scenarios, 5 declare a cluster_posture and 3 of those are gated.
    Two declare a posture and are NOT gated (SIM-CDR-001, SIM-CDR-015): an
    over-permissioned-looking block is not automatically privilege, and gating
    it would be the ceremony that teaches an operator to tick every box.
    """
    import api.scenarios as mod  # noqa: PLC0415
    from engine import k8s_manifest as km  # noqa: PLC0415

    gated = {}
    for sid, block in mod._build_disk_posture_index().items():
        p = km.parse_posture({"cluster_posture": block})
        keys = list(km.required_consent_keys(p))
        if keys:
            gated[sid] = (p.risk_tier(), keys)
    assert gated == CORPUS_GATED, (
        "the set of gated scenarios changed — confirm the new one genuinely "
        "creates a privileged cluster object, and that the console fetches it "
        "via POST /bundle"
    )


@pytest.mark.parametrize("sid", sorted(CORPUS_GATED))
def test_real_corpus_scenarios_are_gated_end_to_end(client, session_factory, sid):
    """The gate against real content, not a fixture.

    Without posture hydration these three would have downloaded a BASELINE
    manifest over a plain GET — SIM-CDR-003 is "privileged container escape",
    so a DC would have applied a manifest that cannot escape and reported "the
    attack ran and Cortex missed it".
    """
    _seed(session_factory, scenario_id=sid)
    r = client.get(f"/api/scenarios/{sid}/download?format=k8s")
    assert r.status_code == 409
    body = r.json()["detail"]
    assert body["code"] == "GATED_FORMAT_REQUIRES_POST"
    risk_tier, keys = CORPUS_GATED[sid]
    assert body["risk_tier"] == risk_tier
    assert body["missing_consent"] == keys
    assert all(m not in r.text for m in PRIVILEGED_MARKERS)


@pytest.mark.parametrize("sid", sorted(CORPUS_GATED))
def test_what_the_gate_refuses_really_is_privileged(sid):
    """Keeps the negative sweep from passing vacuously.

    A gate can 'pass' its own tests by making the feature impossible. This
    asserts the opposite: for each refused scenario the generator, handed the
    same hydrated document, DOES produce a manifest carrying gated capabilities
    — so the refusal above is preventing something real from leaving SimCore
    over an unauthenticated GET.
    """
    import api.scenarios as mod  # noqa: PLC0415
    from engine.push_generator import generate_k8s  # noqa: PLC0415

    doc = {
        "scenario_id": sid, "name": "x", "plane": "CDR",
        "cluster_posture": mod._build_disk_posture_index()[sid],
        "steps": [{"id": "step-01", "name": "s", "command": "echo hi",
                   "platforms": ["k8s"]}],
    }
    content = generate_k8s(doc)
    risk_tier, _ = CORPUS_GATED[sid]
    if risk_tier == "node_access":
        assert any(m in content for m in PRIVILEGED_MARKERS)
    else:
        assert "kind: ClusterRole" in content


@pytest.mark.parametrize("sid", ["SIM-CDR-001", "SIM-CDR-015"])
def test_real_corpus_baseline_postures_still_download_over_get(
    client, session_factory, sid,
):
    _seed(session_factory, scenario_id=sid)
    r = client.get(f"/api/scenarios/{sid}/download?format=k8s")
    assert r.status_code == 200, r.text
    assert r.headers["X-CortexSim-Bundle-Risk-Tier"] == "baseline"


def test_every_ungated_scenario_in_the_corpus_still_passes_verification():
    """The verification layer runs on EVERY k8s download, so a false positive
    in it would break working content for 162 scenarios. Sweep the corpus.

    A refusal here is a bug in the gate, not in the content: these manifests
    were downloadable before this work and must stay downloadable.
    """
    import api.scenarios as mod  # noqa: PLC0415
    from engine import k8s_manifest as km  # noqa: PLC0415
    from engine.push_generator import emittable_targets, generate_k8s  # noqa: PLC0415
    from fastapi import HTTPException  # noqa: PLC0415

    root = Path(__file__).resolve().parents[2] / "scenarios"
    checked, refused = 0, []
    for path in sorted(root.rglob("*.yml")):
        if path.name == "_schema.yml":
            continue
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue
        if not isinstance(doc, dict) or not doc.get("scenario_id") or not doc.get("steps"):
            continue
        if "posix" not in emittable_targets(doc):
            continue
        if km.required_consent_keys(km.parse_posture(doc)):
            continue          # gated — covered by the end-to-end tests above
        content = generate_k8s(doc)
        try:
            mod._verify_emitted(doc["scenario_id"], content, {},
                                allow_privileged_k8s=False, authorized_by="")
        except HTTPException as exc:
            refused.append((doc["scenario_id"], exc.detail.get("code")))
        checked += 1

    assert refused == []
    assert checked > 100, f"corpus sweep only reached {checked} scenarios"


def test_a_served_manifest_never_claims_to_be_self_contained(
    client, session_factory, posture,
):
    """A baseline posture that stages tool payloads is UNGATED and still emits a
    manifest requiring cluster→SimCore reachability. The relaxed delivery
    contract must be stated on both surfaces, not discovered when a pod
    silently no-ops in front of a customer.
    """
    _seed(session_factory)
    posture({"app_identity": {"name": "ci-runner"}, "payloads": ["linpeas.sh"]})

    get = client.get(f"/api/scenarios/{SID}/download?format=k8s")
    assert get.status_code == 200, get.text
    assert "cortexsim.io/delivery: served" in get.text
    assert get.headers["X-CortexSim-Bundle-Selfcontained"] == "false"

    post = client.post(f"/api/scenarios/{SID}/bundle", json={
        "format": "k8s", "server": "http://simcore.example:8888",
    })
    assert post.status_code == 200, post.text
    assert post.headers["X-CortexSim-Bundle-Selfcontained"] == "false"
    assert post.headers["X-CortexSim-Bundle-Delivery"] == "served"


def test_embedded_and_script_bundles_do_claim_self_containment(client, session_factory):
    _seed(session_factory)
    for r in (
        client.get(f"/api/scenarios/{SID}/download?format=k8s"),
        client.get(f"/api/scenarios/{SID}/download?format=bash"),
        client.post(f"/api/scenarios/{SID}/bundle", json={"format": "bash"}),
    ):
        assert r.status_code == 200, r.text
        assert r.headers["X-CortexSim-Bundle-Selfcontained"] == "true"


# ===========================================================================
# 8 · Audit
# ===========================================================================

def test_gated_generation_is_audited_with_consent_and_operator(
    client, session_factory, posture, kill_switch, caplog,
):
    """A DC must be able to prove what was deployed, by whom, under what."""
    _seed(session_factory)
    posture(POSTURE_TIER1)
    kill_switch(False)
    with caplog.at_level(logging.WARNING, logger="cortexsim.api.scenarios"):
        r = client.post(f"/api/scenarios/{SID}/bundle", json={
            "format": "k8s",
            "consent": {"cluster_privilege_authorized": True},
            "authorized_by": "h.reed",
            "reason": "ACME POV day 2",
        })
    assert r.status_code == 200, r.text
    line = next(m for m in caplog.messages if m.startswith("bundle_generated"))
    assert f"scenario={SID}" in line
    assert "risk_tier=cluster_privilege" in line
    assert "consent=cluster_privilege_authorized" in line
    assert "authorized_by=h.reed" in line
    assert "reason=ACME POV day 2" in line
    assert r.headers["X-CortexSim-Manifest-SHA256"] in line


def test_audit_survives_a_dead_event_bus(monkeypatch, client, session_factory):
    """A bus hiccup must never turn a generation into an error the DC sees."""
    import events  # noqa: PLC0415

    async def _boom(*a, **k):
        raise RuntimeError("bus down")

    _seed(session_factory)
    monkeypatch.setattr(events.event_bus, "publish", _boom)
    r = client.post(f"/api/scenarios/{SID}/bundle", json={"format": "k8s"})
    assert r.status_code == 200, r.text
