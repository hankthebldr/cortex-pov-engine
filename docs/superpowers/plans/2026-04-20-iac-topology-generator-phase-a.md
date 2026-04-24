# IaC Topology Generator (Phase A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a feature in CortexSim that lets a DC generate a Terraform bundle (AWS provider; base + edr + cdr + content-library modules) that Torque can consume as a blueprint, provisioning both infrastructure and curated open-source attack/defense content.

**Architecture:** Python FastAPI generator engine reads static Terraform modules from `infra/modules/aws/` and renders a root Terraform configuration via Jinja2 templates. Output is a tar.gz bundle served over HTTP. A React UI panel drives the generation. A `install-content.sh` script runs on the provisioned jumpbox via cloud-init, reads per-module `content.yml` manifests, installs open-source tooling, and surfaces installed tools in SimCore's tool registry via `installed.json`.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, Jinja2, PyYAML, pytest, Terraform 1.6+ HCL, React 18, Vite.

**Spec:** `docs/superpowers/specs/2026-04-20-iac-topology-generator-design.md`

**Out of scope for Phase A (future phases):** GCP + Azure providers; ndr/itdr/tim/asm/cspm/telemetry-replay modules; scenario → module auto-suggestion UI wiring; verify-bundle pre-flight endpoint; end-to-end deploy-against-real-AWS smoke test.

---

## File Structure

### New files

**Python backend:**
- `core/engine/infra_models.py` — Pydantic models for generate request/response
- `core/engine/infra_catalog.py` — Loads module metadata from `infra/modules/` filesystem
- `core/engine/infra_generator.py` — Core generator (template rendering, bundle assembly)
- `core/api/infra.py` — FastAPI router with `/api/infra/*` endpoints
- `core/content_loader.py` — Reads `installed.json` and merges into `TOOL_REGISTRY`

**Terraform modules (AWS):**
- `infra/modules/aws/base/{main,variables,outputs}.tf` + `README.md` + `content.yml` + `userdata.sh.tftpl`
- `infra/modules/aws/edr/{main,variables,outputs}.tf` + `README.md` + `content.yml`
- `infra/modules/aws/cdr/{main,variables,outputs}.tf` + `README.md` + `content.yml`
- `infra/modules/aws/content-library/{README.md,content.yml}` (no .tf — content-only module)

**Jinja2 templates:**
- `infra/templates/main.tf.j2`
- `infra/templates/variables.tf.j2`
- `infra/templates/outputs.tf.j2`
- `infra/templates/terraform.tfvars.j2`
- `infra/templates/README.md.j2`

**Scripts:**
- `scripts/jumpbox/install-content.sh` — Content installer baked into jumpbox
- `scripts/jumpbox/bootstrap.sh` — Cloud-init top-level script

**Frontend:**
- `ui/src/components/InfraGenerator.jsx` — New UI panel

**Tests:**
- `tests/__init__.py`
- `tests/conftest.py`
- `tests/engine/__init__.py`
- `tests/engine/test_infra_models.py`
- `tests/engine/test_infra_catalog.py`
- `tests/engine/test_infra_generator.py`
- `tests/api/__init__.py`
- `tests/api/test_infra_api.py`
- `tests/test_content_loader.py`
- `tests/fixtures/modules/` — small fake module fixtures for generator tests

### Modified files

- `core/main.py` — Register new `infra` router; call `content_loader` on startup
- `core/tools/registry.py` — Extract static `TOOL_REGISTRY` into a function so loader can merge content
- `core/api/tools.py` — Use registry function instead of direct dict import (if applicable)
- `core/engine/scenario_loader.py` — Accept optional `required_content`, `infra_modules_needed` fields
- `scenarios/_schema.yml` — Document the new optional fields
- `core/requirements.txt` — Add `jinja2`, `pytest`, `pytest-asyncio`
- `ui/src/api/client.js` — Add `generateInfra`, `downloadInfraBundle`, `getInfraModules`, `getInfraBundles`
- `ui/src/App.jsx` — Add "Deploy" toggle button and route to `<InfraGenerator />`
- `.gitignore` — Ignore `infra/blueprints/`
- `CLAUDE.md` — Document the new feature, endpoints, and directory layout

---

## Task List

### Task 1: Project scaffolding — directories, gitignore, test setup

