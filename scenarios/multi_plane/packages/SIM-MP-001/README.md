# SIM-MP-001 — C2 Beacon Callback — NGFW + XDR Stitch Validation

**Scenario ID**: `SIM-MP-001`
**Plane**: ANALYTICS (multi-plane: EDR, NDR)
**Methodology family**: F2 — Causality Stitching Validation
**Primary KPI**: Causality Chain Completeness
**MOAT tier**: MOAT
**Source YAML**: [`../../mp-001-c2-beacon-ngfw-xdr-stitch.yml`](../../mp-001-c2-beacon-ngfw-xdr-stitch.yml)

## What This Scenario Proves

This is an F2 (causality-stitching) multi-plane scenario. Coordinated signals
across EDR, NDR sharing the join key
`src_host` must stitch into a **single XSIAM incident** within a 60-second
correlation window. A point product sees only its own plane's alert; only
Cortex XSIAM fuses them into one causality chain — that is the POV moat.

## Kill Chain

| Step | Technique | Plane(s) | Expected signal |
|------|-----------|----------|-----------------|
| step-01 | T1059.004 | EDR | XDR — interactive bash spawned from www-data service context |
| step-02 | T1071.001 | ANALYTICS+EDR+NDR | NGFW — repetitive HTTP beacon to testmynids.org (known IOC list) |
| step-03 | T1572 | ANALYTICS+EDR+NDR | NGFW — DNS anomaly: high TXT query volume with high-entropy labels |
| step-04 | T1105 | ANALYTICS+EDR+NDR | NGFW — download from IOC-listed domain matching threat intel feed |

The XSIAM-stitched incident (see `detections/correlation_rules.xql`) is the
primary POV differentiator.

## Prerequisites

- [ ] Cortex XSIAM tenant with the relevant plane sensors ingesting
      (EDR, NDR)
- [ ] Lab target(s) in an isolated segment — not corporate network
- [ ] Clean baseline (zero pre-existing alerts) before execution
- [ ] IaC lab provisioned: `infra_modules_needed: ['base', 'edr', 'ndr']`
- [ ] Legal / scope authorization signed for this lab

If any checkbox is unchecked: **STOP**. Remediate before running.

## Quick Start

```bash
# Full kill chain with 60s inter-step pacing
./run.sh --mode full --delay 60

# Single step (for rule tuning)
./run.sh --mode single_ttp --ttp T1059.004

# Dry-run (print what would execute, no side effects)
./run.sh --mode full --dry-run

# Cleanup
./run.sh --cleanup
```

Containerized:

```bash
docker compose up --build
# Detection feed served at http://localhost:8080 (evidence/)
```

## IaC Prerequisites

```
POST /api/infra/generate
{ "provider": "aws", "modules": ["base", "edr", "ndr"], "scenario_id": "SIM-MP-001" }
```

## Package Tree

```
SIM-MP-001/
├── README.md                  ← this file
├── run.sh                     ← single-entry runner (mirrors SIM-MP-004)
├── docker-compose.yml         ← containerized runner
├── ttps/                      ← one .sh per scenario step (verbatim commands)
├── detections/
│   ├── correlation_rules.xql  ← XSIAM stitching rule + F2 verifier
│   ├── ioc_list.csv           ← IOCs this scenario generates
│   └── README.md
└── evidence/                  ← run logs + detection scorecard (populated at run time)
```

> **Exemplar note.** `SIM-MP-004` is the canonical reference package and additionally
> ships an `architecture/`, `c2/`, `context/`, and `evidence/screenshots/` deep-dive
> tree. This package is the lightweight-but-runnable form (S-18): same runnable
> contract (`run.sh` + `docker-compose.yml` + per-step TTP scripts + deployable
> detection artifacts) without duplicating the full narrative tree.
