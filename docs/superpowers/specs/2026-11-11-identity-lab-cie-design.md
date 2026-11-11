# Identity lab: a faux Active Directory wired to the Cloud Identity Engine — design

**Date:** 2026-11-11 · **Branch:** `docs/docs-identity-lab-cie-design` (from `dev` 3f3e84b)
**Status:** draft v1, awaiting Henry's decisions (§13)
**Inputs:** two research passes on 2026-11-11 (official Palo Alto Networks docs; range-builder,
Windows evaluation and Samba sources), with the load-bearing claims re-fetched and checked
by the main session. Sources and the verified/unverified split are in
[`2026-11-11-identity-lab-cie-design/sources.md`](2026-11-11-identity-lab-cie-design/sources.md).

## Executive summary

Identity detection is the part of a Cortex POV that CortexSim cannot exercise today. The
corpus has 14 scenarios that need a real Active Directory and nowhere to run them, no card
reads the Cloud Identity Engine's dataset, and the deathstar tenant has no Windows endpoint at
all. This design adds an **identity lab**: a small, disposable Windows Server domain built on
the homelab's KVM host from images and installers kept on the lab's own Gitea, joined to
Cortex XDR and synced to Cortex through the Cloud Identity Engine. A guided console wizard
walks a DC through it in the same shape as the firewall wizard, checking each stage
read-only and saying plainly what is not yet proven. **The main risk is time, not build
effort**: Cortex's built-in identity detectors need 14 days to activate and 30 to train, so
the lab must be stood up weeks before it is relied on, and the first-day value comes from
custom detections that join domain-controller telemetry to the identity data. Recommended
action: approve the build stack and the seven decisions in §13, starting with legal sign-off
on mirroring Microsoft's evaluation media internally.

## 1. Intent

Three things are true on `dev` today:

* **14 scenarios need a domain and have none.** `SIM-ITDR-006/-007/-013/-015/-017/-020`,
  `SIM-EDR-005/-019`, `SIM-MP-002/-005/-019/-020/-030/-036` Kerberoast, AS-REP roast, DCSync,
  abuse AD CS or move laterally inside a domain. The only domain CortexSim can build is the
  AWS `itdr` module (`infra/modules/aws/itdr`), which needs an AWS account and is not where
  Henry's lab is.
* **No detection uses the Cloud Identity Engine.** `pan_dss_raw` ("Active Directory via
  Cloud Identity Engine", which exists only once CIE is set up) appears in **0** of 191 cards.
  Nine cards read `msft_windows_security`, a dataset name a tenant never creates (the real
  names are `microsoft_windows_raw` and `xdr_data`; see the tenant-setup research,
  `docs/docs-tenant-setup-research`).
* **deathstar has no Windows endpoint.** `get_filtered_endpoints platform=windows` returned
  `total_count: 0` on 2026-11-11. The first domain controller would be its first.

"CIE-based detection" in this design means two things, and they behave very differently
(§8):

1. **Cortex's own Identity Analytics detectors** (Kerberoasting, AS-REP roasting, DCSync,
   privileged-group change, …). They need CIE linked to the tenant, the XDR agent with
   Extended Threat Hunting on the DC, and **14 days of activation plus 30 of training**.
2. **Custom detections that join domain-controller telemetry to CIE identity context**
   (`xdr_data` / `microsoft_windows_raw` × `pan_dss_raw`). These fire the day the data lands.

## 2. What the docs settle (verified)

Each row was fetched on 2026-11-11; URLs are in the sources appendix.

