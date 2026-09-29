# Late-Breaking Scenario & Library-Extensibility Spec

**Audience:** the Cortex threat-research team and anyone contributing detection
content to CortexSim.
**Purpose:** define, in one place, the exact contract the engine consumes so you
can turn a fresh piece of adversary research into a **loadable, runnable
simulation** — and know which extension surface to reach for when a plain command
sequence is not enough.
**Companion process:** [`docs/reference/research-intake-process.md`](../reference/research-intake-process.md)
is the *weekly workflow*; this file is the *contract behind it*.

---

## The one law

> **Authored is not proven.** A check that would pass on a build where the
> feature is absent is not a check. `tenant-verified` is **0** and stays 0 until
> a run executes against a live Cortex tenant. When you author a scenario you
> have produced *signal to test a detection with* — you have **not** proven the
> detection fires. Never let a submission report the two as one number.

Everything below serves that law. The engine will happily load a scenario whose
detection never fires; the acceptance gates (§5) exist so you find that out
before a Domain Consultant shows the result to a customer.

Two hard safety invariants ride alongside it, and no scenario may violate them:

- **The engine never writes to Cortex.** You generate signal *into* the
  environment; read-back is opt-in and read-only. Nothing you author changes
  that. `CORTEXSIM_XSIAM_ALLOW_WRITE` / `..._ALLOW_DESTRUCTIVE` stay off.
- **Every artifact you create must be torn down.** `cleanup.commands` is
  required and must remove everything the steps created.

---

## 0. Pick your surface in 60 seconds

Most late-breaking content is a **scenario (Tier A)** and nothing more. Reach
deeper only when the row below says you must.

| You have… | Author this | Section |
|---|---|---|
| A sequence of shell / PowerShell commands that reproduce the technique | **Scenario YAML** (Tier A) | §3 |
| …and you want the deployable detection logic (BIOC / XQL / ABIOC / correlation) to render in the POV report | **+ TTP detection card** (Tier B) | §4.B |
| …and the technique needs a real third-party binary (nmap, hydra, linpeas) | **+ Tool-adapter pack** (Tier C) | §4.C |
| A signal the identity harness **cannot** produce from a shell — network beacon, IdP sign-in log, SaaS OAuth grant, LLM egress, browser action, audit-log stream | **EAL plugin** (Tier D) | §4.D |

If you are unsure, start with the scaffolder (§6). It writes a schema-valid
Tier A scenario **and** a Tier B card stub in one command, and you fill in the
real logic.

---

## 1. What the engine consumes (mental model)

A **scenario** is the unit of simulation. Its `steps[]` carry the attack; each
`command` is *your script, verbatim* — SimCore does no interpolation. Around that
script the engine provides everything else:

```
 scenario.yml ── loaded & schema-validated at boot ──▶ Scenario (DB)
      │
      │  launch
      ▼
 orchestrator ── seeds one Result row per step per expected_detection
      │
      ├── PULL  ▶ Go beacon polls, resolves identity on the target,
      │           runs steps[].command wrapped by the identity harness,
      │           streams per-step output back
      │
      └── PUSH  ▶ generates a self-contained bash (.sh) or PowerShell (.ps1)
                  bundle the DC runs offline on a clean host
      │
      ▼
 expected_detections[] ── the claim: "these detections SHOULD fire"
      │
      ▼
 measurement loop (opt-in, read-only) ── observed alerts auto-validate
      Result rows ──▶ MTTD ──▶ verifier scores the run against threshold
```

Three consequences shape how you author:

1. **`command` is executed as written.** Make it self-contained, idempotent
   where possible, and safe to fail (`… || echo '[*] complete'`) — but see the
   false-negative trap in §3.
2. **`expected_detections[]` is a testable claim, not decoration.** Each row
   becomes a Result the measurement loop tries to satisfy. An empty or wrong
   claim is how "Cortex missed it" gets manufactured.
3. **The same scenario feeds both push and pull.** If you set
   `push_supported: true`, your commands must run on a clean Ubuntu 22.04 host
   with no SimCore at runtime. If a step is Windows-only, express it through
   `platform_variants` (§3), not a bash-only command.

---

