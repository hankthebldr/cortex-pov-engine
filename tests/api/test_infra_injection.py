"""POST /api/infra/generate must not let a request value become Terraform.

``params.jumpbox_size`` (unbounded) and ``region`` were rendered raw into
``"{{ ... }}"`` string literals in ``variables.tf.j2`` and
``terraform.tfvars.j2`` (autoescape off). A quote and a few newlines closed the
literal and the enclosing block, so a request could add a
``data "external"`` block, which ``terraform plan`` runs as a program on the
DC's machine, or set an unrelated variable from inside ``region``.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_TOP_LEVEL_BLOCK = re.compile(r'^(data|resource|module|provider|output|locals|terraform)\b', re.M)
_TFVARS_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=", re.M)
_TFVARS_KEYS = {"region", "project_name", "dc_ssh_cidr", "jumpbox_size", "k8s_node_count",
                "edr_target_count", "ttl_hours", "tags"}

_EXTERNAL_BLOCK = (
    't3.medium"\n}\n\n'
    'data "external" "pwn" {\n  program = ["sh", "-c", "touch /tmp/cortexsim-pwned"]\n}\n\n'
    'variable "pad" {\n  default = "'
)
# 32 characters, region's max_length: closes the literal and sets another variable.
_REGION_OVERRIDE = 'x"\naispm_enable_shadow_gpu=true#'


@pytest.fixture
def api(monkeypatch, tmp_path: Path):
    blueprints = tmp_path / "blueprints"
    blueprints.mkdir()

    from api import infra as infra_module
    from fastapi import FastAPI

    monkeypatch.setattr(infra_module, "_BLUEPRINTS_DIR", blueprints)
    infra_module._reset_generator()
    app = FastAPI()
    app.include_router(infra_module.router, prefix="/api")
    return TestClient(app), blueprints


def _body(**over) -> dict:
    body = {"provider": "aws", "region": "us-east-1", "modules": ["base"],
            "params": {"project_name": "inj-test", "dc_ssh_cidr": "203.0.113.0/32"}}
    params = over.pop("params", {})
    body.update(over)
    body["params"].update(params)
    return body


def _generated(blueprints: Path, name: str) -> list[str]:
    return [p.read_text(encoding="utf-8") for p in blueprints.glob(f"*/{name}")]


def test_jumpbox_size_cannot_add_a_terraform_block(api):
    client, blueprints = api
    resp = client.post("/api/infra/generate", json=_body(params={"jumpbox_size": _EXTERNAL_BLOCK}))

    for text in _generated(blueprints, "variables.tf") + _generated(blueprints, "terraform.tfvars"):
        assert not _TOP_LEVEL_BLOCK.search(text), "the request added a Terraform block"
    assert resp.status_code == 422, resp.text
    assert "jumpbox_size" in resp.text


def test_region_cannot_set_another_variable(api):
    client, blueprints = api
    resp = client.post("/api/infra/generate", json=_body(region=_REGION_OVERRIDE))

    for text in _generated(blueprints, "terraform.tfvars"):
        assert set(_TFVARS_KEY.findall(text)) <= _TFVARS_KEYS, "region set a variable"
    assert resp.status_code == 422, resp.text
    assert "region" in resp.text


@pytest.mark.parametrize("region", ["us-east-1", "eu-central-1", "ap-southeast-7",
                                    "us-gov-west-1", "cn-north-1", "il-central-1"])
@pytest.mark.parametrize("size", ["t3.medium", "m7i-flex.large", "c6i.2xlarge",
                                  "u-6tb1.metal", "x2iedn.32xlarge"])
def test_real_regions_and_instance_types_render_as_literals(api, region, size):
    client, blueprints = api
    resp = client.post("/api/infra/generate", json=_body(region=region,
                                                         params={"jumpbox_size": size}))
    assert resp.status_code == 200, resp.text
    (tfvars,) = _generated(blueprints, "terraform.tfvars")
    (variables,) = _generated(blueprints, "variables.tf")
    assert f'region           = "{region}"' in tfvars
    assert f'jumpbox_size     = "{size}"' in tfvars
    assert f'default     = "{size}"' in variables


@pytest.mark.parametrize("field,value", [
    ("region", "US-EAST-1"), ("region", "us-east"), ("region", "us east 1"),
    ("jumpbox_size", "t3"), ("jumpbox_size", "t3.medium "), ("jumpbox_size", "T3.MEDIUM"),
])
def test_values_outside_the_aws_shapes_are_422(api, field, value):
    client, _ = api
    body = _body(region=value) if field == "region" else _body(params={field: value})
    assert client.post("/api/infra/generate", json=body).status_code == 422


def _without_the_request_gate(monkeypatch):
    """Switch off the beta's SEC-17 allow-list (route and generator) so a test
    can reach the templates' own escaping, the second layer, on its own."""
    from api import infra as infra_module
    from engine import infra_generator

    monkeypatch.setattr(infra_module, "hcl_param_problems", lambda _req: [])
    monkeypatch.setattr(infra_generator, "hcl_param_problems", lambda _req: [])


