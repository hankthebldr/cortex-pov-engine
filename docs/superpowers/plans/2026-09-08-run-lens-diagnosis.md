# Run-lens empty-state diagnosis (Task 1 of the composer-direct-manipulation plan)

**Status: cause established.** This is a written diagnosis only — no source file was
modified in the course of this investigation.

## Observed behaviour

Console header: `SIM-EDR-001 · failed`. Opening `SIM-EDR-001` in the Simulation
Composer (from the Library) and switching to the Run lens renders:

> **No run yet — EXPECTED only.** Launch this chain (or reconcile an observed
> run) and the real causality graph will render here with CONFIRMED / BROKEN
> edges. Nothing on this canvas is inferred before a run exists.

This is misleading: a run of `SIM-EDR-001` (`5b2a45ba-0f4e-41d4-aa34-2f3e19768678`)
exists, is terminal (`status: failed`), and the backend holds a fully populated
causality graph for it.

## Probe 1 — baseline: does `/api/runs` show a recent run for this scenario?

```
$ curl -s localhost:8888/api/runs | python3 -m json.tool | head -40
```

```json
{
    "runs": [
        {
            "id": 32,
            "run_id": "fed0251c-f25d-4265-8c18-446683541146",
            "scenario_id": "SIM-EDR-002",
            "mode": "push",
            "target": null,
            "identity_context": "node",
            "status": "staged",
            ...
        },
        {
            "id": 31,
            "run_id": "5b2a45ba-0f4e-41d4-aa34-2f3e19768678",
            "scenario_id": "SIM-EDR-001",
            "mode": "pull",
            "target": "docker-desktop-6b0149",
            "identity_context": null,
            "status": "failed",
            "tc_verdict": "pending",
            "tc_verdict_detail": {
                "verdict": "pending",
                "detail": "primary KPI declared but not measured: no measured value yet",
                "weighted_pass": 0.0,
                "weighted_total": 0.0,
                "counts": {"pass": 0, "fail": 0, "pending": 0, "not_applicable": 0},
                "primary": {"verdict": "pending", "detail": "no measured value yet",
                            "actual": null, "expected": 300.0, "op": "≤"},
                "unscoreable": [],
                "source": "complete",
                "scored_at": "2026-09-09T01:16:56.983709"
            },
            "stitch_binding": null,
            "channel_dispatch": null,
            "open_tasks": 0,
            "runtime_install_authorized": false,
            "runtime_dependency_gaps": [ ... ]
        },
        ...
```

`run_id 5b2a45ba-0f4e-41d4-aa34-2f3e19768678`, `scenario_id SIM-EDR-001`,
`status "failed"` — matches the console header exactly. This is the run used
for all subsequent probes.

(The single most-recent row by list order is `id 32` / `SIM-EDR-002`, `status
"staged"` — a different scenario's push-bundle stage event, not what the
console header names. The run matching the header is `id 31`.)

## Probe 2 — does the causality endpoint return a graph for that run?

```
$ RUN=5b2a45ba-0f4e-41d4-aa34-2f3e19768678
$ curl -s -o /dev/null -w '%{http_code}\n' "localhost:8888/api/runs/$RUN/causality"
200

$ curl -s "localhost:8888/api/runs/$RUN/causality" | head -c 1200
{"causality_graph":{"run_id":"5b2a45ba-0f4e-41d4-aa34-2f3e19768678","scenario_id":"SIM-EDR-001","run_status":"failed","nodes":[{"id":"cgo:5b2a45ba-0f4e-41d4-aa34-2f3e19768678","kind":"cgo","label":"Causality Group Owner - apache2","image_name":"apache2","primary_username":"www-data","os_pid":1000,"causality_id":"cgo:5b2a45ba-0f4e-41d4-aa34-2f3e19768678","step_index":-1},{"id":"proc:5b2a45ba-0f4e-41d4-aa34-2f3e19768678:step-01","kind":"process","label":"cat","image_name":"cat","command_line":"cat /etc/passwd | grep -v nologin | grep -v false | grep sh$ && echo '[*] Active shell users enumerated'","primary_username":"www-data","os_pid":1001,"causality_id":"cgo:5b2a45ba-0f4e-41d4-aa34-2f3e19768678","mitre_technique":"T1087.001","asset_ref":null,"control_layer":"prevention","step_id":"step-01","step_index":0,"observed":false},{"id":"wrap:5b2a45ba-0f4e-41d4-aa34-2f3e19768678:step-01","kind":"wrapper","label":"runuser","image_name":"runuser","primary_username":"www-data","os_pid":1002,"causality_id":"cgo:5b2a45ba-0f4e-41d4-aa34-2f3e19768678","step_id":"step-01","step_index":0},{"id":"proc:5b2a45ba-0f4e-41d4-aa34-2f3e19768678:step-02","kind":"process","label":"cat","image_name":"cat","com

$ curl -s "localhost:8888/api/runs/$RUN/causality" | wc -c
   15408
```