**Files:**
- Create: `infra/.gitkeep`
- Create: `infra/blueprints/.gitkeep`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/engine/__init__.py`
- Create: `tests/api/__init__.py`
- Create: `tests/fixtures/.gitkeep`
- Modify: `.gitignore`
- Modify: `core/requirements.txt`

- [ ] **Step 1: Create the directory skeleton**

```bash
mkdir -p infra/modules/aws/base
mkdir -p infra/modules/aws/edr
mkdir -p infra/modules/aws/cdr
mkdir -p infra/modules/aws/content-library
mkdir -p infra/templates
mkdir -p infra/blueprints
mkdir -p tests/engine
mkdir -p tests/api
mkdir -p tests/fixtures
mkdir -p scripts/jumpbox
touch infra/.gitkeep infra/blueprints/.gitkeep tests/fixtures/.gitkeep
touch tests/__init__.py tests/engine/__init__.py tests/api/__init__.py
```

- [ ] **Step 2: Update `.gitignore` to ignore generated bundles**

Read current `.gitignore`, then append these lines (do not duplicate existing entries):

```
# IaC generator
infra/blueprints/*
!infra/blueprints/.gitkeep

# pytest
.pytest_cache/
```

- [ ] **Step 3: Add test and template dependencies to `core/requirements.txt`**

Append to the end of `core/requirements.txt`:

```
jinja2==3.1.4
pytest==8.3.3
pytest-asyncio==0.24.0
```

- [ ] **Step 4: Install new dependencies into the existing venv**

Run: `/Users/henry/Github/Github_desktop/cortex-pov-engine/.venv/bin/pip install -r /Users/henry/Github/Github_desktop/cortex-pov-engine/core/requirements.txt`
Expected: installs jinja2, pytest, pytest-asyncio successfully.

- [ ] **Step 5: Create `tests/conftest.py` with a shared base-dir fixture**

```python
"""Shared pytest fixtures for CortexSim tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure core/ is on sys.path so we can import like production
REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_DIR = REPO_ROOT / "core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def fixtures_dir() -> Path:
    return REPO_ROOT / "tests" / "fixtures"
```

- [ ] **Step 6: Verify pytest can discover tests**

Run: `cd /Users/henry/Github/Github_desktop/cortex-pov-engine && .venv/bin/pytest tests/ --collect-only -q`
Expected: `no tests ran` (0 tests collected, no errors) — we haven't written tests yet.

- [ ] **Step 7: Commit**

```bash
git add infra/.gitkeep infra/blueprints/.gitkeep tests/ .gitignore core/requirements.txt scripts/
git commit -m "feat(infra): scaffold IaC generator directories and test harness"
```

---

### Task 2: Pydantic models for infra generate request/response

**Files:**
- Create: `core/engine/infra_models.py`
- Test: `tests/engine/test_infra_models.py`

- [ ] **Step 1: Write failing tests for the models**

Create `tests/engine/test_infra_models.py`:

```python
"""Tests for core.engine.infra_models Pydantic schemas."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from engine.infra_models import (
    InfraGenerateRequest,
    InfraGenerateResponse,
    InfraModuleMetadata,
    InfraBundleSummary,
)


class TestInfraGenerateRequest:
    def test_minimal_valid_request(self):
        req = InfraGenerateRequest(
            provider="aws",
            region="us-east-1",
            modules=["base"],
            params={"project_name": "acme-pov", "dc_ssh_cidr": "1.2.3.4/32"},
        )
        assert req.provider == "aws"
        assert req.modules == ["base"]

    def test_invalid_provider_rejected(self):
        with pytest.raises(ValidationError):
            InfraGenerateRequest(
                provider="oracle",  # not allowed
                region="us-east-1",
                modules=["base"],
                params={"project_name": "x", "dc_ssh_cidr": "1.2.3.4/32"},
            )

    def test_empty_modules_rejected(self):
        with pytest.raises(ValidationError):
            InfraGenerateRequest(
                provider="aws",
                region="us-east-1",
                modules=[],
                params={"project_name": "x", "dc_ssh_cidr": "1.2.3.4/32"},
            )

    def test_missing_project_name_rejected(self):
        with pytest.raises(ValidationError):
            InfraGenerateRequest(
                provider="aws",
                region="us-east-1",
                modules=["base"],
                params={"dc_ssh_cidr": "1.2.3.4/32"},
            )

    def test_invalid_cidr_rejected(self):
        with pytest.raises(ValidationError):
            InfraGenerateRequest(
                provider="aws",
                region="us-east-1",
                modules=["base"],
                params={"project_name": "x", "dc_ssh_cidr": "not-a-cidr"},
            )

    def test_defaults_applied(self):
        req = InfraGenerateRequest(
            provider="aws",
            region="us-east-1",
            modules=["base"],
            params={"project_name": "acme", "dc_ssh_cidr": "1.2.3.4/32"},
        )
        # jumpbox_size, ttl_hours should have defaults
        assert req.params.jumpbox_size == "t3.medium"
        assert req.params.ttl_hours == 72


class TestInfraGenerateResponse:
    def test_response_shape(self):
        resp = InfraGenerateResponse(
            bundle_id="abc-123",
            provider="aws",
            modules=["base", "edr"],
            download_url="/api/infra/bundles/abc-123/download",
            files=["main.tf", "variables.tf"],
        )
        assert resp.bundle_id == "abc-123"
        assert "main.tf" in resp.files


class TestInfraModuleMetadata:
    def test_module_metadata(self):
        m = InfraModuleMetadata(
            name="base",
            description="VPC, jumpbox, security groups",
            providers=["aws"],
            required_params=["project_name", "dc_ssh_cidr"],
            optional_params=["jumpbox_size"],
            dependencies=[],
        )
        assert m.name == "base"


class TestInfraBundleSummary:
    def test_bundle_summary(self):
        b = InfraBundleSummary(
            bundle_id="abc-123",
            provider="aws",
            modules=["base"],
            created_at="2026-04-20T12:00:00",
            size_bytes=1024,
        )
        assert b.size_bytes == 1024
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/henry/Github/Github_desktop/cortex-pov-engine && .venv/bin/pytest tests/engine/test_infra_models.py -v`
Expected: `ModuleNotFoundError: No module named 'engine.infra_models'`

- [ ] **Step 3: Implement `core/engine/infra_models.py`**

Create `core/engine/infra_models.py`:

```python
"""
Pydantic models for the IaC topology generator.

Request shape:
    InfraGenerateRequest → InfraGenerateResponse

Public catalog shape:
    InfraModuleMetadata (returned from GET /api/infra/modules)
    InfraBundleSummary  (returned from GET /api/infra/bundles)
"""
from __future__ import annotations

import ipaddress
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


ALLOWED_PROVIDERS = ("aws", "gcp", "azure")
ALLOWED_MODULES = ("base", "edr", "cdr", "ndr", "itdr", "tim", "asm", "cspm",
                   "content-library", "telemetry-replay")


class InfraGenerateParams(BaseModel):
    """Per-request parameters applied to the generated Terraform."""

    project_name: str = Field(..., min_length=3, max_length=48,
                              pattern=r"^[a-z0-9][a-z0-9-]*$",
                              description="Lowercase-hyphen project name used as resource prefix")
    dc_ssh_cidr: str = Field(..., description="CIDR allowed SSH access, e.g. 203.0.113.0/32")
    jumpbox_size: str = Field(default="t3.medium",
                              description="Provider-specific instance type")
    k8s_node_count: int = Field(default=2, ge=1, le=10,
                                description="Worker nodes for CDR module")
    edr_target_count: int = Field(default=2, ge=1, le=10,
                                  description="Target VMs for EDR module")
    ttl_hours: int = Field(default=72, ge=1, le=720,
                           description="Hint for Torque environment TTL")
    tags: dict[str, str] = Field(default_factory=dict)

    @field_validator("dc_ssh_cidr")
    @classmethod
    def _validate_cidr(cls, v: str) -> str:
        try:
            ipaddress.ip_network(v, strict=False)
        except ValueError as e:
            raise ValueError(f"invalid CIDR: {v}") from e
        return v


class InfraGenerateRequest(BaseModel):
    provider: Literal["aws", "gcp", "azure"]
    region: str = Field(..., min_length=3, max_length=32)
    modules: list[str] = Field(..., min_length=1)
    params: InfraGenerateParams

    @field_validator("modules")
    @classmethod
    def _validate_modules(cls, v: list[str]) -> list[str]:
        for m in v:
            if m not in ALLOWED_MODULES:
                raise ValueError(f"unknown module: {m}")
        # deduplicate, preserve order
        seen: set[str] = set()
        out: list[str] = []
        for m in v:
            if m not in seen:
                out.append(m)
                seen.add(m)
        return out


class InfraGenerateResponse(BaseModel):
    bundle_id: str
    provider: str
    modules: list[str]
    download_url: str
    files: list[str]


class InfraModuleMetadata(BaseModel):
    name: str
    description: str
    providers: list[str]
    required_params: list[str] = Field(default_factory=list)
    optional_params: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    content_tools: list[str] = Field(default_factory=list,
                                     description="Flattened list of tool names from content.yml")


class InfraBundleSummary(BaseModel):
    bundle_id: str
    provider: str
    modules: list[str]
    created_at: str
    size_bytes: int
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/engine/test_infra_models.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add core/engine/infra_models.py tests/engine/test_infra_models.py
git commit -m "feat(infra): Pydantic models for generate request/response"
```

---

### Task 3: Module catalog loader

**Files:**
- Create: `core/engine/infra_catalog.py`
- Create: `tests/fixtures/modules/aws/test_mod/README.md`
- Create: `tests/fixtures/modules/aws/test_mod/content.yml`
- Create: `tests/fixtures/modules/aws/test_mod/main.tf`
- Create: `tests/fixtures/modules/aws/test_mod/variables.tf`
- Test: `tests/engine/test_infra_catalog.py`

The catalog reads module metadata from the filesystem layout so that adding a new module is a matter of creating files, not editing Python.

- [ ] **Step 1: Create fixture module directory for tests**

Create `tests/fixtures/modules/aws/test_mod/README.md`:

```markdown
---
name: test_mod
description: Test fixture module
providers: [aws]
required_params: [project_name]
optional_params: [foo_bar]
dependencies: []
---

# Test Module
Fixture used only for catalog tests.
```

Create `tests/fixtures/modules/aws/test_mod/content.yml`:

```yaml
tools:
  category_a:
    - name: tool-one
      repo: example/tool-one
      install: git-clone
    - name: tool-two
      repo: example/tool-two
      install: binary-release
```

Create `tests/fixtures/modules/aws/test_mod/main.tf`:

```hcl
# minimal fixture — never actually applied
variable "project_name" {
  type = string
}
```

Create `tests/fixtures/modules/aws/test_mod/variables.tf`:

```hcl
# intentionally empty for fixture
```

- [ ] **Step 2: Write failing tests**

Create `tests/engine/test_infra_catalog.py`:

```python
"""Tests for core.engine.infra_catalog."""
from __future__ import annotations

from pathlib import Path

import pytest

from engine.infra_catalog import InfraCatalog


@pytest.fixture
def catalog(fixtures_dir: Path) -> InfraCatalog:
    modules_root = fixtures_dir / "modules"
    return InfraCatalog(modules_root=modules_root)


class TestInfraCatalog:
    def test_list_modules_for_provider(self, catalog: InfraCatalog):
        modules = catalog.list_modules(provider="aws")
        names = [m.name for m in modules]
        assert "test_mod" in names

    def test_list_modules_unknown_provider_empty(self, catalog: InfraCatalog):
        assert catalog.list_modules(provider="nobody") == []

    def test_get_module_metadata(self, catalog: InfraCatalog):
        meta = catalog.get_module(provider="aws", module="test_mod")
        assert meta is not None
        assert meta.description == "Test fixture module"
        assert meta.required_params == ["project_name"]
        assert "tool-one" in meta.content_tools
        assert "tool-two" in meta.content_tools

    def test_get_unknown_module_returns_none(self, catalog: InfraCatalog):
        assert catalog.get_module(provider="aws", module="does_not_exist") is None

    def test_module_path_returns_filesystem_path(self, catalog: InfraCatalog):
        path = catalog.module_path(provider="aws", module="test_mod")
        assert path is not None
        assert path.is_dir()
        assert (path / "main.tf").is_file()

    def test_content_manifest_parsed(self, catalog: InfraCatalog):
        manifest = catalog.load_content_manifest(provider="aws", module="test_mod")
        assert manifest is not None
        assert "tools" in manifest
        assert "category_a" in manifest["tools"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/engine/test_infra_catalog.py -v`
Expected: `ModuleNotFoundError: No module named 'engine.infra_catalog'`

- [ ] **Step 4: Implement the catalog**

Create `core/engine/infra_catalog.py`:

```python
"""
Module catalog loader for the IaC generator.

Reads module metadata from YAML frontmatter in each module's README.md and
flattens the content.yml tool list. Pure filesystem reads — no network,
no DB. Used by the generator and the /api/infra/modules endpoint.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Optional

import yaml

from engine.infra_models import InfraModuleMetadata

logger = logging.getLogger("cortexsim.infra_catalog")

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


class InfraCatalog:
    """
    Walks `infra/modules/{provider}/{module}/` and exposes metadata for each.

    Expected layout per module:
        README.md       with YAML frontmatter (name, description, providers, etc.)
        content.yml     optional, declares installable tools
        main.tf, variables.tf, outputs.tf  (may be absent for content-only modules)
    """

    def __init__(self, modules_root: Path) -> None:
        self._root = Path(modules_root)

    def list_modules(self, provider: str) -> list[InfraModuleMetadata]:
        provider_dir = self._root / provider
        if not provider_dir.is_dir():
            return []
        results: list[InfraModuleMetadata] = []
        for child in sorted(provider_dir.iterdir()):
            if not child.is_dir():
                continue
            meta = self._load_module_metadata(provider, child.name)
            if meta is not None:
                results.append(meta)
        return results

    def get_module(self, provider: str, module: str) -> Optional[InfraModuleMetadata]:
        return self._load_module_metadata(provider, module)

    def module_path(self, provider: str, module: str) -> Optional[Path]:
        p = self._root / provider / module
        return p if p.is_dir() else None

    def load_content_manifest(self, provider: str, module: str) -> Optional[dict[str, Any]]:
        p = self._root / provider / module / "content.yml"
        if not p.is_file():
            return None
        try:
            with p.open("r", encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {}
        except yaml.YAMLError:
            logger.exception("failed to parse content.yml for %s/%s", provider, module)
            return None

    # ------------------------------------------------------------------

    def _load_module_metadata(self, provider: str, module: str) -> Optional[InfraModuleMetadata]:
        readme = self._root / provider / module / "README.md"
        if not readme.is_file():
            return None

        text = readme.read_text(encoding="utf-8")
        match = _FRONTMATTER_RE.match(text)
        if not match:
            logger.warning("module %s/%s has README.md without frontmatter", provider, module)
            return None

        try:
            fm = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            logger.exception("invalid frontmatter YAML in %s/%s README.md", provider, module)
            return None

        content_tools = self._flatten_content_tools(provider, module)

        return InfraModuleMetadata(
            name=fm.get("name", module),
            description=fm.get("description", ""),
            providers=fm.get("providers", [provider]),
            required_params=fm.get("required_params", []),
            optional_params=fm.get("optional_params", []),
            dependencies=fm.get("dependencies", []),
            content_tools=content_tools,
        )

    def _flatten_content_tools(self, provider: str, module: str) -> list[str]:
        manifest = self.load_content_manifest(provider, module)
        if not manifest:
            return []
        tools = manifest.get("tools", {}) or {}
        out: list[str] = []
        for _category, entries in tools.items():
            for entry in entries or []:
                name = entry.get("name")
                if name:
                    out.append(name)
        return out
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/engine/test_infra_catalog.py -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add core/engine/infra_catalog.py tests/engine/test_infra_catalog.py tests/fixtures/
git commit -m "feat(infra): module catalog loader with filesystem-based metadata"
```

---

### Task 4: Write the AWS `base` Terraform module

**Files:**
- Create: `infra/modules/aws/base/main.tf`
- Create: `infra/modules/aws/base/variables.tf`
- Create: `infra/modules/aws/base/outputs.tf`
- Create: `infra/modules/aws/base/README.md`
- Create: `infra/modules/aws/base/content.yml`
- Create: `infra/modules/aws/base/userdata.sh.tftpl`

- [ ] **Step 1: Create `infra/modules/aws/base/variables.tf`**

```hcl
variable "project_name" {
  description = "Lowercase-hyphen project name used as resource prefix"
  type        = string
}

variable "region" {
  description = "AWS region for all resources"
  type        = string
}

variable "dc_ssh_cidr" {
  description = "CIDR allowed SSH access to the jumpbox"
  type        = string
}

variable "jumpbox_size" {
  description = "EC2 instance type for the jumpbox"
  type        = string
  default     = "t3.medium"
}

variable "tags" {
  description = "Additional tags applied to all resources"
  type        = map(string)
  default     = {}
}

variable "content_modules" {
  description = "List of module names whose content.yml should be processed on jumpbox boot"
  type        = list(string)
  default     = ["base"]
}
```

- [ ] **Step 2: Create `infra/modules/aws/base/main.tf`**

```hcl
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }
}

locals {
  name_prefix = var.project_name
  common_tags = merge({
    Project       = var.project_name
    ManagedBy     = "cortexsim-iac-generator"
  }, var.tags)
}

data "aws_availability_zones" "available" {
  state = "available"
}

# ----- VPC & networking -----------------------------------------------------

resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags                 = merge(local.common_tags, { Name = "${local.name_prefix}-vpc" })
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = merge(local.common_tags, { Name = "${local.name_prefix}-igw" })
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(aws_vpc.main.cidr_block, 8, count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true
  tags                    = merge(local.common_tags, { Name = "${local.name_prefix}-public-${count.index}" })
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(aws_vpc.main.cidr_block, 8, count.index + 10)
  availability_zone = data.aws_availability_zones.available.names[count.index]
  tags              = merge(local.common_tags, { Name = "${local.name_prefix}-private-${count.index}" })
}

resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = merge(local.common_tags, { Name = "${local.name_prefix}-nat-eip" })
}

resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id
  tags          = merge(local.common_tags, { Name = "${local.name_prefix}-nat" })
  depends_on    = [aws_internet_gateway.main]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = merge(local.common_tags, { Name = "${local.name_prefix}-public-rt" })
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }
  tags = merge(local.common_tags, { Name = "${local.name_prefix}-private-rt" })
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = length(aws_subnet.private)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# ----- Security group for jumpbox -----------------------------------------

resource "aws_security_group" "jumpbox" {
  name        = "${local.name_prefix}-jumpbox-sg"
  description = "CortexSim jumpbox: SSH from DC, SimCore UI from DC"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "SSH from DC"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.dc_ssh_cidr]
  }

  ingress {
    description = "SimCore UI from DC"
    from_port   = 8888
    to_port     = 8888
    protocol    = "tcp"
    cidr_blocks = [var.dc_ssh_cidr]
  }

  egress {
    description = "Outbound any"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-jumpbox-sg" })
}

# ----- SSH keypair (generated, stored in SSM) -----------------------------

resource "tls_private_key" "jumpbox" {
  algorithm = "ED25519"
}

resource "aws_key_pair" "jumpbox" {
  key_name   = "${local.name_prefix}-jumpbox"
  public_key = tls_private_key.jumpbox.public_key_openssh
  tags       = local.common_tags
}

resource "aws_ssm_parameter" "jumpbox_private_key" {
  name        = "/cortexsim/${local.name_prefix}/jumpbox-ssh-key"
  description = "Private SSH key for the CortexSim jumpbox"
  type        = "SecureString"
  value       = tls_private_key.jumpbox.private_key_openssh
  tags        = local.common_tags
}

# ----- Jumpbox EC2 --------------------------------------------------------

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_instance" "jumpbox" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.jumpbox_size
  subnet_id              = aws_subnet.public[0].id
  vpc_security_group_ids = [aws_security_group.jumpbox.id]
  key_name               = aws_key_pair.jumpbox.key_name

  user_data = templatefile("${path.module}/userdata.sh.tftpl", {
    content_modules = join(",", var.content_modules)
    project_name    = var.project_name
  })

  root_block_device {
    volume_size = 30
    volume_type = "gp3"
    encrypted   = true
  }

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-jumpbox" })
}
```

- [ ] **Step 3: Create `infra/modules/aws/base/outputs.tf`**

```hcl
output "vpc_id" {
  description = "ID of the CortexSim VPC"
  value       = aws_vpc.main.id
}

output "public_subnet_ids" {
  description = "IDs of the public subnets"
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "IDs of the private subnets"
  value       = aws_subnet.private[*].id
}

output "jumpbox_public_ip" {
  description = "Public IP of the jumpbox"
  value       = aws_instance.jumpbox.public_ip
}

output "jumpbox_private_ip" {
  description = "Private IP of the jumpbox"
  value       = aws_instance.jumpbox.private_ip
}

output "jumpbox_security_group_id" {
  description = "Security group ID attached to the jumpbox (reused by other modules)"
  value       = aws_security_group.jumpbox.id
}

output "ssh_key_name" {
  description = "AWS keypair name for the jumpbox"
  value       = aws_key_pair.jumpbox.key_name
}

output "ssh_private_key_ssm_path" {
  description = "SSM parameter path storing the private SSH key"
  value       = aws_ssm_parameter.jumpbox_private_key.name
}

output "region" {
  description = "Deployment region"
  value       = var.region
}

output "project_name" {
  description = "Project name used as resource prefix"
  value       = var.project_name
}
```

- [ ] **Step 4: Create `infra/modules/aws/base/userdata.sh.tftpl`**

```bash
#!/bin/bash
set -euo pipefail
exec > >(tee /var/log/cortexsim-bootstrap.log) 2>&1

echo "[cortexsim] bootstrap starting at $(date -u)"

# Wait for apt to be available
until apt-get update -qq; do sleep 5; done

apt-get install -y git curl ca-certificates docker.io docker-compose \
  python3.11 python3.11-venv python3-pip build-essential jq

usermod -aG docker ubuntu

# Clone CortexSim
su - ubuntu -c "git clone https://github.com/hankthebldr/cortexsim.git /home/ubuntu/cortexsim" || true

# Mark which content modules should be installed
mkdir -p /opt/cortexsim
echo "${content_modules}" > /opt/cortexsim/enabled-modules.txt
echo "${project_name}" > /opt/cortexsim/project-name.txt

# Run CortexSim installer and the content installer
if [ -x /home/ubuntu/cortexsim/install.sh ]; then
  su - ubuntu -c "cd /home/ubuntu/cortexsim && ./install.sh" || true
fi

if [ -x /home/ubuntu/cortexsim/scripts/jumpbox/install-content.sh ]; then
  /home/ubuntu/cortexsim/scripts/jumpbox/install-content.sh \
    --modules="${content_modules}" \
    --repo-root=/home/ubuntu/cortexsim || true
fi

echo "[cortexsim] bootstrap complete at $(date -u)"
```

- [ ] **Step 5: Create `infra/modules/aws/base/content.yml`**

```yaml
# Content installed on the jumpbox for the base module — detection content repos
# that DCs hand to customers. Pure git clones — no binaries, no services.
tools:
  detection_rules:
    - name: sigma
      repo: SigmaHQ/sigma
      install: git-clone
      install_path: /opt/cortexsim/content/base/sigma
    - name: mitre-car
      repo: mitre/car
      install: git-clone
      install_path: /opt/cortexsim/content/base/mitre-car
    - name: sigma-detection-rules
      repo: mdecrevoisier/SIGMA-detection-rules
      install: git-clone
      install_path: /opt/cortexsim/content/base/sigma-detection-rules
```

- [ ] **Step 6: Create `infra/modules/aws/base/README.md`**

```markdown
---
name: base
description: VPC, jumpbox with SimCore, security groups, NAT, SSH keypair. Always deployed.
providers: [aws]
required_params: [project_name, dc_ssh_cidr]
optional_params: [jumpbox_size, tags]
dependencies: []
---

# base (AWS)

Provisions the foundational CortexSim environment on AWS:

- VPC with 2 public + 2 private subnets across 2 AZs
- Internet gateway, NAT gateway
- Security group allowing SSH (22) and SimCore UI (8888) from the DC's CIDR
- Jumpbox EC2 (Ubuntu 22.04) with SimCore + content installer running at boot
- SSH keypair (generated in-place; private key stored in AWS SSM Parameter Store)

## Outputs consumed by other modules

- `vpc_id`, `public_subnet_ids`, `private_subnet_ids`
- `jumpbox_security_group_id`
- `ssh_key_name`

## Accessing the jumpbox

After `terraform apply`, retrieve the SSH private key:

```bash
aws ssm get-parameter --name /cortexsim/<project_name>/jumpbox-ssh-key \
  --with-decryption --query Parameter.Value --output text > jumpbox.pem
chmod 600 jumpbox.pem
ssh -i jumpbox.pem ubuntu@$(terraform output -raw jumpbox_public_ip)
```
```

- [ ] **Step 7: Validate the Terraform syntactically**

Run: `cd /Users/henry/Github/Github_desktop/cortex-pov-engine/infra/modules/aws/base && terraform init -backend=false && terraform validate`
Expected: `Success! The configuration is valid.` (if terraform isn't installed, skip this step but note it must pass in CI).

- [ ] **Step 8: Commit**

```bash
git add infra/modules/aws/base/
git commit -m "feat(infra): AWS base module — VPC, jumpbox, SG, NAT, SSH keypair"
```

---

### Task 5: Write the AWS `edr` Terraform module

**Files:**
- Create: `infra/modules/aws/edr/main.tf`
- Create: `infra/modules/aws/edr/variables.tf`
- Create: `infra/modules/aws/edr/outputs.tf`
- Create: `infra/modules/aws/edr/README.md`
- Create: `infra/modules/aws/edr/content.yml`

- [ ] **Step 1: Create `infra/modules/aws/edr/variables.tf`**

```hcl
variable "project_name" {
  description = "Lowercase-hyphen project name used as resource prefix"
  type        = string
}

variable "vpc_id" {
  description = "ID of the VPC from the base module"
  type        = string
}

variable "private_subnet_ids" {
  description = "List of private subnet IDs from the base module"
  type        = list(string)
}

variable "jumpbox_security_group_id" {
  description = "SG of the jumpbox — target hosts allow SSH from here"
  type        = string
}

variable "ssh_key_name" {
  description = "Name of the AWS keypair to attach to target hosts"
  type        = string
}

variable "target_count" {
  description = "Number of target EDR hosts"
  type        = number
  default     = 2
}

variable "target_size" {
  description = "EC2 instance type for target hosts"
  type        = string
  default     = "t3.small"
}

variable "tags" {
  description = "Additional tags applied to all resources"
  type        = map(string)
  default     = {}
}
```

- [ ] **Step 2: Create `infra/modules/aws/edr/main.tf`**

```hcl
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

locals {
  name_prefix = var.project_name
  common_tags = merge({
    Project   = var.project_name
    Module    = "edr"
    ManagedBy = "cortexsim-iac-generator"
  }, var.tags)

  target_amis = [
    # Diverse OS images for realistic EDR testing
    { name = "ubuntu",   owner = "099720109477", filter = "ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*" },
    { name = "amzn2",    owner = "137112412989", filter = "amzn2-ami-kernel-5.10-hvm-*-x86_64-gp2" },
  ]
}

data "aws_ami" "targets" {
  count       = length(local.target_amis)
  most_recent = true
  owners      = [local.target_amis[count.index].owner]
  filter {
    name   = "name"
    values = [local.target_amis[count.index].filter]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_security_group" "target" {
  name        = "${local.name_prefix}-edr-target-sg"
  description = "CortexSim EDR target hosts — SSH from jumpbox only"
  vpc_id      = var.vpc_id

  ingress {
    description     = "SSH from jumpbox"
    from_port       = 22
    to_port         = 22
    protocol        = "tcp"
    security_groups = [var.jumpbox_security_group_id]
  }

  ingress {
    description = "Inter-target (same SG, for lateral movement simulation)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    self        = true
  }

  egress {
    description = "Outbound any"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-edr-target-sg" })
}

resource "aws_instance" "target" {
  count                  = var.target_count
  ami                    = data.aws_ami.targets[count.index % length(local.target_amis)].id
  instance_type          = var.target_size
  subnet_id              = var.private_subnet_ids[count.index % length(var.private_subnet_ids)]
  vpc_security_group_ids = [aws_security_group.target.id]
  key_name               = var.ssh_key_name

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-edr-target-${count.index}"
    OS   = local.target_amis[count.index % length(local.target_amis)].name
  })
}
```

- [ ] **Step 3: Create `infra/modules/aws/edr/outputs.tf`**

```hcl
output "target_private_ips" {
  description = "Private IPs of EDR target hosts (reachable from jumpbox)"
  value       = aws_instance.target[*].private_ip
}

output "target_instance_ids" {
  description = "EC2 instance IDs of EDR targets"
  value       = aws_instance.target[*].id
}

output "target_security_group_id" {
  description = "Security group ID for target hosts"
  value       = aws_security_group.target.id
}
```

- [ ] **Step 4: Create `infra/modules/aws/edr/content.yml`**

```yaml
tools:
  attack_simulation:
    - name: atomic-red-team
      repo: redcanaryco/atomic-red-team
      install: git-clone
      install_path: /opt/cortexsim/content/edr/atomic-red-team
    - name: edr-testing-script
      repo: op7ic/EDR-Testing-Script
      install: git-clone
      install_path: /opt/cortexsim/content/edr/edr-testing-script
    - name: lolbas
      repo: api0cradle/LOLBAS
      install: git-clone
      install_path: /opt/cortexsim/content/edr/lolbas
    - name: sliver
      repo: bishopfox/sliver
      install: binary-release
  ransomware_simulation:
    - name: cipherstrike
      repo: Cursed271/CipherStrike
      install: git-clone
      install_path: /opt/cortexsim/content/edr/cipherstrike
    - name: ransim
      repo: lawndoc/RanSim
      install: git-clone
      install_path: /opt/cortexsim/content/edr/ransim
    - name: simulate-black-basta
      repo: skandler/simulate-black-basta
      install: git-clone
      install_path: /opt/cortexsim/content/edr/simulate-black-basta
    - name: simulate-akira
      repo: skandler/simulate-akira
      install: git-clone
      install_path: /opt/cortexsim/content/edr/simulate-akira
  telemetry_samples:
    - name: evtx-attack-samples
      repo: sbousseaden/EVTX-ATTACK-SAMPLES
      install: git-clone
      install_path: /opt/cortexsim/content/edr/evtx-attack-samples
      purpose: "Replay into XSIAM for parser validation"
    - name: mordor
      repo: OTRF/mordor
      install: git-clone
      install_path: /opt/cortexsim/content/edr/mordor
```

- [ ] **Step 5: Create `infra/modules/aws/edr/README.md`**

```markdown
---
name: edr
description: Linux target VMs for endpoint detection scenarios (credential dumping, reverse shell, persistence, defense evasion, lateral movement)
providers: [aws]
required_params: [project_name]
optional_params: [target_count, target_size]
dependencies: [base]
---

# edr (AWS)

Provisions 1-10 target Linux VMs in the private subnets of the base VPC, alternating between Ubuntu 22.04 and Amazon Linux 2 for diverse EDR telemetry. Hosts are reachable only from the jumpbox and each other.

## Content installed

Attack simulation: atomic-red-team, EDR-Testing-Script, LOLBAS, sliver.
Ransomware: CipherStrike, RanSim, simulate-black-basta, simulate-akira.
Samples: EVTX-ATTACK-SAMPLES, mordor.

Content is installed on the **jumpbox**, not the target hosts. The jumpbox uses the beacon agent to push TTP commands to targets.
```

- [ ] **Step 6: Validate syntactically**

Run: `cd infra/modules/aws/edr && terraform init -backend=false && terraform validate` (skip if terraform not installed).

- [ ] **Step 7: Commit**

```bash
git add infra/modules/aws/edr/
git commit -m "feat(infra): AWS edr module — diverse OS target VMs with attack/ransomware content"
```

---

### Task 6: Write the AWS `cdr` Terraform module

**Files:**
- Create: `infra/modules/aws/cdr/main.tf`
- Create: `infra/modules/aws/cdr/variables.tf`
- Create: `infra/modules/aws/cdr/outputs.tf`
- Create: `infra/modules/aws/cdr/README.md`
- Create: `infra/modules/aws/cdr/content.yml`

- [ ] **Step 1: Create `infra/modules/aws/cdr/variables.tf`**

```hcl
variable "project_name" {
  description = "Lowercase-hyphen project name used as resource prefix"
  type        = string
}

variable "vpc_id" {
  description = "ID of the VPC from the base module"
  type        = string
}

variable "private_subnet_ids" {
  description = "List of private subnet IDs from the base module"
  type        = list(string)
}

variable "k8s_version" {
  description = "EKS control plane version"
  type        = string
  default     = "1.29"
}

variable "node_count" {
  description = "Number of worker nodes"
  type        = number
  default     = 2
}

variable "node_size" {
  description = "EC2 instance type for worker nodes"
  type        = string
  default     = "t3.medium"
}

variable "tags" {
  description = "Additional tags applied to all resources"
  type        = map(string)
  default     = {}
}
```

- [ ] **Step 2: Create `infra/modules/aws/cdr/main.tf`**

```hcl
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

locals {
  name_prefix = var.project_name
  cluster_name = "${var.project_name}-cdr"
  common_tags = merge({
    Project   = var.project_name
    Module    = "cdr"
    ManagedBy = "cortexsim-iac-generator"
  }, var.tags)
}

# ----- EKS IAM roles -------------------------------------------------------

data "aws_iam_policy_document" "cluster_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "cluster" {
  name               = "${local.cluster_name}-cluster-role"
  assume_role_policy = data.aws_iam_policy_document.cluster_assume.json
  tags               = local.common_tags
}

resource "aws_iam_role_policy_attachment" "cluster_policy" {
  role       = aws_iam_role.cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

data "aws_iam_policy_document" "node_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "node" {
  name               = "${local.cluster_name}-node-role"
  assume_role_policy = data.aws_iam_policy_document.node_assume.json
  tags               = local.common_tags
}

resource "aws_iam_role_policy_attachment" "node_worker" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
}

resource "aws_iam_role_policy_attachment" "node_cni" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
}

resource "aws_iam_role_policy_attachment" "node_ecr" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

# ----- EKS cluster --------------------------------------------------------

resource "aws_eks_cluster" "main" {
  name     = local.cluster_name
  role_arn = aws_iam_role.cluster.arn
  version  = var.k8s_version

  vpc_config {
    subnet_ids              = var.private_subnet_ids
    endpoint_private_access = true
    endpoint_public_access  = true
  }

  tags = merge(local.common_tags, { Name = local.cluster_name })

  depends_on = [aws_iam_role_policy_attachment.cluster_policy]
}

resource "aws_eks_node_group" "main" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "${local.cluster_name}-ng"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = var.private_subnet_ids
  instance_types  = [var.node_size]

  scaling_config {
    desired_size = var.node_count
    min_size     = 1
    max_size     = var.node_count + 2
  }

  tags = local.common_tags

  depends_on = [
    aws_iam_role_policy_attachment.node_worker,
    aws_iam_role_policy_attachment.node_cni,
    aws_iam_role_policy_attachment.node_ecr,
  ]
}
```

- [ ] **Step 3: Create `infra/modules/aws/cdr/outputs.tf`**

```hcl
output "cluster_name" {
  description = "EKS cluster name"
  value       = aws_eks_cluster.main.name
}

output "cluster_endpoint" {
  description = "EKS API endpoint"
  value       = aws_eks_cluster.main.endpoint
}

output "cluster_certificate_authority" {
  description = "Base64-encoded CA cert for kubeconfig"
  value       = aws_eks_cluster.main.certificate_authority[0].data
}

output "kubeconfig_command" {
  description = "Command to configure kubectl for this cluster"
  value       = "aws eks update-kubeconfig --region ${data.aws_region.current.name} --name ${aws_eks_cluster.main.name}"
}

data "aws_region" "current" {}
```

- [ ] **Step 4: Create `infra/modules/aws/cdr/content.yml`**

```yaml
tools:
  attack_simulation:
    - name: deepce
      repo: stealthcopter/deepce
      install: git-clone
      install_path: /opt/cortexsim/content/cdr/deepce
    - name: botb
      repo: brompwnie/botb
      install: git-clone
      install_path: /opt/cortexsim/content/cdr/botb
    - name: kube-hunter
      repo: aquasecurity/kube-hunter
      install: pip-install
    - name: light-k8s-attack-simulations
      repo: lightspin-tech/light-k8s-attack-simulations
      install: git-clone
      install_path: /opt/cortexsim/content/cdr/light-k8s-attack-simulations
    - name: kubehound
      repo: DataDog/KubeHound
      install: git-clone
      install_path: /opt/cortexsim/content/cdr/kubehound
  defensive_tools:
    - name: falco
      repo: falcosecurity/falco
      install: docker-pull
      image: falcosecurity/falco:latest
    - name: falco-rules
      repo: falcosecurity/rules
      install: git-clone
      install_path: /opt/cortexsim/content/cdr/falco-rules
    - name: tetragon
      repo: cilium/tetragon
      install: docker-pull
      image: quay.io/cilium/tetragon:latest
    - name: tracee
      repo: aquasecurity/tracee
      install: docker-pull
      image: aquasec/tracee:latest
    - name: trivy
      repo: aquasecurity/trivy
      install: binary-release
    - name: grype
      repo: anchore/grype
      install: binary-release
```

- [ ] **Step 5: Create `infra/modules/aws/cdr/README.md`**

```markdown
---
name: cdr
description: EKS cluster with worker nodes for container/Kubernetes detection scenarios
providers: [aws]
required_params: [project_name]
optional_params: [node_count, node_size, k8s_version]
dependencies: [base]
---

# cdr (AWS)

Provisions an EKS cluster with a managed node group inside the base VPC's private subnets.

## Content installed

Attack: deepce, botb, kube-hunter, light-k8s-attack-simulations, KubeHound.
Defense: falco + falco-rules, tetragon, tracee, trivy, grype.

## Connecting kubectl

```bash
aws eks update-kubeconfig --region <region> --name <project_name>-cdr
kubectl get nodes
```
```

- [ ] **Step 6: Commit**

```bash
git add infra/modules/aws/cdr/
git commit -m "feat(infra): AWS cdr module — EKS cluster with k8s attack/defense content"
```

---

### Task 7: Write the AWS `content-library` module (content-only)

**Files:**
- Create: `infra/modules/aws/content-library/README.md`
- Create: `infra/modules/aws/content-library/content.yml`

No Terraform — this module only contributes content manifests to the installer.

- [ ] **Step 1: Create `infra/modules/aws/content-library/README.md`**

```markdown
---
name: content-library
description: Detection content repositories for customer hand-off (SIEM rules, XQL queries, BIOCs). Installs on the jumpbox only — no infrastructure provisioned.
providers: [aws, gcp, azure]
required_params: []
optional_params: []
dependencies: [base]
---

# content-library

Clones curated detection content repositories onto the jumpbox. Selecting this module produces no additional cloud resources — it only adds entries to the jumpbox content installer.

## Content installed

- Palo Alto Cortex: xql-hub, cortex-xql-queries, XDR_scripts, CortexXDR-BIOC
- Splunk: splunk/security_content
- Elastic: elastic/detection-rules
- Chronicle: chronicle/detection-rules
```

- [ ] **Step 2: Create `infra/modules/aws/content-library/content.yml`**

```yaml
tools:
  cortex:
    - name: xql-hub
      repo: intrusus-dev/xql-hub
      install: git-clone
      install_path: /opt/cortexsim/content/content-library/xql-hub
    - name: cortex-xql-queries
      repo: PaloAltoNetworks/cortex-xql-queries
      install: git-clone
      install_path: /opt/cortexsim/content/content-library/cortex-xql-queries
    - name: xdr-scripts
      repo: k4nfr3/XDR_scripts
      install: git-clone
      install_path: /opt/cortexsim/content/content-library/xdr-scripts
    - name: cortexxdr-bioc
      repo: Data-Equipment-AS/CortexXDR-BIOC
      install: git-clone
      install_path: /opt/cortexsim/content/content-library/cortexxdr-bioc
  splunk:
    - name: splunk-security-content
      repo: splunk/security_content
      install: git-clone
      install_path: /opt/cortexsim/content/content-library/splunk-security-content
  elastic:
    - name: elastic-detection-rules
      repo: elastic/detection-rules
      install: git-clone
      install_path: /opt/cortexsim/content/content-library/elastic-detection-rules
  chronicle:
    - name: chronicle-detection-rules
      repo: chronicle/detection-rules
      install: git-clone
      install_path: /opt/cortexsim/content/content-library/chronicle-detection-rules
```

- [ ] **Step 3: Commit**

```bash
git add infra/modules/aws/content-library/
git commit -m "feat(infra): content-library module for SIEM detection content handoff"
```

---

### Task 8: Write the Jinja2 root-bundle templates

**Files:**
- Create: `infra/templates/main.tf.j2`
- Create: `infra/templates/variables.tf.j2`
- Create: `infra/templates/outputs.tf.j2`
- Create: `infra/templates/terraform.tfvars.j2`
- Create: `infra/templates/README.md.j2`

- [ ] **Step 1: Create `infra/templates/main.tf.j2`**

```hcl
# Auto-generated by CortexSim IaC Topology Generator
# Bundle ID : {{ bundle_id }}
# Provider  : {{ provider }}
# Modules   : {{ modules | join(", ") }}
# Generated : {{ generated_at }}

terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      CortexSimBundle = "{{ bundle_id }}"
      Project         = var.project_name
    }
  }
}

module "base" {
  source = "./modules/base"

  project_name    = var.project_name
  region          = var.region
  dc_ssh_cidr     = var.dc_ssh_cidr
  jumpbox_size    = var.jumpbox_size
  tags            = var.tags
  content_modules = {{ modules | tojson }}
}
{% if "edr" in modules %}
module "edr" {
  source = "./modules/edr"

  project_name              = var.project_name
  vpc_id                    = module.base.vpc_id
  private_subnet_ids        = module.base.private_subnet_ids
  jumpbox_security_group_id = module.base.jumpbox_security_group_id
  ssh_key_name              = module.base.ssh_key_name
  target_count              = var.edr_target_count
  tags                      = var.tags
}
{% endif %}
{% if "cdr" in modules %}
module "cdr" {
  source = "./modules/cdr"

  project_name       = var.project_name
  vpc_id             = module.base.vpc_id
  private_subnet_ids = module.base.private_subnet_ids
  node_count         = var.k8s_node_count
  tags               = var.tags
}
{% endif %}
# Note: content-library is content-only (no Terraform resources)
```

- [ ] **Step 2: Create `infra/templates/variables.tf.j2`**

```hcl
variable "region" {
  description = "AWS region"
  type        = string
  default     = "{{ region }}"
}

variable "project_name" {
  description = "Resource prefix / tag"
  type        = string
  default     = "{{ project_name }}"
}

variable "dc_ssh_cidr" {
  description = "DC's CIDR for SSH + SimCore UI access"
  type        = string
  default     = "{{ dc_ssh_cidr }}"
}

variable "jumpbox_size" {
  description = "Jumpbox instance type"
  type        = string
  default     = "{{ jumpbox_size }}"
}

variable "k8s_node_count" {
  description = "CDR module worker node count"
  type        = number
  default     = {{ k8s_node_count }}
}

variable "edr_target_count" {
  description = "EDR module target host count"
  type        = number
  default     = {{ edr_target_count }}
}

variable "tags" {
  description = "Additional tags applied to all resources"
  type        = map(string)
  default     = {{ tags | tojson }}
}
```

- [ ] **Step 3: Create `infra/templates/outputs.tf.j2`**

```hcl
output "jumpbox_public_ip" {
  description = "Public IP of the jumpbox"
  value       = module.base.jumpbox_public_ip
}

output "ssh_private_key_ssm_path" {
  description = "SSM parameter storing the jumpbox SSH key"
  value       = module.base.ssh_private_key_ssm_path
}

output "simcore_url" {
  description = "URL to reach SimCore UI"
  value       = "http://${module.base.jumpbox_public_ip}:8888"
}
{% if "edr" in modules %}
output "edr_target_private_ips" {
  description = "Private IPs of EDR target hosts (reachable from jumpbox)"
  value       = module.edr.target_private_ips
}
{% endif %}
{% if "cdr" in modules %}
output "cdr_kubeconfig_command" {
  description = "Command to configure kubectl for the EKS cluster"
  value       = module.cdr.kubeconfig_command
}
{% endif %}
```

- [ ] **Step 4: Create `infra/templates/terraform.tfvars.j2`**

```hcl
region           = "{{ region }}"
project_name     = "{{ project_name }}"
dc_ssh_cidr      = "{{ dc_ssh_cidr }}"
jumpbox_size     = "{{ jumpbox_size }}"
k8s_node_count   = {{ k8s_node_count }}
edr_target_count = {{ edr_target_count }}
tags = {{ tags | tojson }}
```

- [ ] **Step 5: Create `infra/templates/README.md.j2`**

```markdown
# CortexSim POV Environment — `{{ project_name }}`

> Generated by CortexSim IaC Topology Generator on {{ generated_at }}
> Bundle ID: `{{ bundle_id }}`

## What this deploys

- **Provider:** {{ provider }}
- **Region:** {{ region }}
- **Modules:** {{ modules | join(", ") }}

## Prerequisites

- Terraform 1.6+ with the AWS provider
- AWS credentials configured (via Torque, env vars, or `~/.aws/credentials`)
- An SSH client on your workstation

## Deploying via Torque

Push this bundle to your Torque blueprint repository and launch a new environment.
The generated `terraform.tfvars` contains your selected parameters; override per-environment as needed.

## Deploying manually

```bash
terraform init
terraform plan
terraform apply
```

## Accessing the jumpbox

After apply, retrieve the SSH key from AWS SSM and SSH in:

```bash
aws ssm get-parameter \
  --name $(terraform output -raw ssh_private_key_ssm_path) \
  --with-decryption \
  --query Parameter.Value --output text > jumpbox.pem
chmod 600 jumpbox.pem
ssh -i jumpbox.pem ubuntu@$(terraform output -raw jumpbox_public_ip)
```

SimCore UI: `$(terraform output -raw simcore_url)`

## Expected boot time

Bootstrapping the jumpbox (cloning repos, installing content) takes ~10–15 minutes
after `terraform apply` completes. Check `/var/log/cortexsim-bootstrap.log` on the
jumpbox to watch progress.

## Tearing down

```bash
terraform destroy
```

When using Torque, the environment's TTL triggers automatic destroy.
```

- [ ] **Step 6: Commit**

```bash
git add infra/templates/
git commit -m "feat(infra): Jinja2 root-bundle templates (main/variables/outputs/tfvars/README)"
```

---

### Task 9: Implement the generator engine

**Files:**
- Create: `core/engine/infra_generator.py`
- Test: `tests/engine/test_infra_generator.py`

- [ ] **Step 1: Write failing tests**

Create `tests/engine/test_infra_generator.py`:

```python
"""Tests for core.engine.infra_generator.InfraGenerator."""
from __future__ import annotations

import shutil
import tarfile
from pathlib import Path

import pytest

from engine.infra_catalog import InfraCatalog
from engine.infra_generator import InfraGenerator, GenerationError
from engine.infra_models import InfraGenerateParams, InfraGenerateRequest


@pytest.fixture
def real_catalog(repo_root: Path) -> InfraCatalog:
    return InfraCatalog(modules_root=repo_root / "infra" / "modules")


@pytest.fixture
def templates_dir(repo_root: Path) -> Path:
    return repo_root / "infra" / "templates"


@pytest.fixture
def blueprints_dir(tmp_path: Path) -> Path:
    d = tmp_path / "blueprints"
    d.mkdir()
    return d


@pytest.fixture
def generator(real_catalog: InfraCatalog, templates_dir: Path,
              blueprints_dir: Path) -> InfraGenerator:
    return InfraGenerator(
        catalog=real_catalog,
        templates_dir=templates_dir,
        blueprints_dir=blueprints_dir,
    )


def _request(modules: list[str]) -> InfraGenerateRequest:
    return InfraGenerateRequest(
        provider="aws",
        region="us-east-1",
        modules=modules,
        params=InfraGenerateParams(project_name="test-pov",
                                   dc_ssh_cidr="203.0.113.0/32"),
    )


class TestInfraGenerator:
    def test_base_module_always_included(self, generator: InfraGenerator):
        bundle = generator.generate(_request(["edr"]))
        assert "base" in bundle.modules
        assert "edr" in bundle.modules

    def test_generates_root_tf_files(self, generator: InfraGenerator,
                                     blueprints_dir: Path):
        bundle = generator.generate(_request(["edr"]))
        bundle_dir = blueprints_dir / bundle.bundle_id
        assert (bundle_dir / "main.tf").is_file()
        assert (bundle_dir / "variables.tf").is_file()
        assert (bundle_dir / "outputs.tf").is_file()
        assert (bundle_dir / "terraform.tfvars").is_file()
        assert (bundle_dir / "README.md").is_file()

    def test_copies_selected_modules(self, generator: InfraGenerator,
                                     blueprints_dir: Path):
        bundle = generator.generate(_request(["edr", "cdr"]))
        bundle_dir = blueprints_dir / bundle.bundle_id
        assert (bundle_dir / "modules" / "base" / "main.tf").is_file()
        assert (bundle_dir / "modules" / "edr" / "main.tf").is_file()
        assert (bundle_dir / "modules" / "cdr" / "main.tf").is_file()

    def test_omits_unselected_modules(self, generator: InfraGenerator,
                                      blueprints_dir: Path):
        bundle = generator.generate(_request(["edr"]))
        bundle_dir = blueprints_dir / bundle.bundle_id
        assert not (bundle_dir / "modules" / "cdr").exists()

    def test_generates_tar_archive(self, generator: InfraGenerator,
                                   blueprints_dir: Path):
        bundle = generator.generate(_request(["edr"]))
        archive = blueprints_dir / f"{bundle.bundle_id}.tar.gz"
        assert archive.is_file()
        with tarfile.open(archive, "r:gz") as tar:
            names = tar.getnames()
        assert any(n.endswith("main.tf") for n in names)
        assert any("modules/base" in n for n in names)

    def test_unknown_module_raises(self, generator: InfraGenerator):
        # pydantic blocks at request time; generator also validates module exists on disk
        with pytest.raises(GenerationError):
            req = _request(["edr"])
            # Manually sneak in an invalid module (bypasses pydantic) via direct generate call
            req.modules.append("nonexistent")
            generator.generate(req)

    def test_list_bundles(self, generator: InfraGenerator):
        b1 = generator.generate(_request(["edr"]))
        b2 = generator.generate(_request(["cdr"]))
        summaries = generator.list_bundles()
        ids = [s.bundle_id for s in summaries]
        assert b1.bundle_id in ids
        assert b2.bundle_id in ids

    def test_archive_path(self, generator: InfraGenerator):
        bundle = generator.generate(_request(["edr"]))
        p = generator.archive_path(bundle.bundle_id)
        assert p is not None
        assert p.is_file()

    def test_archive_path_unknown(self, generator: InfraGenerator):
        assert generator.archive_path("does-not-exist") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/engine/test_infra_generator.py -v`
Expected: `ModuleNotFoundError: No module named 'engine.infra_generator'`

- [ ] **Step 3: Implement the generator**

Create `core/engine/infra_generator.py`:

```python
"""
Core IaC bundle generator.

Responsibilities
----------------
1. Enforce invariants (base always present, module must exist for provider,
   dependencies satisfied).
2. Render Jinja2 root-bundle templates (main.tf, variables.tf, outputs.tf,
   terraform.tfvars, README.md) with request parameters.
3. Copy selected module directories into the bundle.
4. tar.gz the bundle for download.
"""
from __future__ import annotations

import logging
import shutil
import tarfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from engine.infra_catalog import InfraCatalog
from engine.infra_models import (
    InfraBundleSummary,
    InfraGenerateRequest,
    InfraGenerateResponse,
)

logger = logging.getLogger("cortexsim.infra_generator")

REQUIRED_TEMPLATES = [
    "main.tf.j2",
    "variables.tf.j2",
    "outputs.tf.j2",
    "terraform.tfvars.j2",
    "README.md.j2",
]


class GenerationError(Exception):
    """Raised when a bundle cannot be generated (bad input or IO error)."""


class InfraGenerator:
    def __init__(
        self,
        catalog: InfraCatalog,
        templates_dir: Path,
        blueprints_dir: Path,
    ) -> None:
        self._catalog = catalog
        self._templates_dir = Path(templates_dir)
        self._blueprints_dir = Path(blueprints_dir)
        self._blueprints_dir.mkdir(parents=True, exist_ok=True)

        self._env = Environment(
            loader=FileSystemLoader(str(self._templates_dir)),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
            autoescape=False,  # Terraform HCL, not HTML
        )

    # ------------------------------------------------------------------

    def generate(self, request: InfraGenerateRequest) -> InfraGenerateResponse:
        # 1. Normalize module list (always include base first, deduped)
        modules = self._normalize_modules(request.modules)

        # 2. Validate modules exist on disk for this provider
        for m in modules:
            if self._catalog.module_path(request.provider, m) is None:
                raise GenerationError(f"module '{m}' not available for provider '{request.provider}'")

        # 3. Allocate bundle directory
        bundle_id = str(uuid.uuid4())
        bundle_dir = self._blueprints_dir / bundle_id
        bundle_dir.mkdir()

        try:
            # 4. Copy module directories
            modules_dst = bundle_dir / "modules"
            modules_dst.mkdir()
            for m in modules:
                src = self._catalog.module_path(request.provider, m)
                shutil.copytree(src, modules_dst / m)

            # 5. Render templates
            ctx = self._template_context(bundle_id, request, modules)
            file_names: list[str] = []
            for template_name in REQUIRED_TEMPLATES:
                rendered = self._env.get_template(template_name).render(**ctx)
                output_name = template_name[:-3]  # strip ".j2"
                (bundle_dir / output_name).write_text(rendered, encoding="utf-8")
                file_names.append(output_name)

            # 6. Create tar.gz
            archive = self._blueprints_dir / f"{bundle_id}.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(bundle_dir, arcname=bundle_id)

        except Exception as e:
            # Clean up partial bundle on any failure
            shutil.rmtree(bundle_dir, ignore_errors=True)
            raise GenerationError(f"generation failed: {e}") from e

        logger.info("generated bundle id=%s provider=%s modules=%s",
                    bundle_id, request.provider, modules)

        return InfraGenerateResponse(
            bundle_id=bundle_id,
            provider=request.provider,
            modules=modules,
            download_url=f"/api/infra/bundles/{bundle_id}/download",
            files=file_names + [f"modules/{m}/" for m in modules],
        )

    # ------------------------------------------------------------------

    def list_bundles(self) -> list[InfraBundleSummary]:
        summaries: list[InfraBundleSummary] = []
        for child in sorted(self._blueprints_dir.iterdir()):
            if not child.is_dir():
                continue
            try:
                summary = self._read_bundle_summary(child)
            except Exception:
                logger.warning("could not read bundle summary at %s", child, exc_info=True)
                continue
            if summary is not None:
                summaries.append(summary)
        return summaries

    def archive_path(self, bundle_id: str) -> Optional[Path]:
        archive = self._blueprints_dir / f"{bundle_id}.tar.gz"
        return archive if archive.is_file() else None

    # ------------------------------------------------------------------

    def _normalize_modules(self, modules: list[str]) -> list[str]:
        # Always include base first, dedupe, preserve user order afterwards
        out = ["base"]
        for m in modules:
            if m != "base" and m not in out:
                out.append(m)
        return out

    def _template_context(
        self,
        bundle_id: str,
        request: InfraGenerateRequest,
        modules: list[str],
    ) -> dict:
        p = request.params
        return {
            "bundle_id": bundle_id,
            "provider": request.provider,
            "region": request.region,
            "modules": modules,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "project_name": p.project_name,
            "dc_ssh_cidr": p.dc_ssh_cidr,
            "jumpbox_size": p.jumpbox_size,
            "k8s_node_count": p.k8s_node_count,
            "edr_target_count": p.edr_target_count,
            "ttl_hours": p.ttl_hours,
            "tags": p.tags,
        }

    def _read_bundle_summary(self, bundle_dir: Path) -> Optional[InfraBundleSummary]:
        archive = self._blueprints_dir / f"{bundle_dir.name}.tar.gz"
        size = archive.stat().st_size if archive.is_file() else 0

        # Parse minimal info from main.tf header comment
        main_tf = bundle_dir / "main.tf"
        provider = "unknown"
        modules: list[str] = []
        if main_tf.is_file():
            for line in main_tf.read_text(encoding="utf-8").splitlines()[:8]:
                if line.startswith("# Provider"):
                    provider = line.split(":", 1)[1].strip()
                elif line.startswith("# Modules"):
                    modules = [m.strip() for m in line.split(":", 1)[1].split(",")]

        created_at = datetime.fromtimestamp(
            bundle_dir.stat().st_ctime, tz=timezone.utc
        ).isoformat(timespec="seconds")

        return InfraBundleSummary(
            bundle_id=bundle_dir.name,
            provider=provider,
            modules=modules,
            created_at=created_at,
            size_bytes=size,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/engine/test_infra_generator.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add core/engine/infra_generator.py tests/engine/test_infra_generator.py
git commit -m "feat(infra): generator engine with Jinja2 templates and tar bundle output"
```

---

### Task 10: Implement content_loader for installed.json → TOOL_REGISTRY

**Files:**
- Modify: `core/tools/registry.py`
- Create: `core/content_loader.py`
- Modify: `core/main.py`
- Test: `tests/test_content_loader.py`

- [ ] **Step 1: Refactor `core/tools/registry.py` to expose a function**

Read current file, then rewrite `core/tools/registry.py` as:

```python
"""
CortexSim Tool Registry — static + content-loaded merger.

`STATIC_TOOL_REGISTRY` holds the built-in tools defined in the Phase 1 spec.
`TOOL_REGISTRY` starts as a copy; `content_loader.merge_installed_tools()`
overlays entries from /opt/cortexsim/content/installed.json at startup.

Keep STATIC_TOOL_REGISTRY exactly as defined — existing tests and runtime
code rely on its schema (source_path, build_cmd, binary, run_template, type,
plane, description[, port, health_check]).
"""

STATIC_TOOL_REGISTRY: dict = {
    "signalbench": {
        "source_path": "sources/signalbench",
        "build_cmd": "cargo build --release",
        "binary": "sources/signalbench/target/release/signalbench",
        "run_template": "{binary} --technique {mitre_id} --count {count} --output json",
        "type": "binary",
        "plane": ["edr"],
        "description": "MITRE-mapped endpoint telemetry generator",
    },
    "mocktaxii": {
        "source_path": "sources/mocktaxii",
        "build_cmd": "pip install -r requirements.txt",
        "run_template": "python3 {source_path}/main.py --port {port}",
        "type": "service",
        "port": 9000,
        "plane": ["ndr"],
        "health_check": "http://localhost:9000/taxii/",
        "description": "STIX/TAXII 2.1 server for TIM scenarios",
    },
    "gocortexbrokenbank": {
        "source_path": "sources/gocortexbrokenbank",
        "build_cmd": "pip install -r requirements.txt",
        "run_template": "python3 {source_path}/app.py --port {port}",
        "type": "service",
        "port": 9001,
        "plane": ["cloud_app"],
        "health_check": "http://localhost:9001/health",
        "description": "Intentionally vulnerable app for CI/CD and ASPM scenarios",
    },
    "ackbarx": {
        "source_path": "sources/ackbarx",
        "build_cmd": "cargo build --release",
        "binary": "sources/ackbarx/target/release/ackbarx",
        "run_template": "{binary} --listen-port 162 --forward-url {xsiam_endpoint}",
        "type": "service",
        "plane": ["ndr"],
        "description": "SNMP trap forwarder to XSIAM HTTP endpoints",
    },
    "xdrtop": {
        "source_path": "sources/xdrtop",
        "build_cmd": "cargo build --release",
        "binary": "sources/xdrtop/target/release/xdrtop",
        "run_template": "{binary}",
        "type": "binary",
        "plane": ["all"],
        "description": "Terminal-based live XSIAM/XDR monitor",
    },
}

# Runtime registry — starts with statics, merged with installed content on startup
TOOL_REGISTRY: dict = dict(STATIC_TOOL_REGISTRY)


def reset_to_static() -> None:
    """Test helper — clear runtime additions and restore static-only state."""
    TOOL_REGISTRY.clear()
    TOOL_REGISTRY.update(STATIC_TOOL_REGISTRY)
```

- [ ] **Step 2: Write failing tests**

Create `tests/test_content_loader.py`:

```python
"""Tests for core.content_loader."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from content_loader import merge_installed_tools
from tools.registry import STATIC_TOOL_REGISTRY, TOOL_REGISTRY, reset_to_static


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_to_static()
    yield
    reset_to_static()


def _write_manifest(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"tools": entries}), encoding="utf-8")


class TestMergeInstalledTools:
    def test_no_manifest_is_no_op(self, tmp_path: Path):
        missing = tmp_path / "installed.json"
        count = merge_installed_tools(manifest_path=missing)
        assert count == 0
        # TOOL_REGISTRY still contains only static entries
        for name in STATIC_TOOL_REGISTRY:
            assert name in TOOL_REGISTRY
        assert len(TOOL_REGISTRY) == len(STATIC_TOOL_REGISTRY)

    def test_adds_new_entries(self, tmp_path: Path):
        manifest = tmp_path / "installed.json"
        _write_manifest(manifest, [
            {"name": "atomic-red-team",
             "install_path": "/opt/cortexsim/content/edr/atomic-red-team",
             "type": "content",
             "plane": ["edr"],
             "description": "Atomic TTP library"},
        ])
        count = merge_installed_tools(manifest_path=manifest)
        assert count == 1
        assert "atomic-red-team" in TOOL_REGISTRY
        entry = TOOL_REGISTRY["atomic-red-team"]
        assert entry["type"] == "content"
        assert entry["plane"] == ["edr"]

    def test_does_not_override_static_entries(self, tmp_path: Path):
        manifest = tmp_path / "installed.json"
        _write_manifest(manifest, [
            {"name": "signalbench",
             "install_path": "/opt/cortexsim/content/fake/signalbench",
             "type": "content",
             "plane": ["edr"],
             "description": "should not win"},
        ])
        count = merge_installed_tools(manifest_path=manifest)
        # Static entry wins — merger must never overwrite
        assert TOOL_REGISTRY["signalbench"]["type"] == "binary"
        assert "should not win" not in TOOL_REGISTRY["signalbench"]["description"]

    def test_malformed_manifest_is_logged_and_skipped(self, tmp_path: Path):
        manifest = tmp_path / "installed.json"
        manifest.write_text("{not valid json", encoding="utf-8")
        count = merge_installed_tools(manifest_path=manifest)
        assert count == 0
        # Static entries untouched
        assert len(TOOL_REGISTRY) == len(STATIC_TOOL_REGISTRY)

    def test_missing_required_fields_skipped(self, tmp_path: Path):
        manifest = tmp_path / "installed.json"
        _write_manifest(manifest, [
            {"install_path": "/x"},  # no name
            {"name": "ok",
             "install_path": "/y",
             "type": "content",
             "plane": ["cdr"],
             "description": "valid"},
        ])
        count = merge_installed_tools(manifest_path=manifest)
        assert count == 1
        assert "ok" in TOOL_REGISTRY
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_content_loader.py -v`
Expected: `ModuleNotFoundError: No module named 'content_loader'`

- [ ] **Step 4: Implement `core/content_loader.py`**

Create `core/content_loader.py`:

```python
"""
Content loader — merges jumpbox-installed content into TOOL_REGISTRY.

At startup, SimCore calls `merge_installed_tools()` which reads
/opt/cortexsim/content/installed.json (written by install-content.sh)
and overlays entries that don't collide with STATIC_TOOL_REGISTRY names.

Static entries always win — they're the authoritative definitions from the
Phase 1 spec (signalbench, mocktaxii, etc.).

Content entries use type="content" by convention to distinguish them in the UI.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from tools.registry import STATIC_TOOL_REGISTRY, TOOL_REGISTRY

logger = logging.getLogger("cortexsim.content_loader")

DEFAULT_MANIFEST_PATH = Path("/opt/cortexsim/content/installed.json")

REQUIRED_FIELDS = {"name", "install_path", "type", "plane", "description"}


def merge_installed_tools(manifest_path: Path = DEFAULT_MANIFEST_PATH) -> int:
    """
    Read the manifest and add valid entries to TOOL_REGISTRY (mutating it).

    Returns the number of entries added.
    Never raises — all errors are logged and treated as a no-op.
    """
    if not manifest_path.is_file():
        logger.info("no installed content manifest at %s — skipping merge", manifest_path)
        return 0

    try:
        raw = manifest_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("failed to read installed.json at %s: %s", manifest_path, e)
        return 0

    entries = data.get("tools", []) if isinstance(data, dict) else []
    if not isinstance(entries, list):
        logger.warning("installed.json 'tools' is not a list — skipping")
        return 0

    added = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        missing = REQUIRED_FIELDS - entry.keys()
        if missing:
            logger.debug("skipping content entry (missing fields %s): %s", missing, entry)
            continue

        name = entry["name"]
        if name in STATIC_TOOL_REGISTRY:
            logger.debug("skipping content entry %s — collides with static entry", name)
            continue

        TOOL_REGISTRY[name] = {
            "install_path": entry["install_path"],
            "type": entry["type"],
            "plane": entry["plane"],
            "description": entry["description"],
            "source": "installed-content",
        }
        # Optional passthroughs
        for k in ("repo", "category", "purpose", "image"):
            if k in entry:
                TOOL_REGISTRY[name][k] = entry[k]
        added += 1

    logger.info("content_loader merged %d entries from %s", added, manifest_path)
    return added
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_content_loader.py -v`
Expected: all tests pass.

- [ ] **Step 6: Wire the loader into `core/main.py` startup**

Find the lifespan handler in `core/main.py` (near the `init_db()` call). After the scenario loader call and before the tool instantiator init, add:

```python
    # 3a. Merge installed content into tool registry (no-op if not on a jumpbox)
    from content_loader import merge_installed_tools  # noqa: PLC0415
    try:
        merged = merge_installed_tools()
        logger.info("Content tools merged into registry: %d", merged)
    except Exception:
        logger.exception("content_loader merge failed — continuing without installed content")
```

- [ ] **Step 7: Verify existing tests still pass**

Run: `.venv/bin/pytest tests/ -v`
Expected: all new tests pass; no existing tests broken.

- [ ] **Step 8: Commit**

```bash
git add core/tools/registry.py core/content_loader.py core/main.py tests/test_content_loader.py
git commit -m "feat(infra): content_loader merges jumpbox-installed tools into TOOL_REGISTRY"
```

---

### Task 11: Write the FastAPI infra router

**Files:**
- Create: `core/api/infra.py`
- Modify: `core/main.py`
- Test: `tests/api/test_infra_api.py`

- [ ] **Step 1: Write failing tests**

Create `tests/api/test_infra_api.py`:

```python
"""Tests for /api/infra endpoints."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path: Path, repo_root: Path):
    # Redirect blueprint output to tmp dir so tests don't pollute the repo
    blueprints = tmp_path / "blueprints"
    blueprints.mkdir()

    from api import infra as infra_module
    monkeypatch.setattr(infra_module, "_BLUEPRINTS_DIR", blueprints)
    # Reinitialize the module-level generator with our tmp dir
    infra_module._reset_generator()

    # Build app with just the infra router for isolated testing
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(infra_module.router, prefix="/api")
    return TestClient(app)


class TestInfraAPI:
    def test_list_modules_aws(self, client: TestClient):
        resp = client.get("/api/infra/modules?provider=aws")
        assert resp.status_code == 200
        data = resp.json()
        names = [m["name"] for m in data["modules"]]
        assert "base" in names
        assert "edr" in names

    def test_list_modules_unknown_provider_empty(self, client: TestClient):
        resp = client.get("/api/infra/modules?provider=xyz")
        assert resp.status_code == 200
        assert resp.json()["modules"] == []

    def test_generate_happy_path(self, client: TestClient):
        resp = client.post("/api/infra/generate", json={
            "provider": "aws",
            "region": "us-east-1",
            "modules": ["edr"],
            "params": {
                "project_name": "smoke-test",
                "dc_ssh_cidr": "1.2.3.4/32",
            },
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["bundle_id"]
        assert "base" in body["modules"]
        assert "edr" in body["modules"]
        assert body["download_url"].startswith("/api/infra/bundles/")

    def test_generate_bad_provider(self, client: TestClient):
        resp = client.post("/api/infra/generate", json={
            "provider": "oracle",
            "region": "us-east-1",
            "modules": ["base"],
            "params": {"project_name": "x", "dc_ssh_cidr": "1.2.3.4/32"},
        })
        assert resp.status_code == 422  # pydantic validation

    def test_generate_bad_params(self, client: TestClient):
        resp = client.post("/api/infra/generate", json={
            "provider": "aws",
            "region": "us-east-1",
            "modules": ["base"],
            "params": {"project_name": "x", "dc_ssh_cidr": "not-a-cidr"},
        })
        assert resp.status_code == 422

    def test_download_bundle(self, client: TestClient):
        gen = client.post("/api/infra/generate", json={
            "provider": "aws",
            "region": "us-east-1",
            "modules": ["edr"],
            "params": {"project_name": "dl-test", "dc_ssh_cidr": "1.2.3.4/32"},
        }).json()
        bundle_id = gen["bundle_id"]

        dl = client.get(f"/api/infra/bundles/{bundle_id}/download")
        assert dl.status_code == 200
        assert dl.headers["content-type"].startswith("application/")
        assert "attachment" in dl.headers.get("content-disposition", "")
        assert len(dl.content) > 0

    def test_download_unknown_bundle_404(self, client: TestClient):
        resp = client.get("/api/infra/bundles/does-not-exist/download")
        assert resp.status_code == 404
        body = resp.json()
        assert body["detail"]["code"] == "BUNDLE_NOT_FOUND"

    def test_list_bundles(self, client: TestClient):
        client.post("/api/infra/generate", json={
            "provider": "aws",
            "region": "us-east-1",
            "modules": ["edr"],
            "params": {"project_name": "list-test", "dc_ssh_cidr": "1.2.3.4/32"},
        })
        resp = client.get("/api/infra/bundles")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert len(data["bundles"]) >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/api/test_infra_api.py -v`
Expected: `ModuleNotFoundError: No module named 'api.infra'`

- [ ] **Step 3: Implement the router**

Create `core/api/infra.py`:

```python
"""
CortexSim API — /api/infra router.

Endpoints:
  POST /api/infra/generate                     → render + bundle Terraform
  GET  /api/infra/modules[?provider=aws]       → list available modules
  GET  /api/infra/bundles                      → list previously generated bundles
  GET  /api/infra/bundles/{bundle_id}/download → download tar.gz
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from config import settings
from engine.infra_catalog import InfraCatalog
from engine.infra_generator import GenerationError, InfraGenerator
from engine.infra_models import (
    InfraGenerateRequest,
    InfraGenerateResponse,
    InfraModuleMetadata,
)

logger = logging.getLogger("cortexsim.api.infra")

router = APIRouter(prefix="/infra", tags=["infra"])

# -----------------------------------------------------------------------------
# Module-level paths and lazy generator
# -----------------------------------------------------------------------------

_MODULES_DIR: Path = Path(settings.CORTEXSIM_BASE_DIR) / "infra" / "modules"
_TEMPLATES_DIR: Path = Path(settings.CORTEXSIM_BASE_DIR) / "infra" / "templates"
_BLUEPRINTS_DIR: Path = Path(settings.CORTEXSIM_BASE_DIR) / "infra" / "blueprints"

_generator: Optional[InfraGenerator] = None


def _reset_generator() -> None:
    """Test helper — forces the next request to rebuild the generator with
    current module-level paths (useful when monkeypatching _BLUEPRINTS_DIR)."""
    global _generator
    _generator = None


def _get_generator() -> InfraGenerator:
    global _generator
    if _generator is None:
        catalog = InfraCatalog(modules_root=_MODULES_DIR)
        _generator = InfraGenerator(
            catalog=catalog,
            templates_dir=_TEMPLATES_DIR,
            blueprints_dir=_BLUEPRINTS_DIR,
        )
    return _generator


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------


@router.get("/modules")
def list_modules(provider: str = Query("aws")) -> dict:
    catalog = InfraCatalog(modules_root=_MODULES_DIR)
    modules = catalog.list_modules(provider=provider)
    return {"modules": [m.model_dump() for m in modules], "total": len(modules)}


@router.post("/generate", response_model=InfraGenerateResponse)
def generate_bundle(body: InfraGenerateRequest) -> InfraGenerateResponse:
    gen = _get_generator()
    try:
        return gen.generate(body)
    except GenerationError as e:
        logger.warning("generation failed: %s", e)
        raise HTTPException(
            status_code=422,
            detail={"error": str(e), "code": "GENERATION_FAILED", "detail": ""},
        )


@router.get("/bundles")
def list_bundles() -> dict:
    gen = _get_generator()
    summaries = gen.list_bundles()
    return {"bundles": [s.model_dump() for s in summaries], "total": len(summaries)}


@router.get("/bundles/{bundle_id}/download")
def download_bundle(bundle_id: str):
    gen = _get_generator()
    archive = gen.archive_path(bundle_id)
    if archive is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Bundle not found", "code": "BUNDLE_NOT_FOUND",
                    "detail": f"bundle_id='{bundle_id}'"},
        )
    return FileResponse(
        path=str(archive),
        media_type="application/gzip",
        filename=f"cortexsim-infra-{bundle_id}.tar.gz",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/api/test_infra_api.py -v`
Expected: all tests pass.

- [ ] **Step 5: Register the router in `core/main.py`**

In `core/main.py`, add the import and include the router in the pattern matching the other routers:

```python
from api.infra import router as infra_router            # noqa: E402
```

And:

```python
app.include_router(infra_router, prefix="/api")
```

- [ ] **Step 6: Quick end-to-end check — start SimCore and hit the endpoint**

Run: `cd /Users/henry/Github/Github_desktop/cortex-pov-engine && pkill -f "uvicorn main:app" 2>/dev/null; rm -f data/cortexsim.db; cd core && CORTEXSIM_ENV=development CORTEXSIM_BASE_DIR=/Users/henry/Github/Github_desktop/cortex-pov-engine /Users/henry/Github/Github_desktop/cortex-pov-engine/.venv/bin/uvicorn main:app --port 8888 &`

Then: `sleep 4 && curl -s "http://localhost:8888/api/infra/modules?provider=aws"`
Expected: JSON with `modules` array containing `base`, `edr`, `cdr`, `content-library`.

Stop the server: `pkill -f "uvicorn main:app"`

- [ ] **Step 7: Commit**

```bash
git add core/api/infra.py core/main.py tests/api/test_infra_api.py
git commit -m "feat(infra): /api/infra router — generate, modules, bundles, download"
```

---

### Task 12: Content installer script

**Files:**
- Create: `scripts/jumpbox/install-content.sh`

This is the script embedded via cloud-init in the base module's `userdata.sh.tftpl`. It reads each module's `content.yml`, installs tools per its declared strategy, and writes `installed.json` for `content_loader.py`.

- [ ] **Step 1: Create the installer**

Create `scripts/jumpbox/install-content.sh`:

```bash
#!/bin/bash
#
# CortexSim jumpbox content installer.
#
# Reads infra/modules/<provider>/<module>/content.yml for each enabled module
# and installs the declared tools into /opt/cortexsim/content/<module>/<tool>/.
# Produces /opt/cortexsim/content/installed.json for SimCore's content_loader.
#
# Install strategies supported:
#   git-clone       — shallow git clone
#   binary-release  — download latest GitHub release asset
#   pip-install     — pip install from a repo or package
#   docker-pull     — pull a container image (no local install path)
#
# Usage:
#   install-content.sh --modules=base,edr,cdr --repo-root=/home/ubuntu/cortexsim
#
set -euo pipefail

MODULES=""
REPO_ROOT="/home/ubuntu/cortexsim"
PROVIDER="aws"
DRY_RUN=0

while [ "${1:-}" != "" ]; do
  case "$1" in
    --modules=*)   MODULES="${1#*=}" ;;
    --repo-root=*) REPO_ROOT="${1#*=}" ;;
    --provider=*)  PROVIDER="${1#*=}" ;;
    --dry-run)     DRY_RUN=1 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done

if [ -z "$MODULES" ]; then
  echo "ERROR: --modules is required" >&2
  exit 2
fi

CONTENT_DIR="/opt/cortexsim/content"
MANIFEST="${CONTENT_DIR}/installed.json"
mkdir -p "$CONTENT_DIR"

log()  { echo "[install-content] $(date +%H:%M:%S) $*"; }
fail() { log "ERROR: $*"; exit 1; }

# Require yq for parsing content.yml
if ! command -v yq >/dev/null 2>&1; then
  log "installing yq"
  if [ "$DRY_RUN" -eq 0 ]; then
    curl -sSL -o /usr/local/bin/yq \
      https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64
    chmod +x /usr/local/bin/yq
  fi
fi

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

require_cmd git
require_cmd curl

# ------------------------------------------------------------------
# Per-strategy installers
# ------------------------------------------------------------------

install_git_clone() {
  local repo="$1" dst="$2"
  log "git-clone $repo -> $dst"
  if [ "$DRY_RUN" -eq 1 ]; then return 0; fi
  if [ -d "$dst/.git" ]; then
    (cd "$dst" && git fetch --depth=1 origin && git reset --hard FETCH_HEAD) || \
      log "git update failed for $dst (continuing)"
  else
    mkdir -p "$(dirname "$dst")"
    git clone --depth=1 "https://github.com/$repo.git" "$dst" || \
      log "git-clone failed for $repo (continuing)"
  fi
}

install_binary_release() {
  local repo="$1" dst="$2" name="$3"
  log "binary-release $repo -> $dst"
  if [ "$DRY_RUN" -eq 1 ]; then return 0; fi
  mkdir -p "$dst"
  # Best-effort: fetch latest release metadata and grab a linux x86_64 asset
  local meta
  meta="$(curl -sSL "https://api.github.com/repos/$repo/releases/latest" || true)"
  local url
  url="$(echo "$meta" | grep -Eo 'https://[^"]*linux[^"]*(amd64|x86_64)[^"]*\.(tar\.gz|zip|tgz)' | head -1 || true)"
  if [ -z "$url" ]; then
    log "could not find linux asset for $repo — skipping"
    return 0
  fi
  local tmp; tmp="$(mktemp)"
  curl -sSL -o "$tmp" "$url" || { log "download failed"; return 0; }
  case "$url" in
    *.tar.gz|*.tgz) tar -xzf "$tmp" -C "$dst" ;;
    *.zip) unzip -q "$tmp" -d "$dst" ;;
  esac
  rm -f "$tmp"
}

install_pip() {
  local repo="$1"
  log "pip-install $repo"
  if [ "$DRY_RUN" -eq 1 ]; then return 0; fi
  pip3 install --quiet "git+https://github.com/$repo.git" || log "pip install failed for $repo"
}

install_docker_pull() {
  local image="$1"
  log "docker-pull $image"
  if [ "$DRY_RUN" -eq 1 ]; then return 0; fi
  if command -v docker >/dev/null 2>&1; then
    docker pull "$image" || log "docker pull failed for $image"
  else
    log "docker not present; skipping pull of $image"
  fi
}

# ------------------------------------------------------------------
# Manifest assembly
# ------------------------------------------------------------------

MANIFEST_ENTRIES=()

append_manifest() {
  local name="$1" install_path="$2" plane="$3" repo="$4" category="$5"
  local desc="installed via install-content.sh from $repo"
  local entry
  entry="$(jq -cn \
    --arg name "$name" \
    --arg ip "$install_path" \
    --arg plane "$plane" \
    --arg repo "$repo" \
    --arg cat "$category" \
    --arg desc "$desc" \
    '{name:$name, install_path:$ip, type:"content", plane:[$plane], description:$desc, repo:$repo, category:$cat}')"
  MANIFEST_ENTRIES+=("$entry")
}

# ------------------------------------------------------------------
# Process each selected module
# ------------------------------------------------------------------

IFS=',' read -ra MODULE_LIST <<< "$MODULES"

for module in "${MODULE_LIST[@]}"; do
  module="$(echo "$module" | tr -d ' ')"
  [ -z "$module" ] && continue

  manifest_path="${REPO_ROOT}/infra/modules/${PROVIDER}/${module}/content.yml"
  if [ ! -f "$manifest_path" ]; then
    log "no content.yml for module=$module (skipping)"
    continue
  fi

  log "processing module: $module"
  categories="$(yq eval '.tools | keys | .[]' "$manifest_path" 2>/dev/null || true)"

  while IFS= read -r category; do
    [ -z "$category" ] && continue
    count="$(yq eval ".tools.${category} | length" "$manifest_path")"
    for i in $(seq 0 $((count - 1))); do
      name="$(yq eval ".tools.${category}[$i].name" "$manifest_path")"
      repo="$(yq eval ".tools.${category}[$i].repo" "$manifest_path")"
      install="$(yq eval ".tools.${category}[$i].install" "$manifest_path")"
      install_path="$(yq eval ".tools.${category}[$i].install_path // \"\"" "$manifest_path")"
      image="$(yq eval ".tools.${category}[$i].image // \"\"" "$manifest_path")"

      if [ -z "$install_path" ] && [ "$install" != "docker-pull" ] && [ "$install" != "pip-install" ]; then
        install_path="${CONTENT_DIR}/${module}/${name}"
      fi

      case "$install" in
        git-clone)      install_git_clone "$repo" "$install_path" ;;
        binary-release) install_binary_release "$repo" "$install_path" "$name" ;;
        pip-install)    install_pip "$repo" ;;
        docker-pull)    install_docker_pull "$image" ;;
        *) log "unknown install strategy for $name: $install — skipping" ; continue ;;
      esac

      append_manifest "$name" "${install_path:-(docker)}" "$module" "$repo" "$category"
    done
  done <<< "$categories"
done

# Write manifest
if [ "$DRY_RUN" -eq 1 ]; then
  log "(dry-run) would write manifest with ${#MANIFEST_ENTRIES[@]} entries to $MANIFEST"
else
  entries_json="[]"
  if [ "${#MANIFEST_ENTRIES[@]}" -gt 0 ]; then
    entries_json="$(printf '%s\n' "${MANIFEST_ENTRIES[@]}" | jq -s '.')"
  fi
  jq -n --argjson tools "$entries_json" '{tools:$tools}' > "$MANIFEST"
  log "wrote manifest: $MANIFEST (${#MANIFEST_ENTRIES[@]} entries)"
fi

log "install-content.sh complete"
```

- [ ] **Step 2: Make it executable and bash-syntax check**

```bash
chmod +x /Users/henry/Github/Github_desktop/cortex-pov-engine/scripts/jumpbox/install-content.sh
bash -n /Users/henry/Github/Github_desktop/cortex-pov-engine/scripts/jumpbox/install-content.sh
```
Expected: no output (valid syntax).

- [ ] **Step 3: Dry-run against the repo's own modules**

```bash
cd /Users/henry/Github/Github_desktop/cortex-pov-engine
sudo mkdir -p /opt/cortexsim/content
sudo chown $(whoami) /opt/cortexsim/content
./scripts/jumpbox/install-content.sh \
  --modules=base,edr \
  --repo-root=$(pwd) \
  --provider=aws \
  --dry-run
```

Expected: log lines showing what would be installed; no actual git clones.

- [ ] **Step 4: Commit**

```bash
git add scripts/jumpbox/install-content.sh
git commit -m "feat(infra): jumpbox content installer with per-strategy handlers"
```

---

### Task 13: Extend scenario schema with optional infra hints

**Files:**
- Modify: `scenarios/_schema.yml`
- Modify: `core/engine/scenario_loader.py`

- [ ] **Step 1: Read current scenario loader to understand schema**

Run: `grep -n "required_content\|infra_modules_needed\|class.*Schema\|class.*Scenario" /Users/henry/Github/Github_desktop/cortex-pov-engine/core/engine/scenario_loader.py | head -20`
Note the Pydantic class name used for schema validation (e.g., `ScenarioSchema`).

- [ ] **Step 2: Add optional fields to the Pydantic schema**

Open `core/engine/scenario_loader.py` and find the top-level scenario Pydantic class. Add two optional fields (place them near other top-level optional fields like `tags`):

```python
    # Infra generator hints (optional, backward compatible)
    required_content: list[dict] = Field(default_factory=list,
        description="Open-source tool repos this scenario needs installed")
    infra_modules_needed: list[str] = Field(default_factory=list,
        description="IaC generator module names for auto-suggest (e.g. ['base', 'edr'])")
```

If `Field` is not already imported in that file, add `from pydantic import BaseModel, Field, ...` alongside existing imports.

- [ ] **Step 3: Document the fields in `scenarios/_schema.yml`**

Append to `scenarios/_schema.yml` (or insert in a logical spot near other metadata):

```yaml
# ── IaC Generator Hints (optional) ──────────────────────────────────────────

required_content:
  - repo: "huntergregal/mimipenguin"
  - repo: "bishopfox/sliver"
# (optional) Open-source tools the scenario expects to find installed.
# Consumed by the IaC generator UI to auto-suggest content modules.

infra_modules_needed:
  - edr
  - base
# (optional) Hints which IaC generator modules should be selected when this
# scenario is in the DC's bookmark set. Values: base, edr, cdr, ndr, itdr,
# tim, asm, cspm, content-library, telemetry-replay.
```

- [ ] **Step 4: Verify existing scenarios still load**

Run: `cd /Users/henry/Github/Github_desktop/cortex-pov-engine && pkill -f "uvicorn main:app" 2>/dev/null; rm -f data/cortexsim.db; cd core && CORTEXSIM_ENV=development CORTEXSIM_BASE_DIR=/Users/henry/Github/Github_desktop/cortex-pov-engine /Users/henry/Github/Github_desktop/cortex-pov-engine/.venv/bin/uvicorn main:app --port 8888 &`

Then: `sleep 4 && curl -s http://localhost:8888/api/scenarios | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'loaded {len(d[\"scenarios\"])} scenarios')"`
Expected: `loaded 10 scenarios` (5 CDR + 5 EDR).

Stop: `pkill -f "uvicorn main:app"`

- [ ] **Step 5: Commit**

```bash
git add scenarios/_schema.yml core/engine/scenario_loader.py
git commit -m "feat(infra): optional scenario schema fields for IaC hints (backward compatible)"
```

---

### Task 14: Add UI API client functions

**Files:**
- Modify: `ui/src/api/client.js`

- [ ] **Step 1: Add infra client functions**

Open `ui/src/api/client.js` and append at the end of the file, before any final `export` blocks:

```javascript
// ─── Infra (IaC Topology Generator) ──────────────────────────────────────────

/**
 * GET /api/infra/modules?provider=aws
 * @param {string} provider
 * @returns {Promise<{modules: Array, total: number}>}
 */
