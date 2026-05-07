"""CLI smoke tests."""

import json

import pytest

from cortex_vulnerable_llm import cli


def test_list_returns_routes(capsys):
    rc = cli.main(["list", "--vuln", "llm01,llm07"])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["mounted_vulns"] == ["LLM01", "LLM07"]
    paths = {r["rule"] for r in payload["routes"]}
    assert "/owasp/llm01/chat" in paths
    assert "/owasp/llm07/chat" in paths


def test_docs_known_code(capsys):
    rc = cli.main(["docs", "llm01"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "LLM01" in out
    assert "Prompt Injection" in out


def test_docs_unknown_code_returns_2():
    rc = cli.main(["docs", "llm99"])
    assert rc == 2


def test_serve_help_via_no_args():
    with pytest.raises(SystemExit):
        cli.main([])
