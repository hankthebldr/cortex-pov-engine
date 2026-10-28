# KOI / AES — GlassWorm-shaped agentic endpoint takeover on a lab Mac (SIM-KOI-009)

**Status:** design, builder check done, awaiting approval · **Date:** 2026-10-28 · **Author:** Henry Reed (design conversation with Claude)
**Plane:** KOI · **Scenario:** `SIM-KOI-009` · **Card:** `TTP-2026-0194` · **Branch:** cut from `dev`

> This document is deliberately written at the level of *observable shapes* and
> *detection expectations*. It does not carry attack-mechanics detail, and it
> forbids the builder from implementing evasion mechanics (encrypted loaders,
> execution-delay timers) or functional credential theft. CortexSim validates
> that Cortex *sees* a behaviour; it never ships something that works as the
> real thing.

---

## 1. Intent and why

Every KOI scenario shipped today (`SIM-KOI-001..008`) proves two evidence
paths: Cortex Cloud Code scanning the static artifact pack, and the NGFW
seeing the *fetch* of an artifact through the `agentic_egress` EAL plugin.
**None of them puts an agentic component where the Cortex Agentic Endpoint
Security (AES) sensor inventories it, and none runs one where Cortex XDR
records the behaviour.** A customer evaluating AES therefore has no CortexSim
scenario that exercises the product's own four pillars (inventory, risk score,
guardrails, real-time prevention) on a real endpoint.

GlassWorm's fourth wave (Koi Security, now the PANW Security Operations blog,
published 2026-10-01, originally 2025-12-29) is the right anchor: a poisoned
Open VSX extension that lands on a **macOS developer workstation**, executes
under the IDE's extension host, persists, collects developer credentials and
Keychain material, stages them locally and sends them out. It is the first
KOI scenario whose kill chain is platform-specific (macOS), and it is the
campaign PANW's own AES positioning uses.

This design turns `SIM-KOI-009` from the placeholder scaffold that
`scripts/research_intake.py` left in the tree into the first scenario that
**installs and runs an agentic component on a lab endpoint** so both AES and
XDR have something real to observe.

## 2. Goals · Non-goals · Anti-goals

**Goals**

1. A DC can run one scenario on a macOS lab endpoint and observe, in this
   order: the AES inventory picking up a newly installed, unknown-publisher IDE
   extension with a risk score; XDR telemetry recording the extension host
   spawning a scripting host, touching Keychain- and credential-named paths,
   writing a LaunchAgent and sending an archive out; and one correlated
   incident on the shared `causality_id`.
2. Every step produces a signal a sensor can record (zero marker-only steps).
3. The scenario, card, crosswalk row, counts, exports, golden digests and
   ground truth land together so every gate in `make validate` and
   `make coverage-strict` stays green.
4. The lab-asset "setup instruction" is a documented rung in the lab runbook,
   using the existing enrollment one-liner — not a new installer.

**Non-goals**

- Modelling the encrypted JavaScript loader or the 15-minute execution delay.
  Those are evasion mechanics; the card may *describe* the code-scan detection
  for them, but no step exercises it. This is recorded as an honest gap (§9).
- Modelling blockchain-memo C2 discovery or the wallet-application replacement
  routine. Both are carried as card context only.
- Windows or Linux variants in v1. The campaign is macOS; the scenario is
  macOS-only and says so.
- A general "install all six pack components" provisioner. Henry chose the
  single end-to-end scenario; the provisioner is a separate follow-up.

**Anti-goals** (what would make this a failure even if every gate is green)

- Shipping anything that functions as malware: real Keychain reads, real
  credential file reads, a real C2, or persistence that survives cleanup.
- Reporting the scenario as *proven*. It is authored. `tenant-verified`
  stays 0 until a run executes against a live tenant.
- Collapsing "AES observed it" into "XDR observed it". They are different
  sensors with different evidence; the scenario keeps them as separate legs.

## 3. What exists, what is wrong

| Thing | State |
|---|---|
| `scenarios/koi/sim-koi-009-glassworm-agentic-endpoint-takeover.yml` | `research_intake.py` scaffold, `status: draft`, two echo-only steps, placeholder `UC-KOI-009`/`TC-KOI-009` refs that do not resolve under strict refs |
| `detection_scanner/ttps/TTP-2026-0194-sim-koi-009.json` | matching scaffold, `status: draft`, one placeholder detection per type |
| `sources/cortex-malicious-agentic-pack/vscode/helpful-ai-assistant/` | existing benign-by-gating extension source; reads real `~/.aws/credentials` and `~/.ssh/id_rsa` when present, which this design does **not** reuse |
| `sources/cortex-malicious-agentic-pack/README.md` | promises `tools/build-artifacts.sh`; the script is **not on disk** |
| `core/eal_simulator/plugins/agentic_egress.py` | emulates the *fetch* only; nothing lands on the endpoint |
| Lab runbook | covers the macOS beacon (launchd service, `os=macos`) but has no agentic-endpoint rung |

