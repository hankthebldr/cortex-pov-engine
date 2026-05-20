"""Property tests over every shipped Tool Adapter pack.

Each test iterates every file under ``tools/packs/*.yml`` (excluding
``_schema.yml``). Goal is to catch the "I added a new adapter and broke
startup" regression class before the engine boots in front of a customer
— mirror of ``tests/engine/test_scenario_catalog.py``.

These tests are independent of the unit tests in ``test_adapter_loader.py``;
those exercise the schema with synthetic fixtures, these exercise the
real corpus.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PACKS_DIR = REPO_ROOT / "tools" / "packs"


def _pack_files() -> list[Path]:
    out: list[Path] = []
    for p in sorted(PACKS_DIR.glob("*.yml")):
        if p.name.startswith("_"):
            continue
        out.append(p)
    return out


PACK_FILES = _pack_files()


def test_repo_has_adapter_packs():
    """Belt-and-braces — empty pack dir means the test suite is meaningless."""
    assert PACK_FILES, f"no adapter packs found under {PACKS_DIR}"


@pytest.mark.parametrize("path", PACK_FILES, ids=lambda p: p.name)
def test_pack_parses_and_validates(path: Path):
    """Each pack survives Pydantic validation."""
    from tools.adapter_loader import _parse_and_validate  # noqa: PLC0415

    adapter, err = _parse_and_validate(str(path))
    assert err is None, f"{path.name} failed schema validation: {err}"
    assert adapter is not None


@pytest.mark.parametrize("path", PACK_FILES, ids=lambda p: p.name)
def test_filename_matches_adapter_id(path: Path):
    """Filename slug must match the adapter_id suffix so a contributor
    grepping for ``TOOL-MIMIKATZ`` finds ``mimikatz.yml``."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    adapter_id = raw["adapter_id"]
    expected_slug = adapter_id.removeprefix("TOOL-").lower()
    actual_slug = path.stem.lower()
    # Tolerate hyphen variations (atomic-red-team vs atomicredteam).
    assert expected_slug.replace("-", "") == actual_slug.replace("-", ""), (
        f"{path.name}: filename does not match adapter_id {adapter_id}"
    )


def test_adapter_ids_unique():
    """Two adapters with the same id would cause one to overwrite the
    other in the catalog — surface that loudly."""
    seen: dict[str, Path] = {}
    for path in PACK_FILES:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        aid = raw["adapter_id"]
        if aid in seen:
            pytest.fail(
                f"duplicate adapter_id {aid!r} in {path} and {seen[aid]}"
            )
        seen[aid] = path


@pytest.mark.parametrize("path", PACK_FILES, ids=lambda p: p.name)
def test_pack_renders_command(path: Path):
    """Every tier 1–4 adapter must produce a non-empty command line when
    its ``run_template`` is rendered with its declared ``default_args``."""
    from tools.adapter_loader import _parse_and_validate  # noqa: PLC0415

    adapter, _ = _parse_and_validate(str(path))
    assert adapter is not None
    if adapter.tier == 5:
        pytest.skip("tier 5 adapters do not render")
    rendered = adapter.invoke.run_template.format(
        binary=adapter.install.binary or "",
        **adapter.invoke.default_args,
    )
    assert rendered.strip(), f"{path.name}: empty rendered command"
    # Sanity: rendered command should mention the binary (defends against
    # a template that drops {binary}).
    if adapter.install.binary:
        assert (adapter.install.binary in rendered
                or Path(adapter.install.binary).name in rendered), (
            f"{path.name}: rendered command does not reference declared binary"
        )


@pytest.mark.parametrize("path", PACK_FILES, ids=lambda p: p.name)
def test_dangerous_adapters_carry_cleanup(path: Path):
    """Dual-use, C2, and destructive adapters all benefit from explicit
    cleanup — even when the schema only mandates it for destructive."""
    from tools.adapter_loader import _parse_and_validate  # noqa: PLC0415

    adapter, _ = _parse_and_validate(str(path))
    assert adapter is not None
    if adapter.safety_class in ("dual-use-lab-only", "c2-framework", "destructive"):
        assert adapter.cleanup is not None, (
            f"{path.name}: safety_class={adapter.safety_class} should declare cleanup"
        )
        assert adapter.cleanup.commands, (
            f"{path.name}: safety_class={adapter.safety_class} requires non-empty cleanup.commands"
        )


def test_phase_b_batch_1_covers_all_active_tiers():
    """At the end of Phase B batch 1 the catalog must demonstrate every
    tier we ship adapters for (2/3/4) and at least 3 distinct categories."""
    from tools.adapter_loader import _parse_and_validate  # noqa: PLC0415

    tiers: set[int] = set()
    categories: set[str] = set()
    safety: set[str] = set()
    for path in PACK_FILES:
        adapter, _ = _parse_and_validate(str(path))
        if adapter is None:
            continue
        tiers.add(adapter.tier)
        categories.add(adapter.category)
        safety.add(adapter.safety_class)

    assert {2, 3, 4}.issubset(tiers), f"missing tiers: {tiers}"
    assert len(categories) >= 3, f"need diverse categories, have: {categories}"
    # The C2 gate, the dual-use gate, and the safe path must all be
    # exercised by the corpus so the consent-gate unit tests reflect
    # realistic usage.
    assert {"safe", "dual-use-lab-only", "c2-framework"}.issubset(safety), (
        f"corpus does not exercise every safety_class: {safety}"
    )


@pytest.mark.parametrize("path", PACK_FILES, ids=lambda p: p.name)
def test_ttp_refs_well_formed(path: Path):
    """Every ``ttp_refs[]`` entry follows the corpus id format. Resolution
    against the actual TTP catalog happens at startup as a *warning*; this
    test just enforces shape."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    refs = raw.get("ttp_refs") or []
    for ref in refs:
        assert re.match(r"^TTP-\d{4}-\d{4}$", ref), (
            f"{path.name}: malformed ttp_ref {ref!r}"
        )


@pytest.mark.parametrize("path", PACK_FILES, ids=lambda p: p.name)
def test_equivalents_well_formed(path: Path):
    """Every ``equivalents[]`` entry follows the TOOL-* adapter_id format."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    eqs = raw.get("equivalents") or []
    for eq in eqs:
        assert re.match(r"^TOOL-[A-Z0-9-]+$", eq), (
            f"{path.name}: malformed equivalent {eq!r}"
        )
