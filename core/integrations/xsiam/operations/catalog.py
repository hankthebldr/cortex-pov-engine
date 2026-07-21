# core/integrations/xsiam/operations/catalog.py
"""Singleton in-memory catalog of validated XSIAM API operations.

Mirrors ``core/tools/adapter_catalog.py``: ``load()`` clears and rebuilds a
``{op_id: schema}`` dict, rejecting duplicates and invalid rows (logged,
counted, never fatal). Imported by ``core/main.py`` (boot) and
``core/api/xsiam.py`` (the executor).
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

from .loader import _find_pack_files, _parse_and_validate
from .schema import XsiamOperationSchema

logger = logging.getLogger("cortexsim.xsiam.operations.catalog")


class OperationCatalog:
    def __init__(self) -> None:
        self._by_id: dict[str, XsiamOperationSchema] = {}

    def load(self, packs_dir: str) -> int:
        """Read every ``*.yml`` under ``packs_dir`` and replace the catalog.

        Returns the number of operations indexed. Rejects (parse error, bad row,
        duplicate op_id) are logged and counted but never raise.
        """
        self._by_id.clear()
        files = _find_pack_files(packs_dir)
        if not files:
            logger.info("XSIAM operation catalog: no packs found in %s", packs_dir)
            return 0

        loaded = 0
        rejected = 0
        for filepath in files:
            ops, errors = _parse_and_validate(filepath)
            for err in errors:
                logger.error("REJECTED xsiam operation: %s", err)
                rejected += 1
            for op in ops:
                if op.op_id in self._by_id:
                    logger.error(
                        "REJECTED xsiam operation %s: duplicate op_id (from %s)",
                        op.op_id, filepath,
                    )
                    rejected += 1
                    continue
                self._by_id[op.op_id] = op
                loaded += 1

        logger.info(
            "XSIAM operation catalog loaded: %d operation(s) (rejected=%d) from %s",
            loaded, rejected, packs_dir,
        )
        return loaded

    def find(self, op_id: Optional[str]) -> Optional[XsiamOperationSchema]:
        if not op_id:
            return None
        return self._by_id.get(op_id)

    def all(self) -> list[XsiamOperationSchema]:
        return list(self._by_id.values())

    def list_for_category(self, category: str) -> list[XsiamOperationSchema]:
        return [o for o in self._by_id.values() if o.category == category]

    def list_for_access(self, access_class: str) -> list[XsiamOperationSchema]:
        return [o for o in self._by_id.values() if o.access_class == access_class]

    def categories(self) -> list[str]:
        return sorted({o.category for o in self._by_id.values()})

    def count(self) -> int:
        return len(self._by_id)

    def iter(self) -> Iterable[XsiamOperationSchema]:
        return iter(self._by_id.values())


# Module-level singleton — imported by main.py + the executor
catalog = OperationCatalog()
