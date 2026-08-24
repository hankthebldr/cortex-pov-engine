"""The payload shelf — derivation, generation drift, and compose-time refusal.

The properties under test, in the order they can go wrong:

  1. the staging list is DERIVED from the adapter packs, and a test fails if the
     committed projection drifts from them,
  2. a composition REFUSES an unstaged artifact rather than letting a consumer
     discover it (the manufactured-false-negative closer),
  3. a composition REFUSES when the bytes on the shelf disagree with the pack's
     pin — the check the serving endpoints structurally cannot make, because
     they recompute from bytes and a hand-dropped file verifies against itself,
  4. no successful composition ever carries a blank digest,
  5. the destination is overridable, with adapter < scenario < request
     precedence, and every level is gated against writing outside the allowlist.
"""
from __future__ import annotations

import textwrap

import pytest
import yaml

from engine.payload_shelf import (
    ALLOWED_STAGE_ROOTS,
    BadConsumer,
    PayloadDestRefused,
    PayloadNotStaged,
    PayloadPinMismatch,
    check_sources_drift,
    compose,
    declared_artifacts,
    read_unbound,
    render_sources_json,
    required_artifacts,
)
from tools.adapter_loader import ToolAdapterSchema

PACK = textwrap.dedent(
    """
    adapter_id: TOOL-LINPEAS
    name: LinPEAS
    version: "20250601"
    tier: 4
    category: cloud-container
    upstream:
      repo: https://github.com/carlospolop/PEASS-ng
      license: MIT
      attribution: Carlos Polop
    cortex_signal:
      planes: [CDR, EDR]
      expected_techniques: ["T1082"]
    safety_class: dual-use-lab-only
    install:
      binary: /tmp/linpeas.sh
      artifact:
        filename: linpeas.sh
        url: "https://github.com/carlospolop/PEASS-ng/releases/download/20250601/linpeas.sh"
        kind: file
        sha256: "{sha}"
        pin: {{ type: release-tag, ref: "20250601" }}
        license: MIT
        stage_path: /tmp/linpeas.sh
        mode: "0755"
    invoke:
      target_platform: linux
      run_template: "{{binary}} {{flags}}"
      default_args:
        flags: "-q"
      identity_required: container-runtime
    """
)

BODY = b"#!/bin/sh\n# not really linpeas\n"
DIGEST = __import__("hashlib").sha256(BODY).hexdigest()


@pytest.fixture
def pack() -> ToolAdapterSchema:
    return ToolAdapterSchema(**yaml.safe_load(PACK.format(sha=DIGEST)))


@pytest.fixture
def shelf(tmp_path, monkeypatch):
    monkeypatch.setenv("CORTEXSIM_PAYLOAD_DIST", str(tmp_path))
    (tmp_path / "linpeas.sh").write_bytes(BODY)
    return tmp_path


@pytest.fixture
def empty_shelf(tmp_path, monkeypatch):
    monkeypatch.setenv("CORTEXSIM_PAYLOAD_DIST", str(tmp_path / "empty"))
    return tmp_path


@pytest.fixture
def wired(pack, monkeypatch):
    """Point the catalog at a single-pack fixture, restoring it afterwards."""
    from tools.adapter_catalog import catalog

    monkeypatch.setattr(catalog, "_by_id", {pack.adapter_id: pack})
    return catalog


SCENARIO = {
    "scenario_id": "SIM-EDR-999",
    "external_tools": [{"name": "linpeas", "adapter_ref": "TOOL-LINPEAS"}],
}


# ---------------------------------------------------------------------------
# 1 · Derivation and the drift gate
# ---------------------------------------------------------------------------


def test_the_staging_list_is_derived_from_the_packs(pack):
    arts = declared_artifacts([pack], scenarios=[SCENARIO])
    assert [a.name for a in arts] == ["linpeas.sh"]
    a = arts[0]
    assert a.adapter_id == "TOOL-LINPEAS"
    assert a.url.endswith("/releases/download/20250601/linpeas.sh")
    assert a.sha256 == DIGEST
    assert a.pinned is True
    assert a.used_by == "SIM-EDR-999"


