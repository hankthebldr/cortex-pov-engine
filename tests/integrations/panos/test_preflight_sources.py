"""Per-agent NGFW source checks (spec §5.3, §5.4) over the fixture router.

The stitch contract: endpoint and NGFW records stitch only when the 5-tuple agrees,
so each enrolled agent's source is checked on its own. Every test runs the whole
preflight through ``run_panos_preflight`` (except the pure ``resolve_addresses``
tests) and checks the per-source invariants: each source stage id once and in order,
a remediation on every non-ok stage, no API key anywhere in the report."""
from __future__ import annotations

import json

import pytest

from connectors.preflight import BLOCKED, DEGRADED, OK, SKIPPED
from integrations.panos import codes
from integrations.panos import sources as srcmod
from integrations.panos.preflight import run_panos_preflight
from integrations.panos.sources import SOURCE_STAGE_IDS, AgentRef, resolve_addresses

from .conftest import FIXTURES
from .test_preflight_firewall import BASE, KEY, NOW, PVSYS, SYSINFO, VSYS, check_invariants

SRC = "192.0.2.50"
DST = "198.51.100.10"
RULE = "LAB-POV-AlertAll-Out"
ONE_PROBE = [{"ip": DST, "port": 443, "protocol": "tcp", "application": "ssl"}]
ZONES = {"inside_zone": "inside", "outside_zone": "outside"}


def ent(name: str) -> str:
    return f"entry[@name='{name}']"


def session_cmd(ip: str) -> str:
    return (f"<show><session><all><filter><source>{ip}</source><count>yes</count>"
            "</filter></all></session></show>")


def match_cmd(kind: str, src: str, dst: str = DST, port: int = 443, proto: int = 6,
              app: str = "ssl", frm: str = "inside", to: str = "outside") -> str:
    return (f"<test><{kind}><from>{frm}</from><to>{to}</to><source>{src}</source>"
            f"<destination>{dst}</destination><destination-port>{port}</destination-port>"
            f"<protocol>{proto}</protocol><application>{app}</application></{kind}></test>")


def policy_cmd(src: str = SRC, **kw) -> str:
    return match_cmd("security-policy-match", src, **kw)


def nat_cmd(src: str = SRC, **kw) -> str:
    return match_cmd("nat-policy-match", src, **kw)


def usermap_cmd(ip: str) -> str:
    return f"<show><user><ip-user-mapping><ip>{ip}</ip></ip-user-mapping></user></show>"


def regip_cmd(ip: str) -> str:
    return f"<show><object><registered-ip><ip>{ip}</ip></registered-ip></object></show>"


def fib_cmd(ip: str, advanced: bool = False, router: str = "default") -> str:
    if advanced:
        return (f"<test><advanced-routing><fib-lookup><logical-router>{router}</logical-router>"
                f"<ip>{ip}</ip></fib-lookup></advanced-routing></test>")
    return (f"<test><routing><fib-lookup><virtual-router>{router}</virtual-router>"
            f"<ip>{ip}</ip></fib-lookup></routing></test>")


RULE_LOCAL = VSYS + f"/rulebase/security/rules/{ent(RULE)}"
RULE_PRE = PVSYS + f"/pre-rulebase/security/rules/{ent(RULE)}"
RULE_POST = PVSYS + f"/post-rulebase/security/rules/{ent(RULE)}"
GROUP = VSYS + f"/profile-group/{ent('lab-alertall')}"
ZONE_INSIDE = VSYS + f"/zone/{ent('inside')}"
ZONE_LIST = VSYS + "/zone"


def prof(ptype: str, name: str, scope: str = VSYS) -> str:
    return f"{scope}/profiles/{ptype}/{ent(name)}"


def empty_scopes(kind_tail: str) -> dict:
    """The three non-vsys scopes of an object answered empty."""
    return {f"/config/shared/{kind_tail}": "config__empty.xml",
            f"{PVSYS}/{kind_tail}": "config__empty.xml",
            f"/config/panorama/shared/{kind_tail}": "config__empty.xml"}


