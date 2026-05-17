# detection_scanner

Canonical corpus of attack vectors, TTPs, and adversarial simulations consumed by the **cortex-pov-engine**. Each entry is a single JSON file, grounded in MITRE ATT&CK, with explicit Cortex / PANW product, IOC, BIOC, use-case, and test-case mappings so the engine can load it directly into the simulation library and produce verifiable POV evidence.

## Three-system architecture

```
+----------------------+    crawls    +-------------------+   reads    +---------------------+
|  threat-intel web    | -----------> |  cortex-scraper   | --------> |  detection_scanner  |
|  (Unit 42, CISA, ..) |              |  (parses + emits) |            |  (this repo)        |
+----------------------+              +-------------------+            +----------+----------+
                                                                                  |
                                                                                  | loads
                                                                                  v
                                                                       +---------------------+
                                                                       |  cortex-pov-engine  |
                                                                       |  (simulator + score)|
                                                                       +---------------------+
```

- **cortex-scraper** crawls the sources defined in `sources/source-registry.json`, parses articles, and drops candidate entries into `ttps/_drafts/` with `status: "draft"`.
- A human reviewer (SE or detection engineer) promotes drafts into `ttps/` with `status: "active"` after enriching the Cortex-specific fields (IOCs, BIOCs, product mapping, use cases/test cases).
- **cortex-pov-engine** loads `ttps/*.json` where `status == "active"` and `metadata.pov_engine.auto_load == true`, runs them against a target environment, and validates outcomes against each entry's `success_criteria`.

## Repository layout

```
detection_scanner/
├── README.md                         # this file
├── schema/
│   └── ttp-entry.schema.json         # JSON Schema 2020-12 — the contract
├── sources/
│   ├── source-registry.json          # indexed threat-intel sources for the scraper
│   └── unit42-index.json             # curated Unit 42 backlog (P0/P1/P2)
└── ttps/
    ├── _drafts/                      # cortex-scraper output, pending review
    ├── TTP-2026-0001-helpdesk-mfa-reset-social-engineering.json
    ├── TTP-2026-0002-lsass-memory-credential-dump.json
    ├── TTP-2026-0003-aws-iam-key-abuse-s3-exfil.json
    ├── TTP-2026-0004-dcsync-credential-replication.json
    ├── TTP-2026-0005-rclone-bulk-exfiltration.json
    └── TTP-2026-0006-esxi-mass-encryption-ansible.json
```

## ID convention

`TTP-YYYY-NNNN` where `YYYY` is the year the entry was created and `NNNN` is a zero-padded monotonic counter within the year. IDs are never reused. The filename appends a slugged title for human readability: `TTP-YYYY-NNNN-<slug>.json`.

Use-case and test-case IDs follow `UC-<DOMAIN>-NNN` and `TC-<DOMAIN>-NNN[A-Z]`. Domains: `IDENT`, `CLOUD`, `RANSOM`, `INSIDER`, `SUPPLY`, `OT`, `EMAIL`, `WEB`.

## Schema in one paragraph

Every TTP has: an `identity` (name + summary), a `mitre_attack` block (matrix + technique IDs + tactic IDs), an `execution` block (the exact payload the engine runs — interpreter, code, prerequisites, expected artifacts, cleanup), a `detections` block (IOCs, BIOCs, XQL queries, correlation rules, analytics modules), a `panw_mapping` block (products with coverage tier + rule IDs + license tier, plus use cases with test cases and per-test success criteria), and `references` (always with one Unit 42 or canonical source marked `primary: true`). `metadata.pov_engine` carries the ingest hints the engine uses to decide *whether*, *where*, and *how* to run the entry. Full contract: [`schema/ttp-entry.schema.json`](schema/ttp-entry.schema.json).

## Cortex-specific extensions

The schema is MITRE-grounded but the value-add for the POV engine lives in four PANW-specific extensions:

- **IOCs** — atomic indicators mapped to Cortex XDR / XSIAM IOC objects, with `cortex_severity_override` for tuning.
- **BIOCs** — Behavioral Indicators of Compromise expressed in Cortex BIOC syntax (`preset = xdr_data | filter ...`). The engine validates these post-simulation by re-running the XQL.
- **panw_mapping.products[]** — one entry per PANW module that participates (`cortex-xdr`, `cortex-xsiam`, `cortex-xsoar`, `cortex-cloud`, `cortex-cdr`, `cortex-asm`, `prisma-cloud`, `prisma-access`, `advanced-wildfire`, `advanced-threat-prevention`, `ngfw-pa-series`, `iot-security`, `ai-access-security`, `ai-runtime-security`), each with `coverage_tier` (prevention/detection/investigation/response/exposure-mgmt), `rule_ids`, `license_required`, and an `evidence_query` the SE can paste into the product UI on demo day.
- **panw_mapping.use_cases[].test_cases[]** — the POV scorecard. Each `test_case` has a per-test `success_criteria[]` (verifiable pass/fail statements) and `expected_score_weight` that the engine sums into the POV outcome.

## Status lifecycle

`draft → active → deprecated → withdrawn`. The pov-engine only loads `active`. `deprecated` entries remain queryable for historical POVs; `withdrawn` are kept for audit only.

## Adding a TTP — by hand

1. Pick the next free `TTP-YYYY-NNNN`.
2. Copy an existing entry as a template; edit fields.
3. Validate locally:
   ```bash
   npx ajv-cli validate -s schema/ttp-entry.schema.json -d "ttps/TTP-YYYY-NNNN-*.json" --spec=draft2020
   # or
   python -m jsonschema -i ttps/TTP-YYYY-NNNN-*.json schema/ttp-entry.schema.json
   ```