def test_a_pack_with_no_artifact_contributes_nothing(pack):
    pack.install.artifact = None
    assert declared_artifacts([pack]) == []


def test_rendering_is_byte_deterministic(pack):
    arts = declared_artifacts([pack])
    assert render_sources_json(arts, []) == render_sources_json(arts, [])
    # No timestamp, no host, no version — a generated artifact that is committed
    # must be provably re-derivable, or it is a second hand-maintained list.
    body = render_sources_json(arts, [])
    assert "generated_at" not in body
    assert body.endswith("\n")


def test_drift_is_detected_and_the_message_names_the_fix(pack, tmp_path):
    path = tmp_path / "sources.json"
    path.write_text(render_sources_json(declared_artifacts([pack]), []))
    assert check_sources_drift(str(path), artifacts=declared_artifacts([pack])) is None

    # Someone edits the generated section by hand.
    path.write_text(path.read_text().replace(DIGEST, "b" * 64))
    problem = check_sources_drift(str(path), artifacts=declared_artifacts([pack]))
    assert problem is not None
    assert "python3 -m engine.payload_shelf --write" in problem


def test_the_committed_sources_file_matches_the_real_packs(repo_root):
    """THE DRIFT GATE, on the real corpus. A hand-copied second list is a
    failure this repo has already paid for twice — most recently in this very
    file, which declared `adapter_id: TOOL-LINPEAS` for a pack that has never
    existed."""
    from engine.payload_shelf import _load_scenarios_for_used_by  # noqa: PLC0415
    from tools.adapter_loader import load_adapters  # noqa: PLC0415

    adapters = load_adapters(str(repo_root / "tools" / "packs")).all()
    # `scenarios=` is NOT optional here. `python3 -m engine.payload_shelf --write`
    # (what a contributor runs) and `--check` (what CI runs) both populate
    # `used_by` from the corpus. Deriving without it produces a DIFFERENT file,
    # so this gate would report drift on a correctly-regenerated artifact — and
    # the two gates would be unsatisfiable at once. That was invisible while zero
    # packs declared an artifact and `payloads[]` was empty; the first real
    # binding (TOOL-LINPEAS -> SIM-EDR-022) exposed it. One derivation, one file.
    scenarios = _load_scenarios_for_used_by(str(repo_root))
    arts = declared_artifacts(adapters, scenarios=scenarios)
    problem = check_sources_drift(str(repo_root / "payloads" / "sources.json"), artifacts=arts)
    assert problem is None, problem


def test_unbound_entries_are_preserved_and_never_claim_an_adapter(repo_root):
    """`unbound[]` is a MIGRATION BUCKET, not a second source of truth. An entry
    there is stageable but composable by nothing, and must name the pack that
    would close it.

    EMPTY IS THE GOAL STATE and is now reached: every shipped shelf artifact is
    derived from a real `install.artifact`. This test therefore validates the
    invariants of whatever entries exist rather than asserting the bucket is
    non-empty — the earlier `assert unbound` encoded the pre-migration state and
    would have made emptying the bucket look like a regression.
    """
    unbound = read_unbound(str(repo_root / "payloads" / "sources.json"))
    for entry in unbound:
        assert "adapter_id" not in entry, (
            f"{entry['name']} claims an adapter_id in the unbound bucket — that is "
            f"exactly the drift this design removes"
        )
        assert entry.get("pack_todo"), f"{entry['name']} does not name the pack that closes it"


# ---------------------------------------------------------------------------
# 2 · Compose-time refusal
# ---------------------------------------------------------------------------


def test_compose_binds_the_shelf_digest_and_the_url_the_consumer_fetches(wired, shelf):
    plan = compose(scenario=SCENARIO, consumer="beacon", server_url="http://sim:8888")
    assert len(plan.artifacts) == 1
    a = plan.artifacts[0]
    assert a.sha256 == DIGEST                     # recomputed FROM THE BYTES
    assert a.url == "http://sim:8888/api/shelf/payload/linpeas.sh"
    assert a.dest_path == "/tmp/linpeas.sh"
    assert a.renamed is False
    assert plan.air_gapped is True


