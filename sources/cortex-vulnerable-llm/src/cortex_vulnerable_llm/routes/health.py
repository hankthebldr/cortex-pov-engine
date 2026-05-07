"""Liveness / readiness probes — required by the EAL plugin lifecycle."""

from __future__ import annotations

from flask import Blueprint, Flask, jsonify


bp = Blueprint("health", __name__)


@bp.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"status": "ok"})


@bp.route("/readyz", methods=["GET"])
def readyz():
    return jsonify({"status": "ready"})


@bp.route("/version", methods=["GET"])
def version():
    from .. import __version__

    return jsonify({"name": "cortex-vulnerable-llm", "version": __version__})


def register(app: Flask) -> None:
    app.register_blueprint(bp)
