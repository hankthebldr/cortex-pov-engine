# Detection Planes

CortexSim organises scenarios by **detection plane** — the Cortex engine that
should fire on the simulated TTP. Plane is the primary filter in the console's
scenario browser and the primary axis in the POV report.

**16 planes carry loadable scenarios.** Per-plane counts, tactic coverage,
detection-type mix and the full scenario list live on the generated plane
pages — they are rebuilt from the corpus on every merge, so they cannot drift
from the YAML.

## Plane catalog

| Plane | Cortex engine | Scenarios | Primary driver |
|---|---|---:|---|
| [[Plane-CDR\|CDR]] | Cortex Cloud / Prisma Cloud Compute | 28 | container-runtime exec · K8s manifests |
| [[Plane-ANALYTICS\|ANALYTICS]] | XSIAM Correlation Engine | 23 | multi-plane stitching |
| [[Plane-EDR\|EDR]] | Cortex XDR Agent | 22 | identity-harness shell · signalbench |
| [[Plane-ITDR\|ITDR]] | Cortex ITDR | 20 | AD toolchain · `idp_signin_emulator` |
| [[Plane-NDR\|NDR]] | Network Security / Firewall Analytics | 12 | EAL simulator (HTTP/DNS/TCP plugins) |
| [[Plane-CLOUD_APP\|CLOUD_APP]] | Cortex Cloud App Security | 10 | `oauth_grant_emulator` |
| [[Plane-TIM\|TIM]] | Cortex Threat Intel Management | 9 | mocktaxii · IOC feeds |
| [[Plane-KOI\|KOI]] | Agentic endpoint / supply chain | 8 | `cortex-malicious-agentic-pack` · `agentic_egress` |
| [[Plane-AI_SPM\|AI_SPM]] | Cortex AI Security Posture Management | 7 | `ai-spm` IaC planted findings |
| [[Plane-AI_ACCESS\|AI_ACCESS]] | Cortex AI Access Security | 6 | `llm_provider_egress` |
| [[Plane-ASM\|ASM]] | Cortex Attack Surface Management | 6 | `asm` IaC exposed surface |
| [[Plane-BROWSER\|BROWSER]] | Prisma Browser | 6 | `cortex-browser-attacker` · `browser_attack_runner` |
| [[Plane-AIRS\|AIRS]] | Cortex AI Runtime Security | 5 | `cortex-prompt-attacker` · `airs_prompt_attack` |
| [[Plane-CSPM\|CSPM]] | Cortex Cloud Posture Management | 5 | `cspm` IaC intentional misconfigs |
| [[Plane-EMAIL\|EMAIL]] | XSIAM / NG-SIEM ingestion | 5 | `email_emitter` |
| [[Plane-DLP\|DLP]] | Enterprise DLP · DSPM · DDR · Endpoint DLP | 5 | endpoint + cloud data-movement TTPs |
| | **Total** | **177** | |

`EMAIL` is third-party log ingestion plus correlation — **not** a first-party
PANW product surface.

## How signal reaches a plane

Not every plane is driven the same way, and the difference matters when you are
diagnosing a missing detection:

- **Identity harness** (EDR, CDR-container, ITDR-endpoint) — real commands run
  under a real service account, producing genuine process causality.
- **EAL simulator** (NDR, CLOUD_APP, AI_ACCESS, AIRS, BROWSER, KOI, EMAIL, and
  the analytics log-streamers) — emits network or log shapes the identity
  harness cannot produce. See [[EAL Simulator]].
- **IaC planted findings** (CSPM, ASM, TIM, AI_SPM) — Terraform stands up
  deliberately misconfigured or deliberately exposed resources for a posture
  engine to discover. There is no runtime TTP to execute.
- **Correlation over the others** (ANALYTICS) — no signal of its own; it
  exercises XSIAM's ability to stitch signal that the other planes produced.

## Per-plane scenario directories

Each plane has `scenarios/{plane}/` with a `README.md` covering that plane's
conventions, expected detection types and UC prefix. Browse them from the
[scenarios tree](https://github.com/hankthebldr/cortex-pov-engine/tree/main/scenarios).

## Adding a new plane

1. Add the enum value in `core/engine/scenario_loader.py` (the `plane`
   validator). Until it is there the loader rejects every scenario on the new
   plane, and they are correctly excluded from all counts — DLP sat behind this
   gate until 2026-08-30.
2. Add a `PlaneDescriptor` module under `core/planes/` and register it.
3. Document it in `scenarios/_schema.yml`.
4. Create `scenarios/<plane>/README.md` with the conventions.
5. Author scenarios as `SIM-<PLANE>-NNN`, each with valid `uc_ref` / `tc_ref`
   (strict refs are on by default — a dangling ref rejects at boot).
6. Add the plane to `PLANE_ENGINE` in `scripts/gen_wiki.py` so its wiki page
   gets an engine name.
7. Update `CLAUDE.md`, the root `README.md`, and [[Roadmap]].
