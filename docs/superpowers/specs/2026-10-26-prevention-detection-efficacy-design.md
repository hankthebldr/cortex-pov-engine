# Prevention + detection efficacy — design

Status: **design** (awaiting review) · 2026-10-26
Author: Henry + Claude
Scope: make CortexSim able to prove, in a customer tenant, that the Cortex XDR
agent **stopped** a technique and not only that something **saw** it; give the
library the structure to say what each scenario proves; and expand the library
along that structure. Linux is the proving ground; the structure must not assume
an OS. Builds on the homelab ladder
(`2026-10-04-homelab-detection-validation-ladder.md`) for the tenant, the
collector and the NGFW wires. The **prevention proof target is a separate
Linux VM with a host-installed Cortex XDR agent** (decided 2026-10-26): the
ladder's containerised k3s target carries a smaller prevention set and keeps
the detection-only work. Agent version and the list of modules its profile can
set to Block are owed by Henry; §6 is provisional until they land.

---

## 0 · The one principle

A block-mode demo has to separate four things that the engine today collapses
into one `failed` run whose Results read as detection misses:

```
  the agent killed the step        → PREVENTED        (the win)
  the step failed on its own       → execution error  (ours)
  the tool never arrived           → NOT_EXECUTED     (ours, the payload-shelf failure mode)
  the step was never reached       → NOT_REACHED      (a consequence of the win, not a gap)
```

**Process death alone is never PREVENTED.** The tool that never arrived kills a
step exactly the way the agent does. Confirmed prevention needs two witnesses:
the host says the technique's process was terminated, and the tenant says the
agent acted (`action_pretty` prefixed `Prevented`). One witness lands on
`pending` with its reason stated; it never lands on pass.

Everything the engine already holds about honesty applies unchanged: a zero is
degraded not ok, an unrecognised shape raises, authored is not proven, and
`tenant-verified` stays at its true count.

---

## 1 · Intent and why

A POV opens with the customer's success criteria, which map onto the FY27 index
and select use cases, scenarios and test cases. The DC provisions a lab, runs
scenarios so attributable signal lands in the customer's tenant, and generates
the readout from what is actually in that tenant.

For the XDR agent the demo the field runs is a **paired run**: the same scenario
on the same endpoint, first with the agent profile in **report** mode for full
visibility of the chain, then in **block** mode so the customer watches the
chain and its causality graph die at the step the agent terminated. The delta
between the two runs is the proof.

Nothing in the engine can express that today. There is no outcome axis on a
Result, the matcher never reads the alert's `action`, a killed step is a failed
run, and the steps after it look like misses. Separately, the library cannot
say *how* a scenario's signal reaches the tenant — live agent telemetry, a real
third-party sensor, a synthetic log pushed into a collector, or a finding
planted at rest — so a readout quotes a synthetic-replay detection and a live
BTP block as the same kind of win. That is the contention a customer raises
about the Broker VM content, and the readout has no answer.

---

## 2 · Goals, non-goals, anti-goals

### Goals

1. A paired run (report → block) whose readout states, per step, what the
   report-mode run observed and what the block-mode run prevented, and where
   the chain was severed.
2. An outcome axis carried end to end: scenario expectation → beacon
   termination cause → matcher reading `action` → Result outcome → verdict →
   causality graph → report.
3. Three declared, load-validated axes on the library: **outcome**, **evidence
   class**, **sophistication**. The readout groups by all three and states what
   each class proves and does not prove.
4. "Tool never arrived" and "step never reached" are first-class outcomes that
   are excluded from every denominator and reported on their own lines.
5. Soak and dedup honesty: an Analytics or ABIOC expectation that is silent on a
   tenant whose soakless state is not attested is `pending`, never a miss; a
   second-run silence inside the dedup window is `pending`, never a miss.
6. Library expansion along the axes: an agent-prevention family (one scenario
   per prevention module, each carrying both expectations), collector-depth
   scenarios for the six emitters that have none, and Unit 42 campaign chains
   that contain a blockable step.
7. The structural debts the review found: adopt the loader's `channel: eal`
   step so evidence class is mechanical, regenerate the catalog index, and
   reconcile scenario/card parity.

### Non-goals

- **Switching the agent profile.** The operator flips report → block in the
  Cortex console. CortexSim records what was declared and never writes to
  Cortex (`CORTEXSIM_XSIAM_ALLOW_WRITE` stays default-off).
