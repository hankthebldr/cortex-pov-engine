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
