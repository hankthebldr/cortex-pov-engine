"""Read-only guard: no write ever reaches the wire, and no write verb is spelled in the package."""
from __future__ import annotations

import ast
from pathlib import Path

import httpx
import pytest

from integrations.panos import codes
from integrations.panos.client import OP_ALLOWLIST, PanosClient
from integrations.panos.exceptions import PanosWriteRefused

PKG = Path(__file__).resolve().parents[3] / "core" / "integrations" / "panos"

REFUSED = [
    "<request><restart><system/></restart></request>",
    "<set><deviceconfig/></set>",
    "<clear><session><all/></session></clear>",
    "<delete><x/></delete>",
    "<debug><x/></debug>",
    "<commit/>",
    "<show><config><running/></config></show>",
    "<test><url>example.com</url></test>",
    "<show><system><info/></system><request><restart/></request></show>",
    "<show><system><info><request/></info></system></show>",
    "<show><session><id>1</id></session></show>",
    "<show></show><request/>",
    "not xml at all",
    "",
    '<!DOCTYPE x [<!ENTITY a "b">]><show><clock/></show>',
]

ALLOWED = [
    "<show><system><info/></system></show>",
    "<show><logging-status/></show>",
    "<show><clock/></show>",
    "<show><session><all><filter><count>yes</count></filter></all></session></show>",
    "<show><object><registered-ip><all/></registered-ip></object></show>",
    "<show><user><ip-user-mapping><all/></ip-user-mapping></user></show>",
    "<test><security-policy-match><from>a</from><to>b</to><source>1.1.1.1</source>"
    "</security-policy-match></test>",
    "<test><nat-policy-match><from>a</from><to>b</to></nat-policy-match></test>",
    "<test><routing><fib-lookup><ip>1.1.1.1</ip></fib-lookup></routing></test>",
    "<test><advanced-routing><fib-lookup><ip>1.1.1.1</ip></fib-lookup></advanced-routing></test>",
    "<test><security-policy-match><from>a</from></security-policy-match></test>",
]


def counting_client():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=b'<response status="success"><result/></response>')

    return PanosClient("192.0.2.1", "k", verify_tls=False,
                       transport=httpx.MockTransport(handler)), seen


@pytest.mark.asyncio
@pytest.mark.parametrize("cmd", REFUSED)
async def test_non_allowlisted_op_refused_with_zero_requests(cmd):
    c, seen = counting_client()
    with pytest.raises(PanosWriteRefused) as ei:
        await c.op(cmd)
    assert ei.value.code == codes.PN_WRITE_REFUSED
    assert seen == [] and c.calls_issued == 0
    await c.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("cmd", ALLOWED)
async def test_allowlisted_ops_pass(cmd):
    c, seen = counting_client()
    await c.op(cmd)
    assert len(seen) == 1
    await c.aclose()


def test_allowlist_contains_only_read_roots():
    assert OP_ALLOWLIST
    assert {p[0] for p in OP_ALLOWLIST} <= {"show", "test"}


def test_no_write_action_values_in_package():
    """Only op/config/keygen/show may appear as a type/action value in the package."""
    allowed = {"op", "config", "keygen", "show"}
    bad = []

    def check(value, where):
        if isinstance(value, ast.Constant) and isinstance(value.value, str) \
                and value.value not in allowed:
            bad.append((where, value.value))

    for path in PKG.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if isinstance(k, ast.Constant) and k.value in ("type", "action"):
                        check(v, f"{path.name}:{node.lineno}")
            elif isinstance(node, ast.keyword) and node.arg in ("type", "action"):
                check(node.value, f"{path.name}:{node.value.lineno}")
    assert not bad, f"non-read action values: {bad}"


def test_no_forbidden_verb_literals_as_action_anywhere():
    forbidden = {"set", "edit", "delete", "move", "rename", "commit", "multi-config", "get"}
    hits = []
    for path in PKG.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if (isinstance(k, ast.Constant) and k.value in ("type", "action")
                            and isinstance(v, ast.Constant) and v.value in forbidden):
                        hits.append((path.name, v.value))
    assert not hits