| Question | Answer | Consequence |
|---|---|---|
| Can the DC be Samba 4? | CIE supports on-prem "Microsoft Active Directory and OpenLDAP"; Samba is not listed. The on-prem requirement names Windows Server 2016–2025. The identity detectors need Windows Security events (4768/4769/4662/47xx) that the docs mark "DCs only". The ITDR Identity Profile runs only on Windows endpoints. | **Windows Server DC.** Samba is ruled out. |
| Where does the CIE agent run? | A Windows Server 2016–2025 host, ≥ 8 GB RAM, ≥ 10 GB disk, local admin. Installed "on a supported Active Directory … directory server", so the DC is allowed. Not on the same host as a User-ID agent. | A member server (cleaner) or the DC (one VM fewer). Decision D-2. |
| How does the agent reach CIE? | Outbound 443 to `agent-directory-sync.<region>.paloaltonetworks.com`; 80 to the GoDaddy CRL/OCSP hosts; mutual TLS with a certificate generated in the CIE app (3-month life, auto-renewed from agent 1.5.0). SSL decryption **must be off** for the agent host. LDAPS 636 by default, LDAP 389; **not** the Global Catalog ports. | The lab cannot be air-gapped at run time. Egress allowlist (§9). |
| Is there an agentless on-prem path? | No. | CIE agent is mandatory. |
| How is XSIAM linked? | Settings → Configuration → Integrations → Cloud Identity Engine. CIE must be in the **same region** as the tenant. It is "required to activate Identity Analytics detection rules". | Manual hub/console steps; the wizard checks the result, it cannot do them. |
| What lands in the tenant? | `pan_dss_raw`: users, groups, computers, OUs with `sAMAccountName`, `memberOf`, `adminCount`, `userAccountControl`, `servicePrincipalName`, `msDS-AllowedToDelegateTo`, `sIDHistory`, … Incremental sync every 5 min, full weekly. | Custom detections can join on it (§8.2). |
| Is there an API for CIE sync status? | No documented one. `get_tenant_info` carries `identity_threat_expiration`, `xth_expiration`, `purchased_xth`; XQL can read `pan_dss_raw`. | Preflight proves sync with a **canary** (§7.2). |
| Detector data and timing | e.g. *Possible Kerberoasting attack*: "XDR Agent **and** XDR Agent with XTH", activation 14 days, training 30 days. The Analytics engine baseline needs EDR or network data from 30+ endpoints over 2+ weeks. | A new lab shows no Identity Analytics alert for weeks; a 3-VM lab may never baseline (§7.3). |
| Licensing | CIE is free. Identity Analytics is included with XSIAM. ITDR is an add-on on every tier. XTH is included in Enterprise/Premium, an add-on on NG-SIEM. | Decision D-4: what deathstar is entitled to. |
| Windows evaluation | Server 2022/2025 evaluations last 180 days and must be activated online within 10 days to avoid automatic shutdown. | Activation endpoints in the allowlist, or a lab that lives ≤ 10 days (unverified behaviour). D-6. |
| Range builders | GOAD has no upstream libvirt provider (a 1-commit community fork does); Ludus requires Debian 12/13 or Proxmox 8/9; DetectionLab is unmaintained since 2023; Attack Range is cloud-only; AutomatedLab is Hyper-V/Azure. | **Build it ourselves with Packer + Ansible**, the tools GOAD and Ludus use underneath, and borrow GOAD's data-driven seeding pattern. |

## 3. Goals, non-goals, anti-goals

**Goals**

1. A reproducible faux domain on lab hardware, from a bundle SimCore renders: `DC01`
   (AD DS + DNS + XDR agent), `SRV01` (member, CIE agent, an SPN-backed service, XDR agent),
   `WS01` (domain-joined user/attack endpoint, XDR agent, CortexSim beacon).
2. **Every build artifact pulled from the lab's self-hosted Gitea**, digest-pinned by a
   manifest the bundle carries in. Nothing is fetched from the public internet at build time.
3. A console wizard, **Manage › Identity lab** (`#/identity-lab`), shaped like the firewall
   wizard: Source → Plan → Build → Directory → Agents → CIE → Ready, each stage checked
   read-only and reported even when an earlier one degraded.
4. A readiness ladder that never collapses: **AUTHORED · BUILT · JOINED · SYNCED · TRAINED ·
   VERIFIED**, with `tenant-verified` stated verbatim.
5. CIE-based detection content: custom joins that fire on day one, and honest gating of the
   built-in identity detectors until the training clock has run.

**Non-goals (v1)**

* **SimCore provisioning VMs.** v1 renders a bundle that the DC runs on the hypervisor, the
  same split as the IaC generator. SimCore gets no SSH to the hypervisor and no libvirt
  socket (D-7).
* Any write to CIE, the hub or the tenant. The CIE link is a checklist plus a check.
* Samba, multi-forest, trusts, Entra ID hybrid join, AD CS beyond what `SIM-ITDR-017` needs.
* The Broker VM Windows Event Collector path. Broker VM work is on hold until Henry names the
  applets (collector-targets decision, 2026-11-10); agent-side collection covers v1.
