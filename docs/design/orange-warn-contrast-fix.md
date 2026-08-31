# D-1/D-2/D-3 — payload-blocker warning contrast fix

2026-08-31. Closes the last defect blocking customer-facing lab use, found by an
independent re-measurement of the console's contrast guard.

## D-1 — `--orange-ink` (additive token)

`ui/src/styles/cortex-tokens.css` gains `--orange-ink` (light `#CE2F05`, dark
aliased to `--orange`), same pattern as the existing `--ink-*`/`--ac-ink`
additive tokens: hue+saturation preserved, lightness reduced only as far as
needed to clear 4.5:1 against every light surface. `--orange` itself is
untouched — its border/fill roles already clear the 3:1 non-text floor
(7.02–7.62:1).

`ui/src/styles/destinations/adapters.css`'s seven `color: var(--orange)` sites
now read `color: var(--orange-ink)`. The two `border-color`/`border` sites on
the same rules (lines 125, 392 as of this commit) are unchanged.

**Measured, light theme (`#FA582D` → `#CE2F05`):**

| surface | before | after |
|---|---:|---:|
| `--s0` #F1F3F2 | 2.90:1 | 4.67:1 |
| `--s1` #FFFFFF | 3.23:1 | 5.21:1 |
| `--s2` #F8FAF9 | 3.08:1 | 4.97:1 |
| `--s3` #EBF0ED | 2.80:1 | 4.52:1 |

**Dark theme (`#FF7A54`, unchanged — already passing):**

| surface | ratio |
|---|---:|
| `--s0` | 7.62:1 |
| `--s1` | 7.02:1 |
| `--s2` | 6.44:1 |
| `--s3` | 5.93:1 |

All seven sites (`.payload-banner__tag`, `.chip--pending`,
`.adapter-schema__type`, `.provenance__unpinned`, `.stage-dialog__warn`,
`.launch-blockers__item`, `.payload-compose__warn`) now clear AA in both
themes.

## D-2 — the contrast guard

`ui/src/styles/__tests__/console-contrast.test.jsx`:

1. Imports `../destinations/adapters.css` (already in `vitest.config.js`'s
   `css.include`, but never imported here — the config's claim was silently
   false).
2. Adds `ADAPTERS_ORANGE_WARN_FIXTURES` (7 entries, real markup pulled from
   the mounting JSX, run in both themes via the existing `describe.each`) —
   the D-1 regression guard.
3. Adds `DESTINATION_CANARIES` — one canary selector per claimed destination
   (uctc/ttps/readiness/eal/adapters), each asserting the resolved `color`
   is EXACTLY the token that destination's own CSS sets it to (not merely
   truthy — `.theme-console` itself sets `color: var(--c-text)`, so a
   stubbed sheet still resolves a non-null, AA-passing color by inheritance;
   only an exact-value check catches that).
4. Adds a static, once-only "guard coverage integrity" `describe` block that
   parses `vitest.config.js`'s `css.include` regex and this file's own
   imports and asserts the destination-name sets are identical, and that
   every claimed destination has a `DESTINATION_CANARIES` entry.

**Guard now genuinely covers 5 of 13 destination stylesheets**
(`uctc.css`, `ttps.css`, `readiness.css`, `eal.css`, `adapters.css`), same 5
the guard claimed before this fix — the fix is that all 5 are now actually
imported and exact-value-verified, not that the claimed scope grew. The
other 8 (`agents.css`, `coverage.css`, `environments.css`, `library.css`,
`run-detail.css`, `tenants.css`, `ttp-detail.css`, `uc-detail.css`) are still
stubbed to empty CSS by `vitest.config.js`'s default `css: false` and carry
no contrast coverage — out of scope for this task, noted below.

## RED/GREEN evidence

**D-1 fixtures, RED** (`--orange-ink` reverted to `--orange` in
`adapters.css`, `npx vitest run` on the contrast test): 8 failures — the 7
named light-theme fixtures at their exact pre-fix ratios (e.g.
`.payload-banner__tag` "color #FA582D on background #FFFFFF measures
3.23:1, below the WCAG AA floor of 4.5:1"; `.provenance__unpinned` and
`.launch-blockers__item` / `.payload-compose__warn` at 2.90:1 on `--s0`;
`.adapter-schema__type` at 3.08:1 on `--s2`) plus the D-2 canary for
`adapters.css` (expected `#CE2F05`, got `#FA582D`). Dark-theme copies of the
same 7 fixtures stayed green, as expected (`--orange` already clears AA in
dark). Restored → **GREEN, 72/72** in the contrast file.

**D-2 vacuity check, RED** (`adapters.css` stubbed to one comment line,
`--orange-ink` restored): the `adapters.css` canary failed in **both**
themes (light: expected `#CE2F05`, got `#7E5006`; dark: expected `#FF7A54`,
got `#F5A524` — both fell back to `--ink-pending`, an unrelated inherited
token) — proving the exact-match canary catches a stub the ratio-based D-1
fixtures do **not**: all 7 D-1 fixtures stayed green under the same stub
(their fallback color happened to still pass AA — the exact vacuity failure
mode the task described). Restored → **GREEN, 72/72**.

Full suite after restore: `npx vitest run` → **78 files / 834 tests**
passed (808 baseline + 26 new: 7 D-1 fixtures × 2 themes + 5 canaries × 2
themes + 2 static coverage tests). `npm run build` succeeds.

## D-3 — token file header correction

`ui/src/styles/cortex-tokens.css`'s deviation-block comment claimed *"Dark
values for both are untouched — already >=4.5:1 as authored."* Measured:

| token | `--s0` | `--s1` | `--s2` | `--s3` | `--ac-soft` |
|---|---:|---:|---:|---:|---:|
| dark `--warn` (`#FFCB06`) | 12.88 | 11.86 | 10.88 | 10.03 | 9.87 |
| dark `--tx3` (`#77867E`) | 5.13 | 4.72 | **4.33** | **3.99** | **3.93** |

`--warn` is true as stated. `--tx3` is not — it clears 4.5:1 only against
`--s0`/`--s1`, and falls short against `--s2`, `--s3`, and `--ac-soft`.
Corrected the comment to name exactly which surfaces each token holds
against, rather than asserting a blanket "both." Per the task instructions,
the dark values themselves were **not** changed — only the comment. Current
practical impact is one dark `:hover` rule; not fixed here.

## Not fixed (out of scope for this task)

- The 8 destination stylesheets the guard doesn't cover
  (`agents.css`, `coverage.css`, `environments.css`, `library.css`,
  `run-detail.css`, `tenants.css`, `ttp-detail.css`, `uc-detail.css`) carry
  no contrast fixtures and are still stubbed to empty CSS by
  `vitest.config.js`. Widening `css.include` and adding fixtures for those
  is real remaining work, not silently closed by this pass.
- Dark `--tx3`'s sub-4.5:1 ratios against `--s2`/`--s3`/`--ac-soft` are
  documented, not remediated — no live `color:` use hits those pairs today.

## Files changed

- `ui/src/styles/cortex-tokens.css` — `--orange-ink` token (D-1), corrected
  deviation-block comment (D-3).
- `ui/src/styles/destinations/adapters.css` — 7 sites switched to
  `--orange-ink` (D-1).
- `ui/src/styles/__tests__/console-contrast.test.jsx` — adapters.css import,
  D-1 regression fixtures, D-2 vacuity guard (D-2).
