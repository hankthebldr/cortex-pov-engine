# Caldera Parity & Next-Generation Strategy

**Status:** analysis + strategy proposal · **Date:** 2026-08-21
**Scope:** what MITRE/Apache Caldera actually does (source-level), what CortexSim
actually is today (verified inventory), the gap between them, and the architecture
+ sequencing that makes CortexSim a next-generation engine rather than a Caldera clone.

**Method.** Source-level teardown of `apache/caldera` at HEAD plus 14 plugin
repositories (`stockpile`, `emu`, `response`, `atomic`, `human`, `access`, `manx`,
`builder`, `debrief`, `compass`, `gameboard`, `training`, `sandcat`, `caldera-ot`)
cloned and read directly — not documentation summaries. Read against a static
inventory of this repository. Every number in this document is reproducible; see
§10 for the commands.

---

## 0. Executive summary

Caldera and CortexSim are not the same kind of system, and the difference is not
maturity — it is **objective function**.

- **Caldera is an autonomous offensive planner.** Its core loop discovers its own
  next move: an ability runs, a parser scrapes facts out of the output, those facts
  are stored as a relationship graph, requirement modules gate which follow-on
  abilities are now *legal*, and a planner scores and picks the next one. It plans
  toward **compromise**. It never observes the defender.
- **CortexSim is a detection-evidence generator.** It emits curated, deterministic,
  high-fidelity signal with a declared expected detection per step, then measures
  whether the defender saw it. It plans nothing; it executes a fixed list. But it is
  the only one of the two that closes a loop with the blue side.

Reaching literal "every capability Caldera has" is roughly **60% engine work we
genuinely lack** (facts, planners, requirements, parsers, multi-platform executors,
C2 substrate) and **40% content scale we can import rather than author** (Caldera's
ability YAML maps almost 1:1 onto ours).

But shipping Caldera parity *alone* would make us a worse Caldera — an inferior
clone of a 9-year-old tool with a US-government install base, in a category where
we currently have no competitor. The next-generation move is to **take Caldera's
primitive model, which is correct and battle-tested, and invert its objective
function**: keep the fact plane, the requirement gating, the planner framework and
the multi-platform executor model — then point them at *detection coverage* instead
of *compromise*, and feed the defender's own response back into the plan. Caldera
structurally cannot do this, because nothing in its object model represents a
detection.

**The one-line thesis:**
> Caldera plans toward compromise. CortexSim must plan toward detection coverage —
> and it is the only engine that can, because it is the only one that observes the
> defender.

---

## 1. Caldera teardown

### 1.1 Shape of the system

An asyncio/aiohttp server (`server.py`) exposing a REST API (v2, marshmallow-schema
driven, 17 handler modules) and a VueJS SPA (the `magma` plugin). Everything above
the core is a **plugin** — a separate git repo dropped into `plugins/` and named in
`conf/local.yml`. 18 ship by default.

State lives in RAM (`data_svc`) with periodic serialization, plus a pluggable
**knowledge service** for the fact/relationship graph. 12 services total:
`app_svc`, `auth_svc`, `contact_svc`, `data_svc`, `event_svc`, `file_svc`,
`knowledge_svc`, `learning_svc`, `planning_svc`, `rest_svc`, and interfaces for each.

### 1.2 The object model — the eight primitives that matter

| Primitive | Source | What it is |
|---|---|---|
| **Ability** | `app/objects/c_ability.py` | One atomic technique. Carries `tactic`, `technique_id`, N **executors**, `requirements`, `privilege`, `repeatable`, `buckets`, `singleton`, `access` (red/blue/app), `delete_payload`. |
| **Executor** | `secondclass/c_executor.py` | The per-platform *how*. `platform` × `name` (sh/psh/pwsh/cmd/python/elasticsearch), `command`, or `code`+`language`+`build_target` for compiled payloads, plus `payloads`, `uploads`, `timeout`, `parsers`, `cleanup`, `variations`. |
| **Adversary** | `c_adversary.py` | A profile: an ordered list of ability IDs (`atomic_ordering`) + an `objective`. That is *all* it is — a reusable playlist over the shared ability library. |
| **Fact** / **Relationship** | `secondclass/c_fact.py`, `c_relationship.py` | `trait`/`value`/`score`/`origin_type`. Relationships are `source —edge→ target` triples. This is the operation's working memory. |
| **Source** / **Rule** | `c_source.py`, `secondclass/c_rule.py` | Seed facts for an operation, plus ALLOW/DENY regex rules (e.g. `DENY remote.host.ip 127.0.0.1`) that constrain what the planner may act on. |
| **Requirement** | `secondclass/c_requirement.py` | A pluggable module that decides whether a candidate link is *legal* given the fact graph. Stockpile ships 9: `paw_provenance`, `reachable`, `no_backwards_movement`, `not_exists`, `existential`, `basic`, `req_like`, `universal`. |
| **Objective** / **Goal** | `c_objective.py`, `secondclass/c_goal.py` | Termination condition. A Goal is `target`/`value`/`count`/`operator` (`== < > <= >= in *`) evaluated against the fact set. The operation ends when all goals are satisfied — not when the step list runs out. |
| **Link** | `secondclass/c_link.py` | One (agent × ability × executor × fact-binding) instantiation. The unit of execution, scoring, and cleanup. |