* Fixing the nine `msft_windows_security` cards. That is the tenant-setup research's open
  decision; §8.3 only records the dependency.

**Anti-goals (a failure even if it technically works)**

* A green stage built on an injected transport or a cached answer. Every response carries
  `queries_issued`.
* "0 users", "0 endpoints" or "dataset absent" rendering as ok. A zero is degraded.
* A scenario that **declares** a built-in identity detector as expected while the lab is
  inside its 14 + 30 day window. That scores a working tenant as a miss.
* The domain admin password in SimCore, a log line, a URL or a committed file.
* A build that "succeeds" on an artifact whose digest it never checked.
* An evaluation DC that shuts itself down mid-POV without the console having said when.

## 4. Architecture

```
            lab LAN 192.168.1.0/24                          Cortex cloud (deathstar region)
 ┌──────────────────────────────────────────┐            ┌───────────────────────────────┐
 │ ms-01 (Ubuntu 24.04, libvirt, 96 GB)     │            │ XSIAM tenant      CIE tenant  │
 │  ┌──────────────┐   Gitea generic        │            │   ▲  pan_dss_raw     ▲        │
 │  │ git.lab.local│   registry: ISOs,      │            │   │  xdr_data        │        │
 │  │  /packages   │   MSIs, wheels, seed   │            │   │                  │        │
 │  └──────┬───────┘                        │            └───┼──────────────────┼────────┘
 │         │ fetch + sha256 (build time)    │                │ 443 (XDR)        │ 443 + 80
 │  ┌──────▼─────────────────────────────┐  │      ┌─────────┴──────────────────┴──────┐
 │  │ bundle: fetch.sh → packer (golden  │  │      │ PA-440: allowlist only, no SSL    │
 │  │ qcow2) → virt-install overlays →   │  │      │ decryption for SRV01, no NAT for  │
 │  │ ansible (WinRM)                    │  │      │ the lab subnet                    │
 │  └──────┬─────────────────────────────┘  │      └─────────▲─────────────────────────┘
 │         │ routed libvirt net 10.77.0.0/24│                │
 │   ┌─────▼────┐ ┌──────────┐ ┌──────────┐ │                │
 │   │ DC01     │ │ SRV01    │ │ WS01     │─┼────────────────┘
 │   │ AD DS,DNS│ │ CIE agent│ │ beacon,  │ │
 │   │ XDR agent│ │ XDR agent│ │ XDR agent│ │
 │   └──────────┘ └──────────┘ └──────────┘ │
 └──────────────────────────────────────────┘
        ▲ read-only preflights (LDAPS bind, DNS SRV, :88)
 ┌──────┴──────────────┐
 │ SimCore (this POV)  │  renders the bundle, runs the wizard, reads the tenant read-only
 └─────────────────────┘
```

The lab network is **routed, not NATed**. The firewall wizard's investigation found that
libvirt NAT made the PA-440 see ms-01's address for a guest, and ms-01 is itself an XDR
endpoint, so Cortex attributed the guest's sessions to the wrong host. A routed `/24` keeps
each VM's own address on the wire.

**The lab outlives SimCore.** SimCore is one disposable instance per POV
(`project-one-simcore-per-pov`). The identity lab cannot be: its detectors need 44 days to
come alive. So the lab is persistent lab infrastructure, rebuilt rarely, and each POV's
SimCore **adopts** it through the wizard (registers the artifact source, the read-only bind
account and the hosts) rather than building it again. Rebuilding resets the training clock,
and the console says so before it lets anyone press the button.

## 5. Artifacts from the self-hosted source

### 5.1 Store of record: Gitea's generic package registry

Gitea on ms-01 (`git.lab.local`) already exists. Its generic registry stores files under
`/api/packages/{owner}/generic/{name}/{version}/{file}`. A re-upload of an existing file
answers **409**, so a published version is immutable. Its package-files API returns sha256
per file. Size limits default to unlimited. Large ISOs should be uploaded over the LAN to
Gitea's own port: one report shows Traefik v3.6 cutting streaming uploads after ~5 minutes.

