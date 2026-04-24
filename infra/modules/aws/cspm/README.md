---
name: cspm
description: Intentionally misconfigured AWS resources (S3, IAM, SG, EBS, CloudTrail) for Cortex Cloud CSPM validation. Every resource is a planted finding that Cortex CSPM should surface.
providers: [aws]
required_params: [project_name]
optional_params: []
dependencies: [base]
---

# cspm (AWS)

Provisions a curated set of **intentionally misconfigured** AWS resources that Cortex Cloud CSPM (and any CSPM tool) should flag as policy violations or compliance findings. This is the POV equivalent of CloudGoat — a safe, scoped environment where DCs can prove that Cortex's cloud posture detection actually catches real-world misconfigurations.

## Findings planted

Every resource is tagged with `CortexSimCSPMFinding=<finding-type>` so the DC can cross-reference what Cortex surfaced against what was intentionally deployed.

| Category | Finding | Resource |
|----------|---------|----------|
| **S3** | Public-read ACL | `*-cspm-public-*` bucket |
| **S3** | Versioning disabled | `*-cspm-unversioned-*` bucket |
| **S3** | No customer-managed KMS | `*-cspm-no-kms-*` bucket |
| **Security Group** | SSH open to 0.0.0.0/0 | `*-cspm-ssh-open` SG |
| **Security Group** | DB ports (3306, 5432, 6379) open to 0.0.0.0/0 | `*-cspm-db-open` SG |
| **IAM** | Role with `AdministratorAccess` managed policy | `*-cspm-admin-role` |
| **IAM** | User with inline `iam:*` wildcard policy | `*-cspm-overprivileged-user` |
| **EBS** | Unencrypted orphaned volume | `*-cspm-unencrypted-vol` |
| **CloudTrail** | Log file validation disabled, not multi-region, no global events | `*-cspm-trail` |

## What this does NOT include

- No exposed credentials or secrets (safe)
- No running compute exposing actual data (just config)
- No actual customer data in public buckets (dummy `README.txt` only)
- Total cost: near-zero (S3 lifecycle small, IAM/SG free, 1 GB EBS ~$0.10/month)

## Content installed

Cloud attack labs: CloudGoat, AWSDetonationLab, Stratus Red Team, Leonidas, endgame, aurelian.
Agentless scanning: OpenClarity, ThreatMapper, Trivy, Grype.
IaC/Lambda scanners: LambdaGuard.

The DC can run these from the jumpbox against the CSPM-planted resources to validate that Cortex Cloud detects both the **config state** (static CSPM) and the **active exploitation** (runtime detection).

## Validation workflow

1. Apply the bundle. Cortex Cloud CSPM should surface all planted findings within its scan interval (typically 15-60 min).
2. Compare Cortex findings list against the `findings_summary` output from this module.
3. Run one or two Stratus Red Team attacks against the planted resources to trigger runtime detections.
4. Verify XSIAM incident includes both CSPM finding + runtime attack.
5. `terraform destroy` — all resources are ephemeral and destroy cleanly.

## Security notes

This module creates **real misconfigurations**. Do not deploy into production accounts. Use a dedicated POV account, and set a Torque TTL of 24-72h max. The `force_destroy = true` flag on every S3 bucket ensures clean teardown.
