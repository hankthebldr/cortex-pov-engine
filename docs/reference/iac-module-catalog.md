# IaC Topology Generator — Module Catalog & Reference

> Canonical, exhaustive reference for the CortexSim Infrastructure-as-Code (IaC)
> topology generator: every module, every provisioned resource, every content
> tool, every planted finding/seed, the generate→bundle→download flow, the
> design rules, and every gap found at the time of writing.
>
> **Source of truth files** (read these if this doc drifts):
> - `core/engine/infra_generator.py` — bundle generation logic
> - `core/engine/infra_catalog.py` — module metadata loader (README frontmatter + content.yml)
> - `core/engine/infra_models.py` — Pydantic request/response models + `ALLOWED_MODULES`/`ALLOWED_PROVIDERS`
> - `core/api/infra.py` — `/api/infra/*` FastAPI router
> - `infra/modules/{provider}/{module}/` — the modules themselves
> - `infra/templates/*.j2` — Jinja2 root-bundle templates
>
> Generated/curated: 2026-06-07. Provider coverage on disk: **`aws` only** (10
> wired modules + 1 orphaned module). GCP/Azure/onprem are pending per the phase
> plan (see [Provider Status](#provider-status)).

---

## 1. Quick orientation

The IaC generator turns a DC's module selection into a **self-contained
Terraform bundle** (`tar.gz`) that Torque can consume as a blueprint or that the
DC can `terraform apply` by hand. Modules live on the filesystem under
`infra/modules/{provider}/{module}/`; the generator copies selected module
directories into the bundle and renders a root Terraform config (`main.tf`,
`variables.tf`, `outputs.tf`, `terraform.tfvars`, `README.md`) that wires the
modules together.

Two architectural truths govern everything:

1. **`base` is always included** — every bundle gets the VPC + jumpbox even if the
   DC didn't ask for it (`InfraGenerator._normalize_modules`).
2. **Module metadata lives in `README.md` YAML frontmatter** (not Python).
   Adding/changing a module is a filesystem operation — except for the two hard
   allow-lists in `infra_models.py` (`ALLOWED_MODULES`, `ALLOWED_PROVIDERS`) and
   the per-module `{% if %}` block in `main.tf.j2`/`outputs.tf.j2`, which are the
   two places a new module can silently fail to wire (see gaps GAP-IAC-001,
   GAP-IAC-002).

---

## 2. Module catalog (provider: `aws`)

11 module directories exist on disk under `infra/modules/aws/`. **10** are
reachable through the generator (in `ALLOWED_MODULES`). **1** (`ai-spm`) is fully
built but unreachable (see [Gaps](#gaps)).

Every module's `README.md` carries valid YAML frontmatter (the catalog loader
confirmed all 11 parse; none log the "README.md without frontmatter" warning).

| # | Module | Plane served | Purpose | Has Terraform? | Frontmatter `dependencies` | Reachable via generator? |
|---|--------|--------------|---------|----------------|----------------------------|--------------------------|
| 1 | `base` | (foundation) | VPC + jumpbox + SimCore + SSH keypair; always deployed | yes | `[]` | yes (forced first) |
| 2 | `edr` | EDR (Cortex XDR Agent) | 1–10 Linux target VMs (Ubuntu 22.04 / AL2 alternating) | yes | `[base]` | yes |
| 3 | `cdr` | CDR (Cortex Cloud / Prisma Cloud Compute) | EKS cluster + managed node group | yes | `[base]` | yes |
| 4 | `ndr` | NDR (Network Security / Firewall Analytics) | Segmented network: VPC flow logs, DMZ attack endpoint, NGFW log collector, 3 stitching patterns | yes | `[base]` | yes |
| 5 | `itdr` | ITDR (Cortex ITDR) | Windows AD lab: DC + workstations, seeded roastable accounts | yes | `[base]` | yes |
| 6 | `cspm` | CSPM (Cortex Cloud Posture Mgmt) | Intentionally misconfigured AWS resources (9 planted findings) | yes | `[base]` | yes |
| 7 | `asm` | ASM (Cortex Attack Surface Mgmt) | Deliberately exposed multi-service EC2 + public S3 website | yes | `[base]` | yes |
| 8 | `tim` | TIM (Cortex Threat Intel Mgmt) | TAXII 2.1 server + fake C2 + Route53 IOC domains | yes | `[base]` | yes |
| 9 | `content-library` | (cross-plane content) | Detection-content git clones on the jumpbox; **no cloud resources** | **no** | `[base]` | yes |
| 10 | `telemetry-replay` | (cross-plane content) | Pre-recorded EVTX/PCAP/JSON datasets + replay tooling; **no cloud resources** | **no** | `[base]` | yes |
| 11 | `ai-spm` ⚠️ | AI-SPM (Cortex Cloud Posture Mgmt AI extension) | Heterogeneous AI/ML assets (SageMaker, Bedrock, Lambda→OpenAI, shadow GPU, training PII) — 8 planted findings | yes | `[base]` | **NO — orphaned** (see GAP-IAC-001) |

> **`required_params` / `optional_params`** are advisory frontmatter only — they
> are surfaced via `GET /api/infra/modules` for the UI but the generator does NOT
> enforce them. The only request-level params actually plumbed into Terraform are
> the fixed `InfraGenerateParams` fields (project_name, dc_ssh_cidr, jumpbox_size,
> k8s_node_count, edr_target_count, ttl_hours, tags) — see GAP-IAC-006.

### 2.1 Per-module frontmatter params

| Module | `required_params` | `optional_params` | `providers` (declared) |
|--------|-------------------|-------------------|------------------------|
| `base` | `project_name`, `dc_ssh_cidr` | `jumpbox_size`, `tags` | `[aws]` |
| `edr` | `project_name` | `target_count`, `target_size` | `[aws]` |
| `cdr` | `project_name` | `node_count`, `node_size`, `k8s_version` | `[aws]` |
| `ndr` | `project_name` | `stitching_pattern`, `collector_instance_type`, `attack_endpoint_instance_type` | `[aws]` |
| `itdr` | `project_name` | `ad_domain_name`, `ad_netbios_name`, `dc_instance_type`, `workstation_instance_type`, `workstation_count` | `[aws]` |
| `cspm` | `project_name` | `[]` | `[aws]` |
| `asm` | `project_name` | `exposed_instance_type` | `[aws]` |
| `tim` | `project_name` | `tim_instance_type` | `[aws]` |
| `content-library` | `[]` | `[]` | `[aws, gcp, azure]` ⚠️ (declares 3, only `aws` exists) |
| `telemetry-replay` | `[]` | `[]` | `[aws, gcp, azure]` ⚠️ (declares 3, only `aws` exists) |
| `ai-spm` | `project_name` | `enable_shadow_gpu` | `[aws]` |

> ⚠️ `content-library` and `telemetry-replay` advertise `providers: [aws, gcp,
> azure]` in frontmatter, but only `infra/modules/aws/` exists. Selecting either
> for provider `gcp`/`azure` would pass Pydantic provider validation but fail at
> `module_path()` resolution (`GenerationError: module '…' not available for
> provider 'gcp'`). See GAP-IAC-003.

---

## 3. Per-module deep dive

### 3.1 `base` — foundation (always included)

- **Plane:** none directly; the substrate every other module attaches to.
- **Key resources** (`infra/modules/aws/base/main.tf`):
  `aws_vpc.main` · `aws_internet_gateway.main` · `aws_subnet.public` (×2 AZ) ·
  `aws_subnet.private` (×2 AZ) · `aws_eip.nat` · `aws_nat_gateway.main` ·
  `aws_route_table.{public,private}` + associations ·
  `aws_security_group.jumpbox` (SSH/22 + SimCore UI/8888 from `dc_ssh_cidr`) ·
  `tls_private_key.jumpbox` · `aws_key_pair.jumpbox` ·
  `aws_ssm_parameter.jumpbox_private_key` (private key stored encrypted in SSM) ·
  `aws_instance.jumpbox` (Ubuntu 22.04, runs SimCore + content installer at boot
  via `userdata.sh.tftpl`).
- **Outputs consumed by other modules:** `vpc_id`, `public_subnet_ids`,
  `private_subnet_ids`, `jumpbox_security_group_id`, `ssh_key_name`,
  `jumpbox_public_ip`, `ssh_private_key_ssm_path`.
- **Content tools (3, git-clone, jumpbox only):**
  `sigma` (SigmaHQ/sigma), `mitre-car` (mitre/car),
  `sigma-detection-rules` (mdecrevoisier/SIGMA-detection-rules).
- **Findings/seeds:** none (clean substrate).
- **Cross-refs:** SSH key retrieval path `/cortexsim/<project_name>/jumpbox-ssh-key`.

### 3.2 `edr` — endpoint targets

- **Plane:** EDR (Cortex XDR Agent).
- **Key resources:** `aws_security_group.target` (reachable only from jumpbox +
  peer targets) · `aws_instance.target` (count = `edr_target_count`, 1–10,
  alternating Ubuntu 22.04 / Amazon Linux 2 for diverse telemetry).
- **Output:** `target_private_ips`.
- **Content tools (10):**
  - attack_simulation: `atomic-red-team` (redcanaryco), `edr-testing-script`
    (op7ic/EDR-Testing-Script), `lolbas` (api0cradle/LOLBAS), `sliver`
    (bishopfox/sliver, binary-release).
  - ransomware_simulation: `cipherstrike` (Cursed271/CipherStrike), `ransim`
    (lawndoc/RanSim), `simulate-black-basta` (skandler), `simulate-akira` (skandler).
  - telemetry_samples: `evtx-attack-samples` (sbousseaden), `mordor` (OTRF).
- **Execution note:** content installs on the **jumpbox**, not targets. Jumpbox
  uses the beacon agent to push TTP commands to targets.
- **Findings/seeds:** none planted; signal is generated live by scenarios.
- **Cross-refs (adapters with `install.iac_module: edr`):** `TOOL-MIMIKATZ`,
  `TOOL-HAVOC`, `TOOL-SLIVER`, `TOOL-CALDERA`, `TOOL-STARKILLER`,
  `TOOL-APTSIMULATOR`, `TOOL-PURPLESHARP`, `TOOL-EMPIRE` (8 packs auto-pull `edr`).
- **Cross-refs (scenarios):** multi-plane `mp-001`, `mp-003`, `mp-004`, `mp-005`,
  `mp-002` co-select `edr`.

### 3.3 `cdr` — container / Kubernetes

- **Plane:** CDR (Cortex Cloud / Prisma Cloud Compute).
- **Key resources:** `aws_iam_role.cluster` + `aws_iam_role_policy_attachment.cluster_policy` ·
  `aws_iam_role.node` + 3 node policy attachments (worker, CNI, ECR) ·
  `aws_eks_cluster.main` · `aws_eks_node_group.main` (node_count = `k8s_node_count`, 1–10).
- **Output:** `kubeconfig_command` (`aws eks update-kubeconfig … --name <project>-cdr`).
- **Content tools (11):**
  - attack: `deepce` (stealthcopter), `botb` (brompwnie), `kube-hunter`
    (aquasecurity, pip-install), `light-k8s-attack-simulations` (lightspin-tech),
    `kubehound` (DataDog).
  - defensive: `falco` (docker-pull `falcosecurity/falco:latest`), `falco-rules`
    (falcosecurity/rules), `tetragon` (docker-pull `quay.io/cilium/tetragon:latest`),
    `tracee` (docker-pull `aquasec/tracee:latest`), `trivy` (binary-release),
    `grype` (anchore, binary-release).
- **Findings/seeds:** none planted.
- **Cross-refs (scenarios):** `mp-004-apt29-cloud-cred-theft` and
  `SIM-MP-004` package declare `infra_modules_needed: [base, edr, cdr, tim]`.
  ⚠️ **The `cdr` template block does NOT pass `jumpbox_security_group_id` or
  `ssh_key_name`** (unlike edr/itdr/ndr), so cluster access is jumpbox-routable
  only via VPC networking — verify this is intentional (GAP-IAC-007).

### 3.4 `ndr` — network / stitching

- **Plane:** NDR (Network Security / Firewall Analytics) + multi-plane stitching.
- **Key resources:** `aws_cloudwatch_log_group.flow` + `aws_iam_role.flow` +
  `aws_iam_role_policy.flow` + `aws_flow_log.vpc` (VPC Flow Logs, full format
  string with srcaddr/dstaddr/ports/tcp-flags/pkt-srcaddr/dst) ·
  `aws_security_group.collector` · `aws_security_group.attack_endpoint` ·
  `aws_instance.collector` (private subnet — nginx HTTP sink :8080 + `ackbarx`
  SNMP→HTTP forwarder + `mocktaxii`) · `aws_instance.attack_endpoint` (public/DMZ
  subnet — runs `/opt/cortexsim/attack/beacon.sh` against `testmynids.org`).
- **Outputs:** `collector_private_ip`, `attack_endpoint_public_ip`, `stitching_guidance`.
- **Stitching patterns** (`ndr_stitching_pattern` var, default `external_ngfw_forward`):
  - `marketplace_vmseries` — PAN VM-Series from AWS Marketplace (DC brings license).
  - `external_ngfw_forward` — **default**; existing customer NGFW forwards
    syslog (:514) / HTTP (:8080) to the collector.
  - `suricata_lab` — Suricata IDS stand-in for labs without NGFW.
- **Content tools (6):**
  - network_simulation: `testmynids` (3CORESec/testmynids.org), `redirect-rules`
    (0xZDH/redirect.rules), `c3` (FSecureLABS/C3), `chameleon` (mdsecactivebreach/Chameleon).
  - packet_replay: `tcpreplay-examples` (appneta/tcpreplay).
  - telemetry_samples: `redelk` (outflanknl/RedELK).
  - threat_intel: `mocktaxii` (gocortexio/mocktaxii).
- **Findings/seeds:** generated traffic (HTTP GETs to testmynids.org, short TXT
  DNS queries fingerprinting as DNS-tunnel heuristics). No exfil — pattern only.
- **Cross-refs (adapters):** `TOOL-FRP` declares `install.iac_module: ndr`.
- **Cross-refs (scenarios, `infra_modules_needed`):** `ndr-001`,`ndr-003`,`ndr-005`,
  `ndr-006`,`ndr-007` → `[base, ndr]`; `ndr-002` → `[base, ndr, tim]`; `ndr-004` →
  `[base, ndr, itdr]`; multi-plane `mp-001`,`mp-003`,`mp-005` → `[base, edr, ndr]`.

### 3.5 `itdr` — Windows Active Directory lab

- **Plane:** ITDR (Cortex ITDR).
- **Key resources:** `random_password.ad_admin` · `aws_ssm_parameter.ad_admin_password`
  (SecureString at `/cortexsim/<project>/ad-admin-password`) · `aws_security_group.ad`
  (no public IPs; reachable only via jumpbox SG) · `aws_instance.dc` (Windows
  Server 2022, auto-promotes to new forest on first boot, ~15 min) ·
  `aws_instance.workstation` (count = `itdr_workstation_count`, default 1;
  Server 2022 Core, auto domain-join via 30-attempt retry loop, ~10 min after DC).
- **Outputs:** `dc_private_ip`, `ad_domain_name`, `ad_admin_password_ssm_path`.
- **AD seeding / planted bait:**
  - 50 regular users.
  - 5 misconfigured service accounts — SPN set + weak password `Summer2024`
    (Kerberoast bait, T1558.003).
  - 1 DA-equivalent `helpdesk-admin` with `DoesNotRequirePreAuth` (AS-REP Roast
    bait, T1558.004).
  - krbtgt hash dumpable via DCSync (Golden Ticket pivot, T1558.001).
- **Detection scenarios unlocked (per module README):** Kerberoasting (T1558.003),
  AS-REP Roasting (T1558.004), DCSync (T1003.006), Pass-the-Hash (T1550.002),
  Golden Ticket (T1558.001), BloodHound enum (T1087.002), LDAP recon (T1087.002).
- **Content tools (11):**
  - credential_attacks: `impacket` (fortra, pip), `rubeus` (GhostPack/Rubeus),
    `certipy` (ly4k, pip), `responder` (lgandx/Responder).
  - ad_mapping: `sharphound` (BloodHoundAD/SharpHound, binary), `bloodhound`
    (BloodHoundAD/BloodHound), `bloodhound-python` (dirkjanm/BloodHound.py, pip).
  - identity_simulation: `msinvader` (mvelazc0/msInvader),
    `adversary-emulation-framework` (Aviral2642), `impact` (joeavanzato/impact).
  - credential_dumping_tools: `mimikatz` (gentilkiwi, binary), `pypykatz` (skelsec, pip).
- **Cross-refs (adapters with `install.iac_module: itdr`, 9 packs):** `TOOL-BLOODYAD`,
  `TOOL-TOKENVATOR`, `TOOL-PHISHERY`, `TOOL-BLOODHOUND`, `TOOL-RUBEUS`,
  `TOOL-EVILGINX2`, `TOOL-KRBRELAYUP`, `TOOL-SET`, `TOOL-PRINTSPOOFER`.
- **Cross-refs (scenarios):** `sim-itdr-001..005` → `[base, itdr]`; `ndr-004` and
  multi-plane `mp-002` co-select `itdr`.
- **Security note:** `helpdesk-admin` is **intentionally misconfigured** — destroy
  the environment after the POV.

### 3.6 `cspm` — intentional cloud misconfigurations

- **Plane:** CSPM (Cortex Cloud Posture Management).
- **Key resources (~20):** `random_id.bucket_suffix` · 3 misconfigured S3 buckets
  (`public` with public-read ACL + access-block off + ownership controls +
  `aws_s3_object.dummy`; `unversioned` with versioning disabled; `no_kms`) ·
  `aws_security_group.ssh_open_world` (22 → 0.0.0.0/0) ·
  `aws_security_group.db_open_world` (3306/5432/6379 → 0.0.0.0/0) ·
  `aws_iam_role.admin_role` + `AdministratorAccess` attachment ·
  `aws_iam_user.overprivileged` + inline `iam:*` policy ·
  `aws_ebs_volume.unencrypted` · `aws_s3_bucket.cloudtrail` + policy +
  `aws_cloudtrail.weak` (no log-file validation, not multi-region, no global events).
- **Outputs:** `findings_summary`, `public_bucket_url`.
- **Planted findings (9, each tagged `CortexSimCSPMFinding=<type>`):**

  | Category | Finding | Resource |
  |----------|---------|----------|
  | S3 | Public-read ACL | `*-cspm-public-*` |
  | S3 | Versioning disabled | `*-cspm-unversioned-*` |
  | S3 | No customer-managed KMS | `*-cspm-no-kms-*` |
  | SG | SSH open to 0.0.0.0/0 | `*-cspm-ssh-open` |
  | SG | DB ports (3306/5432/6379) open to world | `*-cspm-db-open` |
  | IAM | Role with `AdministratorAccess` | `*-cspm-admin-role` |
  | IAM | User with `iam:*` wildcard | `*-cspm-overprivileged-user` |
  | EBS | Unencrypted orphaned volume | `*-cspm-unencrypted-vol` |
  | CloudTrail | Weak trail config | `*-cspm-trail` |

- **Content tools (11):** cspm_attack_labs: `cloudgoat` (RhinoSecurityLabs, pip),
  `aws-detonation-lab` (sonofagl1tch/AWSDetonationLab), `stratus-red-team`
  (DataDog, binary), `leonidas` (WithSecureLabs), `endgame` (DavidDikker),
  `aurelian` (praetorian-inc). agentless_scanning: `openclarity`, `threatmapper`
  (deepfence), `trivy`, `grype`. iac_scanners: `lambdaguard` (Skyscanner, pip).
- **Safety:** real misconfigurations — POV-account only, set TTL 24–72h,
  `force_destroy = true` on all S3 buckets.
- **Cross-refs (adapters):** `TOOL-GOPHISH` declares `install.iac_module: cspm`
  ("CSPM IaC ships the cloud public-egress shape gophish needs").

### 3.7 `asm` — exposed attack surface

- **Plane:** ASM (Cortex Attack Surface Management).
- **Key resources:** `random_id.bucket_suffix` · `aws_security_group.exposed` ·
  `aws_instance.exposed` (Ubuntu 22.04, public subnet) ·
  `aws_s3_bucket.website` + public access block + website config + policy +
  `aws_s3_object.index` (public S3 website).
- **Outputs:** `exposed_host_public_ip`, `findings_summary`.
- **Planted exposures (7 ports on the multi-service host):**

  | Port | Service | Finding |
  |------|---------|---------|
  | 80 | nginx | Directory listing + fake admin/config/backup dirs + bait files |
  | 443 | nginx TLS | Self-signed + RSA-1024 + broad cipher list |
  | 2222 | SSH | Non-standard port, password auth enabled |
  | 6379 | Redis | 0.0.0.0 bind, no auth, protected-mode off |
  | 9001 | gocortexbrokenbank | Intentionally vulnerable CI/CD app |
  | 9200 | Fake Elasticsearch | ES v1.7.0 banner (known-vulnerable) |
  | — | Public S3 website | `*.s3-website-<region>.amazonaws.com` discovery |

- **Content tools (7):** vulnerable_apps: `gocortexbrokenbank` (gocortexio).
  honeypots: `cowrie` (micheloosterhof), `dionaea` (DinoTools), `conpot` (mushorg,
  pip), `glastopf` (mushorg), `honeytrap` (honeytrap/honeytrap).
  surface_discovery: `shells` (CodeMonkeyCybersecurity, binary).
- **Cross-refs (adapters):** `TOOL-DVWA` declares `install.iac_module: asm`.

### 3.8 `tim` — threat intelligence + IOC stitching

- **Plane:** TIM (Cortex Threat Intel Management).
- **Key resources:** `aws_security_group.tim` · `aws_instance.tim` (public EC2:
  mocktaxii on :9000 + fake C2 on :8000 logging to `/var/log/fake-c2.log`) ·
  `aws_route53_zone.tim_private` (private zone `<project>-tim.internal`) ·
  `aws_route53_record.ioc_records`.
- **Outputs:** `taxii_endpoint_url`, `fake_c2_url`, `planted_ioc_domains`.
- **Planted IOC seeds (5 Route53 records resolving to the fake C2):**
  `c2-beacon.*`, `exfil-drop.*`, `payload-delivery.*`, `dga-1a2b3c.*`,
  `cryptominer-pool.*` (all under `<project>-tim.internal`).
- **Content tools (10):** threat_intel_sources: `mocktaxii` (gocortexio),
  `unit42-timely-threat-intel` (PaloAltoNetworks), `pan-unit42-public-tools`
  (pan-unit42). ioc_samples: `malware-samples-feed` (MalwareSamples/Malware-Feed),
  `thezoo` (ytisf/theZoo), `malware-souk` (saferwall). yara_rules: `yara-rules`
  (yara-rules/rules), `reversinglabs-yara` (reversinglabs), `awesome-yara`
  (InQuest). malware_source_code: `vxunderground-malware` (vxunderground).
- **Stitching:** pairs with `ndr` + `edr` — endpoint `curl` to a planted IOC
  domain fires TIM IOC match + NDR session log + EDR process lineage → one XSIAM
  incident. See `scenarios/multi_plane/SIM-MP-001`.
- **Cross-refs (scenarios):** `ndr-002` → `[base, ndr, tim]`; `SIM-MP-004` package
  declares `[base, edr, cdr, tim]`.

### 3.9 `content-library` — detection content (content-only)

- **Plane:** cross-plane (customer hand-off content).
- **Terraform resources:** **none** — content-only module (no `main.tf`,
  `variables.tf`, `outputs.tf`; only `README.md` + `content.yml`). The
  `main.tf.j2` template explicitly excludes it ("content-only").
- **Content tools (7):** cortex: `xql-hub` (intrusus-dev), `cortex-xql-queries`
  (PaloAltoNetworks), `xdr-scripts` (k4nfr3/XDR_scripts), `cortexxdr-bioc`
  (Data-Equipment-AS/CortexXDR-BIOC). splunk: `splunk-security-content`
  (splunk/security_content). elastic: `elastic-detection-rules` (elastic).
  chronicle: `chronicle-detection-rules` (chronicle).
- **Findings/seeds:** none.

### 3.10 `telemetry-replay` — pre-recorded datasets (content-only)

- **Plane:** cross-plane (parser/correlation validation without live attacks).
- **Terraform resources:** **none** — content-only (only `README.md` + `content.yml`).
- **Content tools (14):**
  - attack_mapped_evtx: `evtx-attack-samples` (sbousseaden), `evtx-to-mitre-attack`
    (mdecrevoisier, 270+ samples), `hayabusa-sample-evtx` (Yamato-Security).
  - enterprise_simulations: `mordor` (OTRF), `cyber-simulation` (gregdiy, 7.9M logs).
  - ml_datasets: `malbehavd` (mpasco/MalbehavD-V1), `awesome-malware-benign-datasets`
    (0xh3xa), `mh-100k-dataset` (Malware-Hunter), `dikedataset` (iosifache),
    `markov-malware-images` (julismail/Markov), `nlp-pdf-malware` (bliutech).
  - edr_coverage_data: `edr-telemetry` (tsale/EDR-Telemetry).
  - replay_tooling: `chainsaw` (WithSecureLabs, binary), `sigma-rules-crawler`
    (SimoneCagol), `tcpreplay` (appneta).
- **Findings/seeds:** none (datasets are the payload).
- **Note:** README prose mentions `hayabusa` as a tool but it ships only indirectly
  via the `hayabusa-sample-evtx` repo — there is no `hayabusa` binary entry in
  `content.yml` (minor doc/content mismatch, GAP-IAC-008). README also lists
  `MalbehavD-V1`/`awesome-malware-benign-datasets`/`mh-100k`/`dikedataset`/
  `markov-malware-images` but the README ML list omits `nlp-pdf-malware` which
  IS in content.yml — minor list drift.

### 3.11 `ai-spm` — AI/ML posture assets ⚠️ ORPHANED

- **Plane:** AI-SPM (Cortex Cloud Posture Management AI extension).
- **Status:** **Fully built but unreachable through the generator.** Not in
  `ALLOWED_MODULES`, and `main.tf.j2`/`outputs.tf.j2` have no `ai-spm` block. See
  GAP-IAC-001 (critical).
- **Key resources (14):** `random_id.suffix` · `aws_s3_bucket.training_data` +
  `aws_s3_object.training_pii_fixture` + `aws_s3_object.pickled_model` ·
  `aws_iam_role.sagemaker_overprivileged` + `aws_iam_role_policy_attachment.sagemaker_admin` ·
  `aws_sagemaker_model.poisoning_candidate` · `aws_sagemaker_endpoint_configuration.canary` ·
  `aws_sagemaker_endpoint.canary` · `aws_iam_role.lambda_openai` +
  `aws_iam_role_policy_attachment.lambda_basic` · `aws_lambda_function.openai_proxy`
  (hardcoded canary key `sk-DEMO-CORTEXSIM-AISP-04-PLANTED`) ·
  `aws_security_group.shadow_gpu` · `aws_instance.shadow_gpu_llm` (g4dn.xlarge,
  gated behind `enable_shadow_gpu=true`, default false).
- **Output:** `findings_summary` (confirmed present in outputs.tf).
- **Planted findings (8, each tagged `CortexSimAISPMFinding=<type>`):**

  | Category | Finding | Resource | TC coverage |
  |----------|---------|----------|-------------|
  | Managed AI | SageMaker endpoint (canary) | `*-aispm-sagemaker-endpoint` | AISP-01, AISP-02 |
  | Managed AI | SageMaker model w/ insecure pickle artifact | `*-aispm-pickled-model` | AISP-04 |
  | Managed AI | Bedrock invocation logging disabled | account-wide | AISP-02 |
  | Third-party AI | Lambda w/ hardcoded OpenAI key in env | `*-aispm-openai-proxy` | AISP-01, AISP-04 |
  | Shadow AI | EC2 g4dn w/ Ollama LLM container (opt-in) | `*-aispm-shadow-gpu-llm` | AISP-01 (headline) |
  | Training data | S3 bucket w/ PII/PHI/PCI canary fixtures | `*-aispm-training-data` | AISP-05 |
  | IAM | SageMaker execution role w/ `*:*` policy | `*-aispm-sagemaker-overprivileged` | AISP-02 |
  | Supply chain | Lambda layer w/ vulnerable ML deps | `*-aispm-vulnerable-ml-deps` | AISP-03 |

  > ⚠️ The README lists a "Bedrock invocation logging disabled" finding and a
  > "Lambda layer with vulnerable ML deps" finding, but `main.tf` shows no
  > `aws_bedrock*` resource and no `aws_lambda_layer_version` resource. These two
  > findings appear **declared in docs but not implemented in HCL** (GAP-IAC-005).

- **Content tools (6):** ai_spm_validation: `pyrit` (Azure/PyRIT, pip), `garak`
  (NVIDIA/garak, pip), `ai-exploits` (protectai), `model-scan` (protectai/modelscan,
  pip). static_analysis: `bandit` (pip, no repo field), `semgrep` (pip, no repo field).
- **Cross-refs (scenarios):** `sim-aispm-001..006` all declare
  `infra_modules_needed: [base, ai-spm]`. The `scenarios/ai_spm/README.md` even
  claims "The IaC generator auto-suggests the module when an AI-SPM scenario is
  selected" — **false today** because `ai-spm` is rejected at Pydantic validation
  (GAP-IAC-001).
- **References (from module README):** `docs/uc_tc_mapping/methodology-ai-spm.md`,
  `docs/uc_tc_mapping/v2.0-methodology-master.md`.

---

## 4. Generate → bundle → download flow

End-to-end, driven by `core/api/infra.py` + `core/engine/infra_generator.py`:

```
POST /api/infra/generate   (InfraGenerateRequest)
   │
   ▼  InfraGenerator.generate(request)
   1. _resolve_adapter_modules(request.adapter_refs)
        → for each adapter_ref: adapter_catalog.find(ref)
            • not found            → binding (ref,"unresolved",None)   [non-fatal]
            • found, no iac_module → binding (ref,"no-iac",None)
            • found, has iac_module→ binding (ref,"resolved",module) + collect module
   2. _normalize_modules(request.modules + auto_modules)
        → ALWAYS prepend "base"; dedupe; preserve order
   3. validate each module exists on disk for provider
        → catalog.module_path(provider, m) is None ⇒ GenerationError (HTTP 422)
   4. allocate bundle dir  infra/blueprints/<uuid>/
   5. copy each module dir → <bundle>/modules/<m>/  (shutil.copytree)
        → _copy_ignore strips: .terraform, .terraform.lock.hcl,
          terraform.tfstate(.backup), .DS_Store, __pycache__
   6. render 5 required templates → main.tf, variables.tf, outputs.tf,
          terraform.tfvars, README.md   (Jinja2 StrictUndefined, autoescape off)
   7. if adapter_refs present: write ADAPTERS.md provenance table
   8. tar.gz → infra/blueprints/<uuid>.tar.gz  (arcname=<uuid>)
        → on ANY exception: rmtree the partial bundle, raise GenerationError
   │
   ▼  InfraGenerateResponse
        { bundle_id, provider, modules, download_url,
          files[], auto_included_modules[] }

GET /api/infra/bundles/<bundle_id>/download
   → FileResponse of <uuid>.tar.gz as cortexsim-infra-<uuid>.tar.gz
```

**Other endpoints:**

| Method + path | Behavior | Notes |
|---------------|----------|-------|
| `GET /api/infra/modules?provider=aws` | `InfraCatalog.list_modules` → `{modules:[…], total}` | provider defaults to `aws`; builds a fresh `InfraCatalog` each call (not the cached generator) |
| `POST /api/infra/generate` | as above; `GenerationError` → HTTP 422 `{error, code:"GENERATION_FAILED", detail}` | |
| `GET /api/infra/bundles` | `InfraGenerator.list_bundles` → `{bundles:[…], total}` | parses `# Provider:` / `# Modules:` from each bundle's `main.tf` header (first 8 lines) |
| `GET /api/infra/bundles/{id}/download` | tar.gz or HTTP 404 `{error, code:"BUNDLE_NOT_FOUND", detail}` | |

**Adapter-driven auto-inclusion (the interesting bit):** A scenario referencing
`{adapter:TOOL-RUBEUS}` can pass `adapter_refs: ["TOOL-RUBEUS"]`; the generator
reads that adapter's `install.iac_module: itdr` and folds `itdr` into the bundle
even if the DC didn't select it. `auto_included_modules[]` in the response and
`ADAPTERS.md` in the bundle surface exactly what the auto-pull did. Unresolved
refs are **never fatal** — they appear as an "unresolved" row in ADAPTERS.md.

**Bundle filesystem layout:**

```
<bundle_id>/
  main.tf            # header: # Bundle ID / # Provider / # Modules / # Generated
  variables.tf
  outputs.tf
  terraform.tfvars
  README.md
  ADAPTERS.md        # only if adapter_refs[] was non-empty
  modules/
    base/  …         # always
    <module>/  …     # one dir per selected/auto-included module
```

---

## 5. Design rules (IaC-specific, enforced)

| Rule | Where enforced | Notes |
|------|----------------|-------|
| `base` always included, always first | `_normalize_modules` (hardcoded `out = ["base"]`) | even if request omits it |
| Module must exist on disk for the provider | `generate()` step 3, `catalog.module_path` | else `GenerationError` → 422 |
| Module name must be in allow-list | `InfraGenerateRequest._validate_modules` vs `ALLOWED_MODULES` | **Pydantic rejects unknown names BEFORE the generator runs** — this is why `ai-spm`/`airs` fail (GAP-IAC-001/002) |
| Provider must be aws/gcp/azure | `Literal["aws","gcp","azure"]` + `ALLOWED_PROVIDERS` | only `aws` actually has modules on disk |
| Never bundle `.terraform/` / lock / state artifacts | `_copy_ignore` callback | regression-guarded in tests |
| Bundles are stateless artifacts | no DB; `infra/blueprints/` is source of truth | `list_bundles` reparses `main.tf` headers |
| Static `TOOL_REGISTRY` always wins over installed content | `core/content_loader.py` | not generator code, but related content flow |
| Module metadata lives in README frontmatter | `InfraCatalog._load_module_metadata` | adding a module = filesystem-only … **except** ALLOWED_MODULES + template `{% if %}` block |
| All API errors are structured JSON | `core/api/infra.py` | `{error, code, detail}` shape |
| `dc_ssh_cidr` validated as CIDR | `InfraGenerateParams._validate_cidr` | `ipaddress.ip_network(strict=False)` |
| `project_name` lowercase-hyphen, 3–48 chars | Pydantic `pattern=^[a-z0-9][a-z0-9-]*$` | used as resource prefix |

---

## 6. Provider status

| Provider | In `ALLOWED_PROVIDERS`? | Modules on disk | Phase | Status |
|----------|------------------------|-----------------|-------|--------|
| `aws` | yes | 11 dirs (10 wired + `ai-spm` orphaned) | Phase A + B-1 + B-2 | **feature-complete** for wired set |
| `gcp` | yes (accepted by Pydantic) | **none** | Phase C | **pending — request passes validation then fails `module_path`** |
| `azure` | yes (accepted by Pydantic) | **none** | Phase D | **pending — same trap as gcp** |
| `onprem` | **no** (not in Literal/allow-list) | none | Phase E (design only) | Ansible + Docker Compose, not built |

> CLAUDE.md claims "AWS is feature-complete with **10 modules**." That count
> matches `ALLOWED_MODULES` (10) but **excludes the 11th on-disk module `ai-spm`**,
> which is the orphaned one. So "10 modules" is simultaneously the wired count and
> an undercount of what's actually on disk.

---

## 7. Cross-reference index

### 7.1 Adapters → IaC module (`install.iac_module`)

20 tool-adapter packs declare an `iac_module` (excluding the `_schema.yml`
example). Auto-pulled into a bundle when passed via `adapter_refs[]`:

| iac_module | Adapter packs |
|------------|---------------|
| `edr` | TOOL-MIMIKATZ, TOOL-HAVOC, TOOL-SLIVER, TOOL-CALDERA, TOOL-STARKILLER, TOOL-APTSIMULATOR, TOOL-PURPLESHARP, TOOL-EMPIRE |
| `itdr` | TOOL-BLOODYAD, TOOL-TOKENVATOR, TOOL-PHISHERY, TOOL-BLOODHOUND, TOOL-RUBEUS, TOOL-EVILGINX2, TOOL-KRBRELAYUP, TOOL-SET, TOOL-PRINTSPOOFER |
| `ndr` | TOOL-FRP |
| `asm` | TOOL-DVWA |
| `cspm` | TOOL-GOPHISH |

> No adapter declares `iac_module: cdr`, `tim`, `ai-spm`, `content-library`, or
> `telemetry-replay`. Those modules are only reachable via explicit `modules[]`
> selection or scenario `infra_modules_needed`.

### 7.2 Scenarios → `infra_modules_needed` (advisory hints)

`infra_modules_needed` is an optional scenario-schema field (default `[]`). It is
NOT auto-consumed by the generator's request path today (the generator reads
`modules[]` + `adapter_refs[]`, not scenario YAML). It is a UI/operator hint —
the `scenarios/ai_spm/README.md` claim that the generator "auto-suggests" is
aspirational. Observed declarations:

| Modules declared | Scenarios |
|------------------|-----------|
| `[base]` only | all cloud_app (5), all ai_access (5), all browser (5), all koi (5) |
| `[base, ndr]` | ndr-001, ndr-003, ndr-005, ndr-006, ndr-007 |
| `[base, ndr, tim]` | ndr-002 |
| `[base, ndr, itdr]` | ndr-004 |
| `[base, itdr]` | sim-itdr-001..005 |
| `[base, edr, ndr]` | mp-001, mp-003, mp-005 |
| `[base, itdr, edr]` | mp-002 |
| `[base, edr, cdr]` | mp-004 |
| `[base, edr, cdr, tim]` | SIM-MP-004 package README |
| `[base, ai-spm]` ⚠️ | sim-aispm-001..006 (module is orphaned, GAP-IAC-001) |
| `[base, airs]` ⚠️ | sim-airs-001..005 (no `airs` module exists, GAP-IAC-002) |

---

## 8. Gaps

See the structured output for the canonical, severity-ranked gap list. Summary:

- **GAP-IAC-001 (critical):** `ai-spm` module is fully built (14 resources, valid
  frontmatter, content.yml, `findings_summary` output) but unreachable — not in
  `ALLOWED_MODULES`, no `main.tf.j2`/`outputs.tf.j2` block. 6 AI-SPM scenarios
  point at it; requests are rejected at Pydantic validation.
- **GAP-IAC-002 (high):** 5 AIRS scenarios declare `infra_modules_needed: [base,
  airs]` but no `airs` module exists on disk and `airs` is not in `ALLOWED_MODULES`.
- **GAP-IAC-003 (medium):** `content-library` + `telemetry-replay` advertise
  `providers: [aws, gcp, azure]` but only `aws` exists; gcp/azure selection passes
  provider validation then fails at `module_path`.
- **GAP-IAC-004 (medium):** `gcp`/`azure` are in `ALLOWED_PROVIDERS`/the request
  Literal but have zero modules — every request 422s at `module_path` (even for
  `base`). Misleading "accepted then fails" UX.
- **GAP-IAC-005 (medium):** `ai-spm` README documents a "Bedrock invocation
  logging disabled" finding and a "Lambda layer with vulnerable ML deps" finding,
  but `main.tf` has no `aws_bedrock*` or `aws_lambda_layer_version` resource.
- **GAP-IAC-006 (low):** Frontmatter `required_params`/`optional_params` are
  surfaced to the UI but never enforced or plumbed; only the fixed
  `InfraGenerateParams` fields reach Terraform. Module-specific knobs
  (`ad_domain_name`, `node_size`, `target_size`, `stitching_pattern`, etc.) are
  not settable via the API.
- **GAP-IAC-007 (low):** `cdr` template block omits `jumpbox_security_group_id`
  and `ssh_key_name` (unlike edr/itdr/ndr) — verify cluster reachability from the
  jumpbox is intentional.
- **GAP-IAC-008 (low):** Minor doc/content drift in `telemetry-replay` (`hayabusa`
  named in README prose but not in content.yml; README ML list omits
  `nlp-pdf-malware` which is in content.yml).
- **GAP-IAC-009 (low):** `ttl_hours` is a request param but is never rendered into
  any template (not in `terraform.tfvars.j2` or `variables.tf.j2`) — it is a
  no-op "hint for Torque TTL" that nothing consumes.
