"""Tests for the Phase A Tool Adapter loader, catalog, and orchestrator wiring.

Covers:

* Schema validation: tier/category/safety-class enums, tier-specific install
  requirements, destructive-requires-cleanup, run_template/default_args
  consistency, licence-required.
* Catalog: load, find, all, list_for_plane, list_for_category, requires_consent.
* Reference adapter: tools/packs/nmap.yml loads cleanly.
* Orchestrator consent gate: c2-framework + dual-use-lab-only refused
  without consent; safe adapters always pass.
* Orchestrator placeholder substitution: ``{adapter:TOOL-NMAP}`` in a step
  ``command:`` expands to the adapter's ``run_template``.
"""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PACKS_DIR = REPO_ROOT / "tools" / "packs"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _minimal_tier4(**overrides) -> dict:
    """Smallest valid tier-4 adapter dict; tests override fields to break it."""
    base = {
        "adapter_id": "TOOL-FIXTURE",
        "name": "Fixture Tool",
        "version": "1.0.0",
        "tier": 4,
        "category": "network-scan",
        "upstream": {
            "repo": "https://example.com/x",
            "license": "MIT",
            "attribution": "Test",
        },
        "cortex_signal": {"planes": ["NDR"], "expected_techniques": ["T1046"]},
        "safety_class": "safe",
        "install": {
            "runtime_install_command": "true",
            "binary": "true",
        },
        "invoke": {
            "target_platform": "linux",
            "run_template": "{binary}",
            "default_args": {},
            "identity_required": "root",
        },
    }
    base.update(overrides)
    return base


@pytest.fixture
def loaded_catalog():
    """Catalog loaded from the real tools/packs/ dir (currently just nmap)."""
    from tools.adapter_catalog import AdapterCatalog  # noqa: PLC0415

    cat = AdapterCatalog()
    cat.load(str(PACKS_DIR))
    return cat


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


class TestSchemaValidation:
    def test_minimal_valid(self):
        from tools.adapter_loader import ToolAdapterSchema  # noqa: PLC0415

        adapter = ToolAdapterSchema(**_minimal_tier4())
        assert adapter.adapter_id == "TOOL-FIXTURE"

    def test_adapter_id_format_rejected(self):
        from pydantic import ValidationError
        from tools.adapter_loader import ToolAdapterSchema  # noqa: PLC0415

        with pytest.raises(ValidationError, match="adapter_id must match"):
            ToolAdapterSchema(**_minimal_tier4(adapter_id="bad_id"))

    def test_tier_5_forbids_invoke(self):
        from pydantic import ValidationError
        from tools.adapter_loader import ToolAdapterSchema  # noqa: PLC0415

        d = _minimal_tier4(tier=5, install={})  # tier 5 also needs no install
        with pytest.raises(ValidationError, match="tier 5"):
            ToolAdapterSchema(**d)

    def test_tier_5_allows_no_invoke(self):
        from tools.adapter_loader import ToolAdapterSchema  # noqa: PLC0415

        d = _minimal_tier4(tier=5, install={})
        d.pop("invoke")
        adapter = ToolAdapterSchema(**d)
        assert adapter.tier == 5 and adapter.invoke is None

    def test_tier_3_requires_iac_module(self):
        from pydantic import ValidationError
        from tools.adapter_loader import ToolAdapterSchema  # noqa: PLC0415

        d = _minimal_tier4(tier=3)
        # tier 4 install block has no iac_module; tier 3 requires it.
        with pytest.raises(ValidationError, match="install.iac_module"):
            ToolAdapterSchema(**d)

    def test_destructive_requires_cleanup(self):
        from pydantic import ValidationError
        from tools.adapter_loader import ToolAdapterSchema  # noqa: PLC0415

        d = _minimal_tier4(safety_class="destructive")
        with pytest.raises(ValidationError, match="cleanup"):
            ToolAdapterSchema(**d)

    def test_destructive_with_cleanup_passes(self):
        from tools.adapter_loader import ToolAdapterSchema  # noqa: PLC0415

        d = _minimal_tier4(
            safety_class="destructive",
            cleanup={"commands": ["rm -f /tmp/foo"]},
        )
        adapter = ToolAdapterSchema(**d)
        assert adapter.cleanup.commands == ["rm -f /tmp/foo"]

    def test_orphan_default_arg_rejected(self):
        """A default_args key not referenced by run_template never substitutes
        and is almost certainly a typo — reject at load time."""
        from pydantic import ValidationError
        from tools.adapter_loader import ToolAdapterSchema  # noqa: PLC0415

        d = _minimal_tier4()
        d["invoke"]["default_args"] = {"unused_flag": "--x"}
        with pytest.raises(ValidationError, match="orphaned default"):
            ToolAdapterSchema(**d)

    def test_unknown_plane_rejected(self):
        from pydantic import ValidationError
        from tools.adapter_loader import ToolAdapterSchema  # noqa: PLC0415

        d = _minimal_tier4()
        d["cortex_signal"]["planes"] = ["INVALID"]
        with pytest.raises(ValidationError, match="unknown plane"):
            ToolAdapterSchema(**d)

    def test_license_unknown_rejected(self):
        from pydantic import ValidationError
        from tools.adapter_loader import ToolAdapterSchema  # noqa: PLC0415

        d = _minimal_tier4()
        d["upstream"]["license"] = "unknown"
        with pytest.raises(ValidationError, match="license"):
            ToolAdapterSchema(**d)


