# CortexSim — Detection-Engine Gap Analysis & Causality Backlog

> **As-of date:** 2026-07-10 · **Branch:** `ultracode/full-revamp` · **Baseline:**
> 88 loadable scenarios · 15 planes · 89 TTP cards · 753 catalog detections ·
> 550 wired step-detections · Detection Proof Layer shipped (`detection_storyline.py`,
> `efficacy_scorecard.py`, `storyline.py`, `DetectionStoryline.jsx`).
>
> This doc answers the standing question — **"what did we miss / where can we
> improve?"** — as a single tracked, severity-ranked backlog across four themes:
> **detection library** (DL), **test coverage** (TC), **operator UX** (UX), and
> **causality graph** (CG). It is the companion to
> [`GAP-ANALYSIS.md`](GAP-ANALYSIS.md) (the 2026-06 seven-theme backlog, mostly
> CLOSED); this doc is the *next* backlog — the one the Causality-Graph capability
> and the SIM-MP-007 dual-control scenario are being built against.
>
> **Severity legend:** `high` = under-sells a headline Cortex differentiator, ships
> a false-green test, or hides launchable content from the operator · `medium` =
> correctness/consistency gap with a workaround or a shallow-but-present surface ·
> `low` = polish, drift, or documented-but-incomplete.
>
> **Effort legend:** `S` = < ½ day · `M` = ½–2 days · `L` = > 2 days / multi-file
> or multi-scenario build.
>
> **Tally:** 30 gaps — **13 high · 12 medium · 5 low.** Every `file:line` traces to
> the code that evidences the gap; the code wins when it and this doc disagree.

---

## How the four themes interlock

The threads are not independent — they converge on one argument:

```
                    the seeded-Result denominator
                    (orchestrator: 1 Result / expected detection / step)
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
   detection_storyline   efficacy_scorecard   causality_graph   ← 3 pure projections
   (timeline)            (scorecard)          (NEW — this backlog)
          │                   │                   │
          └─── delegate coverage/MTTD to build_summary (never diverge) ───┘
                              │
   DL fills the denominator ──┤── TC proves each projection is real, not stubbed
   UX routes the DC to it  ───┘── CG turns the flat checklist into a linked graph
```

- **DL (detection library)** decides *what signal exists to prove* — today it is
  83 % hand-authored XQL+BIOC, so the corpus proves "we can write queries," not
  "our ML fires on behavior a competitor misses."
- **CG (causality)** decides *whether the proof is a graph or a checklist* — the
  scenarios already declare the stitch contract (`stitching_key`,
  `correlation_window_seconds`, `required_planes_in_incident`) that nothing parses.
- **TC (test coverage)** decides *whether the proof is real* — the Storyline API
  happy-path is currently false-green (every test stubs the builder).
- **UX** decides *whether the DC ever reaches the proof* — the hero surface is
  buried in `More▾` and 4 of 15 planes are unreachable by filter.

---

## Severity-ranked backlog

### HIGH

