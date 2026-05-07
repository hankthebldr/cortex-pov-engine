"""Shared pytest fixtures for cortex-vulnerable-llm."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make ``src/`` importable without requiring ``pip install -e .``.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from cortex_vulnerable_llm.app import app_factory  # noqa: E402


@pytest.fixture
def client():
    """Default Flask test client with all OWASP blueprints mounted."""
    app = app_factory(vulns="all")
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def client_factory():
    """Build clients with custom args (vulns/system_prompt/tools)."""
    def _make(**kwargs):
        app = app_factory(**kwargs)
        app.config["TESTING"] = True
        return app.test_client()
    return _make
