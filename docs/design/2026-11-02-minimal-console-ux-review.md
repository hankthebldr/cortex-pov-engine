# Minimal console — UX review (2026-11-02)

**Scope.** The v1.1.0 image (`main` 1ad8a88) running on `:8888` with one online
beacon (`pov-target-01`), one registered tenant (`deathstar`, last test OK),
193 corpus scenarios + 1 draft, 191 TTP cards. Walked at 1440×900 and
1280×720 in Chromium; every route below was opened, not inferred from code.
Compared against the Enclave console in `local-ai-platform` (1a117eb), which
is what "the detail we had" refers to.

**Not touched.** `tenant-verified` is still 0. Nothing in this review changes
or claims anything about tenant coverage.

---

## Executive summary

Nothing was removed from the product. The Composer, the TTP cards and the full
193-scenario library all ship and load in v1.1.0 — they moved. PR #135 turned
the 17-item rail into five tasks, and the three surfaces became tabs inside
those tasks: the Library is **Simulate › Scenarios**, the Composer is
**Simulate › Build a chain**, the cards are **Catalog › TTP cards**. What a DC
actually loses is findability and trust, not content: one surface carries
three different names, the Composer's own "Start from a library scenario"
button exits the Composer, only 24 of the 193 scenarios can be opened from
inside the Composer at all, the header says *no tenant bound* on the same
screen where Get started says *Connected to deathstar*, and the Runs page is a
wall of 86 UUIDs with no time, agent, duration or filter. Against Enclave the
gap is in **Runs** and in the Composer's **live-run feedback**; the Composer
*canvas* itself is richer than Enclave's. The run-detail tabs #135 deleted
(Topology, Techniques) were rendering prototype seed data, so "bring them
back" is the wrong ask — rebuild those two designs on real run records.
Recommended action: a short fix pass on the six defects marked **fix now**,
then a Runs rebuild to Enclave's standard.

---

## Where things are

| You are looking for | Rail | Tab | Route | On this install |
|---|---|---|---|---|
| The full library | Simulate | Scenarios | `#/library` | 193 scenarios, 16-plane filter rail, grid + inspector |
| The Composer | Simulate | Build a chain | `#/composer` (immersive) | Empty canvas + "three ways" dialog; `#/composer?from=SIM-EDR-001` loads a chain |
| TTP cards | Catalog | TTP cards | `#/ttps` | 191 cards, tactic/platform/status chips, Author new |
| Tool packs + shelf | Catalog | Packages | `#/adapters` | 91 packs, 8 pinned |
| UC / TC index | Catalog | UC / TC index | `#/uctc` | 266 TCs |
| Runs | Runs | — | `#/runs`, `?run=&tab=` | 86 runs; detail = Live · Evidence · Storyline · Causality |
| Report / coverage | Results | Report · Coverage | `#/proof`, `#/coverage` | — |
| Launch gate | Simulate | Launch gate | `#/preflight` (`#/readiness` aliases) | all ok |

Every pre-#135 route still resolves (`setup`, `scope`, `cli`, `validation`
redirect). ⌘K lists every page under "Go to", rail or not.

---

## Technical detail

### A. Findings, ranked

