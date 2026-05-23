"""Tests for core.engine.infra_generator.InfraGenerator."""
from __future__ import annotations

import shutil
import tarfile
from pathlib import Path

import pytest

from engine.infra_catalog import InfraCatalog
from engine.infra_generator import InfraGenerator, GenerationError
from engine.infra_models import InfraGenerateParams, InfraGenerateRequest


@pytest.fixture
def real_catalog(repo_root: Path) -> InfraCatalog:
    return InfraCatalog(modules_root=repo_root / "infra" / "modules")


@pytest.fixture
def templates_dir(repo_root: Path) -> Path:
    return repo_root / "infra" / "templates"


@pytest.fixture
def blueprints_dir(tmp_path: Path) -> Path:
    d = tmp_path / "blueprints"
    d.mkdir()
    return d


@pytest.fixture
def generator(real_catalog: InfraCatalog, templates_dir: Path,
              blueprints_dir: Path) -> InfraGenerator:
    return InfraGenerator(
        catalog=real_catalog,
        templates_dir=templates_dir,
        blueprints_dir=blueprints_dir,
    )


def _request(modules: list[str], adapter_refs: list[str] | None = None) -> InfraGenerateRequest:
    return InfraGenerateRequest(
        provider="aws",
        region="us-east-1",
        modules=modules,
        adapter_refs=adapter_refs or [],
        params=InfraGenerateParams(project_name="test-pov",
                                   dc_ssh_cidr="203.0.113.0/32"),
    )


@pytest.fixture(scope="module", autouse=True)
def _load_adapter_catalog():
    """Populate the adapter-catalog singleton once for the module so the
    auto-pull tests resolve TOOL-MIMIKATZ / TOOL-RUBEUS / etc."""
    from tools.adapter_catalog import catalog  # noqa: PLC0415
    repo = Path(__file__).resolve().parent.parent.parent
    catalog.load(str(repo / "tools" / "packs"))
    assert catalog.count() > 0


class TestInfraGenerator:
    def test_base_module_always_included(self, generator: InfraGenerator):
        bundle = generator.generate(_request(["edr"]))
        assert "base" in bundle.modules
        assert "edr" in bundle.modules

    def test_generates_root_tf_files(self, generator: InfraGenerator,
                                     blueprints_dir: Path):
        bundle = generator.generate(_request(["edr"]))
        bundle_dir = blueprints_dir / bundle.bundle_id
        assert (bundle_dir / "main.tf").is_file()
        assert (bundle_dir / "variables.tf").is_file()
        assert (bundle_dir / "outputs.tf").is_file()
        assert (bundle_dir / "terraform.tfvars").is_file()
        assert (bundle_dir / "README.md").is_file()

    def test_copies_selected_modules(self, generator: InfraGenerator,
                                     blueprints_dir: Path):
        bundle = generator.generate(_request(["edr", "cdr"]))
        bundle_dir = blueprints_dir / bundle.bundle_id
        assert (bundle_dir / "modules" / "base" / "main.tf").is_file()
        assert (bundle_dir / "modules" / "edr" / "main.tf").is_file()
        assert (bundle_dir / "modules" / "cdr" / "main.tf").is_file()

    def test_omits_unselected_modules(self, generator: InfraGenerator,
                                      blueprints_dir: Path):
        bundle = generator.generate(_request(["edr"]))
        bundle_dir = blueprints_dir / bundle.bundle_id
        assert not (bundle_dir / "modules" / "cdr").exists()

    def test_generates_tar_archive(self, generator: InfraGenerator,
                                   blueprints_dir: Path):
        bundle = generator.generate(_request(["edr"]))
        archive = blueprints_dir / f"{bundle.bundle_id}.tar.gz"
        assert archive.is_file()
        with tarfile.open(archive, "r:gz") as tar:
            names = tar.getnames()
        assert any(n.endswith("main.tf") for n in names)
        assert any("modules/base" in n for n in names)

    def test_unknown_module_raises(self, generator: InfraGenerator):
        # pydantic blocks at request time; generator also validates module exists on disk
        with pytest.raises(GenerationError):
            req = _request(["edr"])
            # Manually sneak in an invalid module (bypasses pydantic) via direct generate call
            req.modules.append("nonexistent")
            generator.generate(req)

    def test_list_bundles(self, generator: InfraGenerator):
        b1 = generator.generate(_request(["edr"]))
        b2 = generator.generate(_request(["cdr"]))
        summaries = generator.list_bundles()
        ids = [s.bundle_id for s in summaries]
        assert b1.bundle_id in ids
        assert b2.bundle_id in ids

    def test_archive_path(self, generator: InfraGenerator):
        bundle = generator.generate(_request(["edr"]))
        p = generator.archive_path(bundle.bundle_id)
        assert p is not None
        assert p.is_file()

    def test_archive_path_unknown(self, generator: InfraGenerator):
        assert generator.archive_path("does-not-exist") is None

    def test_bundle_does_not_contain_terraform_state(self, generator: InfraGenerator,
                                                    blueprints_dir: Path):
        """Regression guard: generated bundle must exclude .terraform/ artifacts
        so that modules remain portable."""
        bundle = generator.generate(_request(["edr"]))
        bundle_dir = blueprints_dir / bundle.bundle_id
        # Walk all files in the bundle
        for p in bundle_dir.rglob("*"):
            assert ".terraform" not in p.parts, f"unexpected .terraform artifact: {p}"


