# Detection Plane Coverage Reference

> **Scope.** This document is the canonical, exhaustive inventory of every detection
> plane in CortexSim — synthesized across `scenarios/`, `detection_scanner/ttps/`,
> `core/planes/*.py`, `tools/packs/`, `core/eal_simulator/plugins/`, and
> `infra/modules/aws/`. It is intentionally verbose: a lot of this surface grew fast
> and was never canonically notated. Every item is enumerated (nothing sampled).
>
> **Generated:** 2026-06-07 · **Repo state:** branch `main` @ `b7eebc5` ·
> **Cross-refs:** scenario IDs (`SIM-<PLANE>-NNN`), TTP card IDs (`TTP-2026-NNNN`),
> adapter IDs (`TOOL-*`), EAL plugin names, IaC module names.

---

## How to read this document

CortexSim a detection plane is realized across **five independent artifact layers**.
A plane is only as strong as its weakest populated layer:

| Layer | Where it lives | What it is |
|-------|----------------|------------|
| **Scenario YAML** | `scenarios/<plane>/` | Source-of-truth playbook: steps, identity harness, expected detections, cleanup. Loaded into DB at boot. |
| **TTP card (JSON)** | `detection_scanner/ttps/TTP-2026-NNNN-*.json` | The portable detection-engineering artifact: real BIOC/XQL/IOC/correlation logic, MITRE map, PANW product mapping, UC/TC, remediation. Exported to Sigma/XQL/correlation/XSOAR. |
| **EAL plugin (Python)** | `core/eal_simulator/plugins/*.py` | The Emulated Attack Layer executor that actually generates the network/identity/API signal. |
| **Tool adapter (YAML)** | `tools/packs/<tool>.yml` | Declarative wrapper around a real external tool; scenarios reference by `adapter_ref: TOOL-*`. |
| **IaC module (Terraform)** | `infra/modules/aws/<module>/` | Provisions the lab topology / planted targets the plane needs. |
| **Plane descriptor (Python)** | `core/planes/<plane>.py` | **Stub only** — capability descriptor dataclass. Phase 2 logic is NOT built. |

> ⚠️ **The `core/planes/*.py` modules are all stubs.** Each is a `@dataclass`
> capability descriptor (name, engine, key techniques, description) with a header
> comment `Phase 2 adds full logic; this module exports the plane identity...`.
> They carry **no detection or execution logic**. Only **6 of the 13 active planes**
> even have a descriptor file: `edr.py`, `cdr.py`, `ndr.py`, `itdr.py`,
> `cloud_app.py`, `analytics.py`. The other 7 planes (AI_ACCESS, AIRS, BROWSER, KOI,
> AI_SPM, plus CSPM/ASM/TIM which are IaC-only) have **no descriptor module at all**.

---

## Repository-wide totals (ground truth, counted not claimed)

| Metric | Count | Notes |
|--------|-------|-------|
| Scenario families (dirs under `scenarios/`) | 11 | `ai_access`, `ai_spm`, `airs`, `browser`, `cdr`, `cloud_app`, `edr`, `itdr`, `koi`, `multi_plane`, `ndr` |
| Distinct `plane:` values in scenario YAML | 13 | AI_ACCESS, AI_SPM, AIRS, BROWSER, CDR, CLOUD_APP, EDR, ITDR, KOI, NDR, ANALYTICS (multi_plane), + the IaC-only CSPM/ASM/TIM have no scenarios |
| Launchable scenario YAML (excl. probes/campaigns/packages) | **65** | aiacc 5, aispm 6, airs 5, browser 5, cdr 5, cloud_app 5, edr 5, itdr 5, koi 5, multi_plane 5, ndr 7 + 2 multi_plane package YAML + 10 airs probes + 5 browser campaigns |
| TTP detection cards | **63** | all `status: active`; numbered `TTP-2026-0001`..`0063` |
| TTP cards that are bespoke (not auto-derived from a `SIM-*` scenario) | 6 | `TTP-2026-0001`..`0006` (helpdesk MFA, LSASS dump, AWS IAM/S3 exfil, DCSync, rclone exfil, ESXi mass-encrypt) |
| EAL plugins | **14** | see plugin table below |
| Tool adapter packs | **69** | (70 `.yml` files minus `_schema.yml`) — CLAUDE.md "69 packs" ✅ matches |
| Scenarios wired to an adapter via `adapter_ref` | **27** | matches CLAUDE.md "27 scenarios wired" ✅ |
| IaC modules (AWS) | **11** | `base`, `edr`, `cdr`, `content-library`, `itdr`, `ndr`, `cspm`, `asm`, `tim`, `telemetry-replay`, `ai-spm` — CLAUDE.md claims "10 modules" ❌ (ai-spm makes 11; see gap PLANE-DOC-AISPM) |

### EAL plugin → plane map (all 14)

| Plugin | File | Plane(s) served | Scenario uses |
|--------|------|-----------------|---------------|
| `idp_signin_emulator` | `idp_signin_emulator.py` | ITDR | 5 (SIM-ITDR-001..005) |
| `oauth_grant_emulator` | `oauth_grant_emulator.py` | CLOUD_APP | 5 (SIM-CLOUD-001..005) |
| `llm_provider_egress` | `llm_provider_egress.py` | AI_ACCESS | 5 (SIM-AIACC-001..005) |
| `airs_prompt_attack` | `airs_prompt_attack.py` | AIRS | **0 scenario YAML refs** — see gap PLANE-AIRS-PLUGIN |
| `browser_attack_runner` | `browser_attack_runner.py` | BROWSER | 5 (SIM-BROWSER-001..005) |
| `agentic_egress` | `agentic_egress.py` | KOI | 5 (SIM-KOI-001..005) |
| `c2_http_beacon` | `c2_http_beacon.py` | NDR | 1 (SIM-NDR-001) |
| `dns_tunnel_exfil` | `dns_tunnel_exfil.py` | NDR | 1 (SIM-NDR-002) |
| `stratum_tcp_connect` | `stratum_tcp_connect.py` | NDR | 1 (SIM-NDR-003) |
| `smb_rpc_sweep` | `smb_rpc_sweep.py` | NDR | 1 (SIM-NDR-004) |
| `bulk_https_exfil` | `bulk_https_exfil.py` | NDR | 1 (SIM-NDR-005) |
| `ftp_egress` | `ftp_egress.py` | NDR | 1 (SIM-NDR-006) |
| `ssh_egress` | `ssh_egress.py` | NDR | 1 (SIM-NDR-007) |

