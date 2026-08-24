# CortexSim — Causality-Graph Methodology

> **Domain:** How CortexSim models a Cortex XDR/XSIAM **Causality Instance graph**
> as a pure, offline projection of a run's seeded `Result` denominator — the
> node/edge taxonomy, how each edge is *derived* (spec-EXPECTED) and *upgraded*
> (matcher-CONFIRMED / BROKEN), and how the model maps onto what the Cortex
> Causality Analysis Engine actually does on the endpoint and across planes.
> **Scope:** `core/engine/causality_graph.py` (the builder), the seven typed
> edges, the node types, the EXPECTED→CONFIRMED→BROKEN state machine, and the
> "graph is a third projection" invariant.
> **Audience:** Engineers extending the causality builder, and DCs/scenario
> authors adding causality metadata to a scenario.
>
> This is the *methodology* companion to two peer projections of the same
> denominator: the linear **Detection Storyline** (`core/engine/detection_storyline.py`)
> and the **Efficacy Scorecard** (`core/engine/efficacy_scorecard.py`).

---

## 0. Why a graph, and the one invariant that governs it

CortexSim's Detection Proof Layer proves detections **one at a time** — an
ordered checklist where each entry lines up `step → expected detection →
observed Cortex alert → real MTTD` (`detection_storyline.build_storyline`,
`detection_storyline.py:263`). That timeline is faithful but **flat**: entries
are grouped only by `step_id` and carry *zero* cross-entry links. The matcher
(`connectors/matcher.py:85-111`) correlates only *vertically* — a seeded
`Result` to an `ObservedAlert` on technique / detection_id / name — and never
detection ↔ detection.

The **Causality Graph** is the missing horizontal layer: it promotes each
storyline entry to a node and draws the edges *between* them that model how
Cortex stitches scattered signals into **one investigable incident under one
Causality Group Owner (CGO)**.

**The governing invariant — the graph is a THIRD PROJECTION, never a new source
of counts.** The orchestrator seeds exactly one `Result` per expected detection
per step. That is the single denominator. Three pure builders read it:

```
                          seeded Result rows  (one denominator)
                                   │
          ┌────────────────────────┼────────────────────────┐
          ▼                        ▼                        ▼
  detection_storyline        efficacy_scorecard        causality_graph
  (timeline projection)      (scorecard projection)    (graph projection)
          │                        │                        │
          └──────────── build_summary(entries) ─────────────┘
                        coverage% / MTTD computed ONCE
```

Coverage % and MTTD are computed **once**, in `detection_storyline.build_summary`
(`detection_storyline.py:225`), and the graph *delegates* to it. The timeline,
the scorecard, and the graph therefore can **never disagree** about the same
run — the recurring failure mode this codebase already guards against with a
shared `_status` convention (`detection_storyline._is_detected` ≡
`efficacy_scorecard._status` ≡ `report_generator._status_from_result`). The
graph adds `nodes[]`, `edges[]`, and a `causality_summary`; it does **not**
re-derive coverage.

---

## 1. What Cortex actually does (the thing we model)

The model is grounded in real Cortex XDR/XSIAM mechanics. Understanding them is
what keeps the synthesized graph "Cortex-native" rather than an invented
abstraction.

### 1.1 Endpoint: the Causality Instance (CI) graph and the CGO

Cortex XDR's **Causality Analysis Engine** assembles, per incident, a
**Causality Instance (CI) graph**: a tree of **process** nodes, plus the
**file / network / registry EVENTS** those processes caused, plus the **ALERTS**
that fired on any of them. Walking OS process lineage *backward*, the engine
names a single **Causality Group Owner (CGO)** — the parent process it deems
responsible for the whole chain — rendered as the **leftmost/root** node of
Causality View.

The definitive join key is **`causality_id`** on the `xdr_data` dataset. Every
raw event (`event_type` ∈ `ENUM.PROCESS / FILE / NETWORK / REGISTRY`) belonging
to one chain carries the same `causality_id`, and every alert that fires on any
node **inherits** it. `dataset = xdr_data | filter causality_id = <id>`
reconstructs the entire chain.

`xdr_data` exposes **three nested actor scopes** per event — precisely the
parent/child structure we model:

| Scope | xdr_data prefix | Role | CortexSim node |
|-------|-----------------|------|----------------|
| CGO / root | `causality_actor_*` | the parent process the engine blames | `cgo` node |
| immediate parent | `actor_process_*` | the OS process performing the action | `wrapper` node (runuser/sudo/su) |
| child / target | `action_process_*` | the process being spawned / acted on | `process` node |

