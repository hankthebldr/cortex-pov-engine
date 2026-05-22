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


class TestInfraAPIAdapterAutoPull:
    """API surface for the adapter_refs[] auto-pull plumbing."""

    @pytest.fixture(autouse=True)
    def _load_catalog(self, repo_root: Path):
        from tools.adapter_catalog import catalog  # noqa: PLC0415
        catalog.load(str(repo_root / "tools" / "packs"))

    def test_generate_with_adapter_refs_pulls_modules(self, client: TestClient):
        resp = client.post("/api/infra/generate", json={
            "provider": "aws",
            "region": "us-east-1",
            "modules": ["base"],
            "adapter_refs": ["TOOL-RUBEUS", "TOOL-MIMIKATZ"],
            "params": {"project_name": "auto-pull", "dc_ssh_cidr": "1.2.3.4/32"},
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "edr" in body["modules"]
        assert "itdr" in body["modules"]
        assert set(body["auto_included_modules"]) == {"edr", "itdr"}

    def test_generate_without_adapter_refs_keeps_old_response_shape(self, client: TestClient):
        """Back-compat: callers that never send adapter_refs[] still get a
        well-formed response with auto_included_modules=[]."""
        resp = client.post("/api/infra/generate", json={
            "provider": "aws",
            "region": "us-east-1",
            "modules": ["edr"],
            "params": {"project_name": "legacy", "dc_ssh_cidr": "1.2.3.4/32"},
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["auto_included_modules"] == []

    def test_unresolved_adapter_ref_does_not_400(self, client: TestClient):
        resp = client.post("/api/infra/generate", json={
            "provider": "aws",
            "region": "us-east-1",
            "modules": ["edr"],
            "adapter_refs": ["TOOL-DOES-NOT-EXIST"],
            "params": {"project_name": "stale", "dc_ssh_cidr": "1.2.3.4/32"},
        })
        assert resp.status_code == 200  # never fatal
        body = resp.json()
        assert body["auto_included_modules"] == []