## 4. Evidence model — three legs, each honest about what it proves

| Leg | Sensor | What it proves | How the DC reads it | Machine-scored? |
|---|---|---|---|---|
| **A — AES inventory and risk** | Cortex AES sensor (standalone, Cortex module, or Prisma AIRS) | The extension landed, who published it, what it can reach, and the risk score AES assigned; whether a guardrail (Delayed Access / Version Update Cooldown) or the registry gateway would have blocked the install | AES console, or the XSIAM incident when AES is deployed as a Cortex module | **No.** No public dataset name for AES findings exists in this repo. Recorded as a DC-observed evidence line on the scenario, never as a scored detection. |
| **B — XDR behavioural telemetry** | Cortex XDR agent (macOS) | The extension host → scripting host lineage, Keychain- and credential-path touches, LaunchAgent write, staging-directory create, outbound session — all on one `causality_id` | `xdr_data` XQL / BIOC / ABIOC on the card; correlation `CR-KOI-0009` | Yes, through the existing measurement loop (Detection Accuracy, F1). |
| **C — NGFW egress (optional)** | NGFW / EAL | The archive leaving the host to a non-marketplace destination | NDR-plane XQL on the card | Yes, when the lab has an NGFW in path; otherwise `not_reached`. |

Leg A is the reason this scenario exists; leg B is what the engine can score.
The scenario YAML declares leg B and leg C expectations. Leg A is written into
`success_criteria` and the step descriptions as "observe in AES", and into the
card's `panw_mapping` as a `cortex-aes` product row with `coverage_tier:
visibility` so the readout shows it as an observation, not a verdict.

`outcome: prevent` is **not** declared anywhere: the schema allows it only on
EDR/CDR planes and only for the XDR agent's own prevention modules. An AES
guardrail block is therefore a leg-A observation. Wiring AES prevention into
the two-witness Prevention axis is a follow-up for the prevention-efficacy
program, not this scenario.

## 5. The scenario — shape, not recipe

Every step runs on macOS as the user the beacon is enrolled as, with identity
`direct` and **no other option** (`execution_identity.options: [direct]`).
`developer` appears only as the `cgo_anchor.primary_username` label; on
darwin a non-direct identity resolves to `sudo -n -u <name>`, which would
fail on any Mac whose session user is not literally that name, and the card's
queries never key on a username. Six steps. No step masks its exit status
(S-21 shape). No step is echo-only. Every artifact a step creates carries
`CORTEXSIM-CANARY` and is removed by `cleanup`.

**Step trigger mechanism (design call).** The extension's activation does
only step 02's work and registers a VS Code URI handler for the component.
Each of steps 03–06 is a beacon-run command that opens that component's
`vscode://` deep link for its step id; the IDE delivers it to the extension
host, which performs exactly that step's decoy action **as a child of the
extension host**. So every step has its own beacon command (its own
`executed_at` and `Result` rows), none is echo-only, and the real `xdr_data`
lineage for 03–06 is extension host → child, not beacon → child. Fallback if
URI delivery proves unreliable on the lab Mac: a file-watch trigger in the
decoy tree, same contract. Either is test orchestration, not attack
mechanics.

