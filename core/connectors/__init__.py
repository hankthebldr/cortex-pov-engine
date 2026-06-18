"""CortexSim connector framework — optional read-back of observed signal.

Importing this package registers the built-in connectors. See ``base`` for the
:class:`Connector` ABC + registry, ``matcher`` for result reconciliation, and
the per-source modules (``xsiam``) for concrete connectors.
"""
from __future__ import annotations

from .base import (
    Connector,
    ConnectorConfig,
    ConnectorError,
    ObservedAlert,
    PullResult,
    available_connectors,
    default_http_fetcher,
    get_connector,
    register_connector,
)
from .matcher import MatchVerdict, reconcile

# Importing the connector modules triggers their @register_connector decorators.
from . import xsiam  # noqa: F401,E402

__all__ = [
    "Connector",
    "ConnectorConfig",
    "ConnectorError",
    "ObservedAlert",
    "PullResult",
    "MatchVerdict",
    "available_connectors",
    "default_http_fetcher",
    "get_connector",
    "register_connector",
    "reconcile",
]