### 1.3 The autonomy loop — the thing that actually makes Caldera "Caldera"

```
  Link executes on agent
        │
        ▼
  stdout/stderr  ──►  Parsers (declared per-executor)  ──►  Facts + Relationships
        │                     │                                     │
        │                     └── learning_svc (regex IP/path,      │
        │                         auto-pairs facts into             │
        │                         relationships from the model)     │
        ▼                                                           ▼
  Visibility score                                        knowledge_svc (persistent)
        │                                                           │
        │                          ┌────────────────────────────────┘
        ▼                          ▼
  Operation ◄──── Planner ◄── Requirements gate candidate links
  visibility          │       (paw_provenance, reachable, …)
  threshold           │
                      ▼
             next Link  ──►  fact interpolation #{trait} → variations → link explosion
```

Three details carry most of the power:

1. **Fact interpolation.** Ability commands contain `#{host.dir.staged}`,
   `#{github.access.token}`, `#{paw}`. At link-generation time each unfilled variable
   is cross-producted against every matching fact — one ability against 12 discovered
   hosts becomes 12 links. This is why Caldera *spreads*.
2. **Requirements as guard rails.** `paw_provenance` means "only use a fact this
   agent itself discovered." `no_backwards_movement` stops the operation from
   pivoting back to a host it came from. Without these, autonomy is a fork bomb.
3. **`learning_svc` builds a relationship model from the ability corpus itself.** It
   scans every ability command for co-occurring `#{}` variables; any two traits that
   appear in the same command are assumed relatable. Then non-deterministic parsers
   (`p_ip`, `p_path`) scrape *any* output and auto-pair the results. Caldera learns
   facts from abilities it was never told how to parse.

### 1.4 Planners — six shipped

| Planner | Where | Strategy |
|---|---|---|
| `atomic` | core `app/planners/atomic.py` | Default. One ability per agent per tick, in `atomic_ordering` sequence. |
| `batch` | stockpile | Every currently-satisfiable ability sent at once, wait for all, repeat. |
| `buckets` | stockpile | Abilities bucketed by ATT&CK tactic; walks tactics in matrix order, looping while progress is made. |
| `guided` | stockpile | Goal-oriented. Scores links by likelihood of reaching the objective; `half_life_penalty`, `goal_weight`, `fact_score_weight`, `goal_action_decay`. |
| `look ahead` | stockpile | Reward-table + depth + discount factor. An MDP-flavoured rollout over anticipated future reward. |
| `bayes` | stockpile | Learns from *past operations*. Drops links below `min_prob_link_success`, orders by probability, defers links with `< min_link_data` observations. |

The planner interface is trivial to implement: a class with `state_machine`,
`next_bucket`, and an `execute()` that calls `planning_svc.execute_planner(self)`.
Stopping conditions are fact-based.

### 1.5 Visibility — the noise budget

Every link carries a `Visibility` score (1–100, default 50, with additive
adjustments). An operation has a visibility threshold; links that would exceed it
are not run. Caldera models **"how loud is this action"** as a first-class,
plan-affecting quantity. Nothing in CortexSim does.

### 1.6 C2 substrate

- **Contacts (9):** `http`, `tcp`, `udp`, `dns`, `websocket`, `ftp`, `gist` (GitHub
  Gist as dead-drop), `slack`, `html`. Plus HTTPS via the `ssl` plugin.
- **Tunnels:** SSH (`app/contacts/tunnels/tunnel_ssh.py`).
- **Handles:** `beacon`.
- **Agent fields** (`c_agent.py`): `sleep_min`/`sleep_max` (jitter), `watchdog`,
  `deadman_enabled` (abilities that fire when the agent dies), `privilege`,
  `executors[]`, `trusted`, `pending_contact` (**switch C2 channel at runtime**),
  `proxy_receivers`/`proxy_chain` (**peer-to-peer relay — agents route through each
  other**), `origin_link_id` (which action spawned this agent), `available_contacts`.
- **Obfuscators:** core `plain_text`, `base64_basic`; stockpile adds
  `base64_jumble`, `base64_no_padding`, `caesar_cipher`, `steganography`.
