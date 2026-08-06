# Tool Adapter Catalog — Exhaustive Reference

> **Generated:** 2026-06-07 · archivist pass over `tools/packs/*.yml`,
> `core/tools/adapter_loader.py`, `core/tools/adapter_catalog.py`, and
> `scenarios/`. This is the **complete, enumerated** inventory of every adapter
> pack the engine knows how to load — not a sample. For the narrative/canonical
> doc see [`docs/tool-adapters.md`](../tool-adapters.md); for the design intent
> see the spec at
> [`docs/superpowers/specs/2026-05-19-tool-adapter-framework-design.md`](../superpowers/specs/2026-05-19-tool-adapter-framework-design.md);
> for pack-authoring see [`tools/packs/README.md`](../../tools/packs/README.md).

> **STALENESS WARNING (updated 2026-08-06).** Every count in §0 and every table
> below was enumerated on **2026-06-07, when there were 69 packs**. There are now
> **91**. Treat the per-tier tables as a snapshot, not ground truth. §9 is
> current; §§0–8 are not.
>
> **Measured on the tree 2026-08-06 — this is the ground truth:**
>
> | tier | doc says | actually |
> |---|---:|---:|
> | 1 in-tree | 3 | **3** |
> | 2 submodule | 8 | **1** (`TOOL-ATOMIC-RED-TEAM` only) |
> | 3 IaC-provisioned | 20 | **20** |
> | 4 runtime-fetched | 27 | **56** |
> | 5 external/reference | 11 | **11** |
> | **total** | 69 | **91** |
>
> The tier-4 table below therefore lists **27 of 56** packs, and none of the 8
> shelf-backed ones (`TOOL-LINPEAS`, `TOOL-PSPY`, `TOOL-SUID3NUM`, `TOOL-LSE`,
> `TOOL-LINENUM`, `TOOL-DEEPCE`, `TOOL-TRAITOR`, `TOOL-AMICONTAINED`) — see §9.1
> for those. Individual rows have also drifted: `TOOL-CREDKING`'s licence is now
> `LicenseRef-NONE`, not `LicenseRef-ustayready`, and `TOOL-TRIVY`'s pinned
> release URL is **404 upstream** (see [`payload-shelf.md` §11.3](payload-shelf.md)).
>
> **Consequence worth naming: the tier 2 → 4 re-tiering weakened a CI gate.**
> `scripts/check-adapter-sources.sh` **FAILs** on a missing tier-2 `source_path`
> (the GAP-ADAPT-01 guard) but only **WARNs** on tier-4. With one tier-2 pack
> left, that gate now hard-guards exactly one adapter and emits 51 warnings:
> `Summary: PASS=7 WARN=51 FAIL=0`. That is a real reduction in enforcement, and
> nothing announced it — the re-tiering was correct per pack, but the gate's
> blast radius was never re-examined.
>
> Count, never quote — the snippet is in
> [`tools/packs/README.md` § Counting](../../tools/packs/README.md).

## 0. TL;DR numbers (verified by enumeration, 2026-06-07 — SEE STALENESS WARNING)

| Metric | Value | Notes |
|---|---|---|
| Adapter pack files on disk | **69** | `tools/packs/*.yml` minus `_schema.yml` (70 files total) |
| Distinct `adapter_id`s | **69** | no duplicate ids |
| Tier 1 (in-tree) | 3 | cortex-prompt-attacker, cortex-browser-attacker, cortex-agentic-pack |
| Tier 2 (submodule) | 8 | atomic-red-team, chain-reactor, impacket, payloadsallthethings, scapy, scythe-compound-actions, seclists, yara |
| Tier 3 (IaC-provisioned) | 20 | |
| Tier 4 (runtime-fetched) | 27 | |
| Tier 5 (external/reference, `no_invoke`) | 11 | |
| Safety `safe` | 33 | |
| Safety `dual-use-lab-only` | 32 | |
| Safety `c2-framework` | 4 | Sliver, Empire, Starkiller, Havoc |
| Safety `destructive` | 0 | (none currently; schema supports it) |
| Adapters referenced by ≥1 scenario | **13** | |
| Adapters referenced by ZERO scenarios (orphans) | **56** | see §5 |
| Dangling `adapter_ref` (scenario → missing adapter) | **0** | see §6 |
| Scenarios with an `adapter_ref` | 31 step-lines across 28 scenario files | see §4 |

> **Pain-point callout:** the headline number (69 packs) is real, but **81%
> of the catalog (56 of 69 packs) is never referenced by any scenario.** The
> framework fanned out far ahead of scenario wiring. Most orphans are Phase-C
> "reference" packs (tagged `phase-c`) authored to satisfy the design spec's
> 100-tool inventory rather than to drive a live scenario. See §5 + the gap list.

---

## 1. The 5-tier integration model

The **tier** is the contract between a tool and CortexSim. It dictates *where the
binary comes from*, *how it gets installed*, and *how the engine invokes it*. The
loader (`adapter_loader.py`) enforces tier-specific install requirements at boot;
invalid packs are logged and excluded — they never crash startup.

| Tier | Meaning | Install source | Invocation path | Loader requirement |
|---|---|---|---|---|
| **1** | In-tree (`sources/cortex-*`) — we own the source | `install.sh` builds from `source_path` | direct subprocess on jumpbox | must declare `install.source_path` + an `invoke` block |
| **2** | Git submodule — pinned OSS under `sources/<tool>` | `git submodule` + `build_cmd` | direct subprocess | must declare `install.source_path` + an `invoke` block |
| **3** | IaC-provisioned — installed on the **target VM** via cloud-init / content-library | the named `install.iac_module` Terraform module | pull-mode agent + identity harness on the target | must declare `install.iac_module` + an `invoke` block |
| **4** | Runtime-fetched — installed at task dispatch time | `install.runtime_install_command` (apt/pip/curl/go/git) | subprocess on jumpbox or agent | must declare `install.runtime_install_command` + an `invoke` block |
| **5** | External-only — reference material, **never executed** | none | none — `no_invoke` | must **NOT** carry an `invoke` block |

**Loader-enforced validation rules** (from `adapter_loader.py`, the source of truth):

1. `adapter_id` matches `^TOOL-[A-Z0-9-]+$` and is unique across all packs.
2. `tier` ∈ {1,2,3,4,5}. Tier 5 **forbids** an `invoke` block. Tiers 1–4 **require** one.
3. Tier 3 requires `install.iac_module`. Tier 4 requires `install.runtime_install_command`. Tiers 1–2 require `install.source_path`.
4. `safety_class == destructive` requires a non-empty `cleanup.commands` list (engine refuses to dispatch otherwise).
5. `cortex_signal.planes[]` must be a subset of the plane enum `{EDR, CDR, NDR, ITDR, CLOUD_APP, ANALYTICS, AI_ACCESS, AIRS, BROWSER, KOI}`.
6. `upstream.license` is **required** and may not be `""`, `unknown`, or `tbd` (audit-trail enforcement).
7. Every key in `invoke.default_args` must appear as a `{placeholder}` in `invoke.run_template` (no orphan defaults).
8. `category` ∈ `{adversary-simulation, c2-framework, sandbox, reverse-engineering, network-scan, web-app, identity-credential, cloud-container, social-engineering, wireless-iot, analyst-workbench}`.
9. `invoke.target_platform` ∈ `{linux, windows, macos, k8s, any}`.
10. `invoke.identity_required` is a free string today (the harness maps it; no enum enforced in the loader).

