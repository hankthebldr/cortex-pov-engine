"""
CortexSim Tool Adapter Catalog (Phase A).

Singleton in-memory store of validated ``ToolAdapter`` packs. Mirrors
``core/engine/ttp_catalog.py`` so contributors only learn one pattern.

API:

    catalog.load(packs_dir)         # called at startup
    catalog.find(adapter_id)        # resolve a scenario's adapter_ref
    catalog.all()                   # for the /api/tools/adapters endpoint
    catalog.list_for_plane(plane)   # UI picker filter helper
    catalog.requires_consent(id)    # orchestrator launch gate
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional

from tools.adapter_loader import (
    ToolAdapterSchema,
    _find_pack_files,
    _parse_and_validate,
)

logger = logging.getLogger("cortexsim.tools.adapter_catalog")


class AdapterCatalog:
    """In-memory catalog of validated tool adapters."""

    def __init__(self) -> None:
        self._by_id: dict[str, ToolAdapterSchema] = {}

    # ---- public API ----------------------------------------------------

    def load(self, packs_dir: str) -> int:
        """Read every ``*.yml`` under ``packs_dir`` (excluding ``_schema.yml``)
        and replace the catalog. Returns the number of adapters indexed."""
        self._by_id.clear()
        files = _find_pack_files(packs_dir)
        if not files:
            logger.info("Adapter catalog: no packs found in %s", packs_dir)
            return 0

        loaded = 0
        rejected = 0
        for filepath in files:
            adapter, error = _parse_and_validate(filepath)
            if error:
                logger.error("REJECTED adapter %s: %s", filepath, error)
                rejected += 1
                continue
            assert adapter is not None
            if adapter.adapter_id in self._by_id:
                logger.error(
                    "REJECTED adapter %s: duplicate adapter_id %s (already loaded from %s)",
                    filepath, adapter.adapter_id,
                    "earlier file in the same load",
                )
                rejected += 1
                continue
            self._by_id[adapter.adapter_id] = adapter
            loaded += 1

        logger.info(
            "Adapter catalog loaded: %d adapter(s) (rejected=%d) from %s",
            loaded, rejected, packs_dir,
        )
        return loaded

    def find(self, adapter_id: Optional[str]) -> Optional[ToolAdapterSchema]:
        if not adapter_id:
            return None
        return self._by_id.get(adapter_id)

    def all(self) -> list[ToolAdapterSchema]:
        return list(self._by_id.values())

    def list_for_plane(self, plane: str) -> list[ToolAdapterSchema]:
        return [a for a in self._by_id.values() if plane in a.cortex_signal.planes]

    def list_for_category(self, category: str) -> list[ToolAdapterSchema]:
        return [a for a in self._by_id.values() if a.category == category]

    def requires_consent(self, adapter_id: str) -> Optional[str]:
        """Return the consent kind required to dispatch this adapter, or None.

        Returns one of: ``"c2-framework"`` | ``"dual-use-lab-only"`` |
        ``"destructive"``. The orchestrator maps each to a launch-time check:

          - c2-framework:      scenario must declare ``c2_authorized: true``
          - dual-use-lab-only: scenario must declare ``simulation_authorized: true``
                               (same shape as EAL campaigns already use)
          - destructive:       scenario must declare cleanup; engine enforces
                               execution of every cleanup.commands entry
        """
        adapter = self._by_id.get(adapter_id)
        if adapter is None:
            return None
        if adapter.safety_class in ("c2-framework", "dual-use-lab-only", "destructive"):
            return adapter.safety_class
        return None

    def count(self) -> int:
        return len(self._by_id)

    def iter(self) -> Iterable[ToolAdapterSchema]:
        return iter(self._by_id.values())


# Module-level singleton — imported by main.py + orchestrator
catalog = AdapterCatalog()
