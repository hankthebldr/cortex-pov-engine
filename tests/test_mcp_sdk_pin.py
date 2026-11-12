import importlib.metadata, re
from pathlib import Path

REQ = Path(__file__).resolve().parents[1] / "core" / "requirements.txt"

def test_mcp_sdk_is_pinned_and_importable():
    assert "mcp==1.30.0\n" in REQ.read_text()
    assert importlib.metadata.version("mcp") == "1.30.0"
    from mcp.server.fastmcp import FastMCP  # noqa: F401  (1.x import path)

def test_transitive_pins_follow_the_sdk():
    text = REQ.read_text()
    assert re.search(r"^uvicorn\[standard\]==0\.31\.1$", text, re.M)
    assert re.search(r"^httpx==0\.27\.2$", text, re.M)

def test_mcp_settings_exist_with_safe_defaults():
    from config import Settings
    s = Settings(_env_file=str(REQ.parents[1] / ".env.example"))   # same construction as tests/test_config.py:114
    assert s.CORTEXSIM_MCP_HTTP is False
    assert s.CORTEXSIM_MCP_ALLOWED_HOSTS == ""
    assert s.CORTEXSIM_MCP_MAX_TENANT_CALLS_PER_MINUTE == 30
    assert s.CORTEXSIM_URL == "http://127.0.0.1:8888"