- **Data encoders:** `plain_text`, `base64` — applied to exfiltrated file content.

### 1.7 Payload & build pipeline

`file_svc` serves payloads with on-the-fly obfuscation. The `builder` plugin compiles
`code`+`language`+`build_target` into real binaries inside Docker (C#, C, Go, DLL,
EXE). Stockpile ships `donut.py` for shellcode generation and 25 static payloads.
`delete_payload` controls post-execution artifact removal. Executors declare
`uploads` (exfil back to the server) alongside `payloads` (staged to target).

### 1.8 Plugin surface

| Plugin | What it gives Caldera | CortexSim analogue |
|---|---|---|
| **stockpile** | 209 abilities · 27 adversaries · 5 planners · 9 requirement modules · 23 parsers · 3 fact sources · 25 payloads | scenarios + TTP cards (different shape) |
| **atomic** | Imports Red Canary Atomic Red Team (~1,500 tests) as abilities at runtime | submodule registered, **never imported as executable content** |
| **emu** | Imports CTID Adversary Emulation Library plans (APT29, FIN6, menuPass, OilRig, Sandworm, Turla, Wizard Spider …) as adversaries | `sources/MITRE-Turla-Carbon` only |
| **response** | 42 blue-team abilities across `detection`/`hunt`/`elastic_hunting`/`response` — executors that **query Elasticsearch** and parse results back into facts | none (our connector layer is the nearest cousin) |
| **sandcat** | Go agent, multi-contact, P2P, extensions (gocat) | `agent/` — Go, HTTP-only, poll-only |
| **manx** | Reverse shell / TTY sessions | none |
| **builder** | Dockerised payload compilation | none |
| **human** | Simulated benign user activity (Chrome + native apps) to bury red actions in noise | none |
| **access** | Initial-access tooling | ASM/TIM scenarios, partially |
| **debrief** | Campaign analytics, network-topology replay, PDF export, ATT&CK detection-strategy mapping | POV report generator (**ours is stronger**) |
| **compass** | ATT&CK Navigator layer generation | shipped (per-TTP + coverage export) |
| **gameboard** | Red-vs-blue exercise scoring | none |
| **training** | Certification course | none |
| **magma** / **ssl** / **fieldmanual** | UI / HTTPS / docs | React UI / TLS / docs |
| **caldera-ot** | 6 ICS protocol plugins: bacnet, dnp3, modbus, profinet, iec61850, gems | none (no Cortex plane) |
| **arsenal** | MITRE ATLAS (AI/ML attack) techniques + profiles | AIRS / AI_ACCESS / AI_SPM (**ours is far deeper**) |

### 1.9 What Caldera does *not* do

This list is as strategically important as the capability list, because it is where
our existing moat already sits:

1. **No concept of a detection.** Nothing in the object model represents an expected
   alert, a rule, a detection engine, or a detection outcome. The `response` plugin
   queries Elasticsearch, but as *another red-style ability chain*, not as validation.
2. **No defender-side ground truth.** No MTTD, no expected-vs-observed, no
   pass/fail against a detection. An operation "succeeds" when its facts satisfy a goal.
3. **No normalization-schema awareness.** No equivalent of XDM modeling rules, field
   mapping, or ingestion-health validation.
4. **No infrastructure provisioning.** Caldera assumes you already have hosts with
   agents on them. No IaC.
5. **Endpoint + network only** (plus OT). No SaaS/identity/email/browser/AI-runtime
   emulation planes.
6. **No safety classification or consent gating.** Every ability is equally armed.
7. **No business/evidence layer.** Debrief reports on the red operation, not on the
   customer's security outcome.
8. **Weak security posture by its own admission** — the README explicitly says not to
   expose it to the internet, and it shipped an unauthenticated RCE (CVE-2025-27364)
   in Feb 2025.

---

## 2. CortexSim — verified current state

### 2.1 Inventory

| Dimension | Verified value |
|---|---|
| Loadable scenarios | **88** across **15** planes (118 `*.yml` under `scenarios/`, incl. non-scenario files) |
| Total execution steps | **304** |
| Unique MITRE technique IDs referenced | **106** |
| TTP detection cards | **89** (`detection_scanner/ttps/*.json`, 753 catalog detection objects) |
| Tool adapter packs | **69** (+ `_schema.yml`) across 5 tiers |
| EAL simulator plugins | **14** |
| IaC modules (AWS) | **11** |
| Detection-plane modules | **15** (`core/planes/`) |
| Core Python files | 95 (~7.7k LOC across `core/engine` + `core/api`) |
| Go agent | 1,910 LOC total — `beacon/`, `executor/`, `identity/` |
| Identity harness | 4 modes (`direct`/`runuser`/`sudo_u`/`su`), 7 service accounts |
| **Scenarios with Windows/PowerShell execution** | **0** |
| **Occurrences of variable interpolation in commands** | **0** |

