# Detection-Substrate Expansion — Broadening the Detection Surface

> **As-of:** 2026-06-16 · branch `ultracode/full-revamp`
> Merges (a) a cited deep-research pass on what detection substrates to bring in
> across every Cortex line of defense, with (b) a port-map of the unported
> `sources/xsiam-prisma-cdr-lab` content. Research findings carry a confidence +
> adversarial-vote tag; treat 0-3/refuted items as **do-not-assert**.

CortexSim today models five detection-content types — **BIOC | XQL | Analytics |
Correlation | IOC** — across 14 planes (75 scenarios / 76 cards). The gap the
operator felt is real: there are **whole substrate layers Cortex exposes that the
corpus does not yet model**, plus a fully-built CDR/K8s lab that was never ported.

---

## 1. Bottom line — prioritized bring-in list

Ordered by leverage (how much it broadens the surface ÷ effort). Each row is tagged
**[NET-NEW]** (research-driven, build from scratch), **[PORT]** (already built in the
lab, needs wrapping into CortexSim), or **[FRAMEWORK]** (a schema/engine change that
unlocks a content type).

| # | Bring-in | Type | Why it's high leverage |
|---|----------|------|------------------------|
| 1 | **XDM Parsing + Modeling Rules** as a modeled substrate | NET-NEW · FRAMEWORK | The normalization layer *beneath every detection*. Without it the POV starts at "assume data is already normalized"; with it we prove data-onboarding → XDM → detection. Confirmed first-class Cortex layer (3-0, primary docs). |
| 2 | **ABIOC + UEBA** as a first-class detection-content type | NET-NEW · FRAMEWORK | Documented, PANW-authored, auto-tuned behavioral-ML detection — distinct from hand-authored BIOC. The whole `xsiam-prisma-cdr-lab` is built around ABIOC. Confirmed distinct type (3-0). |
| 3 | **CDR/K8s lab port** (sockshop ABIOC, k8s-goat escape, ransomware-in-container, insecure-deployment, WildFire-in-container) | PORT | ~10 ready-built scenarios; CDR depth goes 8 → ~16. Lowest effort per scenario — the attack content already exists and runs. |
| 4 | **NGFW↔endpoint causality stitch** (App-ID / URL / DGA net-gen) | PORT · NET-NEW | The lab's `ngfw/container-ttp-netgen.sh` + the 5-tuple+10s stitch recipe. Directly strengthens the ANALYTICS plane's headline "stitching" wedge (3-0: stitching is the architectural reality to simulate). |
| 5 | **Email-security plane** (Proofpoint TAP → `proofpoint_tap_raw`, M365) | NET-NEW | A genuinely missing line of defense. Email is a first-class XSIAM ingestion source with shipped parsing/modeling rules (3-0, primary docs + `Palo-Cortex/soc-proofpoint-tap`). Phishing/BEC is the #1 initial-access vector and we model none of it. |
| 6 | **KOI MCP tool-response poisoning** (runtime, not just connect-time) | NET-NEW | Our KOI pack ships malicious-MCP/typosquat (connect-time) but not the **runtime tool-response injection** trust-gap OWASP documents (3-0). One scenario closes a named 2025-2026 attack surface. |
| 7 | Container-workload XQL library (`actor_container_info` JSON extraction) | PORT | The lab's `csa-xql`/`container_info.sql` is reusable detection content for every CDR card. |

**Net read:** items 1-2 are the structural unlock (new substrate layers); 3-4 are
cheap, high-fidelity ports; 5-6 are bounded net-new planes/scenarios with verified
threat backing.

---

## 2. Verified net-new substrates (research-backed, cited)