| # | Technique | Observable shape the sensor must record | Safe form (what makes it harmless) | Expected detections (plane · type) |
|---|---|---|---|---|
| 01 | T1195 Supply Chain Compromise | A new extension directory appears under the user's VS Code extensions path, installed through the IDE's own CLI from the in-tree artifact. On macOS the `code` CLI runs the IDE's own main-process image, so this is the CGO image's first appearance | The artifact is the benign pack component in §6; publisher is a canary name, not the real campaign's publisher | KOI · XQL (validation: extension dir created, install invocation) · **leg A: AES inventory + risk score, observed** |
| 02 | T1059.002 AppleScript (via the extension host) | The IDE is launched; on activation the extension host spawns one macOS automation-interpreter child and registers the step trigger. Parent→child lineage under the IDE is the signal | Activation writes a canary marker and spawns the child once; the child exits | KOI · ABIOC (IDE extension host spawning a scripting host, unsigned extension, first-seen publisher) |
| 03 | T1555.001 Keychain | A `security`-utility invocation and a read of a `*.keychain-db` path by an extension-host child | The beacon step first creates a **decoy keychain** with a decoy item, then triggers; the login keychain is never opened or copied | KOI · BIOC (Keychain access by an IDE descendant) |
| 04 | T1543.001 Launch Agent | A plist written under `~/Library/LaunchAgents/` by an extension-host child and loaded into the user's launchd domain | The plist's program is a no-op; cleanup boots it out and deletes it; label carries the canary string | KOI · BIOC (LaunchAgent write + load from IDE lineage) |
| 05 | T1552.001 + T1074.001 Credentials in files, local staging | ≥3 credential-named files read in one window by an extension-host child, then a staging directory created and populated | The beacon step plants **decoy** files with `CORTEXSIM-DECOY` values in its own temp tree, mirroring the report's file *names* (VS Code storage token, `.npmrc`, SSH key, Keychain copy), then triggers; the staging directory name mirrors the report's | KOI · BIOC (multi-credential read under IDE CGO) · KOI · XQL (validation: staging dir create) |
| 06 | T1560 + T1041 Archive, send over C2 channel | The staging directory archived and sent to a canary destination by an extension-host child | Destination is `cortexsim-canary.invalid` unless `CORTEXSIM_C2_URL` names an authorised sink; nothing leaves otherwise | NDR · XQL (IDE descendant egress to a non-marketplace host) · KOI · XQL (validation) · KOI · Correlation `CR-KOI-0009` stitching 02→06 on `agent_hostname` + `causality_id` (attached to the last real step, as KOI-008 does) |

**Causality contract.** `cgo_anchor.image_name` is the VS Code **main-process**
image on macOS (the binary inside the app bundle, which the `code` CLI also
runs), `primary_username: developer` (label only). Step 01 is the single
root, under the CGO. Step 02 is rooted at the CGO with a `shared_entity` edge
from 01 (the installed extension directory is what install and activation
share; 02's process is not 01's child, and the contract must not claim it
is). Steps 03, 04 and 05 are `process_lineage` from 02 (extension-host
children, which the trigger mechanism makes literally true). Step 06 is
`network_session` with parent 05 (the staged data leaves the host), the same
edge KOI-008 uses for its send step. One connected spine, no synthetic star.
The builder verifies the main-process and extension-host image names on the
lab Mac and writes the card's `causality_actor_process_image_name` predicates
as a set of the known VS Code image names, never one literal.

**Indicators.** The scenario ships **no `indicators[]` block** (see §9, S-23).
The card's `detections.iocs[]` carries the report's network and host
indicators with `context` and no `source_url` — permitted by
`check_ioc_provenance`, which only rejects a *bad* URL and only warns on
Unit 42 cards. The extension identifiers from the report are listed as
`ioc_type: file` context, not as values any step uses.

## 6. The artifact — a new pack component

`sources/cortex-malicious-agentic-pack/vscode/prettier-pro-canary/` with the
same two-file layout as the existing component (`extension/package.json`,
`extension/dist/extension.js`). Rules it must obey, enforced by a test:

1. `publisher` is a canary name (`cortexsim-canary`), `displayName` mirrors
   the campaign's lure ("Prettier Pro"), description contains
   `CORTEXSIM-CANARY`, `activationEvents: ["*"]`.
2. Activation performs **only** step 02's shape (canary marker, one
   scripting-host child that exits) and registers the URI handler. Each
   handled step id performs exactly one decoy action, reading its target
   path from an environment variable the scenario sets to the decoy tree.
   With those variables unset, a trigger writes the canary marker and
   returns.
3. Every outbound call is gated on `CORTEXSIM_C2_URL`, exactly like the
   existing component; default is no network.
4. **No encryption, no obfuscation, no timers, no reads outside the decoy
   tree, no persistence the cleanup cannot remove.** A test greps for the
   absence of `crypto`, `setTimeout`, base64 decoding helpers, and absolute
   home-directory credential paths.
5. Packaged to `.vsix` at install time by step 01 with the OS zip tool, or by
   the promised `tools/build-artifacts.sh` if the builder chooses to add it
   (the README already claims it exists — adding it closes a doc lie).

The pack README's layout table and `scenarios/koi/README.md` gain the row.
`tools/packs/cortex-agentic-pack.yml` `ttp_refs` gains `TTP-2026-0194`.

## 7. Lab asset prerequisites and the "setup instruction"

The setup instruction is a new rung in `docs/reference/lab-runbook.md`,
"Agentic endpoint (macOS)", stating in this order:

1. A macOS endpoint (arm64 or amd64) with a **logged-in developer session**.
   The beacon must be enrolled as that user, not root, so the launchd job
   lands in `~/Library/LaunchAgents` (the installer already branches on
   `id -u`). The IDE, the Keychain and the LaunchAgent domain all belong to
   the session user; a root daemon cannot drive them.
2. VS Code installed with the `code` CLI on PATH.
3. Cortex XDR agent installed on the Mac (macOS telemetry for file, process
   and network events).
4. Cortex AES sensor installed and enrolled, when the tenant is entitled.
   Without it, leg A is simply not observed and the scenario still scores
   leg B.
5. The CortexSim repo checked out on the Mac and the beacon's working
   directory set to the checkout root. **This is an inherited dependency**:
   `SIM-KOI-001..008` already reference `sources/cortex-malicious-agentic-pack/`
   by relative path. The rung states it plainly instead of hiding it.
6. The enrollment one-liner with `os=macos` (existing), then the scenario
   launched in pull mode. `CORTEXSIM_C2_URL` unset unless an authorised canary
   sink exists.

Push mode emits a POSIX bundle carrying the macOS commands. Nothing in the
generator or the tests reads `platforms` to withdraw it: the golden-digest
invariant and the tier-B bundle tests treat it as an ordinary POSIX bundle
(POSIX resolution for every step, shellcheck clean), so it must be exactly
that. The bundle header states it executes on macOS only. Tier-C's docker
end-to-end runs `SIM-EDR-001` alone, so CI never executes this bundle on
Linux. This is the first macOS-only scenario in the corpus; the builder adds
a macOS-only exclusion to a test only if one actually fails, not pre-emptively.

## 8. Repo integration — files, counts, gates

