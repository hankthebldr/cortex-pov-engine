# tests/integration/xsiam/test_exceptions.py
from __future__ import annotations


def test_exception_hierarchy_and_codes():
    from integrations.xsiam.exceptions import (
        XsiamError, XsiamConfigError, XsiamAuthError,
        XsiamApiError, XsiamQueryError, XsiamQuotaError,
    )
    # All subclasses derive from XsiamError
    for cls in (XsiamConfigError, XsiamAuthError, XsiamApiError, XsiamQueryError, XsiamQuotaError):
        assert issubclass(cls, XsiamError)

    # Each carries a stable code + an HTTP status for the envelope
    assert XsiamConfigError("x").http_status == 400
    assert XsiamQuotaError("x").http_status == 429
    assert XsiamAuthError("x").code == "XSIAM_AUTH_ERROR"

    # XsiamApiError remembers the upstream status (drives the 403 fallback)
    err = XsiamApiError("boom", upstream_status=403)
    assert err.upstream_status == 403
    assert err.detail == "boom"
