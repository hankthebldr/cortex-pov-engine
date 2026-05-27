# AI-SPM Family — Detection Methodology Deep-Dive

**Parent doc:** [`v2.0-methodology-master.md`](v2.0-methodology-master.md)
**Family:** F3 (Asset Discovery) + F4 (Posture Scoring) + F6 (MTTD timing)
**Scope:** TC-AISP-01 through TC-AISP-06 — all 6 MOAT-tier AI Security Posture Management TCs
**TC count:** 6 (all MOAT) · **Primary products:** Prisma AIRS + Cortex Cloud
**Owner:** Henry Reed · Last updated 2026-05-27

---

## 1 · Positioning context

The v2.0 master sheet documents the AI-SPM positioning verbatim in the `CrowdStrike Position` / `Microsoft Position` columns. Both are categorized **MOAT** — meaning Cortex out-positions both competitors on AI-SPM specifically:

> **CrowdStrike:** has AI-SPM bolt-on; Cortex provides code-to-cloud AI model discovery + runtime protection
> **Microsoft:** AI security focused on Copilot/Azure AI; Cortex AI-SPM covers any cloud AI workload

The competitive narrative is **multi-cloud + multi-framework**: any AI workload (SageMaker, Bedrock, Vertex, Azure OpenAI, OpenAI API, custom LLMs on GPU VMs) inventoried in one posture pane. The detection methodology must therefore exercise heterogeneous AI surfaces, not just one provider.

---

## 2 · Proposed new plane: `AI_SPM`

CortexSim currently has two AI-adjacent planes:

- **AIRS** (`scenarios/airs/`) — Cortex AI Runtime Security: OWASP LLM01-10 prompt-injection attacks against deployed LLM apps. **Runtime** detection of attacks in flight.
- **AI_ACCESS** (`scenarios/ai_access/`) — Cortex AI Access Security: outbound employee LLM usage with DLP markers. **Network egress** detection of data exfiltration to LLM providers.

Neither covers **posture**. AI-SPM is the third leg:

- **AI_SPM** (`scenarios/ai_spm/` — *proposed*) — Cortex AI Security Posture Management: **static inventory + configuration** of AI assets at rest. Detects shadow AI, model misconfig, supply-chain risk, sensitive training data — *before* anything runs.

| Plane | Question it answers | Detection moment |
|-------|--------------------|------------------|
| AI_SPM | "What AI assets exist and are they configured safely?" | Discovery scan (continuous, every N min) |
| AI_ACCESS | "Is anyone sending sensitive data to external LLMs?" | Network egress event |
| AIRS | "Is someone attacking our deployed LLM?" | Prompt/response inspection at runtime |

The three planes share underlying telemetry but answer distinct questions and trigger distinct workflows. Keeping them separate avoids the failure mode where AI-SPM posture findings (a configuration drift) get mixed with AIRS attack incidents (an active exploitation).

**Schema impact.** Add `AI_SPM` to the `plane` enum in `scenarios/_schema.yml`:

```yaml
plane: "EDR | CDR | NDR | ITDR | CLOUD_APP | ANALYTICS
      | AI_ACCESS | AIRS | AI_SPM | BROWSER | KOI"
```

And to the Pydantic validator's `PlaneEnum` in `core/engine/scenario_loader.py`.

---

## 3 · Shared simulation fixture: `infra/modules/aws/ai-spm/`

The master sheet shows **all 6 TCs share the same `Simulation Input`**: "Deploy SageMaker endpoint + Bedrock invocation + Lambda→OpenAI + shadow GPU VM with custom LLM". One IaC module plants all of them; six scenarios assert on different aspects.

**Proposed module contents** (Phase A AWS, mirrors the existing `infra/modules/aws/cspm/` pattern):

| Resource | Purpose | Per-TC relevance |
|----------|---------|------------------|
| `aws_sagemaker_endpoint.canary` | Managed model endpoint | AISP-01 inventory, AISP-02 model-security checks |
| `aws_sagemaker_model.poisoning_candidate` | Model with intentionally vulnerable pickle artifact | AISP-04 static analysis (pickle deserialization) |
| `aws_bedrock_model_invocation_logging_configuration` (intentionally disabled) | Bedrock without invocation logging | AISP-02 (misconfig finding), AISP-06 (dashboard tile) |
| `aws_lambda_function.openai_proxy` with hardcoded API key in env var | Lambda → OpenAI integration | AISP-01 (third-party AI inventory), AISP-04 (hardcoded secret in ML pipeline) |
| `aws_ec2_instance.shadow_gpu_llm` (g4dn.xlarge with Ollama container) | Shadow AI on unmanaged GPU | AISP-01 (shadow AI discovery — the headline) |
| `aws_s3_bucket.training_data` with seeded PII fixtures | Training dataset with PII | AISP-05 sensitive-data classification |
| `aws_iam_role.sagemaker_overprivileged` with `*:*` policy | Over-permissioned ML role | AISP-02 model-security access-control finding |
| `aws_s3_object.pickled_model` (intentionally insecure serialization) | Pickled model in S3 | AISP-04 insecure model serialization |
| Synthetic `requirements.txt` with known-vulnerable ML deps (e.g. older `transformers` with CVE) | Supply-chain risk | AISP-03 ecosystem & supply chain |