### 2.2 Where we already lead Caldera

These are real, and none of them are things Caldera is likely to build:

1. **Detection is a first-class object.** Every step declares `expected_detections[]`
   with plane, detection type, and a resolvable `detection_id` into a real
   BIOC/XQL/correlation/IOC/ABIOC body. 550/550 slugs resolve.
2. **A measurement loop exists.** `core/connectors/` + `matcher` auto-validate seeded
   `Result` rows against observed alerts → evidence-backed MTTD.
3. **Fifteen detection planes** vs Caldera's endpoint+network. Identity, SaaS, email,
   browser, AI runtime, AI posture, agentic supply chain, cloud posture, attack surface.
4. **Normalization-substrate awareness** — `detections.modeling_rules[]`, the XDM
   layer. No BAS tool models this.
5. **Infrastructure provisioning** — the IaC generator produces the *target
   environment*, with planted findings. Caldera assumes the lab exists.
6. **Safety classification + consent gating** — adapter `safety_class`, `c2_authorized`,
   `simulation_authorized`. A commercial necessity Caldera never needed.
7. **A business/evidence layer** — POV reports, KPI thresholds, methodology families
   F1–F10, MOAT/LEAD/PARITY tiering, ATT&CK Navigator export.

### 2.3 Structural gaps — blunt

| # | Gap | Evidence |
|---|---|---|
| G1 | **No fact model.** No variables, no interpolation, no working memory. | 0 `#{}` occurrences repo-wide |
| G2 | **No planner.** `steps[]` executes in list order, always. | `orchestrator.py` enqueues one task with an ordered step array |
| G3 | **No parsers.** Agent streams raw output; nothing is extracted from it. | `beacon/client.go` POSTs text to `/output` |
| G4 | **No requirements/preconditions.** A step cannot be gated on discovered state. | schema has no such field |
| G5 | **No objectives.** A run terminates on step exhaustion only. | no goal evaluation anywhere |
| G6 | **Linux only.** Harness is `runuser`/`sudo -u`/`su`. | 0 Windows scenarios; `spec/identity_harness.json` |
| G7 | **One executor: bash.** No psh/pwsh/cmd/python/query executors. | `agent/executor/shell.go` |
| G8 | **One C2 channel: HTTP poll.** | `agent/beacon/client.go` |
| G9 | **No P2P / no pivoting.** Agents cannot relay; no agent spawns another. | no proxy fields on `Agent` |
| G10 | **No obfuscation or encoding.** Commands go over the wire in plaintext. | — |
| G11 | **No payload service.** No compile, no shellcode, no upload/download. | — |
| G12 | **No visibility/noise model.** | — |
| G13 | **No scheduling.** | roadmap H2.2, unbuilt |
| G14 | **No reusable ability library.** Scenario is a monolith; steps are not addressable, reusable, or recombinable. | — |
| G15 | **Content scale.** 304 steps vs ~1,700 importable ability-equivalents sitting in repos we already reference. | `sources/atomic-red-team` registered but never imported |
| G16 | **Cleanup is static.** A flat `cleanup.commands[]`, not per-executed-link reverse-ordered teardown. | schema |
| G17 | **No cross-run knowledge persistence.** | — |

---

## 3. Parity matrix

Verdicts: **Adopt** (build it, essentially as Caldera has it) · **Adapt** (build it,
but inverted toward detection) · **Import** (get it from Caldera's content, don't
author it) · **Reject** (deliberately out of scope, with reason).

