"""
OWASP Top 10 for LLM Applications (v2025 / 2.0) — one Flask blueprint per
vulnerability code. Codes are the stable primary key across this package,
the prompt-attacker's probe filenames, and the AIRS scenario YAMLs.

Adding a new vulnerability:
    1. Define ``bp = Blueprint("llmNN", ..., url_prefix="/owasp/llmNN")``
       in ``llmNN.py`` and a ``register(app, vulns)`` callable.
    2. Add ``"LLMNN"`` to ``OWASP_VULNERABILITIES`` and the title to
       ``OWASP_TITLES`` below.
    3. Wire the import + registration in ``BLUEPRINT_REGISTRY``.
"""

from __future__ import annotations

from typing import Callable

from flask import Flask

from . import (
    llm01,
    llm02,
    llm03,
    llm04,
    llm05,
    llm06,
    llm07,
    llm08,
    llm09,
    llm10,
)


# Stable code → human title (single source of truth).
OWASP_TITLES: dict[str, str] = {
    "LLM01": "Prompt Injection",
    "LLM02": "Sensitive Information Disclosure",
    "LLM03": "Supply Chain",
    "LLM04": "Data and Model Poisoning",
    "LLM05": "Improper Output Handling",
    "LLM06": "Excessive Agency",
    "LLM07": "System Prompt Leakage",
    "LLM08": "Vector and Embedding Weaknesses",
    "LLM09": "Misinformation",
    "LLM10": "Unbounded Consumption",
}

OWASP_VULNERABILITIES: list[str] = list(OWASP_TITLES)


# Each entry: code -> (module's register callable, blueprint url prefix).
BLUEPRINT_REGISTRY: dict[str, tuple[Callable[[Flask, dict], None], str]] = {
    "LLM01": (llm01.register, "/owasp/llm01"),
    "LLM02": (llm02.register, "/owasp/llm02"),
    "LLM03": (llm03.register, "/owasp/llm03"),
    "LLM04": (llm04.register, "/owasp/llm04"),
    "LLM05": (llm05.register, "/owasp/llm05"),
    "LLM06": (llm06.register, "/owasp/llm06"),
    "LLM07": (llm07.register, "/owasp/llm07"),
    "LLM08": (llm08.register, "/owasp/llm08"),
    "LLM09": (llm09.register, "/owasp/llm09"),
    "LLM10": (llm10.register, "/owasp/llm10"),
}


def normalise_vuln_codes(raw: object) -> list[str]:
    """Accept ``"all"``, a list, or a comma-string of codes; return a sorted
    list of canonical ``LLMNN`` codes. Unknown codes raise ``ValueError``."""
    if raw is None or raw == "all" or raw == ["all"]:
        return list(OWASP_VULNERABILITIES)
    if isinstance(raw, str):
        items = [t.strip() for t in raw.split(",") if t.strip()]
    elif isinstance(raw, (list, tuple, set)):
        items = [str(t).strip() for t in raw if str(t).strip()]
    else:
        raise TypeError(f"vulns must be str | list | None, got {type(raw).__name__}")

    out = []
    for it in items:
        code = it.upper()
        if not code.startswith("LLM"):
            code = "LLM" + code.lstrip("0")
            if len(code) == 4:
                code = code[:3] + "0" + code[3]
        if code not in OWASP_VULNERABILITIES:
            raise ValueError(
                f"Unknown OWASP code '{it}'. Valid: {OWASP_VULNERABILITIES}"
            )
        out.append(code)
    # Preserve registration order for deterministic mounting.
    return [c for c in OWASP_VULNERABILITIES if c in set(out)]