All resources tagged `CortexSimAISPMFinding=<finding_type>` for deterministic cross-reference, mirroring the existing CSPM module's `CortexSimCSPMFinding` convention.

**Estimated cost.** A `g4dn.xlarge` is ~$0.50/hr. Module is destroy-by-default; bundle includes a `make destroy` target. POV burn for a one-day demo: ~$15 across all resources.

---

## 4 · Per-TC detection methodology

### TC-AISP-01 — AI Asset Discovery & Inventory ⭐ *(reference implementation in this session)*

**KPI.** Asset Discovery Coverage (Qualitative pass — all planted AI assets discovered within one inventory cycle)
**Family.** F3 — Asset Discovery
**Scenario file.** `scenarios/ai_spm/sim-aispm-001-ai-asset-discovery.yml`

**Detection methodology.**

1. **Plant.** Apply `infra/modules/aws/ai-spm/` bundle. After Terraform apply succeeds, capture the set of resource ARNs into a known-cardinality manifest (e.g., 7 distinct AI assets: 1 SageMaker endpoint, 1 SageMaker model, 1 Bedrock invocation, 1 Lambda-to-OpenAI proxy, 1 shadow GPU instance, 1 pickled model in S3, 1 training-data bucket).
2. **Wait.** Cortex Cloud's inventory cycle (typically 5–15 min for new resources). Scenario step inserts a deliberate `sleep 900` with explanatory output.
3. **Probe.** `GET /api/v1/inventory?asset_type=ai&tag=CortexSimAISPMFinding`. Expect ≥ 7 assets returned with `asset_type ∈ {ai_endpoint, ai_model, ai_data_store, ai_function_integration}`.
4. **Assert.** For each planted asset ARN, confirm presence in the response. **Coverage = found / planted.** Pass threshold: 100 % for MOAT positioning ("we don't miss any shadow AI" is the entire value prop).

**Why this is the reference implementation.** TC-AISP-01 is the simplest of the 6 (no model-internals inspection, no behavioral analysis — just "did Cortex find what we planted?"), it's MOAT-tier with explicit competitive positioning, and it exercises the brand-new plane end-to-end. Implementing it forces the schema change, the IaC module, the orchestrator harness, and the scenario YAML — every layer touched.

### TC-AISP-02 — AI Model Security Assessment

**KPI.** MTTD on AI model misconfig findings
**Family.** F4 — Posture Scoring + F6 — Response Timing
**Scenario file.** `scenarios/ai_spm/sim-aispm-002-ai-model-security-assessment.yml` *(proposed)*

**Detection methodology.** Plant the same fixture from §3 with deliberate model-security issues: SageMaker endpoint with overprivileged IAM role (data-poisoning vector), Bedrock with invocation logging disabled (model-extraction visibility gap), Lambda with no input validation (prompt-injection surface). Wait for posture scan. Assert that Cortex Cloud raises 3 findings, one per issue, each tagged with the AI-specific attack taxonomy (data poisoning / model extraction / prompt injection).

**KPI capture.** `MTTD = first_finding.discovered_at - terraform.apply_complete`. Threshold: < 15 min for MOAT.

### TC-AISP-03 — AI Ecosystem & Supply Chain Risk

**KPI.** Asset Discovery Coverage on third-party AI dependencies
**Family.** F3 — Asset Discovery (focused on dependency graph)
**Scenario file.** `scenarios/ai_spm/sim-aispm-003-ai-supply-chain.yml` *(proposed)*

**Detection methodology.** The fixture ships a `requirements.txt` with at least 5 ML packages, 2 of which have known CVEs (e.g., a pinned old `transformers` with a known issue, or `tensorflow` with a known model-loading RCE). The Lambda function and SageMaker container both reference this requirements file. Wait for ecosystem scan.

**Probe.** `GET /api/v1/inventory?asset_type=ai_dependency&risk_level=high`. Assert that the 2 known-CVE packages are flagged with their CVE IDs and EU AI Act risk classification annotations. Also assert the dependency graph traces from the SageMaker model → Lambda → OpenAI plugin chain.

This is the **EU AI Act tie-in TC** — high regulatory weight for EMEA POVs.

### TC-AISP-04 — AI Static Risk Analysis

**KPI.** MTTD on static-analysis findings in AI code
**Family.** F4 — Posture Scoring
**Scenario file.** `scenarios/ai_spm/sim-aispm-004-ai-static-analysis.yml` *(proposed)*

**Detection methodology.** The fixture plants known-bad patterns at static-analysis time:
- Lambda code with `OPENAI_API_KEY = "sk-..."` hardcoded
- A pickled model `.pkl` in S3 (insecure serialization — `pickle.loads` is RCE)
- SageMaker training script with no input validation on user-supplied prompts

Wait for static scan. Assert 3 findings, each with AI-specific taxonomy:
- `MLSEC-001: hardcoded_credentials_in_ml_pipeline`
- `MLSEC-002: insecure_model_serialization`
- `MLSEC-003: unvalidated_model_inputs`