**Result per the brief's decision table: 200 with a populated `causality_graph`**
(15,408 bytes — CGO root, per-step `process`/`wrapper` nodes, MITRE technique
tags, etc.) → **the backend is fine; the bug is in the frontend fetch or its
trigger condition.** Proceeds to Probe 3. `core/api/causality.py` /
`core/engine/causality_graph.py` were not implicated further — no need to read
their preconditions once the 200-populated case was confirmed live.

## Probe 3 — frontend trigger condition

```
$ sed -n '255,305p' ui/src/components/console/ComposerView.jsx
```

```js
  // ── Run lens data ────────────────────────────────────────────────────────────
  // The Run lens renders the REAL causality graph of an in-flight or terminal
  // run for this draft; pre-run it stays null and the canvas says "EXPECTED
  // only" rather than drawing a green chain the tenant never correlated.
  const activeRunId = env.activeRun ? runIdOf(env.activeRun) : null
  // Refetch as the run progresses: the SSE-driven activeRun updates its
  // step/detected/status, and each of those is a moment a stitch may reconcile
  // to CONFIRMED or BROKEN. Keying only on the (stable) run id would freeze the
  // graph at launch — empty/EXPECTED — for the whole run.
  const runTick = env.activeRun
    ? `${env.activeRun.step}:${env.activeRun.detected}:${env.activeRun.status}`
    : null
  const [causalityGraph, setCausalityGraph] = useState(null)
  useEffect(() => {
    if (!activeRunId) { setCausalityGraph(null); return undefined }
    let cancelled = false
    getRunCausality(activeRunId)
      .then((g) => { if (!cancelled) setCausalityGraph(g || null) })
      .catch(() => { if (!cancelled) setCausalityGraph(null) })
    return () => { cancelled = true }
  }, [activeRunId, runTick])
```

```
$ grep -n "getCausality\|setCausalityGraph\|causalityGraph" ui/src/components/console/ComposerView.jsx
267:  const [causalityGraph, setCausalityGraph] = useState(null)
269:    if (!activeRunId) { setCausalityGraph(null); return undefined }
272:      .then((g) => { if (!cancelled) setCausalityGraph(g || null) })
273:      .catch(() => { if (!cancelled) setCausalityGraph(null) })
275:  }, [activeRunId, runTick])
277:    () => causalityStepStates(causalityGraph),
278:    [causalityGraph],
723:          causalityGraph={causalityGraph}
```

(The brief's grep target `getCausality` doesn't appear — the actual imported
symbol is `getRunCausality` from `ui/src/api/client.js`, imported at
`ComposerView.jsx:13`. `ui/src/api/causality.js` exports a *different*,
unrelated function also named `getCausality` used elsewhere (`normalizeCausality`
view-model for a different consumer) — it is not in this call path at all.)

The only input to the fetch is `activeRunId`, derived exclusively from
`env.activeRun`. Tracing that value into `ui/src/context/EnvironmentContext.jsx`:

```
$ sed -n '332,344p' ui/src/context/EnvironmentContext.jsx
```

```js
  // ── Derived active run (most recent truly in-flight run) ─────────────────
  const activeRun = useMemo(() => {
    const running = runs.find((r) => r && r.status === 'running')
    if (!running) return null
    ...
```

`env.activeRun` is a **global, app-wide** value — the first run in the entire
`runs` list whose `status === 'running'`. It carries no relationship at all to
`fromId` / `originDetail` / `savedScenarioId` (the values that identify which
scenario is open in the Composer draft — confirmed absent from the `activeRun`/
`causalityGraph` effect by grep: `env.runs`, `env.lastRun`, `fromId`, and
`scenario_id` do not appear together with `activeRunId`/`causalityGraph`
anywhere in the file). `isRunTerminal()` in `ui/src/components/console/runStatus.js`
confirms `'failed'` is terminal (`isRunComplete(s) || s === 'failed' || s === 'aborted'`),
so a `status: "failed"` run can **never** be `env.activeRun` — only `env.lastRun`
(a separate, also globally-scoped value that `ComposerView` never reads for
this purpose).

