"""CLI smoke tests."""
from __future__ import annotations

import json
import textwrap

import pytest

from cortex_browser_attacker import cli


def _write_campaign(tmp_path, channel="stub"):
    path = tmp_path / "c.yml"
    path.write_text(textwrap.dedent(f"""
        campaign_id: BC-BROWSER-001
        name: smoke
        browser_channel: {channel}
        actions:
          - action: navigate
            params:
              url: https://allowed.invalid/
    """).strip())
    return path


def test_list_actions_json(capsys):
    rc = cli.main(["list-actions"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "navigate" in payload
    assert "paste" in payload


def test_list_actions_names(capsys):
    rc = cli.main(["list-actions", "--format", "names"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "navigate" in out
    assert "screenshot" in out


def test_validate_clean(tmp_path, capsys):
    p = _write_campaign(tmp_path)
    rc = cli.main(["validate", "--campaign", str(p)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["actions"] == ["navigate"]


def test_validate_missing_file_returns_1(tmp_path):
    rc = cli.main(["validate", "--campaign", str(tmp_path / "does-not-exist.yml")])
    assert rc == 1


def test_run_dry_against_stub(tmp_path, capsys):
    p = _write_campaign(tmp_path)
    rc = cli.main(["run", "--campaign", str(p), "--browser-channel", "stub",
                   "--out", "-"])
    assert rc == 0
    out = capsys.readouterr().out
    lines = [json.loads(L) for L in out.splitlines() if L.strip()]
    assert lines[0]["entry_type"] == "run_meta"
    assert any(L.get("entry_type") == "action_attempt" for L in lines)


def test_run_no_args_exits():
    with pytest.raises(SystemExit):
        cli.main([])
