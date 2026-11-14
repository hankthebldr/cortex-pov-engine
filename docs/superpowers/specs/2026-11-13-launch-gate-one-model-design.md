# Launch gate, Get started and the POVengine page: one model, one gate

| | |
|---|---|
| Date | 2026-11-13 |
| Status | Proposed. Decisions D-1 to D-6 (§8) are Henry's. |
| Base | `dev` 48f3dfcd |
| Relates to | v26 handoff (`docs/design/2026-11-13-v26-design-to-code.md` §4.2–4.5, §11), #217, #227 (Task 27), SEC-01 |

## Executive summary

The console asks "is this POV ready to run, and will the result be believable?" in
five places: Get started, the Components hub, the POVengine page, the Launch gate, and
a preflight card buried in the hidden "New POV run" flow. Each place computes its own
answer from its own copy of the facts, and on one instance on 2026-11-13 they gave six
different verdicts. A Domain Consultant cannot tell which one to trust, and the one
that actually decides (the server, when Run is pressed) is the one the console can
predict least: of about twenty reasons the server can refuse a launch, the console
foresees six. The fix is not another page. It is one readiness model owned by the
server, one launch gate whose verdict is the same function the server enforces, and
the existing pages reduced to views of that model. Recommended: merge Get started and
"New POV run" into the Launch gate, keep Components as the place where things get
fixed, and build it in four slices, the server first.

## 1. What is on screen today

Observed on SimCore 1.1.1 with the `dev` console (48f3dfcd), one instance, one moment:

| Surface | Verdict it showed | Computed by |
|---|---|---|
| Header instance status | Degraded · 1/25 agents online · 1 issue | `instanceStatus.js` over `/api/health` |
| Get started | "This instance is ready", 3 of 3 required steps done | `setupProgress.js` |
| Components rail badge | 2 issues | `componentModel.js` |
| POVengine component page | Degraded, "Not ok: api_auth"; its Test always fails | `componentModel.js`, `PovengineComponent.jsx` |
| Launch gate | Degraded, "API authentication (none yet — expected)"; no scenario, no Launch button | `readiness/healthModel.js`, `connectorState.js` |
| New POV run → Preflight | Unknown (data path), plus a consent blocker drawn outside the checklist | `readiness/scenarioPreflight.js` |

The same fact reads differently on different pages:

| Fact | Get started | Components | Launch gate | Run preflight |
|---|---|---|---|---|
| Agents (1 of 25 online) | ✓ done (any agent online) | Degraded "Agents offline" (all must be online) | "Enrolled beacons OK 25" | Judges the selected agent only |
| Tenant | ✓ once any key is stored, even after its test failed | Worst `last_verified_ok`; `xsiam_tenant` only | Any `xsiam` or `xsiam_tenant` that answered | Own one-shot fetch, never refreshed |
| HTTP collector | Your browser tick only | Target's last check, tick as fallback | `eal_collectors` health row | Data-path advisory |
| Broker VM | Tick `bvm` | Tick `bvm`, or all six Configure ticks | — | — |
| API authentication | not shown | Degraded, forever | Degraded, "expected" | — |

`api_auth` is degraded on every build by design (`core/api/health.py`, `API_UNAUTHENTICATED`,
until SEC-01). So the header, the POVengine page and the Components badge can never be
clean, and an indicator that is always amber stops being read.

## 2. Why it diverged

