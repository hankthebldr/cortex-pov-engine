"""`install.artifact` — the payload-shelf binding on a tool-adapter pack.

These guard the properties that make the shelf declaration trustworthy:
  1. every one of the 84 shipped packs still loads, unchanged (the back-compat
     spine — the tier-4 rule was RELAXED, never tightened),
  2. a pack cannot declare an artifact nothing could ever serve or stage,
  3. "pinned" cannot be a claim without a digest, and unpinned cannot be silent,
  4. the artifact's landing path and what `{binary}` renders can never disagree,
  5. two packs cannot claim one shelf filename with different bytes.
"""
from __future__ import annotations

import textwrap

import pytest
import yaml

from tools.adapter_loader import (
    ToolAdapterSchema,
    artifact_conflicting_ids,
    load_adapters,
    validate_artifact_urls,
)

BASE = textwrap.dedent(
    """
    adapter_id: TOOL-FIXTURE
    name: Fixture
    version: "1.0"
    tier: 4
    category: cloud-container
    upstream:
      repo: https://example.invalid/fixture
      license: MIT
      attribution: nobody
    cortex_signal:
      planes: [CDR]
    safety_class: dual-use-lab-only
    install:
      binary: /tmp/fixture.sh
      runtime_install_command: "curl -fsSLo /tmp/fixture.sh https://example.invalid/fixture.sh"
    invoke:
      target_platform: linux
      run_template: "{binary}"
      default_args: {}
      identity_required: container-runtime
    """
)

GOOD_ARTIFACT = {
    "filename": "fixture.sh",
    "url": "https://example.invalid/releases/download/v1/fixture.sh",
    "kind": "file",
    "sha256": "a" * 64,
    "pin": {"type": "release-tag", "ref": "v1"},
    "license": "MIT",
    "stage_path": "/tmp/fixture.sh",
    "mode": "0755",
}


def build(**overrides):
    """Materialise a pack dict with GOOD_ARTIFACT, then apply overrides."""
    doc = yaml.safe_load(BASE)
    doc["install"]["artifact"] = dict(GOOD_ARTIFACT)
    for path, value in overrides.items():
        cursor = doc
        parts = path.split(".")
        for part in parts[:-1]:
            cursor = cursor[part]
        if value is _DELETE:
            cursor.pop(parts[-1], None)
        else:
            cursor[parts[-1]] = value
    return doc


_DELETE = object()


def load(**overrides) -> ToolAdapterSchema:
    return ToolAdapterSchema(**build(**overrides))


def rejects(code: str, **overrides):
    with pytest.raises(Exception) as exc:
        load(**overrides)
    assert code in str(exc.value), f"expected {code} in:\n{exc.value}"


# ---------------------------------------------------------------------------
# The back-compat spine
# ---------------------------------------------------------------------------


def test_every_shipped_pack_still_loads_with_zero_rejections(repo_root):
    """The tier-4 rule went from 'requires runtime_install_command' to 'requires
    at least one of runtime_install_command / artifact'. That is a strict
    relaxation and every existing pack must satisfy it unchanged."""
    loaded = load_adapters(str(repo_root / "tools" / "packs"))
    assert loaded.rejected == 0
    assert len(loaded) >= 84


