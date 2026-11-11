"""SEC-17 — request text never reaches the Terraform templates unvalidated.

``infra/templates/variables.tf.j2`` and ``terraform.tfvars.j2`` render
``default = "{{ region }}"`` and ``"{{ jumpbox_size }}"`` with Jinja autoescape
off (it is HCL, not HTML). ``jumpbox_size`` had no constraint at all and
``region`` only a length, so a value carrying a double quote closed the string
and whatever followed became HCL. Observed on c852eac:

    POST /api/infra/generate  jumpbox_size = t3.medium" } resource "terraform_data" "pwn" {
        provisioner "local-exec" { command = "id" } } variable "x" { default = "
    -> 200, and variables.tf contains a terraform_data resource with a
       local-exec provisioner.

Today the auto-loaded ``terraform.tfvars`` (which cannot contain blocks) makes
that plan fail, so the realistic effect is a broken bundle; with the tfvars
dropped or ``-var`` used, ``terraform apply`` runs the provisioner on the
runner.

The fix refuses the value before anything is rendered: ``400 BAD_INFRA_PARAM``
from the route, and a ``GenerationError`` from the generator for any caller
that builds the request model itself. The same check covers ``tags``, whose
values land in the same two files.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

INJECTION = ('t3.medium" } resource "terraform_data" "cortexsim_pwn" { '
             'provisioner "local-exec" { command = "id > /tmp/pwned" } } '
             'variable "x" { default = "')


@pytest.fixture
def blueprints(tmp_path: Path) -> Path:
    d = tmp_path / "blueprints"
    d.mkdir()
    return d


@pytest.fixture
def client(monkeypatch, blueprints: Path):
    from api import infra as infra_module

    monkeypatch.setattr(infra_module, "_BLUEPRINTS_DIR", blueprints)
    infra_module._reset_generator()
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(infra_module.router, prefix="/api")
    yield TestClient(app)
    infra_module._reset_generator()


def _body(**over) -> dict:
    params = {"project_name": "sec17", "dc_ssh_cidr": "1.2.3.4/32"}
    params.update(over.pop("params", {}))
    body = {"provider": "aws", "region": "us-east-1", "modules": ["edr"], "params": params}
    body.update(over)
    return body


def _rendered(blueprints: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in blueprints.rglob("*") if p.is_file()
                     and p.name in ("variables.tf", "terraform.tfvars", "main.tf"))


def test_an_injected_jumpbox_size_is_refused_and_nothing_is_rendered(client, blueprints):
    resp = client.post("/api/infra/generate", json=_body(params={"jumpbox_size": INJECTION}))

    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "BAD_INFRA_PARAM"
    assert set(detail) >= {"error", "code", "detail"}
    assert [f["field"] for f in detail["detail"]["fields"]] == ["params.jumpbox_size"]
    assert list(blueprints.iterdir()) == [], "a refused request must leave no bundle"
    assert "local-exec" not in _rendered(blueprints)


@pytest.mark.parametrize("region", [
    'us-east-1"\n}',
    'us-east-1" #',
    "us-east-1${x}",
    "us east 1",
    "US-EAST-1",
    "../../etc",
])
def test_an_unsafe_region_is_refused(client, blueprints, region):
    resp = client.post("/api/infra/generate", json=_body(region=region))

    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "BAD_INFRA_PARAM"
    assert detail["detail"]["fields"][0]["field"] == "region"
    assert list(blueprints.iterdir()) == []


@pytest.mark.parametrize("tags", [
    {"Owner": 'x" }\nresource "terraform_data" "t" {}'},
    {"Owner": "${file(\"/etc/passwd\")}"},
    {"Owner": "%{ if true }x%{ endif }"},
    {'k"ey': "v"},
    {"Owner": "line1\nline2"},
])
def test_an_unsafe_tag_is_refused(client, blueprints, tags):
    resp = client.post("/api/infra/generate", json=_body(params={"tags": tags}))

    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == "BAD_INFRA_PARAM"
    assert resp.json()["detail"]["detail"]["fields"][0]["field"].startswith("params.tags")
    assert list(blueprints.iterdir()) == []


@pytest.mark.parametrize("region, size", [
    ("us-east-1", "t3.medium"),
    ("eu-central-1", "m5.large"),
    ("ap-southeast-2", "c6i.2xlarge"),
    ("us-gov-west-1", "m7i-flex.large"),
    ("us-west-2", "u-6tb1.metal"),
    ("me-central-1", "g4dn.xlarge"),
])
def test_real_regions_and_instance_types_still_generate(client, blueprints, region, size):
    resp = client.post("/api/infra/generate", json=_body(
        region=region, params={"jumpbox_size": size,
                               "tags": {"Owner": "dc@example.com", "Cost-Center": "CC 1234"}}))

    assert resp.status_code == 200, resp.text
    rendered = _rendered(blueprints)
    assert f'default     = "{region}"' in rendered
    assert f'default     = "{size}"' in rendered


def test_the_generator_refuses_a_request_built_without_the_route(blueprints, repo_root):
    """Defence in depth: a caller that constructs the model itself gets the
    same refusal, before a bundle directory exists."""
    from engine.infra_catalog import InfraCatalog
    from engine.infra_generator import GenerationError, InfraGenerator
    from engine.infra_models import InfraGenerateParams, InfraGenerateRequest

    gen = InfraGenerator(
        catalog=InfraCatalog(modules_root=repo_root / "infra" / "modules"),
        templates_dir=repo_root / "infra" / "templates", blueprints_dir=blueprints)
    request = InfraGenerateRequest(
        provider="aws", region="us-east-1", modules=["edr"],
        params=InfraGenerateParams(project_name="sec17", dc_ssh_cidr="1.2.3.4/32",
                                   jumpbox_size=INJECTION))

    with pytest.raises(GenerationError, match="jumpbox_size"):
        gen.generate(request)
    assert list(blueprints.iterdir()) == []


def test_the_check_names_every_bad_field_and_never_echoes_it_whole():
    from engine.infra_models import (InfraGenerateParams, InfraGenerateRequest,
                                     hcl_param_problems)

    request = InfraGenerateRequest(
        provider="aws", region='x"y', modules=["edr"],
        params=InfraGenerateParams(project_name="sec17", dc_ssh_cidr="1.2.3.4/32",
                                   jumpbox_size=INJECTION, tags={"a": "ok"}))
    problems = hcl_param_problems(request)
    assert [p["field"] for p in problems] == ["region", "params.jumpbox_size"]
    assert all(len(p["value"]) <= 64 for p in problems)
    assert all(p["expected"] for p in problems)
