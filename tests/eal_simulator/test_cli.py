"""CLI smoke tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# scripts/ is not a package on sys.path by default; reach in directly.
import importlib.util


def _load_cli():
    cli_path = Path(__file__).resolve().parents[2] / "scripts" / "eal_simulator" / "cli.py"
    spec = importlib.util.spec_from_file_location("eal_cli", cli_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_list_plugins(capsys):
    cli = _load_cli()
    rc = cli.main(["list-plugins"])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    names = {p["name"] for p in payload["plugins"]}
    assert "c2_http_beacon" in names


def test_cli_describe_unknown_returns_2(capsys):
    cli = _load_cli()
    rc = cli.main(["describe", "nope_plugin"])
    assert rc == 2


def test_cli_run_dry_run_campaign(tmp_path: Path, capsys):
    cli = _load_cli()
    spec_path = tmp_path / "c.yml"
    spec_path.write_text(
        """
campaign_id: CMP-NDR-900
name: cli-dry-run
dry_run: true
steps:
  - step_id: step-01
    plugin: c2_http_beacon
    params:
      target_url: http://testmynids.org/uid/index.html
      iterations: 1
      sleep_seconds: 0.1
""".strip(),
        encoding="utf-8",
    )
    rc = cli.main(["run", str(spec_path)])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["status"] == "complete"
    assert payload["dry_run"] is True