4. Ensure `references[]` has exactly one entry with `primary: true`, pointing back to the source.
5. Commit. PR review checks: schema validation, ID uniqueness, MITRE technique resolves, all referenced PANW rule IDs exist (or are explicitly marked `TBD` in `rule_ids`).

## Adding a TTP — via cortex-scraper

1. Add (or confirm) the source in `sources/source-registry.json`.
2. Add the article URL to `sources/<source-id>-index.json` (e.g., `unit42-index.json`) with a priority.
3. Run the scraper (entrypoint contract: reads `sources/`, writes `ttps/_drafts/`).
4. Open the draft in `ttps/_drafts/`. The scraper fills `identity`, `mitre_attack` (best-effort), and `references`. A human fills `execution`, `detections`, `panw_mapping`, and validates.
5. Move the file from `_drafts/` to `ttps/`, flip `status` to `active`, validate, commit.

## Source registry

`sources/source-registry.json` is the canonical scraper input. Sources are tiered:

- **Tier 1** — primary / canonical (Unit 42, MITRE ATT&CK).
- **Tier 2** — vendor threat-intel teams (Mandiant, MSTIC, CrowdStrike, SentinelLabs, Talos, Volexity, ZScaler ThreatLabz, Trend Micro, Sophos X-Ops, Trellix, Securelist, CheckPoint, Proofpoint).
- **Tier 3** — government (CISA, NSA Cyber, NCSC-UK, ACSC, CERT-EU).
- **Tier 4** — IR/community (The DFIR Report, Red Canary, Huntress, SpecterOps, TrustedSec, journalism cross-refs).
- **Tier 5** — execution frameworks (Atomic Red Team, Stratus Red Team, Leonidas, CALDERA, PurpleSharp).

Weights drive prioritization when the scraper queues backlogs.

## What's here today

Six entries — the three POV pillars plus a chained BlackSuit Blitz kill chain (Unit 42's flagship 2025 IR narrative) decomposed into individual TTPs. The chained entries (0002 → 0004 → 0005 → 0006) carry correlation rules that fuse them into a single P1 incident via `CR-RANSOM-0002`, letting the POV engine grade against a real end-to-end scenario rather than isolated tests.

| ID | Pillar | Stage in chain | Anchor (Unit 42) | Primary Cortex module(s) | MITRE |
|---|---|---|---|---|---|
| TTP-2026-0001 | Identity | Initial access (alt path) | Muddled Libra help-desk MFA reset | XSIAM Identity Threat Module + XSOAR | T1556.006 / T1078.004 / T1656 |
| TTP-2026-0002 | Endpoint | Credential access (local) | BlackSuit Blitz — LSASS dump | XDR Credential Theft Protection | T1003.001 |
| TTP-2026-0003 | Cloud | Cloud kill chain (parallel) | IAM Your Defense — leaked key + S3 exfil | Cortex Cloud ITDR + CDR + Prisma Cloud + ASM | T1078.004 / T1580 / T1530 / T1567.002 |
| TTP-2026-0004 | Identity / AD | Credential access (domain) | BlackSuit Blitz — DC compromise | XDR Identity Threat Module + XSIAM | T1003.006 (DCSync) |
| TTP-2026-0005 | Network / Data | Exfiltration | BlackSuit Blitz — 400 GB rclone exfil | CDR + Advanced DNS Security + ATP + NGFW DLP | T1567.002 / T1048.003 / T1074.001 |
| TTP-2026-0006 | Ransomware | Impact | BlackSuit Blitz — Ansible ESXi mass encrypt (~60 hosts) | XDR for Linux + XSIAM cross-source correlation | T1486 / T1490 / T1021.004 / T1059.004 |

**Chained scenarios** the engine can run end-to-end:

- **BlackSuit Blitz full chain** — TTP-2026-0002 (LSASS) → TTP-2026-0004 (DCSync) → TTP-2026-0005 (rclone exfil) → TTP-2026-0006 (ESXi mass encrypt). Correlation rules `CR-CRED-0003`, `CR-EXFIL-0001`, `CR-RANSOM-0001`, and the top-level `CR-RANSOM-0002` are designed to fuse the stages into a single P1 narrative.
- **Identity-led ransomware** — TTP-2026-0001 (help-desk MFA reset) → TTP-2026-0004 → TTP-2026-0005 → TTP-2026-0006. The Muddled Libra variant of the same chain.
- **Cloud-native exfil** — TTP-2026-0003 standalone. Mirrors the AWS-only attack pattern Unit 42's IAM research describes.

Next backlog from `sources/unit42-index.json` P0 set: Phantom Taurus IIS web shell, TeamPCP supply-chain compromise, Payroll Pirates BEC, Boggy Serpens AI-assisted spear-phishing.

## Open contracts (still to lock with cortex-pov-engine)

- Exact loader path: does the engine watch `ttps/*.json` or pull from a manifest?
- BIOC syntax dialect: this corpus assumes XQL-flavored `preset = xdr_data | ...`; confirm against current XSIAM 2.x BIOC grammar.
- Test-case scoring: schema allows `expected_score_weight ∈ [0,1]`; engine should normalize per use case (sum to 1 within a UC).
- Cleanup orchestration: schema declares cleanup payloads; engine should enforce them when `safety_class != safe-by-design`.