# ---------------------------------------------------------------------------
# Catalog behaviour
# ---------------------------------------------------------------------------


class TestCatalog:
    def test_load_reference_adapter(self, loaded_catalog):
        nmap = loaded_catalog.find("TOOL-NMAP")
        assert nmap is not None
        assert nmap.tier == 4
        assert nmap.safety_class == "safe"

    def test_find_missing_returns_none(self, loaded_catalog):
        assert loaded_catalog.find("TOOL-DOES-NOT-EXIST") is None
        assert loaded_catalog.find(None) is None

    def test_list_for_plane(self, loaded_catalog):
        ndr = loaded_catalog.list_for_plane("NDR")
        assert any(a.adapter_id == "TOOL-NMAP" for a in ndr)
        # Unused plane returns empty list, not None.
        assert loaded_catalog.list_for_plane("BROWSER") == []

    def test_load_missing_dir(self, tmp_path):
        from tools.adapter_catalog import AdapterCatalog  # noqa: PLC0415

        cat = AdapterCatalog()
        assert cat.load(str(tmp_path / "nope")) == 0
        assert cat.all() == []

    def test_duplicate_adapter_id_rejected(self, tmp_path):
        from tools.adapter_catalog import AdapterCatalog  # noqa: PLC0415

        (tmp_path / "a.yml").write_text(yaml.dump(_minimal_tier4()))
        (tmp_path / "b.yml").write_text(yaml.dump(_minimal_tier4()))  # same id
        cat = AdapterCatalog()
        n = cat.load(str(tmp_path))
        assert n == 1  # one loaded, the duplicate rejected

    def test_requires_consent_classification(self, tmp_path):
        from tools.adapter_catalog import AdapterCatalog  # noqa: PLC0415

        c2 = _minimal_tier4(
            adapter_id="TOOL-C2",
            safety_class="c2-framework",
            category="c2-framework",
        )
        dual = _minimal_tier4(
            adapter_id="TOOL-DUAL",
            safety_class="dual-use-lab-only",
            category="identity-credential",
        )
        safe = _minimal_tier4(adapter_id="TOOL-SAFE")
        (tmp_path / "c2.yml").write_text(yaml.dump(c2))
        (tmp_path / "dual.yml").write_text(yaml.dump(dual))
        (tmp_path / "safe.yml").write_text(yaml.dump(safe))

        cat = AdapterCatalog()
        cat.load(str(tmp_path))
        assert cat.requires_consent("TOOL-C2") == "c2-framework"
        assert cat.requires_consent("TOOL-DUAL") == "dual-use-lab-only"
        assert cat.requires_consent("TOOL-SAFE") is None
        assert cat.requires_consent("TOOL-MISSING") is None


# ---------------------------------------------------------------------------
# Orchestrator wiring
# ---------------------------------------------------------------------------


