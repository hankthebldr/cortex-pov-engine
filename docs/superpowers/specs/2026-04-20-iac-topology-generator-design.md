# IaC Topology Generator — Design

**Status:** Draft for implementation
**Owner:** Henry Reed
**Date:** 2026-04-20

## Problem

CortexSim today requires a pre-provisioned Linux jumpbox. DCs running POVs in new customer environments have no turnkey way to stand up the target infrastructure (jumpbox, K8s cluster, target VMs, exposed services, intentionally misconfigured cloud resources) that CortexSim generates signals against. They cobble together Terraform by hand, which wastes POV cycles and produces inconsistent environments.

## Goal

A feature that lets a DC select which detection planes they want to demo, choose a cloud provider (AWS, GCP, or Azure), and download a complete Terraform bundle that Torque can instantiate as a blueprint. The bundle provisions both the infrastructure *and* the open-source attack/defense content that makes the infrastructure useful for demonstrating Cortex value.

## Non-Goals

- **Not integrating with the Torque API.** Torque consumes the generated Terraform as blueprints on its own. CortexSim only produces the IaC.
- **Not deploying Cortex products.** The DC handles XDR Agent / Prisma Defender / Cortex Cloud agent installation and configuration separately.
- **Not managing cloud credentials at rest.** Credentials live in Torque; CortexSim never stores them.
- **Not orchestrating the deploy/destroy lifecycle.** Torque handles TTL, auto-destroy, and environment state.

## High-Level Architecture

```
DC (UI)
  |
  | POST /api/infra/generate
  v
SimCore (core/api/infra.py)
  |
  | reads module metadata + content manifests
  v
Generator (core/engine/infra_generator.py)
  |
  | Jinja2 templates + module files
  v
Bundle (infra/blueprints/{bundle_id}/)
  |
  | DC downloads as tar.gz  OR  Torque reads directly from repo
  v
Torque
  |
  | terraform apply with DC's cloud credentials
  v
Cloud Environment (AWS / GCP / Azure)
  |
  | Jumpbox boots, cloud-init runs install-content.sh
  v
Ready: SimCore + attack tools + detection content + targets
```

## Components

### 1. Terraform Module Library (`infra/modules/`)

Real, reviewable HCL modules organized by cloud provider and logical function. Each module is independently testable and follows Terraform standard layout (`main.tf`, `variables.tf`, `outputs.tf`, `README.md`).

```
infra/
├── modules/
│   ├── aws/
│   │   ├── base/           # VPC, jumpbox, SGs, NAT (always deployed)
│   │   ├── cdr/            # EKS cluster + workers
│   │   ├── edr/            # Target EC2 instances (diverse OS)
│   │   ├── ndr/            # Multi-subnet, flow logs, packet mirroring
│   │   ├── itdr/           # Windows EC2 + AD domain
│   │   ├── tim/            # mocktaxii + DNS entries + C2 beacon target
│   │   ├── asm/            # Exposed services + honeypots as realistic targets
│   │   ├── cspm/           # Intentionally misconfigured resources
│   │   ├── content-library/    # No infra — content install scripts only
│   │   └── telemetry-replay/   # No infra — replay tooling + datasets
│   ├── gcp/                # Same structure — GKE, Compute Engine, VPC
│   └── azure/              # Same structure — AKS, VM, VNet
├── blueprints/             # Generated outputs (gitignored — produced per request)
└── templates/
    ├── main.tf.j2          # Root module template — wires selected modules
    ├── variables.tf.j2
    ├── outputs.tf.j2
    ├── terraform.tfvars.j2
    └── README.md.j2        # DC-facing: what got deployed and how to connect
```

**Module output contract.** Every `base` module exposes a standard set of outputs so downstream modules can consume them without cloud-specific branching:

- `vpc_id`, `public_subnet_ids`, `private_subnet_ids`
- `jumpbox_public_ip`, `jumpbox_private_ip`, `jumpbox_security_group_id`
- `ssh_key_name` (provider-specific key resource reference)
- `region`, `project_name`

Other modules (`cdr`, `edr`, etc.) take these as inputs. This keeps the root module composition mechanical.

### 2. Content Manifests

Each module has an associated `content.yml` declaring what open-source tools and datasets get installed on the provisioned infrastructure:

```yaml
# infra/modules/aws/edr/content.yml
tools:
  attack_simulation:
    - name: atomic-red-team
      repo: redcanaryco/atomic-red-team
      install: git-clone
      install_path: /opt/cortexsim/content/edr/atomic-red-team
    - name: sliver
      repo: bishopfox/sliver
      install: binary-release
  ransomware_sim:
    - name: CipherStrike
      repo: Cursed271/CipherStrike
    - name: RanSim
      repo: lawndoc/RanSim
  telemetry_samples:
    - name: EVTX-ATTACK-SAMPLES
      repo: sbousseaden/EVTX-ATTACK-SAMPLES
      purpose: "Replay into XSIAM for parser validation"
```