The payload shelf is **not** the store for these. The shelf is per-SimCore, per-tool-pack and
disposable; a 5 GB ISO that outlives every POV belongs in the lab's own registry. The shelf's
integrity rule carries over unchanged: **the consumer verifies against a digest it carried
in**. Gitea's reported sha256 is a cross-check only.

### 5.2 The manifest

`lab/identity/manifest.json` (in the repo, generated and checked like
`payloads/sources.json`):

```json
{
  "schema": 1,
  "source": {"kind": "gitea_generic", "owner": "cortexsim-lab"},
  "artifacts": [
    {"name": "ws2022-eval-iso", "version": "20348.169.210806", "file": "SERVER_EVAL_x64FRE_en-us.iso",
     "sha256": "<pinned>", "bytes": 0, "kind": "iso", "upstream": "https://www.microsoft.com/evalcenter/…",
     "licence": "Microsoft evaluation EULA", "redistribution": "private-org-only", "required": true}
  ]
}
```

| Artifact | Upstream | Licence note | Per POV? |
|---|---|---|---|
| Windows Server 2022 evaluation ISO (2025 later) | Microsoft Evaluation Center | **legal sign-off before mirroring** (D-1) | no |
| Languages and Optional Features ISO | Evaluation Center | as above; feeds OpenSSH etc. offline | no |
| virtio-win stable ISO (versioned name) | fedorapeople | BSD-3 | no |
| Packer + `packer-plugin-qemu` binaries | releases.hashicorp.com | BSL | no |
| `pywinrm` wheel; `ansible.windows`, `microsoft.ad`, `community.windows` tarballs | PyPI, Galaxy | Apache/GPL-3 (tooling only, not vendored) | no |
| Sysmon zip + config (optional) | Sysinternals | private use only, never published | no |
| Seed fixture JSON | this repo | ours | no |
| Cortex XDR agent installer | deathstar (per tenant) | tenant-generated | **yes**: re-uploaded per tenant |
| Cloud Identity agent `DaInstall.msi` | CIE app in the hub | PANW | no |
| Rubeus (self-compiled), Impacket wheel (optional) | GhostPack, Fortra | BSD-3 / modified Apache 1.1, notices kept | no |

`required: false` rows may be absent; a missing `required` row blocks the Source stage.

### 5.3 The `artifact_source` credential

A new integration kind, `artifact_source`, stored like every other credential: `config =
{kind: gitea_generic, base_url, owner, verify_tls}`, secret = a **read-only** Gitea token.
`base_url` goes through the same host normaliser the firewall wizard uses (no scheme tricks,
no userinfo), and is validated at PUT time.

## 6. The build bundle

`POST /api/identity-lab/bundle` renders a tar.gz from a new module,
`infra/modules/onprem/identity-lab/`. It is the first module of the on-prem provider the IaC
design addendum (Phase E) describes, Ansible and shell instead of Terraform, and it uses the
generator's existing copy-and-render path.

```
identity-lab-<build_id>/
├── run.sh                 # preview by default; --apply mutates (Henry's dry-run rule)
├── manifest.lock.json     # the artifacts and sha256 this bundle was rendered against
├── fetch.sh               # pull from Gitea, verify each sha256, refuse on mismatch
├── packer/ws2022.pkr.hcl  # qemu builder, accelerator=kvm, virtio disk, cd_files for answers
├── packer/autounattend.xml# PnpCustomizationsWinPE DriverPaths → virtio; enables WinRM
├── libvirt/net.xml        # routed network, no NAT
├── libvirt/domains.sh     # virt-install qcow2 overlays on the golden image
├── ansible/inventory.yml  # rendered: DC01/SRV01/WS01, addresses, domain
├── ansible/dc.yml         # promote forest, DNS, audit GPO incl. 4662 SACL, seed, canary
├── ansible/member.yml     # join; CIE agent prerequisites (CA cert, bind account)
├── ansible/endpoint.yml   # join; beacon via enrolment token; XDR agent
├── ansible/canary.yml     # bump the sync canary (§7.2)
├── seed/fixture.json      # deterministic users, groups, SPNs, ACEs, flags (+ sha256)
└── teardown.sh            # preview by default; --apply destroys overlays, keeps the image
```

