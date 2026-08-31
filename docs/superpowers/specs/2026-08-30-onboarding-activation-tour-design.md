# First-run activation tour + ambient guidance — design

> **Date:** 2026-08-30 · **Status:** approved, not yet implemented
> **Branch:** `feature/ui-onboarding-activation-tour`
> **Surface:** `ui/src/components/onboarding/` (new), `AppShell.jsx`, `HelpOverlay.jsx`

## 1. Problem

A Domain Consultant opening the CortexSim console for the first time sees eleven
destinations across five groups and no indication of which one to start in, what
order the work happens in, or why nothing they click produces a run. The product
knows Cortex; it does not know CortexSim.

The specific blocker is invisible: **without an enrolled beacon, a new user can
look at everything and run nothing.** Library, TTP Cards, Tools & Payloads,
Coverage, UC/TC Index and Readiness are all fully populated from the shipped
corpus on first boot, so the console *looks* operational. Runs, Agents, Tenants
and Environments are the only empty surfaces, and the emptiness of Agents is the
one that matters — it is the reason a launch does nothing.

### Constraint from the requester

> "We want this to be more premised on product guidance for the user, we want to
> quickly enable tool use without having a required training/readiness course."

This rules out a structural walkthrough of all eleven destinations. The default
experience must reach *productive use* fast, not teach the product's map.

## 2. What already exists

Three things must be built on, not duplicated:

| Existing | State | Consequence for this design |
|---|---|---|
| `HelpOverlay` (⌘/) | Shortcuts + tab cheatsheet + About. **Auto-opens on first run** via `cortexsim.helpOverlay.seenV1`. | Collides with an auto-opening tour. Resolved in §3. |
| `HelpOverlay`'s `onTour` prop | Documented as "launch first-run tour". Passed **nowhere except tests** — the CTA never renders in production. | This design finally wires it. |
| `GuidedPovFlow` | Hidden `guided` destination ("New POV run"): arm → target → launch. Reachable from Library and ⌘K, not in nav. | The tour points *at* the real path rather than reimplementing it. |

Existing `localStorage` convention is `cortexsim.<area>.<key>`, always inside
`try/catch` (private-window and blocked-storage safety).

## 3. Decision: the tour replaces the auto-shown overlay

`HelpOverlay` and the tour cannot both auto-open — a new user would get two
stacked modals.

**On first run, the tour shows and `HelpOverlay` does not.** `HelpOverlay`
remains available on ⌘/ at any time, and gains a "Start guided tour" CTA (wiring
`onTour`) so the tour is repeatable on demand.

A single new key governs the tour: `cortexsim.onboarding.tourSeenV1`. The existing
`cortexsim.helpOverlay.seenV1` is **left in place and still consulted** — a user
who has already dismissed the overlay should not be handed a tour on their next
visit either. On mount in `AppShell`:

```js
showTour = !onboardingState.tourSeen() && shouldShowOnFirstRun()
```

`shouldShowOnFirstRun()` is `HelpOverlay`'s existing export, reused rather than
re-reading its key — otherwise that export and `markFirstRunSeen()` become
test-only, which is precisely the dead-hook pattern (`onTour`) that made this
feature necessary. `AppShell`'s existing `handleCloseHelp` keeps calling
`markFirstRunSeen()` unchanged.

**What changes in `AppShell`:** the first-run `useEffect` currently calls
`setHelpOpen(true)`. It now starts the tour instead. `HelpOverlay` no longer
auto-opens; it opens on ⌘/ and from the tour's final card.

**Accepted edge case:** a brand-new user who presses ⌘/ and closes the overlay
*before* the tour has run will have `markFirstRunSeen()` fire, suppressing the
tour. That user has demonstrably found the help surface on their own, so
suppressing a tour they did not need is the correct outcome, not a bug to work
around.

Existing users therefore see nothing new until they ask for it, and only a
genuinely fresh browser profile gets the tour.

## 4. The five stops

The critical path, ending at the actual blocker.

| # | Destination | Anchor (`data-tour-id`) | Teaches |
|---|---|---|---|
| 1 | `library` | `nav-library` | 170 scenarios across 15 detection planes — a POV starts here |
| 2 | `library` | `scenario-card-first` | a scenario is a TTP chain plus the detections it should trigger; **Arm** stages it |
| 3 | `agents` | `nav-agents` | nothing runs without a beacon — and you have none |
| 4 | `agents` | `agent-enroll` | **activation**: the copyable enrollment one-liner |
| 5 | `runs` | `nav-runs` | where evidence lands — per-detection results, MTTD, POV report |

The tour drives navigation between stops and **leaves the user on Agents holding
the one-liner**. Exiting is not a dead end; it is the next action.

Stop copy states what the surface *is for*, never how to click. Stop 5 must not
imply a run proves detection efficacy — evidence lands there, verification is a
separate, tenant-bound act.