**KPI capture.** Same `MTTD` pattern as AISP-02.

### TC-AISP-05 — AI Sensitive Data Classification

**KPI.** MTTD on sensitive-data findings in training data
**Family.** F4 + F9 — Posture + traceability (data lineage source → training → model)
**Scenario file.** `scenarios/ai_spm/sim-aispm-005-ai-sensitive-data.yml` *(proposed)*

**Detection methodology.** The training-data S3 bucket from §3 contains a synthetic CSV with planted PII / PHI / PCI markers (canary patterns like the `cortex-vulnerable-llm` project uses for DLP). The fixture also includes a SageMaker training job manifest that references the bucket.

**Probe.** Cortex Cloud DSPM + AI-SPM cross-scan. Assert that:
- The S3 bucket is flagged with PII/PHI/PCI tags
- The SageMaker training job is linked to the flagged bucket in the data-lineage graph
- A governance violation `AI-GOV-001: regulated_data_in_training_set` is raised

This TC has the highest **executive narrative weight** for finance/healthcare POVs because it directly answers "is your AI training data leaking compliance scope?"

### TC-AISP-06 — AI Security Dashboard & Posture (executive view)

**KPI.** Asset Discovery Coverage (the dashboard accurately reflects discovered assets)
**Family.** F3 + qualitative dashboard verification
**Scenario file.** `scenarios/ai_spm/sim-aispm-006-ai-security-dashboard.yml` *(proposed)*

**Detection methodology.** After AISP-01..05 have all planted and Cortex has scanned, fetch the AI-SPM dashboard summary endpoint: `GET /api/v1/dashboards/ai_spm/summary`. Assert:
- Aggregate AI asset count matches sum of planted assets across AISP-01..05
- Risk category breakdown shows non-zero counts in model_security / data_governance / access_control categories
- Trend line endpoint returns a series (even a one-point series is enough to prove the API works)

This is F10-flavored (the dashboard is largely visual) but the API contract behind it is verifiable.

---

## 5 · Execution sequence for a POV

For a customer specifically interested in AI-SPM, the recommended POV flow:

```
Day 0:  Apply infra/modules/aws/ai-spm/ bundle (10 min)
        Configure Cortex Cloud AI-SPM scanner against the planted AWS account (15 min)

Day 1:  Run sim-aispm-001 (Asset Discovery) — proves shadow AI is found
        Run sim-aispm-003 (Supply Chain) — proves CVE-in-ML-deps narrative
        Customer-facing readout: dashboard tour (TC-AISP-06)

Day 2:  Run sim-aispm-002 (Model Security Assessment)
        Run sim-aispm-004 (Static Risk Analysis)
        Run sim-aispm-005 (Sensitive Data Classification) — the headline for regulated industries

Day 3:  Destroy bundle. POV report exports via /api/runs/{id}/report?format=markdown
        with all 6 TCs showing pass/fail + Asset Discovery Coverage + MTTD KPIs.
```

Total POV cycle: 3 days. The fixture stays applied across all 6 scenario runs — minimizes cost and avoids re-onboarding noise.

---

## 6 · Build order for AI-SPM

| Priority | Deliverable | TCs | Effort |
|---------:|-------------|-----|-------:|
| P0 | Schema extension (add `AI_SPM` to plane enum) | (all) | XS |
| P0 | `infra/modules/aws/ai-spm/` Terraform module | (all) | M |
| P0 | `scenarios/ai_spm/sim-aispm-001-ai-asset-discovery.yml` (reference) | AISP-01 | M |
| P1 | `scenarios/ai_spm/sim-aispm-003-ai-supply-chain.yml` | AISP-03 | M |
| P1 | `scenarios/ai_spm/sim-aispm-005-ai-sensitive-data.yml` | AISP-05 | M |
| P2 | `scenarios/ai_spm/sim-aispm-002-ai-model-security-assessment.yml` | AISP-02 | M |
| P2 | `scenarios/ai_spm/sim-aispm-004-ai-static-analysis.yml` | AISP-04 | M |
| P3 | `scenarios/ai_spm/sim-aispm-006-ai-security-dashboard.yml` | AISP-06 | S |
| P3 | Add `inventory_probe` validation method to orchestrator | (all F3) | M |

P0 lands in this session as the reference implementation. P1–P3 are scheduled.

---

## 7 · Why this lane is worth disproportionate effort

- **All 6 TCs are MOAT** (rare — only 8 % of all TCs in the master sheet are 100 % MOAT within a single Use Case).
- **The IaC fixture is reusable across all 6** — one module, six scenarios. Cost-per-TC is the lowest of any lane in the v2.0 sheet.
- **The competitive positioning is freshly written** in the v2.0 master (CrowdStrike's AI-SPM is bolt-on; Microsoft's is Copilot-centric). The window for a clean "we discover shadow AI you missed" demo is widest right now.
- **Regulatory tailwind.** EU AI Act enforcement, NIST AI RMF, and US executive orders on AI all give the AISP-05 narrative legs in regulated-industry POVs through 2027.

This deep-dive plus the reference implementation should arm a DC to run an AI-SPM POV end-to-end without further engineering ask.