> Note: the `idp_signin_emulator`, `oauth_grant_emulator`, `llm_provider_egress`,
> `agentic_egress`, `browser_attack_runner` plugin grep returns 6 scenario hits each
> because each plugin name also appears once in its plane's `README.md`. The actual
> scenario-YAML count is 5 each.

### Tool adapter packs by plane (`cortex_signal.planes`)

| Plane tag | # adapters | Adapter IDs |
|-----------|-----------|-------------|
| NDR | 16 | CMSEEK, COMMIX, FEROXBUSTER, FRP, GOBUSTER, MASSCAN, NIKTO, NMAP, NUCLEI, RECON-NG, SCAPY, SECLISTS, SLIVER, SQLMAP, TSHARK, WHATWEB |
| EDR | 14 | APTSIMULATOR, ATOMIC-RED-TEAM, BLOODHOUND, CALDERA, CHAIN-REACTOR, EMPIRE, HAVOC, MIMIKATZ, PURPLESHARP, PYPYKATZ, RUBEUS, SCYTHE-COMPOUND-ACTIONS, SLIVER, STARKILLER |
| ITDR | 14 | BLOODHOUND, BLOODYAD, CREDKING, CROSSLINKED, EVILGINX2, IMPACKET, KRBRELAYUP, MIMIKATZ, PHISHERY, PRINTSPOOFER, PYPYKATZ, RUBEUS, SET, TOKENVATOR |
| CDR | 12 | CLOUDSPLAINING, DEEPCE, GITGOT, GITLEAKS, KUBE-BENCH, KUBESCAPE, NUCLEI, PACU, PROWLER, SCOUTSUITE, SKYARK, TRIVY |
| CLOUD_APP | 5 | EVILGINX2, GOPHISH, PACU, PROWLER, SQLMAP |
| BROWSER | 4 | CORTEX-BROWSER-ATTACKER, EVILGINX2, GOPHISH, PAYLOADSALLTHETHINGS |
| KOI | 2 | CORTEX-AGENTIC-PACK, YARA |
| AIRS | 1 | CORTEX-PROMPT-ATTACKER |
| ANALYTICS | 1 | VT-CLI |
| AI_ACCESS | **0** | — none mapped (see gap PLANE-ADAPTER-AIACC) |
| AI_SPM | **0** | — none mapped (see gap PLANE-ADAPTER-AISPM) |
| CSPM / ASM / TIM | 0 | IaC-only planes; tools land via cloud scans, not adapters |

---

# Per-plane sections

Each section: **Cortex engine · status · # scenarios · # TTP cards · EAL plugin(s) ·
adapters · IaC module · mature-vs-thin assessment.** Sub-items enumerated in full.

---

## 1. CDR — Cloud Detection & Response

| Field | Value |
|-------|-------|
| **Cortex engine** | Cortex Cloud / Prisma Cloud Compute (runtime container defense) |
| **Status** | Active — 5 scenarios |
| **# scenarios** | 5 |
| **# TTP cards** | 5 (`TTP-2026-0022`..`0026`) |
| **EAL plugin(s)** | None — executes real container/K8s commands on a live cluster via the agent/push bundle |
| **Adapters mapped to plane** | 12 (CLOUDSPLAINING, DEEPCE, GITGOT, GITLEAKS, KUBE-BENCH, KUBESCAPE, NUCLEI, PACU, PROWLER, SCOUTSUITE, SKYARK, TRIVY) |
| **Adapters actually wired** | 1 — `cdr-001` → `TOOL-DEEPCE` |
| **IaC module** | `infra/modules/aws/cdr` (EKS target) |
| **Plane descriptor** | `core/planes/cdr.py` (stub) |

**Scenarios (all enumerated):**

| Scenario file | ID | TTP card | Notes |
|---|---|---|---|
| `cdr/cdr-001-container-enum.yml` | SIM-CDR-001 | TTP-2026-0022 | container enumeration; wired to TOOL-DEEPCE |
| `cdr/cdr-002-cryptominer.yml` | SIM-CDR-002 | TTP-2026-0023 | cryptominer in container |
| `cdr/cdr-003-container-escape.yml` | SIM-CDR-003 | TTP-2026-0024 | container escape |
| `cdr/cdr-004-k8s-lateral.yml` | SIM-CDR-004 | TTP-2026-0025 | K8s lateral movement |
| `cdr/cdr-005-wildfire-trigger.yml` | SIM-CDR-005 | TTP-2026-0026 | WildFire detonation trigger |

**Detection depth (BIOC / XQL / IOC / correlation per card):**
0022 → 9/3/2/1 · 0023 → 8/3/2/1 · 0024 → 10/3/1/1 · 0025 → 10/3/0/1 · 0026 → 8/3/2/1.

**Mature:** *Richest BIOC coverage of any plane* (8–10 BIOCs/card — far above the
plane average of ~2). Real container/K8s runtime telemetry. Dedicated EKS IaC module.
12 adapters available.

**Thin:** Only 1 of 5 scenarios is actually adapter-wired (the other 4 hand-roll CLI,
which contradicts the "scenarios reference adapters" design rule). No EAL plugin — relies
on a live cluster, so push-bundle dry-run value is limited. XQL count is flat at 3/card.

---

## 2. EDR — Endpoint Detection & Response

| Field | Value |
|-------|-------|
| **Cortex engine** | Cortex XDR Agent (Linux) |
| **Status** | Active — 5 scenarios |
| **# scenarios** | 5 |
| **# TTP cards** | 5 (`TTP-2026-0032`..`0036`) |
| **EAL plugin(s)** | None — executes real Linux post-ex commands under the identity harness |
| **Adapters mapped to plane** | 14 |
| **Adapters actually wired** | All 5 scenarios → `TOOL-ATOMIC-RED-TEAM`; edr-001 also → TOOL-MIMIKATZ; edr-005 also → TOOL-RUBEUS/BLOODHOUND |
| **IaC module** | `infra/modules/aws/edr` (diverse Linux targets) |
| **Plane descriptor** | `core/planes/edr.py` (stub) |

