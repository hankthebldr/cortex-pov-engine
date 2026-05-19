# Tool Adapter Framework — Integrating 100 Common Adversarial/Defensive Tools

> Author: Henry Reed (with Claude Opus 4.7) · 2026-05-19 · Status: draft, awaiting review

## 1. Problem

The Cortex differentiation moat (Phase 1–7 of the detection-engine review) closes the *content* gap: the engine now bridges scenarios to deployable BIOC/XQL/correlation artifacts. The next gap is the *execution surface*: a Domain Consultant running a POV needs to be able to trigger any of the well-known adversarial tools (Mimikatz, BloodHound, sqlmap, evilginx2, Caldera, …) from CortexSim and have the resulting signal validated against Cortex detections — not bash-script each one from memory in front of a customer.

Today the engine has five integration paths, all valid, none coherent:

| Path | Where it lives | What it does | Limitation |
|---|---|---|---|
| `TOOL_REGISTRY` (`core/tools/registry.py`) | static Python dict | Subprocess lifecycle for 5 in-tree services | Hand-edited; doesn't scale to 100 tools |
| IaC `content-library` module | `infra/modules/aws/content-library/content.yml` | Provisions tool repos on a jumpbox via cloud-init | Install-only; no execution wiring |
| EAL simulator plugins (`core/eal_simulator/plugins/`) | one Python file per plugin | YAML-declared outbound network detonations | Designed for traffic plugins, not arbitrary binaries |
| Scenario `external_tools[]` (per-YAML) | scenario step `command:` | Hand-rolled CLI invocations | No reusable knowledge; every scenario re-derives invocation, args, cleanup |
| In-tree custom toolkits (`sources/cortex-*`) | full submodule each | First-class differentiated detonators | Heavyweight; correct for the 4–5 high-value tools, not for the 100 |

The 100-tool list in the brief spans every one of those paths. We need a single **Tool Adapter** abstraction that knows where a tool lives, how to install it, how to invoke it for a given step, what cleanup it requires, and which detection plane its signal lands on — so scenarios reference adapters by name and the engine handles the rest.

## 2. Goals & non-goals

**Goals**

- Single declarative model (`ToolAdapter`) that scenarios + the orchestrator + the IaC generator + the push bundle generator all consume.
- A 5-tier integration model so a tool's tier dictates its delivery path without per-tool architecture decisions.
- Explicit dual-use safety gates: dangerous tools (C2 frameworks, credential dumpers) require operator consent at scenario launch — not at install.
- Adapter packs (`tools/packs/<tool>.yml`) live alongside the corpus, are validated at startup, and surface in the UI tool picker.
- A reference implementation covering ~12 tools across the 5 tiers, proving the model end-to-end.

**Non-goals**

- Re-architecting EAL plugins. They keep their existing shape; the new Tool Adapter is a *peer* abstraction for binary/script tools, not a replacement for traffic plugins.
- Auto-discovering tool capabilities. Adapters are authored, not scraped.
- Shipping all 100 tools in V1. The framework lands first; tool packs land in waves after.
- Cross-platform parity beyond Linux+Windows. macOS / IoT / mobile tools are tier-5 (external) only.

## 3. The 5-tier integration model

Every tool in the brief slots into exactly one tier. The tier is the *contract* between the tool and CortexSim — install path, execution path, consent gate, and shelf life are all functions of the tier.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                                                             │
│   TIER 1  in-tree                  ← cortex-prompt-attacker, vulnerable-llm │
│   TIER 2  submodule                ← signalbench, atomic-red-team, impacket │
│   TIER 3  IaC-provisioned          ← Mimikatz, BloodHound, Empire, Sliver   │
│   TIER 4  runtime-fetched          ← nuclei, sqlmap, gobuster, masscan      │
│   TIER 5  external-only            ← Ghidra, Wireshark, IoT hardware tools  │
│                                                                             │
│   Higher tier  ⇒  more integration, more maintenance, more value            │
│   Lower tier   ⇒  faster to add, less liability                             │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Tier 1 — in-tree (`sources/cortex-*`)

Differentiated tools we authored. Versioned in this repo. Used when no open-source equivalent produces the exact signal Cortex needs to detect (e.g. AIRS LLM01-10 probe pack, browser DLP detonator, malicious agentic pack).