## 2. The contract you must respect

| Invariant | What it means for your submission |
|---|---|
| **Authored ≠ proven** | Ship `status: draft` until the detection logic is real and validated. Do not describe an authored scenario as "coverage." |
| **No write path to Cortex** | Steps generate signal only. Never author a step that mutates tenant config. |
| **Cleanup is mandatory** | `cleanup.commands` must fully remove artifacts. `safety_class: destructive` adapters require non-empty cleanup. |
| **Consent gates real capability** | Offensive tooling is gated: `safety_class: dual-use-lab-only` needs launch consent; `c2-framework` refuses unless the scenario sets `c2_authorized: true`. |
| **Refs are foreign keys, not free text** | `uc_ref` / `tc_ref` / `tc_refs[]` / `pov_scenario_id` must resolve to the FY27 UC/TC index. Under `CORTEXSIM_STRICT_REFS` (default **true**) a dangling ref **rejects the scenario at boot** (§5). |
| **A zero is degraded, not ok** | If your content produces nothing, that must read as degraded, never as "none authored." |

---

## 3. Fast path — your script as a scenario (Tier A)

**Contract source of truth:** [`scenarios/_schema.yml`](../../scenarios/_schema.yml)
(every field, documented) and the Pydantic `ScenarioSchema` in
[`core/engine/scenario_loader.py`](../../core/engine/scenario_loader.py).
**Edit-time linter:** [`.claude/hooks/lint-scenario.py`](../../.claude/hooks/lint-scenario.py)
(runs automatically on save — §5).

### 3.1 Required top-level fields

The linter's `REQUIRED_TOP` is the enforced set. All eighteen must be present:

```
scenario_id  name  version  status  plane  detection_types
uc_ref  tc_ref  uc_name  tc_name
mitre_tactic  mitre_tactic_name  mitre_technique  mitre_technique_name
execution_identity  push_supported  pull_supported  steps
```

Controlled vocabularies (a value outside these is an **error**):

| Field | Allowed values |
|---|---|
| `scenario_id` | `^SIM-[A-Z_]+-\d{3,}$` — e.g. `SIM-EDR-023`. Never reuse an id. |
| `status` | `active` \| `draft` \| `deprecated` |
| `plane` | `EDR CDR NDR ITDR CLOUD_APP ANALYTICS AI_ACCESS AIRS AI_SPM BROWSER KOI ASM CSPM TIM EMAIL` |
| `detection_types[]` | `BIOC XQL Analytics Correlation IOC ABIOC` (≥1). XDM modeling rules are a **substrate**, not a `detection_type`. |

`ABIOC` (PANW-authored auto-tuned behavioral-ML with a causality chain) and
`Correlation` (cross-source stitch) are the highest-value additions — prioritise
them for late-breaking work.

### 3.2 Required per-step fields

`REQUIRED_STEP`: `id`, `name`, `command`, `identity`, `mitre_technique`,
`expected_detections`. Each `expected_detections` entry needs `plane`, `type`,
`description` (`REQUIRED_DET`); `ttp_ref` and `detection_id` are optional but
link the step to its Tier B card (§4.B).

**Identity** (`identity` per step, and `execution_identity.default`/`options`)
selects the harness wrapper on the target. Canonical values:

```
container-runtime · root · www-data · nobody · node · postgres · svc-backup · administrator
```

Non-`direct` identities create realistic process causality in XSIAM
(`runuser -l`, `sudo -u`, `su -s`). On Windows there is no credential-free
impersonation: the beacon collapses every non-direct identity to `direct` and
writes an explicit `IDENTITY NOT HONOURED` degradation into the run — so do not
assume a Windows step ran as `svc-backup`.

### 3.3 Causality — make the chain connected (optional, high value)

By default every step hangs off the synthetic `cortexsim-agent` node (a star).
Declaring causality collapses that into a real, connected CGO→process→process
spine, which is what XSIAM's causality view actually scores.

- **`cgo_anchor`** (scenario-level): `{image_name, primary_username?}` — the
  realistic initial-access process that OWNS the chain (`apache2`/`www-data`, a
  phishing `winword.exe`, `containerd-shim`). Names the root node instead of the
  beacon.
