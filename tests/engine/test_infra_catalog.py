"""Tests for core.engine.infra_catalog."""
from __future__ import annotations

from pathlib import Path

import pytest

from engine.infra_catalog import InfraCatalog


@pytest.fixture
def catalog(fixtures_dir: Path) -> InfraCatalog:
    modules_root = fixtures_dir / "modules"
    return InfraCatalog(modules_root=modules_root)


class TestInfraCatalog:
    def test_list_modules_for_provider(self, catalog: InfraCatalog):
        modules = catalog.list_modules(provider="aws")
        names = [m.name for m in modules]
        assert "test_mod" in names

    def test_list_modules_unknown_provider_empty(self, catalog: InfraCatalog):
        assert catalog.list_modules(provider="nobody") == []

    def test_get_module_metadata(self, catalog: InfraCatalog):
        meta = catalog.get_module(provider="aws", module="test_mod")
        assert meta is not None
        assert meta.description == "Test fixture module"
        assert meta.required_params == ["project_name"]
        assert "tool-one" in meta.content_tools
        assert "tool-two" in meta.content_tools

    def test_get_unknown_module_returns_none(self, catalog: InfraCatalog):
        assert catalog.get_module(provider="aws", module="does_not_exist") is None

    def test_module_path_returns_filesystem_path(self, catalog: InfraCatalog):
        path = catalog.module_path(provider="aws", module="test_mod")
        assert path is not None
        assert path.is_dir()
        assert (path / "main.tf").is_file()

    def test_content_manifest_parsed(self, catalog: InfraCatalog):
        manifest = catalog.load_content_manifest(provider="aws", module="test_mod")
        assert manifest is not None
        assert "tools" in manifest
        assert "category_a" in manifest["tools"]