def body(inner: str) -> bytes:
    return (f'<response status="success"><result total-count="1" count="1">{inner}'
            "</result></response>").encode()


ALERT_SPYWARE = body(
    '<entry name="lab-spyware"><rules><entry name="alert-all"><threat-name>any</threat-name>'
    "<category>any</category><severity><member>any</member></severity><action><alert/></action>"
    '</entry></rules><botnet-domains><lists><entry name="default-paloalto-dns"><action><alert/>'
    "</action></entry></lists></botnet-domains></entry>")
ALERT_VULN = body(
    '<entry name="lab-vuln"><rules><entry name="alert-all"><threat-name>any</threat-name>'
    "<category>any</category><severity><member>any</member></severity><action><alert/></action>"
    "</entry></rules></entry>")
ALERT_AV = body('<entry name="lab-av"><decoder><entry name="http"><action>alert</action></entry>'
                '<entry name="smtp"><action>alert</action></entry></decoder></entry>')
URL_PROFILE = body('<entry name="lab-url"/>')
WF_PROFILE = body('<entry name="lab-wf"/>')

#: An all-alert profile group: the only shape that lets rule_profiles read ok.
HEALTHY = {
    session_cmd(SRC): "session_count__three.xml",
    policy_cmd(): "policy_match__alertall.xml",
    RULE_LOCAL: "rule__group_alertall.xml",
    GROUP: "profile_group__alertall.xml",
    prof("virus", "lab-av"): ALERT_AV,
    prof("spyware", "lab-spyware"): ALERT_SPYWARE,
    prof("vulnerability", "lab-vuln"): ALERT_VULN,
    prof("url-filtering", "lab-url"): URL_PROFILE,
    prof("wildfire-analysis", "lab-wf"): WF_PROFILE,
    nat_cmd(): "nat_match__outbound.xml",
    usermap_cmd(SRC): "user_mapping__one.xml",
    ZONE_INSIDE: "zone__userid_on.xml",
    regip_cmd(SRC): "registered_ip__empty.xml",
}

A1 = AgentRef(agent_id="a1", hostname="pov-target-01", last_ip=SRC)
A2 = AgentRef(agent_id="a2", hostname="ms-01", last_ip=SRC)


def base_config(**over) -> dict:
    cfg = {"probe_destinations": ONE_PROBE, **ZONES, "agent_addresses": {"a1": [SRC]}}
    cfg.update(over)
    return cfg


async def run(fake_firewall, overrides=None, *, config=None, agents=(A1,), max_calls=120):
    fw = fake_firewall({**BASE, **HEALTHY, **(overrides or {})})
    cfg = {"host": "192.0.2.1", **(base_config() if config is None else config)}
    report = await run_panos_preflight(cfg, KEY, firewall="lab-fw", agents=list(agents),
                                       transport=fw.transport, now=NOW, max_calls=max_calls)
    check_invariants(report, fw)
    check_source_invariants(report)
    return report, fw


def check_source_invariants(report):
    d = report.to_dict()
    blob = json.dumps(d)
    assert KEY not in blob
    for src in d["sources"]:
        if "stages" not in src:
            continue
        assert [s["id"] for s in src["stages"]] == list(SOURCE_STAGE_IDS)
        for s in src["stages"]:
            assert s["status"] in (OK, DEGRADED, BLOCKED, SKIPPED)
            assert s["code"] in codes.ALL_CODES, s["code"]
            if s["status"] != OK:
                assert s.get("remediation", "").strip(), (src["agent_id"], s["id"])


def sstage(report, sid, idx=0):
    return next(s for s in report.to_dict()["sources"][idx]["stages"] if s["id"] == sid)


def issue_codes(st) -> set:
    return {i["code"] for i in st.get("issues", [])} | {st["code"]}


def op_calls(fw, needle: str) -> list:
    return [k for t, k in fw.calls if t == "op" and needle in k]