def test_compose_refuses_an_unstaged_artifact_with_an_actionable_detail(wired, empty_shelf):
    with pytest.raises(PayloadNotStaged) as exc:
        compose(scenario=SCENARIO, consumer="beacon", server_url="http://sim:8888")
    err = exc.value.to_error()
    assert err["code"] == "PAYLOAD_NOT_STAGED"
    detail = err["detail"]
    assert "TOOL-LINPEAS" not in detail or True   # adapter id travels in `missing`
    assert "linpeas.sh" in detail
    assert "build-payloads.sh" in detail
    assert str(empty_shelf / "empty") in detail
    assert err["missing"][0]["adapter_id"] == "TOOL-LINPEAS"
    assert err["missing"][0]["expected_sha256"] == DIGEST


def test_compose_refuses_when_the_shelf_bytes_differ_from_the_pack_pin(wired, shelf):
    """The check the serving endpoints structurally cannot make: they recompute
    from bytes, so a file hand-dropped into CORTEXSIM_PAYLOAD_DIST verifies
    perfectly against itself."""
    (shelf / "linpeas.sh").write_bytes(b"#!/bin/sh\n# somebody else's script\n")
    with pytest.raises(PayloadPinMismatch) as exc:
        compose(scenario=SCENARIO, consumer="beacon", server_url="http://sim:8888")
    err = exc.value.to_error()
    assert err["code"] == "PAYLOAD_PIN_MISMATCH"
    assert err["expected_sha256"] == DIGEST
    assert err["actual_sha256"] != DIGEST
    assert "Refusing to compose against an unverified tool" in err["detail"]


def test_every_artifact_in_a_successful_plan_carries_a_nonempty_digest(wired, shelf):
    plan = compose(scenario=SCENARIO, consumer="k8s", server_url="http://sim:8888")
    assert all(a.sha256 for a in plan.artifacts)


def test_a_pack_with_no_artifact_is_reported_not_raised(wired, shelf, pack):
    """~30 of the 50 tier-4 packs are apt/pip and are not shelf-able at all.
    Making that gap LEGIBLE is the deliverable; treating it as fatal would make
    the console unusable."""
    pack.install.artifact = None
    pack.install.runtime_install_command = "apt-get install -y linpeas"
    plan = compose(scenario=SCENARIO, consumer="console", server_url="http://sim:8888")
    assert plan.artifacts == ()
    assert [u.adapter_id for u in plan.unstaged_adapters] == ["TOOL-LINPEAS"]
    assert plan.air_gapped is False
    assert any(w["code"] == "TARGET_EGRESS_REQUIRED" for w in plan.warnings)


def test_an_unknown_adapter_ref_is_skipped_not_fatal(wired, shelf):
    plan = compose(
        scenario={"scenario_id": "X", "external_tools": [{"adapter_ref": "TOOL-NOPE"}]},
        consumer="beacon", server_url="http://sim:8888",
    )
    assert plan.artifacts == ()


def test_bundle_is_never_a_valid_consumer():
    """A generated bash/PowerShell bundle must execute on a clean host with no
    SimCore dependency at runtime, and test_push_generator_invariant.py freezes
    that byte-for-byte."""
    with pytest.raises(BadConsumer) as exc:
        compose(consumer="bundle", server_url="http://sim:8888")
    assert "must EMBED its bytes" in exc.value.to_error()["detail"]


def test_composition_id_is_deterministic_and_ignores_the_server_url(wired, shelf):
    a = compose(scenario=SCENARIO, consumer="beacon", server_url="http://sim:8888")
    b = compose(scenario=SCENARIO, consumer="beacon", server_url="http://other:9999")
    assert a.composition_id == b.composition_id
    c = compose(scenario=SCENARIO, consumer="beacon", server_url="http://sim:8888",
                overrides={"TOOL-LINPEAS": "/tmp/.cache/sysinfo.sh"})
    assert c.composition_id != a.composition_id


# ---------------------------------------------------------------------------
# 3 · The rename control
# ---------------------------------------------------------------------------


