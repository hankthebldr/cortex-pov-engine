# Console redesign repair — 2026-08-30

Branch: `feature/ui-onboarding-activation-tour`. Fixes Defect 1 (invisible
titles) and Defect 2 (accidental-dark shell, no toggle) from the redesign
handoff. Defect 3 (the light palette's own AA failures) is reported, not
fixed, per instruction.

## Defect 1 — cascade discarding destination titles

**Root cause.** `.theme-console h1, .theme-console h2, .theme-console h3`
(specificity 0,1,1) hardcoded `color: #E8EEF5` (via `--c-text`, itself
hardcoded, no `[data-theme]` variant). Every destination's own title rule
was a single class (0,1,0) and lost regardless of what color it declared —
`UcTcIndexView.jsx`'s `.uctc__pagehead-title` explicitly set `color:
var(--tx)` and still lost to the higher-specificity rule; `TtpBrowserView`,
`ReadinessView`, `EalConsole` never overrode `color` on their titles at
all, so they inherited the broken rule outright.

**Fix — `ui/src/styles/cortex-console.css`.**
1. The `.theme-console` color field (`--c-void/--c-surface*/--c-hairline*`,
   `--c-text/--c-text-secondary/--c-text-muted/--c-text-disabled`) is now
   **aliased onto the token layer** (`--s0..--s3`, `--bd/--bd2`,
   `--tx/--tx2/--tx3`) instead of hardcoded hex — the same pattern
   `cortex-theme.css` already uses for `--cortex-*`. `--c-text-disabled`
   maps to `--tx3` (same as `-muted`); there's no fourth text tier on the
   token layer and WCAG doesn't require contrast on disabled controls, so
   this isn't a new exposure.
2. The heading rule's `color` now reads `var(--tx)` directly rather than
   `var(--c-text)`, per the review brief — belt-and-suspenders with (1).
3. Net effect: whichever rule wins the cascade (the shared rule or a
   destination's own), both now resolve to the *same* correct,
   theme-responsive value, so the specificity trap no longer matters. This
   is the "fix it properly, don't just bump specificity" version — the
   next destination author gets a working default instead of a re-set
   trap.

## Defect 2 — no theme, an accident

**Root cause.** `.theme-console` hardcoded `--c-void: #050A14` with no
`[data-theme]` variant, and nothing in the app ever set `[data-theme]` —
so `:root` (light) was the *only* theme actually reachable, while the
shell chrome painted permanently dark regardless. Five components
(`RunDetailView`, `InflightView` ×2, `EvidenceView`, `NarrativeTimeline`,
`LabView`) self-pinned `data-theme="dark"` on their own roots as an honest
local workaround, which made the split (dark chrome, light-or-dark content
depending on which component owned it) permanent.

**Fix.**
1. `.theme-console`'s color field is theme-aware now (Defect 1's alias
   fix covers this too — `--c-void` etc. now resolve through `--s0..--s3`
   which flip with `[data-theme]`).
2. `AppShell.jsx` gained a real `colorTheme` toggle, following the exact
   `railCollapsed`/`theaterMode` pattern already in that file: `useState`
   seeded from `localStorage`, a `toggle*` callback that flips and
   persists. Key: `cortexsim.colorTheme` (defaults to `'light'`) — a
   *different* key from `cortexsim.theme` in `main.jsx`, which picks
   between the Mission Ops Console and the legacy `?theme=legacy` shell;
   this one picks the palette *within* the console shell. `data-theme` is
   applied on the shell root div only when dark. A header button
   (`ConsoleHeader.jsx`, `.theme-toggle`, styled to match the existing
   `.theater-toggle`) exposes it.
3. Removed the five components' self-pinned `data-theme="dark"`. All five
   now inherit the shell's real theme. Verified via the full test suite
   (all five have existing coverage — `console-smoke`, `TtpBrowserView`,
   `UcTcIndexView`, `appShellTour`, etc. — all still pass) and via the
   contrast guard for the destinations it directly measures.
