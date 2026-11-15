"""
Pydantic models for the IaC topology generator.

Request shape:
    InfraGenerateRequest -> InfraGenerateResponse

Public catalog shape:
    InfraModuleMetadata (returned from GET /api/infra/modules)
    InfraBundleSummary  (returned from GET /api/infra/bundles)
"""
from __future__ import annotations

import ipaddress
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator


ALLOWED_PROVIDERS = ("aws", "gcp", "azure")

#: Narrowest-allowed prefix for ``dc_ssh_cidr``. /16 is 65,536 IPv4 addresses —
#: a whole site. For IPv6 the same idea is a site allocation, /48; an IPv6 /16
#: would be 2**112 addresses and no floor at all.
_MIN_PREFIX_V4 = 16
_MIN_PREFIX_V6 = 48

# Providers that actually ship Terraform modules on disk. gcp/azure are
# accepted by the Pydantic Literal (Phase C/D scope) but have ZERO modules,
# so requests against them must fail fast at the request boundary with a
# clear error rather than failing deep inside generation (GAP-IAC-004).
PROVIDERS_WITH_MODULES = ("aws",)

ALLOWED_MODULES = (
    "base",
    "edr",
    "cdr",
    "ndr",
    "itdr",
    "tim",
    "asm",
    "cspm",
    "ai-spm",
    "content-library",
    "telemetry-replay",
)

# region and jumpbox_size are rendered into Terraform string literals, so they
# are held to the shapes AWS actually uses (aws is the only provider with
# modules). Region: us-east-1, us-gov-west-1, ap-southeast-7. Instance type:
# t3.medium, m7i-flex.large, u-6tb1.metal.
AWS_REGION_PATTERN = r"^[a-z]{2}(-[a-z]+)+-[0-9]{1,2}$"
AWS_INSTANCE_TYPE_PATTERN = r"^[a-z][a-z0-9]*(-[a-z0-9]+)*\.[a-z0-9]+$"


class InfraGenerateParams(BaseModel):
    """Per-request parameters applied to the generated Terraform."""

    project_name: str = Field(
        ...,
        min_length=3,
        max_length=48,
        pattern=r"^[a-z0-9][a-z0-9-]*$",
        description="Lowercase-hyphen project name used as resource prefix",
    )
    dc_ssh_cidr: str = Field(..., description="CIDR allowed SSH access, e.g. 203.0.113.0/32")
    jumpbox_size: str = Field(
        default="t3.medium",
        max_length=32,
        pattern=AWS_INSTANCE_TYPE_PATTERN,
        description="AWS instance type, e.g. t3.medium",
    )
    k8s_node_count: int = Field(
        default=2, ge=1, le=10, description="Worker nodes for CDR module"
    )
    edr_target_count: int = Field(
        default=2, ge=1, le=10, description="Target VMs for EDR module"
    )
    ttl_hours: int = Field(
        default=72, ge=1, le=720, description="Hint for Torque environment TTL"
    )
    tags: dict[str, str] = Field(default_factory=dict)

    @field_validator("dc_ssh_cidr")
    @classmethod
    def _validate_cidr(cls, v: str) -> str:
        # This decides "is it a network, and is it narrow enough". It does NOT
        # decide whether the text is safe between HCL quotes: ip_network()
        # accepts an IPv6 zone id, which is free text. hcl_param_problems()
        # below is what refuses that, for the route and the generator alike.
        try:
            net = ipaddress.ip_network(v, strict=False)
        except ValueError as e:
            raise ValueError(f"invalid CIDR: {v}") from e
        # SEC-10. The base module's jumpbox security group applies this CIDR to
        # BOTH 22/tcp and 8888/tcp (infra/modules/aws/base/main.tf), so it is
        # also the only network control in front of SimCore's unauthenticated
        # API. The field's name says SSH; a DC typing 0.0.0.0/0 "to get SSH
        # working" published the API to the internet and nothing objected.
        floor = _MIN_PREFIX_V4 if net.version == 4 else _MIN_PREFIX_V6
        if net.prefixlen < floor:
            raise ValueError(
                f"dc_ssh_cidr {v} is too broad (/{net.prefixlen}; the floor is "
                f"/{floor} for IPv{net.version}). It also opens 8888/tcp — the "
                f"SimCore UI and API, which are unauthenticated — to every address "
                f"in it. Use the DC's own egress address (/32) or the lab's range."
            )
        return v