export async function getInfraModules(provider = 'aws') {
  return request(`/api/infra/modules?provider=${encodeURIComponent(provider)}`)
}

/**
 * POST /api/infra/generate
 * @param {Object} body  { provider, region, modules, params }
 * @returns {Promise<Object>}
 */
export async function generateInfra(body) {
  return request('/api/infra/generate', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

/**
 * GET /api/infra/bundles
 * @returns {Promise<{bundles: Array, total: number}>}
 */
export async function getInfraBundles() {
  return request('/api/infra/bundles')
}

/**
 * GET /api/infra/bundles/:bundle_id/download
 * Returns the tar.gz as a Blob for download.
 * @param {string} bundleId
 * @returns {Promise<Blob>}
 */
export async function downloadInfraBundle(bundleId) {
  const response = await request(`/api/infra/bundles/${bundleId}/download`, {
    _returnBlob: true,
  })
  return response.blob()
}
```

- [ ] **Step 2: Verify UI still builds**

Run: `cd /Users/henry/Github/Github_desktop/cortex-pov-engine/ui && npm run build 2>&1 | tail -5`
Expected: `✓ built in ...ms` — no errors.

- [ ] **Step 3: Commit**

```bash
git add ui/src/api/client.js
git commit -m "feat(infra-ui): API client functions for /api/infra endpoints"
```

---

### Task 15: Build the InfraGenerator React component

**Files:**
- Create: `ui/src/components/InfraGenerator.jsx`

- [ ] **Step 1: Create the component**

Create `ui/src/components/InfraGenerator.jsx`:

```jsx
import React, { useState, useEffect, useCallback } from 'react'
import {
  getInfraModules,
  generateInfra,
  getInfraBundles,
  downloadInfraBundle,
} from '../api/client.js'

const DEFAULT_PARAMS = {
  project_name: '',
  dc_ssh_cidr: '',
  jumpbox_size: 't3.medium',
  k8s_node_count: 2,
  edr_target_count: 2,
  ttl_hours: 72,
}

const DEFAULT_REGION = 'us-east-1'

function ModuleCard({ module, checked, onToggle }) {
  const isBase = module.name === 'base'
  return (
    <label style={{
      display: 'flex',
      gap: '10px',
      padding: '12px',
      border: checked ? '2px solid var(--cortex-teal)' : '1px solid var(--cortex-border)',
      borderRadius: '6px',
      cursor: isBase ? 'not-allowed' : 'pointer',
      background: checked ? 'rgba(0,192,232,0.08)' : 'white',
      opacity: isBase ? 0.85 : 1,
      transition: 'border-color 0.12s',
    }}>
      <input
        type="checkbox"
        checked={checked}
        disabled={isBase}
        onChange={onToggle}
        style={{ marginTop: '4px' }}
      />
      <div style={{ flex: 1 }}>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '3px' }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, color: 'var(--cortex-navy)' }}>
            {module.name}
          </span>
          {isBase && <span className="badge badge-steel" style={{ fontSize: '10px' }}>required</span>}
          {module.dependencies?.length > 0 && (
            <span style={{ fontSize: '11px', color: 'var(--cortex-steel)' }}>
              requires: {module.dependencies.join(', ')}
            </span>
          )}
        </div>
        <div style={{ fontSize: '12px', color: 'var(--cortex-navy)', marginBottom: '4px' }}>
          {module.description}
        </div>
        {module.content_tools?.length > 0 && (
          <div style={{ fontSize: '11px', color: 'var(--cortex-steel)' }}>
            Content: {module.content_tools.slice(0, 5).join(', ')}
            {module.content_tools.length > 5 && ` +${module.content_tools.length - 5} more`}
          </div>
        )}
      </div>
    </label>
  )
}

