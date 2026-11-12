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


class XsiamCapabilityError(XsiamError):
    code = "XSIAM_CAPABILITY_MISSING"
    http_status = 403  # the credential was never authorised for XQL


# Both XQL refusals are config errors: the caller's request is wrong and is
# refused before anything is sent. One class each, raised by both the SEC-05
# lexer (client.refuse_xql_write_stages, client._xql_start_body) and the D-18
# guards (guards.refuse_xql_write_stage, guards.xql_query_text).
class XqlWriteStageRefused(XsiamConfigError):
    code = "XQL_WRITE_STAGE_REFUSED"
    http_status = 400  # caller's query tries to write


class XqlRequestShapeInvalid(XsiamConfigError):
    code = "XQL_REQUEST_SHAPE_INVALID"
    http_status = 400  # the start-query body has no usable query string