| Caldera capability | CortexSim today | Verdict | Priority |
|---|---|---|---|
| Ability library (atomic, reusable, addressable) | scenario-embedded steps | **Adopt** | P0 |
| Multi-platform executors (linux/windows/darwin) | linux/bash only | **Adopt** | P0 |
| Multiple executor languages per platform | one | **Adopt** | P0 |
| Adversary profile = ordered ability IDs | monolithic scenario | **Adopt** | P0 |
| Facts, relationships, sources, rules | none | **Adapt** — add *detection facts* alongside offensive facts | P1 |
| Output parsers → facts | none | **Adapt** — parse both target output *and* connector observations | P1 |
| Requirement modules (link gating) | none | **Adopt** | P1 |
| Planner framework (pluggable) | none | **Adopt** the framework | P2 |
| `atomic` / `batch` / `buckets` planners | none | **Adopt** | P2 |
| `guided` / `look ahead` / `bayes` planners | none | **Adapt** — reward = detection coverage, not compromise | P4 |
| Objectives + goals as termination | step exhaustion | **Adapt** — `DetectionGoal` | P4 |
| Visibility / noise scoring | none | **Adapt** — becomes the *detection difficulty dial* | P4 |
| Multi-contact C2 (http/tcp/udp/dns/ws/ftp/gist/slack) | http only | **Adapt** — each contact is an NDR **detection surface**, not an evasion channel | P3 |
| P2P proxy chains / agent relay | none | **Adopt** — lateral movement is a Cortex stitching test | P3 |
| Runtime C2 channel switching (`pending_contact`) | none | **Adopt** | P3 |
| Jitter / sleep / watchdog / deadman | fixed interval | **Adopt** | P3 |
| Obfuscators | none | **Adapt** — difficulty ladder, not evasion | P4 |
| Payload service + Docker build + donut | none | **Adopt** (payload service, build) / **Reject** (donut shellcode) | P3 |
| Upload/exfil channel | none | **Adopt** | P3 |
| Per-link auto-generated reverse cleanup | static command list | **Adopt** | P1 |
| Scheduling | none | **Adopt** | P2 |
| Knowledge persistence across operations | none | **Adopt** | P1 |
| Event bus / webhooks | SSE only | **Adopt** | P2 |
| Stockpile 209 abilities | — | **Import** | P5 |
| Atomic Red Team ~1,500 tests | submodule unused | **Import** | P5 |
| CTID emulation plans (APT29, FIN6, …) | 1 (Turla) | **Import** | P5 |
| `response` blue-team ability chains | connector layer | **Adapt** — becomes the closed loop | P4 |
| `human` benign-noise generator | none | **Adapt** — becomes the **false-positive-rate harness** | P4 |
| `gameboard` red-vs-blue scoring | none | **Adapt** — purple-team POV mode | P6 |
| `debrief` campaign analytics + PDF | POV report | **already ahead** | — |
| `compass` Navigator layers | shipped | **already ahead** | — |
| `manx` reverse shells / TTY | none | **Reject** — interactive C2 is a red-team product surface, not a detection-validation one; the *signal shape* is already covered by NDR scenarios |
| `caldera-ot` ICS protocols | none | **Reject** — no corresponding Cortex plane. Revisit if PANW ships an OT detection surface |
| `training` certification | none | **Reject** — DC enablement lives in `docs/operator-runbook.md` |
| Steganography / caesar obfuscators | none | **Reject** — novelty; base64/encoding variants carry the detection-difficulty value |

---

## 4. The strategic thesis — three inversions

Adopting Caldera's primitives without inverting them produces a clone. The three
inversions below are what make the result next-generation.

### Inversion 1 — the objective function

Caldera's `Goal` evaluates over **facts**:
```yaml
goals:
  - target: host.user.password
    value: "*"
    count: 3
```
"Get me three credentials." CortexSim's goal must evaluate over **detections**:

```yaml
objective:
  name: "Prove credential-theft detection depth"
  goals:
    - target: detection.fired
      value: "abioc-*"
      count: 3                 # three distinct ABIOCs must fire
    - target: detection.mttd_seconds
      operator: "<"
      value: 120
    - target: incident.planes_stitched
      value: "EDR,ITDR,NDR"
      operator: "in"
    - target: detection.missed
      operator: "=="
      value: 0
```

The operation is complete when the **defender** has demonstrated something — not when
the attacker has. That single change repurposes the entire Caldera planning stack.

### Inversion 2 — the fact plane runs in both directions

Caldera facts flow one way: target output → facts → next action. CortexSim needs two
fact origins:

- **Offensive facts** — parsed from step output on the target. Gives us chaining,
  pivoting, and content reuse. (This is straight Caldera.)
- **Detection facts** — parsed from the *defender* via `core/connectors/`. Alert
  fired, rule ID, XDM fields populated, incident ID, stitch membership, MTTD.

Once detection facts are in the same graph as offensive facts, the planner can reason
about both: *"the ABIOC did not fire on the quiet variant — escalate to the loud
variant"*, *"NDR saw the beacon but the incident did not stitch to the EDR process —
run the pivot step that supplies the missing join key."*

This is the closed loop. **Caldera cannot build it without inventing a detection
model from scratch, and we already have one** — 89 TTP cards, 753 detection objects,
15 planes, and a working matcher.

### Inversion 3 — obfuscation is a difficulty dial, not an evasion tool

We do not want to evade the customer's Cortex tenant; we want to find *where it
breaks*. So Caldera's obfuscator chain and visibility score become a **detection
difficulty ladder**:

> Run technique T1003.001 at difficulty 0 (naked `procdump`), 1 (renamed binary),
> 2 (base64-encoded command line), 3 (LOLBin proxy execution), 4 (in-memory).
> Report: *"Your BIOC catches levels 0–2. The ABIOC catches 0–3. Nothing caught
> level 4. Here is the XQL rule that would."*

That is a customer-facing deliverable no BAS vendor produces well, it is directly
monetizable as a POV finding, and it is a *reason* to build the obfuscation
substrate that would otherwise look like red-team tooling. It also converts
`Visibility` from an offensive stealth budget into a measured axis of the report.

---

## 5. Target architecture

### 5.1 Object model

| New primitive | Caldera analogue | CortexSim extension |
|---|---|---|
| `Ability` | `Ability` | `+ expected_detections[]`, `+ detection_id`, `+ plane`, `+ difficulty_level`, `+ adapter_ref`, `+ safety_class` |
| `Executor` | `Executor` | `+ identity` (our harness), `+ eal_plugin` (non-shell emission paths) |
| `Profile` | `Adversary` | `+ uc_ref`/`tc_ref`, `+ methodology_family`, `+ threshold` |
| `Fact` / `Relationship` | same | `+ origin_type: DETECTION` |
| `Source` / `Rule` | same | seeded from IaC module outputs |
| `Requirement` | same | `+ detection_precondition` ("only run if the prior ABIOC fired") |
| `Parser` | same | `+ ObservationParser` over connector results |
| `Planner` | same | `+ DetectionCoveragePlanner`, `+ StitchPlanner`, `+ DifficultyLadderPlanner` |
| `Objective` / `DetectionGoal` | `Objective`/`Goal` | evaluates detection facts |
| `Link` | same | `+ expected_detections`, `+ difficulty_level`, `+ observed[]` |
| `Scenario` | *(none)* | **retained** as a saved `Profile + Source + Objective` triple — back-compat |

### 5.2 Migration — do not break the 88 scenarios

The single highest-risk part of this plan is that 88 curated scenarios *are* the
current product value. The migration must be mechanical and reversible:

1. **Shred.** A one-time tool walks every scenario's `steps[]` and emits an `Ability`
   per unique `(command-hash, technique, plane, identity)`. ~304 steps → ~250
   abilities after dedup.
2. **Reconstitute.** Each scenario becomes a `Profile` whose `atomic_ordering`
   reproduces its original step sequence exactly, plus a `Source` holding what were
   hardcoded literals.
3. **Assert equivalence.** A golden test renders each Profile back to the original
   push bundle and diffs byte-for-byte against today's `push_generator` output. Zero
   diff is the gate.
4. **Keep the loader.** `scenario_loader.py` keeps accepting today's YAML; it just
   compiles to the new graph. Field authors notice nothing.

After this, `steps[]` is a *view* over an ability graph rather than the storage
format — and every step becomes independently reusable, planner-schedulable, and
recombinable into new profiles.

### 5.3 Planner framework

Adopt Caldera's interface verbatim (`state_machine`, `next_bucket`, `execute()`,
fact-based stopping conditions) — it is small, proven, and gives us the atomic/batch/
buckets planners nearly free. Then add ours:

- **`DetectionCoveragePlanner`.** Scores candidate links by *marginal detection
  surface*: how many not-yet-fired `detection_id`s, planes, and XDM modeling rules
  this link would newly exercise, minus a redundancy penalty for detections already
  confirmed observed this run. Converges on maximum coverage in minimum actions —
  which is exactly a DC's constraint in a 90-minute POV slot.
- **`StitchPlanner`.** Goal-directed toward correlation: given a target incident
  shape (`required_planes_in_incident`, `stitching_key`, `correlation_window_seconds`
  — **all three already in our schema**), select and *time* links across planes so
  their join keys land inside the window. This is F2 methodology, automated.
- **`DifficultyLadderPlanner`.** Binary-searches the obfuscation level at which a
  given detection stops firing. Terminates with a per-detection break point.

### 5.4 Agent substrate v2

Keep Go, keep the identity harness (it is a genuine differentiator — Caldera has no
equivalent of `runuser`-derived causality chains). Extend:

- **Platforms:** Windows (`psh`, `pwsh`, `cmd`) and macOS (`sh`, `zsh`) executors.
  Windows identity harness via `runas` / `CreateProcessWithLogonW` / scheduled-task
  identity. **This is the single biggest content unlock** — most Cortex XDR POVs are
  Windows-heavy, and we currently run zero Windows steps.
- **Contacts:** add DNS, TCP, UDP, WebSocket. Each is an NDR detection surface in its
  own right — the agent's own C2 channel becomes testable signal.