**Module → Content mapping (consolidated):**

| Module | Infrastructure | Content |
|--------|---------------|---------|
| `base` | Jumpbox + SimCore | SigmaHQ/sigma, mitre/car, SIGMA-detection-rules |
| `cdr` | K8s cluster | Attack: deepce, botb, kube-hunter, light-k8s-attack-simulations, KubeHound. Defense: falco+rules, tetragon, tracee, trivy, grype |
| `edr` | Linux/Windows VMs | Attack: atomic-red-team, EDR-Testing-Script, LOLBAS, sliver, EDR-GhostLocker, BamboozlEDR. Ransomware: CipherStrike, RanSim, simulate-black-basta, simulate-akira. Samples: EVTX-ATTACK-SAMPLES, mordor |
| `ndr` | Multi-subnet + flow logs | testmynids.org, RedELK, C3, redirect.rules |
| `itdr` | Windows AD | msInvader, impact, adversary-emulation-framework |
| `tim` | mocktaxii + DNS | Unit42-timely-threat-intel, MalwareSamples/Malware-Feed, theZoo, vxunderground/MalwareSourceCode, yara-rules, reversinglabs-yara-rules |
| `asm` | Exposed services | gocortexbrokenbank, cowrie, dionaea, conpot, glastopf |
| `cspm` | Misconfigured resources | CloudGoat, AWSDetonationLab, stratus-red-team, leonidas, endgame, aurelian |
| `content-library` | (none) | xql-hub, cortex-xql-queries, XDR_scripts, CortexXDR-BIOC, splunk/security_content, elastic/detection-rules, chronicle/detection-rules |
| `telemetry-replay` | (none) | EVTX-ATTACK-SAMPLES, mordor, cyber_simulation, MalbehavD-V1, EDR-Telemetry + replay tooling |

### 3. Content Installer

A single script baked into the jumpbox via cloud-init:

```bash
/opt/cortexsim/install-content.sh --modules=base,edr,cdr,cspm
```

For each module, reads `content.yml`, downloads/clones each entry to `/opt/cortexsim/content/{module}/{tool}/`, and writes entries to `/opt/cortexsim/content/installed.json`. SimCore gains a small `content_loader.py` module that reads this file on startup and merges installed-content entries into the in-memory `TOOL_REGISTRY` dict (alongside the static entries for signalbench, mocktaxii, etc.), so the content surfaces in the existing UI tool panel and API without schema changes. If the file is missing or empty, SimCore behaves exactly as today.

Install strategies supported:
- `git-clone` — `git clone --depth 1` of a public repo
- `binary-release` — download latest GitHub release asset for the host OS/arch
- `docker-pull` — pull a container image (for tools that ship as containers)
- `pip-install` — `pip install` a Python package from a repo

### 4. Scenario → Module Dependency Hint

The scenario YAML schema (`scenarios/_schema.yml`) gains optional fields:

```yaml
required_content:
  - repo: huntergregal/mimipenguin
  - repo: bishopfox/sliver
infra_modules_needed:
  - edr
  - base
```

When the DC is browsing scenarios in the UI, the generator page can pre-select the right modules based on scenarios they've bookmarked. Backward compatible — scenarios without these fields just don't contribute to auto-suggestions.

### 5. Generator Engine (`core/engine/infra_generator.py`)

Pure Python module, no new runtime dependencies beyond Jinja2 (already transitive via FastAPI/Starlette).

```python
class InfraGenerator:
    def generate(self, request: InfraRequest) -> InfraBundle:
        """
        1. Validate provider + module combination
        2. Load module metadata + content manifests
        3. Render root Terraform (main.tf, variables.tf, outputs.tf, terraform.tfvars)
        4. Copy selected module directories into bundle
        5. Write bundle README with deployment instructions
        6. tar.gz the bundle under infra/blueprints/{bundle_id}/
        7. Return bundle ID + file list
        """
```

Validation rules enforced at generation time:
- `base` module is always included (even if not explicitly requested)
- Invalid module names rejected with structured 400 error
- Cross-module dependencies enforced (e.g., `cdr` requires `base`)
- Provider-specific parameter validation (e.g., AWS region format, instance types)

### 6. API Surface