* **Exit codes are stable and reported.** Every stage ends with a code printed and POSTed to
  `/api/identity-lab/telemetry` (the installer's pattern): `ARTIFACT_MISSING`,
  `ARTIFACT_PIN_MISMATCH`, `KVM_UNAVAILABLE`, `PACKER_BUILD_FAILED`, `WINRM_UNREACHABLE`,
  `DC_PROMOTE_FAILED`, `SEED_DRIFT`, `JOIN_FAILED`, `OK`. Telemetry lives in memory and resets
  with SimCore, as install attempts do.
* **Two-stage build.** Packer makes one golden image per OS (slow, rare). Every lab is
  overlays on it plus Ansible (fast, repeatable). Re-provisioning never reinstalls Windows.
* **The seed is data, not code.** `fixture.json` is generated once, in the repo, from the AWS
  `itdr` module's seeding (`user1..50`, five weak-password SPN service accounts,
  `helpdesk-admin` with `DoesNotRequirePreAuth`), extended with the bait §8 needs: one
  privileged group with an unexpected member, one account with constrained delegation, one
  service account allowed interactive logon, one honey account. Applied by idempotent
  Ansible, so two builds from one fixture are the same domain. GOAD's `config.json` is the
  model; GOAD itself (GPL-3) is not vendored. Detections key on names, never SIDs, which
  differ per build.
* **Secrets never reach SimCore.** `run.sh` generates the domain admin password and the
  service passwords into an Ansible vault file sealed with an operator passphrase. SimCore
  receives only the **read-only bind account** (`svc-cortexsim-ro`, LDAP read, nothing else),
  stored in its vault as kind `lab_directory`. SimCore's API is unauthenticated (SEC-01);
  holding a domain admin credential there would make it the lab's weakest point.

## 7. The wizard

Console destination **Manage › Identity lab**, next to Tenant and Firewall. Each stage has an
explicit operator action; nothing polls in the background. Every stage is reported even when
an earlier one degraded, and every response carries `queries_issued`.

| # | Stage | The DC does | SimCore checks (read-only) | Codes |
|---|---|---|---|---|
| 1 | **Source** | registers Gitea URL + read token | reachable, token accepted, every `required` artifact present, size and sha256 match the manifest | `SOURCE_UNREACHABLE`, `SOURCE_UNAUTHORIZED`, `ARTIFACT_MISSING`, `ARTIFACT_PIN_MISMATCH` |
| 2 | **Plan** | domain, NetBIOS, VM profile (D-2), lab CIDR, CIE region | region equals the tenant's region (when readable); CIDR not overlapping the LAN; evaluation dates | `CIE_REGION_MISMATCH`, `CIDR_OVERLAP` |
| 3 | **Build** | downloads the bundle, runs `run.sh`, then `run.sh --apply` on ms-01 | telemetry codes as they arrive | the §6 codes |
| 4 | **Directory** | — | DNS SRV `_ldap._tcp.dc._msdcs.<domain>`; LDAPS bind as `svc-cortexsim-ro`; RootDSE; seed counts match the fixture (users, SPN accounts, `userAccountControl & 0x400000`); TCP 88 reachable; clock skew under Kerberos' 5 minutes | `DNS_SRV_MISSING`, `LDAP_BIND_FAILED`, `SEED_DRIFT`, `KDC_UNREACHABLE`, `CLOCK_SKEW` |
| 5 | **Agents** | installs XDR on the three VMs; the bundle enrols the beacon on WS01 | beacon on WS01 online with `os: windows` and a domain; tenant endpoints for the three hostnames, `connected`, XDR agent version ≥ 9.1 (≥ 9.3 for the full ITDR feature set) | `BEACON_NOT_ENROLLED`, `XDR_ENDPOINT_MISSING`, `XDR_AGENT_TOO_OLD` |
| 6 | **CIE** | follows the checklist: activate CIE in the tenant's region, generate the agent certificate, install `DaInstall.msi` on SRV01 (or DC01), LDAPS 636 not GC, link in XSIAM | `pan_dss_raw` exists; the **sync canary** for this build is present and current (§7.2) | `CIE_NOT_LINKED`, `CIE_OTHER_DIRECTORY`, `CIE_SYNC_STALE` |
| 7 | **Ready** | — | entitlements from `get_tenant_info`; the training clock; endpoint count against the Analytics baseline; which scenarios this lab unlocks | `ITDR_NOT_ENTITLED`, `XTH_NOT_ENTITLED`, `IDENTITY_TRAINING`, `ANALYTICS_BASELINE_UNLIKELY` |

