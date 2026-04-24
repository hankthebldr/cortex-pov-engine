"""
Content loader — merges jumpbox-installed content into TOOL_REGISTRY.

At startup, SimCore calls `merge_installed_tools()` which reads
/opt/cortexsim/content/installed.json (written by install-content.sh)
and overlays entries that don't collide with STATIC_TOOL_REGISTRY names.

Static entries always win — they're the authoritative definitions from the
Phase 1 spec (signalbench, mocktaxii, etc.).

Content entries use type="content" by convention to distinguish them in the UI.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from tools.registry import STATIC_TOOL_REGISTRY, TOOL_REGISTRY

logger = logging.getLogger("cortexsim.content_loader")

DEFAULT_MANIFEST_PATH = Path("/opt/cortexsim/content/installed.json")

REQUIRED_FIELDS = {"name", "install_path", "type", "plane", "description"}


def merge_installed_tools(manifest_path: Path = DEFAULT_MANIFEST_PATH) -> int:
    """
    Read the manifest and add valid entries to TOOL_REGISTRY (mutating it).

    Returns the number of entries added.
    Never raises — all errors are logged and treated as a no-op.
    """
    if not manifest_path.is_file():
        logger.info("no installed content manifest at %s — skipping merge", manifest_path)
        return 0

    try:
        raw = manifest_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("failed to read installed.json at %s: %s", manifest_path, e)
        return 0

    entries = data.get("tools", []) if isinstance(data, dict) else []
    if not isinstance(entries, list):
        logger.warning("installed.json 'tools' is not a list — skipping")
        return 0

    added = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        missing = REQUIRED_FIELDS - entry.keys()
        if missing:
            logger.debug("skipping content entry (missing fields %s): %s", missing, entry)
            continue

        name = entry["name"]
        if name in STATIC_TOOL_REGISTRY:
            logger.debug("skipping content entry %s — collides with static entry", name)
            continue

        TOOL_REGISTRY[name] = {
            "install_path": entry["install_path"],
            "type": entry["type"],
            "plane": entry["plane"],
            "description": entry["description"],
            "source": "installed-content",
        }
        # Optional passthroughs
        for k in ("repo", "category", "purpose", "image"):
            if k in entry:
                TOOL_REGISTRY[name][k] = entry[k]
        added += 1

    logger.info("content_loader merged %d entries from %s", added, manifest_path)
    return added
