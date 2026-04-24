"""
CortexSim Tool Registry — static + content-loaded merger.

`STATIC_TOOL_REGISTRY` holds the built-in tools defined in the Phase 1 spec.
`TOOL_REGISTRY` starts as a copy; `content_loader.merge_installed_tools()`
overlays entries from /opt/cortexsim/content/installed.json at startup.

Keep STATIC_TOOL_REGISTRY exactly as defined — existing tests and runtime
code rely on its schema (source_path, build_cmd, binary, run_template, type,
plane, description[, port, health_check]).
"""

STATIC_TOOL_REGISTRY: dict = {
    "signalbench": {
        "source_path": "sources/signalbench",
        "build_cmd": "cargo build --release",
        "binary": "sources/signalbench/target/release/signalbench",
        "run_template": "{binary} --technique {mitre_id} --count {count} --output json",
        "type": "binary",
        "plane": ["edr"],
        "description": "MITRE-mapped endpoint telemetry generator",
    },
    "mocktaxii": {
        "source_path": "sources/mocktaxii",
        "build_cmd": "pip install -r requirements.txt",
        "run_template": "python3 {source_path}/main.py --port {port}",
        "type": "service",
        "port": 9000,
        "plane": ["ndr"],
        "health_check": "http://localhost:9000/taxii/",
        "description": "STIX/TAXII 2.1 server for TIM scenarios",
    },
    "gocortexbrokenbank": {
        "source_path": "sources/gocortexbrokenbank",
        "build_cmd": "pip install -r requirements.txt",
        "run_template": "python3 {source_path}/app.py --port {port}",
        "type": "service",
        "port": 9001,
        "plane": ["cloud_app"],
        "health_check": "http://localhost:9001/health",
        "description": "Intentionally vulnerable app for CI/CD and ASPM scenarios",
    },
    "ackbarx": {
        "source_path": "sources/ackbarx",
        "build_cmd": "cargo build --release",
        "binary": "sources/ackbarx/target/release/ackbarx",
        "run_template": "{binary} --listen-port 162 --forward-url {xsiam_endpoint}",
        "type": "service",
        "plane": ["ndr"],
        "description": "SNMP trap forwarder to XSIAM HTTP endpoints",
    },
    "xdrtop": {
        "source_path": "sources/xdrtop",
        "build_cmd": "cargo build --release",
        "binary": "sources/xdrtop/target/release/xdrtop",
        "run_template": "{binary}",
        "type": "binary",
        "plane": ["all"],
        "description": "Terminal-based live XSIAM/XDR monitor",
    },
}

# Runtime registry — starts with statics, merged with installed content on startup
TOOL_REGISTRY: dict = dict(STATIC_TOOL_REGISTRY)


def reset_to_static() -> None:
    """Test helper — clear runtime additions and restore static-only state."""
    TOOL_REGISTRY.clear()
    TOOL_REGISTRY.update(STATIC_TOOL_REGISTRY)