### Consent / safety classes (gates applied at launch by the orchestrator)

| Safety class | Launch gate | Consent flag in `POST /api/run` |
|---|---|---|
| `safe` | none | — |
| `dual-use-lab-only` | lab consent required | `consent.simulation_authorized: true` |
| `c2-framework` | hard gate; never auto-staged from a push bundle | `consent.c2_authorized: true` |
| `destructive` | must declare non-empty `cleanup.commands`; engine enforces cleanup execution | (cleanup-enforced, no separate flag) |

`adapter_catalog.requires_consent(id)` returns the consent kind (or `None`). The
orchestrator maps `c2-framework → c2_authorized`, `dual-use-lab-only →
simulation_authorized`. The ③ Launch UI shows the consent prompt and blocks the
Launch button until the matching flag is set.

---

## 2. Master adapter table (ALL 69 packs)

Columns: **id · name · tier · plane(s) · safety · install method · invoke template (summary) · iac_module · gated? · upstream**.
Sorted by tier, then id. `gated?` = the consent flag the orchestrator demands.

### Tier 1 — in-tree (3)

| id | name | plane(s) | safety | install | invoke (summary) | iac_module | gated? | upstream |
|---|---|---|---|---|---|---|---|---|
| TOOL-CORTEX-PROMPT-ATTACKER | Cortex Prompt Attacker | AIRS | safe | `source_path: sources/cortex-prompt-attacker` (`pip install -e .`) | `cortex-prompt-attacker run --probes {probes} --target-url {target_url} --out {out}` | — | none | hankthebldr/cortex-pov-engine · LicenseRef-Internal |
| TOOL-CORTEX-BROWSER-ATTACKER | Cortex Browser Attacker | BROWSER | safe | `source_path: sources/cortex-browser-attacker` (`pip install -e .[playwright]`) | `cortex-browser-attacker run --plan {plan} --out {out}` | — | none | hankthebldr/cortex-pov-engine · LicenseRef-Internal |
| TOOL-CORTEX-AGENTIC-PACK | Cortex Malicious Agentic Pack | KOI | safe | `source_path: sources/cortex-malicious-agentic-pack` | `python3 -m scripts.eal_simulator.cli run {campaign} --live` | — | none | hankthebldr/cortex-pov-engine · LicenseRef-Internal |

### Tier 2 — git submodule (8)

| id | name | plane(s) | safety | install | invoke (summary) | iac_module | gated? | upstream |
|---|---|---|---|---|---|---|---|---|
| TOOL-ATOMIC-RED-TEAM | Atomic Red Team | EDR | dual-use-lab-only | `source_path: sources/atomic-red-team` | `{invoker} {test_id} -PathToAtomicsFolder {atomics_path} {extra_args}` | — | simulation_authorized | redcanaryco/atomic-red-team · MIT |
| TOOL-CHAIN-REACTOR | chain-reactor | EDR | dual-use-lab-only | `source_path: sources/chain-reactor` (`make`) | `{binary} {recipe}` | — | simulation_authorized | redcanaryco/chain-reactor · MIT |
| TOOL-IMPACKET | Impacket | ITDR | dual-use-lab-only | `source_path: sources/impacket` (`pip install .`) | `{tool} {args}` | — | simulation_authorized | fortra/impacket · Apache-1.1 |
| TOOL-PAYLOADSALLTHETHINGS | PayloadsAllTheThings | BROWSER | safe | `source_path: sources/PayloadsAllTheThings` | `ls {payload_dir}` | — | none | swisskyrepo/PayloadsAllTheThings · MIT |
| TOOL-SCAPY | Scapy | NDR | dual-use-lab-only | `source_path: sources/scapy` (`pip install -e .`) | `python3 {script_path}` | — | simulation_authorized | secdev/scapy · GPL-2.0 |
| TOOL-SCYTHE-COMPOUND-ACTIONS | SCYTHE compound-actions | EDR | dual-use-lab-only | `source_path: sources/scythe-community-threats` | `python3 {runner} {plan}` | — | simulation_authorized | scythe-io/community-threats · LicenseRef-Community |
| TOOL-SECLISTS | SecLists | NDR | safe | `source_path: sources/SecLists` | `ls {wordlist_dir}` | — | none | danielmiessler/SecLists · MIT |
| TOOL-YARA | YARA | KOI | safe | `source_path: sources/yara` (`./bootstrap.sh && ./configure && make`) | `{binary} {rules} {target}` | — | none | VirusTotal/yara · BSD-3-Clause |

### Tier 3 — IaC-provisioned (20)

