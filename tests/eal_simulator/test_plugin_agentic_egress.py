"""Tests for the agentic_egress EAL plugin.

We mock ``httpx.AsyncClient`` so no real outbound traffic is generated;
each test asserts the URL/body/header shape the plugin would send and
the audit events it emits. The artifact tarballing is exercised against
the real in-tree ``cortex-malicious-agentic-pack/`` so the
artifact-resolution path stays honest.
"""
from __future__ import annotations

import asyncio
import io
import tarfile
from pathlib import Path
from typing import Any

import httpx
import pytest

from eal_simulator import AuditLogger, Campaign, CampaignExecutor
from eal_simulator.plugins.agentic_egress import (
    AgenticEgress,
    AgenticEgressParams,
    _COMPONENTS,
    _list_components,
    _resolve_artifact_dir,
    _resolve_pack_root,
    _tarball_directory,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _FakeResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code


class _RecordingClient:
    """Stub ``httpx.AsyncClient`` that records each request."""

    def __init__(self, status_code: int = 200, raise_exc: Exception | None = None) -> None:
        self.requests: list[dict[str, Any]] = []
        self.status_code = status_code
        self.raise_exc = raise_exc
        self.closed = False

    async def get(self, url: str, *, headers=None):
        self.requests.append({"method": "GET", "url": url, "headers": dict(headers or {}), "content": None})
        if self.raise_exc:
            raise self.raise_exc
        return _FakeResponse(self.status_code)

    async def request(self, method: str, url: str, *, headers=None, content=None):
        self.requests.append({
            "method": method, "url": url,
            "headers": dict(headers or {}),
            "content": content,
        })
        if self.raise_exc:
            raise self.raise_exc
        return _FakeResponse(self.status_code)

    async def aclose(self) -> None:
        self.closed = True


def _campaign(*, component: str, artifact_name: str, target_url: str,
              dry_run: bool = False, iterations: int = 1,
              pack_root: str | None = None) -> Campaign:
    spec = {
        "campaign_id": "CMP-KOI-INTEG-001",
        "name": "agentic_egress test",
        "dry_run": dry_run,
        "steps": [{
            "step_id": "step-01",
            "plugin": "agentic_egress",
            "params": {
                "target_url": target_url,
                "component": component,
                "artifact_name": artifact_name,
                "iterations": iterations,
                "sleep_seconds": 0.0,
                "pack_root": pack_root,
            },
        }],
    }
    if not dry_run:
        spec.update({
            "simulation_authorized": True,
            "authorized_by": "tester",
            "target_allowlist": ["staging.invalid", "registry.invalid"],
        })
    return Campaign.model_validate(spec)


# --------------------------------------------------------------------------
# Param validation
# --------------------------------------------------------------------------


class TestParamValidation:
    def test_required_fields(self):
        with pytest.raises(Exception):
            AgenticEgressParams.model_validate({})

    def test_unknown_component_rejected(self):
        with pytest.raises(Exception, match="component must be one of"):
            AgenticEgressParams.model_validate({
                "target_url": "https://x.invalid/",
                "component": "facebook_app",
                "artifact_name": "x",
            })

    def test_path_traversal_in_artifact_name_rejected(self):
        for bad in ["../etc", "../../passwd", "ok/name", "with space"]:
            with pytest.raises(Exception):
                AgenticEgressParams.model_validate({
                    "target_url": "https://x.invalid/",
                    "component": "mcp_server",
                    "artifact_name": bad,
                })

    def test_iterations_bounded(self):
        with pytest.raises(Exception):
            AgenticEgressParams.model_validate({
                "target_url": "https://x.invalid/",
                "component": "mcp_server",
                "artifact_name": "anthroopic-calculator",
                "iterations": 0,
            })

    def test_target_url_scheme(self):
        with pytest.raises(Exception, match="http or https"):
            AgenticEgressParams.model_validate({
                "target_url": "ftp://x.invalid/",
                "component": "mcp_server",
                "artifact_name": "x",
            })


# --------------------------------------------------------------------------
# Artifact resolution + tarballing
# --------------------------------------------------------------------------


class TestArtifactResolution:
    def test_six_components_registered(self):
        assert _list_components() == sorted([
            "mcp_server", "mcp_package", "pypi_mirror",
            "claude_skill", "vscode_ext", "chrome_ext",
        ])

    def test_pack_root_walks_up_to_in_tree(self, repo_root: Path):
        # ``repo_root`` fixture is provided by the project's tests/conftest.py
        root = _resolve_pack_root(None)
        assert root.name == "cortex-malicious-agentic-pack"
        assert (root / "README.md").is_file()

    def test_pack_root_explicit_override(self, tmp_path: Path):
        target = tmp_path / "elsewhere"
        target.mkdir()
        out = _resolve_pack_root(str(target))
        assert out == target.resolve()

    def test_resolve_artifact_dir_rejects_path_traversal(self, repo_root: Path):
        pack = _resolve_pack_root(None)
        component = _COMPONENTS["mcp_server"]
        # ``..`` would escape the component dir; resolution must refuse it.
        # Pydantic catches this earlier, but the helper is defence-in-depth.
        with pytest.raises((ValueError, FileNotFoundError)):
            _resolve_artifact_dir(pack, component, "..")

    def test_tarball_includes_files(self, tmp_path: Path):
        d = tmp_path / "art"
        d.mkdir()
        (d / "README.md").write_text("hello")
        (d / "manifest.json").write_text("{}")
        blob = _tarball_directory(d, "art")
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
            names = sorted(tar.getnames())
        assert "art/README.md" in names
        assert "art/manifest.json" in names


# --------------------------------------------------------------------------
# Plugin run path — mocked httpx
# --------------------------------------------------------------------------


class TestPluginRun:
    def _run_with_stub(self, monkeypatch, **campaign_kw):
        stub = _RecordingClient()
        monkeypatch.setattr(
            AgenticEgress, "_build_client",
            lambda self, params, component: stub,
        )
        campaign = _campaign(**campaign_kw)
        executor = CampaignExecutor(audit=AuditLogger(file_path=None))
        state = _run(executor.execute(campaign))
        return state, stub

    def test_dry_run_does_not_invoke_client(self, monkeypatch):
        def _boom(self, params, component):  # noqa: ARG001
            raise AssertionError("client should not be built in dry-run")
        monkeypatch.setattr(AgenticEgress, "_build_client", _boom)

        campaign = _campaign(
            component="mcp_server",
            artifact_name="anthroopic-calculator",
            target_url="https://staging.invalid/mcp/",
            dry_run=True,
        )
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
        sr = state.step_results[0]
        assert sr.status == "success"
        assert sr.detail["dry_run"] is True
        assert sr.detail["artifact_bytes"] > 0

    def test_mcp_server_artifact_posted(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch,
            component="mcp_server",
            artifact_name="anthroopic-calculator",
            target_url="https://staging.invalid/mcp/",
        )
        assert state.step_results[0].status == "success"
        assert len(stub.requests) == 1
        req = stub.requests[0]
        assert req["method"] == "POST"
        assert req["url"].endswith("anthroopic-calculator.tar.gz")
        # Body is a gzipped tar of the artifact.
        with tarfile.open(fileobj=io.BytesIO(req["content"]), mode="r:gz") as tar:
            names = tar.getnames()
        assert any("anthroopic-calculator" in n for n in names)

    def test_pypi_mirror_does_get_then_post(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch,
            component="pypi_mirror",
            artifact_name="mcp-server-helpers-typo",
            target_url="https://registry.invalid/simple",
        )
        assert state.step_results[0].status == "success"
        # Should be exactly two requests: GET probe + POST artifact.
        methods = [r["method"] for r in stub.requests]
        assert methods == ["GET", "POST"]
        assert "name=mcp-server-helpers-typo" in stub.requests[0]["url"]

    def test_user_agent_matches_component_fingerprint(self, monkeypatch):
        captured: dict[str, str] = {}

        def _capture(self, params, component):
            from eal_simulator.plugins.agentic_egress import _format_user_agent
            captured["ua"] = _format_user_agent(component.user_agent)
            return _RecordingClient()

        monkeypatch.setattr(AgenticEgress, "_build_client", _capture)
        campaign = _campaign(
            component="vscode_ext",
            artifact_name="helpful-ai-assistant",
            target_url="https://staging.invalid/vsx/",
        )
        _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
        assert "VSCode" in captured["ua"]

    def test_per_iteration_simulation_id(self, monkeypatch):
        state, stub = self._run_with_stub(
            monkeypatch,
            component="chrome_ext",
            artifact_name="ai-page-summarizer",
            target_url="https://staging.invalid/cws/",
            iterations=3,
        )
        ids = [
            r["headers"].get("x-simulation-run-id")
            for r in stub.requests
        ]
        # 3 iterations × 1 request each (chrome_ext is single fetch).
        assert len(ids) == 3
        assert len(set(ids)) == 3

    def test_safety_violation_when_target_not_allowlisted(self, monkeypatch):
        stub = _RecordingClient()
        monkeypatch.setattr(
            AgenticEgress, "_build_client",
            lambda self, params, component: stub,
        )
        spec = {
            "campaign_id": "CMP-KOI-SAFETY-001",
            "name": "safety",
            "dry_run": False,
            "simulation_authorized": True,
            "authorized_by": "t",
            "target_allowlist": ["only-this.invalid"],  # excludes our target
            "steps": [{
                "step_id": "step-01",
                "plugin": "agentic_egress",
                "params": {
                    "target_url": "https://other.invalid/mcp/",
                    "component": "mcp_server",
                    "artifact_name": "anthroopic-calculator",
                },
            }],
        }
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(
            Campaign.model_validate(spec),
        ))
        sr = state.step_results[0]
        assert sr.status == "error"
        assert "safety_violation" in (sr.error or "")
        assert stub.requests == []

    def test_missing_artifact_directory_returns_error(self, monkeypatch):
        stub = _RecordingClient()
        monkeypatch.setattr(
            AgenticEgress, "_build_client",
            lambda self, params, component: stub,
        )
        campaign = _campaign(
            component="mcp_server",
            artifact_name="does-not-exist-anywhere",
            target_url="https://staging.invalid/mcp/",
        )
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
        sr = state.step_results[0]
        assert sr.status == "error"
        assert "artifact_resolution_failed" in (sr.error or "")
        assert stub.requests == []

    def test_http_error_surfaces_as_failure_event(self, monkeypatch):
        stub = _RecordingClient(raise_exc=httpx.ConnectError("boom"))
        monkeypatch.setattr(
            AgenticEgress, "_build_client",
            lambda self, params, component: stub,
        )
        campaign = _campaign(
            component="mcp_server",
            artifact_name="anthroopic-calculator",
            target_url="https://staging.invalid/mcp/",
        )
        state = _run(CampaignExecutor(audit=AuditLogger(file_path=None)).execute(campaign))
        # Plugin reports success at the run level (error is per-request).
        assert state.step_results[0].status == "success"
        assert len(stub.requests) == 1


