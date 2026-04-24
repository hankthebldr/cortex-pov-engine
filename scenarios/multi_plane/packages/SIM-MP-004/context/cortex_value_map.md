# Cortex Value Map — SIM-MP-004

POV findings mapped to Cortex product capabilities. Populate the quantitative
fields after each customer run; the structure below is the reporting template.

## Cortex XDR

**Detected natively**: 2 of 2 endpoint-side techniques
- T1552.001 (AWS key grep) — BIOC
- T1078.004 (aws-cli under www-data) — BIOC

**Evidence capability**:
- Full process causality from web-app parent → shell → grep / aws-cli
- Identity context (effective user = www-data) attached to every event
- Command-line text preserved for forensic replay

**Value line for DC briefing**:
> "Cortex XDR caught both sides of the endpoint story without any custom
> rules — out-of-box BIOCs flagged the credential hunt and the cloud CLI pivot.
> That gives the SOC a 90-second window before the attacker leaves the host."

## Cortex Cloud (CDR)

**Detected natively**: 3 of 3 cloud-side techniques
- T1078.004 (sts:GetCallerIdentity from odd IP) — audit-log detection
- T1580 (multi-service enumeration burst) — UEBA anomaly
- T1530 + T1537 (sensitivity-tagged bucket access + cross-account copy) — CSPM + BIOC

**Value line for DC briefing**:
> "Cortex Cloud turned three separate cloud events into three alerts with
> principal-level context — and the sensitivity-tag correlation meant the
> exfil-stage alert fired on data classification, not just activity volume."

## Cortex XSIAM

**Stitching result**: 5 alerts → 1 incident
- Full timeline spans EDR + CDR, one principal, one story
- XSIAM UEBA score elevated on the shared principal
- "Attack Story" narrative generated end-to-end

**Value line for DC briefing**:
> "Without XSIAM the SOC sees five alerts in two consoles. With XSIAM they
> see one incident with a five-step timeline. That's the difference between
> a 40-minute triage and a 4-minute triage."

## Cortex XSOAR

**Automated response**: the `simmp004-auto-contain` playbook
- Enriches host + principal within 10s of incident creation
- If severity ≥ High: disables leaked IAM key and isolates the endpoint
- Posts full context to `#cortex-pov-soc` Slack channel

**Value line for DC briefing**:
> "Time-to-contain in this scenario is limited by the XSOAR playbook, not by
> SOC analyst availability. In the POV run we saw MTTR of 4m12s from first
> BIOC to IAM key disabled."

## Cortex Xpanse

Not demonstrated in this scenario (out of scope — no external exposure angle).
Recommended follow-on: SIM-MP-005 with an exposed API gateway as the initial
access vector to close the loop between Xpanse discovery and XDR genesis.

## Comparison Points (for competitive briefings)

| Capability                               | Cortex Stack | EDR-only Competitor | CNAPP-only Competitor |
|------------------------------------------|--------------|----------------------|------------------------|
| T1552.001 detection (endpoint)           | ✅ XDR BIOC  | ✅ typical          | ❌ no endpoint         |
| T1078.004 stitch (endpoint ↔ cloud)      | ✅ XSIAM     | ❌ no cloud side    | ❌ no endpoint side    |
| T1530 sensitivity-aware bucket detection | ✅ CDR       | ❌                  | ⚠️  sometimes         |
| Full 5-step kill-chain story             | ✅ XSIAM     | ❌ fragmented       | ❌ fragmented          |
| Auto-contain + IAM key rotation          | ✅ XSOAR     | ⚠️ endpoint only   | ⚠️ cloud only         |

> **POV takeaway**: the integration is the differentiator. Any single tool
> catches pieces. Only the Cortex stack stitches the full hybrid kill chain
> and responds automatically.