Lineage edges are `actor_process → action_process`; the CGO is the transitive
root. The CGO field set we mirror: `causality_actor_causality_id`,
`causality_actor_process_image_name`, `_process_command_line`,
`_process_image_sha256`, `_primary_username`, `_process_os_pid`,
`_process_image_path`.

**Alert attachment.** Behavioral detections (**BIOC / ABIOC / Analytics**) do
not float free — each match produces an alert that **attaches to the node whose
event triggered it** and adopts that node's `causality_id`, so it renders inside
the CGO's CI graph. **IOC** matches attach at the file/image (sha256) level.
**Correlation / Analytics** alerts can span multiple nodes but still resolve to
the shared CGO. This is the exact hook by which CortexSim ties seeded `Result`
rows onto graph nodes.

### 1.2 Network + cross-plane: log stitching and the 5-tuple

XSIAM does not stop at the endpoint. **Log stitching** correlates NGFW/firewall
session logs + endpoint raw XDR-agent data + cloud/identity data across sensors
into one event-causality timeline. **XDM normalization** (schema-on-read) maps
NGFW fields (`source_ip / source_port / dest_ip / dest_port / protocol`, App-ID,
url_category) and endpoint fields (`event_type = ENUM.NETWORK`,
`action_local_ip`, `action_remote_ip`, `action_remote_port`,
`actor_container_info`, `actor_effective_user_name`) into the common `xdr_data`
model — so a cross-source JOIN on the shared **5-tuple** (or `container_id`, or
`src_host`) within a tight temporal window is possible. That join is what a
single-source SIEM structurally cannot do; it is the competitive wedge the
scenarios label `moat_tier: MOAT`.

**Correlation → incident grouping.** A correlation rule asserts *≥ N distinct
planes* (`_product`) under a **single `incident_id`**. CortexSim's scenarios
already declare this contract:

```yaml
correlation_window_seconds: 60          # the tight stitch window
required_planes_in_incident: [EDR, NDR] # the completeness predicate
stitching_key: src_host                 # what the two sides join on
```
(`scenarios/multi_plane/mp-001-*.yml:73-77`; `mp-006` uses `container_id` + a
5-tuple `comp ... by source_ip, source_port, dest_ip, dest_port, protocol` with
a ±10s window.)

Until this capability, **nothing parsed that block into a graph and no column
stored it.** The causality graph is where it becomes a machine-checkable edge.

---

## 2. Node taxonomy

Every node is a JSON-serializable dict with a `kind` discriminator and a
**stable id** (so a renderer can lay out a left-rooted tree and diff across SSE
frames). Five node kinds; the first two of the deferred set are documented for
completeness.

| kind | stable id | Models (xdr_data) | Source | Always offline? |
|------|-----------|-------------------|--------|-----------------|
| `cgo` | `cgo:{run_id}` | the Causality Group Owner — the beacon/agent execution shell, one synthetic root per run | synthesized | ✅ |
| `wrapper` | `wrap:{run_id}:{step_id}` | the identity-impersonation process (`runuser -l` / `sudo -u` / `su -s`) = `actor_process_*` | identity harness | ✅ |
| `process` | `proc:{run_id}:{step_id}` | the step's action process (`action_process_*`); `image_name` = head binary of the command | step spec | ✅ |
| `alert` | `alert:{result_id}` | a storyline entry promoted **verbatim** — carries `status`, `mttd_seconds`, `detection_type`, `detection_id`, `observed{}` | `detection_storyline` entry | ✅ (status from matcher) |
| `exposure` | `expo:{run_id}:{asset}` | an ASM/CSPM posture finding on an asset (`control_layer=exposure`); a left-rail node with **no runtime execution** | step spec (`control_layer`) | ✅ |
| `event` *(deferred)* | `evt:{…}` | a caused file/network/registry effect (e.g. `cat /etc/shadow` → FILE node) | command parse | ✅ |

Key modeling decisions:

- **`cgo` — one per run (v1).** A scenario reads as *one* incident under *one*
  CGO, which keeps the graph legible and matches "one `causality_id` per run."
  All `process` nodes share that single `causality_id`. (See §6 for the
  deferred cross-identity refinement.)
- **`process` node fields map onto real xdr_data names** so the graph reads
  Cortex-native: `image_name` = the FIRST executable token of `step.command`
  (strip the pipeline — split on `|`, `;`, `&&`, take the head binary);
  `command_line` = the full wrapped command; `primary_username` =
  `step.identity`; `os_pid` = a deterministic monotonic counter seeded per run
  (reproducible, not random); `causality_id` = the single per-run id;
  `image_sha256` = pulled from an `ioc-…-sha256-…` detection_id when present,
  else null.
