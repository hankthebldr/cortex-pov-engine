"""A router that exists but is not in the route table is a feature that does not
exist.

This repo has shipped that exact shape: the payload shelf passed 4368 tests
while ``Orchestrator._handle_pull`` never called ``compose()``, so the staging
path was dead in production. The Rust-tool shelf is served by
``core/api/tools_dist.py``, which is a NEW module in a package owned by a
concurrent workflow — so its one-line registration in ``core/main.py`` is a
handoff, and a handoff is precisely the kind of step that gets dropped.

The test SKIPS while the module is absent and fails the moment it lands without
being mounted. It deliberately does not import the module: import success is not
what is in doubt.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE = REPO_ROOT / "core" / "api" / "tools_dist.py"
MAIN = REPO_ROOT / "core" / "main.py"


def test_tools_dist_router_is_mounted_if_it_exists():
    if not MODULE.is_file():
        pytest.skip(
            "core/api/tools_dist.py not present yet — it is delivered as a "
            "handoff patch because core/api/** is owned by a concurrent pass. "
            "This test activates the moment the file lands."
        )
    assert MAIN.is_file(), "core/main.py missing"
    main = MAIN.read_text()
    imported = re.search(
        r"from\s+api\.tools_dist\s+import\s+router\s+as\s+(\w+)|import\s+api\.tools_dist",
        main,
    )
    assert imported, (
        "core/api/tools_dist.py exists but core/main.py never imports it — "
        "GET /api/tools/binaries will 404 and the Rust tools baked into "
        "/app/rust-dist are unreachable."
    )
    alias = imported.group(1) or "api.tools_dist.router"
    assert re.search(rf"include_router\(\s*{re.escape(alias)}", main), (
        "tools_dist is imported but its router is never included — the module "
        "is dead code and the shelf is unserved."
    )