**Scenarios:**

| Scenario | ID | TTP card | BIOC/XQL/IOC/corr |
|---|---|---|---|
| `edr/edr-001-credential-dumping.yml` | SIM-EDR-001 | TTP-2026-0032 | 4/5/3/1 |
| `edr/edr-002-reverse-shell.yml` | SIM-EDR-002 | TTP-2026-0033 | 6/4/0/1 |
| `edr/edr-003-persistence-mechanisms.yml` | SIM-EDR-003 | TTP-2026-0034 | 5/5/0/1 |
| `edr/edr-004-defense-evasion.yml` | SIM-EDR-004 | TTP-2026-0035 | 5/5/0/1 |
| `edr/edr-005-lateral-movement.yml` | SIM-EDR-005 | TTP-2026-0036 | 3/5/0/1 |

**Mature:** Second-richest detection content (5–6 BIOCs, 4–5 XQL each). EDR-001 is the
reference-quality card (real `xdr_data` BIOCs against `/etc/shadow`, `/proc/*/environ`,
Mimipenguin; IOC URL+SHA256; correlation `CR-EDR-0001`; full UC/TC `UC-RANSOM-032`).
All 5 scenarios adapter-wired (Atomic Red Team). Identity-harness causality chains
(`www-data`, etc.) are the design's whole point and this plane exercises them.

**Thin:** Only EDR-001 carries IOCs; the other 4 have 0. Single correlation rule per
card (no cross-card stitching except in multi_plane). Linux-only — no Windows endpoint
scenarios despite a Windows-heavy ITDR IaC lab existing.

---

## 3. NDR — Network Detection & Response

| Field | Value |
|-------|-------|
| **Cortex engine** | Network Security / Firewall Analytics (NGFW → XSIAM stitching) |
| **Status** | Active — 7 scenarios (the broadest scenario count) |
| **# scenarios** | 7 |
| **# TTP cards** | **6** — ⚠️ SIM-NDR-005 has NO card (see gap) |
| **EAL plugin(s)** | 7 — one per protocol (`c2_http_beacon`, `dns_tunnel_exfil`, `stratum_tcp_connect`, `smb_rpc_sweep`, `bulk_https_exfil`, `ftp_egress`, `ssh_egress`) |
| **Adapters mapped to plane** | 16 (the most of any plane) |
| **Adapters actually wired** | 1 — `ndr-004` → `TOOL-MASSCAN` (+SCAPY) |
| **IaC module** | `infra/modules/aws/ndr` (3 stitching patterns: marketplace_vmseries, external_ngfw_forward, suricata_lab) |
| **Plane descriptor** | `core/planes/ndr.py` (stub) |

**Scenarios + plugin + card:**

| Scenario | ID | EAL plugin | TTP card | B/X/I/C |
|---|---|---|---|---|
| `ndr/ndr-001-c2-beacon-eal-validation.yml` | SIM-NDR-001 | c2_http_beacon | TTP-2026-0050 | 1/4/0/1 |
| `ndr/ndr-002-dns-tunnel-eal.yml` | SIM-NDR-002 | dns_tunnel_exfil | TTP-2026-0051 | 1/5/0/1 |
| `ndr/ndr-003-cryptojacking-stratum.yml` | SIM-NDR-003 | stratum_tcp_connect | TTP-2026-0052 | 1/2/1/1 |
| `ndr/ndr-004-smb-lateral-sweep.yml` | SIM-NDR-004 | smb_rpc_sweep | TTP-2026-0053 | 1/3/0/1 |
| `ndr/ndr-005-bulk-https-exfil.yml` | SIM-NDR-005 | bulk_https_exfil | **MISSING** | — |
| `ndr/ndr-006-ftp-cleartext-egress.yml` | SIM-NDR-006 | ftp_egress | TTP-2026-0061 | 1/3/1/1 |
| `ndr/ndr-007-ssh-outbound-egress.yml` | SIM-NDR-007 | ssh_egress | TTP-2026-0062 | 1/3/1/1 |

**Mature:** Best **EAL plugin** coverage — 7 dedicated, protocol-accurate signal
generators with safety gating. Most adapters (16) and the only plane with a 3-mode
stitching IaC module. Feeds the multi-plane stitching scenarios (MP-001/003).

**Thin:** Detection cards are shallow — **exactly 1 BIOC each** and only 3 of 7 carry
an IOC. SIM-NDR-005 ships an EAL plugin + scenario but **no TTP card at all** — the
detection-engineering artifact is missing for a live scenario. Only 1 of 16 adapters
wired. NDR detection logic leans on XQL (the BIOC layer is underbuilt).

---

## 4. ITDR — Identity Threat Detection & Response

| Field | Value |
|-------|-------|
| **Cortex engine** | Cortex ITDR |
| **Status** | Active — 5 scenarios |
| **# scenarios** | 5 |
| **# TTP cards** | 5 (`TTP-2026-0037`..`0041`) |
| **EAL plugin(s)** | `idp_signin_emulator` (synthetic IdP audit-log emission — Phase 9) |
| **Adapters mapped to plane** | 14 |
| **Adapters actually wired** | 1 — `mp-002` (a multi_plane scenario) uses ITDR adapters (RUBEUS/BLOODHOUND/IMPACKET); the 5 SIM-ITDR scenarios are EAL-driven, not adapter-wired |
| **IaC module** | `infra/modules/aws/itdr` (Windows AD lab: DC auto-promote, 50 users, 5 Kerberoastable SPNs, 1 AS-REP-roastable DA, SharpHound/Mimikatz/Impacket/Rubeus/Certipy content) |
| **Plane descriptor** | `core/planes/itdr.py` (stub) |

**Scenarios:**