### 7.1 Where the directory checks run from

SimCore on the DC's laptop reaches the lab only if the routed `/24` is reachable from the LAN.
If it is not (a customer lab, a VPN-only path), stage 4 runs as a read-only **probe task** on
the WS01 beacon instead, reporting the same codes. That needs the per-step task environment
the collector-targets work deferred (spec §10 there); until it lands, stage 4 reads
`not_checked` with `DIRECTORY_UNREACHABLE_FROM_SIMCORE`, never ok.

### 7.2 The sync canary

There is no API that says "CIE is syncing". The build creates a sentinel user,
`cxs-canary-<build_id>`, whose `description` holds a value the bundle writes.
`run.sh canary --apply` rewrites it with a fresh nonce and reports the nonce to SimCore.
Stage 6 then runs one priced XQL query:

```
dataset = pan_dss_raw
| filter <sAMAccountName field> = "cxs-canary-<build_id>"
| fields <description field>, _time
```

* Dataset absent → `CIE_NOT_LINKED`.
* Dataset present but no canary → `CIE_OTHER_DIRECTORY` (some directory syncs, not this one).
* Canary present with an older nonce than the last one reported → `CIE_SYNC_STALE`, with the
  age. CIE syncs incrementally every 5 minutes, so the wizard waits at least 10 before
  calling it stale.
* Current nonce → `SYNCED`.

**Unverified:** the exact `pan_dss_raw` field names. They must be captured from deathstar
before the query is fixed, the same capture-before-fixture rule the firewall parsers follow.
A row whose shape the parser does not recognise raises; it does not read as "no canary".

### 7.3 The training clock

Stage 7 records when the CIE link and the DC's XDR agent were first seen together and shows
the earliest date the built-in identity detectors can fire: **+14 days activation, then 30
days training**. It also reads the tenant's endpoint count: the Analytics baseline wants
30+ endpoints over 2+ weeks, and a 3-VM lab on an otherwise empty tenant may never reach it
(`ANALYTICS_BASELINE_UNLIKELY`). Whether the baseline is per tenant is **unverified**. If it
is, a lab joined to an already-baselined tenant skips the wait.

### 7.4 Readiness ladder

| Rung | Means | Does not mean |
|---|---|---|
| AUTHORED | the bundle and content exist | anything was built |
| BUILT | stages 1–4 green | Cortex sees anything |
| JOINED | stage 5 green | identity data reached the tenant |
| SYNCED | stage 6 green (canary current) | a detector will fire |
| TRAINED | the 14 + 30 day clock has run and entitlements are present | a detector *did* fire |
| VERIFIED | a scenario run reconciled an identity detection against the tenant | — |

`tenant-verified` stays **0** until a run reaches VERIFIED.

## 8. Detection content

### 8.1 Built-in Identity Analytics detectors: gated

The detectors this lab exists for, from the Analytics Alert Reference (each page says
activation 14 days, training 30 days):

| Detector | Required data (as the docs state it) |
|---|---|
| Possible Kerberoasting attack | XDR Agent **and** XDR Agent with XTH |
| A user requested multiple service tickets | WEC **or** XDR Agent with XTH |
| Possible AS-REP Roasting Attack | XDR Agent **and** XDR Agent with XTH |
| Multiple TGT requests for users without Kerberos pre-authentication | WEC **or** XDR Agent with XTH |
| Possible DCSync attack | XDR Agent with XTH |
| Potential DCSync by an unusual user | WEC **or** XDR Agent with XTH |
| A user was added to a Windows security group; Machine account added to Domain Admins; SID History added; AdminSDHolder ACL modified; delegation change; PKINIT TGT request | WEC **or** XDR Agent with XTH |
| Possible LDAP enumeration tool usage; multiple LDAP enumeration queries | XDR Agent with XTH |
| Possible Kerberoasting without SPNs; Weakly-encrypted Kerberos ticket requested; NTLM Relay | NGFW traffic **or** XDR Agent (no detection module) |

