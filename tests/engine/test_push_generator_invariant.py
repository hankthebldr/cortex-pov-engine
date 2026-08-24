"""The cardinal push-bundle invariant guard.

``generate_bash`` and ``generate_powershell`` carry the no-SimCore-at-runtime
contract, and 169 scenarios depend on their exact output. This module holds the
two guards that make a regression there impossible to land silently:

1. **Byte-identity across the whole corpus.** ``_golden/push_bundle_digests.json``
   was captured BEFORE the K8s delivery work; every scenario's bash and
   PowerShell bundle must still hash to the recorded value. A regenerated
   golden file is a deliberate act with a visible diff, not an accident.

2. **No SimCore reference.** The K8s mode may relax self-containment (opt-in,
   see ``docs/reference/k8s-delivery.md``). The bash and PowerShell bundles may
   not, ever. If a future refactor "unifies" the three generators, this fails.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
import yaml

from engine.push_generator import generate_bash, generate_powershell, resolve_target

GOLDEN = Path(__file__).parent / "_golden" / "push_bundle_digests.json"


@pytest.fixture(autouse=True)
def _adapter_catalog_loaded(repo_root: Path):
    """Pin the adapter catalog to its production state.

    ``generate_bash`` emits a tool-adapter install section resolved from the
    ``tools.adapter_catalog`` module SINGLETON. An unloaded catalog silently
    emits "Adapter X not in catalog — install skipped" instead of the real
    install lines, so the golden digests depend on whether some earlier test in
    the session happened to load it. Loading here makes this guard
    order-independent — and the golden is captured in the same state
    ``core/main.py`` boots into.
    """
    from tools.adapter_catalog import catalog  # noqa: PLC0415

    catalog.load(str(repo_root / "tools" / "packs"))
    yield


def _corpus(repo_root: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for p in sorted((repo_root / "scenarios").rglob("*.yml")):
        if p.name.startswith("_"):
            continue
        try:
            data = yaml.safe_load(p.read_text())
        except Exception:  # noqa: BLE001 - non-scenario YAML in the tree
            continue
        if not isinstance(data, dict) or "scenario_id" not in data or "steps" not in data:
            continue
        out[data["scenario_id"]] = data
    return out


def test_golden_file_covers_the_corpus(repo_root: Path) -> None:
    """The golden file and the corpus must describe the same scenario set.

    A scenario added without regenerating the golden would otherwise slip
    through unguarded, which is how a "the tests pass" regression happens.
    """
    golden = json.loads(GOLDEN.read_text())
    corpus = _corpus(repo_root)
    assert set(golden) == set(corpus), (
        "golden/corpus scenario-id mismatch — regenerate "
        f"{GOLDEN.name}: only-in-golden={sorted(set(golden) - set(corpus))} "
        f"only-in-corpus={sorted(set(corpus) - set(golden))}"
    )


def test_bash_and_powershell_bundles_are_byte_identical(repo_root: Path) -> None:
    """Every bundle for every scenario hashes to its pre-change value."""
    golden = json.loads(GOLDEN.read_text())
    corpus = _corpus(repo_root)

    drift: list[str] = []
    checked = 0
    for sid, data in sorted(corpus.items()):
        want = golden[sid]
        for fmt, fn, target in (
            ("bash", generate_bash, "posix"),
            ("powershell", generate_powershell, "windows"),
        ):
            emittable = resolve_target(data, target).emittable
            if not emittable:
                if want[fmt] is not None:
                    drift.append(f"{sid}/{fmt}: was emittable, now is not")
                continue
            if want[fmt] is None:
                drift.append(f"{sid}/{fmt}: was NOT emittable, now is")
                continue
            got = hashlib.sha256(fn(data).encode("utf-8")).hexdigest()
            if got != want[fmt]:
                drift.append(f"{sid}/{fmt}: {want[fmt][:12]} -> {got[:12]}")
            checked += 1

    assert not drift, (
        "generate_bash / generate_powershell output CHANGED. These bundles carry "
        "the no-SimCore-at-runtime invariant that 169 scenarios depend on.\n  "
        + "\n  ".join(drift)
    )
    # Sanity: the guard is actually exercising the corpus, not silently empty.
    assert checked >= 160, f"only {checked} bundles compared — the guard is not covering the corpus"


_SIMCORE_MARKERS = (
    "/api/k8s/",
    "CORTEXSIM_SERVER",
    "cortexsim.io/delivery",
    "bootstrap.sh",
)


@pytest.mark.parametrize("fmt", ["bash", "powershell"])
def test_bundles_never_reference_simcore(repo_root: Path, fmt: str) -> None:
    """A self-contained bundle names no SimCore endpoint and no k8s payload path."""
    fn, target = (
        (generate_bash, "posix") if fmt == "bash" else (generate_powershell, "windows")
    )
    hits: list[str] = []
    for sid, data in sorted(_corpus(repo_root).items()):
        if not resolve_target(data, target).emittable:
            continue
        text = fn(data)
        for marker in _SIMCORE_MARKERS:
            if marker in text:
                hits.append(f"{sid}: {marker}")
    assert not hits, (
        f"the {fmt} bundle referenced SimCore — it must run on a clean host with "
        "no SimCore dependency at runtime:\n  " + "\n  ".join(hits)
    )


def test_k8s_work_did_not_edit_the_invariant_generators(repo_root: Path) -> None:
    """Structural guard: the K8s section is appended, never interleaved.

    ``generate_k8s`` and its helpers live below the K8s banner comment. If a
    future edit moves K8s code above it, or moves bash/PowerShell code below,
    the file layout that keeps the invariant reviewable has been lost.
    """
    src = (repo_root / "core" / "engine" / "push_generator.py").read_text()
    banner = "# K8s manifest generator (target=posix, delivered into a cluster)"
    assert banner in src, "the K8s section banner is missing"
    above, below = src.split(banner, 1)
    assert "def generate_bash" in above
    assert "def generate_powershell" in above
    assert "def generate_k8s" in below
    assert "def generate_bash" not in below
    assert "def generate_powershell" not in below
    # k8s_manifest is imported lazily inside generate_k8s so importing
    # push_generator never drags the posture model in.
    assert not re.search(r"^from engine\.k8s_manifest import", above, re.M)