- **Adapter location:** `tools/packs/cortex-<name>.yml`
- **Install:** built by `install.sh` from in-tree source.
- **Invoke:** direct subprocess via `TOOL_REGISTRY` `run_template`.
- **Consent:** none (we own the safety surface).
- **Eligible candidates from the 100-list:** none — these are net-new originals.

### Tier 2 — git submodule (`sources/<tool>`)

Open-source tools where (a) we depend on a specific version, (b) they ship as source we build locally, (c) the project would benefit from local patching during a POV. Reuses the existing submodule pattern.

- **Adapter location:** `tools/packs/<tool>.yml`
- **Install:** `git submodule update --init` + `build_cmd` from adapter.
- **Invoke:** direct subprocess via `TOOL_REGISTRY` `run_template`.
- **Consent:** none for benign tooling (atomic-red-team, signalbench); explicit lab-only consent for dual-use (impacket, BloodHound CLI).
- **Eligible candidates from the 100-list:** atomic-red-team, impacket, BloodHound, scapy, Yara rules, mocktaxii, ackbarx, signalbench (already in tree).

### Tier 3 — IaC-provisioned (`infra/modules/.../content-library`)

Tools that need a *target environment* (Windows AD lab, macOS box, Kubernetes cluster) — we install them on the provisioned VM, not the jumpbox running CortexSim. Already supported by the IaC content-library module.

- **Adapter location:** `tools/packs/<tool>.yml`  + `infra/modules/<provider>/content-library/content.yml` entry
- **Install:** cloud-init via `scripts/jumpbox/install-content.sh` on the target VM.
- **Invoke:** via pull-mode agent on the target — the agent receives a task that references the adapter; agent's identity harness wraps the invocation.
- **Consent:** all entries gated by adapter `safety_class`. C2 frameworks require explicit `--authorize-c2` flag on the IaC `generate` call.
- **Eligible candidates from the 100-list:** Mimikatz, Rubeus, PrintSpoofer, KrbRelayUp, pypykatz, Empire, Sliver, Havoc, Cobalt Strike kits, PurpleSharp, APTSimulator, mimipenguin (already used), CALDERA agents, evilginx2, gophish, SET, frp, BloodHound (Win client), Hayabusa.

### Tier 4 — runtime-fetched (curl/pip/go install at step time)

Tools that are quick to install and need no environment setup. The adapter declares the install command; the orchestrator fetches at first use and caches per host.

- **Adapter location:** `tools/packs/<tool>.yml`
- **Install:** `install_inline: true` in adapter; resolved at task dispatch.
- **Invoke:** direct subprocess on jumpbox or pull-mode agent.
- **Consent:** none for scanners (nuclei, nikto); none for known-safe utilities; lab-only for offensive (commix, sqlmap when targeting non-allowlist).
- **Eligible candidates from the 100-list:** nuclei, nmap, masscan, sqlmap, gobuster, feroxbuster, dirsearch, nikto, WhatWeb, recon-ng, commix, trivy, prowler, scoutsuite, kube-bench, kubescape, gitleaks, pacu, scoutsuite, cloudsplaining, jadx, ILSpy (Windows agent), vt-cli, SecLists (data only), CrossLinked, CredKing.

### Tier 5 — external-only (out of scope for execution)

Tools that are interactive (GUI analyst workbenches), require dedicated lab hardware (radio/IoT/SDR), or are non-automatable. The corpus *references* these as analysis tools so reports can recommend "use Ghidra to inspect the dropped binary", but the engine never invokes them.

- **Adapter location:** `tools/packs/<tool>.yml` with `tier: external, no_invoke: true`
- **Install:** none (informational only).
- **Invoke:** never.
- **Eligible candidates from the 100-list:** Ghidra, radare2, Cutter, IDA-class tooling, Wireshark (the GUI; tshark is tier 4), Cuckoo / CAPEv2 (own infrastructure; integrate later as remote sandbox API), Inetsim, FLARE-VM, Slips, viper, retoolkit, kismet, aircrack-ng (lab hardware), aircheck/krack PoCs, routersploit, attify-badge, IoT-Security-Test-Suite, coppersmith, ptarget, hayabusa (already covered), DidierStevensSuite, pefile (Python module — used by adapter code, not invoked), cuckoosandbox.