# --------------------------------------------------------------- resolve_addresses (pure)


def test_override_wins_over_last_ip():
    [r] = resolve_addresses([A1], {"observed_sources": {"a1": "192.0.2.77"}})
    assert (r.fw_observed_src, r.fw_src_provenance) == ("192.0.2.77", "entered")


def test_last_ip_is_inferred():
    [r] = resolve_addresses([A1], {})
    assert (r.fw_observed_src, r.fw_src_provenance) == (SRC, "inferred:last_ip")


def test_no_last_ip_no_override_is_unknown():
    [r] = resolve_addresses([AgentRef("a9", "h9", None)], {})
    assert (r.fw_observed_src, r.fw_src_provenance) == (None, "unknown")
    assert r.nat_verdict == "unknown"


def test_garbage_last_ip_is_unknown():
    [r] = resolve_addresses([AgentRef("a9", "h9", "not-an-ip")], {})
    assert (r.fw_observed_src, r.fw_src_provenance) == (None, "unknown")


def test_two_agents_same_source_collide():
    r1, r2 = resolve_addresses([A1, A2], {})
    assert r1.collision_with == ["ms-01"]
    assert r2.collision_with == ["pov-target-01"]


def test_collision_compares_equivalent_forms():
    b = AgentRef("a3", "h3", "::ffff:192.0.2.50")
    r1, r3 = resolve_addresses([A1, b], {})
    assert r1.collision_with == ["h3"] and r3.collision_with == ["pov-target-01"]


def test_no_collision_when_unknown():
    r1, r2 = resolve_addresses([AgentRef("a1", "h1", None), AgentRef("a2", "h2", None)], {})
    assert r1.collision_with == [] and r2.collision_with == []


def test_nat_between_when_disjoint():
    [r] = resolve_addresses([A1], {"agent_addresses": {"a1": ["192.0.2.99"]}})
    assert r.nat_verdict == "nat_between"
    assert (r.agent_addresses, r.agent_addr_provenance) == (["192.0.2.99"], "entered")


def test_tuple_intact_when_containing():
    [r] = resolve_addresses([A1], {"agent_addresses": {"a1": ["10.0.0.1", SRC]}})
    assert r.nat_verdict == "tuple_intact"


def test_no_agent_addresses_is_unknown():
    [r] = resolve_addresses([A1], {"agent_addresses": {"other": [SRC]}})
    assert (r.agent_addresses, r.agent_addr_provenance, r.nat_verdict) == ([], "unknown", "unknown")


def test_ipv6_last_ip_passed_unmodified():
    [r] = resolve_addresses([AgentRef("a6", "h6", "2001:db8::5")], {})
    assert r.fw_observed_src == "2001:db8::5"


# --------------------------------------------------------------- through the preflight


@pytest.mark.asyncio
async def test_no_agents_single_degraded_entry(fake_firewall):
    report, _ = await run(fake_firewall, agents=())
    [entry] = report.to_dict()["sources"]
    assert entry["code"] == codes.PN_NO_AGENTS and entry["status"] == DEGRADED
    assert entry["detail"]


@pytest.mark.asyncio
async def test_healthy_source_every_stage_ok(fake_firewall):
    report, fw = await run(fake_firewall)
    [src] = report.to_dict()["sources"]
    for st in src["stages"]:
        assert st["status"] == OK, (st["id"], st["code"], st["detail"])
    assert src["nat_verdict"] == "tuple_intact"
    assert src["fw_src_provenance"] == "inferred:last_ip"
    assert src["agent_addr_provenance"] == "entered"
    assert src["status"] == OK
    [probe] = src["probes"]
    assert probe["rule"] == RULE and probe["rule_index"] == 6 and probe["action"] == "allow"
    assert probe["log_setting"] == "lab-forward-all"
    assert probe["profiles"]["spyware"]["bucket"] == "alert"
    assert probe["profiles"]["virus"]["bucket"] == "alert"
    assert probe["dns_security"]["by_category"] == {"default-paloalto-dns": "alert"}
    assert report.overall == "ready"


