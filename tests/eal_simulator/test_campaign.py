"""Campaign Pydantic schema validation tests."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from eal_simulator import Campaign


def _base() -> dict:
    return {
        "campaign_id": "CMP-NDR-001",
        "name": "X",
        "steps": [{"step_id": "step-01", "plugin": "c2_http_beacon", "params": {}}],
    }


def test_minimum_valid_campaign_defaults_dry_run_true():
    c = Campaign.model_validate(_base())
    assert c.dry_run is True
    assert c.simulation_authorized is False


def test_invalid_campaign_id_format_rejected():
    bad = _base()
    bad["campaign_id"] = "bad id"
    with pytest.raises(ValidationError):
        Campaign.model_validate(bad)


def test_step_id_must_match_format():
    bad = _base()
    bad["steps"][0]["step_id"] = "01"
    with pytest.raises(ValidationError):
        Campaign.model_validate(bad)


def test_duplicate_step_ids_rejected():
    bad = _base()
    bad["steps"].append(bad["steps"][0])
    with pytest.raises(ValidationError):
        Campaign.model_validate(bad)


def test_live_requires_authorisation_block():
    bad = _base()
    bad["dry_run"] = False
    with pytest.raises(ValidationError):
        Campaign.model_validate(bad)


def test_live_with_full_authorisation_passes():
    spec = _base()
    spec.update({
        "dry_run": False,
        "simulation_authorized": True,
        "authorized_by": "op@example.com",
        "target_allowlist": ["example.test"],
    })
    c = Campaign.model_validate(spec)
    assert c.dry_run is False
    assert c.target_allowlist == ["example.test"]


def test_on_error_must_be_continue_or_abort():
    bad = _base()
    bad["steps"][0]["on_error"] = "explode"
    with pytest.raises(ValidationError):
        Campaign.model_validate(bad)


def test_steps_non_empty_required():
    bad = _base()
    bad["steps"] = []
    with pytest.raises(ValidationError):
        Campaign.model_validate(bad)