## 4. Tool Adapter schema

One YAML file per tool under `tools/packs/<tool>.yml`. Validated at startup against `tools/packs/_schema.yml` (mirroring `scenarios/_schema.yml`).

```yaml
# tools/packs/_schema.yml — reference, not a runnable adapter
adapter_id: "TOOL-MIMIKATZ"
# (required) Stable identifier. Format: TOOL-<UPPER-NAME>. Never reused.

name: "Mimikatz"
# (required) Human-readable display name.

version: "2.2.0"
# (required) Pinned upstream version.

tier: 3
# (required) Integration tier: 1 | 2 | 3 | 4 | 5

category: "credential-access"
# (required) Top-level category from a controlled vocabulary:
#   adversary-simulation | c2-framework | sandbox | reverse-engineering
#   | network-scan | web-app | identity-credential | cloud-container
#   | social-engineering | wireless-iot | analyst-workbench

upstream:
  repo: "https://github.com/gentilkiwi/mimikatz"
  # (required) Canonical source URL.
  license: "CC BY 4.0"
  # (required) SPDX-style license identifier.
  attribution: "Benjamin Delpy"
  # (required) Project owner — surfaced in POV reports for proper credit.

safety_class: "dual-use-lab-only"
# (required) Determines consent gating at scenario launch.
# One of:
#   safe                — benign scanner / reporting tool (nmap, nuclei)
#   dual-use-lab-only   — offensive capability that needs lab consent
#                         (Mimikatz, BloodHound, sqlmap)
#   c2-framework        — full C2; requires --authorize-c2 (Sliver, Empire)
#   destructive         — irreversible without cleanup
#                         (must declare cleanup block; engine enforces it)

cortex_signal:
  # (required) Which Cortex plane this tool's signal lands on. Drives
  # the UI tool picker's plane filter and the auto-suggest when a scenario
  # is being authored.
  planes: [EDR, ITDR]
  expected_techniques: ["T1003.001", "T1558.003"]
  # (optional) MITRE techniques this tool exercises out-of-the-box.

install:
  tier_path: 3
  # (required) Where this tool installs in tier 3 IaC.
  iac_module: "edr"
  content_library_entry:
    name: "mimikatz"
    repo: "gentilkiwi/mimikatz"
    install: "git-clone"
    install_path: "C:\\tools\\mimikatz"
    build_cmd: "msbuild mimikatz.sln /p:Configuration=Release /p:Platform=x64"

invoke:
  # (required) How to run the tool. Identity harness wraps this at dispatch.
  target_platform: "windows"
  binary: "C:\\tools\\mimikatz\\x64\\mimikatz.exe"
  run_template: '"{binary}" "{commands}" "exit"'
  default_args:
    commands: "privilege::debug sekurlsa::logonpasswords"
  identity_required: "administrator"

cleanup:
  commands:
    - 'Remove-Item -Force "C:\\tools\\mimikatz\\output.txt" -ErrorAction SilentlyContinue'

ttp_refs:
  # (optional) TTP cards in detection_scanner/ttps/ that should reference
  # this adapter. Used by the new ScenarioBrowser UI filter
  # ("show scenarios using Mimikatz").
  - "TTP-2026-0002"
  - "TTP-2026-0004"

equivalents:
  # (optional) Adapters in the same category that produce overlapping
  # signal. Used by the variant runner (Phase 3 of the detection plan) to
  # rotate detonators within a scenario family — naive=Mimikatz,
  # advanced=pypykatz, apt=nanodump.
  - "TOOL-PYPYKATZ"
  - "TOOL-NANODUMP"

deprecated_by: null
# (optional) When set, the engine prefers the named adapter; this one is
# kept for historical scenarios but hidden from the picker.

author: "Henry Reed"
created: "2026-05-19"
last_updated: "2026-05-19"
```

### Validation rules (enforced at startup)

