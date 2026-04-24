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


def _request(modules: list[str]) -> InfraGenerateRequest:
    return InfraGenerateRequest(
        provider="aws",
        region="us-east-1",
        modules=modules,
        params=InfraGenerateParams(project_name="test-pov",
                                   dc_ssh_cidr="203.0.113.0/32"),
    )


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