@pytest.mark.asyncio
async def test_collision_blocks_both_naming_each_other(fake_firewall):
    report, _ = await run(fake_firewall, agents=(A1, A2),
                          config=base_config(agent_addresses={"a1": [SRC], "a2": [SRC]}))
    for idx, other in ((0, "ms-01"), (1, "pov-target-01")):
        st = sstage(report, "address", idx)
        assert (st["status"], st["code"]) == (BLOCKED, codes.PN_SOURCE_COLLISION)
        assert other in st["detail"]
        assert report.to_dict()["sources"][idx]["collision_with"] == [other]
    assert report.overall == BLOCKED


@pytest.mark.asyncio
async def test_nat_between_blocks(fake_firewall):
    report, _ = await run(fake_firewall, config=base_config(agent_addresses={"a1": ["192.0.2.99"]}))
    st = sstage(report, "address")
    assert (st["status"], st["code"]) == (BLOCKED, codes.PN_NAT_BETWEEN_AGENT_AND_FW)
    assert "5-tuple" in st["remediation"]


@pytest.mark.asyncio
async def test_unknown_agent_side_is_degraded(fake_firewall):
    report, _ = await run(fake_firewall, config=base_config(agent_addresses={}))
    st = sstage(report, "address")
    assert (st["status"], st["code"]) == (DEGRADED, codes.PN_AGENT_ADDRESS_UNKNOWN)
    assert report.overall != "ready"


@pytest.mark.asyncio
async def test_unknown_source_sends_no_network_command(fake_firewall):
    agent = AgentRef("a7", "no-ip-host", None)
    report, fw = await run(fake_firewall, agents=(agent,), config=base_config(agent_addresses={}))
    st = sstage(report, "address")
    assert st["status"] == DEGRADED and st["code"] == codes.PN_SOURCE_UNKNOWN
    assert op_calls(fw, "security-policy-match") == []
    assert op_calls(fw, "nat-policy-match") == []
    assert op_calls(fw, "<session>") == [] and op_calls(fw, "ip-user-mapping") == []
    assert op_calls(fw, "<registered-ip><ip>") == []
    assert all("None" not in k and "<source></source>" not in k for _, k in fw.calls)
    for sid in SOURCE_STAGE_IDS[1:]:
        s = sstage(report, sid)
        assert (s["status"], s["code"]) == (SKIPPED, codes.PN_SOURCE_UNKNOWN), sid
        assert s["detail"]


@pytest.mark.asyncio
async def test_dns_sinkhole_reported_apart_from_spyware_rules(fake_firewall):
    report, _ = await run(fake_firewall, {prof("spyware", "lab-spyware"): "profile_spyware__dns_sinkhole.xml"})
    st = sstage(report, "rule_profiles")
    assert codes.PN_DNS_SEC_SINKHOLE in issue_codes(st)
    assert codes.PN_PROFILE_BLOCKS in issue_codes(st)
    [probe] = report.to_dict()["sources"][0]["probes"]
    dns = probe["dns_security"]
    assert dns["profile"] == "lab-spyware"
    assert dns["by_category"] == {"default-paloalto-dns": "sinkhole", "pan-dns-sec-cc": "sinkhole",
                                  "pan-dns-sec-malware": "signature-default",
                                  "pan-dns-sec-phishing": "block"}
    spy = probe["profiles"]["spyware"]
    assert spy["by_severity"] == {"critical": "block", "high": "block", "medium": "alert",
                                  "low": "alert", "informational": "alert"}
    assert spy["bucket"] == "mixed"
    assert "sinkhole" not in spy["by_severity"].values()
    # the scoped rule is real policy: reported, never folded into by_severity
    assert [r["name"] for r in spy["scoped_rules"]] == ["scoped-allow"]
    assert spy["scoped_rules"][0]["bucket"] == "allow"


