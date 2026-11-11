# XDR protection profiles vs. the CortexSim corpus — review

Reviewed 2026-11-11 against `dev` at `8c90739`. Source: the Cortex XDR
documentation on `cortex-docs.paloaltonetworks.com` (GitBook, section
*Protect your endpoints*, parent page "last updated 7 days ago" on the review
date), fetched as Markdown. Page list and reproduction steps are in §7.

> ***tenant-verified is 0.*** One lab observation is recorded in card prose
> (SIM-EDR-023, report mode, 2026-11-11). The repo does not count it as
> tenant-verified, and neither does this review. **Authored is not proven.**

---

## Executive summary

Cortex XDR's endpoint profiles expose about 75 separately configurable
protection modules across Windows, macOS and Linux. Each module has its own
block / report / off switch. CortexSim describes the agent's prevention behaviour with eight
coarse labels and grades a prevention test against one customer-declared
"block or report" answer for the whole run. The result is a measurement that
can tell a customer "Cortex detected this but failed to stop it" when the
customer's own profile simply had that one module set to report. That would put a
false claim about the product into a POV document. Two of the eight labels describe
capabilities the documentation says do not exist on Linux, and the newest
ransomware scenario is keyed to one of them. The library already exercises
most of the high-value Linux and Windows modules with real techniques, so the
coverage exists. What is missing is the mapping, the per-module attestation and the
platform truth. The recommended action, in order:

1. Record the customer's mode per module instead of per run.
2. Re-key the Linux ransomware content to the module that actually exists.
3. Widen the module vocabulary to match the documentation.

Do this before any further prevention content is authored.

---

## Technical detail

### 1 · Method

1. Pulled every page under *Set up endpoint profiles and exception rules* plus
   the hardening profiles (Device Control, Host Firewall, Disk Encryption,
   FIM) and *Configure global agent settings* as Markdown.
2. Parsed each page per platform tab into module → setting → options.
3. Inventoried the repo's prevention contract: the `Prevention` detection type,
   `PREVENTION_MODULES`, `detections.prevention_modules[]` on cards,
   `outcome: prevent` on scenarios, `panw_mapping.products[]` entries at
   `coverage_tier: prevention`, `evaluate_prevent`, and the canary.
4. Matched each documented module to the scenarios whose technique would
   trigger it. The first pass was a pattern scan over step commands; each hit
   was then checked by scenario name and technique. A hit means *a live-agent
   technique that should reach this module exists*. It does not mean the
   module has been shown to fire.

### 2 · Counted state

| Item | Count | Where |
|---|---:|---|
| Documented configurable modules, malware profile | Windows 31 · macOS 19 · Linux 16 (+ Threat Detection Engine on the Cloud Optimized tab) · Android 1 · iOS 5 · CaaS 1 | malware page |
| Documented modules, exploit profile | Windows 6 · macOS 6 · Linux 3 | exploit page |
| Documented modules, restrictions profile | Windows 6 · macOS 2 · Linux 2 · CaaS 1 · Serverless 1 | restrictions page |
| Identity profile | LDAP Protection · AD-SPM · Conditional Access | identity page |
| `PREVENTION_MODULES` values | 8 | `core/engine/scenario_loader.py:62`, `detection_scanner/scripts/validate.py:45` |
| …that map 1:1 to one documented module | 2 (`btp`, `anti_ransomware`) | §4 |
| Cards with `prevention_modules[]` | 2 (TTP-2026-0198 `local_analysis`, TTP-2026-0200 `anti_ransomware`) | |
| Scenarios with `outcome: prevent` | 1 (SIM-EDR-023) | |
| `panw_mapping` prevention-tier entries naming the XDR agent | 10 cards, all 10 citing rule ids an agent module does not have (`BTP-*`, `XDR-*`, `TBD-XDR-*`); plus 5 Prisma Browser cards labelled `cortex-xdr` | §3 F8 |
| Canary scenario configured by default | none (`CORTEXSIM_CANARY_SCENARIO_LINUX = ""`) | `core/config.py:83` |

