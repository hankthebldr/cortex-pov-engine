"""Security-review hardening: xpath allowlist, canonical send, per-command schemas, redaction."""
from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr

from integrations.panos import codes
from integrations.panos.client import PanosClient, allowed_xpath
from integrations.panos.exceptions import (
    PanosApiError, PanosConfigError, PanosWriteRefused)

DEV = "/config/devices/entry[@name='localhost.localdomain']"
VS = DEV + "/vsys/entry[@name='vsys1']"
PV = "/config/panorama/vsys/entry[@name='vsys1']"
N = "entry[@name='My Rule_1:a/b+c-d']"

XPATH_ALLOWED = [
    DEV + "/deviceconfig/setting/logging",
    DEV + "/deviceconfig/system/timezone",
    VS + "/log-settings/profiles",
    VS + "/log-settings/syslog",
    "/config/shared/log-settings/syslog",
    "/config/shared/log-settings/profiles",
    VS + "/rulebase/decryption/rules",
    VS + "/rulebase/security/rules/" + N,
    VS + "/profile-group/" + N,
    VS + "/zone",
    VS + "/zone/" + N,
    "/config/shared/profile-group/" + N,
    PV + "/pre-rulebase/security/rules/" + N,
    PV + "/post-rulebase/security/rules/" + N,
    PV + "/profile-group/" + N,
    "/config/panorama/shared/profile-group/" + N,
    "/config/panorama/shared/profiles/spyware/" + N,
] + [f"{VS}/profiles/{t}/{N}" for t in
     ("virus", "spyware", "vulnerability", "url-filtering", "wildfire-analysis")] \
  + [f"/config/shared/profiles/{t}/{N}" for t in ("virus", "spyware")] \
  + [f"{PV}/profiles/{t}/{N}" for t in ("virus", "vulnerability")]

XPATH_REFUSED = [
    "/config", "/config/mgt-config/users", "/config/mgt-config",
    DEV + "/deviceconfig/system | //*",
    DEV + "/deviceconfig/system/timezone | //*",
    "//*", VS + "/../../mgt-config", VS + "//zone",
    VS + "/certificate", VS + "/ssl-tls-service-profile",
    DEV + "/certificate",
    VS + "/rulebase/security/rules",            # missing predicate
    VS + "/profile-group",                      # missing predicate
    VS + "/rulebase/security/rules/entry[@name='a'b']",
    VS + "/rulebase/security/rules/entry[@name='a' or '1'='1']",
    VS + "/rulebase/security/rules/entry[@name='a|b']",
    VS + "/rulebase/security/rules/entry[@name='" + "a" * 64 + "']",
    VS + "/profiles/data-filtering/" + N,
    VS + "/zone/" + N + "/network",
    DEV + "/deviceconfig/setting/logging\n",
    "/config/devices/entry/deviceconfig/system/timezone",
    "/config/panorama/vsys/entry[@name='vsys1']/pre-rulebase/security/rules",
    "",
    # non-ASCII / newline-terminated digits must not satisfy vsys\d+
    DEV + "/vsys/entry[@name='vsys\u0663']/zone",
    DEV + "/vsys/entry[@name='vsys\uff11']/zone",
    DEV + "/vsys/entry[@name='vsys1\n']/zone",
    PV + "/profile-group/" + N.replace("My", "My") + "\n",
    "/config/panorama/vsys/entry[@name='vsys\u0663']/profile-group/" + N,
    "/config/shared/log-settings/profiles\n",
]


def counting():
    seen = []

    def h(request):
        seen.append(request)
        return httpx.Response(200, content=b'<response status="success"><result/></response>')

    return PanosClient("192.0.2.1", "KEY", verify_tls=False, transport=httpx.MockTransport(h)), seen


@pytest.mark.parametrize("xp", XPATH_ALLOWED)
def test_allowed_xpath_true(xp):
    assert allowed_xpath(xp)


@pytest.mark.parametrize("xp", XPATH_REFUSED)
def test_allowed_xpath_false(xp):
    assert not allowed_xpath(xp)


