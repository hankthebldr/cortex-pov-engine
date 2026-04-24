# SIM-MP-004 — APT29 Cloud Credential Theft → Lateral → Exfil

**Scenario ID**: `SIM-MP-004`
**Plane**: ANALYTICS (multi-plane: EDR + CDR + ANALYTICS)
**Threat anchor**: APT29 / Midnight Blizzard (G0016) — cloud-native credential theft and S3 data access patterns
**Difficulty**: intermediate
**Products demonstrated**: Cortex XDR · Cortex XSIAM · Cortex Cloud (CDR) · Cortex XSOAR
**Source YAML**: [`../../mp-004-apt29-cloud-cred-theft.yml`](../../mp-004-apt29-cloud-cred-theft.yml)

## What This Scenario Proves

APT29 shifted heavily toward hybrid cloud TTPs post-2023 — stealing IAM credentials
from compromised endpoints, then pivoting into the victim's cloud tenancy for
reconnaissance, data access, and egress. This scenario validates that Cortex can
**stitch the endpoint and cloud halves into a single incident**, not two
disconnected alerts.

The value narrative: a customer running XDR-only will see the credential grep;
a customer running CDR-only will see the CloudTrail anomaly; only a customer
running **XDR + CDR + XSIAM** sees the full kill chain as one story.

## Kill Chain (5 steps, 6 TIDs)

| # | TID          | Tactic          | Plane(s)  | Expected Signal                                                                 |
|---|--------------|-----------------|-----------|---------------------------------------------------------------------------------|
| 1 | T1552.001    | Credential Access | EDR     | XDR BIOC — recursive grep for `AKIA...` from www-data                          |
| 2 | T1078.004    | Initial Access (Cloud) | EDR + CDR | XDR BIOC (cli invocation) + CDR (sts:GetCallerIdentity from odd src IP)  |
| 3 | T1580        | Discovery       | CDR       | CDR Analytics — multi-service enumeration burst from single principal          |
| 4 | T1530        | Collection      | CDR       | CDR BIOC — sensitivity-tagged bucket access by unauthorized principal          |
| 5 | T1537        | Exfiltration    | CDR + ANALYTICS | CDR BIOC (cross-account S3 copy) + XSIAM stitched-incident story         |

The XSIAM-stitched incident is the primary POV differentiator.

## Prerequisites

Before running this scenario you MUST have:

- [ ] Cortex XDR agent deployed and heartbeating on the Linux victim host
- [ ] Cortex Cloud (CDR) connected to the AWS account in scope, ingesting CloudTrail
- [ ] Endpoint in an isolated lab segment — not corporate network
- [ ] A sacrificial AWS account with:
  - One IAM user with `ReadOnlyAccess` + `s3:GetObject` on `cortexsim-sensitive-demo`
  - S3 bucket `cortexsim-sensitive-demo` tagged `Sensitivity=High`
  - S3 bucket `cortexsim-attacker-drop` in a separate account (or same account with
    `AttackerControlled=true` tag) — this is the exfil destination
  - CloudTrail enabled with `LogFileValidation=true` in the source account
- [ ] Snapshot of the victim VM at a clean baseline state
- [ ] Zero pre-existing alerts in XDR and CDR consoles (baseline clean)
- [ ] 30 minutes of agent heartbeat confirmed before execution
- [ ] Legal / scope authorization signed for this lab

If any checkbox is unchecked: **STOP**. Remediate before running.

## Quick Start

```bash
# Full kill chain with 90s inter-step pacing
./run.sh --mode full --delay 90

# Single TTP (for rule tuning)
./run.sh --mode single_ttp --ttp T1552.001

# Dry-run (print what would execute, no side effects)
./run.sh --mode full --dry-run

# Cleanup (unset env vars, remove staged artifacts)
./run.sh --cleanup
```

Containerized mode:

```bash
docker compose up --build attacker c2server scenario-ui
# Then open http://localhost:8080 for live detection feed
```

## IaC Prerequisites

This scenario declares `infra_modules_needed: [base, edr, cdr, tim]` in its YAML.
Use the CortexSim IaC generator to stand up the supporting lab:

```
POST /api/infra/generate
{
  "provider": "aws",
  "modules": ["base", "edr", "cdr", "tim"],
  "scenario_id": "SIM-MP-004"
}
```

Download the bundle, run `terraform apply`, and the victim EC2 + CDR-instrumented
account + TIM TAXII feed are provisioned.

## Package Tree

```
SIM-MP-004/
├── README.md                       ← this file
├── run.sh                          ← single-entry runner
├── docker-compose.yml              ← containerized runner
├── architecture/
│   └── sensor-placement.md        ← XDR agent policy, CDR ingestion config
├── ttps/
│   ├── T1552.001_cred_discovery.sh
│   ├── T1078.004_cloud_pivot.sh
│   ├── T1580_cloud_enum.sh
│   ├── T1530_bucket_access.sh
│   └── T1537_s3_exfil.sh
├── c2/
│   └── sliver-profile.json         ← HTTPS C2 profile (if upgrading to C2-driven variant)
├── detections/
│   ├── bioc_rules.json             ← custom XDR BIOC rules for coverage gaps
│   ├── correlation_rules.xql       ← XSIAM stitching rule
│   ├── ioc_list.csv                ← IOCs this scenario generates
│   └── xsoar_playbook.yml          ← auto-containment playbook
├── evidence/
│   ├── detection_scorecard.csv     ← populated after each run
│   └── screenshots/                ← incident view, causality, timeline
└── context/
    ├── threat_actor_profile.md     ← APT29 threat brief
    ├── attack_narrative.md         ← exec-ready kill-chain story
    └── cortex_value_map.md         ← POV value findings per product
```

## Execution Context (filled)

```
scenario_name:         APT29 Cloud Credential Theft → Lateral → Exfil
threat_actor_anchor:   APT29 / G0016 (Midnight Blizzard)
target_environment:    hybrid (endpoint + cloud_aws)
cortex_products:       xdr · xsiam · cdr · xsoar
lab_hypervisor:        cloud_vms (AWS EC2 victim) + AWS account for CDR side
os_targets:            ["ubuntu_22"]
network_sensor:        none (cloud-native detection focus)
agent_version:         latest stable XDR Linux agent
attacker_c2_profile:   n/a (non-C2 path; upgradeable to sliver for variant-B)
packaging_mode:        single_script + docker
difficulty:            intermediate
things3_project:       <set at run time>
```

## Post-Execution Deliverables

After a clean run the DC should have:

1. Populated `evidence/detection_scorecard.csv` — coverage %
2. Exported XSIAM incident JSON in `evidence/incident_export.json`
3. 3× screenshots in `evidence/screenshots/` (incident view, causality graph, timeline)
4. Any MISSED TIDs covered by a new rule in `detections/bioc_rules.json`
5. ATT&CK Navigator layer JSON regenerated (see `detections/README.md` for cmd)