| id | name | plane(s) | safety | iac_module | invoke (summary) | platform | gated? | upstream |
|---|---|---|---|---|---|---|---|---|
| TOOL-APTSIMULATOR | APTSimulator | EDR | dual-use-lab-only | edr | `"{binary}"` (APTSimulator.bat) | windows | simulation_authorized | NextronSystems/APTSimulator · LicenseRef-Nextron |
| TOOL-BLOODHOUND | BloodHound | ITDR, EDR | dual-use-lab-only | itdr | `{binary} --config-file {config_path} --json-output` | linux | simulation_authorized | SpecterOps/BloodHound · Apache-2.0 |
| TOOL-BLOODYAD | bloodyAD | ITDR | dual-use-lab-only | itdr | `{binary} --host {dc} -d {domain} -u {user} -p {password} {action}` | linux | simulation_authorized | CravateRouge/bloodyAD · MIT |
| TOOL-CALDERA | MITRE Caldera | EDR | dual-use-lab-only | edr | `{binary} --ability {ability_id}` | linux | simulation_authorized | mitre/caldera · Apache-2.0 |
| TOOL-DVWA | Damn Vulnerable Web App | *(none)* ⚠ | dual-use-lab-only | asm | `docker run -d -p 80:80 {image}` | linux | simulation_authorized | digininja/DVWA · GPL-3.0 |
| TOOL-EMPIRE | Empire | EDR | **c2-framework** | edr | `{binary} server` | linux | **c2_authorized** | BC-SECURITY/Empire · BSD-3-Clause |
| TOOL-EVILGINX2 | evilginx2 | ITDR, BROWSER, CLOUD_APP | dual-use-lab-only | itdr | `{binary} -p {phishlets_dir} -c {config_dir} -t {target_phishlet}` | linux | simulation_authorized | kgretzky/evilginx2 · BSD-3-Clause |
| TOOL-FRP | frp | NDR | dual-use-lab-only | ndr | `{binary} -c {config}` | linux | simulation_authorized | fatedier/frp · Apache-2.0 |
| TOOL-GOPHISH | Gophish | CLOUD_APP, BROWSER | dual-use-lab-only | cspm ⚠ | `{binary} --config {config_path}` | linux | simulation_authorized | gophish/gophish · MIT |
| TOOL-HAVOC | Havoc | EDR | **c2-framework** | edr | `{binary} server --profile {profile}` | linux | **c2_authorized** | HavocFramework/Havoc · GPL-3.0 |
| TOOL-KRBRELAYUP | KrbRelayUp | ITDR | dual-use-lab-only | itdr | `"{binary}" relay -d {domain}` | windows | simulation_authorized | Dec0ne/KrbRelayUp · MIT |
| TOOL-MIMIKATZ | Mimikatz | EDR, ITDR | dual-use-lab-only | edr | `"{binary}" "{commands}" "exit"` | windows | simulation_authorized | gentilkiwi/mimikatz · CC-BY-4.0 |
| TOOL-PHISHERY | phishery | ITDR | dual-use-lab-only | itdr | `{binary} -u {url} -i in.docx -o out.docx` | linux | simulation_authorized | ryhanson/phishery · BSD-3-Clause |
| TOOL-PRINTSPOOFER | PrintSpoofer | ITDR | dual-use-lab-only | itdr | `"{binary}" -i -c {command}` | windows | simulation_authorized | itm4n/PrintSpoofer · LicenseRef-itm4n |
| TOOL-PURPLESHARP | PurpleSharp | EDR | dual-use-lab-only | edr | `"{binary}" {playbook}` | windows | simulation_authorized | mvelazc0/PurpleSharp · MIT |
| TOOL-RUBEUS | Rubeus | ITDR, EDR | dual-use-lab-only | itdr | `"{binary}" {action} {flags}` | windows | simulation_authorized | GhostPack/Rubeus · BSD-3-Clause |
| TOOL-SET | Social-Engineer Toolkit | ITDR | dual-use-lab-only | itdr | `{binary}` | linux | simulation_authorized | trustedsec/social-engineer-toolkit · LicenseRef-TrustedSec |
| TOOL-SLIVER | Sliver | EDR, NDR | **c2-framework** | edr | `{binary} {sliver_command}` | linux | **c2_authorized** | BishopFox/sliver · GPL-3.0 |
| TOOL-STARKILLER | Starkiller | EDR | **c2-framework** | edr | `{binary}` | linux | **c2_authorized** | BC-SECURITY/Starkiller · BSD-3-Clause |
| TOOL-TOKENVATOR | Tokenvator | ITDR | dual-use-lab-only | itdr | `"{binary}"` | windows | simulation_authorized | 0xbadjuju/Tokenvator · BSD-3-Clause |

### Tier 4 — runtime-fetched (27)

| id | name | plane(s) | safety | install (runtime summary) | invoke (summary) | gated? | upstream |
|---|---|---|---|---|---|---|---|
| TOOL-CLOUDSPLAINING | Cloudsplaining | CDR | safe | `pip install cloudsplaining` | `{binary} scan --input-file {file}` | none | salesforce/cloudsplaining · Apache-2.0 |
| TOOL-CMSEEK | CMSeeK | NDR | safe | `git clone …/CMSeeK /tmp/CMSeeK` | `{binary} -u {target}` | none | Tuhinshubhra/CMSeeK · GPL-3.0 |
| TOOL-COMMIX | commix | NDR | dual-use-lab-only | `pip install commix` | `{binary} --url={url} --batch` | simulation_authorized | commixproject/commix · GPL-3.0 |
| TOOL-CREDKING | CredKing | ITDR | dual-use-lab-only | `git clone …/CredKing /tmp/CredKing` | `{binary} --target {target} --userfile {users} --password {password}` | simulation_authorized | ustayready/CredKing · LicenseRef-ustayready |
| TOOL-CROSSLINKED | CrossLinked | ITDR | safe | `pip install crosslinked` | `{binary} -f '{fmt}' {company}` | none | m8sec/CrossLinked · MIT |
| TOOL-DEEPCE | DEEPCE | CDR | dual-use-lab-only | `curl …/deepce.sh → /tmp/deepce.sh` | `{binary} {flags}` | simulation_authorized | stealthcopter/deepce · GPL-3.0 |
| TOOL-FEROXBUSTER | feroxbuster | NDR | dual-use-lab-only | `apt-get install feroxbuster \|\| cargo install` | `{binary} -u {url} -w {wordlist}` | simulation_authorized | epi052/feroxbuster · MIT |
| TOOL-GITGOT | GitGot | CDR | dual-use-lab-only | `git clone …/GitGot /tmp/GitGot` | `{binary} -q {query}` | simulation_authorized | BishopFox/GitGot · GPL-3.0 |
| TOOL-GITLEAKS | gitleaks | CDR | safe | `apt-get install gitleaks \|\| go install` | `{binary} detect --source {path}` | none | gitleaks/gitleaks · MIT |
| TOOL-GOBUSTER | gobuster | NDR | dual-use-lab-only | `go install …/gobuster \|\| curl release tarball` | `{binary} {mode} -u {target_url} -w {wordlist} -o {output_path} -q` | simulation_authorized | OJ/gobuster · Apache-2.0 |
| TOOL-KUBE-BENCH | kube-bench | CDR | safe | `curl …/kube-bench release` | `{binary} run --targets {targets} --json --outputfile {output_path}` | none | aquasecurity/kube-bench · Apache-2.0 |
| TOOL-KUBESCAPE | Kubescape | CDR | safe | `curl …/kubescape install.sh \| bash` | `{binary} scan {framework}` | none | kubescape/kubescape · Apache-2.0 |
| TOOL-MASSCAN | masscan | NDR | dual-use-lab-only | `apt-get install masscan \|\| git clone + make` | `{binary} {target} -p{ports} --rate={rate} --output-format json …` | simulation_authorized | robertdavidgraham/masscan · AGPL-3.0 |
| TOOL-NIKTO | Nikto | NDR | safe | `apt-get install nikto` | `{binary} -h {target}` | none | sullo/nikto · GPL-2.0 |
| TOOL-NMAP | Nmap | NDR | safe | `apt-get install nmap \|\| yum install nmap` | `{binary} {flags} {target}` | none | nmap/nmap · NPSL |
| TOOL-NUCLEI | Nuclei | NDR, CDR | safe | `curl …/nuclei release zip` | `{binary} -target {target} -severity {severity} -t {templates} …` | none | projectdiscovery/nuclei · MIT |
| TOOL-PACU | Pacu | CDR, CLOUD_APP | dual-use-lab-only | `pip3 install pacu==1.5.6` | `{binary} --session {session_name} --module-name {module_name} --exec` | simulation_authorized | RhinoSecurityLabs/pacu · BSD-3-Clause |
| TOOL-PROWLER | Prowler | CDR, CLOUD_APP | safe | `pip3 install prowler==5.4.0` | `{binary} {provider} --profile {aws_profile} --output-formats … --output-directory …` | none | prowler-cloud/prowler · Apache-2.0 |
| TOOL-PYPYKATZ | pypykatz | EDR, ITDR | dual-use-lab-only | `pip3 install pypykatz==0.6.10` | `{binary} {dump_type} {target_path} --json --outfile {output_path}` | simulation_authorized | skelsec/pypykatz · MIT |
| TOOL-RECON-NG | recon-ng | NDR | safe | `pip install recon-ng` | `{binary} -m {module} -x run` | none | lanmaster53/recon-ng · GPL-3.0 |
| TOOL-SCOUTSUITE | Scout Suite | CDR | safe | `pip install scoutsuite` | `{binary} {provider}` | none | nccgroup/ScoutSuite · GPL-2.0 |
| TOOL-SKYARK | SkyArk | CDR | dual-use-lab-only | `git clone …/SkyArk /tmp/SkyArk` | `{binary} -{scanner}` (pwsh) | simulation_authorized | cyberark/SkyArk · LicenseRef-CyberArk |
| TOOL-SQLMAP | sqlmap | NDR, CLOUD_APP | dual-use-lab-only | `apt-get install sqlmap \|\| pip3 install sqlmap` | `{binary} -u {target} --batch --random-agent -o --level={level} --risk={risk} …` | simulation_authorized | sqlmapproject/sqlmap · GPL-2.0 |
| TOOL-TRIVY | Trivy | CDR | safe | `curl …/trivy release tarball` | `{binary} {scan_type} --severity {severity} --format {output_format} --output {output_path} {target}` | none | aquasecurity/trivy · Apache-2.0 |
| TOOL-TSHARK | tshark | NDR | safe | `apt-get install tshark` | `{binary} -i {iface} -c {count} -w {out}` | none | wireshark/wireshark · GPL-2.0 |
| TOOL-VT-CLI | VirusTotal CLI | ANALYTICS | safe | `go install …/vt-cli/vt@latest` | `{binary} {subcommand} {arg}` | none | VirusTotal/vt-cli · Apache-2.0 |
| TOOL-WHATWEB | WhatWeb | NDR | safe | `apt-get install whatweb` | `{binary} {target}` | none | urbanadventurer/WhatWeb · GPL-3.0 |

