"""The released image runs with no bind mounts, so every `sources/` tree SimCore
reads at runtime must be COPYed into it.

`docker-compose.yml` bind-mounts `./sources`, which hid that the runtime stage
never copied any of it. An installed SimCore (image only) would then lack the
KOI agentic pack that `agentic_egress` reads in-process, the in-tree AIRS and
browser drivers the EAL plugins shell out to, and the Python tools the registry
starts from `source_path`. The three Rust tools are compiled into
`/app/rust-dist` by the rust-builder stage and never run from `sources/`.

Static half: this test parses the real files. `make check-image-sources`
asserts the same list against a built image.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "core" / "Dockerfile"

# Read in-process or shelled out to by EAL plugins, not via the tool registry.
PLUGIN_TREES = {
    "cortex-malicious-agentic-pack",  # core/eal_simulator/plugins/agentic_egress.py
    "cortex-prompt-attacker",         # airs_prompt_attack.py
    "cortex-vulnerable-llm",          # AIRS target
    "cortex-browser-attacker",        # browser_attack_runner.py
}

PRIVATE_ORIGIN = {"CDR", "xsiam-prisma-cdr-lab"}

_COPY = re.compile(r"^COPY\s+sources/([\w.-]+)/\s+(\S+)\s*$", re.MULTILINE)


def _stages() -> tuple[str, str]:
    """(rust-builder stage text, final runtime stage text)."""
    text = DOCKERFILE.read_text()
    parts = re.split(r"^FROM\s", text, flags=re.MULTILINE)
    rust = next(p for p in parts if re.search(r"\bAS rust-builder\b", p.splitlines()[0]))
    return rust, parts[-1]


def runtime_source_trees() -> set[str]:
    """`sources/<name>` trees SimCore reads at runtime from the image."""
    sys.path.insert(0, str(REPO_ROOT / "core"))
    from tools.registry import STATIC_TOOL_REGISTRY

    rust, _ = _stages()
    compiled = {m.group(1) for m in _COPY.finditer(rust)}
    registry = {
        Path(t["source_path"]).name
        for t in STATIC_TOOL_REGISTRY.values()
        if t.get("source_path", "").startswith("sources/")
    }
    return (registry - compiled) | PLUGIN_TREES


def _copied() -> set[str]:
    _, runtime = _stages()
    return {m.group(1) for m in _COPY.finditer(runtime) if m.group(2) == f"/app/sources/{m.group(1)}/"}


def test_runtime_source_trees_are_known():
    assert runtime_source_trees() == {
        "cortex-malicious-agentic-pack",
        "cortex-prompt-attacker",
        "cortex-vulnerable-llm",
        "cortex-browser-attacker",
        "mocktaxii",
        "gocortexbrokenbank",
    }


def test_dockerfile_copies_every_runtime_source_tree():
    assert runtime_source_trees() - _copied() == set()


def test_dockerfile_never_copies_private_origin_trees():
    assert PRIVATE_ORIGIN & _copied() == set()
