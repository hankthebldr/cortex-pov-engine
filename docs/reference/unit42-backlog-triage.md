# Unit 42 research backlog: triage of the 20 uncited posts (2026-11-11)

Source list: `detection_scanner/sources/unit42-index.json` (68 curated posts).
Counted on `dev` at `8c90739`: **20 posts are cited by no TTP card and no
scenario** (matched by URL across `detection_scanner/ttps/*.json` and
`scenarios/**/*.yml`): 4 P0, 12 P1, 4 P2. This file records a verdict for each.

## Executive summary

One of the twenty posts supports a deep simulation the lab can run and prove
today: the Axios supply-chain compromise, whose Linux second stage is a remote
access tool that needs nothing the lab endpoint lacks. It is being built. Three
more describe real, specific attacker behaviour but cannot be built inside this
pass without a decision from the operator: one is Windows-only, and two are
cloud control-plane techniques that need new log shapes the engine does not
emit yet. The remaining sixteen are reports, opinion pieces, press releases,
reference pages or repeats of scenarios already in the library, and none of
them publishes enough concrete behaviour to ground a simulation. The practical
risk this triage removes is building simulations from thin sources: a scenario
authored from a narrative with no commands or indicators would test a
detection the research never described, and a miss on it would read as a
product gap.

## Technical detail

### Method

- Each post was fetched in full and read by a subagent (workflow
  `unit42-backlog-triage`, 12 readers, 18 posts; run 2026-11-11). Every fetch
  succeeded. Where the first fetch tool returned a model summary, the page was
  re-read from a full scrape. No page contained text addressed to an AI reader.
- Two posts were **not** re-fetched because the operator already decided them
  on 2026-11-11 (CL-STA-1062 dropped, FortiBleed rejected). They are listed
  with that decision and were not reopened.
- Each reader returned a schema-validated record: post type, platforms, a count
  of concrete behaviours (a command line, file path, process chain or API
  call), IOC counts, published queries, and the existing scenarios that cover
  the same actor or technique. The overlap claims quoted below were re-checked
  with direct greps over `scenarios/` and `detection_scanner/ttps/`.
- The four tests, in order: **groundable** (enough concrete behaviour for a
  deep chain), **runnable where it can be proven** (one Ubuntu 24.04 endpoint
  with Cortex XDR in report mode, no node/npm, no Windows or macOS host, no
  cloud account; cloud or SaaS behaviour can only be authored as shape-true
  logs through an existing EAL emitter), **new coverage**, **priority**.

### Verdicts

Counts after Henry's decisions of 2026-11-11: **build now 1 · build as
authored-only 1 (row 10) · built only as a leg of row 10 1 (row 9) · deferred 1
(row 1) · needs Henry 0 · not scenario material 16**. At triage the count was
build now 1 · needs Henry 3 · not scenario material 16. "beh" is the reader's
count of concrete behaviours; "IOCs" is hashes / domains / IPs as published.