class InfraGenerateRequest(BaseModel):
    provider: Literal["aws", "gcp", "azure"]
    region: str = Field(..., min_length=3, max_length=32, pattern=AWS_REGION_PATTERN)
    modules: list[str] = Field(..., min_length=1)
    params: InfraGenerateParams
    adapter_refs: list[str] = Field(
        default_factory=list,
        description=(
            "Optional adapter_ref ids (e.g. 'TOOL-MIMIKATZ'). Each tier-3 "
            "adapter declares install.iac_module — the generator auto-"
            "includes those modules so a scenario that references "
            "{adapter:TOOL-RUBEUS} always ships with the itdr IaC module."
        ),
    )

    @field_validator("provider")
    @classmethod
    def _validate_provider_has_modules(cls, v: str) -> str:
        # gcp/azure are valid Literal values (reserved for Phase C/D) but ship
        # zero modules today. Reject at the request boundary so the operator
        # gets a clear, structured error instead of a deep GenerationError.
        if v not in PROVIDERS_WITH_MODULES:
            raise ValueError(
                f"provider '{v}' has no IaC modules yet (Phase C/D); "
                f"available providers: {', '.join(PROVIDERS_WITH_MODULES)}"
            )
        return v

    @field_validator("modules")
    @classmethod
    def _validate_modules(cls, v: list[str]) -> list[str]:
        for m in v:
            if m not in ALLOWED_MODULES:
                raise ValueError(f"unknown module: {m}")
        seen: set[str] = set()
        out: list[str] = []
        for m in v:
            if m not in seen:
                out.append(m)
                seen.add(m)
        return out


# -----------------------------------------------------------------------------
# SEC-17 — what may be written between two double quotes in generated HCL
# -----------------------------------------------------------------------------
#
# infra/templates/variables.tf.j2 and terraform.tfvars.j2 render
# `default = "{{ region }}"` / `"{{ jumpbox_size }}"` with Jinja autoescape off
# (it is HCL, not HTML), and `{{ tags | tojson }}`, whose JSON strings Terraform
# still reads as templates. A value with a double quote closed the string and
# the rest of it became HCL — a `local-exec` provisioner parsed and planned.
#
# The check is an ALLOW-list per field, applied before anything is rendered,
# never an attempt to escape: a region or an instance type has a known shape,
# and a value outside it is refused, not repaired.
#
# The same two templates quote four request strings, and each is bound once:
#
#   region, jumpbox_size   the shapes below
#   dc_ssh_cidr            _is_plain_cidr below
#   project_name           the request model's own pattern (InfraGenerateParams)
#
# plus `tags`. tests/api/test_infra_hcl_injection.py walks the templates and
# fails when one of them starts rendering a request value nothing refuses.

#: Per-provider shapes. Only AWS ships modules; the generic shapes cover the
#: providers the request model already accepts but the generator refuses.
_HCL_PARAM_SHAPES: dict[str, dict[str, tuple[re.Pattern[str], str]]] = {
    "aws": {
        # us-east-1 · eu-central-1 · ap-southeast-2 · us-gov-west-1
        "region": (re.compile(r"^[a-z]{2}(?:-[a-z]{2,12}){1,3}-[0-9]{1,2}$"),
                   "an AWS region such as us-east-1 or us-gov-west-1"),
        # t3.medium · c6i.2xlarge · m7i-flex.large · u-6tb1.metal
        "jumpbox_size": (re.compile(r"^[a-z][a-z0-9-]{0,22}\.[a-z0-9]{2,12}$"),
                         "an AWS instance type such as t3.medium or m7i-flex.large"),
    },
}
_HCL_GENERIC_SHAPES: dict[str, tuple[re.Pattern[str], str]] = {
    "region": (re.compile(r"^[a-z][a-z0-9-]{1,30}[a-z0-9]$"),
               "lowercase letters, digits and hyphens (3-32 characters)"),
    "jumpbox_size": (re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{1,62}$"),
                     "letters, digits, '.', '_' and '-' (2-63 characters)"),
}

#: AWS's own tag limits, and the characters it allows in a key or value. `$`,
#: `%`, `{`, `"`, `\` and control characters are all outside the set, so no
#: tag can open an HCL template sequence (`${` / `%{`) or close a string.
_TAG_MAX_COUNT = 50
_TAG_KEY_RE = re.compile(r"^[A-Za-z0-9 _.:/=+@-]{1,128}$")
_TAG_VALUE_RE = re.compile(r"^[A-Za-z0-9 _.:/=+@-]{0,256}$")
_TAG_EXPECTED = "letters, digits, space and _ . : / = + - @ (key 1-128, value 0-256)"