- **P2P:** proxy chains so agent B routes through agent A. Produces genuine lateral-
  movement telemetry for stitching tests.
- **Sleep/jitter/watchdog/deadman**, `pending_contact` runtime channel switching.
- **Payload service:** staged payloads, `uploads` exfil path, per-link cleanup.

### 5.5 The closed loop

```
  Plan ──► Emit (agent / EAL plugin / push bundle)
   ▲                        │
   │                        ▼
   │              Cortex tenant (customer's)
   │                        │
   │                        ▼
   └── Re-plan ◄── Detection facts ◄── connectors + matcher
       (coverage gap,          (alert fired? which rule?
        stitch miss,            which XDM fields? MTTD?
        difficulty break)       did it stitch?)
```

Run this until the `DetectionGoal` is satisfied or the coverage frontier stops
moving. The output is not "we ran 12 techniques" — it is *"here is your detection
frontier, here are the four gaps, here is the rule for each."*

---

## 6. Content strategy — import, don't author

Authoring 1,700 abilities by hand is not a plan. Three import paths, all mechanical:

| Source | Volume | Mapping |
|---|---|---|
| Caldera **stockpile** abilities | 209 | Near-1:1 into our `Ability`. `technique.attack_id` → `mitre_technique`; `platforms.<os>.<executor>` → `Executor`; `requirements` → requirement refs; `cleanup` → per-link cleanup. |
| **Atomic Red Team** | ~1,500 tests | Submodule *already registered* at `sources/atomic-red-team` and referenced by `tools/packs/atomic-red-team.yml` — but never imported as executable content. Reuse Caldera's `atomic` plugin conversion logic. |
| **CTID Adversary Emulation Library** | ~15 full plans | Import as `Profile`s. Instant APT29/FIN6/menuPass/Sandworm/Turla/Wizard Spider coverage — the named-actor emulation customers ask for by name. |

**The enrichment step is the moat.** An imported ability has no
`expected_detections` — and an ability without an expected detection is worthless to
us. Build a semi-automated enrichment pipeline:

```
imported Ability ──► mitre_technique ──► candidate TTP cards (89)
                                     ──► candidate detections (753 objects)
                                     ──► human review queue (accept / reject / author)
```

A DC reviews a ranked suggestion instead of authoring from scratch. Realistic
throughput: hundreds of enriched abilities per review-week versus a handful authored.
**This is the step that turns Caldera's public content into Cortex-specific POV
value, and it is not reproducible by anyone without our detection corpus.**

---

## 7. Sequenced roadmap

Each phase has a definition of done and a verifiable claim — per the operating
principles already in `docs/strategic-roadmap.md`.

| Phase | Scope | DoD | Verifiable claim |
|---|---|---|---|
| **P0 — Primitive refactor** | Ability/Executor/Profile model; scenario shredder; multi-platform executor schema (no runtime yet) | All 88 scenarios round-trip to byte-identical push bundles | "Every step is now an addressable, reusable ability" |
| **P1 — Fact plane** | Facts, relationships, sources, rules, `knowledge_svc` equivalent, parsers, requirements, per-link cleanup | A 2-scenario chain where step 2's target is discovered by step 1 | "Scenarios adapt to the lab instead of hardcoding it" |
| **P2 — Planner framework** | Planner interface + atomic/batch/buckets; scheduling; event bus | Same scenario runs under 3 planners with different orderings | "Execution order is a strategy, not a constant" |
| **P3 — Agent v2** | Windows + macOS executors, Windows identity harness, DNS/TCP/WS contacts, P2P, jitter/watchdog, payload service | A Windows credential-theft scenario runs end-to-end with identity causality | **"We run Windows"** — the biggest single content unlock |
| **P4 — Detection objective loop** | `DetectionGoal`, detection facts from connectors, `DetectionCoveragePlanner`, `StitchPlanner`, difficulty ladder, FPR noise harness | A run that terminates on detection-goal satisfaction, not step exhaustion | "The engine plans toward your detection gaps" |
| **P5 — Content import** | Stockpile + Atomic Red Team + CTID importers; enrichment pipeline | 500+ enriched abilities; 5+ named-actor profiles | "10× content, Cortex-annotated" |
| **P6 — Purple surface** | Gameboard-style red/blue scoring; customer validation portal | Live purple-team POV mode | "Both sides scored in one view" |

Dependencies are strictly linear P0 → P1 → P2 → P4, with P3 parallelizable after P0
and P5 parallelizable after P1.

---

## 8. Risks and non-goals

