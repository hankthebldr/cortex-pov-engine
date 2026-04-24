# Attack Narrative — SIM-MP-004

## One-Paragraph Exec Summary

A web-facing service on a Linux host is compromised; the attacker, running with
the privileges of the `www-data` service account, sweeps the filesystem for AWS
access keys. They find one, export it into the environment, and pivot to the
victim's cloud tenancy. Within 90 seconds they fingerprint the environment
(EC2, IAM, S3), locate a sensitivity-tagged bucket, enumerate its contents, and
initiate a cross-account copy to an attacker-owned destination. Five distinct
techniques across two detection planes (EDR, CDR) — Cortex XSIAM stitches all
of them into a single incident with a coherent attack timeline.

## Chronological Story (for SOC briefing)

**T+0s · Endpoint — Credential Access**
`www-data` spawns a recursive `grep` for the `AKIA` pattern across `/home`,
`/var/www`, `/opt`. This is unusual: web service accounts don't normally enumerate
filesystems in this way. Cortex XDR BIOC fires on the pattern.

**T+30s · Endpoint → Cloud — Valid Accounts (Cloud)**
Discovered keys are exported into the environment and `aws sts get-caller-identity`
is invoked. Cortex XDR sees the `aws` binary executed under `www-data` (second
BIOC). Cortex Cloud (CDR) sees the corresponding CloudTrail event — same principal,
source IP not on the corporate egress range. **XSIAM stitches these two events**
into a single incident keyed on the principal ARN.

**T+90s · Cloud — Discovery**
`DescribeInstances`, `ListUsers`, `ListBuckets` fire in rapid succession. CDR
UEBA flags the multi-service burst: this principal has never touched IAM before.

**T+180s · Cloud — Collection**
`GetBucketAcl` + `ListObjects` against `cortexsim-sensitive-demo`. The bucket
carries the `Sensitivity=High` tag. CDR BIOC fires on unauthorized-principal
access to a sensitivity-tagged bucket.

**T+270s · Cloud — Exfiltration**
`CopyObject` to `cortexsim-attacker-drop` — a destination outside the source
account. CDR BIOC fires on cross-account copy with sensitivity-tagged source.
XSIAM adds this event to the existing incident: the story is now
**cred-dump → pivot → enum → collection → exfil**, visible as one timeline.

**T+300s · Response (if XSOAR active)**
The auto-contain playbook (`xsoar_playbook.yml`) fires on the stitched incident.
It disables the leaked IAM key via `iam:UpdateAccessKey` → Inactive, isolates the
endpoint via the XDR agent, and posts a Slack notification to `#cortex-pov-soc`.

## Why This Matters for the POV

A customer running **only XDR** would see the BIOCs but miss the cloud story.
A customer running **only CDR** would see the CloudTrail anomalies but miss the
endpoint genesis. A customer running **XDR + CDR without XSIAM** would get five
disconnected alerts in two consoles. Only **XDR + CDR + XSIAM** tells the full
story as a single incident — and only with **XSOAR** does the response happen
automatically within 5 minutes of first alert.

That gap — five alerts vs. one story vs. one contained incident — is the POV
differentiator. Every screenshot captured in `evidence/screenshots/` should
reinforce that hierarchy.
