# Onboarding activation tour — pre-merge blocker fixes

> **Date:** 2026-08-31 · **Branch:** `feature/ui-onboarding-activation-tour`
> **Binding spec:** `docs/superpowers/specs/2026-08-30-onboarding-activation-tour-design.md`

Fixes six defects from the adversarial final review: three that broke stop 4
(the enrollment one-liner — the entire point of the feature), one dead spec
requirement, one inverted keyboard control, and zero contrast coverage on a
brand-new overlay.

## C1 — stop 4's modal opened underneath the tour

**Fix (the "cleanest" option, as invited by the review):** `TourSpotlight.jsx`
now exits the tour the instant the SPOTLIT anchor control itself is clicked
— a document-level `click` listener (bubble phase, so the real control's own
`onClick` fires first) checks whether the event target is the current
stop's `[data-tour-id]` element and, if so, calls `onExit()`. The user has
done the thing the stop was teaching; nothing stays dimmed above whatever
that control opened. This is general (works for any stop's anchor, not
special-cased to stop 4), and does not require restacking any app modal's
z-index — no change to `cortex-console.css` was needed.

Test: `TourSpotlight.test.jsx` — "activating the SPOTLIT anchor control
exits the tour (C1)" + a negative control ("does NOT exit when a click
lands elsewhere").

## C2 — lazy-load race silently deleted stops 2 and 4

**Fix:** `useTour.js` gained `waitForAnchor(anchorId, timeoutMs)` —
resolves `true` the moment `[data-tour-id]` lands via `MutationObserver`,
`false` once a bounded timeout (`anchorTimeoutMs`, default 1500ms) elapses.
`next()`/`prev()`/`start()` now navigate to a candidate stop's destination
and **await** its anchor before deciding to land there or skip — replacing
the old synchronous `document.querySelector` probe. A skip now logs at
`console.debug` (spec §6), which it never did before.

Test: `useTour.test.jsx` "C2 — anchor wait window" — one test appends the
anchor 15ms into a 40ms wait window and asserts the tour lands on it rather
than skipping; one asserts the bounded skip still happens (and is logged)
when the anchor never appears.

## I3 — `prev()` deterministically skipped stops 2 and 4

**Fix:** `prev()` now shares the same navigate-then-await contract as
forward progress (`search()` walks in either direction), instead of probing
the CURRENT destination's DOM without navigating first.

Test: `useTour.test.jsx` "I3 — prev() re-mounts a destination it navigates
back into" — walks forward to stop `c`, removes stop `b`'s anchor from the
DOM (simulating its lazy surface having unmounted), then asserts `prev()`
re-navigates to `b`'s destination (via the mock `onNavigate`, which
re-mounts the anchor) and lands there rather than skipping to `a`.

## I4 — "any navigation the tour did not initiate → exit" was never implemented

**Fix:** `AppShell.jsx` now tracks the destination the TOUR itself most
recently requested (`tourNavTargetRef`, set by a `tourNavigate` wrapper
around the real `onNavigate`). A new effect compares the incoming
`destination` PROP against that ref while the tour is active; a mismatch
(the persistent nav rail, ⌘K, or anything else navigated instead) calls
`tour.exit()`.

Test: `appShellTour.test.jsx` "AppShell tour — exits on navigation it did
not initiate (I4)" — a controlled wrapper renders `AppShell` with real
`destination` state, clicks `dest-button-agents` directly (bypassing the
tour), and asserts the tour unmounts and marks seen.

## I5 — Enter was globally hijacked as "next"

**Fix:** `TourSpotlight.jsx`'s keydown handler now only treats Enter as
"advance" when the focused element is NOT a `<button>` inside the bubble —
Skip/Back/Next/Done keep their own native Enter-activates-click behavior.

Test: `TourSpotlight.test.jsx` — Enter on Skip calls `onExit` not `onNext`;
Enter on Back calls `onPrev` not `onNext`; Enter on the dialog itself (no
button focused) still advances. Uses `@testing-library/user-event`, since
`fireEvent.keyDown` does not synthesize a button's default Enter-activation
in jsdom — exactly why nothing caught this before.

## I6 — `FirstUseHint` shipped dead

**Wired:** Launch (`LaunchView.jsx`) and Abort (`TelemetryStrip.jsx`) — the
two most consequential of the five named controls (spec §5: Arm · Launch ·
Abort · Reconcile · Export POV). Each hint clears permanently on USE (the
click that fires the action), never on its own dismiss (×), per §5's
"cleared ... not when the bubble is dismissed" contract — dismiss is
separate, session-local component state.

