# tests/integration/xsiam/test_smoke_live.py
"""Live-tenant smoke test — SKIPPED unless real creds are present.

Run against a real tenant:
    CORTEXSIM_XSIAM_TEST_TENANT=https://api-<sub>.xdr.<region>.paloaltonetworks.com \
    CORTEXSIM_XSIAM_TEST_KEY=<api-key> \
    CORTEXSIM_XSIAM_TEST_KEY_ID=<id> \
    .venv/bin/pytest tests/integration/xsiam/test_smoke_live.py -v -s
"""
from __future__ import annotations

import os

import pytest

_BASE = os.environ.get("CORTEXSIM_XSIAM_TEST_TENANT")
_KEY = os.environ.get("CORTEXSIM_XSIAM_TEST_KEY")
_KEY_ID = os.environ.get("CORTEXSIM_XSIAM_TEST_KEY_ID")

pytestmark = pytest.mark.skipif(
    not (_BASE and _KEY and _KEY_ID),
    reason="set CORTEXSIM_XSIAM_TEST_TENANT/KEY/KEY_ID to run the live smoke test",
)


def _client():
    from integrations.xsiam.client import XsiamClient
    from integrations.xsiam.config import XsiamTenantConfig
    cfg = XsiamTenantConfig(base_url=_BASE, region="smoke",
                            auth_mode="standard", api_key_id=_KEY_ID)
    return XsiamClient(cfg, _KEY)


@pytest.mark.asyncio
async def test_live_healthcheck_or_endpoints():
    from integrations.xsiam.exceptions import XsiamApiError
    client = _client()
    try:
        status = await client.healthcheck()
        print("HEALTHCHECK:", status)
        assert status is not None
    except XsiamApiError as exc:
        assert exc.upstream_status == 403  # license-gated on this tenant
        print("healthcheck 403; endpoints sample:", await client.ping_via_endpoints())


@pytest.mark.asyncio
async def test_live_trivial_xql():
    client = _client()
    reply = await client.run_xql("dataset = xdr_data | fields _time | limit 1",
                                 {"relativeTime": 60 * 60 * 1000}, max_wait=60, interval=2)
    print("XQL STATUS:", reply.get("status"), "QUOTA:", reply.get("remaining_quota"))
    assert reply.get("status") == "SUCCESS"
