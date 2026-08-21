"""TA-13..TA-17 — every tier-4 pack must SAY whether the shelf serves it.

WHY THIS FILE EXISTS
--------------------
Before ``install.artifact_exempt``, all 48 artifact-less tier-4 packs produced
ONE byte-identical sentence in ``compose().unstaged_adapters[]``. It read the
same for ``hydra`` (apt — never shelvable), ``nuclei`` (staging the binary
would not even remove the egress dependency) and ``gobuster`` (shelvable the
day an extractor exists). A DC could not tell a **decision** from an
**omission**, which is the console-facing form of "green while proving
nothing".

The guard is a REJECT, not a WARN, for the reason 48 packs accumulated an
identical non-explanation in the first place: a boot warning scrolls past in a
log nobody reads. Structural, and NOT gated by ``CORTEXSIM_STRICT_REFS`` —
same posture as A-17/A-18 for assertions.

Every test here fails against the pre-change loader.
"""
from __future__ import annotations

import os
import textwrap

import pytest
import yaml

from tools.adapter_loader import (
    _REVISITABLE_REASON_CODES,
    _VALID_EXEMPT_REASON_CODES,
    ToolAdapterSchema,
    load_adapters,
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
      binary: fixture
      runtime_install_command: "apt-get install -y fixture"
    invoke:
      target_platform: linux
      run_template: "{binary}"
      default_args: {}
      identity_required: container-runtime
    """
)

GOOD_EXEMPT = {
    "reason_code": "DISTRO_PACKAGE",
    "reason": (
        "Fixture installs from the operating system's package repository, so "
        "this tool needs internet access on the target host."
    ),
}

GOOD_ARTIFACT = {
    "filename": "fixture.sh",
    "url": "https://example.invalid/releases/download/v1/fixture.sh",
    "kind": "file",
    "sha256": "a" * 64,
    "pin": {"type": "release-tag", "ref": "v1"},
    "stage_path": "/tmp/fixture.sh",
    "mode": "0755",
}


def build(**overrides):
    doc = yaml.safe_load(BASE)
    doc["install"]["artifact_exempt"] = dict(GOOD_EXEMPT)
    for path, value in overrides.items():
        cursor = doc
        parts = path.split(".")
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
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
# The real tree — a fixture-only test passes while the shipped tree is broken
# ---------------------------------------------------------------------------


def test_every_tier4_pack_in_the_real_tree_declares_artifact_or_exemption(repo_root):
    """Walks tools/packs/ itself, deliberately — NOT a fixture.

    This repo has already shipped the fixture-vs-reality failure once: the prod
    image did not ``COPY`` the UC/TC snapshot, so strict validation degraded to
    a no-op that every test still passed.
    """
    loaded = load_adapters(str(repo_root / "tools" / "packs"))
    assert loaded.rejected == 0, "a shipped pack no longer loads"

    tier4 = [a for a in loaded.all() if a.tier == 4]
    assert tier4, "no tier-4 packs found — the walk resolved the wrong directory"

    undeclared = [
        a.adapter_id for a in tier4
        if a.install.artifact is None and a.install.artifact_exempt is None
    ]
    assert undeclared == [], (
        f"tier-4 packs with neither install.artifact nor install.artifact_exempt: "
        f"{undeclared}. Each reports as 'needs target egress' with the same "
        f"generic sentence as every other unstaged tool, so a DC cannot tell a "
        f"deliberate decision from an omission."
    )

    both = [
        a.adapter_id for a in tier4
        if a.install.artifact is not None and a.install.artifact_exempt is not None
    ]
    assert both == [], f"packs declaring BOTH artifact and exemption: {both}"


def test_the_real_tree_has_no_exemption_on_a_non_tier4_pack(repo_root):
    loaded = load_adapters(str(repo_root / "tools" / "packs"))
    stray = [
        a.adapter_id for a in loaded.all()
        if a.tier != 4 and a.install.artifact_exempt is not None
    ]
    assert stray == [], f"TA-15: artifact_exempt on non-tier-4 packs: {stray}"


def test_every_shipped_exemption_reason_is_an_explanation_not_a_label(repo_root):
    """The reason is rendered VERBATIM to a DC standing in front of a customer.

    Guards the properties the schema cannot: that the sentence names a
    consequence rather than restating the install command.
    """
    loaded = load_adapters(str(repo_root / "tools" / "packs"))
    for adapter in loaded.all():
        ex = adapter.install.artifact_exempt
        if ex is None:
            continue
        assert ex.reason_code in _VALID_EXEMPT_REASON_CODES
        assert len(ex.reason) >= 40, adapter.adapter_id
        # Second person, and it must tell the operator what it means for them.
        assert "you" in ex.reason.lower() or "target host" in ex.reason.lower(), (
            f"{adapter.adapter_id}: reason does not tell the DC what this means "
            f"for their POV — {ex.reason!r}"
        )


def test_the_backlog_is_a_queryable_number_not_prose(repo_root):
    """Only the codes describing a CortexSim limitation carry a `revisit`.

    That is what keeps "someone should extract this archive" distinguishable
    from "apt, forever" — the whole point of the reason-code vocabulary.
    """
    loaded = load_adapters(str(repo_root / "tools" / "packs"))
    backlog, settled = [], []
    for adapter in loaded.all():
        ex = adapter.install.artifact_exempt
        if ex is None:
            continue
        (backlog if ex.reason_code in _REVISITABLE_REASON_CODES else settled).append(
            (adapter.adapter_id, ex)
        )

    for adapter_id, ex in backlog:
        assert (ex.revisit or "").strip(), f"TA-17: {adapter_id} has no revisit"
        assert len(ex.revisit) >= 40, adapter_id

    # A settled verdict with a revisit line is a contradiction: it says both
    # "this will never be shelvable" and "here is how to shelve it".
    for adapter_id, ex in settled:
        assert not (ex.revisit or "").strip(), (
            f"{adapter_id} is {ex.reason_code} (settled) but carries a revisit "
            f"line — pick the code that matches the intent"
        )

    assert backlog, "the backlog cannot be empty while archives remain unstageable"


# ---------------------------------------------------------------------------
# TA-13 / TA-14 / TA-15 — the declaration itself
# ---------------------------------------------------------------------------


def test_ta13_tier4_with_neither_artifact_nor_exemption_is_rejected():
    rejects("TA-13", **{"install.artifact_exempt": _DELETE})


def test_ta13_names_the_vocabulary_so_the_author_can_act_on_it():
    with pytest.raises(Exception) as exc:
        load(**{"install.artifact_exempt": _DELETE})
    message = str(exc.value)
    assert "DISTRO_PACKAGE" in message, "the rejection must name a valid code"
    assert "install.artifact_exempt" in message


def test_ta14_declaring_both_is_rejected():
    rejects("TA-14", **{"install.artifact": dict(GOOD_ARTIFACT)})


def test_ta15_exemption_on_a_non_tier4_pack_is_rejected():
    rejects(
        "TA-15",
        tier=2,
        **{
            "install.source_path": "sources/fixture",
            "install.runtime_install_command": _DELETE,
        },
    )


def test_a_shelf_backed_tier4_pack_needs_no_exemption():
    """The positive case: declaring an artifact satisfies TA-13 on its own."""
    adapter = load(
        **{
            "install.artifact_exempt": _DELETE,
            "install.artifact": dict(GOOD_ARTIFACT),
            "install.binary": "/tmp/fixture.sh",
        }
    )
    assert adapter.staged is True
    assert adapter.artifact_exempt_reason_code is None


def test_a_non_tier4_pack_needs_neither():
    """TA-13 is tier-4 only — tier 1/2/3/5 have no dispatch-time fetch."""
    adapter = load(
        tier=2,
        **{
            "install.artifact_exempt": _DELETE,
            "install.source_path": "sources/fixture",
            "install.runtime_install_command": _DELETE,
        },
    )
    assert adapter.tier == 2
    assert adapter.artifact_exempt_reason_code is None


# ---------------------------------------------------------------------------
# TA-16 / TA-17 — the reason has to mean something
# ---------------------------------------------------------------------------


def test_ta16_unknown_reason_code_is_rejected():
    rejects("TA-16", **{"install.artifact_exempt.reason_code": "BECAUSE_I_SAID_SO"})


@pytest.mark.parametrize("placeholder", ["TODO", "tbd", "N/A", "unknown", "none", "???"])
def test_ta16_placeholder_prose_is_rejected(placeholder):
    """An exemption records that a human judged this tool unshelvable and why.
    'TODO' records that nobody looked, which is the state this field removes."""
    rejects("TA-16", **{"install.artifact_exempt.reason": placeholder + " later"})


def test_ta16_a_reason_short_enough_to_be_a_label_is_rejected():
    rejects("TA-16", **{"install.artifact_exempt.reason": "Installs from apt."})


def test_ta16_empty_reason_is_rejected():
    rejects("TA-16", **{"install.artifact_exempt.reason": "   "})


def test_ta17_a_revisitable_code_without_a_next_action_is_rejected():
    rejects(
        "TA-17",
        **{"install.artifact_exempt.reason_code": "ARCHIVE_ONLY_NO_EXTRACTOR"},
    )


def test_ta17_a_revisitable_code_with_a_next_action_loads():
    adapter = load(
        **{
            "install.artifact_exempt.reason_code": "ARCHIVE_ONLY_NO_EXTRACTOR",
            "install.artifact_exempt.revisit": (
                "Requires a single-member extractor on the DC's SimCore so "
                "compose() still emits kind: file."
            ),
        }
    )
    assert adapter.artifact_exempt_reason_code == "ARCHIVE_ONLY_NO_EXTRACTOR"


def test_settled_codes_do_not_require_a_revisit():
    """apt has no next action. Forcing one would manufacture a fake backlog."""
    adapter = load()
    assert adapter.install.artifact_exempt.revisit is None


def test_reason_is_whitespace_normalised_so_folded_yaml_round_trips():
    """Packs author these as YAML folded scalars (`>-`), which arrive with
    newlines. The console renders the string raw."""
    adapter = load(
        **{"install.artifact_exempt.reason": "Hydra installs from\nthe OS package repository on the target host."}
    )
    assert "\n" not in adapter.install.artifact_exempt.reason


# ---------------------------------------------------------------------------
# Upstream liveness — the rot no other gate can see
# ---------------------------------------------------------------------------


def _pinned_download_urls(repo_root) -> list[tuple[str, str]]:
    """Every immutable-looking upstream URL a pack would fetch from."""
    import glob
    import re

    out: list[tuple[str, str]] = []
    for path in sorted(glob.glob(str(repo_root / "tools" / "packs" / "*.yml"))):
        if path.endswith("_schema.yml"):
            continue
        text = open(path, encoding="utf-8").read()
        for url in re.findall(r"https://[^\s\"'|)]+", text):
            url = url.rstrip(".,)")
            if "/releases/download/" in url:
                out.append((os.path.basename(path), url))
    return sorted(set(out))


def test_the_pack_tree_still_has_pinned_release_urls_to_check(repo_root):
    """A liveness sweep that silently checks nothing is worse than none."""
    assert len(_pinned_download_urls(repo_root)) >= 8


@pytest.mark.skipif(
    os.environ.get("CORTEXSIM_CHECK_UPSTREAM_URLS") != "1",
    reason="network probe — opt in with CORTEXSIM_CHECK_UPSTREAM_URLS=1",
)
def test_every_pinned_release_url_still_resolves(repo_root):
    """Upstreams PRUNE releases, and nothing else in CI would ever notice.

    Measured 2026-08-06: ``trivy.yml``'s runtime_install_command 404s (Aqua
    pruned the v0.55.x line) and ``bloodhound.yml``'s content-library
    download_url 404s (BloodHound moved to a v9 line that publishes no release
    assets at all). Both are dead on every target, egress or not, and both
    produce the manufactured false negative the shelf exists to prevent: the
    step runs with no tool, detects nothing, and the absent detection reads in
    a POV report as "Cortex missed it".

    Opt-in rather than always-on because a network failure in a unit-test job
    is indistinguishable from upstream rot, and a flaky gate gets ignored —
    which is how the rot survives. Run it on a schedule, not per-PR.
    """
    import urllib.error  # noqa: PLC0415 — only needed on the opt-in path
    import urllib.request  # noqa: PLC0415

    dead: list[str] = []
    for pack, url in _pinned_download_urls(repo_root):
        request = urllib.request.Request(url, method="HEAD")
        request.add_header("User-Agent", "cortexsim-pack-liveness")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if response.status >= 400:
                    dead.append(f"{pack}: HTTP {response.status} {url}")
        except urllib.error.HTTPError as exc:
            dead.append(f"{pack}: HTTP {exc.code} {url}")
        except Exception as exc:  # noqa: BLE001 — report, never mask
            dead.append(f"{pack}: {type(exc).__name__} {url}")

    assert dead == [], "upstream pruned a pinned release:\n  " + "\n  ".join(dead)