- **Replacing synthetic-log content with live signal.** This design labels it
  for what it proves. Whether to replace any of it where a real sensor is
  reachable is a later decision, taken per scenario with the lab in hand.
- **Push-mode prevention.** Push bundles capture no results offline and run
  every step under `|| true`. Prevention is pull-mode only in this design.
- **Dispatching EAL plugins to the beacon.** Today a `channel: eal` step runs
  in SimCore's own process with `dry_run=True` hard-coded, is skipped by
  `_seed_results`, and has no push handling, while the 60 heredoc scenarios
  emit live from the target's network vantage. Moving execution is a separate
  project with its own consent gate. This design declares evidence class
  without moving where anything runs (§4.1).
- **Proving the Windows beacon path.** Windows variants are authored through
  `platform_variants` and stay marked unproven until they execute on Henry's
  Windows machine once the content library is full.
- **Exploit Protection and Device Control scenarios.** The first needs a real
  exploit trigger, the second needs hardware. Both modules stay in the enum and
  are deferred as content.
- **Migrating the connector to the XSIAM 3.x issues/cases endpoints.** Flagged
  as a verification item (§9); not in scope unless the alerts endpoint is found
  not to carry `action` on 3.x.

### Anti-goals (what would make this a failure even if it works)

- A readout that says PREVENTED on process death alone.
- A readout that counts a tool-absent step, or a step after a sever, as a
  detection miss.
- A storyline or report that renders a soak-silent Analytics row as ❌.
- A single coverage number that merges authored, proven, prevented and
  detected.
- A second corpus. Prevention scenarios live in the same library under the same
  schema; the outcome is a field, not a directory.
- An "unknown" alert action that satisfies a `prevent` expectation.

---

## 3 · What exists today (counted 2026-10-26)

Full findings with file:line are in Appendix A. The facts the design rests on:

| Area | State |
|---|---|
| Library | 177 loadable scenarios, 16 planes, 175 cards, 1116 step-detections. By evidence class: live agent 122 · live sensor 30 · synthetic log 25 · planted posture 18 (the last is not mechanically identifiable). 142/177 cite Unit 42. |
| Outcome | 0 scenarios expect an agent prevention. 8 cards declare one, in `panw_mapping.products[].coverage_tier: prevention`, 3 with `TBD-*` rule ids. |
| `channel: eal` | Exists in `StepSchema`; used by 0 scenarios. All 55 plugin uses are shell heredocs inside `command`. |
| Beacon exit | kill → `128+N`, missing tool → `127`, persisted only as text. `exit 137` and SIGKILL are the same integer. 0 tests on signal death. |
| After a kill | fail-fast skips remaining steps; their Results keep `executed_at` and `observed=false`. |
| Run | `status` ∈ {`failed`, `complete`}; no per-step outcome; no launch context; `consent` not persisted. |
| Loader | drops unknown step keys silently. |
| Tools | pull mode never installs a tier-4 tool (33 wired adapters assume the tool pre-exists); no presence check; 118 `\|\| true`-style fallbacks across 34 of 45 wired scenarios; `127` is not an integrity marker; scorecard formats skip the integrity check. |
| Matcher | reads name, source, MITRE, host, rule id, timestamps; never `action`; discards the raw alert. |
| Verdict | silent Analytics can FAIL an accuracy KPI and renders ❌ once terminal; no soak reason; window `[executed_at, +3600 s]`, one value for every type. |
| Pairing | client-only `MultiRunCompare.jsx`; nothing server-side. |
| Graph | edge states EXPECTED / CONFIRMED / BROKEN (out-of-window); no `severed`, no `not_reached`. |
| Readiness | no operator-attested facts anywhere. |

---

## 4 · The three axes (library structure)

### 4.1 Evidence class — how the signal reaches the tenant

Scenario-level field, required once Phase 2 lands, validated at load:

| `evidence_class` | Meaning | What a detection proves | What it does not prove |
|---|---|---|---|
| `live_agent` | the technique ran on a host the XDR agent watches, via the identity harness | the agent observed (or stopped) a real process chain | — |
| `live_sensor` | the technique produced real traffic or requests against a real third-party sensor (NGFW, IdP, SaaS, LLM, browser) | the sensor saw a real event and the rule fired on it | that the sensor is configured like the customer's |
| `synthetic_log` | shape-true records were emitted into a collector or Broker VM | the parser and the rule logic | that the product would see a real attack; the log→detection mapping is heuristic |
| `planted_posture` | an IaC module planted a finding at rest (CSPM / ASM / TIM / AI-SPM) | the posture sweep discovers what was planted | runtime behaviour |