```
POST /api/infra/generate
Body: {
  "provider": "aws" | "gcp" | "azure",
  "region": "us-east-1",
  "modules": ["base", "cdr", "edr", "cspm"],
  "params": {
    "project_name": "acme-pov-2026",
    "jumpbox_size": "t3.medium",
    "k8s_node_count": 2,
    "edr_target_count": 2,
    "dc_ssh_cidr": "203.0.113.0/32",
    "ttl_hours": 72,
    "tags": {"customer": "acme", "owner": "hreed@paloaltonetworks.com"}
  }
}
Response: {
  "bundle_id": "uuid",
  "provider": "aws",
  "modules": ["base", "cdr", "edr", "cspm"],
  "download_url": "/api/infra/bundles/{bundle_id}/download",
  "files": ["main.tf", "variables.tf", "outputs.tf", "terraform.tfvars", "README.md", "modules/..."]
}

GET /api/infra/bundles/{bundle_id}/download
  → .tar.gz of the Terraform root module

GET /api/infra/modules[?provider=aws]
  → {
      "modules": [
        {"name": "base", "description": "...", "required_params": [...], "providers": ["aws","gcp","azure"]},
        ...
      ]
    }

GET /api/infra/bundles
  → list of previously generated bundles (for re-download)
```

No DB schema changes needed. Bundles live on disk at `infra/blueprints/{bundle_id}/`. A lightweight in-memory index tracks bundle metadata; restart loses the index but existing bundles remain downloadable via directory listing.

### 7. UI Panel

New component: `ui/src/components/InfraGenerator.jsx`. Accessible from the header (new "Deploy" button alongside MITRE and Runs).

Form layout:
1. **Provider selector** (radio: AWS / GCP / Azure)
2. **Module checklist** — each module card shows: name, what infra it provisions, what content installs, provider support badges, dependencies
3. **Parameters form** — dynamically shown based on selected modules
4. **Generate button** → calls `POST /api/infra/generate` → offers download + copy-to-clipboard Torque blueprint reference
5. **Bundle history** — list of previously generated bundles with re-download links

## Data Model

No new database tables. Generated bundles are file-system artifacts. The module catalog is static metadata loaded from module `README.md` frontmatter and `content.yml` files at API-request time (cached after first read per server lifetime).

## Error Handling

All error responses follow the existing CortexSim contract: `{"error": "...", "code": "...", "detail": "..."}`. Specific error codes:

- `PROVIDER_UNSUPPORTED` — requested provider doesn't exist
- `MODULE_UNSUPPORTED_FOR_PROVIDER` — module exists but not for this cloud
- `INVALID_PARAMS` — parameter validation failed (with per-field detail)
- `GENERATION_FAILED` — template rendering or file copy error (internal)
- `BUNDLE_NOT_FOUND` — download requested for unknown bundle_id

## Testing Strategy

1. **Unit tests** — `InfraGenerator` logic with mocked filesystem: validates module selection, param validation, template rendering.
2. **Terraform validation** — generated bundles pass `terraform validate` and `terraform plan` against all three clouds. Run as CI job per-cloud using provider-specific mocks (localstack, gcloud fake credentials, azurerm testing SDK) where possible, otherwise guarded real-account integration tests.
3. **Content install dry-run** — a test mode where `install-content.sh` runs with `--dry-run` and reports what it *would* download. Validates manifests without network pulls.
4. **End-to-end smoke** — provision a minimal bundle (`base` only) against a real AWS account via Terraform, verify SSH reachability, SimCore health endpoint, and TTL auto-destroy.

## Delivery Phases

1. **Phase A: AWS + base + edr + cdr + content-library** — smallest end-to-end slice, validates the architecture.
2. **Phase B: cspm + asm + tim + telemetry-replay** — adds cloud-security-focused modules on AWS.
3. **Phase C: GCP provider** — port all modules to GCP.
4. **Phase D: Azure provider + itdr module** — ITDR goes last because Windows AD is complex and ITDR is less mature than other planes.

Each phase is independently shippable and mergeable.

## Open Questions for Later