| ID | Gap | Area | Evidence (`file:line`) | Impact | Recommended fix | Effort |
|----|-----|------|------------------------|--------|-----------------|--------|
| CG-01 | No graph substrate — detections stored/rendered as independent nodes, zero edges | causality | `core/engine/detection_storyline.py:303-357` (by_step grouping + flat `entries[]`); `_build_entry:168-196` (no from/to/edge field) | The headline claim (XSIAM stitches cross-plane signal into one chain) is asserted in prose, never represented as data. Every other CG gap is blocked on this. | Add pure `build_causality_graph` emitting `nodes[]+edges[]` beside `entries[]`; typed `Edge`/`Node` models; delegate coverage/MTTD to `build_summary` | M |
| CG-02 | Endpoint process-lineage edge absent — parent/child never linked | causality | `scenarios/multi_plane/mp-001-*.yml:99-104,129-133`; `core/models.py` Result (no process GUID/parent col) | The strongest XDR narrative (`www-data → bash → curl → beacon`) can't be drawn though the identity harness makes it deterministic. | Derive `process_lineage` edges from identity-harness resolution + command head-binary; persist `actor_process`/`parent_process` on Result | M |
| CG-03 | Network 5-tuple/session edge declared in scenarios but materialized nowhere | causality | `scenarios/multi_plane/mp-006-*.yml:69-73,105-158` (stitch key + `verification_xql` 5-tuple join); `core/models.py` (no 5-tuple cols) | mp-006's whole success criterion (NGFW session ↔ container process on shared 5-tuple within 10s) degrades to two independently-green rows. | Persist observed 5-tuple/`container_id` on matched Results; extend matcher with a tuple key + tighter per-scenario window; emit `network_session` edges | M |
| DL-01 | ABIOC — the flagship XSIAM behavioral-ML differentiator — effectively unrepresented | detection-library | `scenarios/cdr/cdr-009-*.yml:93,109`; `cdr-014-*.yml:101,116`; cards `TTP-2026-0080`,`-0087` — 4/550 step-detections (0.7 %), all CDR | A corpus that is 83 % XQL+BIOC proves "we write queries," not "our ML catches behavior rules miss." A prospect asking for ML detection has 2 CDR demos and nothing on endpoint/identity. | Author 6–10 ABIOC-anchored scenarios across EDR/ITDR/NDR with `abiocs[]`+`modeling_rules[]` cards; seed the first endpoint ABIOC inside SIM-MP-007. Target ABIOC+Analytics ≥15 % of step-detections **[PARTIAL 2026-07-23 two-track: ABIOC step-share 0.7 %→8.4 % (catalog abioc 4→66), now across EDR/ITDR/ANALYTICS (`SIM-EDR-015/016/017`, `SIM-ITDR-013`, `SIM-MP-013/014/015`, `SIM-EDR-018`); ABIOC+Analytics 4.2 %→11.2 % vs 15 % target]** | L |
| DL-02 | No staged-vuln → exploit → post-exploit chains consume the planted IaC weaknesses (F9 = 0 exemplars) | detection-library | `scenarios/asm/asm-004-*.yml:18-21` (titled "Exploitation" but tagged TA0043 recon); `cspm-001`; `methodology_family F9` used by 0/88 | The code-to-cloud narrative Cortex Cloud is sold on ("attacker found this misconfig, exploited it, Cortex stitched discovery→impact") has no end-to-end exemplar. | Build SIM-MP-007 consuming the ASM exposed-Redis/host finding TA0043→TA0001→TA0002→TA0040, `methodology_family: F9`, KPI Causality Chain Completeness | M |
| TC-01 | Storyline API happy-path is false-green — every test stubs the real builder | test-coverage | `tests/api/test_storyline_api.py:81-115` (`stub_storyline_engine`),`:118`,`:137`; real builder at `core/engine/detection_storyline.py:263` never invoked via `core/api/storyline.py:184-191` | A key rename/shape drift in `Result.to_dict()`/`Run.to_dict()`/`Scenario.steps` ships green while the live endpoint returns a wrong/empty storyline. Highest-risk gap in the new Proof Layer. | Add one **non-stubbed** integration test hitting `GET /api/runs/{id}/storyline` against `seeded_run`, asserting `entries[]`/`coverage_pct` from the real builder | S |
| TC-02 | Efficacy scorecard report branch (`?format=scorecard`/`scorecard-html`) has zero tests | test-coverage | `core/api/runs.py:268-279` (renders both formats); grep `format=scorecard` in `tests/` → nothing; `tests/api/test_runs_api.py:126` only checks pdf→422 | The scorecard's only HTTP wiring is untested — content-type split (text/plain vs text/html), title interpolation, multi-run denominator all regress silently; the passing pure builder gives false safety. | Add report tests for both formats over `seeded_run`: assert 200, correct Content-Type, coverage numbers matching seeded results | S |
| UX-01 | The hero "wow" surface (DetectionStoryline) is orphaned in `More▾`, never reached in a real POV | ux | `AppShell.jsx:42-47` (in `MORE_ITEMS`); `AppConsole.jsx:419-424` (`handleRunComplete → 'inflight'`); 0 storyline refs in `InflightView`/`EvidenceView`/`HelpOverlay` | The engine-attributed live kill-chain — the single biggest differentiator — requires the presenter to know it exists and dig for it mid-meeting. | Promote to a first-class **"Prove"** step; add run-complete + Live/Evidence CTAs routing to the causality/storyline view; add to help overlay | M |
| UX-02 | Executive efficacy scorecard (the CISO one-pager) has no UI affordance | ux | `core/api/runs.py:239,268-279` (renders `scorecard`/`scorecard-html`); `ui/src/components/console/ExportMenu.jsx:97-121` (only bundle/md/matrix/navigator) | The most CISO-facing walk-out artifact of the whole Proof Layer is reachable only by hand-crafting a URL. | Add "Executive scorecard (.md/.html)" to ExportMenu + a client wrapper for `report?format=scorecard[-html]`; add a "Generate exec scorecard" CTA on the Evidence/Causality header | S |
| UX-03 | Plane rail lists 11 planes but the library ships 15 — CSPM/ASM/TIM/EMAIL and ~11 scenarios unreachable by filter | ux | `AppConsole.jsx:61-73` (`PLANE_META` = 11); stale comment `:57-60` claims parity; missing ASM(4)/EMAIL(4)/TIM(2)/CSPM(1) | ~11 active scenarios are invisible through the primary navigation affordance; a CSPM/ASM/TIM/EMAIL-scoped POV can't filter to its content. | Derive the rail from the live scenario list's distinct planes so it can never drift again (add CSPM/ASM/TIM/EMAIL labels) | S |
| UX-04 | Agent enrollment uses the legacy self-asserted `--id` installer, not the token front door | ux | `TargetsView.jsx:62-64,283-292` (hand-typed Agent ID + `install?id=…`); 0 `enroll` refs in `ui/src`; backend `POST /api/agents/enroll/tokens` never called | CLAUDE.md declares the enrollment-token flow "the front door"; the UI presents the deprecated, insecure path as blessed and gives the DC no way to mint/revoke enrollment credentials. | Add "Mint enrollment token" (TTL/max-uses/revoke) to the Deploy-agent modal; generate the token-based one-liner; demote raw `--id` to advanced fallback | M |

