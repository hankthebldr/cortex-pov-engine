"""
Core IaC bundle generator.

Responsibilities
----------------
1. Enforce invariants (base always present, module must exist for provider,
   dependencies satisfied).
2. Render Jinja2 root-bundle templates (main.tf, variables.tf, outputs.tf,
   terraform.tfvars, README.md) with request parameters.
3. Copy selected module directories into the bundle (excluding local terraform
   init artifacts like .terraform/ and lock files).
4. tar.gz the bundle for download.
"""
from __future__ import annotations

import logging
import shutil
import tarfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from engine.infra_catalog import InfraCatalog
from engine.infra_models import (
    InfraBundleSummary,
    InfraGenerateRequest,
    InfraGenerateResponse,
)
from tools.adapter_catalog import catalog as adapter_catalog

logger = logging.getLogger("cortexsim.infra_generator")

REQUIRED_TEMPLATES = [
    "main.tf.j2",
    "variables.tf.j2",
    "outputs.tf.j2",
    "terraform.tfvars.j2",
    "README.md.j2",
]

# Module-directory entries never copied into a generated bundle.
_COPY_IGNORE_NAMES = {
    ".terraform",
    ".terraform.lock.hcl",
    "terraform.tfstate",
    "terraform.tfstate.backup",
    ".DS_Store",
    "__pycache__",
}


def _copy_ignore(_src: str, names: list[str]) -> set[str]:
    """shutil.copytree ignore callable — skips local terraform artifacts."""
    return {n for n in names if n in _COPY_IGNORE_NAMES}


class GenerationError(Exception):
    """Raised when a bundle cannot be generated (bad input or IO error)."""


