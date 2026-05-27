# scenarios/ai_spm — Cortex AI Security Posture Management

**New detection plane introduced in CortexSim v2.0 methodology pass.** See [`docs/uc_tc_mapping/methodology-ai-spm.md`](../../docs/uc_tc_mapping/methodology-ai-spm.md) for the family deep-dive.

## Scope

This plane covers **static inventory + configuration** of AI/ML assets at rest — distinct from AIRS (runtime LLM attack protection) and AI_ACCESS (egress DLP to LLM providers). It answers: *what AI exists in our cloud, is it configured safely, and is regulated data near it?*

| Plane | Question | Detection moment |
|-------|----------|------------------|
| AI_SPM (this) | What AI exists and is it configured safely? | Discovery scan (continuous) |
| AI_ACCESS | Is anyone sending sensitive data to external LLMs? | Network egress |
| AIRS | Is someone attacking our deployed LLM? | Prompt/response at runtime |

## Scenarios

| ID | TC ref | KPI | Tier | Status |
|----|--------|-----|------|--------|
| SIM-AISPM-001 | TC-AISP-01 | Asset Discovery Coverage | MOAT | **active** (reference impl) |
| SIM-AISPM-002 | TC-AISP-02 | MTTD (model security) | MOAT | proposed |
| SIM-AISPM-003 | TC-AISP-03 | Asset Discovery Coverage (supply chain) | MOAT | proposed |
| SIM-AISPM-004 | TC-AISP-04 | MTTD (static analysis) | MOAT | proposed |
| SIM-AISPM-005 | TC-AISP-05 | MTTD (sensitive data) | MOAT | proposed |
| SIM-AISPM-006 | TC-AISP-06 | Asset Discovery Coverage (dashboard) | MOAT | proposed |

## Required infrastructure

All scenarios in this directory consume one shared fixture: [`infra/modules/aws/ai-spm/`](../../infra/modules/aws/ai-spm/). Apply once, run all six scenarios against the same planted state.

Bundle generator inclusion: every AI-SPM scenario declares `infra_modules_needed: [base, ai-spm]`. The IaC generator (`core/engine/infra_generator.py`) auto-suggests the module when an AI-SPM scenario is selected.

## Operator prerequisites

- Cortex Cloud tenant with AI-SPM scanning enabled and the test AWS account onboarded
- `CORTEX_CLOUD_API_BASE` and `CORTEX_CLOUD_API_TOKEN` in the environment (or supplied via the bundle's `parameters.json`)
- `aws-cli`, `jq`, `curl` available on the SimCore container or jumpbox (the standard install.sh provisions all three)
