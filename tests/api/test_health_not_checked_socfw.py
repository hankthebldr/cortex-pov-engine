"""Health names the SOC Framework probe as deliberately not checked."""
from __future__ import annotations

from api import health


def test_not_checked_lists_socfw():
    item = next(i for i in health.NOT_CHECKED if i["what"] == "socfw_response_layer")
    assert item["why"] == ("presence and Auto Triage settings need tenant reads, "
                           "which health never makes")
    assert item["checked_by"] == ("POST /api/connectors/xsiam_tenant/preflight "
                                  "(stage response_layer)")