Net: `getRunCausality` fires only when *some* run anywhere in the system is
currently `running`. It is never called for a terminal run, regardless of
whether that run belongs to the open draft. The brief's suspected mechanism
("bound to the open draft") is close but not quite it — the actual gate is
coarser: global running-status only, not per-draft binding at all. Practically
the two produce the same failure for this repro (SIM-EDR-001's only run is
terminal), so the observed symptom is identical either way.

## Probe 4 — live confirmation (Browser pane, not a substitute reading)

Browser tooling was available, so the live console was used directly rather
than the source-reading fallback.

1. Navigated to `http://localhost:8888/#/composer`, acknowledged the launch
   warning banner.
2. Navigated to `http://localhost:8888/#/composer?from=SIM-EDR-001` (the
   router's `from` param — confirmed against `ui/src/app/useConsoleRouter.js`
   — is exactly `ComposerView`'s `fromId`, i.e. "open this scenario from the
   Library"). Network log showed `GET /api/scenarios/SIM-EDR-001 → 200 OK`;
   screenshot confirmed the header changed to `SIM-EDR-001 · 5 steps · 4
   techniques` with the chain rendered in the Design lens.
3. Clicked the **Run** lens tab. Screenshot confirmed the canvas rendered:

   > No run yet — EXPECTED only. Launch this chain (or reconcile an observed
   > run) and the real causality graph will render here with CONFIRMED /
   > BROKEN edges. Nothing on this canvas is inferred before a run exists.

4. Read the network log for the full session (`read_network_requests`, no
   `urlPattern` filter, 50-row window) covering scenario load + lens switch:

   ```
   [86400.5]   GET /assets/index-xiofu_Xb.js               200 OK
   [86400.6]   GET /assets/vendor-react-N--QU9DW.js         200 OK
   [86400.7]   GET /assets/index-A6Kd7vc5.css                200 OK
   [86400.111] GET /assets/ComposerView-VXwMnB-Y.js          200 OK
   [86400.112] GET /assets/useLaunchScenario-X8TMUc5T.js     200 OK
   [86400.113] GET /assets/ComposerView-Cu-yXbzk.css         200 OK
   [86400.114] GET /assets/cortex-mono.png                   200 OK
   [86400.115] GET /api/tools/adapters                       200 OK
   [86400.116] GET /api/shelf/payloads                       200 OK
   [86400.117] GET /api/scenarios                            200 OK
   [86400.118] GET /api/runs                                 200 OK
   [86400.119] GET /api/agents                                200 OK
   [86400.120] GET /api/credentials/integrations?kind=xsiam_tenant  200 OK
   [86400.121] GET /api/health                                200 OK
   [86400.122] GET /api/shelf/payloads                        200 OK
   [86400.123] GET /api/ttps?status=active                    200 OK
   [86400.124] GET /api/tools/adapters                        200 OK
   [86400.125] GET /api/agents                                 200 OK
   [86400.126] GET /api/shelf/artifacts                        200 OK
   [86400.127] GET /api/shelf/artifacts                        200 OK
   [86400.128] GET /api/runs                                    200 OK
   [86400.129] GET /api/health                                  200 OK
   [86400.130] GET /api/scenarios/SIM-EDR-001                   200 OK
   [86400.131] GET /api/runs                                     200 OK
   [86400.132] GET /api/health                                   200 OK
   [86400.133] GET /api/runs                                      200 OK
   [86400.134] GET /api/health                                    200 OK
   [86400.135] GET /api/runs                                       200 OK
   [86400.136] GET /api/health                                     200 OK
   ```

   A separate `urlPattern: "causality"` query against the same log returned
   **"No network requests recorded."**

   `/api/runs` is polled repeatedly (the environment provider's run-list
   poll/SSE-fallback loop), and every poll includes the SIM-EDR-001 row with
   its populated `causality_graph`-bearing run id — but `GET
   /api/runs/{id}/causality` is never issued.

This distinguishes the two candidate causes cleanly: the request is **never
sent** ("not fetched"), not sent-and-returned-empty. This matches Probe 3's
source-level finding exactly: `activeRunId` stayed `null` throughout the
session because no run anywhere in the system had `status: "running"`
(`SIM-EDR-002`'s row was `staged`; `SIM-EDR-001`'s was `failed`), so the
`useEffect` on `ComposerView.jsx:268-275` short-circuited on its guard clause
every time and never called `getRunCausality`.

## Established cause

`ComposerView`'s Run lens sources its causality graph exclusively from
`env.activeRun` (`ComposerView.jsx:259`), which `EnvironmentContext.jsx:333-336`
derives as *the first run anywhere in the system with `status === 'running'`*.
This value is:

1. **Not scoped to the open draft.** It carries no `scenario_id` match against
   `fromId` / `originDetail` / `savedScenarioId` — a running run of an
   unrelated scenario would populate it while an unrelated scenario's Run
   lens is open (out of scope to reproduce here, but follows directly from
   reading the code — noted as a related but distinct risk, see Recommendation).
2. **Never populated for a terminal run**, by construction — `activeRun`'s
   `runs.find((r) => r.status === 'running')` filter excludes `complete`,
   `failed`, and `aborted` outright. `env.lastRun` (which *does* pick up the
   most recent terminal run) exists in `EnvironmentContext.jsx:349-355` but is
   never read by `ComposerView`'s causality effect.

For a corpus scenario opened from the Library whose most recent (or only) run
has already finished — exactly the `SIM-EDR-001 · failed` case in the console
header — `activeRunId` is `null` for the entire life of that Composer session,
`getRunCausality` is never called, `causalityGraph` never leaves its `useState(null)`
initial value, and `RunGraph` (`ComposerCanvas.jsx:358-390`) renders the "No
run yet — EXPECTED only" empty state — a materially false claim, since a real,
backend-verified, 15KB causality graph for that exact run is one `GET` away.

This is a **frontend trigger-condition bug**, confirmed at both the source
level (Probe 3) and by live reproduction (Probe 4). The backend
(`core/api/causality.py` → `core/engine/causality_graph.py`) is not
implicated — Probe 2 already showed it serving the correct payload — so those
files were not required as fix-relevant reading beyond the Probe 2 baseline
already established.

## What was ruled out

- **"200 with an empty/degenerate graph — run produced no observations."**
  Ruled out by Probe 2: the graph is fully populated (CGO root + per-step
  process/wrapper nodes + MITRE technique tags), not empty or degenerate.
- **"404 / 5xx — a backend gap."** Ruled out by Probe 2: HTTP 200.
- **"Fetched empty" (frontend calls the endpoint but renders nothing from the
  response).** Ruled out by Probe 4: no `causality` request appears in the
  network log at all for the entire session, including after switching to
  the Run lens. This is "not fetched," not "fetched empty" — the distinction
  the brief flagged as needing opposite fixes.
- **The brief's literal suspected mechanism ("fires only when a run is bound
  to the open draft").** Partially confirmed, partially refined: there is no
  per-draft binding logic to be "true" or "false" in the first place —
  `env.activeRun` is derived with no reference to the open draft at all. The
  practical effect for this repro is identical to the brief's framing (a
  Library-opened scenario's own run never drives the fetch), but the
  mechanism is coarser than "binding": it is gated on global run status, full
  stop, regardless of which scenario's run it is.

## Recommendation: bounded fix (proposed as Task 13)

This is scoped enough to dispatch directly, and does not require a separate
spec.

**Goal:** the Run lens should show the causality graph for *the run belonging
to the open draft's origin scenario* — in-flight or terminal — not an
unrelated globally-active run, and not nothing when a terminal run with real
data exists.

**Sketch (for the Task 13 implementer, not applied here):**

1. In `ComposerView.jsx`, derive a scenario-scoped run candidate from `env.runs`
   (already available in this component — used today at `ComposerView.jsx:818`
   for `HistoryPane`) by filtering on `r.scenario_id === (draft.originId ||
   savedScenarioId || fromId)` and picking the most recent by `started_at`
   (or run `id`) — including terminal statuses, not just `running`.
2. Prefer `env.activeRun` when it *also* matches that same scenario id (so an
   in-flight run for the open draft still gets the live SSE-driven refetch
   behavior via `runTick`); otherwise fall back to the scenario-scoped
   candidate from step 1.
3. Keep re-fetching while the matched run is non-terminal (existing `runTick`
   mechanism); a terminal match needs no polling — fetch once.
4. Update the empty-state copy path so "No run yet" is only shown when no run
   for *this* scenario exists at all (as opposed to "a run exists but wasn't
   the global active one").

**Out of scope for Task 13** (flag, don't fix here): the corollary risk noted
under "Established cause" point 1 — an in-flight run of scenario B could
currently paint scenario A's Run lens while A's draft is open, since there is
today no scenario-id guard on `env.activeRun` before it's trusted. Task 13's
fix (matching by `scenario_id`) closes this incidentally as a side effect of
the same change, so it does not need its own task, but the implementer should
verify it explicitly as part of Task 13's acceptance check.

No part of this diagnosis required treating the cause as unestablished — all
four probes converged on the same mechanism independently (backend-populated
graph, source-level trigger-condition gate, and live network-log absence of
the request).