| # | Pri | Post | Verdict | Reason and evidence |
|---|---|---|---|---|
| 1 | P0 | Tracking Iranian APT Screening Serpens' 2026 Espionage Campaigns | **deferred** | **Operator decision 2026-11-11: not built now**, deferred until the lab has a Windows host (decision 2 below). Windows-only. beh 12, IOCs 12/23/0, all Windows and .NET Framework (AppDomainManager hijack, DLL side-load, scheduled tasks, Azure App Service C2); no Linux or macOS behaviour, and there is no Windows lab host. This is the same fit question answered "drop" for CL-STA-1062. T1574.014 is already carried by SIM-MP-025, so the index note that it would be new technique coverage is stale. |
| 2 | P0 | Extortion and Ransomware Trends January-March 2025 | not scenario material | Trend report. beh 0, IOCs 0, no ATT&CK ids, no queries. The two observations with detail behind them are already built from their own source posts (SIM-MP-012, SIM-MP-030). |
| 3 | P0 | Anatomy of an Attack: The Payroll Pirates | not scenario material | A narrative of about 900 words. beh 0, IOCs 0, no log-event or API names. The one groundable chain (help-desk vishing, MFA reset, re-enrol, sign-in) is already SIM-ITDR-008 with recon in SIM-ASM-006. The two novel steps (an external email address added as an authentication method; a payroll direct-deposit change) get one sentence each, and no emitter has a preset for either. |
| 4 | P0 | Threat Brief: Mitigating Large-Scale Credential Attacks ("FortiBleed") | not scenario material | **Operator decision 2026-11-11: rejected** as a thin brief with no post-exploitation detail and no IOCs. Not reopened, not re-fetched. |
| 5 | P1 | Muddled Libra: Why Are We So Obsessed With You? | not scenario material | Opinion piece. beh 0, IOCs 0. The actor is already in 11 scenarios; the post adds no behaviour they lack. |
| 6 | P1 | CL-STA-1062 Targets Southeast Asian Governments and Critical Infrastructure | not scenario material | **Operator decision 2026-11-11: dropped**, Windows-only. Not reopened, not re-fetched. |
| 7 | P1 | Multi-Extortion Tactics Continue to Rise | not scenario material | Teaser for a 2023 report. beh 0, IOCs 0, no named actor or malware. The report itself is a PDF behind a registration form and was not followed. |
| 8 | P1 | Novel Technique to Detect Cloud Threat Actor Operations | not scenario material | Alert-statistics and methodology piece. It publishes Cortex alert rule names and ATT&CK ids only: no CloudTrail event names, Azure operation names or queries. The alert names that match existing emitter presets already drive SIM-CDR-019, SIM-CDR-021 and SIM-CLOUD-008. |
| 9 | P1 | The Global Namespace Risk: Universal Bucket Hijacking | **leg of row 10** | **Operator decision 2026-11-11: not built as its own scenario**; its victim-side events are one leg of the row-10 build (decisions 1 and 3 below). Too thin to stand alone. The victim-side sequence is two published GCP events (`storage.objects.delete`, then `storage.buckets.delete`); the recreate happens in the attacker's account and is invisible in victim logs. IOCs 0, no queries. At triage no emitter preset carried the pair; the `stream_bucket_delete` preset on `feature/api-cloud-audit-emitter-presets` now emits it on `gcp`. |
| 10 | P1 | Blinding the Watchmen: Abusing Cloud Logging Services | **build as authored-only, own branch** | **Operator decision 2026-11-11: build it** on its own branch, `feature/scenarios-unit42-cloud-logging`, authored-only, with row 9 as one leg (decision 1 below). The best cloud fit. beh 14 across seven techniques on AWS CloudTrail and Google Cloud Logging, mapped to eight named Cortex XDR analytics alerts; the corpus has T1562.008 as a single step (SIM-CSPM-002). At triage it was blocked: `cloud_audit_emitter` had a closed set of 11 presets, none of which emitted these events, and its builders wrote no `requestParameters`. That change is now on `feature/api-cloud-audit-emitter-presets`: eight logging-service presets, with `requestParameters` on `aws` and `protoPayload.request` on `gcp`. The scenario itself, planned as SIM-CDR-029, is not authored yet: on 2026-11-11 no file on any branch carries that id, and the cloud-logging branch sits at the presets branch's tip. No lab cloud account, so it can never be lab-proven. |
| 11 | P1 | When "Hi, This Is IT" Comes Through Microsoft Teams | not scenario material | Hardening piece built around a simulated vignette. beh 0, IOCs 0, no Teams audit operation named. The MFA-approval step is already SIM-ITDR-002 and SIM-ITDR-008. |
| 12 | P1 | Threat Brief: Widespread Impact of the Axios Supply Chain Attack | **build now** | beh 11, IOCs 24/2/1, four published XQL hunting queries. The Linux second stage is published exactly (`python3 /tmp/ld.py <C2 URL>` under `nohup`, the POST body, a 60-second Base64-JSON beacon, four RAT verbs, the manifest-swap cleanup) and needs only curl and python3. Zero corpus hits for axios, plain-crypto-js or the C2; first T1204.005; first T1219 on a Linux developer endpoint. The npm-to-node first hop cannot run on the lab endpoint (no node) and stays staged. |
| 13 | P1 | #StopRansomware: Akira Ransomware, updated joint advisory | not scenario material | Near-duplicate. The indexed URL is a press release; the advisory behind it (AA24-109A) was read instead: beh 13, hashes 51, but all nine verbatim command lines are Windows, and the Linux and ESXi chain is already SIM-EDR-008, -011, -019 and -025. The Linux remainder is one encryptor flag set (`--encryption_percent`, `--fork`) and four hashes: a variant of SIM-EDR-025 step-05, not a chain. |
| 14 | P1 | NCSC advice following global targeting of Fortinet firewalls and VPN gateways | not scenario material | Press alert. beh 0, IOCs 0, no CVE ids. It adds nothing concrete to the FortiBleed brief in row 4. |
| 15 | P1 | Joint CSA: Improve Router Hygiene (FSB Center 16) | not scenario material | The CSA PDF (12 pages) was read: 0 IOCs, 0 command lines; the technical content is one paragraph (two Cisco OIDs, two file names, four port sets, two CVE ids). The victim is a router the lab lacks, and no emitter carries the SNMP or TFTP legs. |
| 16 | P1 | Tracing Digital Intent: New MacOS Tahoe 26 Artifact Discovered | not scenario material | A forensic data-source write-up, not an attack chain. The only command is the examiner's parse step; macOS Tahoe only; no emitter models the artifact. |
| 17 | P2 | Threat Actor Groups Tracked by Palo Alto Networks Unit 42 | not scenario material | Confirmed reference: actor taxonomy and alias list. |
| 18 | P2 | Introducing Unit 42's Attribution Framework | not scenario material | Confirmed reference: attribution methodology, no technical content. |
| 19 | P2 | Unit 42 Threat Bulletin, October 2025 | not scenario material | Confirmed reference: a digest. Every named topic is already in the corpus, and no linked item is a technical post. |
| 20 | P2 | 2026 World Cup: Discussing The World's Biggest Game's Attack Surface | not scenario material | Confirmed reference: a thematic assessment with no reproducible behaviour. |

