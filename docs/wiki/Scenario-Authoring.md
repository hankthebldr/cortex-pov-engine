# Scenario Authoring

How to write a new scenario YAML.

## File location + naming

Scenarios live under `scenarios/{plane}/sim-<plane>-NNN-<slug>.yml`
(e.g. `scenarios/airs/sim-airs-001-direct-prompt-injection.yml`).

ID format: `SIM-{PLANE}-{NNN}` — never reuse an ID, even after
deprecation.

## Schema reference

The canonical schema is documented in
[`scenarios/_schema.yml`](https://github.com/hankthebldr/cortex-pov-engine/blob/main/scenarios/_schema.yml).
Every scenario YAML is validated against the Pydantic schema in
`core/engine/scenario_loader.py` at startup. Invalid scenarios are
rejected (logged + counted, but the load continues).

## Required fields

```yaml
scenario_id: SIM-NDR-001
name: "Human-readable name"
version: "1.0"
status: active                 # active | draft | deprecated

plane: NDR                     # see Detection-Planes wiki page
detection_types:
  - BIOC
  - Analytics
  - IOC

uc_ref: UCS-NDR-01
tc_ref: TC-NDR-01
uc_name: "..."
tc_name: "..."

mitre_tactic: "TA0011"
mitre_tactic_name: "Command and Control"
mitre_technique: "T1071.001"
mitre_technique_name: "Application Layer Protocol: Web Protocols"

execution_identity:
  default: container-runtime
  options: [container-runtime, root]

push_supported: true
pull_supported: true

steps:
  - id: step-01
    name: "..."
    command: |
      ...
    identity: container-runtime
    mitre_technique: "T1071.001"
    expected_detections:
      - plane: NDR
        type: Analytics
        description: "..."

cleanup:
  commands: []

author: "..."
created: "2026-01-01"
last_updated: "2026-01-01"
```

## Optional fields

```yaml
additional_techniques:
  - technique: "T1059.004"
    name: "Unix Shell"

threat_report: "Unit42 - ..."
threat_report_url: "https://..."

required_content:
  - repo: "owner/repo"

infra_modules_needed:
  - base
  - ndr

external_tools:
  - name: cortexsim-eal-cli
    source: "https://..."
    type: script
    install_inline: false

tags: [foo, bar]
```

## Step pattern — invoking the EAL simulator

For network-observable TTPs, prefer the EAL simulator over hand-rolled
curl. Inline the campaign YAML with a heredoc:

```yaml
- id: step-02
  name: "Periodic HTTP beacon to authorised test endpoint"
  command: |
    cat > /tmp/c2-beacon-live.yml <<'YML'
    campaign_id: CMP-NDR-001
    name: "NDR validation — C2 beacon live"
    authorized_by: "domain-consultant@paloaltonetworks.com"
    simulation_authorized: true
    target_allowlist:
      - testmynids.org
    dry_run: false
    steps:
      - step_id: step-01
        plugin: c2_http_beacon
        params:
          target_url: http://testmynids.org/uid/index.html
          iterations: 12
          sleep_seconds: 30
    YML
    python3 -m scripts.eal_simulator.cli run /tmp/c2-beacon-live.yml --live
  identity: container-runtime
  mitre_technique: "T1071.001"
  expected_detections:
    - plane: NDR
      type: Analytics
      description: "NGFW EAL — periodic HTTP beacon"
```

This is the standard pattern across all AIRS / AI_ACCESS / KOI
scenarios — see those plane directories for examples.

## Validation

After authoring, verify the scenario loads:

```bash
cd core
CORTEXSIM_BASE_DIR=$(pwd)/.. python3 -c "
import asyncio, sys; sys.path.insert(0, '.')
from database import init_db, AsyncSessionLocal
from engine.scenario_loader import load_scenarios
async def go():
    await init_db()
    async with AsyncSessionLocal() as db:
        loaded = await load_scenarios('../scenarios', db)
    print(f'loaded: {len(loaded)}')
asyncio.run(go())
"
```

A clean scenario load confirms the schema validates. Rejected scenarios
log a descriptive Pydantic error.

## Per-step expected detections

Each step lists the detections you expect Cortex to fire. The
orchestrator auto-seeds one `Result` row per `expected_detections`
entry per step. The DC validates each row by marking `observed_at`
in the [[POV Runbook]] flow.

```yaml
expected_detections:
  - plane: NDR
    type: Analytics
    description: "NGFW EAL — periodic HTTP beacon to known-bad indicator"
  - plane: EDR
    type: BIOC
    description: "XDR — curl executed from www-data with suspicious user-agent"
  - plane: ANALYTICS
    type: Analytics
    description: "XSIAM correlation — NGFW outbound + XDR process stitched"
```

`plane` = which Cortex engine fires; `type` = `BIOC | Analytics | IOC`;
`description` is what the DC types into the XSIAM search to confirm.

## Checklist before merge

- [ ] `scenario_id` follows `SIM-<PLANE>-NNN` format and is unique
- [ ] `mitre_tactic` + `mitre_technique` map to a real ATT&CK ID
- [ ] At least 3 steps; each has `expected_detections`
- [ ] `cleanup.commands` removes every artifact created during execution
- [ ] Every host in the steps appears in `target_allowlist` of any
      EAL campaign yaml that reaches it
- [ ] `status: active` only if the runtime tooling exists (otherwise
      `status: draft`)
- [ ] The plane's `README.md` is updated if this scenario adds a new
      coverage row

## See also

- [[Detection Planes]] — what plane to use
- [[EAL Simulator]] — wiring the campaign YAML to a plugin
- [[Plugin Development]] — adding the plugin a scenario references
- [`scenarios/_schema.yml`](https://github.com/hankthebldr/cortex-pov-engine/blob/main/scenarios/_schema.yml) — canonical schema