- **`causality`** (per-step): `{parent_step, pivot?}` — this step descends from
  an **earlier** step (forward/self refs are rejected). The **root** step omits
  `causality`; it links from the CGO. `pivot` ∈
  `process_lineage · network_session · endpoint_network_stitch · shared_entity · exposure_exploit · exploit_impact · temporal`
  (default `process_lineage`).

### 3.4 Cross-platform: one technique, many OSes

Put the primary command in `command`; give per-OS equivalents in
`platform_variants` and declare `platforms[]`:

```yaml
platforms: ["linux", "windows"]
platform_variants:
  windows: "Get-Content C:\\Windows\\System32\\config\\SAM"
```

Keys must be in `linux windows macos container k8s`. This is how a Windows-only
technique stays `push`-emittable as a `.ps1` instead of being smuggled into a
bash bundle.

### 3.5 The false-negative trap (read this)

A step that swallows its own failure manufactures a false PASS. If your tool
shells out to an interpreter it does not install (e.g. a script that needs
`python`), declare it:

```yaml
requires_interpreters: ["python"]
```

The beacon then checks the target's real PATH **before** running and reports a
distinguishable `RUNTIME_DEPENDENCY_MISSING` instead of letting a
`… || echo complete` fallback report success for a tool that never ran.

### 3.6 Minimal loadable scenario

This is the *shape*. It passes `lint-scenario.py` unedited. **The `uc_ref` /
`tc_ref` here are placeholders** — replace them with real FKs from the UC/TC
index (query `GET /api/uctc/test-cases`, or let the scaffolder in §6 wire valid
ones), or strict-ref validation rejects it at boot. Keep `status: draft` until
the detection logic is real.

```yaml
scenario_id: SIM-EDR-999
name: "Example — late-breaking technique skeleton"
version: "1.0"
status: draft

plane: EDR
detection_types:
  - BIOC
  - XQL

uc_ref: UCS-EDR-02          # must resolve in the FY27 index
tc_ref: TC-EDR-03           # must resolve, and its parent must equal uc_ref
uc_name: "Endpoint Detection & Response"
tc_name: "Validate behavioral detection"

mitre_tactic: "TA0006"
mitre_tactic_name: "Credential Access"
mitre_technique: "T1003.008"
mitre_technique_name: "OS Credential Dumping: /etc/passwd and /etc/shadow"

execution_identity:
  default: www-data
  options:
    - www-data
    - root

push_supported: true
pull_supported: true

cgo_anchor:
  image_name: "apache2"
  primary_username: "www-data"

steps:
  - id: step-01
    name: "Enumerate shell users"
    command: "cat /etc/passwd | grep sh$ && echo '[*] enumerated'"
    identity: www-data
    mitre_technique: "T1087.001"
    platforms: ["linux"]
    expected_detections:
      - plane: EDR
        type: XQL
        description: "Non-root process reading /etc/passwd — user enumeration"

  - id: step-02
    name: "Attempt to read /etc/shadow"
    command: "cat /etc/shadow 2>/dev/null || echo '[*] shadow read attempted'"
    identity: www-data
    mitre_technique: "T1003.008"
    platforms: ["linux"]
    causality:
      parent_step: step-01
      pivot: process_lineage
    expected_detections:
      - plane: EDR
        type: BIOC
        description: "Credential file access — /etc/shadow read by non-root service account"

cleanup:
  commands:
    - "echo '[CortexSim] SIM-EDR-999 cleanup complete'"

author: "Research Team"
tags:
  - credential-access
  - example
```

Files live at `scenarios/{plane}/{prefix}-NNN-<slug>.yml` (e.g.
`scenarios/edr/edr-023-…yml`; `ANALYTICS` → `scenarios/multi_plane/mp-NNN-…yml`).

---

## 4. Extensibility tiers — when to reach past YAML

### Tier B — TTP detection card (the detection side)

**When:** you want the deployable BIOC / XQL / ABIOC / correlation logic to
resolve onto the Result row and render inline in the POV report, and you want
per-detection evidence. Link a step to it with `ttp_ref` + `detection_id`.

