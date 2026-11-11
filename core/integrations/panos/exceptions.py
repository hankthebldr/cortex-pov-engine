# core/integrations/panos/exceptions.py
"""Typed failures for the PAN-OS integration. ``code`` is always a ``PN_*`` constant."""
from __future__ import annotations

from typing import Optional

from . import codes


class PanosConfigError(ValueError):
    """The registered/supplied config is invalid (rejected before any request)."""

    def __init__(self, message: str, *, code: Optional[str] = None):
        super().__init__(message)
        self.code = code or codes.PN_CONFIG_INVALID


class PanosShapeError(RuntimeError):
    """The firewall replied in a shape we do not recognise. Raises, never reads as empty."""

    def __init__(self, message: str, *, code: Optional[str] = None):
        super().__init__(message)
        self.code = code or codes.PN_SHAPE_UNRECOGNISED


class PanosApiError(PanosShapeError):
    """The firewall returned a well-formed ``status="error"`` reply.

    ``code`` is the PN_* code (default PN_SHAPE_UNRECOGNISED). The firewall's own
    error code (e.g. "403", "13") is NOT a PN_ code and lives in ``panos_code``.
    """

    def __init__(self, message: str, *, code: Optional[str] = None,
                 panos_code: Optional[str] = None):
        super().__init__(message, code=code)
        self.panos_code = panos_code
        self.message = message


class PanosWriteRefused(RuntimeError):
    """A non-read request was attempted on a read-only client."""

    def __init__(self, message: str, *, code: Optional[str] = None):
        super().__init__(message)
        self.code = code or codes.PN_WRITE_REFUSED


class PanosTransportError(RuntimeError):
    """DNS / connect / timeout failure reaching the management plane."""

    def __init__(self, message: str, *, code: Optional[str] = None):
        super().__init__(message)
        self.code = code or codes.PN_UNREACHABLE