def test_tag_values_render_as_text_not_as_templates(api, monkeypatch):
    """``tags`` reached both .tf files through ``tojson``, which escapes quotes
    and newlines but not HCL's ``${`` / ``%{`` template sequences. A tag value
    holding either was read as a template, so Terraform refused the bundle
    ("Function calls not allowed") instead of tagging with the text.

    Since the merge the route refuses such a tag up front (SEC-17: ``$``, ``%``,
    ``{`` and ``"`` are outside the characters AWS allows in a tag), and the
    templates still render one as text if anything lets it through."""
    client, blueprints = api
    tags = {"Owner": '${pathexpand("~")}', "Note": "%{ if true }x%{ endif }"}
    resp = client.post("/api/infra/generate", json=_body(params={"tags": tags}))
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == "BAD_INFRA_PARAM"
    assert _generated(blueprints, "terraform.tfvars") == []

    _without_the_request_gate(monkeypatch)
    resp = client.post("/api/infra/generate", json=_body(params={"tags": tags}))
    assert resp.status_code == 200, resp.text

    literal = '{"Note": "%%{ if true }x%%{ endif }", "Owner": "$${pathexpand(\\"~\\")}"}'
    (tfvars,) = _generated(blueprints, "terraform.tfvars")
    (variables,) = _generated(blueprints, "variables.tf")
    assert f"tags = {literal}\n" in tfvars
    assert f"default     = {literal}\n" in variables


def test_the_templates_render_any_value_as_one_string_literal(repo_root: Path, tmp_path: Path,
                                                              monkeypatch):
    """The request model is not the only caller of the generator, so the
    templates themselves must keep a value inside its literal.

    Since the merge the generator refuses these values first (the beta's
    SEC-17 gate, GenerationError); with that gate switched off the templates
    alone must still hold."""
    from engine.infra_catalog import InfraCatalog
    from engine.infra_generator import GenerationError, InfraGenerator
    from engine.infra_models import InfraGenerateParams, InfraGenerateRequest

    gen = InfraGenerator(catalog=InfraCatalog(modules_root=repo_root / "infra" / "modules"),
                         templates_dir=repo_root / "infra" / "templates",
                         blueprints_dir=tmp_path)
    params = InfraGenerateParams.model_construct(
        project_name="inj-test", dc_ssh_cidr="203.0.113.0/32", jumpbox_size=_EXTERNAL_BLOCK,
        k8s_node_count=2, edr_target_count=2, ttl_hours=72, tags={})
    req = InfraGenerateRequest.model_construct(
        provider="aws", region='us"${file("/etc/passwd")}', modules=["base"],
        params=params, adapter_refs=[])
    with pytest.raises(GenerationError, match="region"):
        gen.generate(req)
    assert list(tmp_path.iterdir()) == []

    _without_the_request_gate(monkeypatch)
    gen.generate(req)

    for text in _generated(tmp_path, "variables.tf") + _generated(tmp_path, "terraform.tfvars"):
        assert not _TOP_LEVEL_BLOCK.search(text)
        # ``$${`` is HCL's escaped, literal ``${``; a bare ``${`` would interpolate.
        assert not re.search(r"(?<!\$)\$\{file", text)
    (tfvars,) = _generated(tmp_path, "terraform.tfvars")
    assert set(_TFVARS_KEY.findall(tfvars)) <= _TFVARS_KEYS