**Rewritten:** the scenario YAML and the card (both `status: active`,
`entry_version` bumped, `metadata.pov_engine.platforms: ["macos"]`,
`execution.target_platform` set to the schema's macOS value).

**Added:** the pack component (§6); a scenario-specific test module asserting
zero marker-only steps, `platforms: [macos]` on every step, no exit masking,
every `detection_id` resolving to the card, and the artifact rules.

**Edited:**

| File | Change |
|---|---|
| `scripts/uctc_crosswalk_v2.2.py` | `SIM-KOI-009` entry `(["TC-SCA-01","TC-SSCAN-01"], NETNEW, …)` mirroring KOI-008; NETNEW row `TC-AEPS-09` under `UCS-AEPS-02` "Agentic Supply-Chain Compromise", Detection Accuracy >90%, source `XSIAM (XQL: dataset=xdr_data)` |
| engine coverage sheet | regenerated with `--emit-xlsx` (`check-uctc-sheet` is fail-closed) |
| `detection_scanner/exports/` | regenerated with `export_artifacts.py --clean` |
| `tests/engine/_golden/push_bundle_digests.json` | regenerated per that test's own docstring |
| `tests/api/test_uctc_index.py`, `tests/engine/test_corpus_refs_strict.py` | 189 → 190 with a dated note |
| `docs/reference/ground-truth.{json,md}` | `make ground-truth` (runs in the image; needs `make build`) |
| `docs/reference/README.md`, `scenario-catalog.md`, `ttp-catalog.md`, `CLAUDE.md` | counts 189/187 → 190/188; KOI row 8 → 9 scenarios |
| `scenarios/koi/README.md`, pack README | new rows; the pack README's "nothing connects out" promise restated for the new component |
| `core/planes/koi.py` | `key_techniques` + `T1543.001`, `T1555.001` |
| `docs/reference/lab-runbook.md` | the macOS agentic-endpoint rung (§7) |

**Gates that must be green before the branch is done** (Gate A, plus the
repo's "branch green means make ci gates" rule): the lint hook on the YAML,
`python3 detection_scanner/scripts/validate.py` (0 fail), `make validate`,
`make coverage-strict`, host pytest (`tests/`, minus smoke/e2e_isolated), the
golden invariant test, `tests/e2e_isolated` unchanged. Plus Gate A's own
evidence rule: the new artifact-rules test must be observed failing against a
deliberately broken fixture before it is trusted.

**Refs.** `uc_ref: UCS-SCA-01`, `tc_ref: TC-SCA-01`, `tc_refs:
[TC-SCA-01, TC-SSCAN-01]`, `pov_scenario_id: POV-SC-012` — the same binding
KOI-008 uses, so S-10..S-16 resolve and the AEPS mapping lives in the
crosswalk where the other KOI AEPS rows already are.

**Methodology.** F1 · Detection Accuracy ≥ 90 % · `moat_tier: MOAT`, per the
KOI plane convention.

**MITRE.** Primary `T1195` (TA0001); additional `T1059.002`, `T1543.001`,
`T1555.001`, `T1552.001`, `T1074.001`, `T1560`, `T1041`. Card context only
(not exercised): `T1102` web service for C2 discovery.

## 9. Constraints discovered during design (do not rediscover them)

1. **S-23 / IOC provenance accept only Unit 42 and the PaloAltoNetworks
   GitHub org.** The GlassWorm post lives on `paloaltonetworks.com/blog/
   security-operations/`; no Unit 42 article on GlassWorm exists. So:
   no `indicators[]` block; `threat_report_url` points at the SecOps post;
   `is_stitched_chain` is false for this scenario (it requires a Unit 42
   host), so it adds nothing to the HQ-signal or stitched-chain counts and
   breaches neither floor.
2. **Marker-only ceiling is binding.** The corpus sits at 100 marker-only
   steps of 765 against a 0.1308 ceiling. One marker-only step in a
   seven-step scenario breaches it. Zero is the only acceptable number.
3. **`outcome: prevent` is EDR/CDR-only** in the schema; KOI cannot carry it.
4. **Push target resolution reads command text, not `platforms`.** macOS
   commands are POSIX-shaped, so a bash bundle is emitted and every bundle
   test treats it as POSIX (§7). It runs on a Mac only, which is correct and
   must be stated in the bundle header.
5. **Beacon working directory.** The executor leaves `Dir` unset; the
   relative-path dependency on the repo checkout is inherited from
   KOI-001..008 and documented, not fixed here.
6. **`make ground-truth` needs the built image** (docker). The builder must
   have `make build` available or hand the regeneration to someone who does.
7. **Scaffold ids are reserved, not free.** Reuse `SIM-KOI-009` and
   `TTP-2026-0194`; do not mint new ones.

## 10. Design calls

**Mine (Claude), each reversible by Henry:**

- macOS-only in v1; no Linux/Windows variants.
- Identity `direct` only, because the IDE lives in the session user's
  context and darwin's `sudo -n -u` path would fail for any other name;
  `developer` is a label on the CGO node, not a runtime identity.
- Steps 03–06 are triggered through the extension's URI handler so their
  real lineage is extension host → child; six steps, with `CR-KOI-0009` on
  step 06.
- The CGO image is the VS Code main process; 01→02 is a `shared_entity`
  edge, not a process edge.
- The push bundle is an ordinary POSIX bundle to every test; no pre-emptive
  macOS exclusion.
- A **new** pack component rather than reusing `helpful-ai-assistant`,
  because the existing one reads real credential paths and the new one must
  be decoy-only.
- Evidence leg A recorded as observation (`coverage_tier: visibility`), not
  as a scored detection.
- Setup instruction as a runbook rung, not a new script.
- No `indicators[]`; report IOCs on the card without `source_url`.
- Crosswalk NETNEW `TC-AEPS-09` rather than binding an existing AEPS row
  (the three indexed AEPS rows are AUT/PLT capability rows).

**Henry's (asked once, after the builder check):**

- Whether to widen S-23 / IOC provenance to accept the PANW Security
  Operations blog now that Koi publishes there. Not needed for this scenario
  to land; it would let a later version carry `indicators[]`.

## 11. Left to the builder

- Exact step commands, written in the repo's existing safe-mode style
  (decoy files, canary sinks, no exit masking), and verified on a lab Mac.
- The precise VS Code main-process and extension-host image names on macOS
  and the card's query predicates (a set of names, never one literal).
- URI-handler versus file-watch trigger, decided on the lab Mac.
- Detection bodies (XQL/BIOC/ABIOC) with a `dataset = xdr_data` anchor,
  real field names only, keyed on `causality_actor_process_*` +
  `causality_id` as KOI-008 does.
- Whether to add `tools/build-artifacts.sh` or zip inline in step 01.
- The golden-digest regeneration procedure (read the invariant test).
- Branch name under the repo convention (`feature/scenarios-koi-009-…`);
  the current worktree branch does not follow it.

## 12. Acceptance

The design is done when: the scenario and card load `status: active` with
zero rejections; every `detection_id` resolves; the lint hook prints `OK`;
`make validate` and `make coverage-strict` exit 0 with counts 190/188; the
artifact-rules test has been observed failing on a broken fixture; the lab
runbook has the macOS rung; and `tenant-verified` is still reported as 0.