## 5. Components

```
ui/src/components/onboarding/
  onboardingState.js    sole owner of every onboarding localStorage key, all try/catch
  tourStops.js          declarative [{ id, anchor, destination, title, body }]
  useTour.js            state machine: idle → running(index) → done
  TourSpotlight.jsx     dim layer + cutout + positioned bubble
  glossary.js           term → definition, defined exactly once
  Term.jsx              <Term k="mttd">MTTD</Term> — hover tooltip by key
  useFirstUseHint.js    one-time hint, cleared on first real use of the control
  FirstUseHint.jsx      the hint bubble
```

**Storage keys**, all owned by `onboardingState.js`:

- `cortexsim.onboarding.tourSeenV1` — `'true'` once the tour ends by any path
- `cortexsim.onboarding.hint.<controlId>` — `'true'` once that control is used

### Anchoring

Anchors resolve via explicit **`data-tour-id`** attributes. Not CSS class
selectors, and not the existing `data-testid`.

The console has already been restructured twice — `ConsoleTabs` → `ConsoleStepper`
→ `DestinationNav`. A selector-based tour would have broken silently through both.
`data-testid` is rejected as the anchor because it is a test contract that anyone
refactoring tests may rename; the tour would then break in production while the
suite stayed green.

Five attributes: `nav-library`, `nav-agents`, `nav-runs` (in `DestinationNav`),
`scenario-card-first` (Library's first card), `agent-enroll` (Agents' enroll
control).

### Vocabulary

`glossary.js` defines each term once: `MTTD`, `ABIOC`, `BIOC`, `CGO anchor`,
`tenant-verified`, `moat_tier`, `XDM substrate`, `S-13`, `detection_type`,
`push bundle`, `identity harness`. Referenced by key, never inlined as a `title=`
string, so a definition cannot drift between the several places each term appears.

### First-use hints

On the controls that carry consequences: **Arm · Launch · Abort · Reconcile ·
Export POV**. Each clears permanently the first time the control is *used*, not
when the bubble is dismissed — dismissing a hint you did not act on should not
count as having learned it.

## 6. Failure behavior

The characteristic failure of a spotlight tour is a modal pointing at nothing
with no way out. Every one of these is a required behavior, not a nicety:

- **Missing anchor → skip the stop**, log at debug, continue to the next.
- **All anchors missing → exit immediately** and mark seen.
- **Escape → exit** and mark seen.
- **Any navigation the tour did not initiate → exit** and mark seen.
- **Exiting by any path marks seen.** There is no path that re-shows the tour to
  a user who has already left it.
- `prefers-reduced-motion` → no spotlight transition animation.

## 7. Accessibility

- Bubble is `role="dialog" aria-modal="true"`; focus trapped within it; focus
  returns to the previously focused element on exit.
- Stop title is the dialog's accessible name.
- Stop changes announced via `aria-live="polite"`.
- Dim layer is `aria-hidden`; pointer events pass only through the cutout.
- The tour is fully operable by keyboard: Escape exits, Enter/Space advances.

## 8. Testing

Every test below must be observed failing before its implementation exists.

| Test | Proves |
|---|---|
| Tour does not render when `tourSeenV1` is set | the "don't show me again" contract |
| Tour does not render when `helpOverlay.seenV1` is set | existing users are not re-onboarded (§3) |
| A missing anchor **advances** to the next stop | skip, not hang — assert the advance, not merely absence of a crash |
| All anchors missing → tour unmounts and marks seen | cannot trap |
| Escape marks seen; a second mount does not re-show | no re-show loop |
| A first-use hint clears on **use**, not on dismiss | §5 semantics |
| **Guard:** every `<Term k="…">` in the tree resolves to a `glossary.js` entry | a dangling key renders an empty tooltip that looks fine and teaches nothing |

The glossary guard is this repo's "a zero is degraded, not ok" rule applied to
copy: an empty tooltip and an absent tooltip must not be the same outcome.

## 9. Out of scope

Stated so they are not silently assumed:

- **No structural eleven-destination tour.** Considered and rejected against the
  "no training course" constraint.
- **No demo-data seeding.** Unnecessary — seven of eleven surfaces are populated
  on first boot from the shipped corpus.
- **No server-side state and no user identity.** "New user" means a fresh browser
  profile. A DC on a second laptop sees the tour again; a shared demo box shows it
  once for everyone. Accepted, because the console has no identity concept and
  inventing one is a much larger change.
- **No honesty tooltips on greens.** Readiness already states `tenant-verified: 0`
  verbatim; duplicating that as tooltips was not requested.

## 10. Estimated surface

8 new files · 5 `data-tour-id` attributes · 2 touched components (`AppShell` for
the first-run swap, `HelpOverlay` for the `onTour` wire). No backend change, no
API change, no new dependency.