### Tier 5 — external-only / reference (11) — `no_invoke`, never executed

| id | name | category | safety | upstream |
|---|---|---|---|---|
| TOOL-CAPEV2 | CAPEv2 | sandbox | safe | kevoreilly/CAPEv2 · GPL-3.0 |
| TOOL-CUTTER | Cutter | reverse-engineering | safe | rizinorg/cutter · GPL-3.0 |
| TOOL-DIDIERSTEVENSSUITE | Didier Stevens Suite | reverse-engineering | safe | DidierStevens/DidierStevensSuite · Public-Domain |
| TOOL-GHIDRA | Ghidra | reverse-engineering | safe | NationalSecurityAgency/ghidra · Apache-2.0 |
| TOOL-HAYABUSA | Hayabusa | analyst-workbench | safe | Yamato-Security/hayabusa · AGPL-3.0 |
| TOOL-ILSPY | ILSpy | reverse-engineering | safe | icsharpcode/ILSpy · MIT |
| TOOL-INETSIM | INetSim | sandbox | safe | inetsim.org · GPL-2.0 |
| TOOL-JADX | jadx | reverse-engineering | safe | skylot/jadx · Apache-2.0 |
| TOOL-PMA-LABS | Practical Malware Analysis Labs | sandbox | safe | mikesiko/PracticalMalwareAnalysis-Labs · LicenseRef-PMA |
| TOOL-PTEF | Purple Team Exercise Framework | analyst-workbench | safe | scythe-io/purple-team-exercise-framework · LicenseRef-SCYTHE |
| TOOL-RADARE2 | radare2 | reverse-engineering | safe | radareorg/radare2 · LGPL-3.0 |

> ⚠ **Plane note for tier-5:** every tier-5 pack ships with `cortex_signal.planes`
> empty `[]`. They are catalog/report references only and produce no Cortex
> signal, so they will never appear in `list_for_plane()` / the per-plane UI
> picker. That is intentional but undocumented elsewhere.

---

## 3. Per-tier deep-dive (notes, identities, MITRE, cleanup)

### Tier 1 — in-tree (we own the safety surface)

All three are `safe` (no launch consent) so the 15 AI/Browser/KOI scenarios stay
launchable without friction. Identity is `container-runtime` (they run inside the
SimCore container / EAL simulator, not via the AD/Linux harness). Each carries one
`ttp_ref` and a cleanup block.

| id | MITRE techniques | ttp_ref | cleanup cmds | source path on disk? |
|---|---|---|---|---|
| TOOL-CORTEX-PROMPT-ATTACKER | T1059 | TTP-2026-0012 | 1 | ✅ `sources/cortex-prompt-attacker` exists |
| TOOL-CORTEX-BROWSER-ATTACKER | T1539, T1059 | TTP-2026-0017 | 1 | ✅ `sources/cortex-browser-attacker` exists |
| TOOL-CORTEX-AGENTIC-PACK | T1195, T1059 | TTP-2026-0042 | 1 | ✅ `sources/cortex-malicious-agentic-pack` exists |

### Tier 2 — git submodule (⚠ MOSTLY MISSING ON DISK)

The packs declare `source_path: sources/<tool>` but **the submodules are not
actually present** (see GAP-ADAPT-01). Of the 8:

| id | declared source_path | registered submodule? | checked out? | invoke identity |
|---|---|---|---|---|
| TOOL-ATOMIC-RED-TEAM | sources/atomic-red-team | ✅ in `.gitmodules` | ❌ empty dir | root (`any` platform) |
| TOOL-CHAIN-REACTOR | sources/chain-reactor | ❌ not a submodule | ❌ absent | root |
| TOOL-IMPACKET | sources/impacket | ❌ not a submodule | ❌ absent | root |
| TOOL-PAYLOADSALLTHETHINGS | sources/PayloadsAllTheThings | ❌ not a submodule | ❌ absent | root |
| TOOL-SCAPY | sources/scapy | ❌ not a submodule | ❌ absent | root |
| TOOL-SCYTHE-COMPOUND-ACTIONS | sources/scythe-community-threats | ❌ not a submodule | ❌ absent | root |
| TOOL-SECLISTS | sources/SecLists | ❌ not a submodule | ❌ absent | root |
| TOOL-YARA | sources/yara | ❌ not a submodule | ❌ absent | root |