class TestAdapterAutoPull:
    """Auto-include IaC modules required by adapter_refs[].

    Tier-3 adapters declare install.iac_module — e.g. TOOL-MIMIKATZ → edr,
    TOOL-RUBEUS → itdr, TOOL-BLOODHOUND → itdr. A scenario that references
    those adapters should not need the operator to also tick the module
    boxes manually; the generator unions adapter-derived modules into the
    bundle's module list.
    """

    def test_adapter_ref_pulls_in_required_module(self, generator: InfraGenerator):
        bundle = generator.generate(_request(["base"], adapter_refs=["TOOL-RUBEUS"]))
        # itdr is rubeus's iac_module — was not in the request's modules
        # but the generator pulled it in.
        assert "itdr" in bundle.modules
        assert "itdr" in bundle.auto_included_modules

    def test_multiple_adapters_collapse_to_same_module(self, generator: InfraGenerator):
        # rubeus + bloodhound both bind to itdr — exactly one inclusion.
        bundle = generator.generate(
            _request(["base"], adapter_refs=["TOOL-RUBEUS", "TOOL-BLOODHOUND"])
        )
        assert bundle.modules.count("itdr") == 1
        assert bundle.auto_included_modules == ["itdr"]

    def test_adapters_span_multiple_modules(self, generator: InfraGenerator):
        # mimikatz → edr, rubeus → itdr — both included, order preserved
        bundle = generator.generate(
            _request(["base"], adapter_refs=["TOOL-MIMIKATZ", "TOOL-RUBEUS"])
        )
        assert "edr" in bundle.modules
        assert "itdr" in bundle.modules
        assert bundle.auto_included_modules == ["edr", "itdr"]

    def test_module_already_picked_not_double_listed(self, generator: InfraGenerator):
        # Operator picked itdr explicitly + referenced rubeus → still one inclusion,
        # and auto_included is EMPTY because the operator chose it.
        bundle = generator.generate(_request(["itdr"], adapter_refs=["TOOL-RUBEUS"]))
        assert bundle.modules.count("itdr") == 1
        assert "itdr" not in bundle.auto_included_modules

    def test_unresolved_adapter_ref_does_not_crash(self, generator: InfraGenerator):
        # A stale adapter_ref must NEVER fail the bundle — the operator gets
        # a row in ADAPTERS.md telling them what was unresolved.
        bundle = generator.generate(
            _request(["edr"], adapter_refs=["TOOL-DOES-NOT-EXIST"])
        )
        assert "edr" in bundle.modules
        assert bundle.auto_included_modules == []

    def test_adapter_with_no_iac_module_does_not_pull(self, generator: InfraGenerator):
        # tier-4 nmap has no iac_module — adapter_refs should silently skip it.
        bundle = generator.generate(
            _request(["base"], adapter_refs=["TOOL-NMAP"])
        )
        # No auto-includes because nmap is runtime-fetched
        assert bundle.auto_included_modules == []

    def test_adapters_md_emitted_when_adapter_refs_present(
        self, generator: InfraGenerator, blueprints_dir: Path,
    ):
        bundle = generator.generate(
            _request(["base"], adapter_refs=[
                "TOOL-MIMIKATZ", "TOOL-NMAP", "TOOL-DOES-NOT-EXIST",
            ])
        )
        adapters_md = blueprints_dir / bundle.bundle_id / "ADAPTERS.md"
        assert adapters_md.is_file()
        body = adapters_md.read_text(encoding="utf-8")
        # Every binding state surfaces:
        assert "TOOL-MIMIKATZ" in body and "resolved" in body and "`edr`" in body
        assert "TOOL-NMAP" in body and "no-iac" in body
        assert "TOOL-DOES-NOT-EXIST" in body and "unresolved" in body

    def test_adapters_md_omitted_when_no_adapter_refs(
        self, generator: InfraGenerator, blueprints_dir: Path,
    ):
        """Legacy path: no adapter_refs[] → no ADAPTERS.md, no noise."""
        bundle = generator.generate(_request(["edr"]))
        adapters_md = blueprints_dir / bundle.bundle_id / "ADAPTERS.md"
        assert not adapters_md.exists()

    def test_adapter_module_lands_in_archive(
        self, generator: InfraGenerator, blueprints_dir: Path,
    ):
        """The auto-pulled module must be IN the tar.gz, not just listed."""
        bundle = generator.generate(_request(["base"], adapter_refs=["TOOL-RUBEUS"]))
        archive = blueprints_dir / f"{bundle.bundle_id}.tar.gz"
        with tarfile.open(archive, "r:gz") as tar:
            names = tar.getnames()
        assert any("modules/itdr" in n for n in names)
        assert any(n.endswith("ADAPTERS.md") for n in names)