### 3 · Findings, most severe first

**F1 · HIGH · Prevention is graded against a run-level mode; the product
configures it per module.**
`evaluate_prevent` (`core/engine/outcome.py:199`) takes one
`agent_profile_mode ∈ {report, block, unknown}`. In block mode, a `Detected`
alert grades `fail` with `DETECTED_NOT_PREVENTED` (`outcome.py:235`). One
malware profile can hold Global BTP = Block, Reverse Shell Protection =
Report and Cryptominers = Disabled, all at the same time. A "block" attestation for that
profile turns a Report-mode module's correct behaviour into a product failure
on the POV report. The canary cannot close this. It confirms only the
module its trigger reaches, and none is configured by default, so
`block_mode_status` is `CANARY_NOT_AVAILABLE` and the `fail` stands
(`outcome.py:255` only downgrades `UNCONFIRMED`).
*Latent today:* SIM-EDR-023 is the only `prevent` row, and it runs in report
mode. *Fix:* attest per module
(`{module_key: block|report|disabled}` + `attested_by/at`), have the
expectation's module look up its own attested mode, and resolve a non-Block
module to `detected` with reason `MODULE_NOT_IN_BLOCK`, never to `fail`.
*Status (2026-11-11): fixed on `fix/api-prevent-per-module-attestation`
(530bebb, unpushed).* A launch takes `module_modes`; Report reads
`MODULE_NOT_IN_BLOCK`, Disabled holds a silent row `pending` /
`MODULE_DISABLED`, and an unattested module trusts the run-level block, so
legacy runs grade unchanged.

**F2 · HIGH · `anti_ransomware` is keyed on Linux, where the documentation
lists no Ransomware Protection module.**
The Linux malware profile, both the Standard tab and the Cloud Optimized tab, has
no Ransomware Protection module. It is documented for Windows and macOS only.
Affected:
- `TTP-2026-0200` / SIM-EDR-025: `module: anti_ransomware`,
  `platforms: [linux]`, `alert_name_pattern: "Ransomware*"`
  (`detection_scanner/ttps/TTP-2026-0200-sim-edr-025.json:240-244`).
- `TTP-2026-0073` / SIM-EDR-008: `panw_mapping` submodule `anti-ransomware`
  (`:260`). Its recommendation tells the customer to "Enable Cortex XDR
  anti-ransomware behavioral protection on Linux hosts" (`:329`), a setting
  the documentation does not list.
- The spec's content plan (`docs/superpowers/specs/2026-10-26-prevention-detection-efficacy-design.md:500`)
  says "Linux now".

On Linux, mass-encryption prevention can come only from **Global Behavioral
Threat Protection Rules**. *Fix:* re-key SIM-EDR-025 to `btp`, drop the
`Ransomware*` name pattern (see F5), and correct the TTP-2026-0073 remediation
text. SIM-EDR-025 uses `outcome: detect`, so no false grade is live today.
*Status (2026-11-11): fixed on `fix/scenarios-linux-ransomware-btp-key`
(6091687, unpushed).* TTP-2026-0200 is keyed `btp` with
`"Behavioral Threat*"` (still authored, not observed; F5 still applies);
TTP-2026-0073 and TTP-2026-0026 remediation names the BTP rule setting. The
`anti_ransomware` enum value and spec line 500 are left for the F3 decision.

**F3 · HIGH · The 8-value enum is too coarse, and two values name the wrong
kind of thing.**

