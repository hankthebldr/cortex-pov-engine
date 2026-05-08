# Tools Catalog

What every in-tree tool does, where it lives, and how to invoke it.

## In-tree tools (no submodules)

### `cortex-vulnerable-llm`
Path: `sources/cortex-vulnerable-llm/`
License: Apache-2.0
Phase shipped: 2

Deliberately vulnerable Flask app exposing one blueprint per OWASP LLM
Top 10 (v2025/2.0) class. Backed by a deterministic regex-driven
canary — no real LLM calls, no API keys.

```bash
cortex-vulnerable-llm serve --port 8089 --vuln all
cortex-vulnerable-llm list --vuln all | jq .
cortex-vulnerable-llm docs llm01
```

### `cortex-prompt-attacker`
Path: `sources/cortex-prompt-attacker/`
License: Apache-2.0
Phase shipped: 3

Probe → Mutator → Target → Scorer pipeline for AIRS validation.
promptmap-compatible YAML, PyRIT-shape mutator chain, garak-shape
JSONL output.

```bash
cortex-prompt-attacker validate --probes scenarios/airs/probes/
cortex-prompt-attacker list-mutators
cortex-prompt-attacker list-scorers
cortex-prompt-attacker run --probes <dir> --target-url <url> --out events.jsonl
```

### `cortex-malicious-agentic-pack`
Path: `sources/cortex-malicious-agentic-pack/`
License: Apache-2.0
Phase shipped: 5

Static artifact tree for KOI detection validation. Six components: a
typosquat MCP server, a malicious MCP server (`[SYSTEM_OVERRIDE]` in
tool replies), a backdoored PyPI package (subprocess on import), a
malicious Claude skill, a VS Code extension, and a Chrome extension.
All side effects gated on `CORTEXSIM_C2_URL`.

Driven by the [[EAL Simulator]]'s `agentic_egress` plugin.

## Submodules under `sources/`

### `signalbench` (Rust)
MITRE-mapped endpoint telemetry generator. Runs locally, emits
synthetic process/file events that XDR Agent picks up.

```bash
cd sources/signalbench && cargo build --release
./target/release/signalbench --technique T1059.001 --count 5
```

### `mocktaxii` (Python)
STIX/TAXII 2.1 server, port 9000. Used for offline TIM scenarios.

```bash
cd sources/mocktaxii && pip install -r requirements.txt
python3 main.py --port 9000
```

### `gocortexbrokenbank` (Python)
Vulnerable CI/CD app, port 9001. Used for cloud-app /
ASPM scenarios.

### `ackbarx` (Rust)
SNMP trap forwarder to XSIAM HTTP endpoints.

```bash
cd sources/ackbarx && cargo build --release
./target/release/ackbarx --listen-port 162 --forward-url <xsiam>
```

### `xdrtop` (Rust)
Terminal-based live XSIAM/XDR monitor.

### `atomic-red-team`
Atomic TTP library — used as the source for several EDR scenarios'
shell commands.

## EAL Simulator plugins

See [[EAL Simulator]] for the full plugin catalog (7 built-ins).

## Operator CLI

`scripts/eal_simulator/cli.py` is the operator entry point for the
EAL simulator:

```bash
python -m scripts.eal_simulator.cli list-plugins | jq .
python -m scripts.eal_simulator.cli describe c2_http_beacon
python -m scripts.eal_simulator.cli run path/to/campaign.yml [--live]
python -m scripts.eal_simulator.cli worker
```

The `worker` subcommand is the K3s worker pod entrypoint when running
with a Celery-style queue. With the default in-memory queue it just
keeps the pod healthy so the API gateway can submit background tasks.

## Build matrix

| Tool | Language | Build | Python deps | Native deps |
|---|---|---|---|---|
| SimCore (`core/`) | Python 3.11 | `pip install -r core/requirements.txt` | fastapi, sqlalchemy, pydantic, httpx | — |
| `cortexsim-agent` (`agent/`) | Go 1.21+ | `go build -o bin/cortexsim-agent .` | — (stdlib only) | — |
| UI (`ui/`) | React + Vite | `npm install && npm run build` | — | node 20+ |
| `cortex-vulnerable-llm` | Python 3.11 | `pip install -e .[test]` | flask | — |
| `cortex-prompt-attacker` | Python 3.11 | `pip install -e .[test]` | httpx, pydantic, pyyaml | — |
| `signalbench` | Rust 1.74+ | `cargo build --release` | — | — |
| `ackbarx` | Rust | `cargo build --release` | — | — |
| `xdrtop` | Rust | `cargo build --release` | — | — |

## See also

- [[Architecture]] — three-tier design + plugin model
- [[EAL Simulator]] — plugin catalog + campaign model
- [[AIRS Validation]] — full canary + attacker stack
- [[KOI Validation]] — agentic supply-chain artifact pack
