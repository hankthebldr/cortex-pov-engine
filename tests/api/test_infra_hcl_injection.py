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

That first fix named three fields and missed the fourth string the same two
templates quote, ``dc_ssh_cidr`` (review of 3547c57; second half of this file).
The last test walks the templates so a fifth cannot be added unnoticed.
"""
from __future__ import annotations

import re
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


# -----------------------------------------------------------------------------
# dc_ssh_cidr — "it parses as a network" is not a shape
# -----------------------------------------------------------------------------
#
# The first SEC-17 fix gated region, jumpbox_size and tags and left
# ``dc_ssh_cidr`` to ``ipaddress.ip_network``. That is a parser: an IPv6 zone
# id (the text between ``%`` and the ``/``) may hold anything but ``/`` and
# ``%`` — quotes, braces, newlines. Observed on 3547c57 through this route:
#
#     dc_ssh_cidr = fe80::1%a" } resource "terraform_data" "pwn" { provisioner
#         "local-exec" { command = "id" } } variable "z" { default = "x/64
#     -> 200, and variables.tf AND terraform.tfvars carry the provisioner.
#
# Every payload below is one the request model ACCEPTS (one "/" only, a prefix
# inside the SEC-10 floor), so the 400 is the gate's and not a 422 from
# pydantic. A payload the model already refused would prove nothing here.

_ZONE_TAIL =('" } resource "terraform_data" "cortexsim_pwn" { provisioner "local-exec" '
              '{ command = "id" } } variable "z" { default = "x/64')

CIDR_INJECTIONS = [
    pytest.param("fe80::1%a" + _ZONE_TAIL, id="single-line"),
    pytest.param('fe80::1%a"\n}\nresource "terraform_data" "cortexsim_pwn" {\n'
                 '  provisioner "local-exec" {\n    command = "id"\n  }\n}\n'
                 'variable "z" {\n  default = "x/64', id="multi-line"),
    # No host bits set, so ``str(ip_network(v))`` KEEPS the zone id: returning
    # the canonical form instead of the raw input would not have been a fix.
    pytest.param("fe80::%a" + _ZONE_TAIL, id="network-address-form"),
    pytest.param("fe80::%${path.module}/64", id="template-sequence"),
    # Harmless text, refused all the same: a zone id names an interface on one
    # host and means nothing in a security-group rule.
    pytest.param("fe80::1%eth0/64", id="plain-zone-id"),
]


@pytest.mark.parametrize("cidr", CIDR_INJECTIONS)
def test_a_cidr_carrying_a_zone_id_is_refused_and_nothing_is_rendered(client, blueprints, cidr):
    resp = client.post("/api/infra/generate", json=_body(params={"dc_ssh_cidr": cidr}))

    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "BAD_INFRA_PARAM"
    assert [f["field"] for f in detail["detail"]["fields"]] == ["params.dc_ssh_cidr"]
    assert "zone id" in detail["detail"]["fields"][0]["expected"]
    assert list(blueprints.iterdir()) == [], "a refused request must leave no bundle"
    assert "local-exec" not in _rendered(blueprints)


@pytest.mark.parametrize("cidr", CIDR_INJECTIONS)
def test_the_generator_refuses_a_zone_id_cidr_built_without_the_route(blueprints, repo_root, cidr):
    from engine.infra_catalog import InfraCatalog
    from engine.infra_generator import GenerationError, InfraGenerator
    from engine.infra_models import InfraGenerateParams, InfraGenerateRequest

    gen = InfraGenerator(
        catalog=InfraCatalog(modules_root=repo_root / "infra" / "modules"),
        templates_dir=repo_root / "infra" / "templates", blueprints_dir=blueprints)
    request = InfraGenerateRequest(
        provider="aws", region="us-east-1", modules=["edr"],
        params=InfraGenerateParams(project_name="sec17", dc_ssh_cidr=cidr))

    with pytest.raises(GenerationError, match="dc_ssh_cidr"):
        gen.generate(request)
    assert list(blueprints.iterdir()) == []


@pytest.mark.parametrize("cidr", [
    "1.2.3.4/32",
    "203.0.113.0/24",
    "10.20.0.0/16",
    "203.0.113.7",               # bare address: the model has always read it as /32
    "2001:db8:1::/48",
    "2001:DB8:1:2::/64",
    "::ffff:203.0.113.7/128",
])
def test_real_cidrs_still_generate_and_render_as_typed(client, blueprints, cidr):
    resp = client.post("/api/infra/generate", json=_body(params={"dc_ssh_cidr": cidr}))

    assert resp.status_code == 200, resp.text
    rendered = _rendered(blueprints)
    assert f'default     = "{cidr}"' in rendered
    assert f'dc_ssh_cidr      = "{cidr}"' in rendered


def test_the_gate_reads_a_cidr_that_does_not_parse_as_refused():
    """For a caller that skipped model validation: the alphabet alone is not
    the shape, the value must also be a network."""
    from engine.infra_models import (InfraGenerateParams, InfraGenerateRequest,
                                     hcl_param_problems)

    def problems(cidr):
        params = InfraGenerateParams.model_construct(
            project_name="sec17", dc_ssh_cidr=cidr, jumpbox_size="t3.medium", tags={})
        request = InfraGenerateRequest.model_construct(
            provider="aws", region="us-east-1", modules=["edr"], params=params,
            adapter_refs=[])
        return [p["field"] for p in hcl_param_problems(request)]

    assert problems("1.2.3.4/32") == []
    for bad in ("999.1.1.1/99", "1.2.3.4/32\n", "", None, 5, "1.2.3.4/32 #"):
        assert problems(bad) == ["params.dc_ssh_cidr"], repr(bad)


# -----------------------------------------------------------------------------
# The class of miss, not the instance: every interpolation is accounted for
# -----------------------------------------------------------------------------

_HCL_TEMPLATES = ("main.tf.j2", "variables.tf.j2", "terraform.tfvars.j2", "outputs.tf.j2")
_INTERPOLATION = re.compile(r"{{\s*(.*?)\s*}}")

#: Values the generator makes itself (uuid4, an ISO timestamp).
_SERVER_MADE = {"bundle_id", "generated_at"}

#: Every request value a Terraform template interpolates -> where it sits in
#: the request, and a hostile value for it. A template that starts rendering a
#: new request value fails the test below until it is listed here, which is
#: the moment to decide what refuses it.
_HOSTILE_FOR: dict[str, tuple[str, object]] = {
    "provider": ("provider", 'aws"\n}'),
    "region": ("region", 'us-east-1"\n}'),
    "modules": ("modules", ['edr"\n}']),
    "project_name": ("params.project_name", 'sec17"\n}'),
    "dc_ssh_cidr": ("params.dc_ssh_cidr", 'fe80::%a"\n}\nvariable "z" { default = "x/64'),
    "jumpbox_size": ("params.jumpbox_size", 't3.medium"\n}'),
    "k8s_node_count": ("params.k8s_node_count", '2 }\nresource "terraform_data" "x" {}'),
    "edr_target_count": ("params.edr_target_count", '2 }\nresource "terraform_data" "x" {}'),
    "ttl_hours": ("params.ttl_hours", '72 }\nresource "terraform_data" "x" {}'),
    "tags": ("params.tags", {"Owner": 'x"\n}'}),
}


def test_every_request_value_a_terraform_template_renders_is_refused_when_hostile(repo_root):
    """The first fix named three fields and missed a fourth one line below
    them. This walks the templates instead of a list somebody remembered."""
    from pydantic import ValidationError

    from engine.infra_models import InfraGenerateRequest, hcl_param_problems

    names: set[str] = set()
    for template in _HCL_TEMPLATES:
        text = (repo_root / "infra" / "templates" / template).read_text(encoding="utf-8")
        names |= {expr.split("|")[0].strip() for expr in _INTERPOLATION.findall(text)}

    # A walk that finds nothing would pass everything below.
    assert {"region", "project_name", "dc_ssh_cidr", "jumpbox_size", "tags"} <= names, names
    undeclared = names - _SERVER_MADE - set(_HOSTILE_FOR)
    assert not undeclared, (
        f"infra/templates renders {sorted(undeclared)}, which this test does not know. "
        "Decide what refuses a hostile value for it (the request model, or "
        "engine.infra_models.hcl_param_problems), then list it in _HOSTILE_FOR.")

    survived = []
    for name in sorted(names - _SERVER_MADE):
        path, hostile = _HOSTILE_FOR[name]
        body = _body()
        target = body
        *parents, leaf = path.split(".")
        for part in parents:
            target = target[part]
        target[leaf] = hostile
        try:
            request = InfraGenerateRequest(**body)
        except ValidationError:
            continue                                    # the model refused it
        if not any(p["field"].startswith(path) for p in hcl_param_problems(request)):
            survived.append(name)
    assert survived == [], f"a hostile value for {survived} reaches the Terraform templates"
