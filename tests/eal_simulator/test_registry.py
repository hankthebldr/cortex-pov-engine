"""Plugin registry tests — discovery, lookup, manifest."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from eal_simulator.registry import PluginRegistry, get_default_registry


def test_default_registry_loads_built_ins():
    reg = get_default_registry()
    names = reg.names()
    expected = {
        "c2_http_beacon",
        "dns_tunnel_exfil",
        "bulk_https_exfil",
        "stratum_tcp_connect",
        "smb_rpc_sweep",
    }
    assert expected.issubset(set(names)), f"missing: {expected - set(names)}"


def test_registry_lookup_case_insensitive():
    reg = get_default_registry()
    cls = reg.get("C2_Http_Beacon")
    assert cls.Meta.name == "c2_http_beacon"


def test_registry_get_unknown_raises_with_help():
    reg = get_default_registry()
    with pytest.raises(KeyError, match="Available"):
        reg.get("does_not_exist")


def test_manifest_contains_required_keys():
    reg = get_default_registry()
    for entry in reg.manifest():
        assert {"name", "version", "description", "mitre_techniques",
                "eal_targets", "params_schema", "class"} <= set(entry)
        assert isinstance(entry["params_schema"], dict)


def test_load_directory_picks_up_external_plugin(tmp_path: Path):
    plugin_src = textwrap.dedent("""
        from pydantic import BaseModel
        from eal_simulator.base import BaseSimulation, SimulationResult


        class P(BaseModel):
            target: str = "x"


        class Custom(BaseSimulation):
            class Meta:
                name = "custom_external"
                params_model = P

            async def run(self, ctx):
                return SimulationResult(
                    plugin=self.Meta.name,
                    step_id=ctx.step_id,
                    status="success",
                    started_at=self.utcnow(),
                    completed_at=self.utcnow(),
                    events_emitted=0,
                )
    """)
    pf = tmp_path / "custom.py"
    pf.write_text(plugin_src, encoding="utf-8")

    reg = PluginRegistry()
    added = reg.load_directory(tmp_path)
    assert added == 1
    assert reg.has("custom_external")


def test_register_replaces_existing(monkeypatch):
    reg = PluginRegistry()
    from tests.eal_simulator.conftest import DummyPlugin

    reg.register(DummyPlugin)
    assert reg.has("test_dummy")

    # Subclass redefining the same Meta.name; registry should swap it in.
    class DummyAlt(DummyPlugin):
        class Meta(DummyPlugin.Meta):
            name = "test_dummy"

    reg.register(DummyAlt)
    assert reg.get("test_dummy") is DummyAlt
