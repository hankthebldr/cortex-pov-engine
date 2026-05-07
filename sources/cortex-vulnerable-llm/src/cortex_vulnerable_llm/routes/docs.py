"""
Docs route — surfaces the per-OWASP-class exploit narrative from each
blueprint's module docstring so DCs can copy-paste during a POV.

GET /docs                — index of available docs
GET /docs/<llmNN>        — narrative for one OWASP class (text/markdown)
"""

from __future__ import annotations

import importlib

from flask import Blueprint, Flask, Response, jsonify

from ..owasp import OWASP_TITLES, OWASP_VULNERABILITIES


bp = Blueprint("docs", __name__, url_prefix="/docs")


def _module_docstring(code: str) -> str:
    mod_name = f"cortex_vulnerable_llm.owasp.{code.lower()}"
    try:
        mod = importlib.import_module(mod_name)
    except ImportError:
        return f"# {code}\n\n(no module {mod_name})\n"
    body = mod.__doc__ or ""
    return f"# {code} — {OWASP_TITLES.get(code, '')}\n\n{body}\n"


@bp.route("", methods=["GET"])
def index():
    return jsonify({
        "docs": [
            {
                "code": code,
                "title": OWASP_TITLES[code],
                "url": f"/docs/{code.lower()}",
            }
            for code in OWASP_VULNERABILITIES
        ]
    })


@bp.route("/<code>", methods=["GET"])
def doc(code: str):
    code_upper = code.upper()
    if code_upper not in OWASP_VULNERABILITIES:
        return jsonify({"error": f"unknown OWASP code '{code}'"}), 404
    body = _module_docstring(code_upper)
    return Response(body, mimetype="text/markdown")


def register(app: Flask) -> None:
    app.register_blueprint(bp)