**Schema:** [`detection_scanner/schema/ttp-entry.schema.json`](../../detection_scanner/schema/ttp-entry.schema.json).
**Parser:** [`core/engine/ttp_catalog.py`](../../core/engine/ttp_catalog.py).
**Files:** `detection_scanner/ttps/TTP-2026-NNNN-<slug>.json` (filename starts
with the id; id format `^TTP-\d{4}-\d{4}$`).

Required top-level keys: `id`, `schema_version`, `status`, `metadata`,
`identity`, `mitre_attack`, `execution`, `detections`, `panw_mapping`,
`references`.

The `detections` object holds seven sibling arrays. Required fields per object:

| Array | Required fields | Notes |
|---|---|---|
| `biocs[]` | `name`, `description`, `logic` | + `severity`, `mitre_technique_ids[]`, `detection_id` |
| `xql_queries[]` | `purpose`, `query` | + `name`, `dataset`, `expected_rows_min`, `detection_id` |
| `correlation_rules[]` | `name` | + `rule_id` (verbatim, e.g. `CR-CRED-0001`) or `detection_id`, `logic` |
| `iocs[]` | `ioc_type`, `value` | slug from `ioc_type` + `value` |
| `abiocs[]` | `name`, `description`, `logic` | behavioral-ML, logic-bearing |
| `modeling_rules[]` | `name`, `dataset`, `logic` | XDM substrate; counted informationally, **not** as detection depth |
| `analytics_modules[]` | plain strings | references only |

**How `detection_id` resolves.** A step's `detection_id` must match a card
detection. Either set an explicit `detection_id` on the detection object, or let
the slug derive: `f"{prefix}-{clean}"` where `clean` lowercases the `name` and
collapses non-alphanumeric runs to `-`. Prefixes: `bioc- xql- correlation- ioc-
abioc- modeling-`. Correlation rules may resolve by `rule_id`. **If you rename a
detection, its slug changes and the scenario's `detection_id` stops resolving** —
keep names stable (this is why the scaffolder tells you to change logic, not
names).

**Card-local ids are `TS-*`, never `UC-`/`TC-`.** Inside
`panw_mapping.threat_scenarios[]` use `threat_scenario_id` (`^TS-[A-Z0-9]+-\d{3}$`)
and `threat_steps[].threat_step_id`. The master-index `UC-`/`UCS-`/`TC-`
namespace is reserved; a guard test and the validator reject those tokens even
in prose fields.

Also enforced by `validate.py`: exactly one `references[].primary: true`; every
`source_refs` / `publisher_id` resolves to
`detection_scanner/sources/source-registry.json`; technique ids match
`T\d{4}(\.\d{3})?`.

### Tier C — Tool-adapter pack (bring a real binary)

**When:** a step needs a real third-party tool (nmap, hydra, linpeas, sqlmap).
Reference it from the scenario as `external_tools[].adapter_ref: TOOL-<NAME>`
instead of hand-rolling install/CLI.

**Schema:** [`tools/packs/_schema.yml`](../../tools/packs/_schema.yml).
**Loader + validation:** [`core/tools/adapter_loader.py`](../../core/tools/adapter_loader.py)
(`ToolAdapterSchema`). **Prose:** [`tools/packs/README.md`](../../tools/packs/README.md).
**Files:** `tools/packs/<tool>.yml`.

Required: `adapter_id` (`^TOOL-[A-Z0-9-]+$`), `name`, `version`, `tier` (1–5),
`category`, `upstream` (`repo`/`license`/`attribution` — license may not be
empty/`unknown`), `cortex_signal.planes[]`, `safety_class`
(`safe` \| `dual-use-lab-only` \| `c2-framework` \| `destructive`).

> **Note the narrower plane enum here.** `cortex_signal.planes` accepts only
> `EDR CDR NDR ITDR CLOUD_APP ANALYTICS AI_ACCESS AIRS BROWSER KOI` — the
> scenario plane enum is wider (adds AI_SPM/ASM/CSPM/TIM/EMAIL). Pick the closest
> signal plane for the tool.

The **5-tier model**: 1 in-tree · 2 submodule · 3 IaC-provisioned · 4
runtime-fetched · 5 external-only (reference; **no `invoke` block**). Most
research adapters are **tier 4**.