function BundleRow({ bundle, onDownload }) {
  return (
    <div style={{
      display: 'flex', gap: '12px', alignItems: 'center',
      padding: '8px 0', borderBottom: '1px solid var(--cortex-border)',
    }}>
      <div style={{ flex: 1 }}>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--cortex-teal)' }}>
          {bundle.bundle_id}
        </div>
        <div style={{ fontSize: '11px', color: 'var(--cortex-steel)' }}>
          {bundle.provider} · {bundle.modules.join(', ')} · {bundle.created_at?.slice(0, 16) || 'n/a'}
          {' · '}{Math.round((bundle.size_bytes || 0) / 1024)} KB
        </div>
      </div>
      <button className="btn btn-sm btn-secondary" onClick={() => onDownload(bundle.bundle_id)}>
        &#8681; Download
      </button>
    </div>
  )
}

export default function InfraGenerator() {
  const [provider, setProvider] = useState('aws')
  const [region, setRegion] = useState(DEFAULT_REGION)
  const [modules, setModules] = useState([])
  const [selected, setSelected] = useState(new Set(['base']))
  const [params, setParams] = useState(DEFAULT_PARAMS)
  const [bundles, setBundles] = useState([])
  const [loading, setLoading] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [lastBundle, setLastBundle] = useState(null)
  const [error, setError] = useState(null)

  const refreshModules = useCallback(() => {
    setLoading(true)
    getInfraModules(provider)
      .then(d => setModules(d.modules || []))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [provider])

  const refreshBundles = useCallback(() => {
    getInfraBundles().then(d => setBundles(d.bundles || [])).catch(() => {})
  }, [])

  useEffect(() => { refreshModules() }, [refreshModules])
  useEffect(() => { refreshBundles() }, [refreshBundles])

  const toggleModule = (name) => {
    if (name === 'base') return
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name); else next.add(name)
      return next
    })
  }

  const updateParam = (key, value) => setParams(p => ({ ...p, [key]: value }))

  const handleGenerate = async () => {
    setError(null)
    setGenerating(true)
    try {
      const body = {
        provider,
        region,
        modules: Array.from(selected),
        params: {
          ...params,
          k8s_node_count: Number(params.k8s_node_count),
          edr_target_count: Number(params.edr_target_count),
          ttl_hours: Number(params.ttl_hours),
        },
      }
      const resp = await generateInfra(body)
      setLastBundle(resp)
      refreshBundles()
    } catch (e) {
      setError(e.message)
    } finally {
      setGenerating(false)
    }
  }

  const handleDownload = async (bundleId) => {
    try {
      const blob = await downloadInfraBundle(bundleId)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `cortexsim-infra-${bundleId}.tar.gz`
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      setError(e.message)
    }
  }

  const canGenerate = !generating && params.project_name.trim() && params.dc_ssh_cidr.trim()

  return (
    <div className="panel-card">
      <div className="panel-card-header">
        <h3>Deploy POV Infrastructure (IaC Generator)</h3>
        <button className="btn btn-secondary btn-sm" onClick={refreshModules} disabled={loading}>
          {loading ? <span className="spinner" /> : '⟳ Refresh'}
        </button>
      </div>

      <div className="panel-card-body">
        {error && (
          <div style={{ padding: '10px', background: '#FEF0F0', border: '1px solid var(--cortex-danger)',
                       borderRadius: '4px', color: 'var(--cortex-danger)', fontSize: '12px', marginBottom: '12px' }}>
            {error}
          </div>
        )}

        {/* Provider + Region */}
        <div style={{ display: 'flex', gap: '16px', marginBottom: '16px' }}>
          <div style={{ flex: 1 }}>
            <label style={{ display: 'block', fontSize: '11px', fontWeight: 600, marginBottom: '4px',
                           textTransform: 'uppercase', letterSpacing: '0.4px', color: 'var(--cortex-steel)' }}>
              Cloud Provider
            </label>
            <div style={{ display: 'flex', gap: '6px' }}>
              {['aws', 'gcp', 'azure'].map(p => (
                <button
                  key={p}
                  className={`btn btn-sm ${provider === p ? '' : 'btn-secondary'}`}
                  onClick={() => setProvider(p)}
                  disabled={p !== 'aws'}
                  title={p === 'aws' ? '' : 'Coming in a future phase'}
                  style={{ textTransform: 'uppercase' }}
                >
                  {p}
                </button>
              ))}
            </div>
          </div>
          <div style={{ flex: 1 }}>
            <label style={{ display: 'block', fontSize: '11px', fontWeight: 600, marginBottom: '4px',
                           textTransform: 'uppercase', letterSpacing: '0.4px', color: 'var(--cortex-steel)' }}>
              Region
            </label>
            <input type="text" value={region} onChange={e => setRegion(e.target.value)}
              style={{ width: '100%', padding: '6px 8px', border: '1px solid var(--cortex-border)',
                      borderRadius: '4px', fontFamily: 'var(--font-mono)', fontSize: '12px' }} />
          </div>
        </div>

        {/* Modules */}
        <div style={{ marginBottom: '16px' }}>
          <p className="section-label">Modules</p>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))',
                       gap: '8px' }}>
            {modules.map(m => (
              <ModuleCard
                key={m.name}
                module={m}
                checked={selected.has(m.name)}
                onToggle={() => toggleModule(m.name)}
              />
            ))}
          </div>
        </div>

        {/* Params */}
        <div style={{ marginBottom: '16px' }}>
          <p className="section-label">Parameters</p>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '10px' }}>
            <LabeledInput label="Project name (lowercase, hyphens)" value={params.project_name}
              onChange={v => updateParam('project_name', v)} placeholder="acme-pov-2026" required />
            <LabeledInput label="Your IP for SSH (CIDR)" value={params.dc_ssh_cidr}
              onChange={v => updateParam('dc_ssh_cidr', v)} placeholder="203.0.113.0/32" required />
            <LabeledInput label="Jumpbox instance type" value={params.jumpbox_size}
              onChange={v => updateParam('jumpbox_size', v)} />
            <LabeledInput label="K8s node count (CDR)" value={params.k8s_node_count}
              type="number" onChange={v => updateParam('k8s_node_count', v)} />
            <LabeledInput label="EDR target count" value={params.edr_target_count}
              type="number" onChange={v => updateParam('edr_target_count', v)} />
            <LabeledInput label="TTL hours (Torque hint)" value={params.ttl_hours}
              type="number" onChange={v => updateParam('ttl_hours', v)} />
          </div>
        </div>

        {/* Generate */}
        <div style={{ display: 'flex', gap: '10px', alignItems: 'center', marginBottom: '16px' }}>
          <button className="btn" onClick={handleGenerate} disabled={!canGenerate}>
            {generating ? <span className="spinner" /> : '⚙ Generate Bundle'}
          </button>
          {lastBundle && (
            <span style={{ fontSize: '12px', color: 'var(--cortex-success)', fontFamily: 'var(--font-mono)' }}>
              ✓ Generated {lastBundle.bundle_id.slice(0, 8)}…
              <button className="btn btn-sm btn-secondary" style={{ marginLeft: '8px' }}
                onClick={() => handleDownload(lastBundle.bundle_id)}>
                Download now
              </button>
            </span>
          )}
        </div>

        {/* Bundle history */}
        {bundles.length > 0 && (
          <div>
            <p className="section-label">Recent Bundles</p>
            {bundles.map(b => <BundleRow key={b.bundle_id} bundle={b} onDownload={handleDownload} />)}
          </div>
        )}
      </div>
    </div>
  )
}

