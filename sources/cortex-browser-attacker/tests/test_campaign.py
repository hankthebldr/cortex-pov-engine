"""BrowserCampaign Pydantic model tests."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from cortex_browser_attacker.campaign import BrowserAction, BrowserCampaign


def _spec(**overrides):
    base = {
        "campaign_id": "BC-BROWSER-001",
        "name": "cred paste",
        "actions": [
            {"action": "navigate", "params": {"url": "https://example.invalid/"}}
        ],
    }
    base.update(overrides)
    return base


def test_minimum_required_fields_default_dry_run():
    c = BrowserCampaign.model_validate(_spec())
    assert c.dry_run is True
    assert c.browser_channel == "chromium"
    assert c.headless is True


def test_invalid_campaign_id_rejected():
    bad = _spec(campaign_id="bad id")
    with pytest.raises(ValidationError):
        BrowserCampaign.model_validate(bad)


def test_invalid_browser_channel_rejected():
    bad = _spec(browser_channel="firefox")
    with pytest.raises(ValidationError):
        BrowserCampaign.model_validate(bad)


def test_actions_must_be_non_empty():
    bad = _spec(actions=[])
    with pytest.raises(ValidationError):
        BrowserCampaign.model_validate(bad)


def test_live_requires_authorisation_block():
    bad = _spec(dry_run=False)
    with pytest.raises(ValidationError):
        BrowserCampaign.model_validate(bad)


def test_live_with_full_authorisation_passes():
    c = BrowserCampaign.model_validate(_spec(
        dry_run=False,
        simulation_authorized=True,
        authorized_by="tester",
        target_allowlist=["example.invalid"],
    ))
    assert c.dry_run is False


def test_action_on_error_value_must_be_continue_or_abort():
    bad = _spec(actions=[{
        "action": "navigate", "params": {"url": "https://x.invalid/"},
        "on_error": "explode",
    }])
    with pytest.raises(ValidationError):
        BrowserCampaign.model_validate(bad)


def test_action_name_normalised_lowercase():
    a = BrowserAction.model_validate({"action": "Navigate"})
    assert a.action == "navigate"
