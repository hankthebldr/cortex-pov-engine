# core/integrations/xsiam/exceptions.py
"""Typed failures for the XSIAM integration.

Each error carries a stable ``code`` and an ``http_status`` so the global
exception handler in core/main.py can render the repo's {error, code, detail}
envelope without per-route plumbing (mirrors security.crypto.CryptoError).
"""
from __future__ import annotations

from typing import Optional


class XsiamError(RuntimeError):
    code = "XSIAM_ERROR"
    http_status = 502  # default: bad upstream

    def __init__(self, detail: str, *, upstream_status: Optional[int] = None):
        super().__init__(detail)
        self.detail = detail
        self.upstream_status = upstream_status


class XsiamConfigError(XsiamError):
    code = "XSIAM_CONFIG_ERROR"
    http_status = 400  # caller's tenant config is wrong


class XsiamAuthError(XsiamError):
    code = "XSIAM_AUTH_ERROR"
    http_status = 502  # tenant rejected OUR key — upstream, not caller


class XsiamApiError(XsiamError):
    code = "XSIAM_API_ERROR"
    http_status = 502


class XsiamQueryError(XsiamError):
    code = "XSIAM_QUERY_ERROR"
    http_status = 502


class XsiamQuotaError(XsiamError):
    code = "XSIAM_QUOTA_ERROR"
    http_status = 429