| Repo value | What the documentation actually has |
|---|---|
| `btp` | **Global Behavioral Threat Protection Rules** (W/M/L), plus sub-settings: Quarantine, *Vulnerable Drivers Protection* (W), Advanced API Monitoring (W), AI-Powered BTP (W). The dedicated modules below have **their own Action Mode** and are not part of it. |
| — (absorbed into `btp` today) | Reverse Shell Protection (L), Container Escaping Protection (L), Cryptominers (W/M/L), Anti Webshell (W/M/L), Malicious Child Process (W/M/L), Credential Gathering (W/M/L), Financial Malware + Crypto Wallet (W/M/L), In-process Shellcode (W), UAC Bypass (W), Anti Tampering (W/M), IIS (W), UEFI (W), Dynamic Kernel (W), Dynamic Driver (W), Security Measures Bypass (W), Malicious Device (W/M), LDAP Protection (W, also in the Identity profile), Loaded Kernel Modules Examination (L), Network Packet Inspection Engine (W/M), Respond to Malicious Causality Chains (W/M) |
| `credential_protection` | Two modules with different switches: **Credential Gathering Protection** (W/M/L, Block/Report/Disabled) and **Password Theft Protection** (W only, Enabled toggle). The spec says Credential Gathering is Windows-only (`spec:503`). The documentation lists it on all three. |
| `local_analysis`, `wildfire` | Not modules. They are **verdict sources** inside the file-examination modules (PE/DLL, Office macros, JScript, ASP/ASPX, PowerShell, VBS, Java, Mach-O, DMG, ELF execution, ELF loading). The switch that blocks is the examination module's Action Mode. |
| `exploit_protection` | A whole profile: Browser Exploits, Logical Exploits (DLL block list), Known Vulnerable Processes (+ Java Deserialization), OS Exploit Protection, Additional Processes, Unpatched Vulnerabilities (W); Kernel Privilege Escalation (M); 3 on Linux. |
| `device_control`, `host_firewall` | Separate hardening profiles, not malware/exploit/restrictions modules. **Both are documented "Linux: Not supported".** The spec plans `host_firewall` on Linux "if the attested list includes it" (`spec:502`). The documentation already answers that: it is not supported. |

*Fix:* see §6 for a proposed vocabulary and a platform matrix that the
validator enforces. A `module` on a platform the documentation does not list
should be a load ERROR, so F2 cannot recur.