**The default-deny egress problem (tier 4).** A tier-4 tool installs from the
public internet *on the target*, which is the first thing a Cortex customer's
network blocks — and a step whose tool never arrived runs anyway and produces a
manufactured false negative. So every tier-4 pack must declare **exactly one** of:

- `install.artifact` — the tool is staged on the DC's own SimCore **payload
  shelf**, checksum-pinned, and served to the target (no target egress). Only a
  single-file download is shelvable. This declaration is the source of truth;
  `payloads/sources.json` is **generated** from it, never hand-edited.
- `install.artifact_exempt` — it is *not* shelf-backed, with a **closed**
  `reason_code` (`DISTRO_PACKAGE`, `LANGUAGE_PACKAGE_MANAGER`,
  `SOURCE_TREE_REQUIRED`, `ARCHIVE_ONLY_NO_EXTRACTOR`, `NEEDS_RUNTIME_DATA`,
  `LICENCE_NO_REDISTRIBUTION`, `ARCHIVE_MEMBER_NOT_SELF_CONTAINED`,
  `ARTIFACT_TOO_LARGE`, `IN_TREE_ALTERNATIVE`) and a ≥40-char human `reason`.

Declaring **neither** is a hard reject (`TA-13`); declaring **both** is a reject
(`TA-14`). This is deliberate — "nobody got to staging it" must be
unrepresentable. The `TA-01..TA-17` codes validate the artifact block; the ones
you will hit most: `TA-03` (bare servable filename), `TA-04` (64-hex sha256),
`TA-05`/`TA-06` (a pin needs a digest; an unpinned artifact needs a waiver),
`TA-08` (`kind: file` only — `archive` is rejected, no consumer can unpack),
`TA-10` (absolute `binary` and `stage_path` must agree). A staged pack (`linpeas`)
is the reference example.

C2 frameworks (`safety_class: c2-framework`) are never auto-staged and refuse to
launch without `c2_authorized: true`.

### Tier D — EAL plugin (signal the shell cannot make)

**When:** the technique is a signal the identity harness cannot produce from a
command — a network C2 beacon, DNS tunnel, IdP sign-in log, SaaS OAuth grant,
LLM-provider egress, browser action, or an audit/log stream POSTed to a
collector. These are the NDR / ITDR / CLOUD_APP / AI_ACCESS / AIRS / BROWSER /
KOI / EMAIL and analytics-log-streamer surfaces.

**Base class:** `BaseSimulation` in
[`core/eal_simulator/base.py`](../../core/eal_simulator/base.py).
**Registry:** [`core/eal_simulator/registry.py`](../../core/eal_simulator/registry.py)
— **directory auto-discovery by subclass reflection**. **Catalogue:**
[`docs/reference/eal-plugin-catalog.md`](../reference/eal-plugin-catalog.md).

**Adding one is pure drop-in — no registration edit:**

1. Drop a `.py` file in `core/eal_simulator/plugins/` (filename must be a valid
   identifier — no dashes; files starting with `_` are skipped).
2. Subclass `BaseSimulation` with an inner `Meta` (`name`, `params_model`;
   optional `version`, `description`, `mitre_techniques[]`, `eal_targets[]`, and
   analytics fields `data_sources`/`datasets`).
3. Implement `async def run(self, ctx) -> SimulationResult`.

Two load-bearing conventions the base class enforces:

- **Call `ctx.authorise(host, port=...)` before emitting any traffic** — the
  safety gate against the target allowlist.
- **Honour `ctx.dry_run`** — a dry run must emit nothing outbound. Wrap blocking
  socket work in `asyncio.to_thread`.

Minimal plugin skeleton:

```python
from pydantic import BaseModel
from ..base import BaseSimulation, SimulationContext, SimulationResult
from ..audit import ecs_event


class MyThingParams(BaseModel):
    target_host: str


class MyThing(BaseSimulation):
    class Meta:
        name = "my_thing"                  # unique registry key
        params_model = MyThingParams
        mitre_techniques = ["T1071"]
        eal_targets = ["My App-ID match"]

    async def run(self, ctx: SimulationContext) -> SimulationResult:
        params: MyThingParams = ctx.params
        started = self.utcnow()
        ctx.authorise(params.target_host)          # mandatory before any traffic
        if ctx.dry_run:
            return SimulationResult(plugin=self.Meta.name, step_id=ctx.step_id,
                                    status="success", started_at=started,
                                    completed_at=self.utcnow(), events_emitted=0)
        await ctx.emit_event(ecs_event(
            action="my-action", outcome="success", category="network", type_="info",
            campaign_id=ctx.campaign_id, run_id=ctx.run_id, step_id=ctx.step_id,
            plugin=self.Meta.name))
        return SimulationResult(plugin=self.Meta.name, step_id=ctx.step_id,
                                status="success", started_at=started,
                                completed_at=self.utcnow(), events_emitted=1)
```

**Delivery is accounted, not assumed.** For collector-POST plugins, only a `2xx`
counts as delivered; a 401/404/302 is `partial`/`not_delivered`, never a silent
success. If your plugin POSTs to a customer collector, report what the collector
**accepted**.

---

## 5. Acceptance gates — self-verify before you PR

`make` is the normal entry point, but it may be gated by the host toolchain
(e.g. the macOS Xcode licence). The **raw commands** below are what the make
targets and CI run, and they work directly. Run from the repo root.

| Check | Command | Rejects |
|---|---|---|
| **Scenario lint** (edit-time) | `python3 .claude/hooks/lint-scenario.py scenarios/<plane>/<file>.yml` | missing required field, bad enum, `execution_identity.default` not in `options`, forward/self causality ref, bad `pivot`/`platforms`, `cleanup` not a `{commands:[…]}` map. Dangling `ttp_ref`/`adapter_ref`/`detection_id` = **warning**. |
| **Detection corpus** | `python3 detection_scanner/scripts/validate.py --quiet` | card JSON invalid, schema breach, id collision, unresolved `source_refs`, bad MITRE id, `UC-`/`TC-` token in a card, XQL/BIOC grammar lint. |
| **Strict refs** | `CORTEXSIM_BASE_DIR=$PWD PYTHONPATH=core python3 -m pytest tests/engine/test_corpus_refs_strict.py -q` | walks every scenario through the real loader with `CORTEXSIM_STRICT_REFS=true`: a dangling `uc_ref`/`tc_ref`/`tc_refs[]` **rejects** (S-10/S-11/S-12/S-15). |
| **Coverage floors** | `python3 detection_scanner/scripts/coverage_report.py --strict` | MITRE / plane / detection-type / methodology-family below floor. |
| **Payload shelf** (only if you touched `install.artifact`) | `PYTHONPATH=core python3 -m engine.payload_shelf --write && PYTHONPATH=core python3 -m engine.payload_shelf --check` | `payloads/sources.json` drift from the pack declarations. |

**The lint hook also runs automatically.** The `PostToolUse` hook
`.claude/hooks/validate-corpus.py` fires on any `Edit`/`Write` of
`scenarios/**/*.yml` (lint), `detection_scanner/ttps/*.json` (validate +
regenerate exports), and `tools/packs/*.yml` (adapter source check). A clean edit
needs no manual run.

**The validation codes** (`S-…` for scenarios, `TA-…` for adapters) are defined
in the loaders themselves — `core/engine/scenario_loader.py` and
`core/tools/adapter_loader.py` — and are the authority. They are **not** a
contiguous range, and some prose docs reuse the same code number for a different
meaning, so read the code, not a copied table. The author-relevant scenario
codes: **S-01/S-02/S-09** (hygiene warnings — thin scenario, empty
`expected_detections`, declared-vs-actual `detection_types` drift), **S-10/S-11/
S-12/S-15** (index-ref ERRORs, gated by `CORTEXSIM_STRICT_REFS`), **S-13/S-14/
S-16** (advisory positioning warnings), **S-17** (undeclared step identity /
cluster-posture mismatch — ERROR), **S-19** (`external_tools[].stage_as` outside
the staging allowlist — ERROR).

