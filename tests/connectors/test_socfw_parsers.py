"""Pinned SOC Framework facts and parsers (soc-optimization-unified 3.31.4)."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from connectors import socfw

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "socfw"
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)


def load(name):
    return json.loads((FIX / name).read_text())


def test_triage_prefix_matches_pinned_framework_comment():
    text = (FIX / "triage_resolve_comment.txt").read_text().splitlines()[1]
    assert text.startswith(socfw.TRIAGE_RESOLVE_COMMENT_PREFIX)


def test_triage_comment_fixture_is_versioned():
    assert (FIX / "triage_resolve_comment.txt").read_text().splitlines()[0] == socfw.FRAMEWORK_PINNED_VERSION


def test_installed_packs_parsed():
    packs = socfw.parse_installed_packs(load("installed_packs.json"))
    assert packs[socfw.CORE_PACK_ID] == "3.31.4"


def test_unrecognized_packs_shape_raises():
    with pytest.raises(socfw.ResponseLayerShapeError):
        socfw.parse_installed_packs({"unexpected": 1})


def test_pack_row_missing_version_raises():
    with pytest.raises(socfw.ResponseLayerShapeError):
        socfw.parse_installed_packs([{"id": "x"}])


def test_list_names_parsed_and_bad_shape_raises():
    assert "SOCFWTenantConfig" in socfw.parse_list_names(load("list_names.json"))
    with pytest.raises(socfw.ResponseLayerShapeError):
        socfw.parse_list_names({"names": []})


def test_leftover_lists_without_core_pack_is_absent():
    rl = socfw.assemble_response_layer(packs={"soc-crowdstrike-falcon": "2.2.7"},
        list_names=frozenset({"SOCFWTenantConfig"}), tenant_config=None, evidence=None,
        sources_used=["packs", "lists"], read_at=NOW)
    assert (rl.state, rl.code, rl.leftover_lists) == ("ABSENT", "RESPONSE_LAYER_ABSENT", True)


def test_packs_unreadable_is_unknown_not_absent():
    rl = socfw.assemble_response_layer(packs=None, list_names=None, tenant_config=None,
        evidence=None, sources_used=[], read_at=NOW, shape_error="unrecognized")
    assert (rl.state, rl.code) == ("UNKNOWN", "RESPONSE_LAYER_SHAPE_UNRECOGNIZED")


def test_pov_mode_parsed_with_ai_close_live():
    v = socfw.parse_tenant_config(load("tenant_config.json"), load("config_modes.json"))
    assert v.mode == "pov" and v.ai_close_live is True
    assert v.triage_job_on is True


def test_triage_score_off_parsed():
    doc = load("tenant_config.json"); doc["triage"] = {"score_threshold": "off"}
    v = socfw.parse_tenant_config(doc, load("config_modes.json"))
    assert v.triage_score_off is True and v.triage_score_threshold is None


def test_triage_numbers_coerced_from_strings():
    doc = load("tenant_config.json"); doc["triage"] = {"score_threshold": "25", "window_hours": "12"}
    v = socfw.parse_tenant_config(doc, load("config_modes.json"))
    assert (v.triage_score_threshold, v.triage_window_hours, v.triage_score_off) == (25, 12.0, False)


@pytest.mark.parametrize("triage", [
    "100", {"score_threshold": "high"}, {"window_hours": 0}, {"score_threshold": -1}])
def test_bad_triage_raises(triage):
    doc = load("tenant_config.json"); doc["triage"] = triage
    with pytest.raises(socfw.ResponseLayerShapeError):
        socfw.parse_tenant_config(doc, load("config_modes.json"))


def test_unreadable_shadow_value_raises():
    doc = load("tenant_config.json"); doc["shadow"]["soc-close-issue"]["ai"] = "maybe"
    with pytest.raises(socfw.ResponseLayerShapeError):
        socfw.parse_tenant_config(doc, load("config_modes.json"))


def test_non_object_config_raises():
    with pytest.raises(socfw.ResponseLayerShapeError):
        socfw.parse_tenant_config([], load("config_modes.json"))


def test_no_shadow_overrides_is_not_asserted_as_framework_default():
    v = socfw.parse_tenant_config({}, load("config_modes.json"))
    assert v.mode == socfw.MODE_NO_SHADOW != "default" and v.ai_close_live is None


def test_ai_close_back_in_shadow_is_star_and_triage():
    doc = load("tenant_config.json"); doc["shadow"]["soc-close-issue"]["ai"] = True
    doc["shadow"]["soc-assign-case"]["automation"] = True
    v = socfw.parse_tenant_config(doc, load("config_modes.json"))
    assert v.mode == "star_and_triage" and v.ai_close_live is False


def test_execution_evidence_proves_triage_live():
    ev = socfw.summarize_execution_rows([{"action_taken": "auto_triage_closed",
                                          "execution_mode": "production", "n": 3}])
    rl = socfw.assemble_response_layer(packs={socfw.CORE_PACK_ID: "3.31.4"},
        list_names=frozenset(), tenant_config=None, evidence=ev,
        sources_used=["packs", "lists", "xql"], read_at=NOW)
    assert rl.state == "PRESENT_UNREAD" and rl.auto_triage.live is True
    assert rl.auto_triage.live_source == "xql_evidence"
    # The lists were not read: no window or threshold is claimed, not even a default.
    at = rl.auto_triage
    assert (at.window_hours, at.window_source) == (None, "unknown")
    assert (at.score_threshold, at.threshold_source) == (None, "unknown")


def test_shadow_rows_and_analyst_closes_are_not_evidence():
    ev = socfw.summarize_execution_rows([
        {"action_taken": "auto_triage_would_close", "execution_mode": "shadow", "n": 9},
        {"action_taken": "soc-close-issue", "execution_mode": "production", "n": "4", "action_actor": "analyst"},
        {"action_taken": "soc-close-issue", "execution_mode": "production", "n": "2", "action_actor": "ai"}])
    assert (ev.triage_closed_production, ev.ai_close_production) == (0, 2)


def test_execution_row_missing_n_raises():
    with pytest.raises(socfw.ResponseLayerShapeError):
        socfw.summarize_execution_rows([{"action_taken": "x", "execution_mode": "production"}])


def test_pov_layer_reads_present_with_defaults():
    v = socfw.parse_tenant_config(load("tenant_config.json"), load("config_modes.json"))
    rl = socfw.assemble_response_layer(packs=socfw.parse_installed_packs(load("installed_packs.json")),
        list_names=frozenset(socfw.CONFIG_LISTS), tenant_config=v, evidence=None,
        sources_used=["packs", "lists"], read_at=NOW)
    assert (rl.state, rl.code, rl.framework_version, rl.mode) == ("PRESENT", "RESPONSE_LAYER_OK", "3.31.4", "pov")
    assert rl.ai_close == "live" and rl.auto_triage.live is True
    assert rl.auto_triage.window_hours == socfw.TRIAGE_DEFAULT_WINDOW_HOURS
    assert rl.auto_triage.score_threshold == socfw.TRIAGE_DEFAULT_SCORE_THRESHOLD
    assert set(rl.packs) == {socfw.CORE_PACK_ID, "soc-framework-nist-ir"}


def test_round_trip_dict():
    rl = socfw.assemble_response_layer(packs={socfw.CORE_PACK_ID: "3.31.4"},
        list_names=frozenset(socfw.CONFIG_LISTS),
        tenant_config=socfw.parse_tenant_config(load("tenant_config.json"), load("config_modes.json")),
        evidence=None, sources_used=["packs", "lists"], read_at=NOW)
    assert socfw.ResponseLayer.from_dict(rl.to_dict()) == rl
    json.dumps(rl.to_dict())


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf", float("nan"), float("inf")])
@pytest.mark.parametrize("key", ["score_threshold", "window_hours"])
def test_non_finite_triage_raises(key, bad):
    doc = load("tenant_config.json"); doc["triage"] = {key: bad}
    with pytest.raises(socfw.ResponseLayerShapeError, match=key):
        socfw.parse_tenant_config(doc, load("config_modes.json"))


@pytest.mark.parametrize("bad", ["nan", "inf", float("inf")])
def test_non_finite_execution_count_raises(bad):
    with pytest.raises(socfw.ResponseLayerShapeError):
        socfw.summarize_execution_rows(
            [{"action_taken": "auto_triage_closed", "execution_mode": "production", "n": bad}])


@pytest.mark.parametrize("blank", ["", None])
def test_blank_override_is_not_overridden(blank):
    doc = load("tenant_config.json"); doc["triage"] = {"score_threshold": blank}
    v = socfw.parse_tenant_config(doc, load("config_modes.json"))
    assert v.triage_overridden is False
    doc2 = {"triage": {"score_threshold": blank}}  # no override, no job switch known
    v2 = socfw.parse_tenant_config(doc2, load("config_modes.json"))
    rl = socfw.assemble_response_layer(packs={socfw.CORE_PACK_ID: "3.31.4"},
        list_names=frozenset(), tenant_config=v2, evidence=None, sources_used=["packs"], read_at=NOW)
    at = rl.auto_triage
    assert (at.live, at.live_source) == (None, "unknown")
    assert at.window_source == at.threshold_source == "shipped_default"


def test_real_override_sets_tenant_config_source():
    v = socfw.parse_tenant_config({"triage": {"window_hours": 12}}, load("config_modes.json"))
    rl = socfw.assemble_response_layer(packs={socfw.CORE_PACK_ID: "3.31.4"},
        list_names=frozenset(), tenant_config=v, evidence=None, sources_used=["packs"], read_at=NOW)
    assert v.triage_overridden is True
    # Per value: the window was read from the tenant, the threshold was not.
    assert (rl.auto_triage.window_hours, rl.auto_triage.window_source) == (12.0, "tenant_config")
    assert (rl.auto_triage.score_threshold, rl.auto_triage.threshold_source) == (
        socfw.TRIAGE_DEFAULT_SCORE_THRESHOLD, "shipped_default")


# ── I-3: per-value provenance. Assumed and inferred values are never shown as read. ──

def _pov_layer(doc=None, evidence=None):
    v = socfw.parse_tenant_config(doc or load("tenant_config.json"), load("config_modes.json"))
    return socfw.assemble_response_layer(packs={socfw.CORE_PACK_ID: "3.31.4"},
        list_names=frozenset(socfw.CONFIG_LISTS), tenant_config=v, evidence=evidence,
        sources_used=["packs", "lists"], read_at=NOW)


def test_canonical_pov_config_marks_live_inferred_and_settings_shipped_default():
    # No triage section: 6 h / 40 are CortexSim's copies of the framework defaults
    # (the framework reads them from SOCOptimizationConfig_V3, which is not read),
    # and "live" comes from the mode's SHIPPED jobs map, not the tenant's schedule.
    at = _pov_layer().auto_triage
    assert (at.live, at.live_source) == (True, "mode_inferred")
    assert (at.window_hours, at.window_source) == (socfw.TRIAGE_DEFAULT_WINDOW_HOURS, "shipped_default")
    assert (at.score_threshold, at.threshold_source) == (
        socfw.TRIAGE_DEFAULT_SCORE_THRESHOLD, "shipped_default")


def test_execution_evidence_outranks_mode_inference_for_live():
    ev = socfw.ExecutionEvidence(triage_closed_production=2, ai_close_production=0)
    assert _pov_layer(evidence=ev).auto_triage.live_source == "xql_evidence"


def test_score_off_is_a_tenant_config_threshold():
    doc = load("tenant_config.json"); doc["triage"] = {"score_threshold": "off"}
    at = _pov_layer(doc).auto_triage
    assert (at.score_off, at.score_threshold, at.threshold_source) == (True, None, "tenant_config")


def test_provenance_round_trips_and_unknown_source_is_rejected():
    rl = _pov_layer()
    d = rl.to_dict()
    assert d["auto_triage"]["live_source"] == "mode_inferred"
    assert "source" not in d["auto_triage"]  # one conflated source is gone
    assert socfw.ResponseLayer.from_dict(d) == rl
    d["auto_triage"]["window_source"] = "guessed"
    with pytest.raises(socfw.ResponseLayerShapeError):
        socfw.ResponseLayer.from_dict(d)


def test_auto_triage_summary_says_what_was_not_read():
    s = socfw.auto_triage_summary(_pov_layer().to_dict()["auto_triage"])
    assert "live (inferred from mode, not read)" in s
    assert "6 h (shipped default, not read)" in s
    assert "score <= 40 (shipped default, not read)" in s
    unread = socfw.auto_triage_summary({"live": True, "live_source": "xql_evidence",
                                        "window_hours": None, "score_threshold": None,
                                        "score_off": False})
    assert "window not read" in unread and "threshold not read" in unread