| Scenario | ID | TTP card | B/X/I/C |
|---|---|---|---|
| `itdr/sim-itdr-001-impossible-travel.yml` | SIM-ITDR-001 | TTP-2026-0037 | 1/3/0/1 |
| `itdr/sim-itdr-002-mfa-fatigue.yml` | SIM-ITDR-002 | TTP-2026-0038 | 2/2/0/1 |
| `itdr/sim-itdr-003-credential-stuffing.yml` | SIM-ITDR-003 | TTP-2026-0039 | 1/3/0/1 |
| `itdr/sim-itdr-004-token-replay.yml` | SIM-ITDR-004 | TTP-2026-0040 | 2/3/0/1 |
| `itdr/sim-itdr-005-brute-force-lockout.yml` | SIM-ITDR-005 | TTP-2026-0041 | 2/3/0/1 |

**Mature:** Cleanest single-purpose EAL plugin (`idp_signin_emulator` emits synthetic
Google/IdP audit-log JSON — no real auth). The **richest IaC module in the repo**
(full Windows AD forest with seeded roastable accounts + an entire credential-attack
toolchain). The on-host adversary tools (Rubeus, BloodHound, Impacket) are exercised by
the multi_plane Kerberoast chain (MP-002 / TTP-2026-0063).

**Thin:** Cards are shallow (1–2 BIOC, 0 IOC). The IdP emulator covers a single IdP
shape (Google-style) — Okta/Entra audit-log shapes are not emitted by ITDR (those live
in Cloud App's OAuth emulator instead). No correlation across the 5 identity scenarios.

---

## 5. CSPM — Cloud Security Posture Management

| Field | Value |
|-------|-------|
| **Cortex engine** | Cortex Cloud Posture Management |
| **Status** | **IaC-only** — no scenarios, no TTP cards, no EAL plugin |
| **# scenarios** | 0 |
| **# TTP cards** | 0 |
| **EAL plugin(s)** | None |
| **Adapters mapped to plane** | 0 (CSPM tools land via the CDR `cloud-container` category adapters: PROWLER, SCOUTSUITE, CLOUDSPLAINING, etc., but none carry a `CSPM` plane tag) |
| **IaC module** | `infra/modules/aws/cspm` — plants **9 findings**: public S3, unversioned S3, no-KMS S3, SG SSH-to-world, SG DB-ports-to-world (3306/5432/6379), IAM role `AdministratorAccess`, IAM user wildcard `iam:*`, unencrypted EBS, weak CloudTrail. Tagged `CortexSimCSPMFinding=<type>`. |
| **Plane descriptor** | None |

**Mature:** Well-specified planted-finding IaC module (CloudGoat-style), cross-referenceable via tags.

**Thin:** No scenario, no detection card, no plugin. Validation is "deploy module, see if
Cortex CSPM surfaces the 9 findings" — entirely manual, no scored TTP artifact. A POV
running CSPM has nothing in the scenario browser to launch.

---

## 6. ASM — Attack Surface Management

| Field | Value |
|-------|-------|
| **Cortex engine** | Cortex Attack Surface Management |
| **Status** | **IaC-only** |
| **# scenarios** | 0 |
| **# TTP cards** | 0 |
| **EAL plugin(s)** | None |
| **Adapters mapped to plane** | 0 (recon adapters NMAP/MASSCAN/NIKTO carry NDR/web-app tags, not ASM) |
| **IaC module** | `infra/modules/aws/asm` — public EC2 (nginx dir-listing + bait files), weak TLS (self-signed RSA-1024), SSH on 2222 password-auth, Redis 6379 no-auth, fake Elasticsearch banner 9200, gocortexbrokenbank on 9001, + public-website S3 bucket. |
| **Plane descriptor** | None |

**Mature:** Realistic multi-service exposed host for surface discovery validation.

**Thin:** Same as CSPM — no scenario/card/plugin; purely "deploy and inspect" with no
scored detection artifact and nothing launchable in the UI.

---

## 7. TIM — Threat Intel Management

| Field | Value |
|-------|-------|
| **Cortex engine** | Cortex Threat Intel Management |
| **Status** | **IaC-only** |
| **# scenarios** | 0 |
| **# TTP cards** | 0 |
| **EAL plugin(s)** | None |
| **Adapters mapped to plane** | 0 (the IOC/sandbox adapter VT-CLI is tagged `ANALYTICS`, not TIM) |
| **IaC module** | `infra/modules/aws/tim` — mocktaxii (STIX/TAXII 2.1) + fake C2 HTTP endpoint + Route53 private zone w/ 5 IOC subdomains (`c2-beacon`, `exfil-drop`, `payload-delivery`, `dga-1a2b3c`, `cryptominer-pool`) resolving to the fake C2. Produces both the feed AND matching traffic for stitched IOC+NDR+EDR detection. |
| **Plane descriptor** | None |

**Mature:** Clever self-consistent design (the feed and the matching traffic come from
the same module → stitched-incident validation). Uses the in-tree mocktaxii submodule.

**Thin:** No scenario/card/plugin. The "stitched IOC+NDR+EDR" promise is only realized
if a separate NDR/EDR scenario is run against the planted domains — no card ties them.

---

## 8. Cloud App — Cloud App Security (SaaS / OAuth)

| Field | Value |
|-------|-------|
| **Cortex engine** | Cortex Cloud App Security |
| **Status** | Active — 5 scenarios (Phase 9) |
| **# scenarios** | 5 |
| **# TTP cards** | 5 (`TTP-2026-0027`..`0031`) |
| **EAL plugin(s)** | `oauth_grant_emulator` (outbound OAuth 2.0 authorize requests to Okta/Microsoft/Google w/ planted risky scopes; no real client secrets) |
| **Adapters mapped to plane** | 5 (EVILGINX2, GOPHISH, PACU, PROWLER, SQLMAP) — none wired to the SIM-CLOUD scenarios |
| **IaC module** | None |
| **Plane descriptor** | `core/planes/cloud_app.py` (stub) |

**Scenarios:**

| Scenario | ID | TTP card | B/X/I/C |
|---|---|---|---|
| `cloud_app/sim-cloud-001-okta-risky-drive-grant.yml` | SIM-CLOUD-001 | TTP-2026-0027 | 1/3/0/1 |
| `cloud_app/sim-cloud-002-microsoft-admin-consent.yml` | SIM-CLOUD-002 | TTP-2026-0028 | 2/3/0/1 |
| `cloud_app/sim-cloud-003-google-mailbox-takeover.yml` | SIM-CLOUD-003 | TTP-2026-0029 | 1/4/0/1 |
| `cloud_app/sim-cloud-004-cross-provider-grant-rotation.yml` | SIM-CLOUD-004 | TTP-2026-0030 | **0**/5/0/1 |
| `cloud_app/sim-cloud-005-benign-baseline-control.yml` | SIM-CLOUD-005 | TTP-2026-0031 | **0**/4/0/1 |

**Mature:** Multi-IdP OAuth emulator (Okta + Microsoft + Google) is genuinely
cross-provider. SIM-CLOUD-005 is a thoughtful **benign-baseline control** (false-positive
hygiene). Clean Phase-9 safety story (no real secrets).

**Thin:** XQL-only detection (cards 004/005 have **zero BIOCs** — pure analytics). Zero
IOCs across the plane. No IaC module — relies entirely on outbound to real IdP authorize
endpoints. The 5 plane-tagged adapters are phishing/cloud-recon tools unrelated to the
OAuth scenarios (not actually wired).

---

## 9. Analytics — XSIAM Correlation Engine (multi-plane stitching)

| Field | Value |
|-------|-------|
| **Cortex engine** | XSIAM Correlation Engine |
| **Status** | Active — 5 multi-plane scenarios (`plane: ANALYTICS`) |
| **# scenarios** | 5 (the `scenarios/multi_plane/` family) + 2 packaged YAML under `SIM-MP-004` |
| **# TTP cards** | 5 (`TTP-2026-0047` MP-001, `0048` MP-003, `0049` MP-004, `0060` MP-005, `0063` MP-002) |
| **EAL plugin(s)** | None directly — composes EDR/NDR/ITDR plugins + adapters |
| **Adapters mapped to plane** | 1 (VT-CLI) — but MP scenarios wire many (NMAP, RUBEUS, BLOODHOUND, IMPACKET, SLIVER, SCAPY, PACU) |
| **IaC module** | None of its own — leans on `ndr` + `itdr` + cloud modules; `SIM-MP-004` ships its own `docker-compose.yml` + `xsoar_playbook.yml` package |
| **Plane descriptor** | `core/planes/analytics.py` (stub) |

**Scenarios:**

| Scenario | ID | TTP card | B/X/I/C | Theme |
|---|---|---|---|---|
| `multi_plane/mp-001-c2-beacon-ngfw-xdr-stitch.yml` | SIM-MP-001 | TTP-2026-0047 | 3/6/1/3 | C2 NGFW↔XDR session stitch |
| `multi_plane/mp-002-kerberoast-lateral-smb.yml` | SIM-MP-002 | TTP-2026-0063 | 3/6/0/2 | Kerberoast→PtH→DCSync (ITDR+EDR+NDR) |
| `multi_plane/mp-003-data-staged-exfil-dns-tunnel.yml` | SIM-MP-003 | TTP-2026-0048 | 4/5/1/3 | staged exfil via DNS tunnel |
| `multi_plane/mp-004-apt29-cloud-cred-theft.yml` | SIM-MP-004 | TTP-2026-0049 | 5/6/1/3 | APT29 cloud cred theft (has package) |
| `multi_plane/mp-005-cross-plane-correlation.yml` | SIM-MP-005 | TTP-2026-0060 | 3/4/0/1 | generic cross-plane correlation |

**Mature:** **Best correlation depth in the repo** (2–3 correlation rules/card vs 1
elsewhere) and highest XQL counts (5–6/card). MP-004 is the most complete artifact —
ships a `docker-compose.yml`, an XSOAR playbook YAML, and its own `ttps/` + `detections/`
subtree under `scenarios/multi_plane/packages/SIM-MP-004/`. This is the plane that
proves XSIAM's actual differentiator (stitching).

**Thin:** Depends on multiple other planes' IaC being deployed simultaneously
(NDR+ITDR+cloud) — the heaviest lab footprint, so hardest to run end-to-end. Only MP-004
has a packaged self-contained bundle; the other 4 don't. No `ANALYTICS` IaC module.

---

## 10. AI_ACCESS — AI Access Security (outbound LLM egress)

| Field | Value |
|-------|-------|
| **Cortex engine** | Cortex AI Access Security |
| **Status** | Active — 5 scenarios (Phase 4) |
| **# scenarios** | 5 |
| **# TTP cards** | 5 (`TTP-2026-0007`..`0011`) |
| **EAL plugin(s)** | `llm_provider_egress` (outbound to OpenAI/Gemini/Anthropic w/ planted DLP markers) |
| **Adapters mapped to plane** | **0** |
| **IaC module** | None |
| **Plane descriptor** | None |

**Scenarios:**

| Scenario | ID | TTP card | B/X/I/C | Provider |
|---|---|---|---|---|
| `ai_access/sim-aiacc-001-source-code-to-chatgpt.yml` | SIM-AIACC-001 | TTP-2026-0007 | 1/4/0/1 | OpenAI |
| `ai_access/sim-aiacc-002-aws-key-to-anthropic.yml` | SIM-AIACC-002 | TTP-2026-0008 | 1/3/1/1 | Anthropic |
| `ai_access/sim-aiacc-003-high-volume-gemini.yml` | SIM-AIACC-003 | TTP-2026-0009 | **0**/5/0/1 | Gemini |
| `ai_access/sim-aiacc-004-jailbreak-fingerprint.yml` | SIM-AIACC-004 | TTP-2026-0010 | 2/2/0/1 | (provider mix) |
| `ai_access/sim-aiacc-005-cross-provider-rotation.yml` | SIM-AIACC-005 | TTP-2026-0011 | **0**/5/0/1 | rotation |

**Mature:** Genuinely cross-provider egress emulator (3 real LLM providers), planted DLP
markers (source code, AWS keys) for clean DLP-rule validation. Good DLP-driven framing.

**Thin:** XQL-only on 2 of 5 cards (0 BIOC). Only 1 IOC across the plane. **No adapter
mapping and no IaC module** — entirely outbound-egress dependent. No descriptor module.

---

## 11. AIRS — AI Runtime Security (OWASP LLM01-10)

| Field | Value |
|-------|-------|
| **Cortex engine** | Cortex AI Runtime Security |
| **Status** | Active — 5 scenarios |
| **# scenarios** | 5 (+ **10 probe YAMLs** under `scenarios/airs/probes/llm01..llm10/`) |
| **# TTP cards** | 5 (`TTP-2026-0012`..`0016`) |
| **EAL plugin(s)** | `airs_prompt_attack` exists BUT scenarios drive the attack via `adapter_ref: TOOL-CORTEX-PROMPT-ATTACKER` + `command:` shell-out, NOT via the plugin (see gap) |
| **Adapters mapped to plane** | 1 (CORTEX-PROMPT-ATTACKER) — all 5 scenarios wired |
| **IaC module** | None (target is the in-tree `cortex-vulnerable-llm` Flask app on :8089) |
| **Plane descriptor** | None |

**Scenarios + the 10 probes:**

| Scenario | ID | TTP card | B/X/I/C |
|---|---|---|---|
| `airs/sim-airs-001-direct-prompt-injection.yml` | SIM-AIRS-001 | TTP-2026-0012 | 1/4/0/1 |
| `airs/sim-airs-002-indirect-rag-poisoning.yml` | SIM-AIRS-002 | TTP-2026-0013 | 1/4/0/1 |
| `airs/sim-airs-003-system-prompt-leak.yml` | SIM-AIRS-003 | TTP-2026-0014 | 2/3/0/1 |
| `airs/sim-airs-004-tool-call-abuse.yml` | SIM-AIRS-004 | TTP-2026-0015 | 2/3/0/1 |
| `airs/sim-airs-005-token-exhaustion-dos.yml` | SIM-AIRS-005 | TTP-2026-0016 | **0**/5/0/1 |

Probe pack (OWASP LLM mapping, consumed by cortex-prompt-attacker):
`llm01/{delimiter_smuggle, ignore_previous_basic, role_play_dan}`,
`llm02/customer_record_extract`, `llm06/{tool_abuse_exec_shell, tool_abuse_send_email}`,
`llm07/{initial_instruction, repeat_words_above}`, `llm08/rag_trigger`,
`llm10/dos_unbounded_tokens`.

**Mature:** Full pipeline (cortex-prompt-attacker: Probe→Mutator→Target→Scorer) against a
deliberately vulnerable in-tree target (`cortex-vulnerable-llm`, one blueprint per OWASP
LLM01-10). Probe library is real and OWASP-mapped. Scenarios do pre-flight `/healthz`
checks. Garak-shape JSONL output.

**Thin:** **OWASP coverage gaps** — probes exist for LLM01,02,06,07,08,10 only;
**LLM03/04/05/09 have no probe**. The `airs_prompt_attack` EAL plugin is built and unit-
tested but **not referenced by any scenario YAML** (scenarios shell out to the CLI via
adapter instead) — a confusing dual path. XQL-only on card 005, 0 IOCs plane-wide,
no descriptor module.

---

## 12. BROWSER — Prisma Browser

| Field | Value |
|-------|-------|
| **Cortex engine** | Prisma Browser |
| **Status** | Active — 5 scenarios (Phase 6) |
| **# scenarios** | 5 (+ **5 campaign YAMLs** under `scenarios/browser/campaigns/`) |
| **# TTP cards** | 5 (`TTP-2026-0017`..`0021`) |
| **EAL plugin(s)** | `browser_attack_runner` (drives `cortex-browser-attacker` Playwright CLI; StubDriver fallback for tests) |
| **Adapters mapped to plane** | 4 (CORTEX-BROWSER-ATTACKER, EVILGINX2, GOPHISH, PAYLOADSALLTHETHINGS) — all 5 scenarios wired to CORTEX-BROWSER-ATTACKER |
| **IaC module** | None |
| **Plane descriptor** | None |

**Scenarios:**

| Scenario | ID | TTP card | B/X/I/C | Campaign |
|---|---|---|---|---|
| `browser/sim-browser-001-credential-paste.yml` | SIM-BROWSER-001 | TTP-2026-0017 | 1/3/0/1 | campaigns/cred-paste.yml |
| `browser/sim-browser-002-drive-by-download.yml` | SIM-BROWSER-002 | TTP-2026-0018 | 1/4/0/1 | campaigns/drive-by-download.yml |
| `browser/sim-browser-003-risky-extension-install.yml` | SIM-BROWSER-003 | TTP-2026-0019 | 1/4/0/1 | campaigns/risky-extension-install.yml |
| `browser/sim-browser-004-saas-cross-origin-dlp.yml` | SIM-BROWSER-004 | TTP-2026-0020 | 1/4/0/1 | campaigns/saas-cross-origin-dlp.yml |
| `browser/sim-browser-005-screen-capture.yml` | SIM-BROWSER-005 | TTP-2026-0021 | 1/4/0/1 | campaigns/screen-capture.yml |

**Mature:** Real Chromium/Prisma Browser driving via Playwright with a YAML action DSL
(navigate/paste/copy/click/download/install_extension/screenshot). Clean test story
(StubDriver). Has both scenario AND campaign-level YAML. JSONL output rhymes with garak +
cortex-prompt-attacker (consistent SOC stream).

**Thin:** Flat detection cards (uniformly 1 BIOC / 3–4 XQL / 0 IOC each). No IaC module.
No descriptor module. Detection layer is the thinnest-but-uniform of the active planes.

---

## 13. KOI — Agentic Endpoint / Supply-Chain

| Field | Value |
|-------|-------|
| **Cortex engine** | Agentic endpoint / supply-chain (MCP / skills / extensions / PyPI) |
| **Status** | Active — 5 scenarios (Phase 5) |
| **# scenarios** | 5 |
| **# TTP cards** | 5 (`TTP-2026-0042`..`0046`) |
| **EAL plugin(s)** | `agentic_egress` (tarballs + POSTs the malicious artifact tree to an authorized staging host so the NGFW sees the egress shape) |
| **Adapters mapped to plane** | 2 (CORTEX-AGENTIC-PACK, YARA) — all 5 scenarios wired to CORTEX-AGENTIC-PACK |
| **IaC module** | None (artifact tree is the in-tree `cortex-malicious-agentic-pack`) |
| **Plane descriptor** | None |

**Scenarios:**

| Scenario | ID | TTP card | B/X/I/C |
|---|---|---|---|
| `koi/sim-koi-001-typosquat-mcp-server.yml` | SIM-KOI-001 | TTP-2026-0042 | 2/4/1/1 |
| `koi/sim-koi-002-mcp-tool-response-injection.yml` | SIM-KOI-002 | TTP-2026-0043 | 1/4/1/1 |
| `koi/sim-koi-003-backdoored-pypi-package.yml` | SIM-KOI-003 | TTP-2026-0044 | 2/4/2/1 |
| `koi/sim-koi-004-vscode-extension-permission-escalation.yml` | SIM-KOI-004 | TTP-2026-0045 | 2/4/1/1 |
| `koi/sim-koi-005-claude-skill-hidden-instructions.yml` | SIM-KOI-005 | TTP-2026-0046 | 1/5/0/1 |

**Mature:** A novel, timely plane (agentic supply-chain) with a real 6-component
artifact pack (typosquat MCP, malicious MCP w/ hidden injection, backdoored PyPI,
malicious Claude skill, VS Code ext reading `~/.aws/credentials`, Chrome ext w/
`<all_urls>`+cookies). All side-effects gated on `CORTEXSIM_C2_URL` (static-scan safe).
Every card carries IOCs (4 of 5) — the **only AI-adjacent plane with consistent IOCs**.

**Thin:** Detection cards modest (1–2 BIOC). No IaC module / no descriptor. The
`agentic_egress` plugin requires an authorized staging host to see the egress shape —
without it the NDR signal half doesn't fire.

---

## 14. AI_SPM — AI Security Posture Management (⚠️ undocumented plane)

> **This plane is NOT in the CLAUDE.md plane table** but ships 6 scenarios, 6 TTP cards,
> and an IaC module. It is the largest undocumented surface in the repo.

| Field | Value |
|-------|-------|
| **Cortex engine** | Cortex Cloud Posture Management — AI extension (AI-SPM) |
| **Status** | Active — 6 scenarios — **but absent from CLAUDE.md** |
| **# scenarios** | 6 (`scenarios/ai_spm/`) |
| **# TTP cards** | 6 (`TTP-2026-0054`..`0059`) |
| **EAL plugin(s)** | **None** |
| **Adapters mapped to plane** | 0 |
| **IaC module** | `infra/modules/aws/ai-spm` (SageMaker, Bedrock, Lambda→OpenAI, shadow GPU LLM, training data w/ PII; tagged `CortexSimAISPMFinding=<type>`) — the 11th AWS module |
| **Plane descriptor** | None |

**Scenarios:**

| Scenario | ID | TTP card | B/X/I/C |
|---|---|---|---|
| `ai_spm/sim-aispm-001-ai-asset-discovery.yml` | SIM-AISPM-001 | TTP-2026-0054 | 0/4/0/1 |
| `ai_spm/sim-aispm-002-ai-model-security-assessment.yml` | SIM-AISPM-002 | TTP-2026-0055 | 0/3/0/1 |
| `ai_spm/sim-aispm-003-ai-supply-chain.yml` | SIM-AISPM-003 | TTP-2026-0056 | 0/4/0/1 |
| `ai_spm/sim-aispm-004-ai-static-risk-analysis.yml` | SIM-AISPM-004 | TTP-2026-0057 | 0/5/0/1 |
| `ai_spm/sim-aispm-005-ai-sensitive-data.yml` | SIM-AISPM-005 | TTP-2026-0058 | 0/5/0/1 |
| `ai_spm/sim-aispm-006-ai-security-dashboard.yml` | SIM-AISPM-006 | TTP-2026-0059 | 0/3/0/1 |

**Mature:** Has the full triple (6 scenarios + 6 cards + dedicated IaC module). The
IaC module is a thoughtful AI-asset-inventory target (shadow LLM, ML pipelines, PII
training data).

**Thin:** **Every single card is XQL-only (0 BIOC, 0 IOC, 1 correlation).** Posture
checks naturally lean on inventory queries, but there is zero BIOC/IOC enforcement
content. No EAL plugin (purely posture/inventory). And critically — **the plane is
undocumented in CLAUDE.md**, so a DC reading the project guide would not know it exists.

---

# Cross-plane maturity scorecard

Maturity is scored across the artifact layers. **Detection depth** = total
BIOC+XQL+IOC+correlation summed across the plane's cards. Legend: ●=strong / ◐=partial /
○=absent or stub.

| Plane | Engine | Scen. | TTP cards | Detection depth (B/X/I/C totals) | EAL plugin | Adapters mapped / wired | IaC module | Descriptor | Overall |
|-------|--------|:----:|:--------:|:-------------------------------:|:----------:|:----------------------:|:----------:|:----------:|:-------:|
| **EDR** | XDR Agent | 5 | 5 | 23/24/3/5 ● | ○ (real cmds) | 14 / 5 ● | ● | ◐ stub | **●** |
| **CDR** | Cloud/Compute | 5 | 5 | 45/15/7/5 ● | ○ (real cmds) | 12 / 1 ◐ | ● | ◐ stub | **●** |
| **Analytics** | XSIAM Correl. | 5 | 5 | 18/27/3/12 ● | ○ (composes) | 1 / many ◐ | ○ (reuses) | ◐ stub | **●** |
| **NDR** | NGFW/FW Analytics | 7 | 6 ⚠️ | 7/23/3/6 ◐ | ● 7 plugins | 16 / 1 ◐ | ● | ◐ stub | **◐** |
| **ITDR** | Cortex ITDR | 5 | 5 | 8/14/0/5 ◐ | ● 1 plugin | 14 / 1 | ● rich | ◐ stub | **◐** |
| **KOI** | Agentic/supply-chain | 5 | 5 | 8/21/5/5 ◐ | ● 1 plugin | 2 / 5 | ○ | ○ | **◐** |
| **Cloud App** | Cloud App Sec | 5 | 5 | 4/19/0/5 ◐ | ● 1 plugin | 5 / 0 ◐ | ○ | ◐ stub | **◐** |
| **AIRS** | AI Runtime Sec | 5 (+10 probes) | 5 | 6/19/0/5 ◐ | ◐ built-unused | 1 / 5 | ○ | ○ | **◐** |
| **BROWSER** | Prisma Browser | 5 (+5 camp.) | 5 | 5/19/0/5 ◐ | ● 1 plugin | 4 / 5 | ○ | ○ | **◐** |
| **AI_ACCESS** | AI Access Sec | 5 | 5 | 4/19/1/5 ◐ | ● 1 plugin | 0 / 0 ○ | ○ | ○ | **◐** |
| **AI_SPM** | AI-SPM (CSPM ext) | 6 | 6 | 0/24/0/6 ○ | ○ | 0 / 0 ○ | ● | ○ | **◐ (undoc)** |
| **CSPM** | Cloud Posture | 0 | 0 | — ○ | ○ | 0 / 0 ○ | ● | ○ | **○** |
| **ASM** | Attack Surface | 0 | 0 | — ○ | ○ | 0 / 0 ○ | ● | ○ | **○** |
| **TIM** | Threat Intel | 0 | 0 | — ○ | ○ | 0 / 0 ○ | ● | ○ | **○** |

**Reading the table:** EDR / CDR / Analytics are the production-grade trio (deep
detection cards + wired adapters + IaC). NDR/ITDR are strong on signal generation and
labs but shallow on detection cards. The AI/SaaS planes (Cloud App, AIRS, BROWSER,
AI_ACCESS, KOI, AI_SPM) are XQL-heavy with almost no BIOC/IOC enforcement and no IaC.
CSPM/ASM/TIM are IaC-only shells with nothing launchable.

---

# Top 3 weakest planes (by POV impact)

### 1. CSPM / ASM / TIM (tied — IaC-only shells) — **highest POV risk**
These three are in the CLAUDE.md plane table as shipping planes, but each has **0
scenarios, 0 TTP cards, 0 EAL plugins, and no scored artifact**. A DC who selects
"CSPM" (or ASM/TIM) for a POV finds **nothing to launch** in the scenario browser — only
a Terraform module to apply and then manually eyeball the Cortex console. There is no
MTTD measurement, no auto-seeded Result rows, no POV report content. For a tool whose
*entire value proposition* is generating scored, measurable detection validation, three
named planes that produce no scenario is the biggest credibility gap. **Fix:** author at
least one launchable scenario + TTP card per plane that references the existing IaC
module's planted findings.

### 2. AI_SPM — **largest undocumented + zero-enforcement plane**
AI_SPM ships a complete triple (6 scenarios, 6 cards `TTP-2026-0054..0059`, a dedicated
`ai-spm` IaC module — the 11th AWS module) yet is **entirely absent from the CLAUDE.md
plane table**, so it is invisible to anyone onboarding from the project guide. On top of
that, **all 6 cards are XQL-only (0 BIOC, 0 IOC)** — there is no detection-enforcement
content, only inventory queries. The module count discrepancy (CLAUDE.md says "10
modules", reality is 11) traces to this same omission. **Fix:** add the row to CLAUDE.md
and decide whether posture findings warrant BIOC/IOC content or are intentionally
query-only.

### 3. AI_ACCESS — **most isolated active plane (no adapter, no IaC, no descriptor)**
AI_ACCESS has working scenarios + cards + the `llm_provider_egress` plugin, but it is the
only **active** plane with **0 adapters mapped, 0 IaC module, and 0 descriptor module**,
and 2 of its 5 cards are XQL-only with 0 BIOC. It depends entirely on live outbound to
real LLM providers (OpenAI/Gemini/Anthropic) with no lab-contained fallback — fragile in
air-gapped or egress-restricted POV environments where DCs frequently operate. **Fix:**
add a contained/mock provider target (mirroring how AIRS uses the in-tree
`cortex-vulnerable-llm`) and enrich BIOC/IOC content.

> **Honorable mention — AIRS dual-path + OWASP gap:** the `airs_prompt_attack` EAL plugin
> is fully built and unit-tested but **no scenario actually uses it** (scenarios shell out
> to the cortex-prompt-attacker CLI via `adapter_ref` instead), and the probe library
> covers only OWASP LLM01/02/06/07/08/10 — **LLM03/04/05/09 have no probes**.

---

## Appendix A — Notable cross-cutting facts

- **Detection depth is wildly uneven.** CDR cards average ~9 BIOCs; every AI_SPM card has
  0 BIOCs. The repo's BIOC content is concentrated in EDR+CDR+Analytics; the entire AI/SaaS
  family relies on XQL.
- **IOCs are nearly absent outside EDR-001, CDR, KOI, and the exfil NDR cards.** 8 of 13
  active planes have ≤1 IOC total.
- **Every TTP card is `status: active`** — there is no draft/skeleton state in the live
  set (the `_drafts/` dir exists but is empty of tracked cards here).
- **6 bespoke TTP cards** (`TTP-2026-0001..0006`) have no matching `SIM-*` scenario:
  helpdesk MFA reset, LSASS dump, AWS IAM/S3 exfil, DCSync, rclone exfil, ESXi mass-encrypt.
  These are detection-engineering artifacts without a launchable playbook (inverse of the
  CSPM/ASM/TIM gap).
- **The "scenarios reference adapters" design rule is only ~42% realized** — 27 of 65
  scenarios carry an `adapter_ref`; the rest hand-roll CLI in `command:` blocks (notably
  4 of 5 CDR scenarios and 6 of 7 NDR scenarios).
