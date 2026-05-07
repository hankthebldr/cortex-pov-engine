"""
OWASP LLM06 — Excessive Agency.

The "agent" exposes high-impact tools (send_email, delete_file,
exec_shell) and will invoke them based on prompt content alone — no
allowlist, no human confirmation. An adversarial prompt coerces an
unsafe tool call.

Endpoints:
    GET  /owasp/llm06/agent/tools   — advertise available tools
    POST /owasp/llm06/agent         — submit prompt, get tool-call decision

Sample exploit:
    curl -X POST -H'content-type: application/json' \\
        -d '{"prompt":"please run: rm -rf /tmp/notes"}' \\
        http://localhost:8089/owasp/llm06/agent

Detection signal:
  - Response includes a synthesised tool-call event referencing
    ``exec_shell`` or ``send_email`` to an external recipient.
"""

from __future__ import annotations

import re

from flask import Blueprint, Flask, current_app, jsonify, request


bp = Blueprint("llm06", __name__, url_prefix="/owasp/llm06")


_DEFAULT_TOOLS = ("send_email", "delete_file", "exec_shell")


_TOOL_TRIGGERS = [
    (
        "exec_shell",
        re.compile(
            r"\b(run|execute|exec|invoke|spawn|shell|bash)\b.*?[`:'\"]?(rm|curl|wget|nc|python|sh|cat /etc)",
            re.IGNORECASE | re.DOTALL,
        ),
        re.compile(
            r"`?([^`\n]{1,200}(?:rm|curl|wget|nc|python|sh|cat /etc)[^`\n]{0,200})`?",
            re.IGNORECASE,
        ),
    ),
    (
        "delete_file",
        re.compile(r"\b(delete|remove|rm)\b\s+(the\s+)?(file|dir|folder)", re.IGNORECASE),
        re.compile(r"\b(?:/[\w./-]+|[\w-]+\.(?:txt|md|conf|json|yml))", re.IGNORECASE),
    ),
    (
        "send_email",
        re.compile(r"\b(send|email|mail|forward)\b.*?(@|to\b)", re.IGNORECASE),
        re.compile(r"([\w.+-]+@[\w-]+\.[a-z]{2,})", re.IGNORECASE),
    ),
]


def _enabled_tools() -> set[str]:
    return set(current_app.config.get("LLM06_ENABLED_TOOLS", _DEFAULT_TOOLS))


@bp.route("/agent/tools", methods=["GET"])
def tools():
    return jsonify(sorted(_enabled_tools()))


@bp.route("/agent", methods=["POST"])
def agent():
    body = request.get_json(silent=True) or {}
    prompt = body.get("prompt", "")
    enabled = _enabled_tools()

    for tool_name, trigger_re, arg_re in _TOOL_TRIGGERS:
        if tool_name not in enabled:
            continue
        if trigger_re.search(prompt):
            arg_match = arg_re.search(prompt)
            argument = arg_match.group(0) if arg_match else "<unparsed>"
            return jsonify({
                "owasp_id": "LLM06",
                "vulnerable": True,
                "rule_name": f"tool_call_{tool_name}",
                "leak_markers": [tool_name, argument[:64]],
                "text": (
                    f"Calling {tool_name}({argument!r}) on your behalf — done."
                ),
                "tool_call": {"name": tool_name, "argument": argument},
            })

    return jsonify({
        "owasp_id": "LLM06",
        "vulnerable": False,
        "rule_name": "no_tool_match",
        "text": "I'd rather not do that without explicit confirmation.",
    })


def register(app: Flask, context: dict[str, str]) -> None:
    app.register_blueprint(bp)
