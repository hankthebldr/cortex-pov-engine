# UI component pre-merge fixes

Fixes for the adversarial final-review defects assigned to this pass (branch
`feature/ui-onboarding-activation-tour`). Scope: `ui/src/app/destinations.jsx`,
`ui/src/components/console/{useLaunchScenario.js,LaunchView.jsx,TtpBrowserView.jsx,
OperationsView.jsx,RunDetailView.jsx,TenantManager.jsx,UcTcIndexView.jsx}`,
`ui/src/components/EalConsole.jsx`, and the CSS under `ui/src/styles/`.
Onboarding tour files (`ui/src/components/onboarding/**`, `useTour.js`,
`TourSpotlight.jsx`) were explicitly out of scope and untouched.

Status: **all 7 items fixed, tests written and RED/GREEN verified per fix**.
`cd ui && npx vitest run` — **76 files / 782 tests, all green** (baseline
66/715 — no drop, +67 tests added). `npm run build` succeeds; bundle split
intact (initial-paint chunks ≈304 KB raw / ≈93 KB gzip, well under the
542 KB target).

## I-1 (priority) — a launch could silently omit the payload plan

**Root cause.** `useDecodedPlan` (`ui/src/app/destinations.jsx`) decodes a
`?plan=` deep link via a dynamic `import()` of `ToolAdapterCatalog.jsx`
(bundle-splitting). It returned a bare `plan` state that was `null` both
when no plan was ever composed AND while the chunk was still resolving —
the two states were indistinguishable. `useLaunchScenario.js` gated sending
`payload_plan` on `payloadPlan?.artifacts?.length`, which is falsy in both
cases, so a Launch click during the resolving window fired a run with no
payload plan, no error, and no on-screen indication — the exact
manufactured-false-negative failure the payload shelf exists to prevent.

**Fix.**
- `useDecodedPlan` now returns `{ plan, resolving }`. `resolving` is `true`
  synchronously the instant an `encoded` param is present, before the
  dynamic import settles, and flips to `false` only once `decodePlan`
  actually ran (exported for direct unit testing).
- `GuidedPovFlow` destructures both and forwards `payloadPlanResolving` to
  `LaunchView` → `useLaunchScenario`.
- `useLaunchScenario`'s `launch()` **refuses to POST** while
  `payloadPlanResolving` is true (not just relying on the button being
  disabled — this closes the race for any caller of `launch()`, not only
  the rendered button) and adds a named blocker
  (`'Payload plan is still resolving from this link — wait before
  launching'`) so the Launch button stays disabled with a visible reason.
- `LaunchView`'s `PayloadPlanCard` renders a fourth, explicit state
  (`data-testid="launch-payload-plan-resolving"`) instead of silently
  showing nothing while resolving.

**Files:** `ui/src/app/destinations.jsx`, `ui/src/components/console/useLaunchScenario.js`,
`ui/src/components/console/LaunchView.jsx` (this file's edit landed via a
concurrent onboarding-tour commit that touched the same file for an
unrelated reason — content verified intact by this agent before commit).

**Tests (new):**
- `ui/src/app/__tests__/destinations.payloadPlan.test.jsx` — `useDecodedPlan`
  reports `resolving:true, plan:null` synchronously on first render with an
  encoded param, settles to the decoded plan, and is `resolving:false,
  plan:null` when no param exists.
- `ui/src/components/__tests__/useLaunchScenario.payloadPlanResolving.test.jsx`
  — **the load-bearing assertion**: `launch()` called while
  `payloadPlanResolving` is true never POSTs (`posted.length === 0`),
  returns `null`, and surfaces a named blocker; launches normally once the
  flag flips to `false`.
- Two new cases in `LaunchPayloadPlan.test.jsx` — Launch button disabled +
  the resolving placeholder rendered (never nothing) while resolving, and
  the transition to the real plan card once it settles.