No golden-ticket or silver-ticket detector exists under those names in the reference.

A scenario that expects one of these declares it with a new expectation condition,
`requires_rung: TRAINED`. Below that rung the expectation is scored `not_applicable` with the
reason `IDENTITY_TRAINING`, never `missed`, and the POV report says when it becomes scoreable.
Today the honest alternative is `purpose: hunt` on the card, which is what the Windows content
lane did; the condition makes the lab's own state the gate instead of a hand edit.

### 8.2 Custom joins that fire on day one

These read the DC's events (`xdr_data` for agent-normalised events; `microsoft_windows_raw`
for raw Windows events, per the datasets reference) and join them to `pan_dss_raw` for who the
identity *is*. They need CIE linked and the agent on the DC, not the training window.

| Card detection | Event | CIE context | Seeded bait |
|---|---|---|---|
| Kerberoast of a privileged service account | 4769, RC4 ticket (0x17) | target has `servicePrincipalName` and `adminCount = 1` | `svc-sql` in a privileged group |
| AS-REP request for a no-preauth account | 4768, pre-auth type 0 | `userAccountControl` has `DONT_REQ_PREAUTH` | `helpdesk-admin` |
| Unexpected member of a privileged group | 4728 / 4732 / 4756 | member not in the fixture's admin roster | the planted extra member |
| Service account interactive logon | 4624 type 2 / 10 | account has an SPN | `svc-backup` |
| Delegation added to an account | 4738 | `msDS-AllowedToDelegateTo` now non-empty | the delegation account |
| Honey account touched | 4768 / 4769 / 4624 | the honey account's name | `svc-legacy-honey` |
| **Correlation:** identity + endpoint + network | the above, plus the WS01 process and the PA-440 Kerberos session | — | — |

Each needs at least one Correlation row per ten step-detections: the corpus sits exactly at
the `coverage-strict` 10.0 % floor (137 of 1369).

### 8.3 Scenarios

* **New: `SIM-ITDR-021`, CIE-context privileged identity abuse**, pulled on WS01:
  Kerberoast → AS-REP roast → add a user to a privileged group → interactive logon as a
  service account. It uses native PowerShell (`System.IdentityModel` token requests,
  `Set-ADGroupMember` style calls) rather than Rubeus, so no unsigned binary meets the agent.
  The day-one joins (§8.2) are declared; the built-in detectors carry `requires_rung: TRAINED`.
* **Existing: the 14 domain scenarios** in §1 become runnable. Each gets a lab-readiness row
  stating which rung it needs. Several also need Linux attack tooling (`TOOL-IMPACKET`) on a
  Linux beacon inside the lab subnet. `pov-target-01` can be moved onto the routed network
  for that, or a small Linux VM added (open, §13).
* **Dependency, not in scope:** nine cards read `msft_windows_security`. Until the
  tenant-setup decision lands they will read nothing in deathstar. New cards in this design
  use `xdr_data` / `microsoft_windows_raw`.

## 9. Network and egress

* **Build network:** no egress. Packer and Ansible reach only Gitea and the VMs.
* **Lab network at run time:** routed through the PA-440, deny by default, with an allowlist:
  the XDR tenant's endpoints; `agent-directory-sync.<region>.paloaltonetworks.com:443` for
  SRV01; `crl.godaddy.com:80`, `ocsp.godaddy.com:80`, `certs.godaddy.com:443`; App-ID
  `paloalto-cloud-identity`; Microsoft activation if D-6 says so.
* **No SSL decryption** for the CIE agent's host; CIE requires it.
* The firewall wizard's preflight (`test security-policy-match`, read-only) can prove each
  allowlist entry before a POV. Stage 5 links to it rather than duplicating it.

## 10. Security

* The domain admin password lives only in the operator's sealed Ansible vault; SimCore holds
  a read-only bind account. A test asserts no `lab_directory` credential has write rights the
  preflight would use (it binds and searches; there is no modify call in the client).
* LDAPS only. The DC's CA certificate is exported by the bundle and pinned by the preflight;
  `verify_tls: false` is allowed for a first build and shown as `degraded` on every check.
* The lab is deliberately weak. It stays on its own routed subnet behind the firewall's
  deny-by-default policy, and the console says so on the Plan stage.