@pytest.mark.asyncio
async def test_vulnerability_default_is_signature_default(fake_firewall):
    report, _ = await run(fake_firewall, {prof("vulnerability", "lab-vuln"): "profile_vulnerability__default.xml"})
    st = sstage(report, "rule_profiles")
    assert (st["status"], st["code"]) == (DEGRADED, codes.PN_PROFILE_SIGNATURE_DEFAULT)
    [probe] = report.to_dict()["sources"][0]["probes"]
    assert probe["profiles"]["vulnerability"]["bucket"] == "signature-default"


@pytest.mark.asyncio
async def test_scoped_vulnerability_rules_reported(fake_firewall):
    report, _ = await run(fake_firewall, {prof("vulnerability", "lab-vuln"): "profile_vulnerability__scoped.xml"})
    [probe] = report.to_dict()["sources"][0]["probes"]
    vuln = probe["profiles"]["vulnerability"]
    assert vuln["by_severity"]["low"] == "none" and vuln["by_severity"]["critical"] == "block"
    assert {r["name"] for r in vuln["scoped_rules"]} == {"scoped-low", "cat-scoped"}


@pytest.mark.asyncio
async def test_predefined_profiles_classified_from_table(fake_firewall):
    rule = body(f'<entry name="{RULE}"><action>allow</action><profile-setting><profiles>'
                "<virus><member>default</member></virus><spyware><member>strict</member></spyware>"
                "<vulnerability><member>default</member></vulnerability>"
                "<url-filtering><member>default</member></url-filtering>"
                "<wildfire-analysis><member>default</member></wildfire-analysis>"
                "</profiles></profile-setting><log-setting>lab-forward-all</log-setting></entry>")
    over = {RULE_LOCAL: rule}
    for ptype in ("virus", "vulnerability", "url-filtering", "wildfire-analysis"):
        over[prof(ptype, "default")] = "config__empty.xml"
        over.update(empty_scopes(f"profiles/{ptype}/{ent('default')}"))
    over[prof("spyware", "strict")] = "config__empty.xml"
    over.update(empty_scopes(f"profiles/spyware/{ent('strict')}"))
    report, _ = await run(fake_firewall, over)
    [probe] = report.to_dict()["sources"][0]["probes"]
    p = probe["profiles"]
    assert p["spyware"]["source"] == "predefined"
    assert p["spyware"]["by_severity"]["critical"] == "block"
    assert p["spyware"]["by_severity"]["medium"] == "block"
    assert p["vulnerability"]["bucket"] == "signature-default"
    assert p["virus"]["bucket"] == "signature-default"
    st = sstage(report, "rule_profiles")
    assert {codes.PN_PROFILE_BLOCKS, codes.PN_PROFILE_SIGNATURE_DEFAULT} <= issue_codes(st)
    assert codes.PN_OBJECT_NOT_FOUND not in issue_codes(st)


@pytest.mark.asyncio
async def test_unknown_profile_name_is_object_not_found(fake_firewall):
    over = {prof("spyware", "lab-spyware"): "config__empty.xml",
            **empty_scopes(f"profiles/spyware/{ent('lab-spyware')}")}
    report, _ = await run(fake_firewall, over)
    st = sstage(report, "rule_profiles")
    assert codes.PN_OBJECT_NOT_FOUND in issue_codes(st) and st["status"] == DEGRADED
    assert "lab-spyware" in st["detail"]


@pytest.mark.asyncio
async def test_allow_rule_without_profile_is_no_profile(fake_firewall):
    rule = body(f'<entry name="{RULE}"><action>allow</action>'
                "<log-setting>lab-forward-all</log-setting></entry>")
    report, _ = await run(fake_firewall, {RULE_LOCAL: rule})
    st = sstage(report, "rule_profiles")
    assert (st["status"], st["code"]) == (DEGRADED, codes.PN_NO_PROFILE)
    [probe] = report.to_dict()["sources"][0]["probes"]
    assert all(v["bucket"] == "none" for v in probe["profiles"].values())