def test_the_shipped_shelf_bindings_are_pinned_and_licensed(repo_root):
    """The authored `install.artifact` blocks, held to the properties that make
    them worth having.

    Superseded `assert [a.adapter_id for a in loaded if a.staged] == []`, which
    recorded the pre-migration state ("no pack declares an artifact yet") and
    flipped on 2026-08-05 when the first eight landed. An unpinned or
    unlicensed binding is worse than none: unpinned, `build-payloads.sh` records
    whatever it downloaded, every consumer verifies against THAT and passes, and
    the DC silently ships a different tool than the one they tested.
    """
    loaded = load_adapters(str(repo_root / "tools" / "packs"))
    staged = [a for a in loaded if a.staged]
    assert staged, (
        "no pack declares an install.artifact — every tier-4 tool then installs "
        "itself from the public internet ON THE TARGET, which is the egress a "
        "customer's default-deny network blocks first"
    )

    unpinned, unlicensed, wrong_kind, homeless = [], [], [], []
    for a in staged:
        art = a.install.artifact
        if art.pin.type == "none" or not art.sha256:
            unpinned.append(a.adapter_id)
        if not (art.license or a.upstream.license) or (
            art.license or a.upstream.license
        ).lower() == "unknown":
            unlicensed.append(a.adapter_id)
        if art.kind != "file":
            wrong_kind.append(a.adapter_id)
        # TA-10 back-fill must have produced a destination for every one of them,
        # or `{binary}` renders a path the artifact never landed at.
        if not a.effective_stage_path:
            homeless.append(a.adapter_id)

    assert not unpinned, f"unpinned shelf bindings ship a different tool silently: {unpinned}"
    assert not unlicensed, (
        f"a staged artifact is redistributed to a customer host; its licence is "
        f"an audit fact, not a nicety: {unlicensed}"
    )
    assert not wrong_kind, f"only kind: file is servable today (TA-08): {wrong_kind}"
    assert not homeless, f"TA-10 left no stage_path for: {homeless}"


def test_every_staged_filename_is_claimed_by_exactly_one_pack(repo_root):
    """TA-12 on the REAL corpus. The shelf is keyed by filename, so two packs
    claiming one key means one of them can never verify."""
    loaded = load_adapters(str(repo_root / "tools" / "packs"))
    seen: dict[str, str] = {}
    for a in loaded:
        if not a.staged:
            continue
        name = a.install.artifact.filename
        assert name not in seen, (
            f"{a.adapter_id} and {seen[name]} both claim shelf filename {name!r}"
        )
        seen[name] = a.adapter_id


def test_a_wellformed_artifact_loads():
    adapter = load()
    assert adapter.staged is True
    assert adapter.effective_stage_path == "/tmp/fixture.sh"
    assert adapter.install.artifact.pinned is True


def test_tier4_pack_with_artifact_and_no_runtime_command_loads():
    """TA-02 relaxation: a pack served entirely from the shelf needs no
    target-side install command at all."""
    adapter = load(**{"install.runtime_install_command": _DELETE})
    assert adapter.staged is True


def test_tier4_pack_with_neither_is_rejected():
    """TA-02: a tier-4 pack with no way to obtain its tool at all.

    The exemption is present so TA-13 is SATISFIED and this isolates TA-02.
    Without it the pack fails both rules and the assertion would silently start
    proving TA-13 instead — a test that still passes while the rule it names
    goes unexercised.
    """
    rejects("TA-02", **{
        "install.runtime_install_command": _DELETE,
        "install.artifact": _DELETE,
        "install.artifact_exempt": {
            "reason_code": "DISTRO_PACKAGE",
            "reason": (
                "Fixture installs from the operating system's package "
                "repository, so it needs internet access on the target host."
            ),
        },
    })


def test_tier4_pack_with_no_declaration_at_all_names_ta13_first():
    """Neither an install path nor a shelf declaration. TA-13 is the more
    actionable message of the two that apply, and it must be the one raised."""
    with pytest.raises(Exception) as exc:
        load(**{
            "install.runtime_install_command": _DELETE,
            "install.artifact": _DELETE,
        })
    assert "TA-13" in str(exc.value)


# ---------------------------------------------------------------------------
# TA-01 .. TA-12
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tier,extra", [
    (2, {"install.source_path": "sources/fixture"}),
    (3, {"install.iac_module": "cdr"}),
])
def test_TA01_artifact_on_a_non_tier4_pack_is_rejected(tier, extra):
    rejects("TA-01", tier=tier, **extra)


@pytest.mark.parametrize("bad", ["../etc/passwd", "a/b.sh", "", "MANIFEST.json", "SHA256SUMS"])
def test_TA03_unservable_filenames_are_rejected(bad):
    rejects("TA-03", **{"install.artifact.filename": bad})


