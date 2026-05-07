"""
OWASP LLM10 — Unbounded Consumption.

The completion endpoint accepts an arbitrary ``max_tokens`` and returns a
synthesised response of approximately that length. No per-user budget,
no rate limit, no circuit breaker. Repeated high-token requests exhaust
the latency / cost surface.

Endpoint:
    POST /owasp/llm10/chat   — body: {"prompt": "...", "max_tokens": N}

Detection signal:
  - Response body length proportional to requested ``max_tokens``;
    JSON includes a ``token_count`` field for scorers.
  - Sustained burst (50+ requests with ``max_tokens >= 4000``) within a
    short window should trip an AIRS rate-limit / cost-anomaly detector.
"""

from __future__ import annotations

from flask import Blueprint, Flask, jsonify, request


bp = Blueprint("llm10", __name__, url_prefix="/owasp/llm10")


_HARD_CEILING = 200_000  # absolute upper bound to avoid OOM during POVs


def _generate_text(prompt: str, max_tokens: int) -> tuple[str, int]:
    word = "lorem-canary-fabricated "
    target_chars = max(1, max_tokens) * 5  # ~5 chars per "token"
    target_chars = min(target_chars, _HARD_CEILING * 5)
    repeats = (target_chars // len(word)) + 1
    text = (word * repeats)[:target_chars]
    return text, max(1, target_chars // 5)


@bp.route("/chat", methods=["POST"])
def chat():
    body = request.get_json(silent=True) or {}
    prompt = body.get("prompt", "")
    requested = body.get("max_tokens")
    try:
        max_tokens = int(requested) if requested is not None else 256
    except (TypeError, ValueError):
        max_tokens = 256

    if max_tokens <= 0:
        return jsonify({
            "owasp_id": "LLM10",
            "vulnerable": False,
            "rule_name": "invalid_max_tokens",
            "text": "max_tokens must be positive.",
        }), 400

    capped = min(max_tokens, _HARD_CEILING)
    text, token_count = _generate_text(prompt, capped)
    is_unbounded = max_tokens > 8000  # what a reasonable cap would have been

    return jsonify({
        "owasp_id": "LLM10",
        "vulnerable": is_unbounded,
        "rule_name": "unbounded_max_tokens" if is_unbounded else "bounded",
        "leak_markers": ["lorem-canary-fabricated"] if is_unbounded else [],
        "text": text,
        "token_count": token_count,
        "requested_max_tokens": max_tokens,
        "hard_ceiling_applied": max_tokens > _HARD_CEILING,
    })


def register(app: Flask, context: dict[str, str]) -> None:
    app.register_blueprint(bp)