@pytest.mark.asyncio
async def test_deny_rule_blocks(fake_firewall):
    deny = body(f'<entry name="{RULE}"><action>deny</action>'
                "<log-setting>lab-forward-all</log-setting></entry>")
    report, _ = await run(fake_firewall, {RULE_LOCAL: deny})
    st = sstage(report, "rule_profiles")
    assert (st["status"], st["code"]) == (BLOCKED, codes.PN_RULE_DENIES)


@pytest.mark.asyncio
async def test_rule_without_log_setting(fake_firewall):
    rule = body(f'<entry name="{RULE}"><action>allow</action><profile-setting><group>'
                "<member>lab-alertall</member></group></profile-setting></entry>")
    report, _ = await run(fake_firewall, {RULE_LOCAL: rule})
    st = sstage(report, "rule_profiles")
    assert (st["status"], st["code"]) == (DEGRADED, codes.PN_RULE_NO_LOG_FORWARDING)


@pytest.mark.asyncio
async def test_rule_log_profile_flagged_by_stage_8(fake_firewall):
    from .test_preflight_firewall import LFP_VSYS
    incomplete = (FIXTURES / "lfp__missing_wildfire.xml").read_bytes().replace(
        b'name="lab-forward-nowf"', b'name="lab-forward-all"')
    report, _ = await run(fake_firewall, {LFP_VSYS: incomplete})
    st = sstage(report, "rule_profiles")
    assert (st["status"], st["code"]) == (DEGRADED, codes.PN_RULE_LOG_PROFILE_INCOMPLETE)
    assert "lab-forward-all" in st["detail"]


@pytest.mark.asyncio
async def test_rule_log_profile_absent_from_stage_8(fake_firewall):
    rule = (FIXTURES / "rule__group_alertall.xml").read_bytes().replace(
        b"<log-setting>lab-forward-all</log-setting>", b"<log-setting>nowhere-profile</log-setting>")
    report, _ = await run(fake_firewall, {RULE_LOCAL: rule})
    st = sstage(report, "rule_profiles")
    assert codes.PN_OBJECT_NOT_FOUND in issue_codes(st) and "nowhere-profile" in st["detail"]


@pytest.mark.asyncio
async def test_rule_only_in_panorama_pre_rulebase_is_found(fake_firewall):
    report, _ = await run(fake_firewall, {RULE_LOCAL: "config__empty.xml",
                                          RULE_PRE: "rule__group_alertall.xml"})
    st = sstage(report, "rule_profiles")
    assert st["status"] == OK, (st["code"], st["detail"])


@pytest.mark.asyncio
async def test_rule_found_nowhere_is_object_not_found(fake_firewall):
    report, _ = await run(fake_firewall, {RULE_LOCAL: "config__empty.xml",
                                          RULE_PRE: "config__empty.xml",
                                          RULE_POST: "config__empty.xml"})
    st = sstage(report, "rule_profiles")
    assert (st["status"], st["code"]) == (DEGRADED, codes.PN_OBJECT_NOT_FOUND)
    assert RULE in st["detail"]


@pytest.mark.asyncio
async def test_no_policy_match_is_degraded(fake_firewall):
    report, fw = await run(fake_firewall, {policy_cmd(): "policy_match__nomatch.xml"})
    assert sstage(report, "policy_match")["code"] == codes.PN_NO_RULE_MATCH
    assert sstage(report, "policy_match")["status"] == DEGRADED
    assert sstage(report, "rule_profiles")["status"] == SKIPPED
    assert not any("rulebase/security" in k for _, k in fw.calls)


@pytest.mark.asyncio
async def test_zero_sessions_source_not_seen(fake_firewall):
    zero = b'<response status="success"><result><member>0</member></result></response>'
    report, _ = await run(fake_firewall, {session_cmd(SRC): zero})
    st = sstage(report, "source_seen")
    assert (st["status"], st["code"]) == (DEGRADED, codes.PN_SOURCE_NOT_SEEN)


