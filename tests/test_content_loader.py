"""Tests for core.content_loader."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from content_loader import merge_installed_tools
from tools.registry import STATIC_TOOL_REGISTRY, TOOL_REGISTRY, reset_to_static


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_to_static()
    yield
    reset_to_static()


def _write_manifest(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"tools": entries}), encoding="utf-8")


class TestMergeInstalledTools:
    def test_no_manifest_is_no_op(self, tmp_path: Path):
        missing = tmp_path / "installed.json"
        count = merge_installed_tools(manifest_path=missing)
        assert count == 0
        # TOOL_REGISTRY still contains only static entries
        for name in STATIC_TOOL_REGISTRY:
            assert name in TOOL_REGISTRY
        assert len(TOOL_REGISTRY) == len(STATIC_TOOL_REGISTRY)

    def test_adds_new_entries(self, tmp_path: Path):
        manifest = tmp_path / "installed.json"
        _write_manifest(manifest, [
            {"name": "atomic-red-team",
             "install_path": "/opt/cortexsim/content/edr/atomic-red-team",
             "type": "content",
             "plane": ["edr"],
             "description": "Atomic TTP library"},
        ])
        count = merge_installed_tools(manifest_path=manifest)
        assert count == 1
        assert "atomic-red-team" in TOOL_REGISTRY
        entry = TOOL_REGISTRY["atomic-red-team"]
        assert entry["type"] == "content"
        assert entry["plane"] == ["edr"]

    def test_does_not_override_static_entries(self, tmp_path: Path):
        manifest = tmp_path / "installed.json"
        _write_manifest(manifest, [
            {"name": "signalbench",
             "install_path": "/opt/cortexsim/content/fake/signalbench",
             "type": "content",
             "plane": ["edr"],
             "description": "should not win"},
        ])
        count = merge_installed_tools(manifest_path=manifest)
        # Static entry wins — merger must never overwrite
        assert TOOL_REGISTRY["signalbench"]["type"] == "binary"
        assert "should not win" not in TOOL_REGISTRY["signalbench"]["description"]

    def test_malformed_manifest_is_logged_and_skipped(self, tmp_path: Path):
        manifest = tmp_path / "installed.json"
        manifest.write_text("{not valid json", encoding="utf-8")
        count = merge_installed_tools(manifest_path=manifest)
        assert count == 0
        # Static entries untouched
        assert len(TOOL_REGISTRY) == len(STATIC_TOOL_REGISTRY)

    def test_missing_required_fields_skipped(self, tmp_path: Path):
        manifest = tmp_path / "installed.json"
        _write_manifest(manifest, [
            {"install_path": "/x"},  # no name
            {"name": "ok",
             "install_path": "/y",
             "type": "content",
             "plane": ["cdr"],
             "description": "valid"},
        ])
        count = merge_installed_tools(manifest_path=manifest)
        assert count == 1
        assert "ok" in TOOL_REGISTRY
