"""
cortex-vulnerable-llm — deliberately vulnerable LLM Flask app for CortexSim
AIRS detection validation.

Backed by a "canary LLM" that pattern-matches the prompt against
OWASP-aligned regex/lures and returns scripted responses that look like a
real model fell for the attack. **No real API calls. No keys. Ever.**

Public surface:
  - app_factory(vulns: Iterable[str] | None) -> flask.Flask
  - Canary  (the matcher engine)
  - OWASP_VULNERABILITIES  (list of registered LLM01..LLM10 codes)
"""

from __future__ import annotations

from .app import app_factory
from .canary import Canary, CanaryResponse
from .owasp import OWASP_VULNERABILITIES, OWASP_TITLES

__version__ = "1.0.0"

__all__ = [
    "Canary",
    "CanaryResponse",
    "OWASP_TITLES",
    "OWASP_VULNERABILITIES",
    "__version__",
    "app_factory",
]