**RED/GREEN.** RED: reverted `destinations.jsx` + `useLaunchScenario.js` +
`LaunchView.jsx` via targeted `git stash` — `destinations.payloadPlan.test.jsx`
failed on missing named export, `useLaunchScenario.payloadPlanResolving.test.jsx`
failed with `posted.length === 1` (the exact bug: a run went out mid-race),
`LaunchPayloadPlan.test.jsx`'s two new cases failed (no resolving testid,
button not disabled). GREEN: all pass after restoring.

Commit: `4e3dfff fix(ui): I-1 — Launch could silently omit an in-flight payload plan`.

## I-3 — `.destination-loading` static inline style, zero CSS anywhere

**Root cause.** `DestinationLoading` (`destinations.jsx`) — the Suspense
fallback shown for every lazy destination chunk — used
`style={{ padding: '2rem', opacity: 0.6, fontSize: '0.9em' }}` and
`.destination-loading` had no CSS rule anywhere in `ui/src`.

**Fix.** Added `.theme-console .destination-loading` to
`cortex-console.css` using the token vocabulary the rest of the pass
adopted (`--space-8`/`--space-6` for padding, `--c-text-muted` for color,
`13px` matching the codebase's other small-text rules), then removed the
inline `style` prop. Also exported `DestinationLoading` for direct
component testing.

**Files:** `ui/src/app/destinations.jsx`, `ui/src/styles/cortex-console.css`.

**Test (new):** `ui/src/styles/__tests__/destinationLoading.test.jsx` —
uses the same real-stylesheet cascade resolver as
`console-contrast.test.jsx` (`resolveProperty`) to assert padding/opacity/
color resolve from the real stylesheet (not jsdom's `getComputedStyle`,
which doesn't resolve `var()`), plus a render assertion that the component
itself carries no inline `style` attribute.

**RED/GREEN.** RED: reverted the CSS rule + JSX — `opacity` resolved to
`null` instead of `'0.6'`, and the render test threw (`DestinationLoading`
wasn't exported yet). GREEN after restoring.

Commit: CSS rule landed in `4e3dfff` (bundled with I-1's shared
`cortex-console.css` hunk — see note below); test coverage in
`d275fea test(ui): I-3 — cover the .destination-loading CSS rule`.

## M-1 — a deleted `@keyframes` broke another component's animation

**Root cause.** `ToolStatusPanel.jsx` correctly dropped its inline
`@keyframes pulse` block, but that was the app's only definition, and
`cortex-console.css`'s `.theme-console .tenant-mgr__pill--testing` still
declared `animation: pulse 1s ease-in-out infinite`. The TenantManager
"TESTING…" pill's loading affordance silently stopped animating.

**Fix.** Added `@keyframes pulse { 0%,100% { opacity:1 } 50% { opacity:.4 } }`
to `cortex-console.css`, right next to the rule that references it.

**Files:** `ui/src/styles/cortex-console.css`.

**Test (new):** `ui/src/styles/__tests__/m1-pulse-keyframes.test.jsx` —
scans `document.styleSheets` for a `CSSKeyframesRule` named `pulse`.

**RED/GREEN.** RED: reverted the keyframes block — scan returned no match.
GREEN after restoring.

Commit: rule landed in `4e3dfff`; test coverage in
`acc507a test(ui): M-1 — cover the @keyframes pulse definition`.

## M-3 — three new classes had no CSS rule

`eal-console__heading`, `tools-destination__head-copy` (structural
wrappers) and `ttpb-callout--warn` (the Remediation-guidance callout
modifier whose warn treatment never landed).

**Fix.**
- `.eal-console__heading { display:flex; flex-direction:column; min-width:0; }`
  in `destinations/eal.css`.
- `.theme-console .tools-destination__head-copy { display:flex;
  flex-direction:column; min-width:0; }` in `destinations/adapters.css`.
- `.ttpb-callout--warn { background: color-mix(in srgb, var(--warn) 8%,
  transparent); border-radius: 0 4px 4px 0; } .ttpb-callout--warn
  .ttpb-callout__label { color: var(--warn); }` in `destinations/ttps.css`
  (the base `.ttpb-callout` already sets `border-left`; the modifier now
  adds a soft tint + a warn-colored label on top).
- `vitest.config.js`: added `adapters.css` to the `css.include` allowlist
  (it wasn't previously loaded with real CSS in tests — `eal.css`/`ttps.css`
  already were).

**Files:** `ui/src/styles/destinations/{eal,adapters,ttps}.css`, `ui/vitest.config.js`.

**Test (new):** `ui/src/styles/__tests__/m3-missing-classes.test.jsx`.

**A methodology note worth keeping.** The first version of this test used
`cssCascade.js`'s `resolveProperty`, checking `display`/`flex-direction`
after mounting each class inside its real parent markup (e.g.
`.eal-console > .eal-console__hero > .eal-console__heading`). It **passed
even against the unfixed source** — `resolveProperty` walks the ancestor
chain for *any* property (by design, for naturally-inherited properties
like `color`), and `.eal-console` itself already declares
`flex-direction: column` for unrelated layout reasons, so the ancestor
walk resolved that instead of finding nothing on the child. Verified
empirically (a debug test printed the resolved values against reverted
CSS) before rewriting the test to look up the *exact authored selector*
directly in the loaded stylesheets — a check only a real rule on that
precise class can satisfy. Left as a comment in the test file as a
warning for the next person reaching for that resolver on a structural
(non-inherited) property.

**RED/GREEN.** RED (after the rewrite): all three `findRule(...)` lookups
returned `null` against reverted CSS. GREEN after restoring.

Commit: CSS rules landed in `4e3dfff`; test + vitest.config.js change in
`ed38ec3 test(ui): M-3 — cover eal-console__heading / tools-destination__head-copy / ttpb-callout--warn`.

## M-2 — copy changed silently, contrary to the brief

`TtpBrowserView.jsx`'s `TtpDetail`: "Detection coverage" had become
"Detection logic", and the "Summary" section label had been dropped
entirely (the paragraph lost its `DetailSection` wrapper). Neither change
was argued for — restored both verbatim.

**Files:** `ui/src/components/console/TtpBrowserView.jsx`.

**Test (new):** new case in `ui/src/components/__tests__/TtpBrowserView.test.jsx`
asserting both labels render and `"Detection logic"` is absent.

**RED/GREEN.** RED: temporarily re-applied the old copy —
`screen.getByText('Summary')` threw (not found). GREEN after restoring.

Commit: `ab4d1c9 fix(ui): M-2 — restore dropped "Summary" section + "Detection coverage" label`.

## M-4 — masthead eyebrow copy inconsistent across seven destinations

Four patterns coexisted: nav-group-alone (`Traffic`, `Manage` — correct,
matches `CoverageView`'s `CoverageKicker`, which deliberately dropped its
own `· Phase N` suffix and is the reference implementation), group+phase
(`Operate · Phase 2`), group+destination (`Analyze · UC / TC Index`), and
destination-alone (`Runs & Proof`). Picked the nav-group-alone pattern
(already correct in `EalConsole`/`ToolAdapterCatalog`/`LabView`/
`TargetsView`/`CoverageView`) and normalized the four deviating
destinations:

| Component | Before | After |
|---|---|---|
| `OperationsView` (Library) | `Operate · Phase 2` | `Operate` |
| `TenantManager` | `Manage · Phase 1` | `Manage` |
| `UcTcIndexView` | `Analyze · UC / TC Index` | `Analyze` |
| `RunDetailView` | `Runs & Proof` | `Operate` (its real nav group, per `destinations.jsx`'s `{ id: 'runs', group: 'Operate' }`) |

**Files:** `ui/src/components/console/{OperationsView,TenantManager,UcTcIndexView,RunDetailView}.jsx`.

**Test (new):** `ui/src/app/__tests__/mastheadEyebrow.test.jsx` — renders
each component (via `EnvironmentContext.Provider` + `DEFAULT_ENV` for the
two that read ambient scope) and asserts the fixed text, plus the absence
of the old string and any `Phase \d` substring.

**RED/GREEN.** RED: `sed`-reverted all four strings to their pre-fix
copy — all 4 assertions failed. GREEN after restoring.

Commit: `07c4496 fix(ui): M-4 — one masthead eyebrow pattern, no "· Phase N" suffix`.

## M-8 — `EalConsole.jsx` tablist a11y

"+ New Campaign" drove the same tab state as the two `role="tab"` buttons
while living outside the `role="tablist"` as a header action. When it was
active, neither remaining tab reported `aria-selected="true"` — a
screen-reader user was told nothing was selected. No
`role="tabpanel"`/`aria-controls` pairing existed either.

**Fix.** Moved the button into the tablist as a genuine third tab
(`id`, `role="tab"`, `aria-selected`, `aria-controls`), keeping its
distinct CTA visual treatment via a renamed class
(`.eal-console__tab--new`, was `.eal-console__build-btn`) with
`margin-left:auto` to hold its former top-right position within the row,
rather than adopting the underline-tab look. Each panel now wraps in
`role="tabpanel"` + `aria-labelledby` pointing back at its tab.

**Files:** `ui/src/components/EalConsole.jsx`, `ui/src/styles/destinations/eal.css`.

**Test (new):** two cases in `ui/src/components/__tests__/EalConsole.test.jsx`
— exactly one tab reports `aria-selected="true"` at all times including
when "+ New Campaign" is active, and every tab↔panel
`aria-controls`/`aria-labelledby` pair resolves to a real, correctly-typed
element.

**RED/GREEN.** RED: temporarily restored the pre-fix structure (button
outside the tablist, driving `tab==='new'` with no `role="tab"` of its
own) — `getAllByRole('tab')` returned 2 instead of 3, both new assertions
failed. GREEN after restoring.

Commit: CSS rename/rule landed in `4e3dfff`; JSX + tests in
`2d84d59 fix(ui): M-8 — EalConsole tablist a11y — "+ New Campaign" is a real tab now`.

## Explicitly not fixed (per instructions)

- **I-2** (tour stops skipped behind lazy boundaries) — the onboarding
  agent's; untouched.
- **M-5** (`StackCoverageView` continuous → 3-bucket intensity) —
  deliberate and documented; left as-is pending operator sign-off.
- **M-6** (two CSS-loading conventions + duplicate class definitions
  across `cortex-console.css` and `destinations/*.css` whose winner
  depends on injection order) — real, confirmed still present, but
  untangling it mid-merge risks more than it fixes. **Follow-up.**
- **M-7** (`CoverageView` still uses `style={{background: s.color}}`
  where `UcTcIndexView` uses classes) — confirmed still present.
  **Follow-up.**

## A note on concurrent-agent git hygiene

This branch had at least two other agents committing concurrently
(onboarding-tour fixes, and a separate console-contrast/token-aliasing
repair pass) into the same working tree. Two things worth recording:

1. `LaunchView.jsx`'s half of the I-1 wiring (the `payloadPlanResolving`
   prop plumbing) landed inside a concurrent onboarding-tour commit that
   touched the same file for an unrelated reason (wiring `FirstUseHint`).
   Content was verified intact before relying on it.
2. Whole-file `git add`/plain `git commit -m` briefly picked up another
   agent's concurrently-staged files (the shared index is not isolated
   per agent process) — one commit (`test(ui): M-1 — cover the @keyframes
   pulse definition`) ended up including two of that agent's test files
   and a large chunk of their `console-contrast.test.jsx` addition
   alongside the intended one-file change. No content was lost or
   corrupted (verified byte-for-byte after the fact), and a
   `git reset --soft` used to correct it briefly orphaned that other
   agent's own docs commit before it was restored — also with no data
   loss, verified via `git reflog`. From that point on, every remaining
   commit in this pass used an explicit trailing pathspec on `git commit`
   (`git commit -m "..." -- file1 file2`) specifically to make this
   impossible going forward: a pathspec restricts the commit to exactly
   those paths regardless of what else is sitting staged in the shared
   index. Net effect: the git history for `acc507a` and `b9475c9` reads
   slightly off from what each commit's message strictly describes, but
   all content is correct, present, and unambiguously attributable by
   diffing each commit individually.
