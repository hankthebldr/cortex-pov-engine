# Composer: doors by signal path, not by plane (2026-11-10)

Status: **approved 2026-11-10**, with CLOUD CONTROL PLANE as a fourth path
(§10). Branch `feature/ui-composer-doors`, cut from `dev` @ 3f5426a. This is phase 2 of
`2026-11-10-composer-correlation-story-design.md` (phase 1 merged as #173). It
replaces that spec's §8 where the two disagree; §9 below lists where and why.

## 1. Problem

Every step card in the Composer carries a door badge (`AGT`, `CC`, `BVM`, `DC`,
`ENG`), and the Lanes lens and the Runs topology band steps by the same five
keys. The badge is derived from the step's **plane**: NDR reads `BVM`, CDR reads
`CC`, ANALYTICS reads `ENG`. The plane says which product *observes* the signal.
It says nothing about how the step *produces* it, and that is what a DC needs to
know before a POV: is this something the agent sees, records I post to a
collector, traffic a firewall sees, or nothing at all?

Henry's requirement named two injection paths, "at the endpoint runtime" and
"fired in through the BVM". On 2026-11-10 he added a third: live traffic past a
sensor.

Measured on `dev` @ 3f5426a with a throwaway script (193 scenarios, 796 steps).
Phase 2's pure modules become the counted truth and a test pins them.

| Fact | Count |
|---|---|
| Steps declaring `channel: eal` | **0**. Every badge in the corpus is plane-derived |
| Steps that run the EAL CLI from the beacon (`eal_simulator … run`) | 138, each naming its plugin in the campaign heredoc (`plugin: <name>`) |
| ...naming a collector-class plugin (log injection) | 89 |
| ...naming a wire-class plugin (live traffic) | 49 |
| ...with an unparseable or unregistered plugin, or plugins of both classes | **0** |
| Steps that mention the EAL but only `describe` a plugin or import a helper | 27. Nothing is emitted; they are preflight checks |
| Steps that call a cloud or cluster API directly (`aws` 43, `kubectl` 14) | 44 |
| Steps whose command only echoes, sleeps or comments | 128 |
| Everything else: a command the beacon runs on the host (includes the 27 preflight steps) | 486 |
| EAL plugins | 27: 15 collector (have `collector_url`), 12 wire. The existing `/api/eal/plugins` `family` split agrees on all 27 |

**Today's badge against what each step does:**

| Badge today ↓ / step does → | endpoint | log injection | live traffic | cloud API | no action | total |
|---|---:|---:|---:|---:|---:|---:|
| `AGT` ENDPOINT | **293** | 26 | 17 | 6 | 58 | 400 |
| `CC` CLOUD | 81 | 25 | 12 | **38** | 18 | 174 |
| `BVM` NETWORK | 53 | **28** | 16 | 0 | 7 | 104 |
| `DC` DATA STREAMS | 17 | **10** | 0 | 0 | 1 | 28 |
| `ENG` ANALYTICS | 42 | 0 | 4 | 0 | 44 | 90 |

The bold cells are the ones where the badge names how the step is launched:
369 of 796. **The other 427 badges (54%) name a door the step is not launched
through.** 53 steps read "Broker VM relay" and run a command on the host; 90
steps sit in the Engine lane, which is where tenant content now lives
(phase 1).

## 2. The model: three facts, kept apart

| Fact | Question | Source | Shown as |
|---|---|---|---|
| **runs on** | where does the command execute? | `channel`, `target` | `AGT` (the beacon on the target host), `SIM` (SimCore's own process, `channel: eal`), `→ <agent>` (a second endpoint, existing chip) |
| **signal path** | what mechanism produces the evidence? | the command and the EAL plugin registry | the lane, and the badge's second word |
| **observed by** | which product's detection should fire? | the expected detection's `plane` | unchanged: the plane chip and tint |

A fourth fact, **where the evidence enters Cortex** (agent, forwarded logs, an
API connector, built-in), is now derivable from #174's
`spec/dataset_requirements.yml` through each detection's dataset. It is not in
phase 2 (§8).

## 3. Signal paths

A path is a **mechanism**: what the step does to produce evidence. It is not
the observer. A plain `curl` C2 beacon on an NDR step is ENDPOINT RUNTIME: the
beacon runs it on the host, and a firewall may also see its traffic, which the
plane chip already says. LIVE TRAFFIC means a purpose-built traffic generator
(an EAL wire plugin), the mechanism Henry named. Defining the lane by observer
instead needs every detection's dataset, and 207 steps carry a detection whose
dataset the cards do not name (§8).

| Key | Lane label | Lane sub-label | Rule (first match wins) | Corpus |
|---|---|---|---|---:|
| `COLLECTOR` | LOG INJECTION | records POSTed to a collector · Broker VM or HTTP collector | `channel: eal` with a collector-class plugin, or an EAL `run` naming one | 89 |
| `WIRE` | LIVE TRAFFIC | a traffic generator · a sensor sees it on the wire, NGFW or SaaS | the same, wire-class plugin | 49 |
| `CLOUD` | CLOUD CONTROL PLANE | the provider's audit log · pulled by a connector | the command calls `aws`, `gcloud`, `gsutil`, `az`, `bq` or `kubectl` | 44 |
| `NONE` | NO ACTION | the step only logs or waits | every line is `echo`, `printf`, `sleep`, `date`, `true`, `:`, a comment or blank | 128 |
| `ENDPOINT` | ENDPOINT RUNTIME | the beacon runs it on the host · the Cortex Agent sees the process | anything else | 486 |

Lane order on the canvas: LAUNCH · ENDPOINT RUNTIME · LOG INJECTION · LIVE
TRAFFIC · CLOUD CONTROL PLANE · NO ACTION · CORTEX ENGINE (tenant content only) ·
PROOF.

* **The badge** reads runs-on, then the path when it is not the endpoint:
  `AGT`, `AGT → COLLECTOR`, `AGT → WIRE`, `SIM → COLLECTOR`, `AGT → CLOUD`,
  `AGT · no action`. Its title text names the plugin and the lane's sub-label;
  the Inspector shows both.
* **Broker VM is named only on LOG INJECTION**, and only as one of the possible
  destinations. Which collector a run posts to is known from `collector_url` at
  run time, not from the step.
* **A plugin whose class cannot be read** (the plugin list failed to load, or a
  name the registry does not know) reads `AGT → EAL ?` in the error tone. It is
  never guessed into a lane; it goes to ENDPOINT RUNTIME with an `unplaced`
  reason, the Runs topology convention.
* **NO ACTION is honest, not a judgement.** Many of those 128 steps wait for a
  scan of planted infrastructure (CSPM, ASM, AI-SPM) or print a marker. The lane
  says the step produces nothing itself; it does not say the scenario is wrong.
* **The CORTEX ENGINE lane holds tenant content only.** No step derives into it.
  The 90 steps there today move to the path their command takes.

## 4. Derived, not declared

Phase 1's §8 proposed a per-step `emitter` field with a 165-step backfill,
because reading the plugin out of a heredoc looked brittle. Measured, it is not:
all 138 steps that run the EAL CLI name exactly one class of plugin, every name
is in the registry, and the 27 "unparseable" steps turned out to emit nothing.
A declared field would add 138 YAML edits that carry no information the command
does not already state.

So phase 2 **derives** the path, in one pure function per language, and guards
the derivation instead of duplicating it:

* `ui/src/components/console/composerSignal.js` derives it in the browser, so the
  badge follows an edited command without a round trip. Plugin classes come from
  `GET /api/eal/plugins`.
* `core/engine/signal_path.py` derives it for the loader lint and the corpus
  test.
* `spec/signal_path_cases.json` is asserted by both, the phase-1 pattern.
* **Loader lint S-28 (ERROR, structural, not gated by `CORTEXSIM_STRICT_REFS`):**
  a step whose command runs the EAL CLI must name at least one plugin the
  registry knows, and all its plugins must be of one class. Zero hits today.
* **Corpus tripwire:** the five counts above, pinned, so a scenario that changes
  them is a visible edit.
* **Plugin class is pinned too:** a test asserts `family == analytics_log_streamer`
  if and only if the params model has `collector_url`, for every plugin.
  `GET /api/eal/plugins` gains `delivery: collector | wire`, derived from the
  same family.

If a future step cannot be derived from its command, an optional authored
override is the answer then; phase 2 does not add one.

## 5. Lanes stop being a retarget control

Today a card dragged into another lane either writes the step's `channel`
(ENDPOINT ⇄ DATA STREAMS) or records a presentation-only `laneOverrides` entry
that the badge then follows. With the lane derived from what the step does, a
drag would either rewrite the command or lie about it.

* The Lanes lens becomes **read-only for lane membership.** A card dropped in
  another lane snaps back with the reason: "A step's lane follows what its
  command does. Change the command, or the channel in the Inspector."
* `setStepLane` and `laneOverrides` are removed. `laneOverrides` was never
  persisted (`DraftScenarioSchema` drops it), so no saved draft loses anything.
* The channel stays editable in the Inspector, and a `channel: eal` step still
  reads `SIM → …`.

## 6. Who reads it

`laneOf()` stays the one function every consumer calls; only its answer changes.

| Consumer | Change |
|---|---|
| Composer door badge (`composerGraph`, `ComposerCanvas`) | two-fact badge; title names the plugin |
| Composer Lanes lens | new bands (§3); rules stay in CORTEX ENGINE; drag no longer retargets |
| Execution timeline (`ExecutionTimeline.jsx`) | lane chip from the new keys |
| Runs topology (`runTopology.js`, `RunTopologyView.jsx`) | bands become signal paths; "N of 5 doors used" becomes "N of 5 paths used"; the Engine stays a view onto Correlation / Analytics / ABIOC rows |
| Runs topology without the scenario | today lanes are rebuilt from each Result's `plane`. A path needs the command, so those steps become `unplaced` with the reason "scenario not loaded: the signal path is read from the step's command" |
| `/api/eal/plugins` | gains `delivery` |

Tests that pin the old keys (`composerLanes.test.js`, `ComposerCanvas.test.jsx`,
`runTopology.test.js`, `RunTopologyView.test.jsx`, `povdata.test.js`,
`tests/e2e/10-runs-rebuild.spec.ts`) are rewritten against the new keys, each
with a case from the crosswalk in §1 that the old derivation got wrong.

## 7. Guards

* **vitest, `composerSignal.js`:** every rule in §3 and its precedence, the
  unknown-plugin and plugins-not-loaded cases, the shared fixture.
* **pytest, `signal_path.py`:** the shared fixture, S-28 on a crafted step, the
  plugin-class pin, the corpus tripwire.
* **Existing guards hold:** `validateDraft` / `spineProblems` still match
  `validate_causality_spine`; a fork carries every field the draft contract
  accepts; no draft field is added.
* **Real browser:** `ui/tests/e2e/13-composer-doors.spec.ts` through installed
  Chrome. `SIM-AIACC-003` step-02 reads `AGT → WIRE` and sits in LIVE TRAFFIC; a
  collector scenario reads `AGT → COLLECTOR`; an NDR step that runs a plain
  command no longer reads Broker VM; a dropped card snaps back with the reason;
  the Runs topology bands a finished run by path. `09` and `10` stay green.

Every new guard is observed failing with its change reverted.

## 8. Deliberately not done

* **Where the evidence enters Cortex.** #174's registry says, per dataset,
  whether records arrive from the agent, by log forwarding, through an API
  connector or built in. Joining that to each detection would show "observed by
  NGFW · enters by log forwarding" in the Inspector. Phase 2b, after this lands.
* **Naming the collector.** Whether a LOG INJECTION step posts to a Broker VM
  applet or an HTTP collector is set per run (`collector_url`); the EAL preflight
  already answers it.
* **Changing any scenario.** No YAML is edited. The 427 wrong badges are a
  derivation fix, not a corpus fix.
* **Phases 3 and 4** of the phase-1 spec (deployable rules, present in tenant).

## 9. Where this departs from phase 1's §8

| Phase 1 §8 said | Phase 2 does | Why |
|---|---|---|
| a per-step `emitter` field, backfilled on 165 steps | derive from the command; S-28 lint and a tripwire | derivation is exact on today's corpus; the field would carry nothing new |
| plugins declare `Meta.delivery` | derive from the existing `family` split, pin it, serve it | the two already agree on all 27 plugins |
| three signal paths | five lanes: Henry's three, plus CLOUD CONTROL PLANE and NO ACTION | 44 steps call a cloud API directly; 128 do nothing themselves (§10) |

## 10. Decisions

**Decided (Henry, 2026-11-10): a direct cloud API call is a fourth path,
CLOUD CONTROL PLANE.** 44 steps call `aws` or `kubectl` directly from the
beacon. The agent sees the `aws` process, but the evidence their detections read
mostly arrives another way: 30 of the 44 read only datasets that #174's registry
marks as API- or audit-log-ingested (CloudTrail, Kubernetes audit).

**Defaults taken** (none was struck).

1. Derive the path; add no YAML field (§4).
2. Lanes are read-only; drag no longer retargets; `laneOverrides` is removed (§5).
3. NO ACTION is its own lane rather than being placed in ENDPOINT RUNTIME (§3).
4. Runs topology follows the new lanes, and a run whose scenario cannot be
   loaded shows its steps as `unplaced` with the reason (§6).
5. "Where the evidence enters Cortex" waits for phase 2b (§8).
