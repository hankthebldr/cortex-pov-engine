"""The K8s manifest's binding to the payload shelf.

Two properties, both of which have a specific failure this repo has already
paid for at least once in another form:

1. **`_resolve_payloads` DELEGATES to `payload_shelf.compose`.** Two
   implementations of an integrity check drift, and the one that drifts is the
   one nobody ran that quarter. A monkeypatch proves the call actually happens,
   so re-forking the lookup fails CI instead of passing quietly.

2. **A `delivery=served` manifest is REFUSED while the shelf demands a bearer
   token.** `_SERVED_FETCH` is a bare `wget` with no Authorization header. Under
   `CORTEXSIM_K8S_PAYLOAD_AUTH=token` the manifest applies cleanly, pulls its
   image, and 403s in the init container — a pod that never produced a
   detection, which is indistinguishable in a POV report from a detection miss.

The delegation is deliberately NARROW: only the literal
`cluster_posture.payloads` names go through, never the scenario's
`external_tools[].adapter_ref` set. Widening it would move an adapter-bound
artifact's destination from `/cortexsim/tools/<name>` (what the in-pod shim
resolves against) to the pack's `stage_path` (`/tmp/<name>`), breaking the one
delivery path that has been verified on a live kind cluster. `test_..._narrow`
below pins that.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from engine import k8s_manifest as K


def _scenario(**posture):
    """A minimal served-delivery scenario declaring shelf payloads."""
    return {
        "scenario_id": "SIM-TEST-001",
        "plane": "CDR",
        "steps": [{"id": "step-01", "name": "noop", "command": "true",
                   "identity": "container-runtime"}],
        "external_tools": [
            # Present ON PURPOSE: TOOL-DEEPCE declares an install.artifact, so a
            # widened delegation would pull it in and relocate it.
            {"name": "deepce", "type": "script", "adapter_ref": "TOOL-DEEPCE"},
        ],
        "cluster_posture": {"workload": "job", "payloads": ["deepce.sh"], **posture},
    }


def _build(scenario, delivery="served"):
    return K.build_objects(
        scenario,
        posture=K.parse_posture(scenario),
        delivery=delivery,
        simcore_url="http://simcore.lab:8888",
        now=datetime(2026, 8, 5, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# 1 · delegation
# ---------------------------------------------------------------------------


def test_resolve_payloads_delegates_to_the_shared_resolver(monkeypatch) -> None:
    """No second shelf lookup. The call must reach `payload_shelf.compose`."""
    import engine.payload_shelf as shelf

    seen: dict = {}

    class _Art:
        name, sha256 = "deepce.sh", "a" * 64

    class _Comp:
        artifacts = (_Art(),)

    def _fake_compose(**kwargs):
        seen.update(kwargs)
        return _Comp()

    monkeypatch.setattr(shelf, "compose", _fake_compose)

    out = K._resolve_payloads(["deepce.sh"], scenario_id="SIM-TEST-001")

    assert seen, (
        "_resolve_payloads did not call payload_shelf.compose — the K8s path has "
        "re-forked the integrity check. There must be exactly one resolver."
    )
    assert seen["consumer"] == "k8s"
    assert out == [{"name": "deepce.sh", "sha256": "a" * 64}]


def test_delegation_passes_only_cluster_posture_literals_not_adapter_refs(
    monkeypatch,
) -> None:
    """NARROW on purpose — see the module docstring.

    An adapter-derived artifact lands at the pack's `stage_path` (`/tmp/...`),
    but the in-pod shim resolves against `/cortexsim/tools/`. Widening the input
    silently moves the destination and breaks the kind-verified path.
    """
    import engine.payload_shelf as shelf

    seen: dict = {}

    class _Art:
        name, sha256 = "deepce.sh", "b" * 64

    class _Comp:
        artifacts = (_Art(),)

    monkeypatch.setattr(
        shelf, "compose", lambda **kw: (seen.update(kw), _Comp())[1]
    )

    K._resolve_payloads(["deepce.sh"], scenario_id="SIM-TEST-001")

    passed = seen["scenario"]
    assert passed["cluster_posture"]["payloads"] == ["deepce.sh"]
    assert not passed.get("external_tools"), (
        "external_tools was forwarded to the resolver: an adapter-bound artifact "
        "would be relocated from /cortexsim/tools/ to the pack's stage_path and "
        "the in-pod shelf shim would stop resolving the scenario's verbatim curl."
    )


def test_resolver_refusal_surfaces_as_payload_not_staged(monkeypatch) -> None:
    """The resolver's structured refusal keeps this module's exception type.

    Every `except PayloadNotStaged` site imports it from `k8s_manifest`; the
    delegation must not change what callers catch, only who decides.
    """
    import engine.payload_shelf as shelf

    def _boom(**_kw):
        raise shelf.PayloadNotStaged(
            "PAYLOAD_NOT_STAGED", "Declared tool artifact not staged",
            "SIM-TEST-001 needs ['ghost.sh'] but the shelf does not carry them. "
            "Fix: run ./scripts/build-payloads.sh",
        )

    monkeypatch.setattr(shelf, "compose", _boom)

    with pytest.raises(K.PayloadNotStaged) as exc:
        K._resolve_payloads(["ghost.sh"], scenario_id="SIM-TEST-001")

    detail = str(exc.value)
    assert "ghost.sh" in detail
    assert "build-payloads.sh" in detail, (
        "the refusal lost its remediation line — a 'not staged' error a DC "
        "cannot act on sends them to read source instead of running one script"
    )


def test_declared_order_is_preserved_not_the_resolver_sort_order(
    monkeypatch,
) -> None:
    """integrity.env is indexed PAYLOAD_<i>_NAME, so declaration order wins.

    `compose` sorts by filename for its own determinism. If that ordering leaked
    into the manifest, a scenario's declared order would silently change and any
    consumer keying on the index would read the wrong digest.
    """
    import engine.payload_shelf as shelf

    class _A:
        def __init__(self, n, s):
            self.name, self.sha256 = n, s

    class _Comp:
        # Resolver order: alphabetical.
        artifacts = (_A("aaa.sh", "1" * 64), _A("zzz.sh", "2" * 64))

    monkeypatch.setattr(shelf, "compose", lambda **kw: _Comp())

    out = K._resolve_payloads(["zzz.sh", "aaa.sh"], scenario_id="SIM-TEST-001")
    assert [e["name"] for e in out] == ["zzz.sh", "aaa.sh"]
    assert out[0]["sha256"] == "2" * 64


def test_empty_payload_list_never_touches_the_resolver(monkeypatch) -> None:
    """The overwhelmingly common case must not import or call the shelf."""
    import engine.payload_shelf as shelf

    def _explode(**_kw):  # pragma: no cover - must not run
        raise AssertionError("compose called for an empty payload list")

    monkeypatch.setattr(shelf, "compose", _explode)
    assert K._resolve_payloads([]) == []


# ---------------------------------------------------------------------------
# 2 · the served + token-auth refusal
# ---------------------------------------------------------------------------


def test_served_delivery_is_refused_when_the_shelf_requires_a_token(
    monkeypatch,
) -> None:
    """Would apply cleanly and 403 in the pod. Refuse at generation instead."""
    monkeypatch.setenv("CORTEXSIM_K8S_PAYLOAD_AUTH", "token")
    monkeypatch.setenv("CORTEXSIM_K8S_PAYLOAD_TOKENS", "s3cret")

    with pytest.raises(K.ShelfAuthUnreachableFromCluster) as exc:
        _build(_scenario(), delivery="served")

    detail = str(exc.value)
    assert "SIM-TEST-001" in detail
    assert "CORTEXSIM_K8S_PAYLOAD_AUTH" in detail
    assert "delivery=embedded" in detail, (
        "the refusal must name the way OUT, not only the problem — a DC in front "
        "of a customer needs the next command, not a diagnosis"
    )
    assert "403" in detail


def test_embedded_delivery_is_unaffected_by_shelf_token_auth(monkeypatch) -> None:
    """Embedded carries its payload inline; it never calls SimCore."""
    monkeypatch.setenv("CORTEXSIM_K8S_PAYLOAD_AUTH", "token")
    monkeypatch.setenv("CORTEXSIM_K8S_PAYLOAD_TOKENS", "s3cret")

    scenario = _scenario()
    scenario["cluster_posture"].pop("payloads")  # embedded cannot carry shelf tools
    objects = _build(scenario, delivery="embedded")
    assert any(o["kind"] == "ConfigMap" for o in objects)


def test_served_delivery_is_allowed_when_the_shelf_is_open(monkeypatch) -> None:
    """The guard is narrow: open mode is the default and must not be blocked."""
    monkeypatch.delenv("CORTEXSIM_K8S_PAYLOAD_AUTH", raising=False)

    class _Art:
        name, sha256 = "deepce.sh", "c" * 64

    class _Comp:
        artifacts = (_Art(),)

    import engine.payload_shelf as shelf

    monkeypatch.setattr(shelf, "compose", lambda **kw: _Comp())

    objects = _build(_scenario(), delivery="served")
    integrity = next(
        o for o in objects
        if o["kind"] == "ConfigMap" and o["metadata"]["name"].endswith("-integrity")
    )
    env = integrity["data"]["integrity.env"]
    assert "PAYLOAD_0_NAME=deepce.sh" in env
    assert "PAYLOAD_0_SHA256=" + "c" * 64 in env


def test_the_served_fetch_shim_still_names_the_k8s_prefix() -> None:
    """`/api/k8s/*` is mounted forever — manifests in the field hard-code it.

    The shelf gained an `/api/shelf/*` prefix served by the SAME handlers. If a
    future cleanup 'unifies' the prefixes by moving rather than adding, every
    manifest already in a customer's GitOps repo stops fetching. A redirect
    would be worse: the drift would never be discovered.
    """
    assert "/api/k8s/payloads" in K._SERVED_FETCH
    assert "/api/k8s/payload/$PN" in K._SERVED_FETCH
    assert "/api/shelf/" not in K._SERVED_FETCH
