# SIM-MP-002 — Kerberoast + Pass-the-Hash Lateral Movement — ITDR + EDR + NDR Stitch

**Scenario ID**: `SIM-MP-002`
**Plane**: ANALYTICS (multi-plane: ITDR, EDR, NDR)
**Methodology family**: F2 — Causality Stitching Validation
**Primary KPI**: Cross-Source Correlation Rate
**MOAT tier**: MOAT
**Source YAML**: [`../../mp-002-kerberoast-lateral-smb.yml`](../../mp-002-kerberoast-lateral-smb.yml)

## What This Scenario Proves

This is an F2 (causality-stitching) multi-plane scenario. Coordinated signals
across ITDR, EDR, NDR sharing the join key
`user_principal` must stitch into a **single XSIAM incident** within a 60-second
correlation window. A point product sees only its own plane's alert; only
Cortex XSIAM fuses them into one causality chain — that is the POV moat.

## Kill Chain

| Step | Technique | Plane(s) | Expected signal |
|------|-----------|----------|-----------------|
| step-01 | T1558.003 | ITDR+NDR | ITDR - LDAP search filter (servicePrincipalName=*) from non-typical ho |
| step-02 | T1110.002 | ANALYTICS | XSIAM - MTTD clock: time-gap between Kerberoast (step-01) and lateral  |
| step-03 | T1021.002 | ANALYTICS+EDR+ITDR+NDR | XDR (workstation) - sql-svc account authenticated remotely via SMB fro |
| step-04 | T1003.006 | ANALYTICS+ITDR+NDR | ITDR - DCSync: DRSUAPI GetNCChanges from non-DC source, critical sever |

The XSIAM-stitched incident (see `detections/correlation_rules.xql`) is the
primary POV differentiator.

## Prerequisites

- [ ] Cortex XSIAM tenant with the relevant plane sensors ingesting
      (ITDR, EDR, NDR)
- [ ] Lab target(s) in an isolated segment — not corporate network
- [ ] Clean baseline (zero pre-existing alerts) before execution
- [ ] IaC lab provisioned: `infra_modules_needed: ['base', 'itdr', 'edr', 'ndr']`
- [ ] Legal / scope authorization signed for this lab

If any checkbox is unchecked: **STOP**. Remediate before running.

## Quick Start

```bash
# Full kill chain with 60s inter-step pacing
./run.sh --mode full --delay 60

# Single step (for rule tuning)
./run.sh --mode single_ttp --ttp T1558.003

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
{ "provider": "aws", "modules": ["base", "itdr", "edr", "ndr"], "scenario_id": "SIM-MP-002" }
```

## Package Tree

```
SIM-MP-002/
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