def test_TA03_non_http_url_is_rejected():
    rejects("TA-03", **{"install.artifact.url": "file:///etc/shadow"})


def test_TA04_a_malformed_digest_is_rejected():
    rejects("TA-04", **{"install.artifact.sha256": "deadbeef"})


def test_TA05_claiming_a_pin_without_a_digest_is_rejected():
    """'I claim this is pinned to v1' with no digest is a claim, not a pin."""
    rejects("TA-05", **{"install.artifact.sha256": _DELETE})


def test_TA06_unpinned_without_a_waiver_reason_is_rejected():
    rejects("TA-06", **{
        "install.artifact.sha256": _DELETE,
        "install.artifact.pin": {"type": "none"},
    })


def test_TA06_unpinned_with_a_waiver_reason_loads():
    adapter = load(**{
        "install.artifact.sha256": _DELETE,
        "install.artifact.pin": {"type": "none", "waiver_reason": "upstream ships no tags"},
    })
    assert adapter.install.artifact.pinned is False


def test_TA07_release_tag_without_a_ref_is_rejected():
    rejects("TA-07", **{"install.artifact.pin": {"type": "release-tag"}})


def test_TA07_unknown_pin_type_is_rejected():
    rejects("TA-07", **{"install.artifact.pin": {"type": "vibes"}})


def test_TA08_archive_is_rejected_because_no_consumer_can_unpack_one():
    rejects("TA-08", **{"install.artifact.kind": "archive"})


@pytest.mark.parametrize("bad", [
    "relative/path.sh",
    "/usr/bin/fixture",
    "/etc/cron.d/fixture",
    "/tmp/../usr/bin/fixture",
])
def test_TA09_stage_path_outside_the_allowlist_is_rejected(bad):
    """A destination is a WRITE on a host the DC does not own."""
    rejects("TA-09", **{
        "install.artifact.stage_path": bad,
        "install.binary": bad,
    })


def test_TA09_a_bad_mode_is_rejected():
    rejects("TA-09", **{"install.artifact.mode": "rwxr-xr-x"})


def test_TA10_stage_path_backfills_from_an_absolute_binary():
    adapter = load(**{"install.artifact.stage_path": _DELETE})
    assert adapter.install.artifact.stage_path == "/tmp/fixture.sh"
    assert adapter.effective_stage_path == adapter.install.binary


def test_TA10_stage_path_disagreeing_with_an_absolute_binary_is_rejected():
    """The artifact would land at one path while `{binary}` rendered another, so
    every step invoking the adapter would run a file that is not there."""
    rejects("TA-10", **{"install.artifact.stage_path": "/tmp/other.sh"})


def test_TA10_a_bare_binary_name_requires_an_explicit_stage_path():
    rejects("TA-10", **{
        "install.binary": "fixture",
        "install.artifact.stage_path": _DELETE,
    })


def test_TA11_a_moving_url_warns_and_does_not_reject(caplog):
    adapter = load(**{
        "install.artifact.url": "https://example.invalid/releases/latest/download/fixture.sh",
    })
    assert adapter.staged is True
    warnings = validate_artifact_urls([adapter])
    assert warnings and "TA-11" in warnings[0]
    assert "moving target" in warnings[0]


def test_TA12_two_packs_claiming_one_filename_with_different_bytes_conflict():
    first = load()
    second = ToolAdapterSchema(**build(adapter_id="TOOL-FIXTURE-2", **{
        "install.artifact.sha256": "b" * 64,
    }))
    assert artifact_conflicting_ids([first, second]) == {"TOOL-FIXTURE-2"}


def test_TA12_same_filename_same_digest_is_fine():
    first = load()
    second = ToolAdapterSchema(**build(adapter_id="TOOL-FIXTURE-2"))
    assert artifact_conflicting_ids([first, second]) == set()
