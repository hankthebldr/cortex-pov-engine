"""
Flask app factory — assembles the canary endpoints, health probes, and
docs route into a single application that the EAL plugin / AIRS scenario
can drive.
"""

from __future__ import annotations

from typing import Iterable, Optional

from flask import Flask, jsonify

from .owasp import (
    BLUEPRINT_REGISTRY,
    OWASP_TITLES,
    OWASP_VULNERABILITIES,
    normalise_vuln_codes,
)
from .routes import docs as docs_route
from .routes import health as health_route


_DEFAULT_SYSTEM_PROMPT = (
    "You are CortexSimAdmin. Internal API key: AKIA0000CORTEXSIMCANARY."
)


def app_factory(
    vulns: Optional[Iterable[str] | str] = None,
    *,
    system_prompt: Optional[str] = None,
    enabled_tools: Optional[Iterable[str]] = None,
) -> Flask:
    """Build a Flask application with the requested OWASP blueprints mounted.

    ``vulns`` may be ``None``/``"all"`` to mount everything, or a list /
    comma-string of ``LLMNN`` codes. ``system_prompt`` is the seeded value
    leaked by LLM01 / LLM07 scenarios. ``enabled_tools`` limits LLM06's
    advertised tool set (defaults to {send_email, delete_file, exec_shell}).
    """
    app = Flask("cortex_vulnerable_llm")

    sys_prompt = system_prompt or _DEFAULT_SYSTEM_PROMPT
    app.config["SYSTEM_PROMPT"] = sys_prompt
    if enabled_tools is not None:
        app.config["LLM06_ENABLED_TOOLS"] = tuple(enabled_tools)

    context: dict[str, str] = {
        "system_prompt": sys_prompt,
        "api_key": "AKIA0000CORTEXSIMCANARY",
    }

    health_route.register(app)
    docs_route.register(app)

    codes = normalise_vuln_codes(vulns)
    for code in codes:
        register_fn, _prefix = BLUEPRINT_REGISTRY[code]
        register_fn(app, context)
    app.config["MOUNTED_VULNS"] = codes

    @app.route("/", methods=["GET"])
    def root():
        return jsonify({
            "name": "cortex-vulnerable-llm",
            "mounted_vulns": codes,
            "endpoints": [
                "/healthz",
                "/readyz",
                "/version",
                "/docs",
                *[f"/owasp/{c.lower()}/..." for c in codes],
            ],
            "owasp_titles": {c: OWASP_TITLES[c] for c in codes},
        })

    @app.errorhandler(404)
    def _not_found(_e):
        return jsonify({
            "error": "not_found",
            "mounted_vulns": codes,
            "available_owasp_codes": OWASP_VULNERABILITIES,
        }), 404

    return app