#: ``dc_ssh_cidr``. "ip_network() parsed it" is not a shape: an IPv6 zone id —
#: the text between ``%`` and the ``/`` — may hold anything but those two
#: characters, so ``fe80::1%a" } resource … { default = "x/64`` is a network to
#: the parser and rendered a ``local-exec`` provisioner exactly as an
#: unconstrained jumpbox_size did. The alphabet is what an address, a prefix
#: length and a dotted netmask are written in. ``%`` is outside it on purpose:
#: a zone id names an interface on one host and means nothing in a
#: security-group rule. (``str(ip_network(v))`` keeps the zone id whenever no
#: host bits are set, so rendering the canonical form would not have helped.)
_CIDR_RE = re.compile(r"^[0-9A-Fa-f:.]{2,45}(?:/[0-9.]{1,15})?$")
_CIDR_EXPECTED = ("an IPv4 or IPv6 network such as 203.0.113.7/32 or "
                  "2001:db8:1::/48, with no zone id ('%...')")


def _is_plain_cidr(value: object) -> bool:
    """A network written in the CIDR alphabet only, that also parses."""
    if not isinstance(value, str) or not _CIDR_RE.fullmatch(value):
        return False
    try:
        ipaddress.ip_network(value, strict=False)
    except ValueError:
        return False
    return True


def _shown(value: object) -> str:
    """A refused value, for an error body: bounded and repr-escaped."""
    text = repr(value)
    return text if len(text) <= 64 else text[:61] + "..."


def hcl_param_problems(request: "InfraGenerateRequest") -> list[dict[str, str]]:
    """Every request value that must not be rendered into Terraform, or ``[]``.

    One entry per refused value: ``{"field", "value", "expected"}``. Pure, and
    the single place the rule lives — the route turns a non-empty answer into
    ``400 BAD_INFRA_PARAM`` and the generator into a ``GenerationError``, so a
    caller that builds the request model itself is refused the same way.
    """
    shapes = {**_HCL_GENERIC_SHAPES, **_HCL_PARAM_SHAPES.get(request.provider, {})}
    problems: list[dict[str, str]] = []
    for field, value in (("region", request.region),
                         ("params.jumpbox_size", request.params.jumpbox_size)):
        pattern, expected = shapes[field.rsplit(".", 1)[-1]]
        if not isinstance(value, str) or not pattern.fullmatch(value):
            problems.append({"field": field, "value": _shown(value), "expected": expected})

    cidr = request.params.dc_ssh_cidr
    if not _is_plain_cidr(cidr):
        problems.append({"field": "params.dc_ssh_cidr", "value": _shown(cidr),
                         "expected": _CIDR_EXPECTED})

    tags = request.params.tags or {}
    if len(tags) > _TAG_MAX_COUNT:
        problems.append({"field": "params.tags", "value": f"{len(tags)} tags",
                         "expected": f"at most {_TAG_MAX_COUNT} tags"})
    for key, value in tags.items():
        if not isinstance(key, str) or not _TAG_KEY_RE.fullmatch(key):
            problems.append({"field": "params.tags (key)", "value": _shown(key),
                             "expected": _TAG_EXPECTED})
        elif not isinstance(value, str) or not _TAG_VALUE_RE.fullmatch(value):
            problems.append({"field": f"params.tags[{key}]", "value": _shown(value),
                             "expected": _TAG_EXPECTED})
    return problems


class InfraGenerateResponse(BaseModel):
    bundle_id: str
    provider: str
    modules: list[str]
    download_url: str
    files: list[str]
    auto_included_modules: list[str] = Field(
        default_factory=list,
        description=(
            "Modules added by adapter_refs[] resolution that were NOT in the "
            "request's modules[]. Surfaced to the UI so the operator sees "
            "what the auto-pull did."
        ),
    )


class InfraModuleMetadata(BaseModel):
    name: str
    description: str
    providers: list[str]
    required_params: list[str] = Field(default_factory=list)
    optional_params: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    content_tools: list[str] = Field(
        default_factory=list,
        description="Flattened list of tool names from content.yml",
    )


class InfraBundleSummary(BaseModel):
    bundle_id: str
    provider: str
    modules: list[str]
    created_at: str
    size_bytes: int