1. `adapter_id` matches `^TOOL-[A-Z0-9-]+$` and is unique.
2. `tier` in `{1..5}`. Tier 5 forbids an `invoke` block. Tier 3 requires `install.iac_module`. Tier 4 requires `install.runtime_install_command`.
3. `safety_class == c2-framework` requires the scenario to set `c2_authorized: true` at launch.
4. `safety_class == destructive` requires non-empty `cleanup.commands`.
5. Every `ttp_refs[]` entry resolves in the TTP catalog (warn-not-fail, same pattern as Phase 1).
6. `cortex_signal.planes[]` values are a subset of the plane enum.
7. Every adapter's referenced `iac_module` exists under `infra/modules/`.

## 5. Engine changes

### Files to add

| File | Phase | Purpose |
|---|---|---|
| `tools/packs/_schema.yml` | 1 | Reference schema |
| `core/tools/adapter_loader.py` | 1 | Pydantic loader, mirrors `scenario_loader.py` |
| `core/tools/adapter_catalog.py` | 1 | In-memory catalog, mirrors `engine/ttp_catalog.py` |
| `core/api/tools.py` (extend) | 1 | `GET /api/tools/adapters` returns the catalog for the UI picker |
| `tools/packs/*.yml` | 1–N | One per tool; ship 12 at launch |

### Files to modify

| File | Phase | Change |
|---|---|---|
| `core/main.py` | 1 | Load adapter catalog before scenarios; warn on dangling adapter refs from scenarios |
| `core/engine/scenario_loader.py` | 1 | `external_tools[]` entries can carry `adapter_ref: TOOL-NMAP` instead of inline `source/type`; loader resolves |
| `core/engine/orchestrator.py` | 1 | At task dispatch, replace `{adapter_ref}` placeholders in step `command:` with resolved `run_template` |
| `core/engine/push_generator.py` | 1 | Push bundles include the adapter's install script for tier-4 tools so the bundle is self-contained |
| `core/engine/infra_generator.py` | 1 | Pull tier-3 adapters' `install.content_library_entry` into the bundled `content.yml` automatically |
| `core/api/scenarios.py` | 2 | `POST /api/scenarios/{id}/run` rejects launches whose scenario uses a `c2-framework` adapter without `c2_authorized: true` |
| `ui/src/components/ScenarioBrowser.jsx` | 2 | New filter chip: "tools used"; clicking shows only scenarios that reference the picked adapter |
| `core/engine/report_generator.py` | 2 | POV report's "Tools used" section enumerates every adapter the run touched with version + license — table-stakes audit/compliance evidence |

