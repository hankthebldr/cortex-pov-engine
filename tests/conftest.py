"""Shared pytest fixtures for CortexSim tests."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Ensure core/ is on sys.path so we can import like production
REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_DIR = REPO_ROOT / "core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

# Point CORTEXSIM_BASE_DIR at the repo root before `config.settings` is
# first imported, so module-level path derivations (e.g. api.infra) resolve
# to the real infra/ tree rather than the Docker default of /app.
os.environ.setdefault("CORTEXSIM_BASE_DIR", str(REPO_ROOT))


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def fixtures_dir() -> Path:
    return REPO_ROOT / "tests" / "fixtures"


# ---------------------------------------------------------------------------
# Hermetic payload shelf
# ---------------------------------------------------------------------------
# The K8s delivery stages third-party tools (deepce.sh, linpeas.sh) that
# payloads/.gitignore deliberately excludes — you do not commit 1.1 MB of
# someone else's GPL/MIT code, and scripts/build-payloads.sh fetches them on
# the DC's own host instead.
#
# The consequence is that a fresh clone has an EMPTY shelf, and the generator
# (correctly) refuses to emit a manifest for a scenario declaring payloads it
# cannot digest. So without this, `make test` fails on a clean checkout and in
# CI — not because anything is broken, but because the test suite depended on
# an artifact the repo does not carry.
#
# Tests therefore get their OWN shelf, always, even on a machine where the real
# one is populated. Hermetic beats convenient: a suite whose behaviour changes
# depending on whether someone ran build-payloads.sh is a suite that passes on
# your laptop and fails in CI. The stub bytes differ from the real tools, which
# is fine — what is under test is that a declared payload RESOLVES to a digest
# and gets baked into the manifest, never the content of linpeas itself.
def _declared_payload_names() -> set[str]:
    """Every payload name the scenario corpus declares."""
    import yaml  # noqa: PLC0415

    names: set[str] = set()
    for path in (REPO_ROOT / "scenarios").rglob("*.yml"):
        if path.name.startswith("_"):
            continue
        try:
            doc = yaml.safe_load(path.read_text())
        except Exception:  # noqa: BLE001 — a malformed file is another test's problem
            continue
        if isinstance(doc, dict):
            posture = doc.get("cluster_posture") or {}
            if isinstance(posture, dict):
                names.update(posture.get("payloads") or [])
    return names


@pytest.fixture(scope="session", autouse=True)
def _hermetic_payload_shelf(tmp_path_factory):
    """Stage a deterministic stub for every payload the corpus declares."""
    shelf = tmp_path_factory.mktemp("payload-shelf")
    # Declare the shelf a test double. Stub bytes can never satisfy a pack's
    # real sha256 pin, and the alternative — downloading 8 live offensive tools
    # to run the suite — is worse. payload_shelf honours this marker only when
    # CORTEXSIM_PAYLOAD_DIST is set explicitly and the env is not production, so
    # neither the repo's payloads/ nor the image's baked-in shelf can be marked,
    # and every composition off it carries a STUB_SHELF warning.
    (shelf / ".cortexsim-stub-shelf").write_text("")
    for name in sorted(_declared_payload_names()):
        # Deterministic content -> deterministic digest, so a manifest rendered
        # twice in one session is byte-identical (the export-determinism gate
        # and the bundle-equivalence guard both rely on that).
        (shelf / name).write_text(
            f"#!/bin/sh\n# CortexSim test stub for {name} — not the real tool.\nexit 0\n"
        )
    os.environ["CORTEXSIM_PAYLOAD_DIST"] = str(shelf)
    yield shelf
    os.environ.pop("CORTEXSIM_PAYLOAD_DIST", None)
