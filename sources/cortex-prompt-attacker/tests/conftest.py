"""Shared fixtures for cortex-prompt-attacker tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


@pytest.fixture
def repo_root() -> Path:
    return _REPO_ROOT


@pytest.fixture
def probes_dir(tmp_path: Path) -> Path:
    """Return a tmp directory the test can fill with probe YAMLs."""
    return tmp_path