- **`alert` node is the single source of truth.** It is the `detection_storyline`
  entry **verbatim** — the graph never recomputes status or MTTD. This is the
  mechanical guarantee behind the §0 invariant.
- **`exposure` node has no process.** It is the "the door Cortex should have
  found at rest" node — the exposure half of the dual-control story (§5). It is
  wired to runtime nodes by an `exposure_exploit` edge, not by lineage.

---

## 3. Edge taxonomy — seven typed edges

Edges are the whole point: they are what the flat storyline structurally cannot
draw. Each edge carries a `type`, a directed/undirected sense, a `state`
(§4), and — once CONFIRMED — its **evidence** (the alert external_ids that
upgraded it, exactly as `matcher.MatchVerdict` already records
`alert_external_id`, `matcher.py:159-167`).

| # | type | Sense | Joins | Derived from | Cortex analogue |
|---|------|-------|-------|--------------|-----------------|
| 1 | `process_lineage` | directed parent→child | `cgo → wrapper → process` (or `cgo → process` when identity is direct) | identity-harness resolution (`resolve_identity_mode`) | CI-graph `actor_process → action_process` spine under the CGO |
| 2 | `network_session` | undirected | two detections sharing an observed **5-tuple** (`src/dst ip+port+proto`) within the window | observed 5-tuple on matched Results | XDM-normalized NGFW session grouping |
| 3 | `endpoint_network_stitch` | undirected | an ENDPOINT/CLOUD process node ↔ a NETWORK node on a shared `src_host`/`container_id`/5-tuple | `stitching_key` + `correlation_window_seconds` | XSIAM joining an NGFW session to the XDR process that originated it — **the hero cross-plane proof** |
| 4 | `temporal` | directed (dashed overlay) | step N process → step N+1 process, `|Δobserved_at| ≤ correlation_window_seconds` | pairwise `observed_at` delta | the network-behavior arc NGFW analytics flag as a pattern |
| 5 | `shared_entity` | undirected | detections joined on a shared host / `container_id` / account / ip value | entity columns on the Result | XDM entity resolution across sources |
| 6 | `sequence` | directed | step N process → step N+1 in scenario order | scenario step order (always available) | the kill-chain backbone, drawable before any observation |
| 7 | `exposure_exploit` | directed (dotted) | an `exposure` node → the later exploit/impact node on the **same asset entity** | `control_layer` + `asset_ref` on the step spec | the code-to-cloud dual-control edge (§5) |

### 3.1 How `process_lineage` is derived (the load-bearing edge)

Edge #1 is the one that must be *identical on push and pull*, because it is the
only edge that models the endpoint CGO tree directly. It is derived from the
**identity harness** — the same single source of truth
(`spec/identity_harness.json`, `core/engine/identity_spec.py`) that the push
bash `run_as` harness (`push_generator.py:63-118`) and the Go beacon's
`identity.ResolveIdentity` both consume. A small pure helper —
`resolve_identity_mode(identity) -> {mode, wrapper}` — replicates that resolution
(`direct_identities()` / `service_accounts()`, default `runuser`):

```
identity = www-data  →  mode=runuser, wrapper="runuser -l www-data -c …"
                            cgo:{run} ──lineage──▶ wrap:{run}:step ──lineage──▶ proc:{run}:step
identity = root      →  mode=direct
                            cgo:{run} ──lineage──▶ proc:{run}:step        (no wrapper node)
```

Because the builder consumes the **same** allowlists the executors do, the
modeled parent/child chain matches what XDR actually observes on the endpoint.
A Python-vs-Go drift here would silently *falsify* the graph — so the resolver
stays a single shared spec (a Go test already guards drift, per the identity
harness contract).

### 3.2 How the stitch edges (#2/#3) become evidence-backed

