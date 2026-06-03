# tests/integration/xsiam/test_loader.py
from __future__ import annotations

import pytest

from tests.integration.xsiam.conftest import seed_tenant  # helper (Task 8 conftest)


@pytest.mark.asyncio
async def test_loader_builds_client(db_session):
    from integrations.xsiam.loader import load_xsiam_client
    await seed_tenant(db_session, name="acme",
                      base_url="https://api-acme.xdr.us.paloaltonetworks.com",
                      api_key="k-very-long-secret-value-1234567890")
    client = await load_xsiam_client(db_session, "acme")
    assert client is not None


@pytest.mark.asyncio
async def test_loader_missing_tenant_raises_config_error(db_session):
    from integrations.xsiam.loader import load_xsiam_client
    from integrations.xsiam.exceptions import XsiamConfigError
    with pytest.raises(XsiamConfigError):
        await load_xsiam_client(db_session, "nope")


@pytest.mark.asyncio
async def test_loader_rejects_bad_config(db_session):
    from integrations.xsiam.loader import load_xsiam_client
    from integrations.xsiam.exceptions import XsiamConfigError
    await seed_tenant(db_session, name="bad",
                      base_url="http://evil.example.com",  # invalid -> rejected on load
                      api_key="k-very-long-secret-value-1234567890")
    with pytest.raises(XsiamConfigError):
        await load_xsiam_client(db_session, "bad")