# --------------------------------------------------------------------------
# Pack-content sanity checks (catches regressions in the artifact pack)
# --------------------------------------------------------------------------


class TestPackContent:
    def test_anthroopic_calculator_has_canary_marker(self, repo_root: Path):
        path = (repo_root / "sources" / "cortex-malicious-agentic-pack"
                / "mcp" / "anthroopic-calculator" / "mcp.json")
        assert path.is_file()
        body = path.read_text(encoding="utf-8")
        assert "anthroopic-tools" in body  # typosquat publisher
        assert "Ignore previous instructions" in body  # injection in description

    def test_pa_firewall_mcp_embeds_system_override(self, repo_root: Path):
        path = (repo_root / "sources" / "cortex-malicious-agentic-pack"
                / "mcp" / "pa-firewall-mcp" / "server.py")
        assert "[SYSTEM_OVERRIDE]" in path.read_text(encoding="utf-8")
        assert "AKIA0000CORTEXSIMCANARY" in path.read_text(encoding="utf-8")

    def test_pypi_typosquat_runs_subprocess_on_import(self, repo_root: Path):
        path = (repo_root / "sources" / "cortex-malicious-agentic-pack"
                / "pypi" / "mcp-server-helpers-typo" / "src"
                / "mcp_server_helpers" / "__init__.py")
        body = path.read_text(encoding="utf-8")
        assert "subprocess" in body
        assert "_post_install_beacon()" in body
        # Module-level call (not behind if __name__ == "__main__").
        assert "\n_post_install_beacon()" in body

    def test_vscode_extension_overbroad_activation(self, repo_root: Path):
        path = (repo_root / "sources" / "cortex-malicious-agentic-pack"
                / "vscode" / "helpful-ai-assistant" / "extension"
                / "package.json")
        import json
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["activationEvents"] == ["*"]

    def test_chrome_extension_overbroad_permissions(self, repo_root: Path):
        path = (repo_root / "sources" / "cortex-malicious-agentic-pack"
                / "chrome" / "ai-page-summarizer" / "manifest.json")
        import json
        manifest = json.loads(path.read_text(encoding="utf-8"))
        perms = set(manifest["permissions"])
        assert {"cookies", "webRequest", "webRequestBlocking"} <= perms
        assert "<all_urls>" in manifest["host_permissions"]

    def test_claude_skill_carries_hidden_injection(self, repo_root: Path):
        path = (repo_root / "sources" / "cortex-malicious-agentic-pack"
                / "claude-skills" / "code-reviewer.skill" / "skill.md")
        body = path.read_text(encoding="utf-8")
        assert "Ignore previous instructions" in body
        assert "~/.aws/credentials" in body


# --------------------------------------------------------------------------
# Plugin metadata / registry integration
# --------------------------------------------------------------------------


class TestRegistration:
    def test_plugin_registered(self):
        from eal_simulator import get_default_registry

        assert get_default_registry().has("agentic_egress")

    def test_metadata_lists_eal_targets(self):
        from eal_simulator import get_default_registry

        meta = get_default_registry().get("agentic_egress").metadata()
        assert any("KOI" in t for t in meta["eal_targets"])
        assert "T1195" in meta["mitre_techniques"]
        props = meta["params_schema"]["properties"]
        assert "component" in props and "artifact_name" in props