@pytest.mark.asyncio
@pytest.mark.parametrize("xp", XPATH_ALLOWED[:4])
async def test_config_show_sends_allowed(xp):
    c, seen = counting()
    await c.config_show(xp)
    assert len(seen) == 1
    await c.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("xp", XPATH_REFUSED)
async def test_config_show_refuses_before_send(xp):
    c, seen = counting()
    with pytest.raises(PanosWriteRefused) as ei:
        await c.config_show(xp)
    assert ei.value.code == codes.PN_WRITE_REFUSED
    assert "xpath not in read allowlist" in str(ei.value)
    assert seen == [] and c.calls_issued == 0
    await c.aclose()


# ---- I2: canonical bytes, smuggling vectors ------------------------------

SMUGGLE = [
    "<show><system><info/><!--x--></system></show>",
    "<show><system><info/></system></show><?pi x?>",
    "<show><system><info><![CDATA[<request><restart><system/></restart></request>]]></info></system></show>",
    "<show><system><info><a:request xmlns:a='u'><x/></a:request></info></system></show>",
    "<show><system><info><Commit/></info></system></show>",
    "<show><system attr='1'><info/></system></show>",
    "<show><system><info/>tail-text</system></show>",
    "<show>text<system><info/></system></show>",
    "<show><system><info/></system>tail</show>",
    "<show><system><info/></system></show>trailing",
    "<show><{x}><info/></{x}></show>",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("cmd", SMUGGLE)
async def test_smuggling_vectors_refused(cmd):
    c, seen = counting()
    with pytest.raises(PanosWriteRefused):
        await c.op(cmd)
    assert seen == []
    await c.aclose()


@pytest.mark.asyncio
async def test_sends_canonical_reserialisation_not_input():
    c, seen = counting()
    await c.op("<show>\n  <system>\n <info/></system></show>")
    sent = seen[0].content.decode()
    from urllib.parse import parse_qs
    cmd = parse_qs(sent)["cmd"][0]
    import xml.etree.ElementTree as ET
    assert cmd == ET.tostring(ET.fromstring(cmd), encoding="unicode")
    assert "\n" not in cmd.replace("\n", "") and "info" in cmd
    await c.aclose()


# ---- I3: per-command schemas ---------------------------------------------

PASS = [
    "<show><system><info/></system></show>",
    "<show><clock/></show>",
    "<show><logging-status/></show>",
    "<test><security-policy-match><from>a</from><to>b</to><source>1.1.1.1</source>"
    "<destination>2.2.2.2</destination><destination-port>443</destination-port>"
    "<protocol>6</protocol><application>ssl</application><source-user>u</source-user>"
    "</security-policy-match></test>",
    "<test><nat-policy-match><from>a</from></nat-policy-match></test>",
    "<test><routing><fib-lookup><virtual-router>default</virtual-router><ip>1.1.1.1</ip></fib-lookup></routing></test>",
    "<test><advanced-routing><fib-lookup><logical-router>default</logical-router><ip>1.1.1.1</ip></fib-lookup></advanced-routing></test>",
    "<show><session><all><filter><source>1.1.1.1</source><count>yes</count></filter></all></session></show>",
    "<show><user><ip-user-mapping><all/></ip-user-mapping></user></show>",
    "<show><user><ip-user-mapping><ip>1.1.1.1</ip></ip-user-mapping></user></show>",
    "<show><object><registered-ip><all/></registered-ip></object></show>",
    "<show><object><registered-ip><ip>1.1.1.1</ip></registered-ip></object></show>",
]

FAIL = [
    "<show><system><info><x/><y/></info></system></show>",
    "<show><system><info><x/></info></system></show>",
    "<show><clock><more/></clock></show>",
    "<show><logging-status><a/></logging-status></show>",
    "<show><session><all/></session></show>",  # unfiltered
    "<show><session><all><filter><source>1.1.1.1</source></filter></all></session></show>",  # no count
    "<show><session><all><filter><count>no</count></filter></all></session></show>",
    "<show><session><all><filter><count>yes</count><evil>1</evil></filter></all></session></show>",
    "<show><session><all><filter><count><x/></count></filter></all></session></show>",
    "<show><user><ip-user-mapping/></user></show>",
    "<show><user><ip-user-mapping><all/><ip>1.1.1.1</ip></ip-user-mapping></user></show>",
    "<show><user><ip-user-mapping><foo>1</foo></ip-user-mapping></user></show>",
    "<show><user><ip-user-mapping><all><x/></all></ip-user-mapping></user></show>",
    "<show><object><registered-ip><ip><x/></ip></registered-ip></object></show>",
    "<show><object><registered-ip/></object></show>",
    "<test><security-policy-match><from>x</from><shutdown/></security-policy-match></test>",
    "<test><security-policy-match><from><deep/></from></security-policy-match></test>",
    "<test><nat-policy-match><anything><deep><deeper/></deep></anything></nat-policy-match></test>",
    "<test><routing><fib-lookup><ip>1.1.1.1</ip><source>x</source></fib-lookup></routing></test>",
    # leaves must be canonical: sent text == checked text
    "<show><session><all><filter><count> yes </count></filter></all></session></show>",
    "<show><session><all><filter><count>yes\n</count></filter></all></session></show>",
    "<show><session><all><filter><count>\n</count></filter></all></session></show>",
    "<show><user><ip-user-mapping><ip> 1.1.1.1</ip></ip-user-mapping></user></show>",
    "<test><nat-policy-match><from>a\tb</from></nat-policy-match></test>",
    "<test><nat-policy-match><from>a\rb</from></nat-policy-match></test>",
    "<test><nat-policy-match><from>a\x01b</from></nat-policy-match></test>",
    "<test><advanced-routing><fib-lookup><virtual-router>d</virtual-router></fib-lookup></advanced-routing></test>",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("cmd", PASS)
async def test_schema_allowed_forms_pass(cmd):
    c, seen = counting()
    await c.op(cmd)
    assert len(seen) == 1
    await c.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("cmd", FAIL)
async def test_schema_violations_refused(cmd):
    c, seen = counting()
    with pytest.raises(PanosWriteRefused):
        await c.op(cmd)
    assert seen == [] and c.calls_issued == 0
    await c.aclose()


# ---- minors ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_m1_api_key_redacted_from_api_error():
    body = b'<response status="error" code="13"><msg><line>bad KEYSECRET here</line></msg></response>'
    c = PanosClient("192.0.2.1", "KEYSECRET", verify_tls=False,
                    transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body)))
    with pytest.raises(PanosApiError) as ei:
        await c.op("<show><clock/></show>")
    assert "KEYSECRET" not in str(ei.value) and "KEYSECRET" not in ei.value.message
    assert ei.value.panos_code == "13"
    await c.aclose()


