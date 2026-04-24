"""
Module catalog loader for the IaC generator.

Reads module metadata from YAML frontmatter in each module's README.md and
flattens the content.yml tool list. Pure filesystem reads — no network,
no DB. Used by the generator and the /api/infra/modules endpoint.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Optional

import yaml

from engine.infra_models import InfraModuleMetadata

logger = logging.getLogger("cortexsim.infra_catalog")

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


class InfraCatalog:
    """
    Walks `infra/modules/{provider}/{module}/` and exposes metadata for each.

    Expected layout per module:
        README.md       with YAML frontmatter (name, description, providers, etc.)
        content.yml     optional, declares installable tools
        main.tf, variables.tf, outputs.tf  (may be absent for content-only modules)
    """

    def __init__(self, modules_root: Path) -> None:
        self._root = Path(modules_root)

    def list_modules(self, provider: str) -> list[InfraModuleMetadata]:
        provider_dir = self._root / provider
        if not provider_dir.is_dir():
            return []
        results: list[InfraModuleMetadata] = []
        for child in sorted(provider_dir.iterdir()):
            if not child.is_dir():
                continue
            meta = self._load_module_metadata(provider, child.name)
            if meta is not None:
                results.append(meta)
        return results

    def get_module(self, provider: str, module: str) -> Optional[InfraModuleMetadata]:
        return self._load_module_metadata(provider, module)

    def module_path(self, provider: str, module: str) -> Optional[Path]:
        p = self._root / provider / module
        return p if p.is_dir() else None

    def load_content_manifest(self, provider: str, module: str) -> Optional[dict[str, Any]]:
        p = self._root / provider / module / "content.yml"
        if not p.is_file():
            return None
        try:
            with p.open("r", encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {}
        except yaml.YAMLError:
            logger.exception("failed to parse content.yml for %s/%s", provider, module)
            return None

    # ------------------------------------------------------------------

    def _load_module_metadata(self, provider: str, module: str) -> Optional[InfraModuleMetadata]:
        readme = self._root / provider / module / "README.md"
        if not readme.is_file():
            return None

        text = readme.read_text(encoding="utf-8")
        match = _FRONTMATTER_RE.match(text)
        if not match:
            logger.warning("module %s/%s has README.md without frontmatter", provider, module)
            return None

        try:
            fm = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            logger.exception("invalid frontmatter YAML in %s/%s README.md", provider, module)
            return None

        content_tools = self._flatten_content_tools(provider, module)

        return InfraModuleMetadata(
            name=fm.get("name", module),
            description=fm.get("description", ""),
            providers=fm.get("providers", [provider]),
            required_params=fm.get("required_params", []),
            optional_params=fm.get("optional_params", []),
            dependencies=fm.get("dependencies", []),
            content_tools=content_tools,
        )

    def _flatten_content_tools(self, provider: str, module: str) -> list[str]:
        manifest = self.load_content_manifest(provider, module)
        if not manifest:
            return []
        tools = manifest.get("tools", {}) or {}
        out: list[str] = []
        for _category, entries in tools.items():
            for entry in entries or []:
                name = entry.get("name")
                if name:
                    out.append(name)
        return out
