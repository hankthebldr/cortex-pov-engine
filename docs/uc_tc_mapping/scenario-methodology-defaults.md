# v2.0 Methodology Metadata — Per-Plane Rollout Convention

> **Status:** active convention · **Owner:** Lane SCEN · **Established:** 2026-06-08
>
> Companion to [`v2.0-methodology-master.md`](v2.0-methodology-master.md) (the 10
> methodology families) and the scenario schema
> ([`../../scenarios/_schema.yml`](../../scenarios/_schema.yml) §"v2.0 KPI /
> Methodology Metadata"). This doc records the **per-plane defaults** used to roll
> the v2.0 KPI/MOAT metadata across the scenario corpus (gap **S-07**) and the F2
> stitching metadata across the ANALYTICS plane (gap **S-04**).

## Why a convention

The v2.0 master sheet assigns every Test Case to exactly one of ten methodology
families (F1..F10) with a Primary KPI and a MOAT/LEAD/PARITY differentiation
tier. Authoring those columns one scenario at a time invites drift. This doc
fixes a **sensible default per plane** so the metadata is consistent and the POV
report exec-summary ordering (which keys off `moat_tier` + `methodology_family`)
is deterministic. Defaults are *not fabricated KPIs* — they map each plane to the
family its detection shape already belongs to in the master methodology.

## The per-plane default table

| Plane | Family | Primary KPI | `moat_tier` | Rationale |
|-------|--------|-------------|-------------|-----------|
| **EDR** | F1 | MTTD | LEAD | Endpoint signal injection → BIOC/XQL detection + timing. |
| **CDR** | F1 | MTTD | LEAD | Container/k8s signal injection → detection + timing. |
| **NDR** | F1 | MTTD | LEAD | Per-protocol App-ID signal injection → detection + timing. |
| **ITDR** | F1 | Detection Accuracy | LEAD | IdP audit-log signal accuracy (impossible travel, MFA fatigue, …). |
| **CLOUD_APP** | F1 | Detection Accuracy | LEAD | OAuth-grant detection. The benign-baseline control (SIM-CLOUD-005) uses **False Positive Rate** instead. |
| **AI_ACCESS** | F1 | Detection Accuracy | LEAD | LLM-egress DLP marker detection. |
| **AIRS** | F1 | Detection Accuracy | **MOAT** | OWASP-LLM runtime protection — a Cortex differentiator vs. EDR-only competitors. |
| **BROWSER** | F1 | Detection Accuracy | LEAD | Prisma Browser DLP / extension / phishing. |
| **KOI** | F1 | Detection Accuracy | **MOAT** | Agentic / supply-chain (MCP, skills, extensions) — novel coverage with no point-product equivalent. |
| **ANALYTICS** (multi-plane) | F2 | Cross-Source Correlation Rate **or** Causality Chain Completeness | **MOAT** | Cross-plane causality stitching — the headline XSIAM moat. See F2 fields below. |
| **AI_SPM** | F3 / F4 | Asset Discovery Coverage / MTTD | MOAT | Static AI posture (already populated — see `methodology-ai-spm.md`). |
| **ASM / CSPM / TIM** | F3 / F4 / F2 | Asset Discovery Coverage / Compliance Score / Cross-Source Correlation Rate | MOAT | Posture/exposure/intel planes (already populated on the launchable scenarios). |

### Threshold defaults

- **F1 / MTTD** — `MTTD ≤ 300 s` (5-minute detection budget; conservative for a
  controlled signal). The benign-baseline FP control asserts `False Positive
  Rate = 0 %`.
- **F1 / Detection Accuracy** — `Detection Accuracy ≥ 90 %` (≥ 9 of 10 injected
  signals produce the expected alert).
- **F2 / Cross-Source Correlation Rate** — `≥ 80 %` of runs stitch into one
  incident inside the correlation window.
- **F2 / Causality Chain Completeness** — `≥ 2 planes` (or `≥ 3` for the
  three-plane flagships SIM-MP-002 / SIM-MP-005) represented under one
  `incident_id`.

These are *demo-conservative* defaults a DC can tighten per engagement; they are
captured in the Run record and surfaced verbatim in the POV report.

## F2 stitching-field convention (ANALYTICS plane — S-04)

Every `plane: ANALYTICS` multi-plane scenario carries the three F2 fields:

| Field | Convention |
|-------|------------|
| `correlation_window_seconds` | `60` default; `30` for the tight-timing flagship SIM-MP-005. |
| `required_planes_in_incident` | The exact planes the causality chain MUST span (the verifier asserts each is present under one `incident_id`). |
| `stitching_key` | The join field: **`src_host`** for host-centric chains (C2 beacon, DNS-tunnel exfil), **`user_principal`** for identity-centric chains (Kerberoast→PtH→DCSync, hybrid endpoint→cloud cred pivot). One of: `src_host | session_id | user_principal | container_id`. |

Per-scenario F2 assignment after this rollout:

| Scenario | window | planes | key |
|----------|-------:|--------|-----|
| SIM-MP-001 | 60 | EDR, NDR | `src_host` |
| SIM-MP-002 | 60 | ITDR, EDR, NDR | `user_principal` |
| SIM-MP-003 | 60 | EDR, NDR | `src_host` |
| SIM-MP-004 | 60 | EDR, CLOUD_APP | `user_principal` |
| SIM-MP-005 | 30 | EDR, NDR, ITDR | `src_host` |

## Coverage after rollout

All 63 loadable scenarios now carry `moat_tier`, `methodology_family`,
`primary_kpi`, `threshold`, and `success_criteria`. All 5 ANALYTICS scenarios
carry the three F2 fields (previously only SIM-MP-001 and SIM-MP-005 did — S-04).
The loader (`core/engine/scenario_loader.py`) validates `methodology_family ∈
F1..F10` and `moat_tier ∈ {MOAT, LEAD, PARITY}`; all other fields are free-form
enrichment.