* Evaluation media stay in a private Gitea organisation; Sysinternals' terms forbid
  publishing.
* Teardown is preview-by-default; `--apply` deletes the overlays and keeps the golden image.

## 11. API sketch

| method | path | purpose |
|---|---|---|
| `GET` | `/api/identity-lab` | adopted lab, per-stage state, rung, training clock, `tenant_verified: 0` |
| `PUT` | `/api/credentials/integrations` | existing route; kinds `artifact_source`, `lab_directory` |
| `POST` | `/api/identity-lab/plan` | validate the plan (stage 2), no side effects |
| `POST` | `/api/identity-lab/bundle` | render the bundle for the plan, return a download URL |
| `POST` | `/api/identity-lab/telemetry` / `GET …/attempts` | build stage codes, in memory |
| `POST` | `/api/connectors/artifact_source/preflight` | stage 1 |
| `POST` | `/api/connectors/lab_directory/preflight?stage=directory\|agents\|cie\|ready` | stages 4–7; tenant reads use the existing `xsiam_tenant` credential and are priced per query |

All errors are `{error, code, detail}`. No new background loop.

## 12. Phasing

1. **P1, the bundle.** Manifest, `artifact_source` preflight, the `onprem/identity-lab` module,
   Packer and Ansible content, seed fixture. Done when one build on ms-01 from Gitea alone
   reaches stage 4 green, run by hand.
2. **P2, the wizard.** Console destination, stages 1–5, the readiness ladder.
3. **P3, CIE.** Stage 6 with the canary (after capturing `pan_dss_raw`'s real shape), stage 7
   with the training clock.
4. **P4, content.** §8.2 cards, `requires_rung`, `SIM-ITDR-021`, lab-readiness rows for the 14.
5. **P5, proof.** Run `SIM-ITDR-021` against deathstar and reconcile. The first identity
   result that can move `tenant-verified` off 0.

P1 can start as soon as D-1 to D-3 are answered; the 44-day clock argues for building the lab
early and writing the wizard while it trains.

## 13. Decisions for Henry

| # | Decision | Recommendation |
|---|---|---|
| D-1 | Mirror the Windows Server evaluation ISO in a private Gitea org. Microsoft's evaluation EULA was not fetched; the 2025 OEM terms forbid copying beyond a backup. | Get legal sign-off. Fallback: each operator downloads the ISO and uploads it to their own Gitea; the manifest pins the sha256 either way. |
| D-2 | VM profile: 3 VMs (CIE agent on SRV01) or 2 (CIE agent on DC01, which the docs allow). | 3 VMs. Cleaner, and SRV01 doubles as the SPN-backed service. |
| D-3 | Hypervisor: ms-01 (96 GB, also the K3s control plane and Gitea) or r630. | ms-01: the lab needs 12–16 GB and 140–200 GB thin, and libvirt is already there. Watch disk. |
| D-4 | deathstar's entitlements and region: ITDR add-on? XTH? Is CIE already activated for its tenant service group, and in which region? | Read `get_tenant_info` (it timed out twice on 2026-11-11) and check the hub before P3. |
| D-5 | The lab is persistent infrastructure, unlike SimCore. | Accept. Rebuilding resets the 44-day clock. |
| D-6 | Let the DCs reach Microsoft activation, or run unactivated. | Allow activation. What an unactivated evaluation does after day 10 is unverified, and a mid-POV shutdown is the worst failure. |
| D-7 | SimCore drives libvirt over SSH, or renders a bundle the DC runs. | Bundle only in v1, matching the IaC generator and keeping SimCore off the hypervisor. |
| D-8 | A Linux attack host inside the lab subnet for the Impacket-based scenarios: move `pov-target-01` or add a small VM. | Add a VM; `pov-target-01` keeps its own XDR history. |

## 14. What this design does not prove

Nothing in it has run. No build has happened on ms-01, no CIE agent has synced, and no
identity detection has fired in deathstar. The `pan_dss_raw` field names, the CIE agent on a
DC versus a member server, the per-tenant scope of the Analytics baseline, the evaluation's
behaviour after day 10, and whether Rubeus or Impacket behave against Server 2025's Kerberos
defaults are all **unverified** and listed in the sources appendix. `tenant-verified` is 0.
