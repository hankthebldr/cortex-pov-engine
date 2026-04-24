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
                provider="oracle",
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
