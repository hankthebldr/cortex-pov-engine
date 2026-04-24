# Threat Actor Profile — APT29 / Midnight Blizzard

**MITRE Group ID**: [G0016](https://attack.mitre.org/groups/G0016/)
**Aliases**: Cozy Bear · Nobelium · Midnight Blizzard · The Dukes · UNC2452
**Attribution**: Russian Foreign Intelligence Service (SVR)
**Motivation**: Strategic espionage — government, defense, think tanks, IT supply chain

## Why This Scenario Fits APT29

APT29 shifted its center of gravity from on-prem to cloud identity post-2021
(SolarWinds supply-chain compromise, Microsoft corporate tenant intrusion 2024).
Their modern TTP fingerprint overweights:

- **Credential theft from compromised hosts** — files, browser stores, token caches
- **Direct cloud API abuse** using stolen IAM keys or OAuth refresh tokens
- **Low-and-slow cloud enumeration** — discovery burst then quiet collection
- **Living-off-the-cloud data access** — native AWS/Azure APIs, not custom tooling
- **Data staging and exfil via cloud-native paths** — S3 cross-account, Azure Blob

## Referenced Campaigns

- **Midnight Blizzard Microsoft tenant intrusion (Jan 2024)** — password-spray on
  legacy tenant → OAuth app persistence → mailbox access to senior leadership
- **SolarWinds / SUNBURST follow-on cloud TTPs (2020–2021)** — post-implant,
  pivoted to Microsoft 365 + Azure AD via Golden SAML and service principal abuse
- **Unit 42 analysis, APT29 cloud TTPs (ongoing)** — emphasizes AWS/Azure pivots
  with minimal custom tooling to blend into legitimate admin traffic

## What We Emulate in SIM-MP-004

This scenario emulates the **mid-kill-chain segment** of a realistic APT29
operation: the hybrid pivot from a compromised Linux endpoint (web app server)
into the victim's AWS tenancy, discovery, and data egress.

We do **not** emulate:

- Initial access (assumed — web-app RCE or supply-chain implant)
- Custom malware / C2 implant (APT29's cloud TTPs often skip this entirely)
- Email-tier TTPs (OAuth persistence, mailbox access) — out of scope for AWS

## Pivot Points for Variant Scenarios

- **SIM-MP-004-B (C2 variant)** — add sliver HTTPS beacon as the endpoint
  remote-access layer; tests XDR network-module + memory protection
- **SIM-MP-004-C (Azure variant)** — rewrite steps 2–5 against Azure ARM +
  Microsoft Graph; tests CDR's Azure connector parity
- **SIM-MP-004-D (evasive variant)** — add AWS CLI via odd paths, base64-encoded
  env, session-token laundering through `sts:AssumeRole` to test BIOC coverage