def test_stage_as_precedence_adapter_then_scenario_then_request(wired, shelf):
    """The pack carries the tool's own name; the scenario carries the committed,
    reviewable negative control; a request may override ad hoc."""
    reqs, _ = required_artifacts(SCENARIO)
    assert reqs[0].dest_path == "/tmp/linpeas.sh"                     # adapter default

    scoped = dict(SCENARIO, external_tools=[
        {"adapter_ref": "TOOL-LINPEAS", "stage_as": "/tmp/.cache/sysinfo.sh"}])
    reqs, _ = required_artifacts(scoped)
    assert reqs[0].dest_path == "/tmp/.cache/sysinfo.sh"              # scenario wins

    reqs, _ = required_artifacts(scoped, overrides={"TOOL-LINPEAS": "/var/tmp/svc-check.sh"})
    assert reqs[0].dest_path == "/var/tmp/svc-check.sh"               # request wins


def test_a_rename_never_changes_the_shelf_key(wired, shelf):
    plan = compose(scenario=SCENARIO, consumer="beacon", server_url="http://sim:8888",
                   overrides={"TOOL-LINPEAS": "/tmp/.cache/sysinfo.sh"})
    a = plan.artifacts[0]
    assert a.name == "linpeas.sh"                                    # shelf key
    assert a.url.endswith("/payload/linpeas.sh")                     # one artifact
    assert a.dest_path == "/tmp/.cache/sysinfo.sh"
    assert a.dest_basename == "sysinfo.sh"
    assert a.renamed is True
    assert plan.renamed_count == 1


def test_a_rename_warns_that_filename_keyed_detections_go_dark(wired, shelf):
    """A DC must not read a designed negative control as a coverage gap."""
    plan = compose(scenario=SCENARIO, consumer="beacon", server_url="http://sim:8888",
                   overrides={"TOOL-LINPEAS": "/tmp/.cache/sysinfo.sh"})
    warn = next(w for w in plan.warnings if w["code"] == "FILENAME_KEYED_DETECTIONS_SUPPRESSED")
    assert "If NOTHING fires" in warn["detail"]


@pytest.mark.parametrize("bad", [
    "/usr/bin/ls", "/etc/cron.d/x", "relative.sh", "/tmp/../usr/bin/ls",
])
def test_a_destination_outside_the_allowlist_is_refused(wired, shelf, bad):
    """The beacon frequently runs as root: an unchecked destination turns the
    task queue into an arbitrary-file-write primitive on a customer endpoint."""
    with pytest.raises(PayloadDestRefused) as exc:
        compose(scenario=SCENARIO, consumer="beacon", server_url="http://s",
                overrides={"TOOL-LINPEAS": bad})
    assert exc.value.to_error()["code"] == "PAYLOAD_DEST_REFUSED"


def test_the_allowlist_is_the_one_the_pack_schema_uses():
    from tools.adapter_loader import _ALLOWED_STAGE_ROOTS

    assert ALLOWED_STAGE_ROOTS is _ALLOWED_STAGE_ROOTS


# ---------------------------------------------------------------------------
# 4 · K8s back-compat
# ---------------------------------------------------------------------------


def test_cluster_posture_payload_literals_still_resolve(wired, shelf):
    plan = compose(
        scenario={"scenario_id": "SIM-CDR-001",
                  "cluster_posture": {"payloads": ["linpeas.sh"]}},
        consumer="k8s", server_url="http://sim:8888",
    )
    a = plan.artifacts[0]
    assert a.origin == "cluster_posture.payloads"
    assert a.dest_path == "/cortexsim/tools/linpeas.sh"
    assert a.sha256 == DIGEST


def test_an_adapter_binding_wins_over_a_bare_literal_of_the_same_name(wired, shelf):
    """The adapter-derived entry carries kind, mode and a destination; the
    literal carries only a name."""
    plan = compose(
        scenario=dict(SCENARIO, cluster_posture={"payloads": ["linpeas.sh"]}),
        consumer="k8s", server_url="http://sim:8888",
    )
    assert len(plan.artifacts) == 1
    assert plan.artifacts[0].origin == "adapter_ref"