**Gate A evidence (in the PR body).** Beyond green CI, a detection contribution
must include: what was observably wrong or missing, how you verified the new
signal, and **proof the guard fails without the fix** — a check never observed
failing is an assumption with good syntax. See
[`CONTRIBUTING.md`](../../CONTRIBUTING.md) for the full branching + gate rules.

---

## 6. Late-breaking workflow — the weekly on-ramp

Do not start from a blank file. The scaffolder writes a schema-valid Tier A
scenario **and** a Tier B card stub, with the `detection_id` slugs already wired
and unique ids picked automatically:

```bash
scripts/research_intake.py \
  --title "Muddled Libra abuses helpdesk MFA reset" \
  --url "https://unit42.paloaltonetworks.com/muddled-libra/" \
  --summary "Social-engineered MFA reset yields valid-account SaaS access." \
  --plane ITDR
# --dry-run previews both files without writing; --file reads the same fields from a file
```

Both files ship `status: draft`. Your job — the part that needs a human — is to
replace the scaffold logic with the technique **from the research**: real actors,
MITRE techniques, payload, and the ABIOC / XQL / correlation bodies in the card;
real steps and commands in the scenario. **Keep the detection names** so the
slugs keep resolving. Then flip both to `status: active`, run the §5 gates, and
open a `feature/<plane>-<slug>` PR against `dev`. Full loop:
[`docs/reference/research-intake-process.md`](../reference/research-intake-process.md).

---

## 7. Reference index

| Thing | Where |
|---|---|
| Scenario field reference | [`scenarios/_schema.yml`](../../scenarios/_schema.yml) |
| Scenario loader / `ScenarioSchema` + S-codes | [`core/engine/scenario_loader.py`](../../core/engine/scenario_loader.py) |
| Scenario edit-time linter | [`.claude/hooks/lint-scenario.py`](../../.claude/hooks/lint-scenario.py) |
| TTP card JSON Schema | [`detection_scanner/schema/ttp-entry.schema.json`](../../detection_scanner/schema/ttp-entry.schema.json) |
| TTP catalogue / slug logic | [`core/engine/ttp_catalog.py`](../../core/engine/ttp_catalog.py) |
| Tool-adapter field reference | [`tools/packs/_schema.yml`](../../tools/packs/_schema.yml) |
| Tool-adapter loader + TA-codes | [`core/tools/adapter_loader.py`](../../core/tools/adapter_loader.py) |
| Adapter authoring prose | [`tools/packs/README.md`](../../tools/packs/README.md) |
| EAL plugin base class | [`core/eal_simulator/base.py`](../../core/eal_simulator/base.py) |
| EAL plugin registry | [`core/eal_simulator/registry.py`](../../core/eal_simulator/registry.py) |
| EAL plugin catalogue | [`docs/reference/eal-plugin-catalog.md`](../reference/eal-plugin-catalog.md) |
| Weekly intake process + scaffolder | [`docs/reference/research-intake-process.md`](../reference/research-intake-process.md) |
| Payload shelf | [`docs/reference/payload-shelf.md`](../reference/payload-shelf.md) |
| Branching + QA gates | [`CONTRIBUTING.md`](../../CONTRIBUTING.md) |

| Vocabulary | Values |
|---|---|
| Planes (scenario) | `EDR CDR NDR ITDR CLOUD_APP ANALYTICS AI_ACCESS AIRS AI_SPM BROWSER KOI ASM CSPM TIM EMAIL` |
| Planes (adapter signal) | `EDR CDR NDR ITDR CLOUD_APP ANALYTICS AI_ACCESS AIRS BROWSER KOI` |
| detection_type | `BIOC XQL Analytics Correlation IOC ABIOC` (modeling rules = substrate) |
| Identity harness | `container-runtime root www-data nobody node postgres svc-backup administrator` |
| Causality pivots | `process_lineage network_session endpoint_network_stitch shared_entity exposure_exploit exploit_impact temporal` |
| Step platforms | `linux windows macos container k8s` |
| Adapter tiers | `1 in-tree · 2 submodule · 3 IaC · 4 runtime-fetched · 5 external-only` |
| Safety classes | `safe · dual-use-lab-only · c2-framework · destructive` |
