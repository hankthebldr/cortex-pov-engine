---
name: ai-spm
description: Heterogeneous AI/ML assets (SageMaker, Bedrock, Lambda→OpenAI, shadow GPU LLM, training data with PII) for Cortex AI-SPM validation. Every asset is a planted finding that Cortex AI-SPM should surface in inventory and posture.
providers: [aws]
required_params: [project_name]
optional_params: [enable_shadow_gpu]
dependencies: [base]
---

# ai-spm (AWS)

Provisions a curated set of **AI/ML assets across heterogeneous frameworks** that Cortex AI-SPM (Cortex Cloud Posture Management's AI extension) should discover, inventory, and assess. This is the AI-SPM equivalent of the existing `cspm` module — a safe, scoped environment for validating that Cortex's AI posture detection catches real-world shadow AI, misconfigured ML pipelines, and AI-specific data governance violations.

This module is the canonical fixture for all 6 MOAT-tier TCs in the AI-SPM family (TC-AISP-01 through TC-AISP-06). Apply once, run all six scenarios against the same planted state.

## Findings planted

Every resource is tagged with `CortexSimAISPMFinding=<finding-type>` so the DC can cross-reference what Cortex AI-SPM surfaced against what was intentionally deployed.

| Category | Finding | Resource | TC coverage |
|----------|---------|----------|-------------|
| **Managed AI** | SageMaker endpoint (canary) | `*-aispm-sagemaker-endpoint` | AISP-01, AISP-02 |
| **Managed AI** | SageMaker model with insecure pickle artifact | `*-aispm-pickled-model` (S3 object) | AISP-04 |
| **Managed AI** | Bedrock invocation logging **disabled** | account-wide setting | AISP-02 |
| **Third-party AI** | Lambda with hardcoded OpenAI API key in env | `*-aispm-openai-proxy` | AISP-01, AISP-04 |
| **Shadow AI** | EC2 g4dn with Ollama LLM container (optional) | `*-aispm-shadow-gpu-llm` | AISP-01 (the headline finding) |
| **Training data** | S3 bucket with PII/PHI/PCI canary fixtures | `*-aispm-training-data` | AISP-05 |
| **IAM** | SageMaker execution role with `*:*` policy | `*-aispm-sagemaker-overprivileged` | AISP-02 |
| **Supply chain** | Lambda layer with known-vulnerable ML deps | `*-aispm-vulnerable-ml-deps` (layer) | AISP-03 |

## What this does NOT include

- No real customer data — PII canaries are deterministic fakes (`SSN: 000-00-0000`, etc.)
- No real OpenAI API key — `sk-DEMO-CORTEXSIM-AISP-04-PLANTED` is a planted canary the Lambda's env exposes for the static-analysis TC; it does not authenticate against any real provider.
- No actual model training run — SageMaker model resource is created but no training job runs (cost containment).
- The `g4dn.xlarge` shadow-GPU instance is **gated behind `enable_shadow_gpu=true`** (default false) so a DC can opt in to the shadow-AI demo at ~$0.50/hr or skip it for cost-sensitive POVs. AISP-01 still passes with the managed assets alone — the GPU just makes the demo more visceral.

## Estimated cost

| Resource | Hourly | Notes |
|----------|-------:|-------|
| SageMaker endpoint (`ml.t2.medium`) | ~$0.06 | Smallest managed instance |
| Lambda (1 invocation/min) | ~$0.00 | Free tier |
| S3 buckets | ~$0.00 | <1 GB total |
| Shadow GPU EC2 (`g4dn.xlarge`) **opt-in** | ~$0.53 | Most of the bill if enabled |
| **Total without GPU** | **~$1.50/day** | Suitable for week-long POVs |
| **Total with GPU** | **~$14/day** | One-day demo recommended |

## Validation workflow

1. **Apply.** Run the generated bundle's root Terraform. Capture the `findings_summary` output — that's the ground-truth manifest.
2. **Wait for Cortex Cloud AI-SPM scan.** Typical first-scan latency is 10–15 minutes.
3. **Run scenarios.** From SimCore, run `sim-aispm-001` first to validate asset-discovery coverage. Then run AISP-02..06 against the same fixture.
4. **Compare.** Each scenario's `expected_detections` lists the specific finding Cortex AI-SPM should surface. The Run report shows pass/fail per finding plus aggregate Asset Discovery Coverage %.

## References

- TC family deep-dive: [`docs/uc_tc_mapping/methodology-ai-spm.md`](../../../../docs/uc_tc_mapping/methodology-ai-spm.md)
- Master methodology: [`docs/uc_tc_mapping/v2.0-methodology-master.md`](../../../../docs/uc_tc_mapping/v2.0-methodology-master.md)
- Pattern parent: [`infra/modules/aws/cspm/README.md`](../cspm/README.md)
