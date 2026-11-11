"""Strict PAN-OS XML parsers: exact values on lab-shaped fixtures, raise on anything else."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from integrations.panos import codes, xmlparse as x
from integrations.panos.exceptions import PanosApiError, PanosShapeError

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "panos"


def raw(name: str) -> bytes:
    return (FIX / name).read_bytes()


def res(name: str) -> ET.Element:
    return x.parse_response(raw(name))


def wrong() -> ET.Element:
    """A syntactically fine result carrying nothing any parser expects."""
    return ET.fromstring("<result><unexpected/></result>")


# ---- parse_response / safe_fromstring ------------------------------------

def test_parse_response_success_returns_result():
    r = res("system_info__ok.xml")
    assert r.tag == "result"


def test_parse_response_success_without_result_returns_root():
    r = x.parse_response(b'<response status="success"><msg>ok</msg></response>')
    assert r.tag == "response"


def test_parse_response_error_403_maps_to_auth_rejected():
    with pytest.raises(PanosApiError) as ei:
        x.parse_response(raw("error__403.xml"))
    assert ei.value.panos_code == "403"
    assert ei.value.code == codes.PN_AUTH_REJECTED
    assert ei.value.message == "Invalid credentials."


def test_parse_response_error_joins_line_children():
    with pytest.raises(PanosApiError) as ei:
        x.parse_response(raw("error__lines.xml"))
    assert ei.value.panos_code == "12"
    assert ei.value.code == codes.PN_SHAPE_UNRECOGNISED
    assert ei.value.message == "Object not valid; Check the xpath"


def test_parse_response_error_without_code():
    with pytest.raises(PanosApiError) as ei:
        x.parse_response(raw("error__no_code.xml"))
    assert ei.value.panos_code is None
    assert ei.value.message == "Something failed"


def test_parse_response_api_error_is_a_shape_error():
    assert issubclass(PanosApiError, PanosShapeError)


@pytest.mark.parametrize("name", ["xxe.xml", "html_login_page.html"])
def test_parse_response_refuses_hostile_or_html(name):
    with pytest.raises(PanosShapeError):
        x.parse_response(raw(name))


def test_parse_response_refuses_doctype_in_any_case_and_encoding():
    with pytest.raises(PanosShapeError):
        x.parse_response(b'<!doctype x><response status="success"/>')
    with pytest.raises(PanosShapeError):
        x.parse_response(b'<!entity x "y"><response status="success"/>')
    utf16 = '<!DOCTYPE r [<!ENTITY a "b">]><response status="success"/>'.encode("utf-16")
    with pytest.raises(PanosShapeError):
        x.parse_response(utf16)


@pytest.mark.parametrize("body", [
    b"", b"not xml", b"<html><body/></html>",
    b'<response><result/></response>',                       # no status
    b'<response status="weird"><result/></response>',
])
def test_parse_response_wrong_shapes_raise(body):
    with pytest.raises(PanosShapeError):
        x.parse_response(body)


def test_safe_fromstring_accepts_str_and_bytes_and_refuses_doctype():
    assert x.safe_fromstring("<a/>").tag == "a"
    assert x.safe_fromstring(b"<a/>").tag == "a"
    with pytest.raises(PanosShapeError):
        x.safe_fromstring("<!DOCTYPE a><a/>")
    with pytest.raises(PanosShapeError):
        x.safe_fromstring("<a>")


# ---- keygen --------------------------------------------------------------

def test_parse_keygen():
    assert x.parse_keygen(res("keygen__ok.xml")).startswith("LUFRPT1")


def test_parse_keygen_negative():
    with pytest.raises(PanosShapeError):
        x.parse_keygen(wrong())
    with pytest.raises(PanosShapeError):
        x.parse_keygen(ET.fromstring("<result><key>  </key></result>"))


# ---- system info ---------------------------------------------------------

def test_parse_system_info_ok():
    s = x.parse_system_info(res("system_info__ok.xml"))
    assert s == x.SystemInfo(
        hostname="lab-fw-01", model="PA-440", serial="007051000000000",
        sw_version="10.2.8-h4", multi_vsys=False, operational_mode="normal",
        advanced_routing=False, cloud_mode="non-cloud")


def test_parse_system_info_multivsys():
    s = x.parse_system_info(res("system_info__multivsys.xml"))
    assert s.multi_vsys is True and s.advanced_routing is True
    assert s.operational_mode == "fips-cc"


def test_parse_system_info_negative():
    with pytest.raises(PanosShapeError):
        x.parse_system_info(wrong())
    with pytest.raises(PanosShapeError):  # missing serial
        x.parse_system_info(ET.fromstring(
            "<result><system><hostname>h</hostname><model>m</model></system></result>"))
    with pytest.raises(PanosShapeError):  # multi-vsys neither on nor off
        x.parse_system_info(ET.fromstring(
            "<result><system><hostname>h</hostname><model>m</model><serial>s</serial>"
            "<sw-version>1</sw-version><multi-vsys>maybe</multi-vsys>"
            "<operational-mode>normal</operational-mode><advanced-routing>off</advanced-routing>"
            "</system></result>"))


# ---- clock / timezone ----------------------------------------------------

def test_parse_clock_and_timezone():
    assert x.parse_clock(res("clock__ok.xml")) == "Tue Nov 10 22:00:20 2026"
    assert x.parse_timezone(res("timezone__central.xml")) == "US/Central"
    assert x.parse_timezone(res("config__empty.xml")) is None


def test_clock_to_utc():
    got = x.clock_to_utc("Tue Nov 10 22:00:20 2026", "US/Central")
    assert got == datetime(2026, 11, 11, 4, 0, 20, tzinfo=timezone.utc)
    # a zone abbreviation in the text and a padded day are both tolerated
    assert x.clock_to_utc("Sun Nov  1 01:02:03 CST 2026", "UTC") == \
        datetime(2026, 11, 1, 1, 2, 3, tzinfo=timezone.utc)


def test_clock_to_utc_unknown_tz_is_none_and_garbage_raises():
    assert x.clock_to_utc("Tue Nov 10 22:00:20 2026", None) is None
    assert x.clock_to_utc("Tue Nov 10 22:00:20 2026", "Mars/Olympus") is None
    with pytest.raises(PanosShapeError):
        x.clock_to_utc("yesterday-ish", "UTC")


def test_clock_negative():
    with pytest.raises(PanosShapeError):
        x.parse_clock(wrong())
    with pytest.raises(PanosShapeError):
        x.parse_clock(ET.fromstring("<result>   </result>"))
    with pytest.raises(PanosShapeError):
        x.parse_timezone(wrong())


# ---- logging status ------------------------------------------------------

def test_logging_status_active():
    cs = x.parse_logging_status(res("logging_status__active.xml"))
    assert [c.name for c in cs] == ["RECEPTR00EXAMPLE", "", ""]
    a = cs[0]
    assert a.active is True
    assert a.connection_ip == "lr-203.0.113.50"
    assert a.types["threat"].total == 917
    assert a.types["traffic"].total == 8692
    assert a.types["threat"].created == datetime(2026, 11, 10, 21, 59, 56)
    assert a.types["threat"].forwarded == datetime(2026, 11, 10, 22, 0, 0)
    assert a.types["hipmatch"] == x.LogTypeRow(created=None, forwarded=None, total=0)
    assert a.types["globalprotect"].total == 151
    assert "url" not in a.types and "wildfire" not in a.types   # not invented
    assert cs[1].active is False and cs[1].connection_ip == "lr-cms0"
    assert cs[2].connection_ip == "lr-cms1"


def test_logging_status_inactive():
    cs = x.parse_logging_status(res("logging_status__inactive.xml"))
    assert len(cs) == 2 and not any(c.active for c in cs)
    assert cs[0].types["traffic"].total == 0


def test_logging_status_threat_stale():
    a = x.parse_logging_status(res("logging_status__threat_stale.xml"))[0]
    assert a.active is True
    assert a.types["threat"].forwarded == datetime(2026, 11, 9, 4, 10, 2)
    assert a.types["traffic"].forwarded == datetime(2026, 11, 10, 22, 0, 0)


def _ls(text: str) -> ET.Element:
    return ET.fromstring(
        f"<result><show-logging-status><Conn-Info>{text}</Conn-Info></show-logging-status></result>")


def test_logging_status_negative():
    with pytest.raises(PanosShapeError):
        x.parse_logging_status(wrong())
    with pytest.raises(PanosShapeError):   # collector with no Connection Status line
        x.parse_logging_status(_ls("\nLog Collector : C1\nConnection IP : lr-1\nRate : 1 logs/sec\n"))
    with pytest.raises(PanosShapeError):   # no collector at all
        x.parse_logging_status(_ls("\nnothing useful\n"))
    with pytest.raises(PanosShapeError):   # a row that is not the known layout
        x.parse_logging_status(_ls(
            "\nLog Collector : C1\nConnection Status : lr - Active\n\n  traffic   garbage here\n"))
    with pytest.raises(PanosShapeError):   # text before any collector
        x.parse_logging_status(_ls("\nstray line\nLog Collector : C1\nConnection Status : lr - Active\n"))


# ---- device-level logging config ----------------------------------------

def test_lsf_and_eal_device():
    r = res("device_logging__lsf_on_eal_on.xml")
    assert x.parse_lsf_config(r) == x.LsfConfig(enabled=True, region="americas")
    assert x.parse_eal_device(r) is True
    r = res("device_logging__lsf_off_eal_off.xml")
    assert x.parse_lsf_config(r) == x.LsfConfig(enabled=False, region=None)
    assert x.parse_eal_device(r) is False


def test_lsf_eal_not_configured_reads_off_only_for_an_empty_result():
    r = res("config__empty.xml")
    assert x.parse_lsf_config(r) == x.LsfConfig(enabled=False, region=None)
    assert x.parse_eal_device(r) is False


def test_lsf_eal_negative():
    for fn in (x.parse_lsf_config, x.parse_eal_device):
        with pytest.raises(PanosShapeError):
            fn(wrong())
    with pytest.raises(PanosShapeError):   # enable is neither yes nor no
        x.parse_lsf_config(ET.fromstring(
            "<result><logging><logging-service-forwarding><enable>maybe</enable>"
            "</logging-service-forwarding></logging></result>"))


# ---- log forwarding profiles --------------------------------------------

def test_lfp_complete():
    (p,) = x.parse_log_forwarding_profiles(res("lfp__complete.xml"))
    assert p.name == "lab-forward-all" and p.eal is True and p.has_tag_action is False
    assert [m.log_type for m in p.matches] == ["traffic", "threat", "url", "wildfire", "data"]
    m = p.matches[0]
    assert (m.filter, m.send_to_panorama, m.syslog_profiles) == ("All Logs", True, ["lab-syslog"])


def test_lfp_missing_wildfire():
    (p,) = x.parse_log_forwarding_profiles(res("lfp__missing_wildfire.xml"))
    assert p.eal is False
    assert "wildfire" not in {m.log_type for m in p.matches}


def test_lfp_tag_action():
    (p,) = x.parse_log_forwarding_profiles(res("lfp__tag_action.xml"))
    assert p.has_tag_action is True


def test_lfp_empty_and_negative():
    assert x.parse_log_forwarding_profiles(res("config__empty.xml")) == []
    with pytest.raises(PanosShapeError):
        x.parse_log_forwarding_profiles(wrong())
    with pytest.raises(PanosShapeError):   # match entry without a log-type
        x.parse_log_forwarding_profiles(ET.fromstring(
            '<result><profiles><entry name="p"><match-list><entry name="m"><filter>f</filter>'
            "</entry></match-list></entry></profiles></result>"))


# ---- syslog --------------------------------------------------------------

def test_syslog_profiles():
    ps = x.parse_syslog_profiles(res("syslog__two_servers_udp.xml"))
    assert [p.name for p in ps] == ["lab-syslog", "empty-profile"]
    a, b = ps[0].servers
    assert a == x.SyslogServer("broker-a", "192.0.2.20", "UDP", 514, "BSD", "LOG_USER")
    assert b == x.SyslogServer("broker-b", "198.51.100.20", "UDP", 1514, "IETF", "LOG_LOCAL1")
    assert ps[1].servers == []


def test_syslog_negative():
    with pytest.raises(PanosShapeError):
        x.parse_syslog_profiles(wrong())
    with pytest.raises(PanosShapeError):   # server entry with no address
        x.parse_syslog_profiles(ET.fromstring(
            '<result><syslog><entry name="p"><server><entry name="s"><port>514</port></entry>'
            "</server></entry></syslog></result>"))
    with pytest.raises(PanosShapeError):   # non-numeric port
        x.parse_syslog_profiles(ET.fromstring(
            '<result><syslog><entry name="p"><server><entry name="s"><server>192.0.2.1</server>'
            "<port>abc</port></entry></server></entry></syslog></result>"))


# ---- decryption ----------------------------------------------------------

def test_decryption_rules():
    assert x.parse_decryption_rules(res("decryption__rules.xml")) == [
        ("decrypt-outbound", True), ("decrypt-off", False)]
    assert x.parse_decryption_rules(res("config__empty.xml")) == []


def test_decryption_negative():
    with pytest.raises(PanosShapeError):
        x.parse_decryption_rules(wrong())


# ---- registered ip -------------------------------------------------------

def test_registered_ips():
    assert x.parse_registered_ips(res("registered_ip__empty.xml")) == {}
    assert x.parse_registered_ips(res("registered_ip__tagged.xml")) == {
        "192.0.2.50": ["bypass", "lab"], "192.0.2.51": ["lab"]}


def test_registered_ips_negative():
    with pytest.raises(PanosShapeError):   # unknown child
        x.parse_registered_ips(wrong())
    with pytest.raises(PanosShapeError):   # entry without @ip
        x.parse_registered_ips(ET.fromstring("<result><entry><tag><member>t</member></tag></entry></result>"))
    with pytest.raises(PanosShapeError):   # text where a table was expected
        x.parse_registered_ips(ET.fromstring("<result>some prose</result>"))


# ---- policy / nat match --------------------------------------------------

def test_policy_match():
    pm = x.parse_policy_match(res("policy_match__alertall.xml"))
    assert pm == x.PolicyMatch(name="LAB-POV-AlertAll-Out", index=6, action="allow")
    assert x.parse_policy_match(res("policy_match__nomatch.xml")) is None


def test_policy_match_negative():
    with pytest.raises(PanosShapeError):
        x.parse_policy_match(wrong())
    with pytest.raises(PanosShapeError):   # entry without action
        x.parse_policy_match(ET.fromstring(
            '<result><rules><entry name="r"><index>1</index></entry></rules></result>'))


def test_nat_match():
    assert x.parse_nat_match(res("nat_match__outbound.xml")) == x.NatMatch(name="LAB-Outbound-NAT", index=2)
    assert x.parse_nat_match(res("nat_match__nomatch.xml")) is None


def test_nat_match_negative():
    with pytest.raises(PanosShapeError):
        x.parse_nat_match(wrong())


# ---- rule ----------------------------------------------------------------

def test_rule_group():
    r = x.parse_rule(res("rule__group_alertall.xml"))
    assert r == x.RuleConfig(name="LAB-POV-AlertAll-Out", action="allow", disabled=False,
                             group="lab-alertall", profiles={}, log_setting="lab-forward-all")


def test_rule_profiles():
    r = x.parse_rule(res("rule__profiles.xml"))
    assert r.group is None and r.log_setting is None
    assert r.profiles == {"virus": "default", "spyware": "lab-spyware",
                          "vulnerability": "strict", "wildfire-analysis": "default"}


def test_rule_deny_disabled():
    r = x.parse_rule(res("rule__deny_disabled.xml"))
    assert (r.action, r.disabled, r.group, r.profiles) == ("deny", True, None, {})


def test_rule_negative():
    with pytest.raises(PanosShapeError):
        x.parse_rule(wrong())
    with pytest.raises(PanosShapeError) as ei:   # empty result = object absent, distinct code
        x.parse_rule(ET.fromstring('<result total-count="0" count="0"/>'))
    assert ei.value.code == codes.PN_OBJECT_NOT_FOUND
    with pytest.raises(PanosShapeError):   # no action
        x.parse_rule(ET.fromstring('<result><entry name="r"/></result>'))


# ---- profile group / profile actions -------------------------------------

def test_profile_group():
    assert x.parse_profile_group(res("profile_group__alertall.xml")) == {
        "virus": "lab-av", "spyware": "lab-spyware", "vulnerability": "lab-vuln",
        "url-filtering": "lab-url", "wildfire-analysis": "lab-wf",
        "file-blocking": "basic file blocking"}


def test_profile_group_negative():
    with pytest.raises(PanosShapeError):
        x.parse_profile_group(wrong())


def test_spyware_actions_and_dns_security():
    pa = x.parse_profile_actions(res("profile_spyware__dns_sinkhole.xml"), "spyware")
    assert pa.by_severity == {"critical": "block", "high": "block", "medium": "alert",
                              "low": "alert", "informational": "alert"}   # first rule wins
    assert pa.dns_security == {
        "default-paloalto-dns": "sinkhole", "pan-dns-sec-cc": "sinkhole",
        "pan-dns-sec-malware": "signature-default", "pan-dns-sec-phishing": "block"}


def test_vulnerability_any_expands_and_default_is_signature_default():
    pa = x.parse_profile_actions(res("profile_vulnerability__default.xml"), "vulnerability")
    assert set(pa.by_severity) == {"critical", "high", "medium", "low", "informational"}
    assert set(pa.by_severity.values()) == {"signature-default"}
    assert pa.dns_security == {}


def test_virus_decoders():
    pa = x.parse_profile_actions(res("profile_virus__decoders.xml"), "virus")
    assert pa.by_decoder == {"http": "signature-default", "smtp": "alert", "ftp": "block"}
    assert pa.by_severity == {}


def test_profile_actions_negative():
    with pytest.raises(PanosShapeError):
        x.parse_profile_actions(wrong(), "spyware")
    with pytest.raises(PanosShapeError):   # action nobody knows
        x.parse_profile_actions(ET.fromstring(
            '<result><entry name="p"><rules><entry name="r"><severity><member>high</member></severity>'
            "<action><weird/></action></entry></rules></entry></result>"), "spyware")
    with pytest.raises(PanosShapeError):   # rule without a severity
        x.parse_profile_actions(ET.fromstring(
            '<result><entry name="p"><rules><entry name="r"><action><alert/></action></entry></rules></entry></result>'),
            "spyware")
    with pytest.raises(ValueError):
        x.parse_profile_actions(res("profile_spyware__dns_sinkhole.xml"), "url-filtering")


def test_classify_action():
    assert x.classify_action("default") == "signature-default"
    for a in ("drop", "reset-client", "reset-server", "reset-both", "block-ip", "block"):
        assert x.classify_action(a) == "block"
    for a in ("alert", "allow", "sinkhole"):
        assert x.classify_action(a) == a
    with pytest.raises(PanosShapeError):
        x.classify_action("weird")


# ---- user mapping / sessions / fib / zones --------------------------------

def test_user_mapping():
    assert x.parse_user_mapping(res("user_mapping__one.xml")) == ["lab\\analyst"]
    assert x.parse_user_mapping(res("user_mapping__empty.xml")) == []


def test_user_mapping_negative():
    with pytest.raises(PanosShapeError):
        x.parse_user_mapping(wrong())
    with pytest.raises(PanosShapeError):
        x.parse_user_mapping(ET.fromstring("<result><entry><ip>192.0.2.1</ip></entry></result>"))


def test_session_count():
    assert x.parse_session_count(res("session_count__three.xml")) == 3


def test_session_count_negative():
    with pytest.raises(PanosShapeError):
        x.parse_session_count(wrong())
    with pytest.raises(PanosShapeError):
        x.parse_session_count(ET.fromstring("<result>lots</result>"))
    with pytest.raises(PanosShapeError):
        x.parse_session_count(ET.fromstring("<result/>"))


def test_fib():
    assert x.parse_fib(res("fib__ok.xml")) == "ethernet1/1"
    assert x.parse_fib(res("fib__noroute.xml")) is None


def test_fib_negative():
    with pytest.raises(PanosShapeError):
        x.parse_fib(wrong())


def test_zone_userid():
    assert x.parse_zone_userid(res("zone__userid_on.xml")) is True
    assert x.parse_zone_userid(res("zone__userid_off.xml")) is False


def test_zone_userid_negative():
    with pytest.raises(PanosShapeError):
        x.parse_zone_userid(wrong())


def test_interface_zone():
    r = res("zones__list.xml")
    assert x.parse_interface_zone(r, "ethernet1/1") == "outside"
    assert x.parse_interface_zone(r, "ethernet1/3.10") == "inside"
    assert x.parse_interface_zone(r, "ethernet9/9") is None


def test_interface_zone_negative():
    with pytest.raises(PanosShapeError):
        x.parse_interface_zone(wrong(), "ethernet1/1")