Validation (lint + loader, same code): a `live_agent` scenario may not carry a
`channel: eal` step or a `plugin:` reference; a `synthetic_log` or `live_sensor`
scenario must carry at least one `channel: eal` step naming a plugin of the
matching family; `planted_posture` must name an `infra_modules_needed` entry.
The 47 scenarios that mix a plugin step with setup/verify agent steps classify
by their plugin step.

**How it is made mechanical without moving execution (Claude's call, revised
after the builder check):** the heredoc plugin calls stay exactly where they
run today. Each such step gains an additive annotation, `eal_plugin: <name>`,
that the lint cross-checks against the `plugin:` reference inside the command
(mismatch or omission is an ERROR once Phase 2 lands, a WARNING before). The
scenario's `evidence_class` is then validated against the set of annotated
plugins and their family, exactly as the table above states. A scenario with
no annotation and no plugin reference is `live_agent` or `planted_posture` by
its other fields. Nothing is dispatched differently; the loader's unused
`channel: eal` path is left alone and documented as such in `_schema.yml`.

Where the record landed — Broker VM, HTTP log collector, XDR Collector — is a
property of the campaign's collector configuration, not of the scenario. The
run records it from the campaign (`collector_kind`) so the readout can group
synthetic-log detections by ingestion path.

### 4.2 Outcome — what the agent is expected to do