| # | Sev | Finding | Where | Action |
|---|---|---|---|---|
| 1 | **High** | **Composer can open 24 of 193 scenarios.** The palette's "Scenario library" group is `env.scenarios.slice(0, 24)` and the palette search filters *that slice*, so the other 169 are unreachable from inside the Composer. The only full path is the `?from=<id>` URL param, and nothing in the UI emits it: the Library inspector's actions are Continue to Launch · Pull · Push · bash · Close, and `ScenarioTable.jsx` (which has an "Open in Simulation Composer" shortcut) is not mounted anywhere. The "three ways" dialog's first two options call `onNavigate('library')` / `onNavigate('ttps')`, i.e. they leave the Composer with no way to come back carrying a selection. | `ComposerView.jsx:835` (slice), `:1319-1320` (three ways), `ScenarioInspector.jsx` (no CTA), `ScenarioTable.jsx:32` (unused) | **fix now** — drop the slice (search the full list), add "Edit in Composer" to the Library inspector, make the two "Start from…" options open an in-place picker instead of navigating |
| 2 | **High** | **Header contradicts Get started about the tenant.** Header pill: *no tenant bound*. Get started step 2: ✓ *Connected to deathstar*. Runs and Composer both print *TENANT none selected*. Root cause: the provider's stale-pointer guard only re-points a persisted id that no longer resolves; with no persisted id (fresh browser) and one registered tenant it never binds. Side effect: the blast-radius acknowledgement is keyed `__none__`. This is exactly the class of contradiction #135 was raised to remove. | `EnvironmentContext.jsx:277-288` | **fix now** — one-tenant-per-instance is a stated contract; bind the only tenant when the pointer is null |
| 3 | **High** | **Runs is a wall of UUIDs.** Each row is `scenario_id · uuid · status`. No `started_at`, agent, duration, detections count, filter, pager or grouping; 86 rows of `SIM-EDR-001 FAILED` are indistinguishable. This was never richer — `RunList` shipped this shape in 08a90b8 — so it is a gap, not a regression. | `app/destinations.jsx::RunList` | **rebuild** — see §B for the Enclave bar |
| 4 | Med | **One surface, three names.** Rail *Simulate* → h1 *Library* → tab *Scenarios*; rail *Catalog* → h1 *TTP Cards* → tab *TTP cards*; rail *Results* → route `proof` → tab *Report*. Copy then uses all of them: "launch a scenario from Simulate", "Browse all scenarios", "Library". A DC reading the rail cannot predict the page title. | `destinations.jsx` labels vs `OperationsView.jsx:258`, `TtpBrowserView` h1 | **fix now** — one rule: the h1 is the tab label, the rail is the task |
| 5 | Med | **Deep-link params disagree per surface.** Library `?open=`, Composer `?from=`, TTPs `?ttp=`, UC/TC `?uc=&tc=`, Runs `?run=&tab=`. `#/composer?open=SIM-EDR-001` silently renders the empty state. | registry + each surface | **fix now** — accept `open` everywhere (keep the old names as aliases) |
| 6 | Med | **Composer's run panes point away from the Composer.** *Active run* is one line with a *Follow ▸* that navigates to Runs; *History* is capped at 8 rows and also navigates away. Enclave tails per-step rows inline and auto-switches to its Active Run tab when a run starts. The DC launches from the canvas and then has to leave it to see what happened. | `ComposerView.jsx::ActivePane`, `::HistoryPane` | **rebuild** with 3 |
| 7 | Low | **Library refetches and prints "0 scenarios" while loading.** `OperationsView` fetches `/api/scenarios` itself although the provider already holds the list; the h1 meta reads *0 scenarios · Loading scenarios…* on every entry to Simulate. A zero rendered as a fact, which Gate A5 forbids. | `OperationsView.jsx:90` | **fix now** — read the provider's list; never print a count before it arrives |
| 8 | Low | **Health says 194, Library says 193.** `probe_scenario_catalog` counts `Scenario` rows including the one draft; `/api/scenarios` excludes drafts. The About page (fed by `/api/health`) and the Library disagree by the draft count. | `core/api/health.py:472` | **fix now** — report `corpus` and `drafts` separately |
| 9 | Low | **Get started never mentions the Composer.** Its only exits are *Set up this run* (guided), *Browse all scenarios* (Library), *Open Runs*. A DC who wants to build a chain finds it only via Simulate's second tab or ⌘K. | `GetStartedView.jsx` | add one line under step 5 |
| 10 | Low | At 800 px wide the rail label wraps ("Get / started 1"). DCs run laptops, so cosmetic. | `--w-rail` 202 px | defer |

### B. Composer and Runs vs Enclave