Spec-derived, these edges are **EXPECTED** — declared by the scenario's
`stitching_key` but not yet observed. They upgrade to **CONFIRMED** only when
the matcher observes *both* ends inside `correlation_window_seconds`. This is a
much **tighter** temporal claim than the matcher's coarse detection window
(`DEFAULT_WINDOW_SECONDS = 3600`, `matcher.py:30` — "this technique fired
*sometime this hour*"). A stitch says "these two alerts fired within 10–60s of
each other *and* share a tuple," so the graph threads a per-scenario window
distinct from the detection window, and the matcher gains a **network-tuple key**
alongside its technique/detection_id/name keys (`matcher._correlation_keys`,
`matcher.py:85-111`). The CONFIRMED edge carries the two alert `external_id`s as
its evidence.

---

## 4. The edge-state machine: EXPECTED → CONFIRMED / BROKEN

The graph is **offline-first**: every spec-derivable edge exists the moment a
scenario loads, before any tenant is touched. Observation *upgrades* edges; it
never *creates* the graph.

```
        (scenario spec — always offline)
                    │
                    ▼
              ┌───────────┐
              │  EXPECTED  │  spec-derived; declared but unobserved
              └─────┬─────┘
                    │  matcher observes BOTH ends …
        ┌───────────┴────────────┐
        │ …within the window      │ …but OUTSIDE the window
        ▼                         ▼
  ┌───────────┐            ┌──────────┐
  │ CONFIRMED  │            │  BROKEN   │
  │ (evidence: │            │ (demonstr-│
  │  2 alert   │            │  able     │
  │  ext_ids)  │            │  stitch   │
  └───────────┘            │  gap)     │
                            └──────────┘
```

- **EXPECTED** — spec-derived from step order + identity + `control_layer` +
  the declared `stitching_key`. The always-available offline backbone. Lineage
  and sequence edges are typically EXPECTED even with zero observations.
- **CONFIRMED** — the matcher observed both endpoints *within*
  `correlation_window_seconds`. Carries evidence (alert external_ids).
- **BROKEN** — both endpoints were observed, but their `observed_at` fall
  **outside** the window. This is the highest-value POV signal the linear
  storyline is structurally blind to: a declared stitch that *should* have
  fired as one incident but did not — a real detection-engineering gap in the
  customer tenant (missed enrichment, dropped log source, clock skew). A BROKEN
  edge is worth more to a DC than a plain missed detection, because it names a
  *stitching* failure, not just a coverage hole.

This mirrors the storyline's own status nuance (`detected` / `pending` /
`missed`, `detection_storyline._entry_status`): an edge is only BROKEN once
there is evidence to contradict the window, exactly as a detection is only
`missed` once the run reaches a terminal state.

---

## 5. Dual-control: the `exposure_exploit` edge as the platform argument

The seventh edge is where the graph becomes a *sales* artifact. The thesis: a
breach requires **both** a standing weakness (a staged exposure/misconfig) **and**
a live exploit that abuses it. Two control layers own the two halves:

- **Exposure / posture** — find and shrink the weakness *before* exploitation:
  ASM/Xpanse (`core/planes/asm.py`), CSPM/Cortex Cloud (`core/planes/cspm.py`),
  AI-SPM. `control_layer: exposure`.
- **Runtime prevention** — stop the exploit *at* execution: the XDR agent's
  BIOC/ABIOC process/memory/causality blocking (`core/planes/edr.py`), CDR
  container-runtime escape detection. `control_layer: prevention`.

A vuln scanner alone knows a door is unlocked but cannot stop the burglar
already inside; an EDR alone stops today's exploit but never shrinks the surface
so the same door invites tomorrow's. The platform payoff that *neither point
tool can replicate* is the **stitch**: the same system that inventoried the day-0
exposure also runs the runtime kill and joins them under **one `incident_id`**.

The `exposure_exploit` edge encodes this directly. It links an `exposure` node
(the ASM/CSPM finding on `asset_ref: dmz-web-01`) to the later exploit/impact
`process` nodes on the **same asset**. Its state *is* the verdict:

| Edge state | Meaning | Dual-control verdict |
|------------|---------|----------------------|
| **CONFIRMED** | exposure-plane finding **and** runtime block both fired, stitched under one `incident_id` within window | "Platform: you shrank the surface *and* blocked the exploit, as one incident." |
| **half-edge (exposure only)** | posture flagged, runtime silent | *"You flagged it but could not stop it."* |
| **half-edge (prevention only)** | runtime blocked, surface never shrank | *"You blocked it but never shrank the surface."* |

The two half-edges are the demonstrable point-tool blind spots the POV report
flags. This is the code-to-cloud story the linear storyline *structurally
cannot draw* — it has no node for "the exposure ASM should have caught at rest"
and no edge to tie it to "the runtime process only a BIOC/ABIOC severed." The
`SIM-MP-007` scenario (`required_planes_in_incident: [ASM, EDR, NDR]` — one
exposure plane AND two runtime planes) is authored so the incident **cannot
"complete"** unless both control layers fire.

---

## 6. Design decisions, deferrals, and the offline guarantee

- **One `causality_id` per run (v1).** Matches "one scenario = one incident,"
  keeps the graph legible. *Deferred:* start a new CI subtree when a step's
  `su`/`runuser` switches to a different service account — closer to real CGO
  boundary behavior, but only worth it once scenarios exercise cross-identity
  pivots.
