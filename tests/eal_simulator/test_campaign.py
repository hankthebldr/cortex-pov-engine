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
        # _base() uses the C2-shaped c2_http_beacon plugin, so a live campaign
        # now also requires c2_authorized (EAL-G01 — mirrors the scenario
        # adapter consent.c2_authorized gate for safety_class:c2-framework).
        "c2_authorized": True,
    })
    c = Campaign.model_validate(spec)
    assert c.dry_run is False
    assert c.target_allowlist == ["example.test"]
    assert c.c2_authorized is True


# --- EAL-G01: C2 consent gate (mirrors scenario adapter consent.c2_authorized) ---


def _live_base() -> dict:
    spec = _base()
    spec.update({
        "dry_run": False,
        "simulation_authorized": True,
        "authorized_by": "op@example.com",
        "target_allowlist": ["example.test"],
    })
    return spec


def test_live_c2_campaign_without_c2_authorized_is_refused():
    """A live C2-shaped campaign (c2_http_beacon) must fail without c2_authorized."""
    spec = _live_base()  # uses c2_http_beacon, c2_authorized defaults False
    with pytest.raises(ValidationError, match="c2_authorized"):
        Campaign.model_validate(spec)


def test_live_c2_campaign_with_c2_authorized_is_allowed():
    """The same campaign validates once c2_authorized=true is granted."""
    spec = _live_base()
    spec["c2_authorized"] = True
    c = Campaign.model_validate(spec)
    assert c.c2_authorized is True


def test_dry_run_c2_campaign_does_not_require_c2_authorized():
    """Planning mode never emits packets, so the C2 gate is skipped in dry-run."""
    spec = _base()  # dry_run defaults True, c2 plugin, no c2_authorized
    c = Campaign.model_validate(spec)
    assert c.dry_run is True
    assert c.c2_authorized is False


def test_live_non_c2_campaign_does_not_require_c2_authorized():
    """A live campaign with no C2-shaped plugin needs no c2_authorized."""
    spec = _live_base()
    spec["steps"] = [{"step_id": "step-01", "plugin": "idp_signin_emulator",
                      "params": {}}]
    c = Campaign.model_validate(spec)
    assert c.dry_run is False
    assert c.c2_authorized is False


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