class TestOrchestratorWiring:
    """Direct unit-test of the placeholder substitution + consent gate
    helpers. Avoids spinning up the full async DB harness — those code paths
    have their own integration test in tests/api/.
    """

    def _stub_scenario(self, tools_list):
        class _Scn:
            external_tools = tools_list
        return _Scn()

    def test_consent_gate_passes_for_safe_adapter(self, loaded_catalog):
        from engine.orchestrator import _check_adapter_consent  # noqa: PLC0415

        # nmap is safety_class=safe — no consent required.
        scn = self._stub_scenario([{"adapter_ref": "TOOL-NMAP", "name": "nmap"}])
        assert _check_adapter_consent(scn, {}) is None

    def test_consent_gate_refuses_c2_without_consent(self, tmp_path):
        from engine.orchestrator import _check_adapter_consent  # noqa: PLC0415
        from tools.adapter_catalog import catalog  # noqa: PLC0415

        c2 = _minimal_tier4(
            adapter_id="TOOL-C2-FIXTURE",
            safety_class="c2-framework",
            category="c2-framework",
        )
        (tmp_path / "c2.yml").write_text(yaml.dump(c2))
        catalog.load(str(tmp_path))

        scn = self._stub_scenario([{"adapter_ref": "TOOL-C2-FIXTURE", "name": "fixture"}])
        err = _check_adapter_consent(scn, {})
        assert err is not None and "c2_authorized" in err

    def test_consent_gate_accepts_c2_with_consent(self, tmp_path):
        from engine.orchestrator import _check_adapter_consent  # noqa: PLC0415
        from tools.adapter_catalog import catalog  # noqa: PLC0415

        c2 = _minimal_tier4(
            adapter_id="TOOL-C2-FIXTURE2",
            safety_class="c2-framework",
            category="c2-framework",
        )
        (tmp_path / "c2.yml").write_text(yaml.dump(c2))
        catalog.load(str(tmp_path))

        scn = self._stub_scenario([{"adapter_ref": "TOOL-C2-FIXTURE2", "name": "fixture"}])
        assert _check_adapter_consent(scn, {"c2_authorized": True}) is None

    def test_consent_gate_refuses_dual_use_without_consent(self, tmp_path):
        from engine.orchestrator import _check_adapter_consent  # noqa: PLC0415
        from tools.adapter_catalog import catalog  # noqa: PLC0415

        dual = _minimal_tier4(
            adapter_id="TOOL-DUAL-FIXTURE",
            safety_class="dual-use-lab-only",
            category="identity-credential",
        )
        (tmp_path / "dual.yml").write_text(yaml.dump(dual))
        catalog.load(str(tmp_path))

        scn = self._stub_scenario([{"adapter_ref": "TOOL-DUAL-FIXTURE", "name": "x"}])
        err = _check_adapter_consent(scn, {})
        assert err is not None and "simulation_authorized" in err

    def test_placeholder_substitution(self, loaded_catalog):
        """``{adapter:TOOL-NMAP}`` in a step command expands at dispatch."""
        from engine.orchestrator import _resolve_adapter_placeholders  # noqa: PLC0415
        from tools.adapter_catalog import catalog  # noqa: PLC0415

        catalog.load(str(PACKS_DIR))  # ensure the module singleton matches
        steps = [
            {"id": "step-01", "command": "{adapter:TOOL-NMAP}"},
            {"id": "step-02", "command": "echo unchanged"},
        ]
        out = _resolve_adapter_placeholders(steps)
        assert out[0]["command"].startswith("nmap ")
        assert "-sS" in out[0]["command"]
        assert out[1]["command"] == "echo unchanged"

    def test_placeholder_unresolved_preserved(self, loaded_catalog):
        """A miss leaves the placeholder text so the failure is visible
        in the agent's output rather than collapsing to an empty command."""
        from engine.orchestrator import _resolve_adapter_placeholders  # noqa: PLC0415
        from tools.adapter_catalog import catalog  # noqa: PLC0415

        catalog.load(str(PACKS_DIR))
        steps = [{"id": "step-01", "command": "{adapter:TOOL-NOT-REAL}"}]
        out = _resolve_adapter_placeholders(steps)
        assert out[0]["command"] == "{adapter:TOOL-NOT-REAL}"

    def test_substitution_does_not_mutate_input(self, loaded_catalog):
        """Scenarios are loaded once at boot and shared across runs — the
        substitution must return a NEW list of step dicts."""
        from engine.orchestrator import _resolve_adapter_placeholders  # noqa: PLC0415
        from tools.adapter_catalog import catalog  # noqa: PLC0415

        catalog.load(str(PACKS_DIR))
        original = [{"id": "step-01", "command": "{adapter:TOOL-NMAP}"}]
        out = _resolve_adapter_placeholders(original)
        assert original[0]["command"] == "{adapter:TOOL-NMAP}"
        assert out[0]["command"] != original[0]["command"]


# ---------------------------------------------------------------------------
# Reference adapter — proves the schema accepts a real-world definition
# ---------------------------------------------------------------------------


def test_nmap_reference_adapter_loads():
    from tools.adapter_loader import _parse_and_validate  # noqa: PLC0415

    adapter, err = _parse_and_validate(str(PACKS_DIR / "nmap.yml"))
    assert err is None, f"nmap reference adapter failed validation: {err}"
    assert adapter is not None
    assert adapter.tier == 4
    assert adapter.cortex_signal.planes == ["NDR"]
    assert adapter.safety_class == "safe"
    # Sanity-check the rendered command shape.
    rendered = adapter.invoke.run_template.format(
        binary=adapter.install.binary or "nmap",
        **adapter.invoke.default_args,
    )
    assert rendered.startswith("nmap ")
    assert "-sS" in rendered
