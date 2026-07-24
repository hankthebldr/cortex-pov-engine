---
name: port-infra-module
description: Port a CortexSim IaC module to a new cloud provider (Phase C GCP, Phase D Azure) — reads the AWS module as source of truth, maps each planted finding to a provider-native equivalent, writes the module directory with catalog-loadable frontmatter, and reports an explicit parity verdict. Also covers the one-time provider bring-up (root template, PROVIDERS_WITH_MODULES gate). Use when porting a module, or when the user asks to "port to GCP", "start Phase C", or "add Azure support".
disable-model-invocation: true
---

# port-infra-module

Port one IaC module from AWS to another provider. AWS is feature-complete with 11
modules; Phase C (GCP) and Phase D (Azure) replicate them. This is mechanically
similar work repeated 11 times, which makes it exactly the kind of task where the
failure mode is **silent scope reduction** — a port that plants six of nine
findings and says nothing, leaving the coverage docs claiming nine.

The deliverable is therefore not just HCL. It is HCL **plus an explicit parity
verdict**.

## Read this first: a port is four changes, not one

Copying a module directory is necessary and not sufficient. `infra/templates/main.tf.j2`
is hardcoded AWS today — the `required_providers` block, the `provider "aws"`
block, and every module wiring line that references AWS-shaped `module.base`
outputs. The first port to a provider must also do the bring-up.

| # | Change | Where | Once per provider, or per module? |
|---|---|---|---|
| 1 | Root bundle template | `infra/templates/main.tf.j2` (+ `variables`, `outputs`, `tfvars`) | once — provider bring-up |
| 2 | Provider gate | `PROVIDERS_WITH_MODULES` in `core/engine/infra_models.py` | once — flip when ≥ base ships |
| 3 | Module directory | `infra/modules/<provider>/<module>/` | per module |
| 4 | Tests | `tests/` — catalog + generator coverage for the new provider | once, then extend |

**Order matters: `base` ports first.** Every other module consumes `module.base`
outputs (vpc id, subnets, SG, ssh key), so the base module's output *names* are
the contract the rest of the port depends on. Keep them identical across providers
wherever the concept exists — that is what lets the root template stay one file
with provider branches rather than three divergent files.

If the user asks to port a non-base module to a provider that has no `base` yet,
say so and offer to do `base` first.

## Steps

1. **Confirm scope.** Which module, which provider, and is this the provider's
   first port (bring-up needed) or an incremental one?
   ```bash
   ls infra/modules/                          # which providers exist
   ls infra/modules/aws/                      # the 11 to port
   grep -n "PROVIDERS_WITH_MODULES" core/engine/infra_models.py
   ```

2. **Read the AWS module as the source of truth** — all of it, not just the HCL.
   `README.md` (frontmatter + the findings table), `content.yml`, `main.tf`,
   `variables.tf`, `outputs.tf`. The README's findings table is the contract you
   are porting; the HCL is one implementation of it.

3. **Build the finding-parity map before writing HCL.** For each planted finding,
   name the provider-native equivalent. Write this table down — it becomes the
   port's README section and the parity verdict.

   | AWS finding | GCP equivalent | Status |
   |---|---|---|
   | Public-read S3 bucket | `allUsers` IAM binding on a GCS bucket | direct |
   | SG SSH open to 0.0.0.0/0 | VPC firewall rule, `0.0.0.0/0` → tcp:22 | direct |
   | IAM user + wildcard inline policy | service account + custom role | approximate — GCP has no IAM users |
   | CloudTrail weak config | Cloud Audit Logs config | approximate |

   Three honest outcomes per finding: **direct**, **approximate** (name the
   difference), **not portable** (name why). Never silently drop one.

4. **Write the module directory.** `infra/modules/<provider>/<module>/` with
   `README.md`, `main.tf`, `variables.tf`, `outputs.tf`, and `content.yml` when the
   AWS module has one.

   The README **must start at byte 0** with frontmatter — `infra_catalog`
   regex-matches it anchored, and on a miss returns `None`, silently dropping the
   module from `/api/infra/modules` and the UI picker with only a logged warning:
   ```yaml
   ---
   name: <module>
   description: <what it provisions and why — this is the picker card text>
   providers: [<provider>]          # must include the directory it lives under
   required_params: [project_name, ...]   # provider-native params, NOT copied from AWS
   optional_params: [...]
   dependencies: [base]
   ---
   ```
   Then the body: what it provisions, the **findings table with the parity map from
   step 3**, a "what this does NOT include" section, and the content installed.

   Rules that carry over unchanged: keep output names matching AWS where the
   concept exists · tag every planted finding (`CortexSim<Plane>Finding=<type>`) ·
   no real data, no credentials, no escape from the lab account · never create
   `.terraform/` or a lock file in the module.

5. **Provider bring-up (first port only).** Add the provider's `required_providers`
   + `provider` block to `infra/templates/main.tf.j2` behind a `{% if provider ==
   "<p>" %}` branch, mirror the module wiring using the ported base outputs, then
   flip `PROVIDERS_WITH_MODULES` in `core/engine/infra_models.py` to include it.
   The generator fails fast on providers not in that tuple, so leave it unflipped
   until `base` actually applies.

6. **Verify catalog loadability** — cheap, and the failure is otherwise silent:
   ```bash
   cd core && python3 - <<'PY'
   import sys; sys.path.insert(0, ".")
   from pathlib import Path
   from engine.infra_catalog import InfraCatalog
   c = InfraCatalog(Path("../infra/modules"))
   for prov in [p.name for p in Path("../infra/modules").iterdir() if p.is_dir()]:
       mods = c.list_modules(prov)
       print(f"{prov}: {len(mods)} modules -> {[m.name for m in mods]}")
   PY
   ```
   Every module you wrote must appear. One missing means bad frontmatter.
   `terraform fmt` / `validate` if the CLI is available — it may not be installed,
   which is fine; say so rather than claiming it passed.

7. **Generate a bundle end to end** once the provider is flipped on, and confirm
   the tar.gz has a root config plus the module dirs and no `.terraform` artifacts
   (there is a regression guard for that in `tests/`).

8. **Review and reconcile.** Run the `iac-module-reviewer` subagent — it is built
   for this and will demand the parity verdict. Then update the docs that carry
   provider/module counts: `CLAUDE.md`'s IaC phase list and
   `docs/reference/`; the `refresh-inventory` skill reconciles the counted ground
   truth. Commit the module, the template/gate change, and the docs on separate
   boundaries (named-file `git add`, no `-A`).

## Output

Report: the module and provider, the files written, catalog-load confirmation
(module visible to `InfraCatalog`), and the **parity verdict** as a count plus the
detail — *N findings direct, N approximate (each named with the difference), N not
portable (each named with the reason)*. If anything was dropped, say it in the
report and in the module README. A port that quietly plants fewer findings than
its AWS source is worse than no port, because the coverage docs keep claiming the
original number.
