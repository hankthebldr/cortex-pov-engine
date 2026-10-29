"""Rules test for the SIM-KOI-009 pack component ``prettier-pro-canary``.

The component is a benign, decoy-only VS Code extension. These tests enforce
spec section 6's rules on its source so a later edit cannot quietly make it
less harmless.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPONENT = REPO_ROOT / "sources" / "cortex-malicious-agentic-pack" / "vscode" / "prettier-pro-canary" / "extension"
FORBIDDEN = ("require(\"crypto\")", "require('crypto')", "setTimeout", "setInterval", "atob(",
             "Buffer.from(", ".aws/credentials", ".ssh/id_rsa", "login.keychain", "/Users/",
             # Dynamic evaluation: the loader shape the spec forbids reproducing.
             "eval(", "new Function(",
             # Raw sockets and name resolution: the component's only network
             # path is the CORTEXSIM_C2_URL-gated http/https request.
             "require(\"net\")", "require(\"dns\")",
             # Synchronous child execution: every spawn must stay non-blocking
             # and short-lived, as execFile already is.
             "execSync", "spawnSync",
             # Undeclared egress and payload encoding.
             "fetch(", "toString(\"base64\")")


def check_source_rules(src: str) -> None:
    """The rule set, as one function, so the negative control exercises the real check."""
    assert "CORTEXSIM-CANARY" in src
    assert "registerUriHandler" in src
    for path in ("/step-03", "/step-04", "/step-05", "/step-06"):
        assert path in src
    assert "CORTEXSIM_KOI009_DECOY_ROOT" in src and "CORTEXSIM_C2_URL" in src
    for tok in FORBIDDEN:
        assert tok not in src, tok
    hosts = set(re.findall(r"https?://([A-Za-z0-9.-]+)", src))
    assert hosts <= {"cortexsim-canary.invalid"}, hosts


def test_manifest_shape():
    m = json.loads((COMPONENT / "package.json").read_text())
    assert m["publisher"] == "cortexsim-canary" and m["name"] == "prettier-pro-canary"
    assert m["displayName"] == "Prettier Pro" and m["activationEvents"] == ["*"]
    assert "CORTEXSIM-CANARY" in m["description"]


def test_source_rules():
    check_source_rules((COMPONENT / "dist" / "extension.js").read_text())


@pytest.mark.parametrize("tok", FORBIDDEN + ("https://example.invalid/x",))
def test_rules_fail_on_a_broken_copy(tok):
    """Gate A evidence: the real check is observed failing on a copy that breaks one rule."""
    broken = (COMPONENT / "dist" / "extension.js").read_text() + f"\n// {tok}\n"
    with pytest.raises(AssertionError):
        check_source_rules(broken)