| Dimension | POVengine (v1.1.0) | Enclave (`local-ai-platform`) | Who is ahead |
|---|---|---|---|
| Canvas | CGO trigger node, step nodes with technique · identity · detection-type chips, Proof/teardown node, four lenses (Design · Lanes · Run · Stitch overlay), Chain/YAML toggle, PROVES bar (TC · UCS · plane · techniques), 5-step causality spine with pivot kinds | Node canvas + Step Config form, chat-led boot sequence | **POVengine** |
| Inspector | Step name, command, identity, technique, causality parent + pivot, platforms, channel (agent/EAL), target agent, stitch entities, expected detections with add/remove by plane and type, scenario teardown | Step Config: id, name, role/model, tools & skills | **POVengine** |
| Entry into the editor | Palette capped at 24 scenarios; "three ways" dialog exits the editor; no Library → Composer link | Chat capture → scaffold, palette, Workflows index → open | **Enclave** |
| Bottom workstream | Payload plan (real digests, 8 pinned) · Preflight · Active run (1 line, navigates away) · History (8, navigates away) | Step Config · Active Run (per-step id · model · duration · status, auto-switch on launch) · History · Logs (per-step input/output, expandable) · In Progress | **Enclave** on run feedback, POVengine on supply |
| Runs list | `scenario · uuid · status` | started-ago, progress, tokens, pager with total, filters | **Enclave** |
| Run detail | Live (SSE stdout) · Evidence · Storyline · Causality graph; stdout/stderr, exit code, attestations | Step roster (id · role · status · prompt), cascade, per-step I/O, timing breakdown (load · prompt-eval · generation), Stop · Re-run · Mark failed · Copy JSON | **Enclave** on operator actions and per-step drill-down; POVengine has the causality graph Enclave lacks |

The honest reading: the Composer canvas is the strongest surface in either
console. The Runs surface is where the two diverge, and it was thin before
#135.

### C. What PR #135 removed, and whether to bring it back

Deleted: `RunTopology`, `RunTechniques`, `PlaneCoverageMatrix`,
`WorkflowSwitcher`, `ObjectSheet`, `EvidenceCollection`, `LaunchGate` (canned
block), `TenantValidationView`, `ComponentsView`, `CliItemsView`,
`SetupWizardView`, `FlowBar`, `ComposeTabs`. Every one of them read the
`povdata/` seed catalogs (`RUN_LOG`, `SCENARIOS`, `WORKFLOWS`) — a DC could
screenshot "Acme Financial · 7 of 9 checks" as if it were the customer's
tenant. Removing them was right.

What was lost is the *design* of two of them, and both are worth rebuilding
on real records:

- **Techniques** — a kill-chain strip with empty stages drawn dimmed (a chain
  that proves execution and exfil but never persistence has a hole), plus a
  per-technique verdict table. Source of truth now exists: `Result` rows per
  step-detection, `tc_verdict`.
- **Topology** — lane bands derived from the run's steps, Engine pinned
  terminal. `composerLanes.js::laneOf` is already the one lane derivation the
  canvas and timeline share; a run topology can be re-derived from it.

### D. Recommended order

1. **Fix pass on this branch** — findings 2, 1, 4, 5, 7, 8. Each is a few
   lines plus a guard that fails without the fix (Gate A).
2. **Runs rebuild** — findings 3 and 6 together: a run row with started-at,
   agent, duration, `detected / expected`, status; filter by scenario and
   status; group repeats; the Composer's Active run pane tails step rows
   inline and auto-switches on launch. Then Techniques and Topology from §C
   on real data.
3. **Fold accepted items into `DESIGN-SYNC.md` § "What is NOT at parity
   yet"** so the design track stops rediscovering them.

### E. Method and one caveat

Routes walked: `start`, `library`, `composer`, `composer?from=SIM-EDR-001`,
`ttps`, `runs`, `runs?run=…`, `tenants`, at 1440×900 and 1280×720; page text
read, not just screenshots. API cross-checks: `/api/health`,
`/api/scenarios`, `/api/scenarios/drafts`, `/api/credentials/integrations`,
`/api/agents`, `/api/runs`. Enclave compared from its served console source
(`api/static/js/runs/runs-tab.js`, `workspace-legacy/composer-workstream.js`,
`index.html` nav) and its README screenshots.

Two runs were launched against `pov-target-01` during the walk (22:06:48Z
`SIM-EDR-001`, 22:07:46Z `SIM-EDR-008`). Neither came from this review:
SimCore's log shows both with `identity=None`, unlike the console's own 22:02
launch (`identity=www-data`), and the second landed while only read-only
shell commands were running here. Another session is driving that agent.