### Wire diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                                                                         │
│   tools/packs/*.yml                                                     │
│         │                                                               │
│         ▼ load at startup                                               │
│   adapter_catalog (in-memory)                                           │
│         │                                                               │
│         ├──► scenario_loader  ─── warn on dangling adapter_ref          │
│         │                                                               │
│         ├──► orchestrator     ─── inline run_template at dispatch       │
│         │                          + enforce safety_class consent       │
│         │                                                               │
│         ├──► push_generator   ─── emit install script for tier-4 tools  │
│         │                          into the self-contained bundle       │
│         │                                                               │
│         ├──► infra_generator  ─── auto-include content-library entry    │
│         │                          for tier-3 tools the scenario needs  │
│         │                                                               │
│         ├──► report_generator ─── "Tools used" section + licence/attrib │
│         │                                                               │
│         └──► api/tools/adapters ── UI picker, search, filter            │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

## 6. The 100-tool list, placed

The brief lists 100 tools across 10 categories. Placement below applies the tier model — and explicitly flags which ones we should **not** integrate (duplicate of existing, out of scope, or higher risk than value).

Notation: 🟢 high-leverage add · 🟡 medium · 🔴 defer/never · ✅ already in repo

### Adversary Simulation & Purple Teaming (10)
| Tool | Tier | Verdict | Note |
|---|---|---|---|
| mitre/caldera | 3 | 🟢 | Adapter wraps Caldera ability YAMLs as scenarios via Phase 7 loader |
| redcanaryco/atomic-red-team | 2 | ✅ | Already submoduled; build Phase 7 loader on top |
| mvelazc0/PurpleSharp | 3 | 🟢 | Windows telemetry-specific; complements signalbench |
| NextronSystems/APTSimulator | 3 | 🟡 | One-shot script; lower fidelity than atomic; ship as adapter, low priority |
| uber-common/metta | — | 🔴 | Vagrant-based; redundant with IaC + atomic |
| redcanaryco/chain-reactor | 2 | 🟡 | Linux-only chain runner; submodule; useful for EDR Linux scenarios |
| 0xsp-Mongoose | 4 | 🔴 | Niche; defer |
| scythe-io/compound-actions | 2 | 🟢 | YAML adversary plans — perfect input for the Phase 4 scraper |
| praetorian-inc/purple-team-exercise-framework | 5 | 🟡 | Docs-only — link from POV report as reference |
| civilsphere/Slips | 5 | 🔴 | IDS, not generator — out of scope |

### C2 Frameworks (10)
| Tool | Tier | Verdict | Note |
|---|---|---|---|
| BC-SECURITY/Empire | 3 | 🟢 | `safety_class: c2-framework`; gate on `--authorize-c2` |
| Cobalt-Strike kits | — | 🔴 | Commercial licence; ship attribution only |
| SliverArmory/Sliver | 3 | 🟢 | Same gate as Empire; preferred over Cobalt for OSS POVs |
| Starkiller | 3 | 🟡 | Frontend for Empire; ship together |
| BishopFox/Havoc | 3 | 🟡 | Same family as Sliver/Empire; pick one |
| shad0w | 3 | 🔴 | Lower-maintained; pick Sliver |
| Yamato-Security/Hayabusa | 5 | 🟡 | Defensive log scanner; tier-5 reference |
| fatedier/frp | 3 | 🟡 | Pivot/proxy infra; useful for NDR tunnel scenarios |
| Ne0nd0g/merlin | 3 | 🔴 | Pick Sliver |
| pupy | 3 | 🔴 | Less maintained; pick Sliver |

C2 recommendation: ship **Sliver + Empire (+ Starkiller)** as the OSS C2 pair, gate hard. Skip the rest to avoid maintaining four overlapping frameworks.

### Malware Sandboxing & Dynamic Analysis (10)
| Tool | Tier | Verdict | Note |
|---|---|---|---|
| cuckoosandbox/cuckoo | 5 | 🔴 | Separate platform; future "remote sandbox API" tier-5 integration |
| CAPEv2 | 5 | 🟡 | Same — track for future remote-sandbox tier |
| elastic/detonate | 5 | 🟡 | Architecturally similar to our orchestrator; cross-reference, don't fold in |
| kevoreilly/CAPEv2 | 5 | 🟡 | Same |
| buffer/kinshasa | 5 | 🔴 | Lightweight; skip |
| viper-framework/viper | 5 | 🔴 | Sample mgmt; outside scope |
| PracticalMalwareAnalysis-Labs | 5 | 🟡 | Sample data for AIRS/EDR test rigs; reference only |
| retoolkit | 5 | 🔴 | Workstation provisioning tool |
| VirusTotal/vt-cli | 4 | 🟢 | IOC enrichment for the cortex-scraper (Phase 4) and POV report links |
| fireeye/flare-vm | 5 | 🔴 | Analyst workstation |

### Reverse Engineering & Static Analysis (10)
All tier-5 except YARA (tier 2, useful as detection content for static IoC scenarios).

| Tool | Tier | Verdict |
|---|---|---|
| Ghidra, radare2, Cutter, ILSpy, jadx, DidierStevensSuite, pefile, Inetsim | 5 | 🔴 reference only |
| Yara-Rules/rules + VirusTotal/yara | 2 | 🟢 — adapter ships YARA engine; KOI plane scenarios pattern-match against staged malicious agentic pack |

### Network Vulnerability Scanning (10)
| Tool | Tier | Verdict |
|---|---|---|
| nmap | 4 | 🟢 |
| projectdiscovery/nuclei | 4 | 🟢 — templated; high-leverage for ASM plane |
| recon-ng | 4 | 🟡 |
| nikto | 4 | 🟡 |
| discover | 4 | 🔴 — duplicates nmap+recon |
| shadowserver | — | 🔴 — config feeds, not a tool |
| masscan | 4 | 🟢 — ASM plane internet sweeps |
| SecLists | 2 | 🟢 — data submodule, no `invoke` |
| WhatWeb | 4 | 🟡 |
| Sn1per | 4 | 🔴 — meta-tool; replicates pieces we already have |

### Web Application & API (10)
| Tool | Tier | Verdict |
|---|---|---|
| sqlmap | 4 | 🟢 |
| w3af | 4 | 🔴 — abandoned upstream |
| PayloadsAllTheThings | 2 | 🟢 — payload corpus submodule, no invoke |
| DVWA | 3 | 🟢 — target lab, ship as tier-3 IaC module addition |
| feroxbuster | 4 | 🟢 |
| dirsearch | 4 | 🔴 — pick feroxbuster |
| CMSeeK | 4 | 🟡 |
| Chiron | 4 | 🟡 |
| gobuster | 4 | 🟢 — common, fast |
| commix | 4 | 🟡 |

### Active Directory & Internal Pivoting (10)
| Tool | Tier | Verdict |
|---|---|---|
| mimikatz | 3 | 🟢 — flagship LSASS detonator |
| BloodHound | 3 | 🟢 — paired with SharpHound on the target |
| impacket | 2 | ✅ |
| bloodyAD | 3 | 🟡 |
| Rubeus | 3 | 🟢 — Kerberoast detonator |
| PowerLessShell | 3 | 🔴 — narrow, replaced by built-in PS evasion |
| KrbRelayUp | 3 | 🟡 |
| pypykatz | 4 | 🟢 — Python LSASS variant; pairs with mimikatz as the `advanced` evasion grade |
| Bypass-Shield | 3 | 🔴 |
| PrintSpoofer | 3 | 🟡 |

### Cloud & Container (10)
| Tool | Tier | Verdict |
|---|---|---|
| prowler | 4 | 🟢 |
| trivy | 4 | 🟢 |
| skyark | 4 | 🟡 |
| pacu | 4 | 🟢 — AWS exploit framework; gate as `dual-use-lab-only` |
| kube-bench | 4 | 🟢 |
| scoutsuite | 4 | 🟢 |
| GitGot | 4 | 🟡 |
| gitleaks | 4 | 🟢 |
| kubescape | 4 | 🟢 |
| cloudsplaining | 4 | 🟡 |

### Social Engineering & Phishing (10)
| Tool | Tier | Verdict |
|---|---|---|
| gophish | 3 | 🟢 |
| SET | 3 | 🟢 |
| evilginx2 | 3 | 🟢 — gate as `dual-use-lab-only`; pairs with `cortex-identity-attacker` (Phase 6) |
| phishery | 3 | 🟡 |
| SessionPhish | 4 | 🟡 |
| CredKing | 4 | 🟢 — password spray with IP rotation; tier-4 |
| Tokenvator | 3 | 🟡 |
| Email-Spoofer | 4 | 🟡 |
| CrossLinked | 4 | 🟡 |
| SocialBox | 4 | 🔴 |

### Wireless / Hardware / IoT (10)
All tier-5 except Wireshark CLI (`tshark`, tier-4):

| Tool | Tier | Verdict |
|---|---|---|
| Wireshark / tshark | 4 | 🟢 — tshark for PCAP capture in NDR scenarios |
| scapy | 2 | 🟢 — Python lib for packet forging; submodule |
| nmap, masscan | (covered above) | |
| krackattacks | 5 | 🔴 — lab hardware |
| pTarget, kismet, aircrack-ng | 5 | 🔴 |
| coppersmith, attify-badge, IoT-Security-Test-Suite, routersploit | 5 | 🔴 — no IoT/wireless plane in CortexSim |

## 7. Phased rollout

```
┌────────────────────────────────────────────────────────────────────────────┐
│                                                                            │
│   Phase A: framework            Phase B: tool packs        Phase C: scale  │
│   ───────────────               ──────────────────         ──────────────  │
│   adapter schema                ship 12 reference          full coverage   │
│   + catalog + loader            adapters (≥2 per tier)     of 🟢 verdicts  │
│   + orchestrator wiring         + their TTP cards          (~40 tools)     │
│   + safety gates                + their scenarios          + UI picker     │
│                                                            + report block  │
│                                                                            │
│        2-3 dev-days                  5 dev-days              ~3 weeks      │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘
```

### Phase A — framework only (lands first)

Deliverable: schema + catalog + loader + orchestrator wiring + safety gates. Zero adapter packs shipped. Existing scenarios keep working unchanged (backward compatible — `external_tools` without `adapter_ref` falls back to legacy path).

Done when: catalog loads cleanly with zero adapters; `pytest tests/tools/` covers the empty-corpus + dangling-ref + safety-gate paths.

### Phase B — 12 reference adapters

One per integration tier, plus the highest-leverage 🟢 entries from the inventory. Suggested seed set:

| Tool | Tier | Why first |
|---|---|---|
| `TOOL-NMAP` | 4 | Smallest possible tier-4 example |
| `TOOL-NUCLEI` | 4 | Highest-leverage scanner; templates dovetail with TTP cards |
| `TOOL-SQLMAP` | 4 | Cloud-app plane validation |
| `TOOL-TRIVY` | 4 | CDR plane container-scan flow |
| `TOOL-MASSCAN` | 4 | ASM plane sweep |
| `TOOL-MIMIKATZ` | 3 | Flagship credential dumper |
| `TOOL-BLOODHOUND` | 3 | AD path discovery |
| `TOOL-SLIVER` | 3 | C2-gated reference for the consent path |
| `TOOL-EVILGINX2` | 3 | ITDR + identity-attacker pairing |
| `TOOL-CALDERA` | 3 | Validates the ability-loader (Phase 7) integration |
| `TOOL-SCAPY` | 2 | Already-submoduled equivalent |
| `TOOL-ATOMIC-RED-TEAM` | 2 | Codifies the existing submodule properly |

Each adapter ships with: 1 TTP card in `detection_scanner/ttps/` (Phase 2 generator emits the skeleton), 1 scenario YAML that exercises it, and an integration test under `tests/tools/test_<adapter>.py`.

### Phase C — fan out

Author the remaining 🟢 + 🟡 adapters in waves. Cadence: ~4 per week. Track in the same RUNBOOK that drives the corpus.

## 8. Reuse — don't re-invent

- `core/engine/ttp_catalog.py` (just shipped in PR #32) is the load-and-warn template for `adapter_catalog.py`.
- `core/engine/scenario_loader.py` Pydantic + dangling-ref-warning pattern is the template for `tools/packs/_schema.yml` validation.
- `infra/modules/aws/content-library/content.yml` already speaks the "install on target VM" language — Phase A reuses the same shape under `install.content_library_entry`.
- `core/eal_simulator/plugins/` is the parallel abstraction for traffic plugins; adapters are the binary-tool equivalent. They stay separate but follow the same registry-on-first-use pattern.
- `core/engine/report_generator.py` already has a per-result rendering loop — the "Tools used" section is one helper added there.

## 9. Risks

- **C2 frameworks in tier 3 are a footgun.** Mitigation: hard gate at scenario launch (`c2_authorized=true` required), default off, audit log every authorization, never auto-stage from a push bundle.
- **Tool drift.** Adapters pin a version; upstream changes break invocation. Mitigation: weekly canary in CI (Phase B+) runs `--version` against every tier-2/3/4 adapter and fails the build on a parse mismatch.
- **Licence pollution.** Some tools (Cobalt Strike kits) are commercial; YARA-Rules has CC-BY. Mitigation: adapter `upstream.license` is required, the report generator surfaces every licence per run, and CI fails on `license: unknown`.
- **Maintenance debt.** 40+ tier-4 adapters is a lot of CLI surface. Mitigation: tier-4 fetches are idempotent — if upstream changes break `run_template`, only one adapter file changes, not the engine.

## 10. Verification

End-to-end Phase B verification loop:

1. DC opens UI tool picker → filters by "category: identity-credential" → sees Mimikatz + BloodHound + pypykatz + Rubeus with version + licence + safety class.
2. DC selects a scenario referencing `TOOL-MIMIKATZ`; UI shows the lab-only consent prompt.
3. DC accepts; orchestrator dispatches the step with the adapter's `run_template`; pull-mode agent on the AD lab box executes via identity harness.
4. Phase 5 XSIAM validator confirms the LSASS BIOC fired; report includes the Mimikatz adapter version in the "Tools used" section + a licence-attribution footer.

That's the deliverable.
