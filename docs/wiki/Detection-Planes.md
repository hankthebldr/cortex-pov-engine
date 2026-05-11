# Detection Planes

CortexSim organises scenarios by **detection plane** — the Cortex
engine that should fire on the simulated TTP. Plane is the primary
filter in the UI scenario browser and the primary axis in the POV
report.

## Plane catalog

| Plane | Cortex engine | Active scenarios | Driver |
|---|---|---|---|
| `EDR` | Cortex XDR Agent | 5 | Identity-harness shell commands; signalbench |
| `CDR` | Cortex Cloud / Prisma Cloud Compute | 5 | Container-runtime exec, K8s manifests |
| `NDR` | NGFW Analytics / EAL | 5 | EAL simulator (HTTP/DNS/TCP plugins) |
| `ITDR` | Cortex ITDR | (IaC only — scenarios pending) | Future Phase 6+ |
| `CSPM` | Cortex Cloud Posture | (IaC module ships intentional misconfigs) | Cortex Cloud scan |
| `ASM` | Cortex Attack Surface Management | (IaC module exposes services) | ASM crawler |
| `TIM` | Cortex Threat Intel Management | (IaC ships TAXII + fake C2) | mocktaxii |
| `CLOUD_APP` | Cortex Cloud App Security | planned | TBD |
| `ANALYTICS` | XSIAM Correlation Engine | 3 multi-plane | stitching |
| `AI_ACCESS` | Cortex AI Access Security | **5** | `llm_provider_egress` plugin |
| `AIRS` | Cortex AI Runtime Security | **5** | `cortex-prompt-attacker` + `airs_prompt_attack` |
| `BROWSER` | Prisma Browser | 5 (draft — Phase 6) | `cortex-browser-attacker` (planned) |
| `KOI` | Agentic endpoint / supply-chain | **5** | `cortex-malicious-agentic-pack` + `agentic_egress` |

## AI / Browser / Agentic surface

The four planes added in Phases 1–5 correspond to Cortex's
post-2025 product surfaces. See:

- [[AI Access Validation]]
- [[AIRS Validation]]
- [[Browser Validation]] *(Phase 6)*
- [[KOI Validation]]

Each has a dedicated wiki page covering scenarios, tooling, and
expected detections.

## Per-plane scenario directories

Each plane has a directory under `scenarios/{plane}/` with a `README.md`
explaining the conventions for that plane plus N scenario YAML files.

| Plane dir | README link |
|---|---|
| `scenarios/edr/` | [README](https://github.com/hankthebldr/cortex-pov-engine/blob/main/scenarios/edr/README.md) |
| `scenarios/cdr/` | [README](https://github.com/hankthebldr/cortex-pov-engine/blob/main/scenarios/cdr/README.md) |
| `scenarios/ndr/` | [README](https://github.com/hankthebldr/cortex-pov-engine/blob/main/scenarios/ndr/README.md) |
| `scenarios/itdr/` | [README](https://github.com/hankthebldr/cortex-pov-engine/blob/main/scenarios/itdr/README.md) |
| `scenarios/cloud_app/` | [README](https://github.com/hankthebldr/cortex-pov-engine/blob/main/scenarios/cloud_app/README.md) |
| `scenarios/multi_plane/` | [README](https://github.com/hankthebldr/cortex-pov-engine/blob/main/scenarios/multi_plane/README.md) |
| `scenarios/ai_access/` | [README](https://github.com/hankthebldr/cortex-pov-engine/blob/main/scenarios/ai_access/README.md) |
| `scenarios/airs/` | [README](https://github.com/hankthebldr/cortex-pov-engine/blob/main/scenarios/airs/README.md) |
| `scenarios/airs/probes/` | [README](https://github.com/hankthebldr/cortex-pov-engine/blob/main/scenarios/airs/probes/README.md) |
| `scenarios/browser/` | [README](https://github.com/hankthebldr/cortex-pov-engine/blob/main/scenarios/browser/README.md) |
| `scenarios/koi/` | [README](https://github.com/hankthebldr/cortex-pov-engine/blob/main/scenarios/koi/README.md) |

## Adding a new plane

1. Add the enum value to `core/engine/scenario_loader.py::validate_plane`.
2. Document it in `scenarios/_schema.yml` (the plane comment block).
3. Create `scenarios/<plane>/` with a `README.md` explaining the
   conventions, expected detection types, and UC-prefix for the plane.
4. Author 5 scenarios. Each gets a `SIM-<PLANE>-NNN` ID.
5. Update the [[Roadmap]] page status.
6. Update `CLAUDE.md` and `README.md` plane tables.
