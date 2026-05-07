"""CLI smoke tests."""
from __future__ import annotations

import json
import textwrap

import pytest

from cortex_prompt_attacker import cli


def _write_probe(dir_path, name="p1"):
    text = textwrap.dedent(f"""
        schema_version: 1
        name: {name}
        type: prompt_injection
        owasp_id: LLM01
        severity: low
        prompt: hi
    """).strip()
    path = dir_path / f"{name}.yml"
    path.write_text(text)
    return path


def test_list_mutators(capsys):
    rc = cli.main(["list-mutators"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "noop" in payload and "base64" in payload


def test_list_scorers(capsys):
    rc = cli.main(["list-scorers"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "system_prompt_leak" in payload


def test_validate_clean_dir(tmp_path, capsys):
    _write_probe(tmp_path, "p1")
    rc = cli.main(["validate", "--probes", str(tmp_path)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["loaded"] == 1


def test_validate_missing_dir_returns_1(tmp_path, capsys):
    rc = cli.main(["validate", "--probes", str(tmp_path / "nope")])
    assert rc == 1


def test_run_with_no_probes_errors(tmp_path):
    rc = cli.main([
        "run",
        "--probes", str(tmp_path),
        "--target-url", "http://127.0.0.1:1",
    ])
    assert rc == 2


def test_no_args_exits():
    with pytest.raises(SystemExit):
        cli.main([])