Three design passes each added an "am I ready" page and none replaced the one before
(history: PR #82, #128, #135, #252, #253, #260):

- **August, Readiness → Launch gate.** A connector-honesty page: the AUTHORED ·
  CONFIGURED · REACHABLE · VERIFIED ladder. In September it also carried the per-chain
  gate. PR #135 removed that block as seed data, and the real per-scenario preflight
  moved into the launch flow, leaving a page called "Launch gate" that gates nothing.
- **October, Get started.** A checklist for a fresh install, with its own step model
  and browser-local "I've set this up" ticks.
- **November, Components and the POVengine page (v26).** A per-component health hub.
  v26 said to fold Get started in "rather than duplicate" (§4.3) and kept the Launch
  gate "unchanged in purpose" (§4.4). Only the Configure links landed (#260).

v26 §4.4 also says a Scenario card's **Run** goes to "Launch gate with the scenario
loaded". The design already treats the gate as the page you launch from. The code does
not: Run goes to the hidden `guided` flow.

## 3. What the server already decides

There is no server object that is "POV readiness". Readiness is computed per endpoint:

| Scope | Server source today | Outbound |
|---|---|---|
| Instance | `GET /api/health` (17 components, `not_checked[]`) | no |
| Tenant | `GET /api/readiness` (rungs per integration; the console ignores its `rungs` and recomputes them), `POST /api/connectors/{kind}/preflight` | preflight only |
| Collectors, firewall | `GET /api/collector-targets` (last check), `GET /api/ngfw` | no |
| Ingestion doors | `GET /api/doors`, `GET /api/doors/readiness`, `GET /api/scenarios/{id}/doors` | no |
| Scenario × target | `bundle_targets`, `GET /api/shelf/resolve/{id}`, `GET /api/scenarios/{id}/prerequisites`, `GET /api/agents/{id}/preflight` | no |
| Launch | `POST /api/runs` → `orchestrator.launch()` | no |

`POST /api/runs` can refuse for about twenty reasons, in a fixed order. Only the two
Composer-draft checks have a read-only mirror (`GET /api/scenarios/drafts/{id}/launchable`),
which nothing in the console calls and which reports `DRAFT_NOT_TC_BOUND` even when the
real refusal is `DRAFT_CHAIN_INVALID`. Six refusals happen after the Run row is
committed (`_refuse_after_seed`), so a refused launch leaves a failed run in history.
Three refusals carry no code at all (push-mode cluster consent, `STITCH_CONTEXT_INVALID`
in prose, pull with no target). Nothing read-only predicts `CONSENT_REQUIRED`,
`ADAPTER_MISSING_CLEANUP`, `AGENT_POSTURE_INSUFFICIENT`, `AGENT_TOOL_ABSENT`, pair or canary.

On the client, the launch decision is made in six places with different rules. The
guided flow applies the preflight, consent and payload checks; Library Quick launch and
⌘L apply only `useLaunchScenario`'s four blockers; the TTP browser's launch-all applies
none. The server still refuses what the client missed, so this is a 409 after the
click rather than a safety gap, but it is the "dead stop mid-demo" the hook's own
comment warns about.

## 4. The design: one model, one gate, views of it

```
                  ┌────────────────────────────────────────────┐
 server           │  readiness model  (core/engine/readiness.py)│
                  │  instance · lab · run   →  checks[]          │
                  │        run scope = launch dry run  ─────────┼──► POST /api/runs
                  └───────────────┬────────────────────────────┘    (same functions)
                                  │ GET /api/readiness/checks
 console                          ▼
          useReadinessModel()  ── header word · rail badges
                 │
     ┌───────────┼────────────────────┐
     ▼           ▼                    ▼
 Launch gate   Components ▸        POVengine page
 (all scopes,  (instance + lab,    (instance scope;
  + Launch)     grouped by          Setup + API tabs
                component; Fix)     unchanged)
```

### 4.1 One model, owned by the server

A new pure module, `core/engine/readiness.py`, composes the existing server functions
into one list of checks. It adds no new probe and makes no outbound call; checks that
need one (tenant preflight, collector preflight) report the last stored result and its
age, exactly as health and `/api/readiness` do today.

```
GET /api/readiness/checks[?scenario_id=&agent_id=&mode=pull|push]

{ "model_version": 1, "generated_at": "…",
  "verdict": "ready" | "blocked" | "unknown",
  "counts": { "pass": 14, "warn": 0, "block": 0, "unknown": 1, "off": 3, "standing": 1 },
  "checks": [ {
      "id": "run.target.enrolled",
      "scope": "instance" | "lab" | "run",
      "component": "pov|agents|tenant|http|bvm|firewall|xdrc|engine" | null,
      "title": "Target agent has polled",
      "verdict": "pass|warn|block|unknown|off|standing",
      "evidence": "measured|derived|attested|not_checked",
      "code": "TARGET_AGENT_NOT_ENROLLED",
      "detail": "…", "checked_at": "…",
      "blocks_launch": true,
      "fix": { "destination": "component-agents", "params": { "tab": "setup", "step": "online" } }
  } ],
  "not_checked": [ … same six entries health publishes … ] }
```

| Scope | Checks (built from) |
|---|---|
| `instance` | Each `/api/health` component that matters to a DC (db, catalogs, shelf, beacon binaries, task queue, master key); `api_auth` as `standing` (D-2) |
| `lab` | Target agent(s) liveness (D-3); one row per tenant credential carrying its rungs; one per collector target from its last check; firewall; one per ingestion door; Broker VM, XDR Collector, Cortex Engine as `attested` or `not_checked` (D-4) |
| `run` | Everything `POST /api/runs` would refuse for this scenario × target × mode (§4.2), then the advisory checks the preflight card shows today: identity, measurement path, data path, platform variant, prevention context |

Rules the model enforces, so no view has to:

- `unknown` is not ready, and never renders green.
- `attested` reads "you confirmed" and never counts as `pass`.
- `standing` is shown once, explained, and excluded from counts and the overall verdict.
- `blocks_launch` is set by the server. The console never decides whether a check blocks.
- Component status for Components and the rail is the worst verdict among that
  component's checks: `block`→Down, `warn`→Degraded, `unknown`→Unknown (Can't check),
  `off`→Off, all `pass`→Up, attested only→Unknown "You confirmed".

### 4.2 One gate: the dry run is the launch

The run scope comes from a dry run of the launch itself: `POST /api/runs/preflight`
with the same body as `POST /api/runs`, returning every refusal the launch would make
(all of them, not the first) as checks, writing nothing.

1. Extract each pre-seed check in `orchestrator.launch()` into a pure function
   (consent, adapter cleanup, cluster consent, pair, canary, pull planning, target
   satisfiability, shelf `compose` with `consumer=beacon`, `agent_launch_gate.evaluate_launch`,
   stitch context). `launch()` calls them in order and stops at the first refusal; the
   dry run calls all of them. One implementation, two callers, so they cannot drift.
2. Move the six post-seed refusals before the Run row is written wherever they do not
   need the row, so a refused launch no longer leaves a failed run.
3. Give the three codeless refusals codes (`CLUSTER_CONSENT_REQUIRED`,
   `STITCH_CONTEXT_INVALID`, `PULL_TARGET_MISSING`).
4. Fold the draft mirror into the dry run and fix its code (`DRAFT_CHAIN_INVALID` when
   the chain is invalid). Retire `getDraftLaunchable` and `/launchable` once nothing
   reads them.

Behaviour test that makes "the gate shown is the gate enforced" true: for each refusal
code, a fixture where the dry run reports it and `POST /api/runs` returns it. Every
launch button in the console (Scenarios Run, Library Quick launch and ⌘L, Composer,
TTP launch-all) calls the dry run first and shows the same blockers.

### 4.3 The views

**Launch gate (default route).** One page, three bands in fixed order:

1. **This POVengine.** Instance checks. Clean in one line when all pass.
2. **Lab.** Lab checks, each with Fix › into its component's Configure step.
3. **This run.** With nothing armed: "Arm a scenario", plus the first-run suggestion
   Get started makes today. With a scenario armed: target and mode pickers (from the
   guided flow, without its 25-row agent list), the run checks, the attested prevention
   context, consent, and the **Launch** button.

A fresh install lands here and reads as Get started: two short bands and "arm your
first scenario". The hundredth run reads the same page with every band green except
what changed. It is a preflight checklist: the same list before every flight.

**Components and the POVengine page.** Unchanged in shape (v26 §4.2). Status words,
Services rows and Configuration rows render the model's instance and lab checks. They
remain where things get fixed; the gate links into them and never duplicates a
Configure flow. Get started's inline agent installer and tenant form retire in favour
of the Agents and Tenant Configure steps, which already hold the same flows.

**Where today's Launch gate content goes.**

| Today on the Launch gate | Moves to |
|---|---|
| Cortex proof ladder, per-tenant rows, SOC Framework row, Preflight button | Tenant component, Read-back tab (rungs from the server's `/api/readiness`, no client recomputation) |
| "This SimCore" component grid | POVengine page Services and Configuration (already there) |
| "Not checked" list | POVengine page, and the gate's footer as one line |
| Attested-only facts | This run band (they are per run) |

**Header and rail.** The header word is the worst instance verdict, excluding
`standing`. The Components badge counts components that are Down or Degraded. The
first rail item's badge counts required instance and lab checks that are not `pass`.
All three read `useReadinessModel()`, one poll, so they cannot disagree with the pages.

**Routes.** `start`, `setup`, `scope`, `guided` and `readiness` redirect to the gate
(`guided?arm=` becomes `preflight?arm=`). `preflight` keeps its id.

## 5. What gets deleted

| File | Fate |
|---|---|
| `setupProgress.js` state logic, `useSetupProgress.js` | Removed; step copy moves to check titles |
| `GetStartedView.jsx` | Removed after redirect |
| `componentModel.js` status derivation | Reduced to "worst verdict per component" over the model |
| `readiness/healthModel.js` downgrades (empty catalog, unpinned shelf) | Moved server-side into the model |
| `readiness/connectorState.js` ladder recomputation, dead `/api/connectors` fetch, 200-row assertion cap | Removed; Tenant tab reads server rungs |
| `readiness/scenarioPreflight.js` | Advisory checks move server-side; the file shrinks to rendering |
| `instanceStatus.js` | Reads the model |
| Draft `launchable` mirror and `getDraftLaunchable` | Folded into the dry run |
| localStorage `cortexsim.setup.confirmed.v1` | Replaced by server attestations (D-4) |

## 6. Build order

One branch, one task each (CONTRIBUTING). Slices 1 and 2 are independent; 3 needs 2;
4 needs 1 and 3.

| # | Branch | Delivers | Size |
|---|---|---|---|
| 1 | `fix/api-launch-dry-run` | §4.2: pure pre-seed checks, `POST /api/runs/preflight`, post-seed refusals moved earlier, codes for the codeless refusals, draft mirror folded in. Behaviour test per refusal code. | M–L, the risky one: `orchestrator.launch()` |
| 2 | `feature/api-readiness-model` | §4.1: `core/engine/readiness.py`, `GET /api/readiness/checks` (instance + lab now; run scope calls slice 1 when it lands), attestations API (D-4) | M |
| 3 | `feature/ui-readiness-model` | `useReadinessModel()`; header, rail badges, Components, POVengine page and today's Get started read it; §5 deletions except the page merge. Test: one server fixture, header word, both badges and every page agree. | M–L |
| 4 | `feature/ui-launch-gate-merge` | §4.3: the three-band page, guided flow and Get started folded in, every launch button through the dry run, redirects, Launch gate content moved, `DESIGN-SYNC.md` updated (v26 §0.3) | L |

Smaller defects found on the way are fixed in the slice that owns them, not as separate
PRs: Get started marks the tenant done after a failed test; the tenant list is never
re-polled; the agent auto-select picks an agent that has never polled; a staged push
run reads "A run is in progress" forever on Get started; the header fallback still says
"Launch gate".

## 7. What this does not do

- No new probe and no outbound call from the model. Reachability stays an operator
  action, as health's `not_checked[]` already states.
- No fourth page, and no client-side model kept as a fallback.
- No change to the eight component pages' shape or to Configure.
- No write path to Cortex or the firewall.

## 8. Decisions

| # | Question | Recommendation | Alternative |
|---|---|---|---|
| D-1 | One page or three? | Merge Get started and "New POV run" into the Launch gate, default route, first rail item. The "Compose & simulate" group loses its Launch gate entry; Run on a scenario opens the gate with it armed (v26 §4.4). | Keep Get started as a separate first-run view over the same model. The model and gate do not change. |
| D-2 | `api_auth` until SEC-01 | `standing`: shown on the gate and the POVengine page with its fix (bind address), excluded from Degraded and from issue counts | Keep counting it, and accept a header that is never clean |
| D-3 | What "agents ready" means | The run's target agent(s). Fleet staleness lives on the Agents page only. (Separately: whether to forget agents offline for more than N days; 24 of 25 here are dead `docker-desktop` rows.) | Keep "all enrolled agents online" |
| D-4 | Where "I've set this up" ticks live | Server attestations (who, when), so every DC on the instance sees the same state; still "you confirmed", never green | Keep them per browser |
| D-5 | Which verdicts hold a launch (#217) | Only `block` holds. `warn` and `unknown` show with the existing "Unknown is not ready" copy and do not hold. | Hold on `unknown` too |
| D-6 | #227 Task 27 ("Get Started rebuilt from `/api/doors`") | Re-target: doors become lab checks in the model (one per door), not a `GetStartedView` rewrite | Keep Task 27 as written, against a page this plan removes |

## 9. Sources

Mapped read-only on 2026-11-13 from `dev` 48f3dfcd: the console code for each surface,
the server endpoints and the `POST /api/runs` refusal order, git history and open
issues. The live readings in §1 come from the `dev` console served against the running
SimCore 1.1.1 on this host.
