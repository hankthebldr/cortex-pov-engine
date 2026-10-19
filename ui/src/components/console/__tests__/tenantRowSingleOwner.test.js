/**
 * Regression guard for a merge-residue CSS bug jsdom cannot see.
 *
 * `.tenant-mgr__row` was defined in TWO stylesheets at equal specificity:
 * the pre-refactor `cortex-console.css` set `display:grid` + a 4-column
 * template, and the new card design in `destinations/tenants.css` set only
 * background/border/padding — no `display`. Because the cascade is per-PROPERTY,
 * the new card silently inherited the stale grid, which crushed the base-URL
 * cell to ~1 character per line and blew the row to ~1000px tall. No text line
 * conflicted, so the merge kept both; vitest/jsdom computes no layout, so no
 * test caught it. This asserts the source-level invariant that made it safe:
 * exactly ONE stylesheet may set `display` on `.tenant-mgr__row`.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const STYLES = resolve(dirname(fileURLToPath(import.meta.url)), '../../../styles')
const FILES = ['cortex-console.css', 'destinations/tenants.css']

// Count stylesheets whose `.tenant-mgr__row { ... }` rule sets `display`.
function sheetsSettingRowDisplay() {
  const hits = []
  for (const rel of FILES) {
    const css = readFileSync(resolve(STYLES, rel), 'utf8')
    // match the exact selector rule block (not --active / -top / -name etc.)
    const re = /\.tenant-mgr__row\s*\{([^}]*)\}/g
    let m
    while ((m = re.exec(css))) {
      if (/(^|[;{\s])display\s*:/.test(m[1])) { hits.push(rel); break }
    }
  }
  return hits
}

describe('.tenant-mgr__row has a single display owner', () => {
  it('exactly one stylesheet sets display on the row (no cross-file grid leak)', () => {
    const owners = sheetsSettingRowDisplay()
    expect(owners).toEqual(['destinations/tenants.css'])
  })
})