@pytest.mark.asyncio
async def test_tagged_source(fake_firewall):
    report, _ = await run(fake_firewall, {regip_cmd(SRC): "registered_ip__tagged.xml"})
    st = sstage(report, "registered_tags")
    assert (st["status"], st["code"]) == (DEGRADED, codes.PN_SOURCE_TAGGED)
    assert st["tags"] == ["bypass", "lab"]
    assert "bypass" in st["detail"]


@pytest.mark.asyncio
async def test_no_user_mapping(fake_firewall):
    report, _ = await run(fake_firewall, {usermap_cmd(SRC): "user_mapping__empty.xml"})
    st = sstage(report, "user_id")
    assert (st["status"], st["code"]) == (DEGRADED, codes.PN_NO_USER_MAPPING)
    assert "user" in st["remediation"]


@pytest.mark.asyncio
async def test_zone_user_id_off(fake_firewall):
    report, _ = await run(fake_firewall, {ZONE_INSIDE: body(
        '<entry name="inside"><network><layer3><member>ethernet1/2</member></layer3></network></entry>')})
    st = sstage(report, "user_id")
    assert codes.PN_ZONE_USERID_OFF in issue_codes(st) and st["status"] == DEGRADED


@pytest.mark.asyncio
async def test_nat_egress_is_informational_ok(fake_firewall):
    report, _ = await run(fake_firewall)
    st = sstage(report, "nat")
    assert st["status"] == OK and "LAB-Outbound-NAT" in st["detail"]
    assert report.to_dict()["sources"][0]["probes"][0]["nat_rule"] == "LAB-Outbound-NAT"


FIB_INSIDE = body("<interface>ethernet1/2</interface>").replace(b' total-count="1" count="1"', b"")


@pytest.mark.asyncio
async def test_fib_legacy_routing_resolves_zones(fake_firewall):
    over = {fib_cmd(SRC): FIB_INSIDE, fib_cmd(DST): "fib__ok.xml", ZONE_LIST: "zones__list.xml"}
    report, fw = await run(fake_firewall, over, config=base_config(inside_zone=None, outside_zone=None))
    assert fib_cmd(SRC) in fw.keys() and fib_cmd(DST) in fw.keys()
    assert op_calls(fw, "advanced-routing") == []
    assert sstage(report, "policy_match")["status"] == OK
    probe = report.to_dict()["sources"][0]["probes"][0]
    assert (probe["from_zone"], probe["to_zone"]) == ("inside", "outside")


@pytest.mark.asyncio
async def test_fib_advanced_routing_command(fake_firewall):
    sysinfo = (FIXTURES / "system_info__ok.xml").read_bytes().replace(
        b"<advanced-routing>off</advanced-routing>", b"<advanced-routing>on</advanced-routing>")
    assert b"<advanced-routing>on</advanced-routing>" in sysinfo
    over = {SYSINFO: sysinfo, fib_cmd(SRC, True): FIB_INSIDE, fib_cmd(DST, True): "fib__ok.xml",
            ZONE_LIST: "zones__list.xml"}
    report, fw = await run(fake_firewall, over, config=base_config(inside_zone=None, outside_zone=None))
    assert fib_cmd(SRC, True) in fw.keys()
    assert op_calls(fw, "<routing><fib-lookup>") == []
    assert sstage(report, "policy_match")["status"] == OK


@pytest.mark.asyncio
async def test_fib_no_route_is_zone_unresolved_and_sends_no_match(fake_firewall):
    over = {fib_cmd(SRC): FIB_INSIDE, fib_cmd(DST): "fib__noroute.xml", ZONE_LIST: "zones__list.xml"}
    report, fw = await run(fake_firewall, over, config=base_config(inside_zone=None, outside_zone=None))
    st = sstage(report, "policy_match")
    assert (st["status"], st["code"]) == (DEGRADED, codes.PN_ZONE_UNRESOLVED)
    assert op_calls(fw, "security-policy-match") == []
    assert sstage(report, "nat")["status"] == SKIPPED


