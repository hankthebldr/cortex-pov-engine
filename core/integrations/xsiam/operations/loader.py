# core/integrations/xsiam/operations/loader.py
"""File walk + parse for the XSIAM operation packs.

Mirrors ``core/tools/adapter_loader.py``: ``*.yml`` files (excluding
``_``-prefixed), parse-and-validate that **never raises** (returns errors as
strings so the catalog can reject-and-log), and a ``default_ops_dir`` convention.

Unlike the adapter packs (one mapping per file), an operation pack is a mapping
with a ``category`` and an ``operations:`` list, so category/api metadata are
declared once per file and merged onto each row.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

import yaml
from pydantic import ValidationError

from .schema import XsiamOperationSchema

logger = logging.getLogger("cortexsim.xsiam.operations.loader")


def _find_pack_files(packs_dir: str) -> list[str]:
    """All ``*.yml`` files in packs_dir except ``_``-prefixed ones."""
    if not os.path.isdir(packs_dir):
        return []
    found: list[str] = []
    for fname in sorted(os.listdir(packs_dir)):
        if not fname.endswith(".yml") or fname.startswith("_"):
            continue
        found.append(os.path.join(packs_dir, fname))
    return found


def _parse_and_validate(filepath: str) -> tuple[list[XsiamOperationSchema], list[str]]:
    """Parse one pack file into a list of operations + a list of error strings.

    Never raises. A file-level failure (bad YAML / wrong shape) returns an empty
    op list with one error. Per-row schema failures collect individual errors so
    one bad row never sinks the whole file.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            raw: Any = yaml.safe_load(fh)
    except Exception as exc:  # noqa: BLE001
        return [], [f"{filepath}: YAML parse error: {exc}"]

    if not isinstance(raw, dict):
        return [], [f"{filepath}: YAML root is not a mapping"]

    category = raw.get("category")
    operations = raw.get("operations")
    if not isinstance(operations, list):
        return [], [f"{filepath}: missing 'operations' list"]

    ops: list[XsiamOperationSchema] = []
    errors: list[str] = []
    for i, row in enumerate(operations):
        if not isinstance(row, dict):
            errors.append(f"{filepath}[{i}]: operation is not a mapping")
            continue
        merged = dict(row)
        merged.setdefault("category", category)  # file-level default
        try:
            ops.append(XsiamOperationSchema(**merged))
        except ValidationError as exc:
            errors.append(f"{filepath}[{i}] ({row.get('op_id', '?')}): {exc}")
    return ops, errors


def default_ops_dir(base_dir: str) -> str:
    """Convention: ``<base>/core/integrations/xsiam/operations/packs``.

    The packs live inside the ``core`` package so the Docker image ships them
    with the code (no separate COPY needed).
    """
    return os.path.join(base_dir, "core", "integrations", "xsiam", "operations", "packs")
