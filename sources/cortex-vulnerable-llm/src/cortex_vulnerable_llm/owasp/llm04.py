"""
OWASP LLM04 — Data and Model Poisoning.

A user-feedback / training endpoint accepts arbitrary text "for future
fine-tuning" without filtering. Subsequent queries that touch the
poisoned topic produce the planted output, demonstrating that the
"model" has incorporated the attacker's content.

Endpoints:
    POST /owasp/llm04/feedback   — submit poisoned feedback
    POST /owasp/llm04/chat       — query, may surface poisoned content

Detection signal:
  - The feedback endpoint accepts a payload whose body contains an
    instruction-shape ("when asked X, respond Y").
  - The chat endpoint then leaks the planted Y on a related query.

State is in-memory only — process-restart wipes the poison.
"""

from __future__ import annotations

import re

from flask import Blueprint, Flask, jsonify, request


bp = Blueprint("llm04", __name__, url_prefix="/owasp/llm04")


# Per-blueprint in-memory poison store. Keyed by topic regex → planted reply.
_POISON_STORE: list[tuple[re.Pattern[str], str]] = []

_INSTRUCTION_RE = re.compile(
    r"when (?:asked |the user asks )?(?:about |for )?(.+?),?\s+(?:respond|reply|say) (.+)",
    re.IGNORECASE | re.DOTALL,
)


@bp.route("/feedback", methods=["POST"])
def feedback():
    body = request.get_json(silent=True) or {}
    text = body.get("content", "")
    match = _INSTRUCTION_RE.search(text)
    if match:
        topic_phrase = match.group(1).strip().rstrip('.,"\'')
        planted = match.group(2).strip().strip('"\'')
        # Build a generous regex from the topic phrase (word stems).
        words = [re.escape(w) for w in topic_phrase.split() if len(w) > 2]
        if words:
            topic_pattern = re.compile(
                r"\b(" + "|".join(words) + r")\b", re.IGNORECASE
            )
            _POISON_STORE.append((topic_pattern, planted))
            return jsonify({
                "owasp_id": "LLM04",
                "vulnerable": True,
                "rule_name": "feedback_accepts_instruction",
                "leak_markers": ["planted reply", planted[:32]],
                "text": "Feedback accepted; this will improve future replies.",
                "poison_count": len(_POISON_STORE),
            })

    return jsonify({
        "owasp_id": "LLM04",
        "vulnerable": False,
        "rule_name": "feedback_filtered",
        "text": "Thanks for the feedback.",
    })


@bp.route("/chat", methods=["POST"])
def chat():
    body = request.get_json(silent=True) or {}
    prompt = body.get("prompt", "")
    for topic_pattern, planted in _POISON_STORE:
        if topic_pattern.search(prompt):
            return jsonify({
                "owasp_id": "LLM04",
                "vulnerable": True,
                "rule_name": "poisoned_response",
                "leak_markers": [planted[:32]],
                "text": planted,
            })
    return jsonify({
        "owasp_id": "LLM04",
        "vulnerable": False,
        "rule_name": "no_poison_match",
        "text": "I don't have specific information on that.",
    })


@bp.route("/_reset", methods=["POST"])
def reset():
    """Test helper — clear the poison store."""
    _POISON_STORE.clear()
    return jsonify({"owasp_id": "LLM04", "poison_count": 0})


def register(app: Flask, context: dict[str, str]) -> None:
    app.register_blueprint(bp)
