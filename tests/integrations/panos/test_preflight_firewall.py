"""Firewall-wide NGFW preflight (spec §5.1, §5.2) over the fixture router.

Every test also checks the report invariants: each stage id exactly once and in
order, queries_issued equal to the calls the transport saw, tenant_verified 0, and a
remediation on every non-ok stage."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from connectors.preflight import BLOCKED, DEGRADED, OK, SKIPPED
from integrations.panos import codes
from integrations.panos import preflight as pf
from integrations.panos.preflight import (
    FIREWALL_STAGE_IDS, PROVES_TEMPLATE, REMEDIATIONS, run_panos_preflight)
from integrations.panos.sources import AgentRef

from .conftest import FIXTURES

DEV = "/config/devices/entry[@name='localhost.localdomain']"
VSYS = DEV + "/vsys/entry[@name='vsys1']"
SYSINFO = "<show><system><info /></system></show>"
CLOCK = "<show><clock /></show>"
LOGSTATUS = "<show><logging-status /></show>"
REGIP = "<show><object><registered-ip><all /></registered-ip></object></show>"
TZ = DEV + "/deviceconfig/system/timezone"
LOGGING = DEV + "/deviceconfig/setting/logging"
SYSLOG_VSYS = VSYS + "/log-settings/syslog"
SYSLOG_SHARED = "/config/shared/log-settings/syslog"
LFP_VSYS = VSYS + "/log-settings/profiles"
LFP_SHARED = "/config/shared/log-settings/profiles"
DECRYPT = VSYS + "/rulebase/decryption/rules"

# show clock in clock__fresh.xml is 22:05:00 US/Eastern (EST) = 03:05:00 UTC.
NOW = datetime(2026, 11, 11, 3, 5, 10, tzinfo=timezone.utc)
KEY = "LUFRPT1-canary-key-9d2e"
AGENT = AgentRef(agent_id="a1", hostname="pov-target", last_ip="192.0.2.50")

BASE = {
    SYSINFO: "system_info__ok.xml",
    CLOCK: "clock__fresh.xml",
    TZ: "timezone__eastern.xml",
    LOGGING: "device_logging__lsf_on_eal_on.xml",
    LOGSTATUS: "logging_status__active.xml",
    SYSLOG_VSYS: "config__empty.xml",
    SYSLOG_SHARED: "config__empty.xml",
    LFP_VSYS: "lfp__complete.xml",
    LFP_SHARED: "config__empty.xml",
    DECRYPT: "decryption__rules.xml",
    REGIP: "registered_ip__empty.xml",
}


async def run(fake_firewall, overrides=None, *, config=None, now=NOW, agents=(AGENT,), key=KEY,
              max_calls=120):
    fw = fake_firewall({**BASE, **(overrides or {})})
    cfg = {"host": "192.0.2.1", **(config or {})}
    report = await run_panos_preflight(cfg, key, firewall="lab-fw", agents=list(agents),
                                       transport=fw.transport, now=now, max_calls=max_calls)
    check_invariants(report, fw)
    return report, fw


def check_invariants(report, fw):
    assert [s.id for s in report.stages] == list(FIREWALL_STAGE_IDS)
    assert report.queries_issued == len(fw.calls)
    d = report.to_dict()
    assert d["tenant_verified"] == 0
    assert d["kind"] == "panos_ngfw"
    assert d["config_source"] == "running"
    assert d["proves"] == PROVES_TEMPLATE.format(n=len(fw.calls))
    for s in report.stages:
        if s.status != OK:
            assert s.remediation and s.remediation.strip(), s.id
    json.dumps(d)  # serialisable


def stage(report, sid):
    return next(s for s in report.stages if s.id == sid)


def err(msg: str) -> bytes:
    return f'<response status="error"><msg><line>{msg}</line></msg></response>'.encode()


# --------------------------------------------------------------- happy path / shape


@pytest.mark.asyncio
async def test_healthy_sls_firewall(fake_firewall):
    report, fw = await run(fake_firewall)
    by = {s.id: s for s in report.stages}
    for sid in ("config", "dns_tls", "auth", "system", "clock", "forwarding_sls",
                "eal_device", "log_forwarding_profiles", "decryption", "registered_ip"):
        assert by[sid].status == OK, (sid, by[sid].code, by[sid].detail)
    assert by["forwarding_syslog"].status == SKIPPED
    assert by["forwarding_syslog"].code == codes.PN_PATH_NOT_SELECTED
    assert report.panos_version == "10.2.8-h4"
    d = report.to_dict()
    assert d["firewall"] == "lab-fw" and d["mgmt_host"] == "192.0.2.1"
    assert d["proves"].startswith(f"Firewall configuration as read by {len(fw.calls)} live call(s).")
    assert "does not show that the tenant ingested or stitched anything" in d["proves"]
    # LSF is selected, so the syslog profiles are still read and reported.
    assert SYSLOG_VSYS in fw.keys() and SYSLOG_SHARED in fw.keys()
    # One read of deviceconfig/setting/logging serves both LSF and device EAL.
    assert fw.keys().count(LOGGING) == 1
    assert by["clock"].extra["skew_seconds"] == 10


@pytest.mark.asyncio
async def test_to_dict_keys(fake_firewall):
    report, _ = await run(fake_firewall)
    assert set(report.to_dict()) == {
        "firewall", "kind", "mgmt_host", "overall", "stages", "sources", "queries_issued",
        "panos_version", "config_source", "proves", "tenant_verified"}


@pytest.mark.asyncio
async def test_invalid_config_blocks_without_calls(fake_firewall):
    report, fw = await run(fake_firewall, config={"host": "https://192.0.2.1"})
    assert stage(report, "config").status == BLOCKED
    assert stage(report, "config").code == codes.PN_CONFIG_INVALID
    assert fw.calls == [] and report.queries_issued == 0
    for s in report.stages[1:]:
        assert s.status == SKIPPED and s.code == codes.PN_SKIPPED_CONFIG
    assert report.overall == BLOCKED
    assert report.to_dict()["proves"].startswith("Firewall configuration as read by 0 live call(s)")


@pytest.mark.asyncio
async def test_missing_api_key_blocks_config(fake_firewall):
    report, fw = await run(fake_firewall, key="")
    assert stage(report, "config").code == codes.PN_CONFIG_INVALID
    assert fw.calls == []


# --------------------------------------------------------------- reachability / auth


def _raise_connect(request):
    raise httpx.ConnectError(f"connect failed; X-PAN-KEY: {KEY}", request=request)


@pytest.mark.asyncio
async def test_unreachable_short_circuits(fake_firewall):
    report, fw = await run(fake_firewall, {SYSINFO: _raise_connect})
    assert stage(report, "dns_tls").status == BLOCKED
    assert stage(report, "dns_tls").code == codes.PN_UNREACHABLE
    for s in report.stages[2:]:
        assert (s.status, s.code) == (SKIPPED, codes.PN_SKIPPED_UNREACHABLE), s.id
    assert report.queries_issued == 1
    assert report.overall == BLOCKED
    # firewall-wide part short-circuited: no source checks, but not an empty list either
    assert report.sources and report.sources[0]["status"] == SKIPPED


@pytest.mark.asyncio
async def test_non_2xx_is_unreachable_not_auth_ok(fake_firewall):
    report, _ = await run(fake_firewall, {SYSINFO: 502})
    assert stage(report, "dns_tls").code == codes.PN_UNREACHABLE
    assert stage(report, "auth").code == codes.PN_SKIPPED_UNREACHABLE


@pytest.mark.asyncio
async def test_auth_rejected_status_error_403(fake_firewall):
    report, fw = await run(fake_firewall, {SYSINFO: "error__403.xml"})
    assert stage(report, "dns_tls").status == OK
    assert (stage(report, "auth").status, stage(report, "auth").code) == (BLOCKED, codes.PN_AUTH_REJECTED)
    for s in report.stages[3:]:
        assert (s.status, s.code) == (SKIPPED, codes.PN_SKIPPED_AUTH), s.id
    assert report.queries_issued == 1


@pytest.mark.asyncio
async def test_auth_rejected_http_403(fake_firewall):
    report, _ = await run(fake_firewall, {SYSINFO: 403})
    assert stage(report, "auth").code == codes.PN_AUTH_REJECTED
    assert stage(report, "clock").code == codes.PN_SKIPPED_AUTH


@pytest.mark.asyncio
async def test_html_login_page_is_never_auth_ok(fake_firewall):
    report, fw = await run(fake_firewall, {SYSINFO: "html_login_page.html"})
    assert stage(report, "auth").status == BLOCKED
    assert stage(report, "auth").code == codes.PN_SHAPE_UNRECOGNISED
    assert stage(report, "system").code == codes.PN_SHAPE_UNRECOGNISED
    # not a short-circuit: the rest still ran
    assert CLOCK in fw.keys() and REGIP in fw.keys()


@pytest.mark.asyncio
async def test_tls_unverified_is_degraded_even_when_all_else_ok(fake_firewall):
    report, _ = await run(fake_firewall, config={"verify_tls": False})
    s = stage(report, "dns_tls")
    assert (s.status, s.code) == (DEGRADED, codes.PN_TLS_UNVERIFIED)
    assert s.remediation == REMEDIATIONS[codes.PN_TLS_UNVERIFIED]
    assert stage(report, "auth").status == OK


@pytest.mark.asyncio
async def test_call_budget_exhausted_is_never_auth_ok(fake_firewall):
    report, fw = await run(fake_firewall, max_calls=0)
    assert fw.calls == [] and report.queries_issued == 0
    for s in report.stages[1:]:
        assert s.status == BLOCKED and s.code == codes.PN_CALL_BUDGET, (s.id, s.status, s.code)
    assert report.overall == BLOCKED


@pytest.mark.asyncio
async def test_call_budget_mid_run_blocks_later_stages_only(fake_firewall):
    report, fw = await run(fake_firewall, max_calls=3)  # sysinfo, clock, timezone
    assert len(fw.calls) == 3
    assert stage(report, "clock").status == OK
    for sid in FIREWALL_STAGE_IDS[5:]:
        if sid == "forwarding_syslog":
            continue  # not selected under auto: its blocked read stays blocked
        assert stage(report, sid).code == codes.PN_CALL_BUDGET, sid


# --------------------------------------------------------------- Review Focus 2


def _status_error_with_key(request):
    return httpx.Response(200, content=err(f"bad key {KEY}"))


@pytest.mark.asyncio
async def test_api_key_never_in_report_transport_error(fake_firewall):
    report, _ = await run(fake_firewall, {SYSINFO: _raise_connect})
    assert KEY not in json.dumps(report.to_dict())


@pytest.mark.asyncio
async def test_api_key_never_in_report_firewall_error_text(fake_firewall):
    report, _ = await run(fake_firewall, {DECRYPT: _status_error_with_key,
                                          LFP_VSYS: _status_error_with_key})
    assert stage(report, "decryption").status == BLOCKED
    assert KEY not in json.dumps(report.to_dict())


@pytest.mark.asyncio
async def test_api_key_scrubbed_even_if_a_stage_leaks_it(fake_firewall, monkeypatch):
    async def leaky(ctx, run):
        from connectors.preflight import Stage
        return Stage("registered_ip", DEGRADED, codes.PN_REGISTERED_TAGS_PRESENT,
                     f"oops {KEY}", remediation=f"r {KEY}", extra={"x": [KEY], "y": {"z": KEY}})
    monkeypatch.setattr(pf, "_stage_registered_ip", leaky)
    report, _ = await run(fake_firewall)
    assert KEY not in json.dumps(report.to_dict())


# --------------------------------------------------------------- system


@pytest.mark.asyncio
async def test_multi_vsys_without_explicit_vsys(fake_firewall):
    report, _ = await run(fake_firewall, {SYSINFO: "system_info__multivsys.xml"})
    s = stage(report, "system")
    assert (s.status, s.code) == (DEGRADED, codes.PN_MULTI_VSYS)
    assert {i["code"] for i in s.extra["issues"]} == {codes.PN_MULTI_VSYS, codes.PN_NOT_NORMAL_MODE}


@pytest.mark.asyncio
async def test_multi_vsys_with_explicit_vsys_reports_only_mode(fake_firewall):
    report, _ = await run(fake_firewall, {SYSINFO: "system_info__multivsys.xml"},
                          config={"vsys": "vsys1"})
    s = stage(report, "system")
    assert (s.status, s.code) == (DEGRADED, codes.PN_NOT_NORMAL_MODE)


# --------------------------------------------------------------- clock


@pytest.mark.asyncio
async def test_clock_skew_45s_degraded(fake_firewall):
    report, _ = await run(fake_firewall, now=NOW + timedelta(seconds=35))
    s = stage(report, "clock")
    assert (s.status, s.code) == (DEGRADED, codes.PN_CLOCK_SKEW)
    assert s.extra["skew_seconds"] == 45


@pytest.mark.asyncio
async def test_clock_no_timezone_configured_is_degraded(fake_firewall):
    report, _ = await run(fake_firewall, {TZ: "config__empty.xml"})
    s = stage(report, "clock")
    assert (s.status, s.code) == (DEGRADED, codes.PN_CLOCK_TZ_UNKNOWN)


@pytest.mark.asyncio
async def test_clock_unknown_timezone_name_is_degraded(fake_firewall):
    tz = b'<response status="success"><result total-count="1" count="1"><timezone>Mars/Olympus</timezone></result></response>'
    report, _ = await run(fake_firewall, {TZ: tz})
    s = stage(report, "clock")
    assert (s.status, s.code) == (DEGRADED, codes.PN_CLOCK_TZ_UNKNOWN)


@pytest.mark.asyncio
async def test_clock_tzdata_missing_is_degraded(fake_firewall, monkeypatch):
    monkeypatch.setattr(pf, "clock_to_utc", lambda text, tz: None)
    report, _ = await run(fake_firewall)
    s = stage(report, "clock")
    assert (s.status, s.code) == (DEGRADED, codes.PN_CLOCK_TZ_UNKNOWN)


# --------------------------------------------------------------- forwarding path matrix


@pytest.mark.asyncio
async def test_auto_syslog_only(fake_firewall):
    report, fw = await run(fake_firewall, {LOGGING: "device_logging__lsf_off_eal_off.xml",
                                           SYSLOG_VSYS: "syslog__two_servers_udp.xml"})
    sls, sys_ = stage(report, "forwarding_sls"), stage(report, "forwarding_syslog")
    assert (sls.status, sls.code) == (SKIPPED, codes.PN_PATH_NOT_SELECTED)
    assert (sys_.status, sys_.code) == (DEGRADED, codes.PN_SYSLOG_DELIVERY_UNVERIFIED)
    assert codes.PN_SYSLOG_UDP in {i["code"] for i in sys_.extra["issues"]}
    servers = sys_.extra["profiles"][0]["servers"]
    assert servers[0] == {"name": "broker-a", "server": "192.0.2.20", "transport": "UDP",
                          "port": 514, "format": "BSD", "facility": "LOG_USER"}
    assert LOGSTATUS not in fw.keys()
    assert stage(report, "eal_device").code == codes.PN_EAL_DEVICE_OFF
    # lfp__complete sends to lab-syslog, which exists, so the profile is complete on syslog
    assert stage(report, "log_forwarding_profiles").status == OK


@pytest.mark.asyncio
async def test_auto_neither_path_blocked(fake_firewall):
    report, _ = await run(fake_firewall, {LOGGING: "device_logging__lsf_off_eal_off.xml"})
    for sid in ("forwarding_sls", "forwarding_syslog"):
        s = stage(report, sid)
        assert (s.status, s.code) == (BLOCKED, codes.PN_NO_FORWARDING_PATH), sid
        assert "no firewall log can reach the tenant" in s.remediation


@pytest.mark.asyncio
async def test_auto_both_paths(fake_firewall):
    report, _ = await run(fake_firewall, {SYSLOG_SHARED: "syslog__two_servers_udp.xml"})
    assert stage(report, "forwarding_sls").status == OK
    s = stage(report, "forwarding_syslog")
    assert (s.status, s.code) == (DEGRADED, codes.PN_SYSLOG_DELIVERY_UNVERIFIED)
    assert "tenant" in s.remediation and "Broker VM" in s.remediation


@pytest.mark.asyncio
async def test_sls_selected_but_disabled(fake_firewall):
    report, _ = await run(fake_firewall, {LOGGING: "device_logging__lsf_off_eal_off.xml",
                                          SYSLOG_VSYS: "syslog__two_servers_udp.xml"},
                          config={"forwarding_path": "sls"})
    s = stage(report, "forwarding_sls")
    assert (s.status, s.code) == (BLOCKED, codes.PN_LSF_DISABLED)
    s2 = stage(report, "forwarding_syslog")
    assert (s2.status, s2.code) == (SKIPPED, codes.PN_PATH_NOT_SELECTED)
    assert s2.extra["profiles"]  # still read and reported


@pytest.mark.asyncio
async def test_syslog_selected_with_no_servers(fake_firewall):
    report, _ = await run(fake_firewall, config={"forwarding_path": "syslog"})
    s = stage(report, "forwarding_syslog")
    assert (s.status, s.code) == (BLOCKED, codes.PN_SYSLOG_NO_SERVERS)
    assert stage(report, "forwarding_sls").code == codes.PN_PATH_NOT_SELECTED


@pytest.mark.asyncio
async def test_syslog_profile_with_no_servers_is_not_configured(fake_firewall):
    only_empty = b'<response status="success"><result total-count="1" count="1"><syslog><entry name="empty-profile"/></syslog></result></response>'
    report, _ = await run(fake_firewall, {SYSLOG_VSYS: only_empty},
                          config={"forwarding_path": "syslog"})
    assert stage(report, "forwarding_syslog").code == codes.PN_SYSLOG_NO_SERVERS


@pytest.mark.asyncio
async def test_both_selected_requires_both(fake_firewall):
    report, _ = await run(fake_firewall, config={"forwarding_path": "both"})
    assert stage(report, "forwarding_sls").status == OK
    assert stage(report, "forwarding_syslog").code == codes.PN_SYSLOG_NO_SERVERS
    assert report.overall == BLOCKED


@pytest.mark.asyncio
async def test_syslog_always_degraded_when_selected(fake_firewall):
    report, _ = await run(fake_firewall, {SYSLOG_VSYS: "syslog__two_servers_udp.xml"},
                          config={"forwarding_path": "syslog"})
    assert stage(report, "forwarding_syslog").code == codes.PN_SYSLOG_DELIVERY_UNVERIFIED
    assert stage(report, "forwarding_sls").code == codes.PN_PATH_NOT_SELECTED


@pytest.mark.asyncio
async def test_collector_inactive_blocked(fake_firewall):
    report, _ = await run(fake_firewall, {LOGSTATUS: "logging_status__inactive.xml"})
    s = stage(report, "forwarding_sls")
    assert (s.status, s.code) == (BLOCKED, codes.PN_COLLECTOR_INACTIVE)


@pytest.mark.asyncio
async def test_threat_log_stale(fake_firewall):
    report, _ = await run(fake_firewall, {LOGSTATUS: "logging_status__threat_stale.xml"})
    s = stage(report, "forwarding_sls")
    assert (s.status, s.code) == (DEGRADED, codes.PN_LOG_TYPE_STALE)
    assert s.extra["log_types"]["threat"]["age_seconds"] > 15 * 60
    assert s.extra["log_types"]["traffic"]["age_seconds"] == 5 * 60
    assert "threat" in s.detail


@pytest.mark.asyncio
async def test_traffic_never_forwarded(fake_firewall):
    # only the inactive collectors carry rows for traffic; the active one is absent
    body = (pf_fixture("logging_status__active.xml")
            .replace(b"      traffic   2026/11/10 22:00:00   2026/11/10 22:00:00",
                     b"      traffic         Not Available         Not Available", 1))
    report, _ = await run(fake_firewall, {LOGSTATUS: body})
    s = stage(report, "forwarding_sls")
    assert (s.status, s.code) == (DEGRADED, codes.PN_LOG_TYPE_NEVER)


def pf_fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


# --------------------------------------------------------------- log forwarding profiles


@pytest.mark.asyncio
async def test_zero_lfp_blocked(fake_firewall):
    report, _ = await run(fake_firewall, {LFP_VSYS: "config__empty.xml"})
    s = stage(report, "log_forwarding_profiles")
    assert (s.status, s.code) == (BLOCKED, codes.PN_NO_LFP)
    assert s.remediation == REMEDIATIONS[codes.PN_NO_LFP]


@pytest.mark.asyncio
async def test_lfp_missing_wildfire_and_eal_off(fake_firewall):
    report, _ = await run(fake_firewall, {LFP_VSYS: "lfp__missing_wildfire.xml"})
    s = stage(report, "log_forwarding_profiles")
    assert (s.status, s.code) == (DEGRADED, codes.PN_LFP_MISSING_TYPE)
    assert "lab-forward-nowf" in s.detail and "wildfire" in s.detail
    assert codes.PN_LFP_EAL_OFF in {i["code"] for i in s.extra["issues"]}


@pytest.mark.asyncio
async def test_lfp_tag_action(fake_firewall):
    report, _ = await run(fake_firewall, {LFP_SHARED: "lfp__tag_action.xml"})
    s = stage(report, "log_forwarding_profiles")
    assert s.status == DEGRADED
    assert codes.PN_LFP_TAG_ACTION in {i["code"] for i in s.extra["issues"]}


@pytest.mark.asyncio
async def test_lfp_wrong_target(fake_firewall):
    only_panorama = pf_fixture("lfp__complete.xml").replace(
        b"<send-syslog><member>lab-syslog</member></send-syslog>", b"")
    report, _ = await run(fake_firewall, {LFP_VSYS: only_panorama,
                                          SYSLOG_VSYS: "syslog__two_servers_udp.xml"},
                          config={"forwarding_path": "syslog"})
    s = stage(report, "log_forwarding_profiles")
    assert (s.status, s.code) == (DEGRADED, codes.PN_LFP_WRONG_TARGET)


@pytest.mark.asyncio
async def test_lfp_shape_error_blocks_stage_and_later_stages_run(fake_firewall):
    bogus = b'<response status="success"><result><bogus/></result></response>'
    report, fw = await run(fake_firewall, {LFP_VSYS: bogus})
    s = stage(report, "log_forwarding_profiles")
    assert (s.status, s.code) == (BLOCKED, codes.PN_SHAPE_UNRECOGNISED)
    assert LFP_VSYS in s.detail
    assert s.remediation == REMEDIATIONS[codes.PN_SHAPE_UNRECOGNISED]
    assert DECRYPT in fw.keys() and REGIP in fw.keys()
    assert stage(report, "decryption").status == OK
    assert stage(report, "registered_ip").status == OK


# --------------------------------------------------------------- decryption / registered ip


@pytest.mark.asyncio
async def test_decryption_off(fake_firewall):
    report, _ = await run(fake_firewall, {DECRYPT: "config__empty.xml"})
    s = stage(report, "decryption")
    assert (s.status, s.code) == (DEGRADED, codes.PN_DECRYPTION_OFF)
    assert "WildFire" in s.remediation


@pytest.mark.asyncio
async def test_decryption_all_rules_disabled(fake_firewall):
    only_off = b'<response status="success"><result total-count="1" count="1"><rules><entry name="decrypt-off"><disabled>yes</disabled><action>no-decrypt</action></entry></rules></result></response>'
    report, _ = await run(fake_firewall, {DECRYPT: only_off})
    assert stage(report, "decryption").code == codes.PN_DECRYPTION_OFF


@pytest.mark.asyncio
async def test_registered_tags_present(fake_firewall):
    report, _ = await run(fake_firewall, {REGIP: "registered_ip__tagged.xml"})
    s = stage(report, "registered_ip")
    assert (s.status, s.code) == (DEGRADED, codes.PN_REGISTERED_TAGS_PRESENT)
    assert s.extra["count"] == 2 and set(s.extra["tags"]) == {"bypass", "lab"}


# --------------------------------------------------------------- sources stub


@pytest.mark.asyncio
async def test_no_agents_is_degraded_entry_not_empty(fake_firewall):
    report, _ = await run(fake_firewall, agents=())
    assert report.sources == [{"code": codes.PN_NO_AGENTS, "status": DEGRADED,
                               "detail": report.sources[0]["detail"]}]
    assert report.sources[0]["detail"]
    assert report.overall != "ready"


# --------------------------------------------------------------- context helpers


from integrations.panos.client import PanosClient  # noqa: E402
from integrations.panos.context import PreflightContext, find_object, xp, xp_device  # noqa: E402
from integrations.panos.host import validate_panos_config  # noqa: E402

GROUP = "entry[@name='lab-alert']"


def _ctx(fw, **cfg):
    config = validate_panos_config({"host": "192.0.2.1", **cfg})
    client = PanosClient(config["host"], KEY, verify_tls=True, transport=fw.transport)
    return PreflightContext(client=client, config=config, report=None, now=NOW)


def test_xp_builds_device_and_vsys_prefixes(fake_firewall):
    ctx = _ctx(fake_firewall({}), device_name="fw-a", vsys="vsys2")
    assert xp(ctx, "log-settings/profiles") == (
        "/config/devices/entry[@name='fw-a']/vsys/entry[@name='vsys2']/log-settings/profiles")
    assert xp_device(ctx, "deviceconfig/system/timezone") == (
        "/config/devices/entry[@name='fw-a']/deviceconfig/system/timezone")


@pytest.mark.asyncio
async def test_find_object_order_vsys_shared_panorama(fake_firewall):
    fw = fake_firewall({
        VSYS + f"/profile-group/{GROUP}": "config__empty.xml",
        f"/config/shared/profile-group/{GROUP}": "config__empty.xml",
        f"/config/panorama/vsys/entry[@name='vsys1']/profile-group/{GROUP}": "profile_group__alertall.xml",
    })
    ctx = _ctx(fw)
    result = await find_object(ctx, "profile-group", "lab-alert")
    await ctx.client.aclose()
    assert result is not None and result.find("entry") is not None
    assert fw.keys() == [VSYS + f"/profile-group/{GROUP}",
                         f"/config/shared/profile-group/{GROUP}",
                         f"/config/panorama/vsys/entry[@name='vsys1']/profile-group/{GROUP}"]


@pytest.mark.asyncio
async def test_find_object_not_found_is_none(fake_firewall):
    names = [VSYS + "/rulebase/security/rules/entry[@name='r1']",
             "/config/panorama/vsys/entry[@name='vsys1']/pre-rulebase/security/rules/entry[@name='r1']",
             "/config/panorama/vsys/entry[@name='vsys1']/post-rulebase/security/rules/entry[@name='r1']"]
    fw = fake_firewall({n: "config__empty.xml" for n in names})
    ctx = _ctx(fw)
    assert await find_object(ctx, "security-rule", "r1") is None
    await ctx.client.aclose()
    assert fw.keys() == names


@pytest.mark.asyncio
async def test_find_object_unrecognised_shape_raises(fake_firewall):
    from integrations.panos.exceptions import PanosShapeError
    bogus = b'<response status="success"><result><bogus/></result></response>'
    fw = fake_firewall({VSYS + "/profiles/spyware/entry[@name='s1']": bogus})
    ctx = _ctx(fw)
    with pytest.raises(PanosShapeError):
        await find_object(ctx, "spyware", "s1")
    await ctx.client.aclose()


@pytest.mark.asyncio
async def test_find_object_unexpressible_name_sends_nothing(fake_firewall):
    from integrations.panos.exceptions import PanosShapeError
    fw = fake_firewall({})
    ctx = _ctx(fw)
    with pytest.raises(PanosShapeError):
        await find_object(ctx, "profile-group", "x' or '1'='1")
    await ctx.client.aclose()
    assert fw.calls == []
