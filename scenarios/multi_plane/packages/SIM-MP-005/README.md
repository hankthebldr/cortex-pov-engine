# SIM-MP-005 — Cross-Plane Correlation MOAT — EDR + NDR + ITDR Stitch (TC-IR-05)

**Scenario ID**: `SIM-MP-005`
**Plane**: ANALYTICS (multi-plane: EDR, NDR, ITDR)
**Methodology family**: F2 — Causality Stitching Validation
**Primary KPI**: Cross-Source Correlation Rate
**MOAT tier**: MOAT
**Source YAML**: [`../../mp-005-cross-plane-correlation.yml`](../../mp-005-cross-plane-correlation.yml)

## What This Scenario Proves

This is an F2 (causality-stitching) multi-plane scenario. Coordinated signals
across EDR, NDR, ITDR sharing the join key
`src_host` must stitch into a **single XSIAM incident** within a 30-second
correlation window. A point product sees only its own plane's alert; only
Cortex XSIAM fuses them into one causality chain — that is the POV moat.

## Kill Chain

| Step | Technique | Plane(s) | Expected signal |
|------|-----------|----------|-----------------|
| step-01 | T1071.001 | NDR | NGFW — outbound HTTP to known IOC list (testmynids.org) |
| step-02 | T1059.004 | EDR | XDR BIOC — interactive bash from www-data service context (process lin |
| step-03 | T1110.003 | ITDR | ITDR — Kerberos pre-auth failure burst against 5 service accounts from |
| step-04 | T1078 | ANALYTICS | XSIAM correlation — EDR + NDR + ITDR signals from same src_host stitch |

The XSIAM-stitched incident (see `detections/correlation_rules.xql`) is the
primary POV differentiator.

## Prerequisites

- [ ] Cortex XSIAM tenant with the relevant plane sensors ingesting
      (EDR, NDR, ITDR)
- [ ] Lab target(s) in an isolated segment — not corporate network
- [ ] Clean baseline (zero pre-existing alerts) before execution
- [ ] IaC lab provisioned: `infra_modules_needed: ['base', 'edr', 'ndr', 'itdr']`
- [ ] Legal / scope authorization signed for this lab

If any checkbox is unchecked: **STOP**. Remediate before running.

## Quick Start

```bash
# Full kill chain with 60s inter-step pacing
./run.sh --mode full --delay 60

# Single step (for rule tuning)
./run.sh --mode single_ttp --ttp T1071.001

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
{ "provider": "aws", "modules": ["base", "edr", "ndr", "itdr"], "scenario_id": "SIM-MP-005" }
```

## Package Tree

```
SIM-MP-005/
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