### The three decisions held for Henry, and his answers (2026-11-11)

1. **Row 10, Blinding the Watchmen (with row 9 folded in).** Extend
   `core/eal_simulator/plugins/cloud_audit_emitter.py` with presets for stop
   logging, trail and sink deletion, log-destination deletion, key-swap
   impairment, log-object overwrite, rogue trail or sink creation and log
   redirection, plus `requestParameters` support and tests. No in-flight branch
   touches that file (checked against every remote branch on 2026-11-11). Two
   limits to accept before saying yes: the post gives no attacker order of
   operations, so the chain would be a composition; and for most techniques it
   names the CLI operation or IAM permission, so the exact audit-log event and
   field names would come from provider documentation, not from the post.
   **Answer: "Yes, separate branch (Recommended)".** The emitter presets are on
   `feature/api-cloud-audit-emitter-presets`; the scenario goes on
   `feature/scenarios-unit42-cloud-logging`, authored-only, with row 9 as one
   leg, and is not authored yet. Saying yes accepts the two limits above, as the question framed them.
   It is not lab-provable: the lab has no cloud account, and records POSTed to
   a collector do not arm the vendor analytics alerts the post maps to (the
   logging-service entry of `docs/reference/eal-plugin-catalog.md` on the
   presets branch, "Honest limits").
2. **Row 1, Screening Serpens.** Build a Windows chain that no host here can
   run, or leave it until a Windows lab host exists. The earlier answer to the
   same question (CL-STA-1062) was to drop it.
   **Answer: "Don't build now (Recommended)".** Deferred until the lab has a
   Windows host. Unlike CL-STA-1062 it is not dropped.
3. **Row 9, Universal Bucket Hijacking.** Only meaningful as part of decision 1.
   **Answer: "Don't build now (Recommended)"** for a standalone scenario. It is
   built only as the bucket-deletion leg of decision 1.

### Gaps the triage surfaced that these posts cannot ground

Each of these is absent from the corpus, and each needs a different source
than the post that raised it:

- A Microsoft Teams external-chat leg (row 11 names no audit operation).
- A payroll or HR SaaS direct-deposit change, and an external address added as
  an authentication method (row 3).
- Network-device configuration theft, T1602.001 and T1602.002 (row 15).
- DragonForce ransomware behaviour (row 5 names it with no detail).
- The DPRK IT-worker insider pattern, and DDoS used as extortion pressure
  (rows 2 and 7).
- Silk Typhoon, and techniques T1538, T1021.007 and T1555.006 (row 8).

### Stale entries in the index, not changed here

- The Screening Serpens note says T1574.014 "would be NEW MITRE technique
  coverage". SIM-MP-025 has carried it since the stitched-chain pass.
- `detection_scanner/.tasks/recurring-tasks.json` still lists Payroll Pirates
  as candidate `TTP-2026-0009`. On the evidence in row 3 that candidate has no
  groundable content.
- Row 13 is indexed by its press-release URL. The technical content is in
  advisory AA24-109A.