**Not wired:** Reconcile and Export POV. This is a deliberate scope
decision (the task asked for "at least Launch and Abort"), not a judgment
that either control is unwireable — both are ordinary buttons and would
follow the identical `onUse()`-in-the-click-handler pattern. Left for a
follow-up pass rather than silently dropped.

Test: `FirstUseHintWiring.test.jsx` (new) — hint shows on a fresh profile,
clicking Launch/Abort clears it permanently (localStorage key set, survives
remount), dismissing via × hides it for the mount but does not persist.

## M7 — zero contrast coverage on the tour's own overlay

Two real AA failures found and fixed, not just coverage added:

- **`.tour__progress`** (cortex-theme.css): `var(--cortex-steel)` →
  `var(--tx2)`, a THEME-RELATIVE token, was paired with `.tour__bubble`'s
  ALWAYS-dark `--ink` background. Light theme measured **2.92:1** (matches
  the review's 2.93:1 finding). Fixed to `var(--ink-tx2)` — this
  codebase's existing "muted text on always-dark ink chrome" token (already
  used by `ttps.css .ttpb-pre`, `run-detail.css`), same value in both
  themes because the surface it pairs with doesn't change either.
- **`.first-use-hint`**: `var(--cortex-teal)` (→ `--ac`, the accent GREEN
  in this redesign) on the ordinary light page background measured
  **2.80:1** — an accent color tuned for large/graphic use failing at 11px
  body text, the same "Defect 3" class the console-redesign-repair task
  explicitly flagged elsewhere. Fixed to `var(--tx2)` (guaranteed-legible
  secondary text); the teal-tinted background/border stay as the visual
  "info" cue.

Added `TOUR_OVERLAY_FIXTURES` (4 fixtures × 2 themes = 8 new tests) to
`console-contrast.test.jsx`: `.tour__progress`, `.tour__body` (already
passed — hardcoded `#d7e3ec` on `--ink`), `.term__tip` (already passed,
same pairing), and `.first-use-hint`. The hint's own background is
translucent (`rgba(0,192,232,.12)`), which `contrastRatio.js::parseColor`
deliberately refuses to score without compositing — its fixture uses the
same `bgSelector: null` → shell/page-background convention the file
already established for the bare-title fixture.

## RED → GREEN evidence

Every fix above was verified failing on the pre-fix code (`git stash` of
just that file, rerun, restore) before being confirmed green:

| Fix | RED (unfixed) | GREEN (fixed) |
|---|---|---|
| C1 | 3 failed (2× I5 also unfixed at that point, 1× C1) | 18/18 `TourSpotlight.test.jsx` |
| C2 | 2 failed (`C2 — anchor wait window` block) | 11/11 `useTour.test.jsx` |
| I3 | 1 failed (`I3 — prev() re-mounts...`) | 11/11 `useTour.test.jsx` |
| I4 | 1 failed (timed out waiting for exit) | 4/4 `appShellTour.test.jsx` |
| I5 | 2 failed (Skip/Back Enter tests) | 18/18 `TourSpotlight.test.jsx` |
| I6 | 6/6 failed (`window.localStorage` undefined pre-wiring, then all 6 assertion-failed once stubbed) | 6/6 `FirstUseHintWiring.test.jsx` |
| M7 | 2/22 failed (`.tour__progress` 2.92:1, `.first-use-hint` 2.80:1) | 22/22 `console-contrast.test.jsx` |

## Final state

- `cd ui && npx vitest run` — **74 files / 760 tests, all passing** (baseline
  was 66/715 — net +8 files... actually file count includes files created by
  a concurrent workstream sharing this tree; the onboarding-specific delta is
  +1 new file (`FirstUseHintWiring.test.jsx`) and +34 tests across the tour
  suite: useTour (+3), TourSpotlight (+6), appShellTour (+1),
  console-contrast (+8), FirstUseHintWiring (+6, new file), for a total of
  24 net-new tests directly attributable to this pass; the remainder of the
  file/test-count delta versus the stated 66/715 baseline belongs to other
  concurrent work already landed in this shared tree at the time this pass
  ran).
- `cd ui && npm run build` — succeeds.
- No changes to `core/`, `agent/`, `deploy/`, `scenarios/`.
- No changes to `ui/src/styles/cortex-console.css` or the theme toggle in
  `AppShell.jsx` (owned by another workstream) — the only z-index-adjacent
  question (C1) was resolved without touching that file, per the task's
  preference.