- **Event nodes are deferred.** v1 attaches `alert` nodes directly to `process`
  nodes and still yields a faithful `CGO → process → alert` tree. Add
  command-derived file/network/registry `event` nodes (e.g. `cat /etc/shadow` →
  FILE event on `/etc/shadow`) only when a scenario's value depends on the
  caused-event layer.
- **Edge persistence is deferred.** v1 is per-run and in-memory. *Deferred:* an
  `Edge` table for campaign-level, cross-run graphs so an `exposure_exploit`
  edge can span a *posture* run and a *later exploit* run on the same asset.
- **Orphan-step defense.** The builder handles the same schema-drift case
  `detection_storyline` guards (a `Result` whose `step_id` has no matching
  scenario step) by emitting a synthetic `process` node, so **no seeded
  detection silently drops out of the graph** — the denominator stays whole.
- **Offline-first is non-negotiable.** Every node and every EXPECTED edge is
  derivable from the always-available step spec (identity resolution + command
  head-binary + scenario order + `control_layer`/`asset_ref`). CortexSim makes
  **no** tenant call to build the graph; the matcher only *upgrades* edge state
  when reconciliation evidence exists. A run with zero observations still
  renders a complete, correct EXPECTED graph.

---

## 7. Authoring: adding causality metadata to a scenario

The scenario schema already carries the stitch contract for `mp-*` scenarios
(`stitching_key`, `correlation_window_seconds`, `required_planes_in_incident`).
To make **any** multi-step scenario draw causality edges:

1. **Declare the stitch window + key** at the top level (already valid for
   ANALYTICS scenarios):
   ```yaml
   correlation_window_seconds: 120
   required_planes_in_incident: [ASM, EDR, NDR]
   stitching_key: asset_id
   ```
2. **Tag each expected detection** with its control layer and the asset it
   concerns, so the dual-control and exposure_exploit edges can form:
   ```yaml
   expected_detections:
     - plane: ASM
       type: Analytics
       control_layer: exposure      # exposure | prevention
       asset_ref: dmz-web-01
       description: "Cortex ASM discovers exposed unauth Redis on public host"
       detection_id: analytics-mp-007-asm-exposed-redis-surface
   ```
3. **Add a top-level `causality:` block** declaring the `exposure_exploit` edge
   on the shared asset:
   ```yaml
   causality:
     exposure_exploit:
       - asset_ref: dmz-web-01
   ```

Existing scenarios that declare none of this load **unchanged** and simply
produce a graph with lineage + sequence edges only (default: no cross-plane
stitch, no exposure edge). Nothing about the graph is required for a scenario
to be valid.

---

## 8. Field/source cross-reference

| CortexSim construct | Cortex/xdr_data analogue | Source of truth |
|---------------------|--------------------------|-----------------|
| `cgo` node, one per run | Causality Group Owner (`causality_actor_*`) | synthesized `cgo:{run_id}` |
| `wrapper` node | immediate parent (`actor_process_*`) | identity harness resolution |
| `process` node | action process (`action_process_*`) | `step.command` head binary + `step.identity` |
| single per-run `causality_id` | `causality_id` on `xdr_data` | one id shared by all process nodes |
| `alert` node | alert attached to a node, inheriting `causality_id` | `detection_storyline` entry verbatim |
| `network_session` / `endpoint_network_stitch` | XDM-normalized 5-tuple JOIN | `stitching_key` + observed 5-tuple |
| `required_planes_in_incident` predicate | correlation rule: ≥ N `_product` under one `incident_id` | scenario `mp-*.yml` |
| edge evidence (alert external_ids) | alerts co-grouped under the CGO | `matcher.MatchVerdict.alert_external_id` |
| coverage % / MTTD | — (not a Cortex concept; the POV metric) | `detection_storyline.build_summary` (delegated) |

**Primary sources.**
Code: `core/engine/detection_storyline.py`, `core/engine/efficacy_scorecard.py`,
`core/engine/identity_spec.py`, `core/engine/push_generator.py:63-118`,
`core/connectors/matcher.py:85-111`, `spec/identity_harness.json`,
`scenarios/multi_plane/mp-001-c2-beacon-ngfw-xdr-stitch.yml:73-77`,
`scenarios/multi_plane/mp-006-ngfw-container-causality-stitch.yml`.
Cortex docs: Causality-Group-Owner (CGO), Causality-Actor (XQL Schema
Reference), Causality View (XDR Pro admin), XDR_DATA-Fields, "What is
Causality" (XSIAM).

---

*Generated by CortexSim — Palo Alto Networks Cortex Detection Simulation Engine.*
