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
        # TWO PHASES, and phase 1 is not optional: src/techniques/software.rs
        # does include_bytes!("../../embedded_binaries/pacemaker_helper") and
        # embedded_binaries/ is NOT in the upstream git tree — it is produced by
        # the separate helpers/pacemaker crate. The old bare "cargo build
        # --release" failed rc=101 for every DC who ever ran it, naming a file
        # nobody can find rather than the crate that makes it. Prefer
        # `make rust-dist`, which does this in a musl container and needs no
        # toolchain on the target at all.
        "build_cmd": (
            "cd helpers/pacemaker && cargo build --release && cd ../.. && "
            "mkdir -p embedded_binaries && "
            "cp helpers/pacemaker/target/release/pacemaker_helper embedded_binaries/ && "
            "cargo build --release"
        ),
        "binary": "rust-dist/cortexsim-tool-signalbench-linux-amd64",
        # Subcommand CLI: `signalbench run T1082 T1016`. The previous template
        # passed --technique/--count/--output, NONE of which exist — clap
        # rejected it outright with "unexpected argument '--technique' found".
        # Verified by executing the baked binary.
        "run_template": "{binary} run {mitre_id}",
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
        "binary": "rust-dist/cortexsim-tool-ackbarx-linux-amd64",
        # ackbarx is CONFIG-FILE driven. It has no --listen-port and no
        # --forward-url; the previous template died with "unexpected argument
        # '--listen-port' found". Generate a config with --generate-config (or
        # --generate-simple-config) and pass it with -c. Verified by execution.
        "run_template": "{binary} --config {config_path}",
        "type": "service",
        "plane": ["ndr"],
        "description": "SNMP trap forwarder to XSIAM HTTP endpoints",
    },
    "xdrtop": {
        "source_path": "sources/xdrtop",
        "build_cmd": "cargo build --release",
        "binary": "rust-dist/cortexsim-tool-xdrtop-linux-amd64",
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
