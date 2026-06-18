# tests/integration/xsiam/test_config.py
from __future__ import annotations

import pytest


def test_valid_tenant_config():
    from integrations.xsiam.config import XsiamTenantConfig, AuthMode
    cfg = XsiamTenantConfig(
        base_url="https://api-acme.xdr.us.paloaltonetworks.com",
        region="us",
        auth_mode="standard",
        api_key_id="42",
    )
    assert cfg.auth_mode is AuthMode.standard
    assert cfg.base_url.startswith("https://api-")


@pytest.mark.parametrize("bad_url", [
    "http://api-acme.xdr.us.paloaltonetworks.com",      # not https
    "https://acme.example.com",                          # not a PANW tenant FQDN
    "https://api-acme.xdr.us.paloaltonetworks.com.evil.com",  # suffix smuggling
    "not-a-url",
])
def test_rejects_dangerous_base_url(bad_url):
    from pydantic import ValidationError
    from integrations.xsiam.config import XsiamTenantConfig
    with pytest.raises(ValidationError):
        XsiamTenantConfig(base_url=bad_url, region="us", auth_mode="standard", api_key_id="1")


def test_rejects_unknown_auth_mode():
    from pydantic import ValidationError
    from integrations.xsiam.config import XsiamTenantConfig
    with pytest.raises(ValidationError):
        XsiamTenantConfig(base_url="https://api-x.xdr.us.paloaltonetworks.com",
                          region="us", auth_mode="sso", api_key_id="1")
