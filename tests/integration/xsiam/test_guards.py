# tests/integration/xsiam/test_guards.py
import ast
import pathlib

import pytest

from integrations.xsiam import guards, codes
from integrations.xsiam.exceptions import XsiamCapabilityError, XqlWriteStageRefused

REPO = pathlib.Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("config,granted", [
    (None, False), ({}, False), ({"capabilities": "xql"}, False),
    ({"capabilities": ["read_alerts"]}, False), ({"capabilities": ["XQL"]}, True),
    ({"capabilities": ["read_alerts", "xql"]}, True)])
def test_xql_capability_granted_only_when_listed(config, granted):
    assert guards.xql_capability_granted(config) is granted


def test_require_xql_capability_raises_403_with_remediation():
    with pytest.raises(XsiamCapabilityError) as ei:
        guards.require_xql_capability(["read_alerts"], tenant="acme")
    assert ei.value.code == codes.XSIAM_CAPABILITY_MISSING and ei.value.http_status == 403
    assert "config.capabilities" in codes.REMEDIATION[codes.XSIAM_CAPABILITY_MISSING]


@pytest.mark.parametrize("q", ["dataset = x | target type = dataset out",
                               "dataset=x|TARGET TYPE=lookup l", "x | target  type = dataset"])
def test_write_stages_are_refused_not_rewritten(q):
    with pytest.raises(XqlWriteStageRefused) as ei:
        guards.refuse_xql_write_stage(q)
    assert ei.value.code == codes.XQL_WRITE_STAGE_REFUSED and ei.value.http_status == 400


def test_plain_queries_pass():
    guards.refuse_xql_write_stage("dataset = xdr_data | filter a = 1 | limit 2000")


def test_preflight_uses_the_shared_rule():
    src = (REPO / "core/api/connectors.py").read_text()
    assert "xql_capability_granted(" in src and "not isinstance(caps, list)" not in src


def test_verifier_aliases_the_shared_budget_and_key():
    from engine import verifier
    assert verifier._tenant_budget is guards.tenant_budget
    assert verifier._tenant_name is guards.ledger_key_for


@pytest.mark.parametrize("name", ["tenant_budget", "ledger_key_for", "xql_capability_granted",
                                  "refuse_xql_write_stage", "require_xql_capability"])
def test_exactly_one_definition_under_core(name):
    _one_definition(name, "core/integrations/xsiam/guards.py")


def test_tenant_ledger_key_has_one_definition_in_ledger():
    _one_definition("tenant_ledger_key", "core/integrations/xsiam/ledger.py")


def _one_definition(name, home):
    hits = [p for p in (REPO / "core").rglob("*.py")
            if "__pycache__" not in p.parts
            and any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
                    for n in ast.walk(ast.parse(p.read_text())))]
    assert hits == [REPO / home]


def test_client_carries_capabilities_and_base_url():
    from integrations.xsiam.client import XsiamClient
    from integrations.xsiam.config import XsiamTenantConfig
    c = XsiamClient(XsiamTenantConfig(base_url="https://api-t.xdr.us.paloaltonetworks.com",
                                      region="us", api_key_id="1"), "k", capabilities=["xql"])
    assert c.capabilities == ["xql"] and c.base_url.startswith("https://api-t")