class InfraGenerator:
    def __init__(
        self,
        catalog: InfraCatalog,
        templates_dir: Path,
        blueprints_dir: Path,
    ) -> None:
        self._catalog = catalog
        self._templates_dir = Path(templates_dir)
        self._blueprints_dir = Path(blueprints_dir)
        self._blueprints_dir.mkdir(parents=True, exist_ok=True)

        self._env = Environment(
            loader=FileSystemLoader(str(self._templates_dir)),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
            autoescape=False,  # Terraform HCL, not HTML
        )

    # ------------------------------------------------------------------

    def generate(self, request: InfraGenerateRequest) -> InfraGenerateResponse:
        # 1. Resolve adapter_refs[] → IaC modules + remember provenance so
        #    we can surface what the auto-pull did (and write ADAPTERS.md).
        adapter_bindings, auto_modules = self._resolve_adapter_modules(request.adapter_refs)

        # 2. Normalize module list — always base first, union of explicit
        #    + adapter-derived modules, dedupe while preserving order.
        modules = self._normalize_modules(list(request.modules) + auto_modules)
        auto_included = [m for m in auto_modules if m not in set(request.modules)]

        # 3. Validate modules exist on disk for this provider
        for m in modules:
            if self._catalog.module_path(request.provider, m) is None:
                raise GenerationError(f"module '{m}' not available for provider '{request.provider}'")

        # 4. Allocate bundle directory
        bundle_id = str(uuid.uuid4())
        bundle_dir = self._blueprints_dir / bundle_id
        bundle_dir.mkdir()

        try:
            # 5. Copy module directories (excluding local terraform artifacts)
            modules_dst = bundle_dir / "modules"
            modules_dst.mkdir()
            for m in modules:
                src = self._catalog.module_path(request.provider, m)
                shutil.copytree(src, modules_dst / m, ignore=_copy_ignore)

            # 6. Render templates
            ctx = self._template_context(bundle_id, request, modules)
            file_names: list[str] = []
            for template_name in REQUIRED_TEMPLATES:
                rendered = self._env.get_template(template_name).render(**ctx)
                output_name = template_name[:-3]  # strip ".j2"
                (bundle_dir / output_name).write_text(rendered, encoding="utf-8")
                file_names.append(output_name)

            # 7. Adapter provenance — write ADAPTERS.md so the operator
            #    sees which adapter_refs drove which module inclusions.
            #    Only emit when adapter_refs[] is non-empty; otherwise the
            #    file would just be noise.
            if adapter_bindings:
                adapter_md = self._render_adapters_md(adapter_bindings, auto_included)
                (bundle_dir / "ADAPTERS.md").write_text(adapter_md, encoding="utf-8")
                file_names.append("ADAPTERS.md")

            # 8. Create tar.gz
            archive = self._blueprints_dir / f"{bundle_id}.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(bundle_dir, arcname=bundle_id)

        except Exception as e:
            # Clean up partial bundle on any failure
            shutil.rmtree(bundle_dir, ignore_errors=True)
            raise GenerationError(f"generation failed: {e}") from e

        logger.info(
            "generated bundle id=%s provider=%s modules=%s auto_included=%s",
            bundle_id, request.provider, modules, auto_included,
        )

        return InfraGenerateResponse(
            bundle_id=bundle_id,
            provider=request.provider,
            modules=modules,
            download_url=f"/api/infra/bundles/{bundle_id}/download",
            files=file_names + [f"modules/{m}/" for m in modules],
            auto_included_modules=auto_included,
        )

    # ------------------------------------------------------------------

    def list_bundles(self) -> list[InfraBundleSummary]:
        summaries: list[InfraBundleSummary] = []
        for child in sorted(self._blueprints_dir.iterdir()):
            if not child.is_dir():
                continue
            try:
                summary = self._read_bundle_summary(child)
            except Exception:
                logger.warning("could not read bundle summary at %s", child, exc_info=True)
                continue
            if summary is not None:
                summaries.append(summary)
        return summaries

    def archive_path(self, bundle_id: str) -> Optional[Path]:
        archive = self._blueprints_dir / f"{bundle_id}.tar.gz"
        return archive if archive.is_file() else None

    # ------------------------------------------------------------------

    def _normalize_modules(self, modules: list[str]) -> list[str]:
        # Always include base first, dedupe, preserve user order afterwards
        out = ["base"]
        for m in modules:
            if m != "base" and m not in out:
                out.append(m)
        return out

    def _resolve_adapter_modules(
        self, adapter_refs: list[str],
    ) -> tuple[list[tuple[str, str, Optional[str]]], list[str]]:
        """Resolve a list of adapter_ref ids against the catalog.

        Returns ``(bindings, modules)`` where:
          * ``bindings`` is a list of ``(adapter_ref, status, iac_module)``
            tuples — status is "resolved" | "unresolved" | "no-iac" so the
            ADAPTERS.md provenance trail surfaces every state.
          * ``modules`` is the deduped list of IaC modules to fold into
            the bundle, preserving the order adapter_refs appeared in.

        Unresolved adapter_refs (stale ids, typos) are NEVER fatal — they
        produce an "unresolved" binding so the operator sees the gap in
        ADAPTERS.md and the bundle still generates.
        """
        bindings: list[tuple[str, str, Optional[str]]] = []
        modules: list[str] = []
        for ref in adapter_refs:
            adapter = adapter_catalog.find(ref)
            if adapter is None:
                bindings.append((ref, "unresolved", None))
                continue
            iac_module = adapter.install.iac_module
            if not iac_module:
                bindings.append((ref, "no-iac", None))
                continue
            bindings.append((ref, "resolved", iac_module))
            if iac_module not in modules:
                modules.append(iac_module)
        return bindings, modules

    @staticmethod
    def _render_adapters_md(
        bindings: list[tuple[str, str, Optional[str]]],
        auto_included: list[str],
    ) -> str:
        lines: list[str] = []
        lines.append("# Adapter-driven module inclusions")
        lines.append("")
        lines.append(
            "This bundle was generated with `adapter_refs[]`. Each row "
            "below shows which IaC module a referenced tool adapter "
            "required, and whether the generator auto-included it."
        )
        lines.append("")
        lines.append("| adapter_ref | status | iac_module | auto-included |")
        lines.append("|-------------|--------|------------|---------------|")
        for ref, status, mod in bindings:
            mod_label = mod or "—"
            tag = "yes" if mod and mod in auto_included else "—"
            lines.append(f"| `{ref}` | {status} | `{mod_label}` | {tag} |")
        lines.append("")
        if auto_included:
            lines.append(
                f"**Auto-included modules:** "
                + ", ".join(f"`{m}`" for m in auto_included)
            )
            lines.append("")
        return "\n".join(lines)

    def _template_context(
        self,
        bundle_id: str,
        request: InfraGenerateRequest,
        modules: list[str],
    ) -> dict:
        p = request.params
        return {
            "bundle_id": bundle_id,
            "provider": request.provider,
            "region": request.region,
            "modules": modules,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "project_name": p.project_name,
            "dc_ssh_cidr": p.dc_ssh_cidr,
            "jumpbox_size": p.jumpbox_size,
            "k8s_node_count": p.k8s_node_count,
            "edr_target_count": p.edr_target_count,
            "ttl_hours": p.ttl_hours,
            "tags": p.tags,
        }

    def _read_bundle_summary(self, bundle_dir: Path) -> Optional[InfraBundleSummary]:
        archive = self._blueprints_dir / f"{bundle_dir.name}.tar.gz"
        size = archive.stat().st_size if archive.is_file() else 0

        # Parse minimal info from main.tf header comment
        main_tf = bundle_dir / "main.tf"
        provider = "unknown"
        modules: list[str] = []
        if main_tf.is_file():
            for line in main_tf.read_text(encoding="utf-8").splitlines()[:8]:
                if line.startswith("# Provider"):
                    provider = line.split(":", 1)[1].strip()
                elif line.startswith("# Modules"):
                    modules = [m.strip() for m in line.split(":", 1)[1].split(",")]

        created_at = datetime.fromtimestamp(
            bundle_dir.stat().st_ctime, tz=timezone.utc
        ).isoformat(timespec="seconds")

        return InfraBundleSummary(
            bundle_id=bundle_dir.name,
            provider=provider,
            modules=modules,
            created_at=created_at,
            size_bytes=size,
        )