**A seventh detection type (Henry's decision, 2026-10-26).** The six-value
`detection_type` vocabulary is all rule families. An agent prevention module is
not a rule: its alert has source `XDR Agent`, no rule id, and until now no card
object to bind to. The vocabulary becomes seven:

```
BIOC | XQL | Analytics | Correlation | IOC | ABIOC | Prevention
```

and cards gain a matching object kind:

```json
"detections": {
  "prevention_modules": [
    {
      "name": "BTP reverse shell from web server",
      "module": "btp",
      "platforms": ["linux", "windows"],
      "alert_name_pattern": "Behavioral Threat*",
      "description": "…",
      "mitre_techniques": ["T1059.004"]
    }
  ]
}
```

`module` ∈ `btp | exploit_protection | local_analysis | wildfire |
anti_ransomware | credential_protection | device_control | host_firewall`. The
slug is `prevention-<slugified-name>`, resolved by the same GAP-4 rule as every
other kind. The card's existing `panw_mapping.products[]` prevention-tier
entry is no longer the binding; the lint cross-checks that a card with
`prevention_modules[]` also carries one and warns when it does not.

Per expected detection:

```yaml
expected_detections:
  - plane: EDR
    type: Prevention
    outcome: prevent            # detect (default) | prevent
    ttp_ref: TTP-2026-0033
    detection_id: prevention-btp-reverse-shell-from-web-server
```

The module is read from the bound card object, never authored twice on the
scenario.

Rules, all load-time:
- `outcome: prevent` requires `type: Prevention`. ERROR otherwise. (A BIOC
  cannot block; prevention comes from the agent's profiles, not from rules.)
- `type: Prevention` with `outcome: detect` is legal: the module is expected to
  report but not block, for example a module the customer's profile keeps in
  report mode by policy.
- `prevent` is legal only when the scenario is `live_agent` and the detection's
  plane is EDR or CDR. Anything else is an ERROR.
- A `prevention-*` detection id must resolve to a `prevention_modules[]` object
  (GAP-4 as today); a card object with a placeholder name or an empty `module`
  is rejected by the validator, not warned.

Touched by the seventh type: `scenarios/_schema.yml` and `DETECTION_TYPES` in
the loader; the card schema and `detection_scanner/validate.py`; the
deterministic export and its `sha256sum -c` check in the `detection` CI job;
the GAP-4 resolution guard; `coverage_report.py`, which gains a `Prevention`
bucket and must not let it dilute the ABIOC+Analytics step-share floor; the
matcher's `_FAMILIES` and `signal_family` (§5.3); the report's `by_type` table;
and the UI `DetectionTypeChip`.
- The step carrying a `prevent` expectation may not mask its exit (`|| true`,
  `|| echo`, `; true`, `2>/dev/null ||` chains). ERROR. The step should run the
  technique's process directly (`exec`) so the shell's termination is the
  technique's termination. (Claude's call; the alternative — reading the
  child's death through a surviving shell — needs per-step process tracking
  the beacon does not have.)
- Every other step keeps masking as a WARNING in Phase 2 so the existing 118
  fallbacks do not break the corpus; closing them is content work.

A `prevent` expectation evaluates differently by run mode (§5.3). In report
mode it is scored as `detect` and labelled "report mode: detection only
expected"; in block mode it is scored as `prevent`.

### 4.3 Sophistication — what the detection means

Nothing new is authored beyond the seventh type. `expected_detections[].type`
carries it. The coverage report and the POV readout group by it, label
`Analytics`, `ABIOC` and `Correlation` as **signature-free** (derived, never
authored), and give `Prevention` its own group labelled **agent module**, so
"twelve detections" reads as "three blocked by the agent, eight behavioural
with no signature involved, one IOC match."

---

## 5 · The paired run (engine contract)

Pull mode only. One launch per mode; the pair is a link, not a new run type.

### 5.1 Launch context on the Run

`LaunchRequest` gains, and `Run` persists (same migration pattern as
`runtime_install_authorized`):

| Field | Values | Notes |
|---|---|---|
| `agent_profile_mode` | `report` · `block` · `unknown` (default) | operator-declared, never verified by the engine |
| `bas_mode_attested` | `true` · `false` · `null` | BAS mode on the agent profile: when enabled "only the simulation itself is terminated"; when disabled the beacon itself may be killed |
| `soakless_attested` | `true` · `false` · `null` | internal PANW tenant setting; lifts activation, dedup and the other soak timers |
| `attested_by` / `attested_at` | string / timestamp | who declared the three above |
| `paired_with_run_id` | run id or null | set on the second launch; validated: same `scenario_id`, same `target_agent_id`, different `agent_profile_mode`, first run terminal |
| `canary_run_id` | run id or null | the separate canary run that preceded a block-mode launch (§5.2); null when no canary was requested or none is available for the platform |

`GET /api/runs/{id}/pair` returns both runs. The report for a run with a pair
renders the delta (§5.6).

### 5.2 Beacon: termination cause and per-step outcome

The `/complete` body carries a per-step record in addition to the existing
exit code and summary:

```json
{"step_id": "step-03", "exit_code": 137, "termination": "signaled",
 "signal": 9, "reason": null}
```

`termination` ∈ `exited | signaled | anchor_terminated | beacon_lost | timeout
| aborted | not_run`.

**Which process the agent kills, and what the host can witness (Claude's call,
revised after the builder check).** 158 of 177 scenarios declare `cgo_anchor`
and run on the beacon's chained path: one persistent anchor shell is the CGO,
each step runs in a subshell of it, and the beacon reads only the integer `$?`
the anchor's sentinel prints. BTP terminates a causality chain, so in block
mode the realistic outcome is that the **anchor dies**, not just the step. The
design accepts three host witnesses, in descending strength:

| Host witness | How the beacon sees it | `termination` |
|---|---|---|
| anchor shell died mid-step | `ChainSession.RunStep` returns `-1` with "anchor shell terminated before step completed" (today reported as an execution error) | `anchor_terminated` |
| step subshell killed, anchor alive | `$?` ≥ 128 from the sentinel; on the per-step fallback path `WaitStatus.Signaled()` or the wrapper's `128+N` | `signaled` |
| the beacon itself died | no `/complete` ever arrives; the agent goes `offline` while the run is `running` | `beacon_lost` (set by SimCore, §5.8) |

The `$?` ≥ 128 discriminator is weak on its own (`exit 137` looks the same).
That is acceptable **only because the host witness is necessary, never
sufficient**: a forged host death with no `Prevented` alert lands on `pending`
(§5.3), and the `exec` rule on `prevent` steps (§4.2) removes the shell layer
that would otherwise mask the technique's exit. Phase 1 is therefore "carry the
cause on the wire and stop treating anchor death as an execution error", not a
redesign of step observation. A dedicated `Signaled()` test per identity
wrapper (`runuser`, `su`, `sudo`) is still owed. On Windows there is no signal
concept; a killed step reports `exited` with the killer's code and the design
leans on the tenant witness — one more reason Windows stays unproven.

`/output` persists `step_id`. SimCore persists each per-step record on a
`step_outcomes` table keyed by run and step.

Fail-fast stays. A `signaled` or `anchor_terminated` step stops the task; the
remaining steps are reported `not_run` with `reason: UPSTREAM_SEVERED` and the
run records `severed_at_step`. A `prevent` step ending that way is **not** a
failed run: `Run.status` becomes `complete` with `severed_at_step` set.

**Canary (Claude's call, revised).** A canary that works is, by definition,
killed, so it cannot be a step inside the scenario task under fail-fast. It is
a **separate, one-step run** of a built-in canary scenario, launched by SimCore
immediately before the block-mode run and linked by `canary_run_id` (§5.1).
The beacon needs no notion of roles. The canary command is a vendor-documented,
benign test trigger the agent in block mode is expected to terminate; never a
real sample. Outcomes:

- canary `anchor_terminated` / `signaled` with a `Prevented` alert →
  `block_mode_confirmed`;
- canary exits 0 in attested block mode → `block_mode_unconfirmed`; the block
  run still proceeds (the operator may have a reason), but every `prevent`
  expectation in it resolves `pending` with reason `BLOCK_MODE_UNCONFIRMED`
  instead of `fail`, and the readout's Attestations section says so;
- no canary documented for the platform → `canary_not_available`; mode stays
  attested-only and the readout says so.

### 5.3 Result outcome and the verdict rules

`Result` gains `outcome`, `alert_action` (the raw `action_pretty` string) and
`alert_outcome`. Observed alerts are persisted on an `observations` table
(normalised dict plus raw JSON, keyed by run and matched result) so the readout
can quote evidence instead of a note string.

`alert_outcome` is classified from `action_pretty` by prefix — `Prevented (…)`
→ `prevented`, `Detected (…)` → `detected`, anything else or absent → `unknown`.
`unknown` is legitimate for non-agent sources and can satisfy a `detect`
expectation; it can never satisfy a `prevent` expectation.

`Result.outcome` ∈ `prevented | detected | missed | pending | not_reached |
not_executed`.

Block-mode evaluation of a `prevent` expectation:

| Alert outcome | Host termination | `Result.outcome` | Verdict | Reason |
|---|---|---|---|---|
| `prevented` | `signaled` or `anchor_terminated` | `prevented` | pass | — |
| `prevented` | `exited` (0) | `pending` | pending | `PREVENTION_ALERT_WITHOUT_TERMINATION` |
| `prevented` | `beacon_lost` | `pending` | pending | `PREVENTION_UNCONFIRMED_BEACON_LOST` (enable BAS mode) |
| none yet | `signaled` or `anchor_terminated` | `pending` | pending | `PREVENTION_UNCONFIRMED_NO_ALERT` |
| any, canary survived | any | `pending` | pending | `BLOCK_MODE_UNCONFIRMED` |
| `detected` | `exited` | `detected` | fail | `DETECTED_NOT_PREVENTED` |
| none, window closed | `exited` (0) | `missed` | fail | — |
| any | `not_run` / `UPSTREAM_SEVERED` | `not_reached` | excluded | — |
| any | `not_run` / `TOOL_ABSENT` | `not_executed` | excluded | — |

**Matcher family.** `_FAMILIES` gains `prevention`, and `signal_family` maps
alert source `XDR Agent` (and `type: Prevention`) onto it instead of returning
`None`. The existing veto stands: families that are both known and different
never match, so a BTP block alert cannot credit a BIOC or Analytics
expectation, and a BIOC alert cannot credit a `Prevention` one.

A `detect` expectation keeps today's rules, with two additions: `not_reached`
and `not_executed` from the host side override everything, and a `prevented`
alert satisfies a `detect` expectation **of the same family** (a
`type: Prevention, outcome: detect` expectation observed in report mode).

`not_reached` and `not_executed` are excluded from every denominator —
coverage, accuracy KPI, MTTD — and listed on their own report lines.

### 5.4 Tool presence

Before a step whose scenario wires an `adapter_ref`, the beacon checks the
pack's `install.binary` is on `PATH` (the field already exists,
`tools/packs/_schema.yml:102`, and 77 of 92 packs carry it; a pack without one
records `TOOL_PRESENCE_UNCHECKED` and the lint warns).
Absent → the step is `not_run` with `reason: TOOL_ABSENT`, its Results are
`not_executed`, and fail-fast marks the rest `UPSTREAM_NOT_EXECUTED`.

`127`, `command not found` and `RUNTIME_DEPENDENCY_MISSING` join
`_INTEGRITY_MARKERS`. The scorecard formats stop skipping the integrity check.
The `TARGET_EGRESS_REQUIRED` compose warning is corrected to say that pull mode
does not install, and is surfaced on the run instead of logged at INFO.

### 5.5 Soak and dedup

- A `type: Analytics | ABIOC` expectation that is silent at window close on a
  run with `soakless_attested != true` resolves `pending` with reason
  `ANALYTICS_ACTIVATION_PERIOD`, and the accuracy KPI withholds the row.
- On the second run of a pair with `soakless_attested != true`, a silent
  `Analytics | ABIOC` expectation that the first run observed resolves
  `pending` with reason `DEDUP_WINDOW`.
- Analytics and ABIOC expectations use their own, longer reconcile window
  (`CORTEXSIM_RECONCILE_WINDOW_ANALYTICS`); the default is the builder's call,
  sourced from the Analytics Alert Reference Guide's test periods, not guessed.
- Storyline and report render `pending` as ⏳ with the reason, never as ❌.

### 5.6 Graph and report

Causality graph: new edge state `severed` on the lineage/sequence edge out of
the killed step; downstream step nodes carry state `not_reached`; the alert
node pinned to the killed step shows `outcome: prevented` and the module. The
existing `control_layer` "prevention" bucket is renamed to what it is
(`runtime_control`) so the word stops meaning two things.

Report (`GET /api/runs/{id}/report`) gains:

```
## Prevention Delta (report → block)        when paired_with_run_id is set
   per step: report-mode observed · block-mode outcome · module · MTTD
## Chain Integrity
   severed at step N of M · not_reached: [...] · not_executed: [...] with reasons
## Evidence Classes
   per class: expected · observed · what this class proves / does not prove
## Attestations
   agent_profile_mode · bas_mode · soakless — attested by, at; canary result
```

Coverage and the by-type table group by outcome, evidence class and
signature-free, and never merge them into one number.

### 5.7 Readiness

Three new rungs under *Manage → Readiness*, each rendered **attested, not
verified**, stating what would verify it (the canary for block mode; nothing
for soakless, which has no public dataset and no API surface). The UI's
provenance bug — reconcile-matched detections counted as "attested" because
observations are never passed to the storyline — is fixed in the same pass so
"attested" means one thing.

### 5.8 A run whose beacon dies

With BAS mode disabled the agent may terminate the beacon's whole tree. Today
such a run sits in `running` forever: `complete_run` fires only on the beacon's
POST, and the only orphan handling is restart-time `rehydrate()`. Phase 1
extends the existing agent heartbeat sweep: when an agent transitions to
`offline` while it owns a `running` run, the sweep terminalises that run as
`complete` with `termination: beacon_lost` on the step the beacon last
reported starting, `severed_at_step` set there, and the remaining steps
`not_run` / `UPSTREAM_SEVERED`. The run is then scored and pairable. Its
`prevent` expectations can reach at most `pending` (§5.3), because beacon
death is indistinguishable from a beacon crash on the host side; the readout
says "beacon terminated — enable BAS mode so only the simulation is
terminated."

---

## 6 · Content expansion

Authored only after Phase 2 carries the fields. Each scenario in the prevention
family carries both expectations so one YAML serves the pair.

**Agent prevention family, Linux first (Claude's module order; PROVISIONAL
until Henry supplies the agent version and the modules its Linux profile can
set to Block — rows that the attested list does not cover move to "Windows
variant only" or are deferred):**

| Module | Technique shape | Platform |
|---|---|---|
| `btp` | reverse shell from a web-server CGO; LD_PRELOAD injection; mass file encryption from an unexpected parent; credential-store read from an unexpected process | Linux now, Windows variant authored |
| `anti_ransomware` | canary-file tamper pattern | Linux now, Windows variant authored |
| `local_analysis` / `wildfire` | drop and execute a vendor test ELF / PE | Linux now, Windows variant authored |
| `host_firewall` | outbound connection against an enforced rule | Linux, if the attested list includes it |
| `credential_protection` | LSASS access | Windows variant only (Credential Gathering Protection is a Windows module; on Linux the credential-store read is BTP's) |
| `exploit_protection`, `device_control` | deferred (§2) | — |

**Collector depth:** one scenario per orphan emitter (`duo_auth_emitter`,
`globalprotect_emitter`, `third_party_alert_emitter`,
`third_party_firewall_emitter`, `third_party_vpn_emitter`), plus
`airs_prompt_attack` wired by at least one AIRS scenario so AIRS stops being
"semantically live-sensor, mechanically agent". Every `synthetic_log` scenario
labelled.

**Unit 42 chains:** campaign scenarios where a named chain contains a
blockable step, so the delta line reads "<campaign>, chain severed at step N by
<module>". Existing 142 Unit 42-sourced scenarios are the pool; the ones to
promote are chosen with the lab in hand.

---

## 7 · Phases and order

| Phase | Delivers | Proof it is done |
|---|---|---|
| **1 · Engine contract** | §5 in full, pull mode | Tier-D e2e extended with an external SIGKILL from the harness (not an agent) against **both** the step subshell and the anchor shell, plus a beacon-kill case that exercises the §5.8 sweep — proving all three host witnesses end to end; then one live paired run on the Linux VM with the host-installed agent, enrolled to `deathstar` — the first `tenant-verified` prevention, counted as 1 |
| **2 · Library structure** | §4 fields, lint codes, `channel: eal` migration, catalog regen (177 / 16 / DLP), scenario-card parity (177 vs 175), coverage report grouped by the axes | `make validate` and `make check-refs` green; every scenario carries an explicit or `inferred` evidence class; 0 `prevent` expectations with `TBD` rule ids |
| **3 · Content** | §6 | each prevention scenario executed in both modes on the Linux target; Windows variants authored and marked unproven |

Phase 2 cannot start before Phase 1's `Result.outcome` exists; Phase 3 cannot
start before Phase 2's fields exist. Each phase gets its own plan.

---

## 8 · Decisions made here

| Decision | Whose |
|---|---|
| Paired run: report mode first, then block mode, operator flips the profile | Henry |
| Block mode stops at the kill; the DC demos the severed chain and graph | Henry |
| Scope spans agent, Broker VM and collector; synthetic-log content is the contested class | Henry |
| Soakless and BAS mode are the lab guidance; soakless lifts all soak timers | Henry |
| Linux first; Windows later on Henry's machine; structure must be OS-agnostic | Henry |
| Seventh detection type `Prevention` with card object kind `detections.prevention_modules[]`, a `prevention` matcher family for source `XDR Agent`, validator / export / GAP-4 guard / coverage report updated | Henry |
| Prevention proof target is a Linux VM with a host-installed agent; the ladder's k3s container target keeps detection work; agent version and Block-capable module list to follow | Henry |
| Confirmed prevention needs both witnesses; one witness is `pending` | Claude, not objected |
| Canary step at the start of a block-mode run | Claude, not objected |
| Field names and enums in §4 and §5 | Claude |
| Migrate heredoc plugin calls to `channel: eal` | Claude |
| Label synthetic-log content; do not replace it in this design | Claude |
| `prevent` steps must `exec` and may not mask; other masking stays a warning | Claude |
| `not_reached` / `not_executed` excluded from denominators | Claude |
| Separate, longer reconcile window for Analytics / ABIOC | Claude |
| Persist raw observations | Claude |
| Pull-mode only for prevention | Claude |
| Module order in §6 | Claude |
| Three host witnesses (`anchor_terminated`, `signaled`, `beacon_lost`); host witness is necessary, never sufficient | Claude, after the builder check |
| Evidence class via `eal_plugin` annotation, execution untouched; EAL-to-beacon dispatch is a separate project | Claude, after the builder check |
| Canary is a separate linked run; a surviving canary turns `prevent` fails into `pending` rather than refusing the run | Claude, after the builder check |
| Beacon death terminalises the run via the heartbeat sweep; resolves `pending`, not `failed` | Claude, after the builder check |

---

## 9 · Left to the builder

- Migration SQL for the new Run, Result, `step_outcomes` and `observations`
  columns and tables.
- SSE frame names for per-step termination and outcome.
- Lint code numbers for the new rules (next free `S-2x`), and whether the
  `prevent`-step masking check reuses `scripts/lab_readiness.py`'s probe.
- The canary command per platform and module (§5.2 constraint applies).
- The built-in canary scenario's id and where it lives in `scenarios/`.
- The heartbeat-sweep threshold before a `running` run is terminalised as
  `beacon_lost` (§5.8).
- **Owed by Henry, not the builder:** the Linux VM's agent version and the
  list of prevention modules its profile can set to Block. §6's module table
  is rewritten from that list before Phase 3 is planned.
- The Analytics reconcile window default (§5.5 source applies).
- Exact report wording and the UI components touched (storyline, graph,
  readiness, `MultiRunCompare`).
- Test fixtures: a recording server that returns `Prevented (Blocked)` alerts;
  wrapper-level `Signaled()` tests for `runuser`, `su`, `sudo`.
- **Verification item, before Phase 1 code:** confirm on a 3.x tenant that
  `/public_api/v1/alerts/get_alerts_multi_events` still serves and that its
  records carry `action` / `action_pretty`. If not, the connector moves to the
  issues endpoint first and that becomes Phase 0.

---

## Appendix A · Review findings (2026-10-26, read-only)

Defects found in passing, not prevention-specific, recorded so they are not
lost:

1. Push bundle identity chain `runuser … || sudo -u … || su …` re-runs a
   killed or failed command up to three times and discards the first two
   stderrs (`core/engine/push_generator.py:531-533`).
2. Push bundle unknown-identity fallback runs the step as the bundle user via
   `bash -c` — a silent identity escalation (`push_generator.py:546`).
3. UI provenance: reconcile-matched detections count as "attested" because
   neither the storyline nor the causality API passes observations
   (`ui/src/api/storyline.js:25-49`, `core/api/causality.py:216-221`).
4. `scorecard` and `scorecard-html` report formats skip `_execution_integrity`
   (`core/api/runs.py:524-540`).
5. `TARGET_EGRESS_REQUIRED` warning text claims the target fetches at dispatch;
   pull mode never does (`core/engine/payload_shelf.py:845-855`,
   `orchestrator.py:1171-1176`).
6. The compose projection omits `steps`, so a staging failure is attributed to
   step 1 (`payload_shelf.py:438-468`).
7. `trivy.yml:32` points at a dead upstream URL and no gate notices.

Doc drift (code is ahead or behind the doc):

- `docs/reference/api-and-agent-surface.md:971-972`: says a missing binary
  returns `-1`; it returns `127`.
- `docs/reference/payload-shelf.md` §9.4 (S-19 shipped, S-20 and `stage_as`
  not), §9.5, §9.5a, §9.12, §9.13, §9.14 all shipped but still listed as owed.
- `docs/tool-adapters.md`: 84/69 packs, tier-4 ×27, 27 scenarios wired; code
  has 91 packs, tier-4 ×56, 45 files.
- `docs/reference/adapter-catalog.md` §9 and `adapter_loader.py:110-113`: tier
  4 "installs from the public internet at dispatch" is true only for push.
- `docs/reference/scenario-catalog.md`: claims 169 / 169 / 15, tree has
  177 / 175 / 16, DLP missing.

Review mechanics: four parallel read-only subagents (execution chain, adapters
and shelf, library axes, measurement loop and readout), every count from a
command they ran, file:line retained in their transcripts.

## Appendix B · Verified external facts

- **Alert action vocabulary** (XSOAR Cortex XDR IR integration reference,
  mirrors the public API): `action_pretty` values are all prefixed
  `Prevented (…)` or `Detected (…)`; raw `action` splits `BLOCKED*` /
  `PROMPT_BLOCK` from `REPORTED` / `DETECTED*` / `POST_DETECTED` / `SCANNED` /
  `DOWNLOAD` / `PROMPT_ALLOW`. The case record carries `is_blocked`.
  https://xsoar.pan.dev/docs/reference/integrations/cortex-xdr---ir
- **Soak**: the activation period "is informally referred to as soak time";
  per-detector lengths live in the Cortex XDR Analytics Alert Reference Guide.
  Rule status (Enabled / Disabled / Pending Activation) is visible only in the
  console; "XSIAM does not currently provide a public dataset that contains the
  metadata or status of detection rules."
  https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/analytics/analytics-detection-time-intervals
  https://live.paloaltonetworks.com/t5/cortex-xsiam-discussions/xsiam-analytics-rules-dashboard/td-p/1254198
- **Soakless mode** is internal to Palo Alto Networks and lifts every soak
  timer (activation, dedup, …). Not documented; operator-attested only. (Henry,
  2026-10-26.)
- **BAS mode** (agent profile, 5.x docs): Enabled → "only the simulation
  itself is terminated"; Disabled → BAS tools "are treated like any other
  malicious process". Recommended only for the evaluation window and a limited
  set of agents. The BAS module arrived in agent 8.9.0.
  https://docs-cortex.paloaltonetworks.com/r/Cortex-XDR/Cortex-XDR-5.x-Documentation/Set-up-malware-prevention-profiles
- **3.x vocabulary** is issues and cases; the Core platform integration reads
  `Core.CaseExtraData.issues.data.action_pretty`. See the verification item in
  §9.