### MEDIUM

| ID | Gap | Area | Evidence (`file:line`) | Impact | Recommended fix | Effort |
|----|-----|------|------------------------|--------|-----------------|--------|
| DL-03 | Cross-source correlation — XSIAM's headline capability — exercised by <10 % of the corpus | detection-library | Correlation 33/550 (6.0 %) + Analytics 3.5 %; 65/88 are `F1` single-signal vs 9 `F2`; stitching lives in `scenarios/multi_plane/mp-001..006` only | A POV whose story is "XSIAM collapses 6 alerts into 1 incident" rests on 6–9 scenarios; the bulk validates atomic single-source detections and under-sells the correlation engine. | Add correlation-terminal steps (assert one `incident_id` spans N planes, as mp-005 does) to high-value EDR/ITDR/CDR scenarios; lift F2 toward ~25 % **[PARTIAL 2026-07-23 two-track: correlation step-share 6.0 %→9.2 % (target 10 %), ABIOC+Analytics 4.2 %→11.2 %; F2 9→14 via `SIM-MP-011/014` + correlation-terminals in `SIM-MP-011..015`, `SIM-CSPM-003/004`, `SIM-EDR-016`, `SIM-EDR-018`]** | L |
| DL-04 | MITRE tactic depth lopsided — Initial Access 20× the depth of Execution | detection-library | Primary-tactic: Initial Access 20, Cred Access 12 … Execution TA0002=2, Resource Dev TA0042=1, Persistence 3, PrivEsc 3, Lateral Movement 3 | Coverage is "full width, shallow middle" — a POV scoped on Execution/PrivEsc/Lateral Movement has 2–3 scenarios, thin enough that one env quirk sinks the demo. | Backfill Execution (T1059 variants, T1204), PrivEsc (T1548, T1068), Lateral Movement (T1021, T1570) on EDR/ITDR where the identity harness already gives causality **[PARTIAL 2026-07-23 two-track: Lateral Movement T1570+T1021.002 (`SIM-EDR-015` chisel/PsExec), Execution T1204.004+T1059.001/003 (`SIM-EDR-016/017` ClickFix), PrivEsc T1548.005/T1098 (`SIM-CSPM-003`, `SIM-ITDR-013` BadSuccessor dMSA)]** | M |
| DL-05 | Single-scenario planes can't sustain a plane-focused POV | detection-library | `scenarios/cspm/` = 1 file (`cspm-001`); TIM = 2; ASM/EMAIL = 4 each | A CSPM- or TIM-centric POV has one demo; if the customer env doesn't reproduce that finding shape there's no fallback. Reads as checkbox coverage. | Set a per-plane floor of 3: CSPM (compliance-drift, IAM exposure, encryption/logging gaps), TIM (STIX lifecycle, feed-to-detection stitch, FP suppression) **[CLOSED 2026-07-23 two-track: CSPM 1→4 via `SIM-CSPM-003` (IAM PassRole posture→runtime) + `SIM-CSPM-004` (unencrypted-snapshot export); TIM=3 — every plane now ≥ floor 3]** | M |
| CG-04 | Temporal-window edge — `correlation_window_seconds` is an unused YAML field | causality | `core/connectors/matcher.py:30` (`DEFAULT_WINDOW_SECONDS=3600`, unrelated); scenario window `mp-006:69`,`mp-001:73` read by nothing; storyline computes only per-entry MTTD `:191` | Temporal proximity — a core stitching primitive — is never computed *between* detections; two catches 4s apart inside a 10s window are shown with independent MTTDs. | After matching, compute pairwise `observed_at` deltas; emit `temporal` edges where delta ≤ `scenario.correlation_window_seconds` | S |
| CG-05 | Shared-entity edge — no entity extraction across planes (host/container_id/account) | causality | `core/connectors/base.py:41-70` (ObservedAlert has `host`, no container_id/account); Result has no entity cols; `matcher._correlation_keys:85-111` has no entity dimension | Identity-driven correlation (e.g. SIM-MP-002 Kerberoast identity reused on an endpoint) has no representation; two detections on the same host in different planes can't be joined. | Extract entities (host/container_id/account/ip) onto Result + ObservedAlert; emit `shared_entity` edges on any shared value | M |
| CG-06 | Exposure→exploit edge — ASM/CSPM findings never linked to the exploit that used them | causality | `core/engine/detection_storyline.py:263-357` (scoped to one run's results + one scenario's steps; no cross-run join) | The most business-relevant narrative (attack surface → breach) can't be assembled; asm-001 exposed Redis then an EDR exploit produce two isolated storylines with no linking edge. | v1: intra-scenario `exposure_exploit` edge in SIM-MP-007 on shared `asset_ref`; later: campaign-level Edge table spanning runs (see deferred roadmap) | M |
| CG-07 | No `incident_id` grouping — the matcher can't express "these alerts are one XSIAM incident" | causality | `core/connectors/base.py:41-70` (no `incident_id`); `matcher.py:131-167` (per-result loop, no grouping); `mp-006:158` asserts `incident_count=1` but nothing ingests it | The single most direct proof of stitching is unrepresentable — the connector can pull 3 alerts XSIAM already stitched under incident 4711 and never record they share it. | Add `incident_id` to ObservedAlert + a `group_by_incident` pass; emit `same_incident` edges between co-grouped Results | M |
| TC-03 | Credential-backed reconcile HTTP endpoint happy path untested, no injection seam | test-coverage | `tests/api/test_connectors_api.py:90-101` (only 404/400); `core/api/connectors.py:135-161` calls `reconcile_run` without threading the `fetcher` param `core/connectors/service.py:52` accepts | A regression in the 200 pull→match→persist path through the HTTP layer isn't caught; the endpoint can't be happy-path tested without a live tenant. | Thread a fetcher/connector override into the endpoint (or a `get_connector` dependency override) + a 200 test asserting `observed_at`/MTTD persist | M |
| TC-04 | SSE emission from the measurement loop unasserted on the backend | test-coverage | `core/connectors/service.py:131-138` (publishes `result.observed`); `storyline.py:42-49` documents the frame→mutation contract; `test_connectors_api.py` asserts only body summary | The live-presenter contract can silently break while every reconcile test (JSON-summary only) stays green. | In the manual-ingest test, subscribe to `event_bus` for the run and assert a `result.observed` frame with expected `result_id`/`observed_at`/`mttd` per match **[CLOSED 2026-07-23 two-track: `tests/api/test_connectors_api.py::test_manual_ingest_publishes_result_observed_sse` — subscribes to `event_bus`, drives real `ingest_observations`, asserts the `result.observed` frame; mutation-verified (no-op'ing the publish fails the test)]** | S |
| TC-05 | No live SSE-fold test for the DetectionStoryline UI (only the fetch snapshot is tested) | test-coverage | `ui/src/components/__tests__/DetectionStoryline.test.jsx:3` ("jsdom has no EventSource … skipped"); frame→mutation map `storyline.js`/`storyline.py:42-49` untested | The incremental live-update path — the core value of the presenter timeline — is entirely untested; a broken frame handler that fails to flip pending→detected ships green. | Mock a global `EventSource`, dispatch a `result.observed` frame, assert a row transitions Awaiting→Detected with MTTD/engine badge | S |
| UX-05 | No forward hand-off through the prove→export tail of the journey | ux | `AppConsole.jsx:419-424` (`→'inflight'` only); `InflightView` footer has no "Go to Evidence/Storyline"; `ConsoleStepper.jsx` has no Next control | The guided narrative that makes the front half convincing evaporates exactly where the "here's the proof, here's your report" climax belongs. | Add explicit forward CTAs Live→"See detection proof ▸"→Evidence→"Export briefing ▸"; a persistent "Next step" in the stepper | M |
| UX-06 | Fabricated sensor-health status shown to customers | ux | `AppConsole.jsx:121` (`sensors:{xdr:'healthy',cdr:'healthy',ndr:'healthy'}`, comment admits placeholders) | A fabricated green light in a customer-facing tool — misleading, and undercuts credibility if the tenant is actually degraded. | Wire the pill to the Phase 9 read-only `/healthcheck` source, or render it muted/"unknown" until real data exists | S |
| UX-07 | Storyline "Evidence" affordance drops the detection context | ux | `DetectionStoryline.jsx:186-194` (`onOpenEvidence(det)` passes the detection); `AppConsole.jsx:535-539` (`onOpenEvidence={() => setActiveTab('evidence')}` — arg discarded) | The chain-of-custody "wow" is half-delivered: click evidence on one catch, land on the unfiltered scorecard, re-find the row by hand. | Pass the detection through and deep-link: open EvidenceView with that row pre-selected and its DetectionDrawer open (`EvidenceView.jsx:152`) | S |
| UX-08 | "Export POV" means three different artifacts under one label | ux | `InflightView.jsx:66-87` (→.md); `AppConsole.jsx:250-273` (→.tar.gz); `ExportMenu.jsx:74` (→.tar.gz) | Same verb, three payloads; under demo pressure a DC hands the customer the wrong (thinner) artifact. | Reserve "Export POV briefing" for the full bundle everywhere; relabel the Live single-file action "Export narrative (.md)" | S |

### LOW

| ID | Gap | Area | Evidence (`file:line`) | Impact | Recommended fix | Effort |
|----|-----|------|------------------------|--------|-----------------|--------|
| DL-06 | Half the declared validation-methodology families have zero scenarios | detection-library | `scenarios/_schema.yml` enum F1..F10; usage F1=65,F2=9,F3=7,F4=6,F8=1; F5/F6/F7/F9/F10 = 0 | The v2.0 methodology master promises 10 families, delivers 5; KPI columns like MTTR (F6) and AI-triage (F7) render empty in a generated POV report. | Seed ≥1 scenario per unused family, prioritizing F6 (MTTR/response-timing against the measurement loop) and F7 (AI-triage-summarization) **[PARTIAL 2026-07-23 two-track: F6 seeded (1 — `SIM-MP-012` EDR-blinding time-to-contain), F7 seeded (3 — `SIM-MP-013` rogue-VM grouping, `SIM-MP-015` triage-under-decoy, `SIM-EDR-018` summarization); F9 now 1. Only F5 (Automation/Workflow) + F10 (Qualitative Evidence) remain empty]** | M |
| TC-06 | Three consumers each re-implement detected/missed/pending with no parity test | test-coverage | `report_generator.py:80`, `efficacy_scorecard.py:190`, `detection_storyline.py:135` — three impls, each unit-tested in isolation only | Documented to "never disagree" but unenforced; a fix to one (e.g. `observed_at`-without-`observed`) can silently diverge scorecard from report from storyline. | One parametrized parity test feeding a shared row-set (detected/reviewed-unfired/pending) through all three, asserting equal counts | S |
| TC-07 | Multi-plane / ANALYTICS correlation scenarios reconcile path untested | test-coverage | tests reference `multi_plane` only via catalog/loader validation; `matcher.py` correlates each Result independently, no cross-plane crediting guard | SIM-MP-001..006 are validated only as loadable YAML; cross-plane crediting has no regression guard. | Add a reconcile test seeding a `plane: ANALYTICS` run's multi-signal Results, assert coverage credited across the stitched detections | M |
| UX-09 | Empty-state copy points to a tab that no longer exists | ux | `EvidenceView.jsx:69`, `InflightView.jsx:144` say "Launch a scenario from the Operations tab"; stepper labels it "Library"+"Launch" | The first instruction a first-time DC reads is wrong. | Update copy to "Arm a scenario in Library, then fire it from the Launch step." | S |
| UX-10 | Duplicate keyboard shortcut in the command palette | ux | `AppConsole.jsx:294` (`G T` → Targets) and `:357` (`G T` → Tenants) | The displayed chord is ambiguous/non-functional for one of them. | Reassign one (e.g. Tenants → `G N` or `G H`) | S |

---

## Now / Next / Later roadmap

Ordered for the Causality-Graph capability build. **Now** is the current build
slice; **Next** is the fast follow that makes it defensible and reachable;
**Later** is corpus-policy and campaign-scale work that outlives this branch.

### NOW — the causality-graph first slice + the false-greens it depends on

The graph is only trustworthy if the projection it delegates to is tested, and
only valuable if the DC reaches it. Ship these together:

- **CG-01** — pure `build_causality_graph` (nodes[]+edges[], delegates coverage/MTTD
  to `build_summary`) + typed Node/Edge models. *Unblocks all other CG work.*
- **CG-02 / CG-03** — the two hero edges: `process_lineage` (identity-harness +
  head-binary, always-offline EXPECTED) and `network_session` 5-tuple (upgraded to
  CONFIRMED/BROKEN by matcher verdicts).
- **DL-02** — **SIM-MP-007** staged-exposure→runtime-exploit→impact, the F9
  exemplar that gives the `exposure_exploit` edge (CG-06) something to draw and
  seeds the first endpoint ABIOC (down-payment on DL-01).
- **TC-01 / TC-02** — kill the two false-greens: the non-stubbed storyline
  integration test and the scorecard report-branch tests. *Cheap, S-effort, and
  they guard the exact projection the graph reuses.*
- **UX-01 / UX-02 / UX-03** — make the proof reachable: promote the graph/storyline
  to a first-class **"Prove"** step, surface the executive scorecard in ExportMenu,
  and derive the plane rail from the live scenario list (fixes the 11-vs-15 drift).

### NEXT — evidence-backed edges, richer stitching, and the back-half rails

- **CG-04 / CG-05 / CG-07** — the remaining spec-derived edges: `temporal`
  (pairwise `observed_at` ≤ scenario window), `shared_entity` (host/container/
  account extraction), and `same_incident` (add `incident_id` to ObservedAlert +
  `group_by_incident`). These turn EXPECTED edges into evidence-backed CONFIRMED.
- **TC-03 / TC-04 / TC-05** — close the live-presenter coverage gap top-to-bottom:
  reconcile-endpoint injection seam + 200 test, backend `result.observed` SSE
  assertion, and the UI EventSource live-fold test.
- **UX-04 / UX-05 / UX-06 / UX-07 / UX-08** — rail the back half: enrollment-token
  front door, forward hand-off CTAs, real (or muted) sensor-health, Storyline→
  Evidence deep-link, and one unambiguous "Export POV briefing" label.
- **DL-04** — backfill the shallow ATT&CK middle (Execution / PrivEsc / Lateral
  Movement) where the identity harness already yields realistic causality.

### LATER — corpus policy and campaign-scale causality

- **DL-01 (full)** — 6–10 ABIOC scenarios across EDR/ITDR/NDR beyond SIM-MP-007's
  endpoint ABIOC, pushing ABIOC+Analytics ≥15 % of step-detections. *The single
  highest-leverage detection-library investment; L-effort, hence Later.*
- **DL-03** — lift cross-source correlation (F2) toward ~25 % by adding
  incident-stitch terminal steps to existing high-value EDR/ITDR/CDR scenarios.
- **DL-05 / DL-06** — enforce the per-plane floor of 3 (rescue CSPM=1, TIM=2) and
  seed one scenario per unused methodology family (esp. F6 MTTR, F7 AI-triage) so
  every KPI a POV report can render has a backing demo.
- **CG-06 (campaign)** — an Edge table for cross-run graphs so `exposure_exploit`
  can span a posture run and a later exploit run on the same asset; event nodes
  (file/network/registry) derived from the step command; cross-identity CGO
  subtrees when a step's `su`/`runuser` switches service account.
- **TC-06 / TC-07 / UX-09 / UX-10** — the polish tail: cross-consumer status-parity
  test, ANALYTICS reconcile guard, empty-state copy, duplicate keybinding.

---

## Policy going forward (corpus targets)

These are the standing bars this backlog sets, to be enforced at scenario-authoring
time and checked in the detection-corpus CI lane:

| Metric | Current baseline | Target |
|--------|------------------|--------|
| ABIOC + Analytics share of step-detections | 4.2 % (23/550) | **≥ 15 %** |
| Cross-source correlation (F2) share of corpus | ~10 % (9/88) | **~25 %** |
| Scenarios per plane (floor) | CSPM=1, TIM=2 | **≥ 3 every plane** |
| Shallow tactics (Execution/PrivEsc/Lateral Movement) | 2 / 3 / 3 | **≥ 5 each** |
| Methodology families with ≥1 exemplar | 5/10 (F1-F4,F8) | **10/10** |
| Causality edges materialized | 0 | **7 typed kinds** (process_lineage · network_session · endpoint↔network stitch · temporal · shared_entity · sequence · exposure_exploit) |

> **Invariant that governs the whole capability:** the causality graph is a *third
> projection of the seeded-Result denominator*, not a new source of counts. It
> delegates coverage/MTTD to `detection_storyline.build_summary` so the timeline,
> the scorecard, and the graph can never report divergent coverage for the same
> run — the recurring failure mode TC-06 exists to guard.