def test_m2_bad_host_rejected_at_construction():
    with pytest.raises(PanosConfigError):
        PanosClient("https://evil/x?y", "k", verify_tls=False)
    with pytest.raises(PanosConfigError):
        PanosClient("", "k", verify_tls=False)


@pytest.mark.asyncio
async def test_m2_host_is_normalised():
    seen = []

    def h(r):
        seen.append(str(r.url))
        return httpx.Response(200, content=b'<response status="success"><result/></response>')

    c = PanosClient("  FW.Example.COM:8443 ", "k", verify_tls=False, transport=httpx.MockTransport(h))
    await c.op("<show><clock/></show>")
    assert seen == ["https://fw.example.com:8443/api/"]
    await c.aclose()


def test_m3_no_password_named_local_in_client():
    import ast
    from pathlib import Path
    src = Path(__file__).resolve().parents[3] / "core/integrations/panos/client.py"
    tree = ast.parse(src.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                names = [n.id for n in ast.walk(t) if isinstance(n, ast.Name)]
                assert "data" not in names or "password" not in ast.dump(node), ast.dump(node)
        if isinstance(node, ast.Dict):
            keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
            assert "password" not in keys or node in _inline_in_call(tree), "password dict not inline"


def _inline_in_call(tree):
    import ast
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            for sub in ast.walk(n):
                out.add(sub)
    return out


@pytest.mark.asyncio
async def test_m4_uppercase_forbidden_tag_refused():
    c, seen = counting()
    with pytest.raises(PanosWriteRefused):
        await c.op("<show><system><info><DELETE/></info></system></show>")
    assert seen == []
    await c.aclose()