function LabeledInput({ label, value, onChange, placeholder, type = 'text', required = false }) {
  return (
    <div>
      <label style={{
        display: 'block', fontSize: '11px', fontWeight: 600, marginBottom: '3px',
        textTransform: 'uppercase', letterSpacing: '0.4px', color: 'var(--cortex-steel)',
      }}>
        {label}{required && <span style={{ color: 'var(--cortex-danger)' }}> *</span>}
      </label>
      <input
        type={type}
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        style={{
          width: '100%', padding: '6px 8px',
          border: '1px solid var(--cortex-border)', borderRadius: '4px',
          fontSize: '12px', fontFamily: type === 'number' ? 'var(--font-mono)' : 'inherit',
        }}
      />
    </div>
  )
}
```

- [ ] **Step 2: Verify UI still builds**

Run: `cd /Users/henry/Github/Github_desktop/cortex-pov-engine/ui && npm run build 2>&1 | tail -5`
Expected: `✓ built in ...ms` — no errors.

- [ ] **Step 3: Commit**

```bash
git add ui/src/components/InfraGenerator.jsx
git commit -m "feat(infra-ui): InfraGenerator panel with modules, params, bundle history"
```

---

### Task 16: Wire InfraGenerator into App layout

**Files:**
- Modify: `ui/src/App.jsx`

- [ ] **Step 1: Add import, state, toggle button, and view routing**

In `ui/src/App.jsx`:

**a)** Add the import at the top (with the other component imports):

```jsx
import InfraGenerator from './components/InfraGenerator.jsx'
```

**b)** Extend the `AppHeader` signature (add `onToggleDeploy` and `showDeploy` to the props):

```jsx
function AppHeader({ hostname, version, onToggleResults, showResults,
                   onToggleMitre, showMitre, onToggleDeploy, showDeploy }) {
```

**c)** Add a Deploy button in the header's view-toggle group, right after the MITRE button:

```jsx
      <button
        className={`btn btn-sm ${showDeploy ? 'btn-navy' : 'btn-secondary'}`}
        onClick={onToggleDeploy}
        style={{
          border: '1px solid rgba(255,255,255,0.15)',
          background: showDeploy ? 'rgba(0,192,232,0.2)' : 'rgba(255,255,255,0.08)',
          color: 'var(--cortex-white)',
        }}
      >
        &#x2630; Deploy
      </button>
```

**d)** Add `showDeploy` state in `App()` (near the other `useState` calls for `showResults`, `showMitre`):

```jsx
  const [showDeploy, setShowDeploy] = useState(false)
```

**e)** Update the `AppHeader` invocation with the new props. Change any handlers that set `showResults` or `showMitre` to also set `showDeploy` to false (mutually exclusive views). Find the existing pattern:

```jsx
      <AppHeader
        hostname={hostname}
        version={version}
        onToggleResults={() => { setShowResults(v => !v); setShowMitre(false) }}
        showResults={showResults}
        onToggleMitre={() => { setShowMitre(v => !v); setShowResults(false) }}
        showMitre={showMitre}
      />
```

Replace with:

```jsx
      <AppHeader
        hostname={hostname}
        version={version}
        onToggleResults={() => { setShowResults(v => !v); setShowMitre(false); setShowDeploy(false) }}
        showResults={showResults}
        onToggleMitre={() => { setShowMitre(v => !v); setShowResults(false); setShowDeploy(false) }}
        showMitre={showMitre}
        onToggleDeploy={() => { setShowDeploy(v => !v); setShowResults(false); setShowMitre(false) }}
        showDeploy={showDeploy}
      />
```

**f)** Extend the main panel routing. Find:

```jsx
        {showMitre ? (
          <MitreHeatmap />
        ) : showResults ? (
          <ResultsViewer runs={runs} onClose={() => setShowResults(false)} />
        ) : (
```

Change to:

```jsx
        {showDeploy ? (
          <InfraGenerator />
        ) : showMitre ? (
          <MitreHeatmap />
        ) : showResults ? (
          <ResultsViewer runs={runs} onClose={() => setShowResults(false)} />
        ) : (
```

- [ ] **Step 2: Verify UI builds**

Run: `cd /Users/henry/Github/Github_desktop/cortex-pov-engine/ui && npm run build 2>&1 | tail -5`
Expected: `✓ built in ...ms` with no errors.

- [ ] **Step 3: Copy to core/static**

Run: `rm -rf /Users/henry/Github/Github_desktop/cortex-pov-engine/core/static/assets /Users/henry/Github/Github_desktop/cortex-pov-engine/core/static/index.html && cp -r /Users/henry/Github/Github_desktop/cortex-pov-engine/ui/dist/* /Users/henry/Github/Github_desktop/cortex-pov-engine/core/static/`

- [ ] **Step 4: Commit**

```bash
git add ui/src/App.jsx core/static/
git commit -m "feat(infra-ui): wire InfraGenerator into App header + main panel"
```

---

### Task 17: End-to-end verification

**Files:** (no new files)

- [ ] **Step 1: Start SimCore fresh**

```bash
cd /Users/henry/Github/Github_desktop/cortex-pov-engine
pkill -f "uvicorn main:app" 2>/dev/null || true
rm -f data/cortexsim.db
cd core && CORTEXSIM_ENV=development CORTEXSIM_BASE_DIR=/Users/henry/Github/Github_desktop/cortex-pov-engine /Users/henry/Github/Github_desktop/cortex-pov-engine/.venv/bin/uvicorn main:app --port 8888 &
sleep 4
```

- [ ] **Step 2: List AWS modules via API**

```bash
curl -s "http://localhost:8888/api/infra/modules?provider=aws" | python3 -m json.tool
```

Expected: JSON with 4 modules: `base`, `cdr`, `content-library`, `edr`. Each has `description`, `providers`, `dependencies`, and `content_tools` populated.

- [ ] **Step 3: Generate a bundle**

```bash
curl -s -X POST http://localhost:8888/api/infra/generate \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "aws",
    "region": "us-east-1",
    "modules": ["edr", "content-library"],
    "params": {
      "project_name": "smoke-test",
      "dc_ssh_cidr": "203.0.113.0/32"
    }
  }' | python3 -m json.tool
```

Expected: response with `bundle_id`, `modules` containing `base`, `edr`, `content-library`, and a `download_url`.

- [ ] **Step 4: Verify bundle contents**

```bash
BUNDLE_ID=$(curl -s http://localhost:8888/api/infra/bundles | python3 -c "import sys,json; print(json.load(sys.stdin)['bundles'][0]['bundle_id'])")
curl -s "http://localhost:8888/api/infra/bundles/$BUNDLE_ID/download" -o /tmp/cortexsim-bundle.tar.gz
tar tzf /tmp/cortexsim-bundle.tar.gz | head -20
```

Expected: listing showing `<bundle_id>/main.tf`, `<bundle_id>/variables.tf`, `<bundle_id>/outputs.tf`, `<bundle_id>/terraform.tfvars`, `<bundle_id>/README.md`, `<bundle_id>/modules/base/main.tf`, `<bundle_id>/modules/edr/main.tf`, `<bundle_id>/modules/content-library/README.md`.

- [ ] **Step 5: Inspect the generated main.tf**

```bash
tar xzf /tmp/cortexsim-bundle.tar.gz -C /tmp
cat /tmp/$BUNDLE_ID/main.tf
```

Expected: HCL containing `module "base"`, `module "edr"` (because edr was selected), and no `module "cdr"`. Project name is `smoke-test`, region is `us-east-1`.

- [ ] **Step 6: Optional — Terraform validate the bundle**

If `terraform` is installed:

```bash
cd /tmp/$BUNDLE_ID && terraform init -backend=false && terraform validate
```

Expected: `Success! The configuration is valid.`

If terraform isn't installed locally, note it in the task checklist and rely on the AWS-deploy smoke test in a follow-up task.

- [ ] **Step 7: Run the full pytest suite**

```bash
cd /Users/henry/Github/Github_desktop/cortex-pov-engine && .venv/bin/pytest tests/ -v
```

Expected: all tests pass across `tests/engine/`, `tests/api/`, `tests/test_content_loader.py`.

- [ ] **Step 8: Stop SimCore**

```bash
pkill -f "uvicorn main:app" 2>/dev/null
```

- [ ] **Step 9: Commit (if anything was tweaked during verification)**

```bash
git status
# If tweaks: git add ... && git commit -m "fix: end-to-end verification adjustments"
```

---

### Task 18: Update CLAUDE.md with the new feature

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add IaC section to CLAUDE.md**

Find the end of the file (after the "Spec Reference" section) and append a new section:

```markdown

## IaC Topology Generator

The IaC generator produces Terraform bundles Torque can consume as blueprints. Phase A supports AWS with `base`, `edr`, `cdr`, and `content-library` modules.

### Key paths

- `infra/modules/{provider}/{module}/` — Terraform modules (+ `content.yml`, `README.md` with YAML frontmatter)
- `infra/templates/*.j2` — Jinja2 root-bundle templates rendered by the generator
- `infra/blueprints/` — generated bundles (gitignored)
- `core/engine/infra_generator.py` — core generation logic
- `core/engine/infra_catalog.py` — module metadata loader
- `core/engine/infra_models.py` — Pydantic request/response models
- `core/api/infra.py` — `/api/infra/*` endpoints
- `core/content_loader.py` — merges `/opt/cortexsim/content/installed.json` into TOOL_REGISTRY
- `scripts/jumpbox/install-content.sh` — runs on provisioned jumpbox via cloud-init

### API endpoints

- `POST /api/infra/generate` — generate a bundle, returns `bundle_id` and `download_url`
- `GET  /api/infra/modules[?provider=aws]` — list available modules
- `GET  /api/infra/bundles` — list previously generated bundles
- `GET  /api/infra/bundles/{bundle_id}/download` — download tar.gz

### Design rules (IaC-specific)

- **Base module always included** in any bundle (enforced in `InfraGenerator._normalize_modules`).
- **Static TOOL_REGISTRY always wins** over installed-content entries — `content_loader` never overwrites.
- **Module metadata lives in `README.md` frontmatter**, not in Python — adding a module is filesystem-only.
- **Bundles are stateless artifacts** — no DB schema. File-system is source of truth.

### Spec

Full design: `docs/superpowers/specs/2026-04-20-iac-topology-generator-design.md`.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document IaC topology generator in CLAUDE.md"
```

---

## Self-Review

I reviewed the plan against the spec:

**Spec coverage check:**

- Section 1 (Module library): covered by Tasks 4–7 (base/edr/cdr/content-library) + structure defined in Task 1.
- Section 2 (Content manifests): covered by Tasks 4–7 (content.yml per module) + Task 12 (installer reads them).
- Section 3 (Content installer): Task 12 implements `install-content.sh`; Task 10 implements `content_loader.py` for the SimCore side.
- Section 4 (Scenario schema hints): Task 13.
- Section 5 (Generator engine): Tasks 2, 3, 9.
- Section 6 (API surface): Task 11 (`POST /generate`, `GET /modules`, `GET /bundles`, `GET /bundles/:id/download`).
- Section 7 (UI panel): Tasks 14–16.
- Error handling (API contract): Task 11 (`BUNDLE_NOT_FOUND`, `GENERATION_FAILED`; `PROVIDER_UNSUPPORTED`/`INVALID_PARAMS` via Pydantic 422s).
- Testing strategy: unit tests in Tasks 2, 3, 9, 10, 11; dry-run installer in Task 12; end-to-end smoke in Task 17.
- Phase A scope (AWS + base/edr/cdr/content-library): covered end-to-end.

Phases B/C/D modules (GCP, Azure, ndr/itdr/tim/asm/cspm/telemetry-replay) are explicitly out-of-scope for this plan and called out in the header.

**Placeholder scan:** none. Every step has actual file content, exact commands, and expected output.

**Type consistency check:**
- `InfraGenerateRequest`, `InfraGenerateResponse`, `InfraModuleMetadata`, `InfraBundleSummary`, `InfraGenerateParams` — consistent across Tasks 2, 3, 9, 11.
- `InfraCatalog.list_modules(provider)`, `.get_module(provider, module)`, `.module_path(provider, module)`, `.load_content_manifest(provider, module)` — consistent across Tasks 3 and 9.
- `InfraGenerator.generate(request)`, `.list_bundles()`, `.archive_path(bundle_id)` — consistent across Tasks 9 and 11.
- `merge_installed_tools(manifest_path=...)` — consistent across Task 10 (implementation) and the main.py wiring.
- `_reset_generator()` test helper — defined in Task 11 router, referenced from Task 11 tests.

No inconsistencies found.