**F4 · MEDIUM · BIOC rules can block; the loader says they cannot.**
The restrictions profile's **Custom Prevention Rules** (W/M/L) enables
user-defined *BIOC prevention rules*. Supported investigation types:
file, process execution, remote code execution, network, Windows event log, module.
**Custom Indicator Prevention Rules** does the same for indicators, and needs
a TIM license. The loader rejects `outcome: prevent` on anything but
`type: Prevention` (`core/engine/scenario_loader.py:362`). The spec states "A
BIOC cannot block" (§4.2). Every card BIOC that uses a supported event type is
a candidate prevention rule. *Decision owed:* model it as a Prevention module
(`custom_bioc_prevention`, bound to the BIOC's slug) or allow
`type: BIOC` + `outcome: prevent` behind the same two-witness rule.

**F5 · MEDIUM · `alert_name_pattern` depends on tenant configuration.**
The global setting **Informative BTP Alerts** gives BTP alerts unique,
descriptive names. The spec's own example (`"Behavioral Threat*"`, §4.2) stops
matching on any tenant that turns it on. *Fix:* match on alert source
(`XDR Agent`), the raw `action` (`BLOCKED*` vs `REPORTED`/`DETECTED*`), host
and time window. Keep the name pattern as a tie-breaker only, and record the
tenant's Informative BTP setting as an attested fact.

**F6 · MEDIUM · Settings that silently remove the signal are not on the
readiness ladder.** These are all in the Agent Settings profile:
- **Linux Agent Operation Mode.** In asynchronous mode (unsupported kernel, `--no-km`, or
  user-space mode on a kernel older than 5.0) the documentation says there is no event collection, no BTP,
  no EDR and no anti-LPE. Every Linux EDR scenario would then read *missed*.
  This is one possible cause of the pov-target-01 steps that never reached
  telemetry (not verified).
- **Monitor and Collect Enhanced Endpoint Data** (XDR Pro) and **Data
  Generation Providers**. Disabling any provider drops its event family. Disabling the
  Data Generation Module disables all of them.
- **XDR Agent Tampering Protection** (service, process, pipe, file and registry
  protection) decides whether SIM-EDR-021's EDR-kill technique can act at all.

*Fix:* add these as attested rungs, rendered *attested, not verified*, the way
BAS mode and soakless already are.

**F7 · LOW · BAS mode is attested correctly. One detail is missing.**
`bas_mode_attested` exists. The documentation adds that on content older than
**1850** the BAS setting cannot be configured, and the agent is treated as BAS
**Enabled** whatever the profile says. The attestation needs the agent's
content version beside it, or a "disabled" attestation can be silently wrong.

**F8 · LOW · `panw_mapping` prevention claims for the agent use invented
identifiers.**
Agent modules have no rule ids. All ten agent-side entries cite ids such as
`BTP-REVSHELL-LINUX-001`, `BTP-CRED-LSASS-001`, `Exploit-LSASS-Handle-Block`,
`TBD-XDR-001/002` and `TBD-XDR-ISOLATE-001`, under submodule names (`agent-traps`,
`credential-theft-protection`, `agent`) that are not documentation terms. They
should name the module:

| Card / scenario | Claimed | Documented module(s) |
|---|---|---|
| TTP-2026-0033 / SIM-EDR-002 | `agent-traps` `BTP-REVSHELL-LINUX-001` | Reverse Shell Protection (L) |
| TTP-2026-0002 / SIM-EDR-006 | `credential-theft-protection` | Password Theft Protection + Credential Gathering Protection (W) |
| TTP-2026-0078 / SIM-ITDR-007 | `agent-traps` LSASS / token theft | Password Theft Protection, Credential Gathering, OS Exploit Protection (W) |
| TTP-2026-0119 / SIM-EDR-014 | `agent` `TBD-XDR-001/002` | IIS Protection + Anti Webshell + ASP & ASPX Files examination (W) |
| TTP-2026-0144 / SIM-EDR-021 | `agent` `TBD-XDR-001` | Global BTP → Vulnerable Drivers Protection, Dynamic Driver Protection, Security Measures Bypass, Anti Tampering (W) |
| TTP-2026-0142 / SIM-MP-020 | `agent` `TBD-XDR-ISOLATE-001` | Endpoint isolation is a **response action**, not a profile module. Re-tier it as response/automation. |
| TTP-2026-0073 / SIM-EDR-008 | `anti-ransomware` | Global BTP (L). See F2. |
| TTP-2026-0017..0021 | `cortex-xdr/dlp` etc. | Prisma Browser policy, not the XDR agent. The product label is wrong. |
| TTP-2026-0177 / SIM-DLP-001 | `cortex-xdr/None` USB block | Device Control profile (W/M); restrictions → Removable Media Files (W) |

**F9 · INFO · SIM-EDR-023 is associated correctly. Record its governing
settings.**
Seeing `Local Analysis Malware` on the test ELF requires the **ELF Execution
Examination** module with these settings:
- Action on unknown ELF = **Run Local Analysis**. With Allow, no verdict is produced.
- Action Mode = **Block**, for a block-mode kill.
- Quarantine = **"Quarantine WildFire and Local Analysis malware"**. The
  WildFire-only option does not quarantine a Local Analysis verdict.

These three settings belong in the scenario's preconditions.

### 4 · Module × signal matrix

Status key:
- **Bound**: a card `prevention_modules[]` object exists.
- **Signal**: a live-agent scenario runs a technique that should reach the module, with no binding yet.
- **Partial**: the technique is adjacent, or covers only the precursor.
- **None**: no scenario.
- **n/a**: a modifier, a scan setting or a mobile setting.

#### 4.1 Malware profile

| Module | W | M | L | CO | Repo key today | Best existing signal | Status |
|---|:-:|:-:|:-:|:-:|---|---|---|
| ELF Execution Examination | | | ● | ● | `local_analysis`/`wildfire` | SIM-EDR-023 (test ELF); SIM-CDR-005, SIM-CDR-012, SIM-CDR-013 (container) | **Bound** (L) |
| ELF Loading Examination | | | ● | | — | — | None |
| Loaded Kernel Modules Examination | | | ● | | — | — | None |
| Local File Threat Examination (PHP) | | ● | ● | ● | — | SIM-MP-037 (PHP2Shell), SIM-ASM-004 | Partial |
| PE & DLL Examination | ● | | | | `wildfire` | SIM-EDR-020 (side-loaded DLL) | Partial |
| Office Files with Macros | ● | | | | — | — | None |
| JScript / VBS / PowerShell script files | ● | | | | — | SIM-EDR-016, SIM-EDR-017 run interpreters inline; no script file is examined | None |
| ASP & ASPX Files (on-write) | ● | | | | — | SIM-EDR-014 | Signal |
| Java Files | ● | | ● | | — | — | None |
| Mach-O Execution / Loading, DMG | | ● | | | — | SIM-KOI-009 (macOS) | Partial |
| Global Behavioral Threat Protection Rules | ● | ● | ● | | `btp` | SIM-EDR-002/003/004/005/015/016/017; SIM-EDR-025 (L encryption) | Signal; SIM-EDR-025 mis-keyed |
| ↳ Vulnerable Drivers Protection | ● | | | | — | SIM-EDR-021 (ThrottleStop.sys) | Signal |
| Reverse Shell Protection | | | ● | ● | (`btp` in the spec example) | **SIM-EDR-002** | Signal |
| Container Escaping Protection | | | ● | ● | — | **SIM-CDR-003**, SIM-CDR-011 | Signal |
| Cryptominers Protection | ● | ● | ● | ● | — | **SIM-CDR-002** (XMRig), SIM-CDR-014 | Signal (L) |
| Credential Gathering Protection | ● | ● | ● | ● | `credential_protection` | **SIM-EDR-001** (L, /etc/shadow, mimipenguin); SIM-KOI-009 (M keychain); SIM-EDR-006 (W) | Signal |
| Password Theft Protection | ● | | | | `credential_protection` | **SIM-EDR-006** (comsvcs LSASS), SIM-EDR-020, SIM-ITDR-007 | Signal |
| Anti Webshell Protection | ● | ● | ● | | — | SIM-EDR-014, SIM-EDR-020 | Signal (W) |
| IIS Protection | ● | | | | — | **SIM-EDR-014** (w3wp backdoor) | Signal |
| Malicious Child Process Protection | ● | ● | ● | ● | — | SIM-EDR-014/020 (w3wp child), SIM-EDR-016 (explorer → interpreter), SIM-EDR-022 | Signal |
| In-process Shellcode Protection | ● | | | | — | **SIM-EDR-013** (UUID-string shellcode) | Signal |
| Anti Tampering (+ Safe Mode reboot) | ● | ● | | | — | SIM-EDR-021 | Signal (W) |
| Security Measures Bypass | ● | | | | — | SIM-EDR-021 | Signal |
| Dynamic Driver Protection | ● | | | | — | SIM-EDR-021 | Signal |
| Dynamic Kernel Protection | ● | | | | — | — | None |
| UAC Bypass Prevention | ● | | | | — | — | None |
| UEFI Protection | ● | | | | — | — | None |
| Ransomware Protection | ● | ● | ✗ | ✗ | `anti_ransomware` | SIM-EDR-011 (W variant, pre-encryption only) | Partial (W); **not a Linux module** |
| Financial Malware + Crypto Wallet | ● | ● | ● | ● | — | — (SIM-KOI-009 does not model GlassWorm's wallet theft) | None |
| Malicious Device Protection | ● | ● | | | — | — | None |
| LDAP Protection | ● | | | | — | SIM-ITDR-006, SIM-ITDR-015 (AD enum; the agent must be on the DC) | Partial |
| Network Packet Inspection Engine | ● | ● | | | — | SIM-EDR-015 (chisel tunnel) | Partial |
| Respond to Malicious Causality Chains | ● | ● | | | — | SIM-EDR-015 (PsExec fan-out) | Partial |
| Threat Detection Engine | | | | ● | — | — | None (not described on the page) |
| BAS Tools settings | ● | ● | ● | | `bas_mode_attested` | attestation, not a signal | n/a |
| On-write / On-demand / Generic file types | ● | ● | ● | | — | modifiers | n/a |

#### 4.2 Exploit profile

| Module | W | M | L | Best existing signal | Status |
|---|:-:|:-:|:-:|---|---|
| Browser Exploits | ● | ● | | — | None |
| Logical Exploits (DLL block list) | ● | ● | | SIM-EDR-020 (python.exe DLL side-load) | Signal |
| Known Vulnerable Processes (+ Java Deserialization) | ● | ● | ● | — | None |
| Operating System Exploit Protection | ● | ● | ● | SIM-ITDR-007 (PrintSpoofer, KrbRelayUp). On Linux, SIM-EDR-022 enumerates only; no scenario runs an LPE exploit | Signal (W) |
| Kernel Privilege Escalation | | ● | | — | None |
| Exploit Protection for Additional Processes / Unpatched Vulnerabilities | ● | ● | ● | configuration | n/a |

#### 4.3 Restrictions profile

| Module | W | M | L | CaaS | Best existing signal | Status |
|---|:-:|:-:|:-:|:-:|---|---|
| Executable Files (path restriction) | ● | | | | SIM-EDR-016/017 (execution from user-writable paths) | Partial |
| Network Location / Removable Media / Optical Drive Files | ● | | | | SIM-DLP-001 (USB) | Partial |
| Custom Prevention Rules (BIOC prevention) | ● | ● | ● | | any card BIOC on a supported event type | None declared (F4) |
| Custom Indicator Prevention Rules | ● | ● | ● | | IOC cards | None declared |
| Altered Binary Execution Restriction | | | | ● | SIM-CDR-002, SIM-CDR-013 (download-and-exec in a running container) | Signal |

#### 4.4 Other profiles

| Profile | Platforms | Signal | Note |
|---|---|---|---|
| Identity (LDAP Protection, AD-SPM, Conditional Access) | W (DC) | ITDR AD scenarios | Needs an agent on the DC |
| Device Control | W/M (Linux not supported) | SIM-DLP-001 | `device_control` must not accept `linux` |
| Host Firewall | W/M (Linux not supported) | — | `host_firewall` must not accept `linux` |
| Disk Encryption, Host Inventory, Vulnerability Assessment | W/M | — | posture, not prevention |
| File Integrity Monitoring (add-on) | W/M/L | SIM-EDR-003/004 (persistence and log-tamper file writes) | detection, not prevention |
| Agent Settings → AES run-mode | W/M | SIM-KOI-009/010/011 | gate for every KOI endpoint leg |

**Totals** (47 rows in §4.1–4.3): 1 Bound, 18 Signal, 9 Partial, 16 None and
3 n/a. One Signal row, Global BTP, carries the mis-keyed SIM-EDR-025. The
cheapest wins are the **Signal** rows: the technique already runs, and only
the card object and the per-module attestation are missing.

### 5 · Recommended detections and content (in order)

1. **Bind the strong Linux signals first.** All of these are on the Cloud Optimized tab, so one agent profile
   covers them on the pov-target-01 VM:
   - Reverse Shell Protection → SIM-EDR-002
   - Credential Gathering → SIM-EDR-001
   - Container Escaping → SIM-CDR-003
   - Cryptominers → SIM-CDR-002
   - ELF Execution → SIM-EDR-023 (done)
   - Global BTP → SIM-EDR-025 (re-keyed)
2. **Windows second.** These are authored and unproven, since no Windows host has run the beacon:
   - Password Theft → SIM-EDR-006
   - In-process Shellcode → SIM-EDR-013
   - IIS + Anti Webshell + ASP/ASPX → SIM-EDR-014
   - Vulnerable Drivers / Dynamic Driver / Security Measures Bypass → SIM-EDR-021
   - Logical Exploits → SIM-EDR-020
   - OS Exploit → SIM-ITDR-007
3. **New content only where a high-value module has no signal at all:**
   - UAC Bypass (W)
   - Office macros (W)
   - Ransomware Protection with real encryption (W/M)
   - Crypto Wallet (M, extend SIM-KOI-009)
   - Loaded Kernel Modules (L)
   - a Linux LPE exploit for OS Exploit Protection (the `TOOL-TRAITOR` shelf artifact already stages one)

### 6 · Proposed vocabulary (decision owed, not implemented)

Replace the 8-value enum with module keys named after the documentation.
Add a `platforms` matrix, owned by one module both the loader and `validate.py`
import, so a card naming a module on an unsupported platform is rejected:

```
malware:      elf_execution  elf_loading  loaded_kernel_modules  local_file_threat
              pe_dll  office_macros  script_files  asp_aspx  java_files
              macho_execution  macho_loading  dmg
              global_btp  vulnerable_drivers  reverse_shell  container_escaping
              cryptominers  credential_gathering  password_theft  anti_webshell
              iis  malicious_child_process  inprocess_shellcode  anti_tampering
              security_measures_bypass  dynamic_driver  dynamic_kernel  uac_bypass
              uefi  ransomware  financial_malware  malicious_device  ldap_protection
              network_packet_inspection  causality_chain_response
exploit:      browser_exploits  logical_exploits  known_vulnerable_processes
              java_deserialization  os_exploit  kernel_privilege_escalation
restrictions: executable_paths  network_location  removable_media  optical_drive
              custom_bioc_prevention  custom_indicator_prevention  altered_binary
hardening:    device_control  host_firewall
```

Keep `wildfire` / `local_analysis` as a separate `verdict_source` field on
the file-examination modules, because they describe *how* the verdict was
reached, not *which switch* blocked.

### 7 · Reproduction

Pages fetched as Markdown by appending `.md` to each URL under
`https://cortex-docs.paloaltonetworks.com/cortex-xdr/protect-your-endpoints/`.
Index: `https://cortex-docs.paloaltonetworks.com/llms.txt`.

- `install-and-manage-endpoints/set-up-endpoint-protection/set-up-endpoint-profiles-and-exception-rules/`:
  `set-up-malware-prevention-profiles`, `set-up-exploit-prevention-profiles`,
  `set-up-restrictions-prevention-profiles`, `set-up-agent-settings-profiles`,
  `set-up-identity-profiles`, `set-up-exception-profiles-and-rules`
- `install-and-manage-endpoints/harden-endpoint-security/`: `device-control`,
  `host-firewall`, `disk-encryption`
- `endpoint-protection/file-integrity-monitoring-fim`,
  `install-and-manage-endpoints/set-up-endpoint-protection/configure-global-agent-settings`

The old `docs-cortex.paloaltonetworks.com/r/Cortex-XDR/...` URLs now redirect
to the GitBook host and return 404. The BAS-mode citation in the prevention
spec's Appendix B uses that form and needs updating.

Repo inventory, from the repo root:

```bash
git grep -l '"prevention_modules"' -- detection_scanner/ttps
git grep -l 'outcome: prevent' -- scenarios | grep -v _schema
grep -n "PREVENTION_MODULES" core/engine/scenario_loader.py detection_scanner/scripts/validate.py
```