This matters because **TOOL-ATOMIC-RED-TEAM is the single most-referenced adapter
in the catalog** (8 scenarios) and its source tree is empty. The `docs/tool-adapters.md`
"Pending" note acknowledges this loosely ("the actual `sources/<tool>` submodules
… are added on demand"), but it is a live blocker for any DC who runs an EDR or
MP scenario expecting Atomic tests to execute. Pack tags hint at the gap:
atomic-red-team is tagged `already-submoduled` and scapy `already-submoduled-target`,
which are now stale/aspirational.

### Tier 3 — IaC-provisioned (20)

Installed on the **target VM** by the named `iac_module`. The engine's
`infra_generator` auto-includes a tier-3 adapter's `iac_module` in any bundle
that references it. Windows tools (Mimikatz, Rubeus, PurpleSharp, APTSimulator,
KrbRelayUp, PrintSpoofer, Tokenvator) run via `administrator`; Linux tools via
`root`. iac_module distribution across the 20 packs:

| iac_module | adapters | exists under `infra/modules/aws/`? |
|---|---|---|
| `edr` | APTSimulator, Caldera, Empire, Havoc, Mimikatz, PurpleSharp, Sliver, Starkiller (8) | ✅ |
| `itdr` | BloodHound, bloodyAD, evilginx2, KrbRelayUp, phishery, PrintSpoofer, Rubeus, SET, Tokenvator (9) | ✅ |
| `ndr` | frp (1) | ✅ |
| `asm` | DVWA (1) | ✅ |
| `cspm` | Gophish (1) | ✅ |

Two semantic oddities (see GAP-ADAPT-04):
- **TOOL-GOPHISH** is a `social-engineering` tool but pins `iac_module: cspm`
  (the cloud-posture-misconfig module). The pack carries an inline comment
  ("CSPM IaC ships the cloud public-egress shape gophish needs") so it is
  deliberate, but it is non-obvious and couples a phishing tool to a posture module.
- **TOOL-DVWA** pins `iac_module: asm` and declares **empty** `cortex_signal.planes`
  even though it is tier 3 (which requires an `invoke` block) — so it is a runnable
  adapter that maps to no plane. It will never surface in the plane picker.

### Tier 4 — runtime-fetched (27)

Installed at dispatch via `runtime_install_command`. Push bundles emit these
install commands inline; they are excluded from the bundle's hard dependency
check. All are `linux` except `kube-bench` (`k8s`). Identities are mostly `root`
(`gobuster` uses `nobody`; `deepce` uses `container-runtime`). The 27 split:

- **safe (16):** cloudsplaining, cmseek, crosslinked, gitleaks, kube-bench, kubescape, nikto, nmap, nuclei, prowler, recon-ng, scoutsuite, trivy, tshark, vt-cli, whatweb
- **dual-use-lab-only (11):** commix, credking, deepce, feroxbuster, gitgot, gobuster, masscan, pacu, pypykatz, skyark, sqlmap

### Tier 5 — external/reference (11)

No `invoke` block (loader forbids it). These exist only so a POV report can
attribute a tool the DC used manually (RE / sandbox / DFIR workbench tools), and
so the catalog/UI can list them. They never get installed or executed by the
engine. All tagged `phase-c`.

---

## 4. Reverse index — which scenarios reference each adapter

Built by `grep -rn "adapter_ref" scenarios/`. 13 distinct adapters are referenced
by 28 scenario files (31 `adapter_ref` lines — some scenarios reference multiple).

| adapter_id | # scenarios | scenario files (with line) |
|---|---|---|
| **TOOL-CORTEX-PROMPT-ATTACKER** | 5 | airs/sim-airs-001 (L46), airs/sim-airs-002 (L49), airs/sim-airs-003 (L46), airs/sim-airs-004 (L49), airs/sim-airs-005 (L45) |
| **TOOL-CORTEX-BROWSER-ATTACKER** | 5 | browser/sim-browser-001 (L43), browser/sim-browser-002 (L46), browser/sim-browser-003 (L44), browser/sim-browser-004 (L43), browser/sim-browser-005 (L43) |
| **TOOL-CORTEX-AGENTIC-PACK** | 5 | koi/sim-koi-001 (L46), koi/sim-koi-002 (L46), koi/sim-koi-003 (L46), koi/sim-koi-004 (L46), koi/sim-koi-005 (L46) |
| **TOOL-ATOMIC-RED-TEAM** | 6 | edr/edr-001 (L46), edr/edr-002 (L45), edr/edr-003 (L46), edr/edr-004 (L46), edr/edr-005 (L47), multi_plane/mp-005 (L63) |
| **TOOL-NMAP** | 3 | edr/edr-005 (L52), ndr/ndr-004 (L48), multi_plane/mp-005 (L68) |
| **TOOL-MASSCAN** | 1 | ndr/ndr-004 (L53) |
| **TOOL-MIMIKATZ** | 2 | multi_plane/mp-002 (L64), multi_plane/mp-004 (L66) |
| **TOOL-BLOODHOUND** | 2 | multi_plane/mp-002 (L69), multi_plane/mp-004 (L71) |
| **TOOL-RUBEUS** | 1 | multi_plane/mp-002 (L59) |
| **TOOL-PACU** | 1 | multi_plane/mp-004 (L61) |
| **TOOL-SCAPY** | 1 | multi_plane/mp-003 (L53) |
| **TOOL-SLIVER** | 1 | multi_plane/mp-001 (L79) — **the only c2-framework adapter wired to a scenario** |
| **TOOL-DEEPCE** | 1 | cdr/cdr-001 (L42) |

**Per-scenario inventory (each scenario → its adapters):**

| scenario file | plane | adapter_ref(s) |
|---|---|---|
| airs/sim-airs-001..005 | AIRS | TOOL-CORTEX-PROMPT-ATTACKER |
| browser/sim-browser-001..005 | BROWSER | TOOL-CORTEX-BROWSER-ATTACKER |
| koi/sim-koi-001..005 | KOI | TOOL-CORTEX-AGENTIC-PACK |
| edr/edr-001-credential-dumping | EDR | TOOL-ATOMIC-RED-TEAM |
| edr/edr-002-reverse-shell | EDR | TOOL-ATOMIC-RED-TEAM |
| edr/edr-003-persistence-mechanisms | EDR | TOOL-ATOMIC-RED-TEAM |
| edr/edr-004-defense-evasion | EDR | TOOL-ATOMIC-RED-TEAM |
| edr/edr-005-lateral-movement | EDR | TOOL-ATOMIC-RED-TEAM, TOOL-NMAP |
| ndr/ndr-004-smb-lateral-sweep | NDR | TOOL-NMAP, TOOL-MASSCAN |
| multi_plane/mp-001-c2-beacon-ngfw-xdr-stitch | ANALYTICS | TOOL-SLIVER (c2) |
| multi_plane/mp-002-kerberoast-lateral-smb | ANALYTICS | TOOL-RUBEUS, TOOL-MIMIKATZ, TOOL-BLOODHOUND |
| multi_plane/mp-003-data-staged-exfil-dns-tunnel | ANALYTICS | TOOL-SCAPY |
| multi_plane/mp-004-apt29-cloud-cred-theft | ANALYTICS | TOOL-PACU, TOOL-MIMIKATZ, TOOL-BLOODHOUND |
| multi_plane/mp-005-cross-plane-correlation | ANALYTICS | TOOL-ATOMIC-RED-TEAM, TOOL-NMAP |
| cdr/cdr-001-container-enum | CDR | TOOL-DEEPCE |

---

## 5. Orphan adapters (defined but referenced by ZERO scenarios) — 56

These packs load into the catalog, appear in `GET /api/tools/adapters` and the UI
picker, but **no scenario's `adapter_ref` points at them.** 49 of 56 carry the
`phase-c` tag (authored as catalog breadth, not wired to content). All 11 tier-5
packs are inherently orphans (they have no invoke path). The 4 c2-framework
adapters except Sliver are orphans (Empire, Starkiller, Havoc).

| Tier | Orphan adapters |
|---|---|
| **2** | TOOL-CHAIN-REACTOR, TOOL-IMPACKET, TOOL-PAYLOADSALLTHETHINGS, TOOL-SCYTHE-COMPOUND-ACTIONS, TOOL-SECLISTS, TOOL-YARA (6 of 8 tier-2 are orphans) |
| **3** | TOOL-APTSIMULATOR, TOOL-BLOODYAD, TOOL-CALDERA, TOOL-DVWA, TOOL-EMPIRE, TOOL-EVILGINX2, TOOL-FRP, TOOL-GOPHISH, TOOL-HAVOC, TOOL-KRBRELAYUP, TOOL-PHISHERY, TOOL-PRINTSPOOFER, TOOL-PURPLESHARP, TOOL-SET, TOOL-STARKILLER, TOOL-TOKENVATOR (16 of 20) |
| **4** | TOOL-CLOUDSPLAINING, TOOL-CMSEEK, TOOL-COMMIX, TOOL-CREDKING, TOOL-CROSSLINKED, TOOL-FEROXBUSTER, TOOL-GITGOT, TOOL-GITLEAKS, TOOL-GOBUSTER, TOOL-KUBE-BENCH, TOOL-KUBESCAPE, TOOL-NIKTO, TOOL-NUCLEI, TOOL-PROWLER, TOOL-PYPYKATZ, TOOL-RECON-NG, TOOL-SCOUTSUITE, TOOL-SQLMAP, TOOL-SKYARK, TOOL-TRIVY, TOOL-TSHARK, TOOL-VT-CLI, TOOL-WHATWEB (23 of 27) |
| **5** | ALL 11: TOOL-CAPEV2, TOOL-CUTTER, TOOL-DIDIERSTEVENSSUITE, TOOL-GHIDRA, TOOL-HAYABUSA, TOOL-ILSPY, TOOL-INETSIM, TOOL-JADX, TOOL-PMA-LABS, TOOL-PTEF, TOOL-RADARE2 |

**High-value orphans worth wiring first** (because the scenario gap is glaring):
TOOL-PROWLER / TOOL-SCOUTSUITE / TOOL-TRIVY / TOOL-NUCLEI (CDR scenarios use none
of them despite 5 CDR scenarios existing), the entire ITDR Windows toolchain
(Rubeus is wired only via MP, but BloodyAD/SET/phishery/etc. are unused by the 5
ITDR scenarios), and TOOL-SQLMAP / TOOL-NIKTO / TOOL-WHATWEB for the 7 NDR
scenarios (only NMAP/MASSCAN/SCAPY are wired).

---

## 6. Dangling references — NONE (adapter_ref → adapter)

Every `adapter_ref` in every scenario resolves to a defined `adapter_id`. The
loader's `_warn_dangling_adapter_refs()` (`core/engine/scenario_loader.py:321`)
would warn-not-fail on a miss; today it has nothing to warn about.

**However**, there is a *second* class of dangling reference inside the packs
themselves — the `equivalents:` field points at adapter ids that do not exist
(see GAP-ADAPT-03). The loader does **not** validate `equivalents`, so these are
silent:

| pack | dangling equivalent id | should be |
|---|---|---|
| TOOL-BLOODHOUND | `TOOL-SHARPHOUND` | no SharpHound pack exists |
| TOOL-EVILGINX2 | `TOOL-MODLISHKA` | no Modlishka pack exists |
| TOOL-SCAPY | `TOOL-HPING3` | no hping3 pack exists |
| TOOL-RUBEUS | `TOOL-IMPACKET-GETUSERSPNS` | likely meant `TOOL-IMPACKET` |

(`TOOL-BLOODYAD`, `TOOL-CALDERA`, `TOOL-PURPLESHARP`, `TOOL-FEROXBUSTER`,
`TOOL-SET`, `TOOL-KUBESCAPE`, `TOOL-PROWLER`, etc. referenced as equivalents
*do* exist.)

---

## 7. Cross-references to other domains

- **TTP detection cards** — adapters link to cards via `ttp_refs[]`. Only **12**
  packs carry a non-empty `ttp_refs` (the original Phase A/B set); the 56 Phase-C
  packs carry none. All referenced TTP ids resolve to a card under
  `detection_scanner/ttps/*.json` (verified: TTP-2026-0001/0002/0003/0004/0012/0017/0042).
  Reverse link from a TTP card is `referenced_by_adapters`. See
  `core/engine/ttp_catalog.py`.
  - TTP-2026-0001 ← evilginx2
  - TTP-2026-0002 ← mimikatz, pypykatz
  - TTP-2026-0003 ← nmap, nuclei, pacu, prowler
  - TTP-2026-0004 ← bloodhound, mimikatz, rubeus
  - TTP-2026-0012 ← cortex-prompt-attacker
  - TTP-2026-0017 ← cortex-browser-attacker
  - TTP-2026-0042 ← cortex-agentic-pack
- **IaC modules** — tier-3 `install.iac_module` values (`edr`, `itdr`, `ndr`,
  `asm`, `cspm`) all exist under `infra/modules/aws/`. The IaC generator
  (`core/engine/infra_generator.py`) auto-includes them when a scenario's
  `adapter_refs[]` resolve to a tier-3 adapter. See
  [`docs/superpowers/specs/2026-04-20-iac-topology-generator-design.md`].
- **EAL traffic plugins** — a *peer* abstraction, not adapters. The tier-1
  cortex-* adapters wrap EAL-driven CLIs but the AI_ACCESS / CLOUD_APP / ITDR /
  AIRS-emulator planes deliberately stay on the legacy EAL path with **no**
  adapter (per `docs/tool-adapters.md` §7 "Deliberately left legacy").
- **POV report "Tools Used"** — `core/api/runs.py` `_build_tools_used_rows()`
  resolves each `adapter_ref` to name/version/tier/category/safety/**license +
  attribution** for the compliance trail.
- **API** — `GET /api/tools/adapters` and `/adapters/{id}` serve the catalog
  (`core/api/tools.py`). UI consumers: `AdapterRegistryView.jsx`,
  `ToolAdapterCatalog.jsx`, `CoverageView.jsx` ("Tool Adapters" sub-tab).

---

## 8. Gaps, staleness, and inconsistencies (full list)

See the structured `gaps` array for severity. Summary:

1. **Tier-2 source trees missing on disk** — 8 packs declare `source_path:
   sources/<tool>`; only `sources/atomic-red-team` is even registered as a
   submodule (and it is not checked out / empty). The other 7 (chain-reactor,
   impacket, PayloadsAllTheThings, scapy, scythe-community-threats, SecLists,
   yara) are neither submodules nor present. `sources/` on disk contains only the
   3 in-tree cortex-* dirs + cortex-vulnerable-llm. **Atomic Red Team is the
   most-referenced adapter (8 scenarios) and cannot run.**
2. **81% orphan rate** — 56 of 69 adapters are referenced by zero scenarios. The
   catalog grew to satisfy the design spec's 100-tool inventory; scenario wiring
   did not keep pace. This is the documentation/POV pain the user flagged.
3. **`equivalents` dangling refs** — 4 packs point at non-existent adapter ids
   (TOOL-SHARPHOUND, TOOL-MODLISHKA, TOOL-HPING3, TOOL-IMPACKET-GETUSERSPNS). The
   loader never validates `equivalents`, so these are silent dead links.
4. **Semantic iac_module couplings** — TOOL-GOPHISH (social-engineering) → `cspm`
   module; TOOL-DVWA (web-app) → `asm` module **with empty planes**. Both load
   fine but are non-obvious; DVWA maps to no Cortex plane yet is a runnable tier-3
   adapter.
5. **Stale counts in the canonical doc** — `tools/packs/README.md` still says
   "Phase A — framework only … reference adapter `nmap.yml`" and the file map in
   `docs/tool-adapters.md` §2 says "Adapter packs (18)" / "live: 18" in §7, while
   the catalog is 69. The headline in `docs/tool-adapters.md` is correct (69) but
   the body has not been reconciled.
6. **Tier-5 empty planes undocumented** — all 11 tier-5 packs have empty
   `cortex_signal.planes`; this is correct (no_invoke) but not stated anywhere, so
   they silently vanish from plane filters.
7. **Doc safety-count drift** — `docs/tool-adapters.md` §3 says "safe ×33 ·
   dual-use-lab-only ×32 · c2-framework ×4" which **matches** reality (verified)
   — but the same doc's §7 "live: 18" contradicts it. Internal inconsistency.
8. **Stale pack tags** — atomic-red-team tagged `already-submoduled`, scapy
   tagged `already-submoduled-target` — aspirational/false given GAP #1.

---

## 9. Payload-shelf bindings (`install.artifact`) — added 2026-08-05

Every tier-4 pack installs its tool **from the public internet, on the target
host, at dispatch**. The customers who buy Cortex run default-deny egress, so
that is the first thing their network blocks — and a step whose tool never
arrived RUNS ANYWAY, produces no detection, and the absent detection reads in a
POV report as *"Cortex missed it"*. A pack that declares `install.artifact`
moves the fetch onto the DC's own SimCore, where egress is already accepted, and
makes the bytes checksummable before they reach a customer host.

Contract: [`payload-shelf.md`](payload-shelf.md). Schema and the `TA-01`..`TA-17`
rules: [`tools/packs/_schema.yml`](../../tools/packs/_schema.yml).

Since 2026-08-06 a tier-4 pack must declare **exactly one** of
`install.artifact` (the shelf serves it) or `install.artifact_exempt` (it does
not, and here is why) — `TA-13` rejects neither, `TA-14` rejects both,
`TA-15` keeps the exemption tier-4-only, `TA-16` rejects a placeholder reason,
and `TA-17` requires a `revisit` on the three codes that describe a CortexSim
limitation rather than a property of the tool. All are **structural** and are
NOT gated by `CORTEXSIM_STRICT_REFS`.

### 9.1 The 8 packs that declare a staged artifact

Every digest below was fetched from the real upstream and pasted; every licence
was read from the project's own LICENSE file rather than taken from the GitHub
API. `python3 -m engine.payload_shelf --check` gates `payloads/sources.json`
against this set.

| adapter | shelf filename | pin | licence | size | staged at |
|---|---|---|---|---|---|
| `TOOL-LINPEAS` | `linpeas.sh` | release-tag `20260803-00785084` | GPL-2.0-or-later | 1.1 MB | `/tmp/linpeas.sh` |
| `TOOL-PSPY` | `pspy64` | release-tag `v1.2.1` | GPL-3.0 | 3.1 MB | `/tmp/pspy64` |
| `TOOL-SUID3NUM` | `suid3num.py` | commit `881b4886…` | MIT | 27 KB | `/tmp/suid3num.py` |
| `TOOL-LSE` | `lse.sh` | release-tag `4.14nw` | GPL-3.0 | 55 KB | `/tmp/lse.sh` |
| `TOOL-LINENUM` | `LinEnum.sh` | commit `c47f9b22…` | MIT | 46 KB | `/tmp/LinEnum.sh` |
| `TOOL-DEEPCE` | `deepce.sh` | commit `420b1d1d…` | GPL-3.0 | 41 KB | `/tmp/deepce.sh` |
| `TOOL-TRAITOR` | `traitor-amd64` | release-tag `v0.0.14` | MIT | 9.4 MB | `/tmp/traitor-amd64` |
| `TOOL-AMICONTAINED` | `amicontained-linux-amd64` | release-tag `v0.4.9` | MIT | 6.1 MB | `/tmp/amicontained-linux-amd64` |

**Zero unpinned. Zero `unbound[]`.** `PAYLOAD_ALLOW_UNPINNED=0
./scripts/build-payloads.sh` stages all eight and every digest matched its pin.
The migration bucket the shelf design opened has reached zero — including the
pre-existing drift where `payloads/sources.json` claimed
`adapter_id: TOOL-LINPEAS` for a pack that had never existed.

Two licence notes worth carrying into a customer conversation:

* **PEASS-ng (LinPEAS) is GPL-2.0-or-later, not MIT.** The GitHub API reports
  `NOASSERTION`; the repository's `LICENSE` is a GPLv2-or-later text with
  Nmap-style "derived works" clarifications under which *parsing* peass-ng
  output creates a derived work while "typical shell or execution-menu apps,
  which simply display raw peass-ng output" do not. CortexSim streams the step's
  raw stdout and never parses it, which is the exempt case. The §8 worked
  example in `payload-shelf.md` says MIT — that is wrong; `tools/packs/linpeas.yml`
  is the authority.
* Four of the eight are GPL. A staged artifact is **redistributed to a customer
  host**, so the licence is an audit fact, not a nicety —
  `tests/tools/test_adapter_artifact_schema.py::test_the_shipped_shelf_bindings_are_pinned_and_licensed`
  fails on an unlicensed or `"unknown"` binding.

### 9.2 Why the other 48 tier-4 packs have NO artifact — and must not fake one

This gap is meant to be **legible, not closed by guessing**. Since 2026-08-06 it
is also **mandatory to state**: every tier-4 pack declares exactly one of
`install.artifact` or `install.artifact_exempt`, and **TA-13 REJECTS a pack that
declares neither**. A warning would have scrolled past in a boot log — which is
precisely how 48 packs came to share one byte-identical non-explanation. The
counts below are measured on the tree, not estimated.

| reason_code | count | why it is not shelf-able |
|---|---:|---|
| `DISTRO_PACKAGE` | 18 | An `apt-get install -y <pkg>` resolves a dependency graph, not a file. Closing this means hosting a Debian mirror — a different project. |
| `LANGUAGE_PACKAGE_MANAGER` | 12 | Same shape one layer up (pip / pipx / gem / `go install` / cargo): a resolver, not a file. `evil-winrm` alone pulls 8 runtime gems. A vendored wheel/gem set is a real option and a separate design. |
| `SOURCE_TREE_REQUIRED` | 7 | The tool **is** a directory. `seclists` is 3.3 GB of wordlists; `cmseek` needs its signature DB; `skyark` imports two sibling PowerShell modules; `chain-reactor` publishes no binaries and is built with `make` at run time. |
| `ARCHIVE_ONLY_NO_EXTRACTOR` | 6 | Single artifact, wrong container (`.tar.gz` / `.zip`). `kind: archive` is REJECTED by **TA-08** because **no consumer can unpack one** — `agent/beacon/artifact.go::Artifact` has no `Kind` field at all, the K8s init container is a `wget`+`mv` loop on an image with no `unzip`, and `build-payloads.sh` hard-fails on it. These are the **first candidates** when single-member extraction lands; each carries the measured archive digest and member name in its `revisit` line. |
| `NEEDS_RUNTIME_DATA` | 2 | The binary is inert without a feed it fetches at run time (a template repo; a vulnerability DB). Staging the binary would remove the *install* download and leave the *data* download — so the target still needs egress while CortexSim reported `air_gapped: true`. A false-green the shelf would manufacture itself. |
| `LICENCE_NO_REDISTRIBUTION` | 1 | `credking` publishes no licence at all, so there is no right to serve its bytes onto a host the DC does not own. The shelf **redistributes**; it does not cache. |
| `ARCHIVE_MEMBER_NOT_SELF_CONTAINED` | 1 | `kube-bench`'s archive holds **205 entries** — the binary plus a `cfg/` CIS-benchmark tree. It is *under* the size cap and looks shelvable on the release page; only opening the archive shows the binary alone exits with a config error. |
| `ARTIFACT_TOO_LARGE` | 1 | `kubescape` is genuinely one binary, but the `v4.0.11` linux-amd64 asset is **262,032,010 bytes** — four times the 64 MiB `CORTEXSIM_SHELF_MAX_BYTES` default. Raising the cap for one tool trades a hard limit for a 250 MB image layer. |

The honest summary: **8 of 56 tier-4 packs are air-gap-capable today.** The
remaining 48 still need public-internet egress on the target, and the console
says so per scenario at `GET /api/shelf/resolve/{scenario_id}`. Of those 48,
**40 are settled forever** — they would need a mirror of someone else's package
index — and the real backlog is the **8 adapters** carrying a mandatory
`revisit` line (TA-17). 40 + 8 = 48. Full triage, including two dead upstream
URLs found by probing every pinned URL in the tree:
[`payload-shelf.md` §11](payload-shelf.md).

### 9.3 Where a DC actually SEES this — and where they do not yet

| surface | shows the egress fact? | shows the exemption **reason**? |
|---|---|---|
| Launch → per-scenario preflight (`ScenarioPreflightCard`, `tools` row) | **yes** — names each tool that the *target* fetches at run time, and warns that the step runs without it on a default-deny network | no |
| Readiness → *Payload shelf* component + Tools & Payloads nav badge | **yes** — declared vs staged, and the scenario IDs that will refuse at compose | no |
| `GET /api/shelf/resolve/{scenario_id}` · `compose().unstaged_adapters[]` | yes | **no** — one generic sentence for all 48 |
| `GET /api/tools/adapters` | no | **no** — `_adapter_summary()` omits `install.artifact` and `install.artifact_exempt` entirely |

**The durable declaration is right; the operator-facing string is not yet wired.**
A DC learns *that* a tool needs target egress at the moment it matters (the
launch button). They cannot yet learn *why* it is not on the shelf, or whether
that is a settled decision or an open backlog item, without opening the pack
YAML. Closing this is two small changes in files this pass did not own:
`_adapter_summary()` in `core/api/tools.py` should carry
`artifact_exempt.{reason_code,reason,revisit}`, and `UnstagedAdapter` in
`core/engine/payload_shelf.py` should carry `reason_code` so
`unstaged_adapters[]` stops emitting one sentence for 48 different decisions.
Console copy guidance is in [`payload-shelf.md` §11.4](payload-shelf.md).

---

## 10. The three tier-2 **Rust** tools — invisible to this catalog until 2026-08-05

`signalbench`, `ackbarx` and `xdrtop` are tier-2 submodule tools that **have no
`tools/packs/*.yml` at all**. `grep -rln` over `tools/packs/` for all three
returns nothing, so §2's master table, §5's orphan list, `scripts/check-adapter-
sources.sh`'s tier-2 loop and the CI `adapters` job **never looked at any of
them**. Their only binding is `core/tools/registry.py::STATIC_TOOL_REGISTRY`,
consumed by `core/tools/instantiator.py`.

That blindness had a cost. Three defects were live and unnoticed:

| # | defect | proven by |
|---|---|---|
| 1 | **`signalbench` has never been buildable with its declared `build_cmd`.** `src/techniques/software.rs:19` does `include_bytes!("../../embedded_binaries/pacemaker_helper")`; that file is not in the upstream tree and must be produced by building `helpers/pacemaker` first. `cargo build --release` fails rc=101. | executing it |
| 2 | **`signalbench`'s `run_template` is rejected by its own CLI.** `--technique {mitre_id} --count {count} --output json` → `error: unexpected argument '--technique' found`. The real CLI is subcommand-based: `signalbench run <TECHNIQUES>...`. | executing it |
| 3 | **`ackbarx`'s `run_template` is rejected by its own CLI.** `--listen-port 162 --forward-url {…}` → `error: unexpected argument '--listen-port' found`. `ackbarx` is config-file driven (`-c <FILE>`). | executing it |

### Why they are NOT on the payload shelf

The shelf's `install.artifact` describes a **download**: it requires an upstream
`url`, a `pin`, and a `sha256` known *before* the fetch. A locally-built binary
has no upstream URL and its digest is a function of the toolchain. **TA-01**
already hard-rejects `install.artifact` on `tier != 4` — **keep that rule**, but
note its stated rationale is **wrong for these three**: it says tier-1/2 trees
"have no egress problem for the shelf to solve", and `cargo build --release` on
a customer jumpbox needs rustup *and* crates.io, which is an egress problem in
every sense. Fix the sentence, keep the rule.

Upstream releases cannot rescue it either: `xdrtop` ships only `.deb`/`.tar.gz`/
`.zip` and **TA-08 rejects `kind: archive` outright**, and `ackbarx`'s gitlink is
**untagged**, so no `pin` could honestly reference the source we ship.

### What replaced it

A sibling shelf, `rust-dist/`, filled by `scripts/build-rust-dist.sh` (or
`core/Dockerfile`'s `rust-builder` stage) and served with the **agent's** exact
idiom — plural collection / singular artifact / `/sha256`, `FileResponse`, an
`X-CortexSim-*-SHA256` header, and a 404 naming the directory and the command
that fills it. Three static-musl `linux/amd64` binaries, each **executed** on
clean ubuntu 22.04/24.04, debian 12-slim and alpine 3 with `--network none`
before publication.

A ~50 ms `--check-recipe` gate now runs inside `scripts/check-adapter-sources.sh`
— so this catalog's own preflight finally sees all three — and asserts, among
other things, that `sources/signalbench/helpers/pacemaker` still exists.

**Full contract: [`rust-tools.md`](rust-tools.md).**