### 2a. XDM normalization layer — Parsing Rules + Modeling Rules `[high · 3-0]`
The single highest-leverage net-new substrate. Modeling rules "map your logs into a
single, unified data model … regardless of source or dataset," written in XQL under a
`MODEL` declaration that maps raw event JSON → XDM (Cortex Data Model) fields.
Normalization is **opt-in per source / schema-on-read** — un-modeled logs stay
searchable but are *not* analytics-grade. So modeling rules are the **gating substrate**
for analytics-grade detection. *Source: PANW Developer Guide — Data modeling rules
(primary).*
**CortexSim modeling:** add a `parsing`/`modeling` artifact kind to the detection card
(or a sibling content tree) carrying the `[MODEL: dataset=…]` XQL; scenarios can then
prove "raw `<source>` log → XDM normalized → BIOC/Analytics fires."

### 2b. ABIOC + UEBA behavioral-ML `[high · 3-0]`
ABIOC is a **separately documented** content type: "indicate a single event of
suspicious behavior with an identified chain of causality … leverage user, endpoint,
and network profiles … based on a simple statistical profile or a more complex
machine-learning profile … Cortex tailors each ABIOC to your environment and
continually tunes/delivers new ABIOCs with content updates." UEBA is a related-but-
distinct identity/entity analytics layer (don't conflate). *Source: PANW XSIAM docs —
BIOC + ABIOC pages (primary).*
**CortexSim modeling:** extend the `detection_type` enum to include **ABIOC** (and
optionally `UEBA`); the lab's sockshop/malicious-container scenarios are the natural
first ABIOC cards.

### 2c. Email-security plane `[high · 3-0]`
XSIAM (3.x/NG-SIEM) ingests Proofpoint TAP into `proofpoint_tap_raw` with shipped
`[MODEL: dataset=proofpoint_tap_raw, content_id="ProofpointTAP"]` parsing+modeling
rules; a parallel M365 connector exists (compliance-mailbox BCC). PANW markets "~10K
detectors / 2.6K ML models across endpoint, network, cloud, identity, **and email**."
*Sources: PANW Proofpoint TAP docs (primary), `Palo-Cortex/soc-proofpoint-tap` GitHub.*
⚠️ The 10K/2.6K counts are round, unaudited vendor figures — cite descriptively, not as
efficacy benchmarks. Open question (below): is there a first-party Email Security
*product* vs. raw log ingestion?
**CortexSim modeling:** new `EMAIL` plane; data sources = Proofpoint TAP + M365; TTPs =
phishing (T1566.001/.002), BEC/T1656 impersonation, malicious attachment/link, thread
hijack; detections = parsing/modeling of `proofpoint_tap_raw` + correlation to the
endpoint/identity follow-on (the stitch story).

### 2d. KOI MCP tool-response poisoning `[high · 3-0]`
"The root cause is a trust gap between connect-time and runtime. Tool descriptions are
reviewed once … Tool responses go straight into the LLM context with no equivalent
check." Heightened when users add MCP servers freely + agents hold privileged tools.
Live CVEs: CVE-2025-49596, CVE-2025-54136. *Source: OWASP MCP Tool Poisoning
(primary); OWASP MCP Top-10 MCP04.*
**CortexSim modeling:** new KOI scenario exercising a malicious **tool-response**
(runtime) injection — distinct from the existing connect-time typosquat/malicious-MCP
pack.

### 2e. Cross-plane raw-telemetry stitching `[high · 3-0]`
"Continuous collection, stitching, and normalization of raw data, not just alerts …
normalizes data and stitches different POVs of the same event into a single, augmented
log line." This validates the existing ANALYTICS scenarios and argues for simulating
**raw cross-plane telemetry feeding one augmented event**, not isolated per-plane
alerts. *Source: PANW Cyberpedia AI-SOC (primary), corroborated by XDM onboarding
guides.* The lab's NGFW net-gen (5-tuple + 10s window) is the concrete recipe.

---

## 3. Port-from-lab — `sources/xsiam-prisma-cdr-lab` → CortexSim

The lab (1.0 + 2.0, ~16 scenario folders) is built and runnable but never wrapped into
CortexSim scenario/card form. Highest-fidelity, lowest-effort additions:

| Lab folder | → CortexSim target | Plane | Notes |
|------------|--------------------|-------|-------|
| `2.0/sockshop-k8-ABIOC` | SIM-CDR-009 microservices behavioral anomaly | CDR | **First ABIOC card** (needs §2b) |
| `2.0/ransomware-in-container` | SIM-CDR-010 ransomware-in-container | CDR | Distinct from EDR-008 host ransomware |
| `2.0/panw-goat` (kubernetes-goat) | SIM-CDR-011 container-escape→host chain | CDR | Complements CDR-003 (escape) with full kill chain |
| `2.0/malicious-container-2.0` (`wildfire-samples.sh`, `container-escape.sh`, `K8-misconfig`) | SIM-CDR-012 WildFire-in-container + misconfig | CDR | Exercises WildFire verdict path |
| `2.0/insecure-deployment` + `k8-busybox-attack` | SIM-CDR-013 insecure-deployment detection | CDR | Posture→runtime |
| `2.0/attk-ttp` | SIM-CDR-014 malicious-k8s-deployment TTP chain | CDR | Multi-step in-cluster TTP |
| `2.0/ngfw` (`container-ttp-netgen.sh`, `fw-analysis`) | SIM-MP-006 NGFW App-ID/URL/DGA ↔ endpoint stitch | ANALYTICS | The 5-tuple+10s causality stitch (item #4) |
| `2.0/juice-shop`, `2.0/dvwa` | fold into ASM-004 / new web-in-cluster | ASM/CDR | Web targets already partly covered |
| `2.0/tests-toolkits/csa-xql`, `container_info.sql` | reusable container XQL in CDR cards | CDR | Detection content, not a scenario |
| `2.0/tests-toolkits/WIP/crwd-detection-container` | competitive detection-container proof | — | CrowdStrike-vs-Cortex POV artifact (handle as a competitive demo, not a scenario) |
| `1.0/cryptominers` multi-miner replicaSet | depth add to CDR-002 | CDR | `todo.md`: persistence via replicaSet |

Net: CDR plane 8 → ~14 scenarios + 1 new ANALYTICS stitch scenario, mostly by wrapping
existing lab artifacts.

---

## 4. Framework changes these unlock

- **`detection_type` enum** → add `ABIOC` (and consider `UEBA`). Touches
  `scenarios/_schema.yml`, `core/engine/scenario_loader.py`, `core/engine/uctc_mapper.py`,
  the card schema's `detections`, and the coverage rollup (ABIOC is validated-ML, like
  Analytics but causality-anchored).
- **Parsing/Modeling-rule artifact kind** → a new content tree (or card section)
  carrying `[MODEL: dataset=…]` XQL, surfaced like the existing exports so a DC can paste
  it into XSIAM and prove the ingestion→normalization→detection chain.
- **`EMAIL` plane** → new `PlaneDescriptor`, plane enum value, IaC/data-source hint, and
  a TTP-card family for phishing/BEC.

---

## 5. Caveats — do NOT assert (failed verification)

The research pass adversarially killed 14 of 25 claims. The following are **refuted or
unverified** — do not put them in a POV deck without primary re-verification:

- ❌ **"Cortex beats CrowdStrike on non-endpoint analytics"** — refuted 0-3 (PANW
  marketing page only). Frame the wedge as *platform-breadth positioning* (native
  cross-plane analytics + XDM normalization + single augmented log), **not** an audited
  competitive fact.
- ❓ **MITRE ATLAS v5.4.0 counts** (16 tactics / 84 techniques) and new agentic ATLAS
  techniques ("Publish Poisoned AI Agent Tool") — unverified; re-check before citing.
- ❓ **Prisma AIRS 2.0** three-module architecture / "500+ attack types" — unverified
  (SiliconAngle only). Re-verify against PANW primary before scoping AIRS depth.
- ❓ **Cortex AgentiX = next-gen XSOAR + six prebuilt agents** — unverified; the
  XSOAR/response-automation "plane" is plausible but not source-confirmed.
- ❓ **2026 BAS market-leader listicle** — unverified, excluded.

---

## 6. Open questions the research could not close (next round)

1. **ATT&CK v16 cloud-specific TTPs** (cloud-IAM/federation abuse, container-image
   supply chain) — in the brief but NO surviving claim covered them. Need canonical
   XSIAM datasets + content types per TTP.
2. **OT/IoT line of defense** — viability + canonical data sources/TTPs unaddressed.
3. **First-party Email Security product vs. raw ingestion** — the marketing page
   conflated them; confirm what detection-content types/datasets a product (if any)
   adds beyond Proofpoint/M365 logs.
4. **XSOAR/AgentiX response-automation plane** — worth modeling playbook/automated-
   response validation? Confirm the AgentiX capability surface first.
5. **AIRS 2.0 capability surface** — verify module breakdown + OWASP LLM Top-10
   coverage before scoping new AIRS scenarios.

### Round-2 research status (2026-06-16) — RATE-LIMITED / INCONCLUSIVE

The second deep-research pass (`wf_483218a4-9d7`) **was throttled**: the verification
fan-out hit sustained API rate-limits, so every claim landed at 0-0 (abstain) or 1-0
(single vote) and was dropped by the 2/3-consensus rule. **This is "could not verify,"
not "found false."** The raw claims gathered are plausible, on-target leads — but they
are **UNVERIFIED; re-verify against primary sources before any POV use**, and re-run the
pass when limits clear. Captured leads:

- **(Q1) Identity-federation abuse** — `T1556.007` Modify Authentication Process: Hybrid
  Identity (IaaS/IdP/Office/SaaS/Windows); `T1606.002` Forge Web Credentials: SAML Tokens
  (Credential Access, forge tokens with a stolen signing cert or rogue AD FS trust). Both
  map to ITDR/CDR/CSPM. Datasets (claimed): `msft_azure_ad_raw`/`msft_azure_ad_audit_raw`
  (Azure Logs pack), `amazon_aws_raw` (AWS CloudTrail pack, maps `user_identity_arn` →
  `xdm.source.user.identifier`). Caveat: those Marketplace packs reportedly ship
  **normalization rules only, no detection content** — so federation-abuse BIOC/XQL must
  be authored on top.
- **(Q1) Container supply chain** — `T1195.002` (Linux/Win/macOS only — **does NOT list
  Containers/IaaS**, so it doesn't map natively to CDR; supplement with) `T1612` Build
  Image on Host (Docker API build to bypass registry-pull monitoring).
- **(Q2) OT/IoT** — claimed first-party PANW IoT Security / OT Security with XSIAM
  ingestion (device inventory + protocol metadata). Unverified; viability for a Cortex
  BAS plane still open.
- **(Q3) Email** — a claimed **first-party "Cortex Advanced Email Security"** (LLM-based,
  correlates email/identity/endpoint) surfaced repeatedly. **If real, this changes Plan
  04** (which currently assumes third-party Proofpoint/M365 ingestion only). HIGH-PRIORITY
  to verify against PANW primary before building the Email plane.
- **(Q4/Q5) AgentiX + AIRS 2.0** — no surviving signal this round; still open.

---

## Sources (surviving, by quality)

- **Primary (PANW/OWASP docs):** Data modeling rules; BIOC + ABIOC pages; Proofpoint TAP
  ingestion; AI-SOC stitching (Cyberpedia); OWASP MCP Tool Poisoning. + `Palo-Cortex/
  soc-proofpoint-tap` (GitHub).
- **Secondary/excluded:** CrowdStrike-vs-Cortex comparison (marketing), SiliconAngle
  AIRS 2.0, StockTitan AgentiX, BAS listicles — used only where independently
  corroborated; otherwise flagged in §5.

Full machine output: deep-research run `wf_40dee639-fb1` (107 agents · 12 sources
fetched · 52 claims → 11 confirmed / 14 killed / 6 synthesized).