@pytest.mark.asyncio
async def test_ipv6_source_sent_unmodified(fake_firewall):
    v6 = "2001:db8::5"
    agent = AgentRef("a6", "v6-host", v6)
    over = {session_cmd(v6): "session_count__three.xml", policy_cmd(v6): "policy_match__alertall.xml",
            nat_cmd(v6): "nat_match__outbound.xml", usermap_cmd(v6): "user_mapping__one.xml",
            regip_cmd(v6): "registered_ip__empty.xml"}
    report, fw = await run(fake_firewall, over, agents=(agent,),
                           config=base_config(agent_addresses={"a6": [v6]}))
    assert session_cmd(v6) in fw.keys() and policy_cmd(v6) in fw.keys()
    src = report.to_dict()["sources"][0]
    assert src["fw_observed_src"] == v6 and src["nat_verdict"] == "tuple_intact"
    assert sstage(report, "policy_match")["status"] == OK


@pytest.mark.asyncio
async def test_call_budget_mid_source_skips_rest_and_later_sources(fake_firewall):
    # Count the firewall-wide calls, then allow exactly two source calls
    # (session count + policy match): the rule read hits the budget.
    probe_report, probe_fw = await run(fake_firewall, agents=())
    budget = len(probe_fw.calls) + 2
    b = AgentRef("b1", "second-host", "192.0.2.60")
    report, fw = await run(fake_firewall, agents=(A1, b), max_calls=budget,
                           config=base_config(agent_addresses={"a1": [SRC], "b1": ["192.0.2.60"]}))
    assert report.queries_issued == budget
    first, second = report.to_dict()["sources"]
    by = {s["id"]: s for s in first["stages"]}
    assert by["source_seen"]["status"] == OK and by["policy_match"]["status"] == OK
    for sid in ("rule_profiles", "nat", "user_id", "registered_tags"):
        assert (by[sid]["status"], by[sid]["code"]) == (SKIPPED, codes.PN_CALL_BUDGET), sid
    assert second["stages"][0]["id"] == "address" and second["stages"][0]["status"] == OK
    for s in second["stages"][1:]:
        assert (s["status"], s["code"]) == (SKIPPED, codes.PN_CALL_BUDGET), s["id"]
    assert first["probes"][0]["rule"] == RULE  # work done before the cap is kept


@pytest.mark.asyncio
async def test_shape_error_isolated_to_its_stage(fake_firewall):
    bogus = b'<response status="success"><result><bogus/></result></response>'
    report, _ = await run(fake_firewall, {session_cmd(SRC): bogus})
    st = sstage(report, "source_seen")
    assert (st["status"], st["code"]) == (BLOCKED, codes.PN_SHAPE_UNRECOGNISED)
    assert "session" in st["detail"]
    assert sstage(report, "policy_match")["status"] == OK
    assert sstage(report, "registered_tags")["status"] == OK


@pytest.mark.asyncio
async def test_internal_error_isolated_and_key_free(fake_firewall, monkeypatch):
    def boom(_r):
        raise ZeroDivisionError("leaks " + KEY)
    monkeypatch.setattr(srcmod, "parse_user_mapping", boom)
    report, _ = await run(fake_firewall)
    st = sstage(report, "user_id")
    assert (st["status"], st["code"]) == (BLOCKED, codes.PN_INTERNAL_ERROR)
    assert st["detail"] == "internal error (ZeroDivisionError)"
    assert sstage(report, "registered_tags")["status"] == OK


@pytest.mark.asyncio
async def test_operator_values_are_escaped_not_injected(fake_firewall):
    # An application name with XML metacharacters is sent as text, never as markup.
    probes = [{"ip": DST, "port": 443, "protocol": "tcp", "application": "ssl</application><x>"}]
    report, fw = await run(fake_firewall, config=base_config(probe_destinations=probes))
    sent = op_calls(fw, "security-policy-match")
    assert sent and all("&lt;/application&gt;&lt;x&gt;" in k for k in sent)
    assert all("<x>" not in k for k in sent)
