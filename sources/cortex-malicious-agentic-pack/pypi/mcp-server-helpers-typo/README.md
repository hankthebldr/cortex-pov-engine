# mcp-server-helpers (TYPOSQUAT — CORTEXSIM-CANARY)

This is a deliberately backdoored typosquat of the legitimate
`mcp-server-helper` package, shipped as part of the
`cortex-malicious-agentic-pack` artifact set for KOI detection
validation.

## Detection signals (code scan)

* Package name typosquats `mcp-server-helper` (off-by-one suffix).
* `src/mcp_server_helpers/__init__.py` runs `subprocess.run` on import
  — i.e. side effects on `import mcp_server_helpers`.
* Side effect attempts an outbound HTTP GET to `CORTEXSIM_C2_URL` (env
  default empty — no real beacon at scan-time).

## Safety

The post-install hook reads `CORTEXSIM_C2_URL` from the environment
and **no-ops** if unset. Without that variable being explicitly set,
importing this package emits a marker line on stderr and returns.

```bash
# Scan-safe (no network):
python -c "import mcp_server_helpers"

# To exercise the C2 path in a controlled lab:
CORTEXSIM_C2_URL=http://127.0.0.1:9999/ python -c "import mcp_server_helpers"
```

CORTEXSIM-CANARY.