| Risk | Mitigation |
|---|---|
| **We accidentally build a real C2 framework.** Multi-contact + P2P + payload compilation + obfuscation is, structurally, a C2. | The `safety_class` / consent-gating machinery already exists — extend it to contacts and obfuscation levels. Hard-refuse interactive shell (`manx`-style) surfaces. Every emission stays declared, logged, and tied to an `expected_detection`. **If an action has no expected detection, it does not ship.** That rule alone keeps us a validation tool. |
| **Autonomy destroys determinism**, which is what DCs actually trust today. | Two execution modes over one object graph: **Deterministic** (today's behaviour, `atomic` planner, fixed source — the default) and **Autonomous** (planner-driven, opt-in). Never silently autonomous. |
| **Complexity blowup.** Caldera is ~9 years of accreted machinery. | Import the interfaces, not the implementations. The planner interface is ~40 lines. Resist porting stockpile's Python wholesale. |
| **Security posture.** Caldera shipped an unauthenticated RCE. | We are adding a task-dispatch surface with payload staging. Threat-model P3 explicitly; keep the master-key guard; no unauthenticated task endpoints. |
| **Import-without-enrichment floods the corpus with untargeted content.** | Imported abilities land in a `draft` state and are invisible to scenario selection until enriched with at least one `expected_detection`. |

**Explicit non-goals:** OT/ICS protocols (no Cortex plane today), interactive
reverse shells, certification/training content, shellcode generation, evasion-for-
evasion's-sake obfuscators.

---

## 9. What makes this next-generation, not a Caldera clone

Seven claims Caldera cannot make, ranked by defensibility:

1. **Closed-loop detection objective.** The operation terminates when the *defender*
   proves something. Requires a detection model + a connector layer. We have both.
2. **Detection difficulty ladder.** Automatic discovery of the obfuscation level at
   which each specific rule breaks. A customer-facing finding, not a red-team stat.
3. **Cross-plane stitching planner.** 15 planes vs 2. Automated F2 methodology using
   `correlation_window_seconds` / `stitching_key` / `required_planes_in_incident` —
   fields our schema *already has* and nothing currently consumes.
4. **Normalization-substrate validation.** Planning toward XDM modeling-rule coverage.
   No BAS tool models the normalization layer at all.
5. **Provisioned targets.** The engine builds the lab it attacks, with planted
   findings, then validates detection of them. Caldera starts one layer above this.
6. **False-positive-rate harness.** Caldera's `human` plugin generates benign noise
   to *hide* red actions. Inverted, it measures the customer's FPR — already a
   `primary_kpi` value in our schema with nothing producing it.
7. **Evidence layer.** Every run terminates in a POV artifact with KPI thresholds,
   methodology family, and competitive positioning.

Items 1, 2, 3 and 6 are each products in their own right. Items 4 and 5 are
structural advantages that would take a competitor years to replicate.

---

## 10. Reproducing the numbers

```bash
# Caldera side
git clone --depth 1 https://github.com/mitre/caldera.git
for p in stockpile emu response atomic human access manx builder \
         debrief compass gameboard training sandcat caldera-ot; do
  git clone --depth 1 https://github.com/mitre/$p.git plug-$p
done
find plug-stockpile/data/abilities -name '*.yml' | wc -l    # 209
find plug-stockpile/data/adversaries -name '*.yml' | wc -l  #  27
ls    plug-stockpile/data/planners/                         #   5
ls    plug-stockpile/app/requirements/                      #   9 modules
ls    plug-stockpile/app/parsers/                           #  23
find plug-response/data -name '*.yml' | wc -l               #  42
ls    caldera/app/contacts/                                 #   9 channels

# CortexSim side
find scenarios -name '*.yml' ! -name '_schema.yml' | wc -l              # 118 files / 88 loadable
grep -rhE "^\s+- id: [\"']?step-" scenarios/ --include='*.yml' | wc -l  # 304 steps
grep -rhoE "T1[0-9]{3}(\.[0-9]{3})?" scenarios/ --include='*.yml' | sort -u | wc -l  # 106
ls detection_scanner/ttps/*.json | wc -l                                #  89
ls tools/packs/*.yml | wc -l                                            #  70 (69 + _schema)
grep -rli "powershell\|cmd.exe\|\.ps1" scenarios/ --include='*.yml' | wc -l  # 0
grep -rn "#{" core/engine/*.py agent/*.go                               # 0 matches
```

---

## Pointers

- `docs/strategic-roadmap.md` — the existing horizon plan this document feeds
- `CORTEXSIM_AGENT_CONTEXT.md` — Phase 1 build specification
- `docs/design/e2e-execution-methodology.md` — Tier A/B/C test methodology
- `docs/tool-adapters.md` — adapter framework, the nearest existing analogue to an ability library
- `scenarios/_schema.yml` — current scenario contract (the migration source of truth)