- Do we version-pin content repos (reproducibility) or pull latest (freshness)? Suggest: pin to a commit SHA in content.yml, with periodic bot-generated PRs to bump versions.
- How does the DC get the SSH private key out of Terraform state securely? Suggest: generated keypair stored in cloud-specific secret store (AWS SSM Parameter Store / GCP Secret Manager / Azure Key Vault), output shows the secret path.
- Do we need a "verify bundle" endpoint (pre-flight check that the DC's parameters are valid) before full generation? Probably yes for the UI experience; design doesn't block on it.

## On-Prem Provider (Design Addendum — Phase E)

Customers frequently run POVs inside existing on-prem infrastructure where Torque + cloud Terraform aren't usable. The design extends naturally to cover this case.

### Approach

Add `"onprem"` as a fourth provider alongside `aws/gcp/azure`. The module pattern stays identical — `infra/modules/onprem/{base,edr,itdr,ndr,...}/` — but module contents produce **Ansible playbooks + Docker Compose**, not Terraform HCL. The generator's module copy and Jinja2 rendering work unchanged; only the template bodies differ.

```
infra/modules/onprem/base/
├── README.md           (YAML frontmatter — same format as cloud modules)
├── content.yml         (same format — content installer reads it the same way)
├── playbook.yml        (Ansible playbook instead of main.tf)
├── inventory.yml.j2    (Ansible inventory template, rendered with DC's host list)
└── docker-compose.yml  (services that run on the jumpbox itself)
```

### Root bundle shape for on-prem

The generator produces a bundle that includes:
- `run.sh` — top-level bootstrap (runs `ansible-playbook`)
- `inventory.yml` — rendered with DC's target host IPs/credentials
- `playbook.yml` — imports each selected module's playbook
- `docker-compose.yml` — composed from each module's compose fragments (networks merged)
- `modules/{module}/` — per-module assets

### What changes vs. cloud

| Concern | Cloud (Terraform) | On-prem (Ansible + Compose) |
|---------|------------------|----------------------------|
| Compute provisioning | Terraform resources | DC provides existing VMs in inventory |
| Identity harness | cloud-init user_data | Ansible tasks using `become_user` |
| K8s for CDR | Managed EKS/GKE/AKS | Assume customer k8s cluster; playbook applies manifests |
| Windows for ITDR | Cloud Windows VMs | DC's existing Windows hosts via WinRM |
| Secrets | SSM/Secret Manager | Ansible vault files, DC manages |
| Teardown | `terraform destroy` | `ansible-playbook playbook.yml --tags cleanup` |

### Phase E implementation plan (separate phase, not this plan)

Port the existing four modules (base/edr/cdr/content-library) to Ansible equivalents. Base becomes a playbook that installs SimCore on a designated jumpbox host; EDR becomes a playbook that joins target hosts and installs the beacon agent; CDR becomes a playbook that applies K8s manifests to an existing cluster. Content installer stays the same (install-content.sh is OS-native, not cloud-specific).

---

## NDR Stitching Patterns (Design Addendum)

The NDR module intentionally supports three deployment patterns so DCs can match whatever their customer environment has. The Terraform module provisions only the **surrounding topology** (segmented VPC, attack endpoint, flow logs, traffic mirroring, log ingestion endpoint). The firewall itself is plugged in according to one of these patterns:

### Pattern A: PAN VM-Series in AWS Marketplace

The module outputs guidance for deploying VM-Series from the AWS Marketplace as a gateway. Requires the DC to accept marketplace terms and bring a PANW license. VM-Series sits between the internet gateway and the protected subnets, and forwards session logs via PAN-OS HTTP log forwarding to the XSIAM HTTP collector endpoint that the module provisions on the jumpbox.

### Pattern B: Existing on-prem NGFW with log forwarding only

Module provisions just the collector side: an EC2 endpoint running `ackbarx` (already in `sources/`) that accepts PAN-OS syslog/HTTP logs forwarded from the customer's existing NGFW. The DC configures the customer NGFW's log forwarding profile to target this endpoint's public IP. Useful when the DC's POV is about proving XSIAM can correlate existing firewall logs with XDR endpoint logs.

### Pattern C: Suricata as a lightweight NGFW stand-in

For lab environments with no PAN NGFW available, the module provisions a small Linux VM running Suricata in IDS mode on a spanned interface. Suricata flow/alert logs forward to XSIAM via the same HTTP collector. Not production-realistic but useful for demonstrating the stitching pattern itself.

### Stitching payload

Regardless of pattern, the NDR module deploys an **attack endpoint** in the DMZ subnet that runs scripted network activity:
- Beaconing to simulated C2 endpoints (testmynids.org-derived patterns)
- DNS tunneling exfil
- Protocol abuse (HTTPS over non-443 port, DNS over TCP)
- Simulated lateral movement via SMB to internal-subnet targets

Each activity is designed to produce a matching alert on both the firewall side (network layer) and the XDR endpoint side (process lineage). The multi-plane scenarios exercise these stitched detections — that's where XSIAM's correlation gets validated.

---

## Success Criteria

1. A DC can, from scratch, select AWS + `base` + `edr` + `cspm`, generate a bundle, hand it to Torque, and have a working POV environment in under 20 minutes.
2. The generated Terraform passes `terraform validate` for all three cloud providers for every valid module combination.
3. Content installation on the jumpbox completes without manual intervention and tools appear in the CortexSim UI tool panel.
4. The bundle's README is clear enough for a DC who hasn't used the feature before to understand what was deployed and how to access it.
