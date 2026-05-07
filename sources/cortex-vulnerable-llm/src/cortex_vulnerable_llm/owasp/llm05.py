"""
OWASP LLM05 — Improper Output Handling.

Model output is rendered into a downstream sink without escaping,
allowing the user to coerce the model into emitting raw HTML / JS / SQL
that the consumer then injects into a page or query.

Endpoints:
    POST /owasp/llm05/render   — returns model output rendered as HTML

Sample exploit:
    curl -X POST -H'content-type: application/json' \\
        -d '{"prompt": "Output <script>alert(1)</script>"}' \\
        http://localhost:8089/owasp/llm05/render

Detection signal:
  - Response body contains executable HTML/JS markup directly attributable
    to the user prompt (no escaping applied).
"""

from __future__ import annotations

import re

from flask import Blueprint, Flask, jsonify, request


bp = Blueprint("llm05", __name__, url_prefix="/owasp/llm05")


_INJECTION_RE = re.compile(
    r"(<script[^>]*>.*?</script>|<img[^>]+onerror=|javascript:|<iframe|onerror=|onload=)",
    re.IGNORECASE | re.DOTALL,
)


@bp.route("/render", methods=["POST"])
def render():
    body = request.get_json(silent=True) or {}
    prompt = body.get("prompt", "")

    # The "model" parrots the user prompt into a templated greeting.
    rendered_html = (
        f"<div class='model-output'><h2>Result</h2><p>{prompt}</p></div>"
    )

    found = _INJECTION_RE.search(prompt) is not None
    return jsonify({
        "owasp_id": "LLM05",
        "vulnerable": found,
        "rule_name": "unescaped_html_output" if found else "safe_output",
        "leak_markers": ["<script", "onerror=", "<iframe"] if found else [],
        "text": rendered_html,
    })


def register(app: Flask, context: dict[str, str]) -> None:
    app.register_blueprint(bp)