4. Updated the stale explanatory comments in `run-detail.css` and
   `environments.css` that described the old workaround, so a future
   reader isn't pointed at a rationale that no longer holds. (One other
   destination, `coverage.css`, independently re-declared the same
   `--c-*` names scoped to itself as its own workaround for this identical
   root cause — left untouched: it's now redundant but harmless, and
   removing it wasn't asked for.)

## The guard — `ui/src/styles/__tests__/console-contrast.test.jsx`

`ui/vitest.config.js` ran with `css: false` — CSS imports become no-ops,
so no test in this repo ever rendered a stylesheet. That's exactly how a
36-agent redesign shipped invisible titles behind 701 green tests.

**jsdom limitation found during this work:** jsdom's own
`getComputedStyle` neither resolves `var()` (returns the literal string
`"var(--x)"`) nor respects CSS specificity when two rules match the same
element (empirically: it picks whichever rule is *last* in
`document.styleSheets`, regardless of specificity — verified with a
minimal repro). Naively rendering components and reading
`getComputedStyle(...).color` would not just fail to catch this defect,
it would report the *opposite* answer for it. `ui/src/test/cssCascade.js`
is a small real-stylesheet-driven resolver: it uses `document.styleSheets`
(real parsed CSS) and `Element.matches()` (real selector matching, via
jsdom's nwsapi) but computes standard CSS specificity and resolves
`var()` chains itself. `ui/src/test/contrastRatio.js` is plain sRGB
relative-luminance arithmetic — no new dependency.

**Wiring.** Rather than a second vitest project (Vitest 1.x — the version
actually installed here — doesn't auto-discover `vitest.workspace.js`
under a bare `vitest run`, so that split was found to silently not run
under this repo's/CI's real invocation), `vitest.config.js`'s `css`
option is `{ include: [...] }` scoped to exactly the stylesheets the
guard needs (`cortex-tokens.css`, `cortex-theme.css`, `cortex-console.css`,
`destinations/{uctc,ttps,readiness,eal}.css`). Every other CSS import in
the suite stays stubbed, so the rest of the suite pays no cost.

**Coverage.** 14 cases × (light, dark): the bare shell heading, and the
four destinations named in the defect report (UC/TC Index, TTP Cards,
Readiness, EAL Traffic Simulator) using markup copied verbatim from their
component JSX, plus two primary-body-text checks (`--tx2` on `--s0`/`--s1`,
deliberately *not* the `--tx3`/`--ac` combinations that are Defect 3).

**RED proof** (`cortex-console.css` reverted to the pre-fix version,
guard run in isolation):

```
 × title contrast clears AA: UC / TC Index — UcTcIndexView.jsx h1.uctc__pagehead-title
   → title color #E8EEF5 on background #F1F3F2 measures 1.05:1, below the WCAG AA
     floor of 3:1 for large text: expected 1.0480026151194979 to be greater than or equal to 3
 × title contrast clears AA: TTP Cards — TtpBrowserView.jsx bare h1 inside .ttpb .view-head
   → ...measures 1.05:1...
 × title contrast clears AA: Readiness — ReadinessView.jsx bare h1 inside .readiness header.view-head
   → ...measures 1.05:1...
 × title contrast clears AA: EAL Traffic Simulator — EalConsole.jsx h2.eal-console__title
   → ...measures 1.05:1...

 Test Files  1 failed (1)
      Tests  4 failed | 10 passed (14)
```

Note the failures are all *light*-theme titles, all measuring exactly the
task-cited 1.05:1 — and dark-theme titles pass even pre-fix, matching the
narrative ("dark is the one theme that actually worked"). The bare-shell
heading case also passes pre-fix in light mode, because it has no
destination wrapper repainting the background — it's the interaction
between a destination's own light background paint and the shell's
hardcoded-dark text that produces the catastrophe.

**GREEN proof** (fix restored):

```
 ✓ title contrast clears AA: bare .theme-console h1 ...
 ✓ title contrast clears AA: UC / TC Index ...
 ✓ title contrast clears AA: TTP Cards ...
 ✓ title contrast clears AA: Readiness ...
 ✓ title contrast clears AA: EAL Traffic Simulator ...
 ✓ primary text contrast clears AA: UC/TC Index intro prose ...
 ✓ primary text contrast clears AA: TTP Cards intro ...
 (× 2 themes)

 Test Files  1 passed (1)
      Tests  14 passed (14)
```

## Defect 3 — reported, not fixed

Not touched, per instruction: `--ac` on `--s1` (3.12:1), `--ac-str` on
`--ac-soft` (1.90:1), `--tx3` on `--s0` (2.77:1) remain exactly as
authored in `cortex-tokens.css` (byte-identical to the design reference —
verified via `git diff --stat` showing no change).

**Re-measurement.** A precise re-measurement of the *specific* elements
Defect 1 named (the four destination titles + the shared cascade rule) is
the guard above: 1.05:1 → full AA pass, both themes, using the actual
production markup and stylesheets. That's the number this task asked to
verify.

A broader, best-effort scan was also run for context (not committed — a
throwaway script reusing the same cascade resolver, walking every CSS
rule across the console's full stylesheet surface that declares a literal
`color`, built as isolated DOM fixtures per selector's compound chain).
Caveats up front: it has real methodological gaps (no accounting for
actual production DOM nesting/specificity beyond a selector's own compound
chain, includes some rules that may not be reachable in the shapes tested,
and almost certainly isn't the same tool that produced the task's cited
817/201 baseline — so the absolute numbers below are **not directly
comparable** to that baseline). Before → after, same corpus, same method:

| theme | before | after |
|---|---:|---:|
| light | 690 / 1459 checked | 758 / 1460 checked |
| dark  | 242 / 1459 checked | 72 / 1460 checked |

Dark drops sharply (**-70%**), as expected — the shell became genuinely
theme-responsive. Light going *up* is a real, explainable finding, not
tool noise: before the fix, `.theme-console`'s own background was
hardcoded dark *unconditionally* (not gated by `[data-theme]` at all), so
"light mode" never actually painted a light background behind the shell's
own base rules — meaning brand/utility colors tuned for a dark background
(`.btn-primary` white-on-accent, `.badge-*`, `.text-*` utilities, plain
`<a>` teal links) were never actually being tested against a real light
background, because light mode never rendered. After the fix, light mode
is real for the first time, and a chunk of those dark-tuned colors measure
below AA against the *correct* light background — which is squarely
Defect 3's territory (the light palette's own accent-color contrast at
these sizes), not a regression from this repair. The dedicated guard
above is the trustworthy number for what this task asked to fix; the wider
scan is offered as directional context for follow-up Defect-3 work.

## Verification

- `cd ui && npx vitest run` — **66 files / 715 tests**, all green (was 65
  files / 701 tests; net addition, no drop).
- `cd ui && npm run build` — succeeds; chunk split unchanged (same file
  names/sizes as before this change, e.g. `index-*.js` 162.90 kB raw /
  47.49 kB gzip, `RunDetailView-*.js`, `UcTcIndexView-*.js` etc. still
  their own chunks).
- `git diff --stat -- ui/src/styles/cortex-tokens.css` — empty; the token
  layer is untouched.

## Not fixed / left as-is

- Defect 3 (light palette's own AA failures) — explicitly out of scope.
- `coverage.css`'s redundant local re-alias of the same `--c-*` names —
  harmless now, not asked to remove.
- The wider scan's ~68-count light-mode increase is new *visibility* into
  pre-existing Defect-3-adjacent failures (dark-tuned brand/utility colors
  now measurable against a real light background), not a new bug from
  this repair.
