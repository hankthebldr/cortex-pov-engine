# SIM-MP-004 — Sensor Placement & Policy

## Lab Topology

```
┌─ Attacker workstation (DC laptop) ─────────────────────────────┐
│  runs: ./run.sh  (this package)                                │
│  no direct network path to victim — drives victim via SSM/SSH  │
└────────────────────────────────────────────────────────────────┘
                                 │ SSM / SSH
                                 ▼
┌─ Victim VPC (lab AWS account) ─────────────────────────────────┐
│                                                                │
│  EC2 linux-victim  (Ubuntu 22.04)                              │
│    · Cortex XDR agent installed, heartbeating                  │
│    · www-data user (simulated web svc)                         │
│    · aws-cli installed                                         │
│    · scenario scripts run here                                 │
│                                                                │
│  S3: cortexsim-sensitive-demo  tag Sensitivity=High            │
│  S3: cortexsim-attacker-drop   tag AttackerControlled=true     │
│                                                                │
│  CloudTrail: org-level, log-file-validation on, multi-region   │
└────────────────────────────────────────────────────────────────┘
                                 │ CloudTrail
                                 ▼
┌─ Cortex Cloud (CDR) ───────────────────────────────────────────┐
│  account connected via role, CloudTrail ingestion verified     │
│  CSPM scan baseline taken pre-run                              │
└────────────────────────────────────────────────────────────────┘
                                 │ events
                                 ▼
┌─ Cortex XSIAM ─────────────────────────────────────────────────┐
│  XDR + CDR events arriving in Data Lake                        │
│  correlation rules loaded from detections/correlation_rules.xql│
│  XSOAR playbook simmp004-auto-contain imported                 │
└────────────────────────────────────────────────────────────────┘
```

## Cortex XDR Agent Policy

- **Prevention mode**: REPORT-ONLY for this scenario (maximize detection
  visibility; do not block simulated behaviors).
- **Enabled modules**:
  - Behavioral Threat Protection — required for BIOC firing
  - Script Security — catches shell/grep patterns
  - Memory Protection — baseline only; not stressed in MP-004
  - Network Protection — baseline only
- **Policy exclusions**: none. The scenario runs from the authorized lab host.
  Do NOT exclude the attacker workstation IP; the BIOCs under test must see
  the unexcluded behavior.

## Cortex Cloud (CDR) Configuration

- AWS account connected via IAM role (read-only + CloudTrail ingest)
- CloudTrail: multi-region, org-level, log-file-validation enabled
- CSPM baseline scan completed before execution (zero open findings target,
  but preexisting findings don't block the scenario)
- Sensitivity tagging: confirm `Sensitivity=High` tag is present on
  `cortexsim-sensitive-demo` BEFORE running step 4 — the BIOC depends on it

## XSIAM Correlation Ingest

- Load `detections/correlation_rules.xql` via XSIAM rule admin
- Load `detections/bioc_rules.json` BIOCs via XDR rule admin
- Verify the ingestion latency window: CloudTrail typically lands in Data Lake
  within 60–120s of the event. The scenario uses 90s inter-step pacing to
  accommodate this.

## XSOAR

- Import `detections/xsoar_playbook.yml`
- Wire the playbook to trigger on `incident.name matches "SIM-MP-004.*"` or
  on the stitched incident type produced by XSIAM
- Verify the `AWS-IAM` + `XDR` integrations are connected in XSOAR before
  relying on auto-contain actions

## Pre-Run Checklist

- [ ] Clean baseline — zero open incidents touching the victim host/principal
- [ ] VM snapshot taken of victim EC2 at clean state
- [ ] 30 minutes of XDR + CDR telemetry observed post-baseline with no alerts
- [ ] `TARGET_BUCKET` and `ATTACKER_BUCKET` env vars set or defaults match your lab
- [ ] Legal/scope authorization signed for this specific lab account
