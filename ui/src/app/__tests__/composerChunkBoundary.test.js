/**
 * composerChunkBoundary.test.js — proves @xyflow/react stays out of the
 * entry chunk, instead of merely asserting it by source-level convention.
 *
 * `entryCodeSplit.test.js` guards the import SHAPE that makes lazy-loading
 * possible (main.jsx statically imports AppConsole, dynamically imports the
 * legacy App) — but it never inspects a built bundle, so it cannot see
 * whether a *dependency* (like @xyflow/react, added for the Composer's
 * Design lens in Task 8) actually landed where the import shape implies. A
 * requirement that is described as tested but never actually exercised is
 * exactly the failure class this repo calls "authored is not proven" — see
 * CLAUDE.md's UC/TC section. This file closes that gap: it runs a REAL
 * production build and inspects the REAL emitted chunks.
 *
 * The marker used to detect React Flow's presence is `react-flow__pane` — a
 * literal DOM className React Flow assigns to its own pane container at
 * runtime, so it survives minification (Terser renames identifiers, not
 * string literals actually assigned to `className`). Verified present in
 * the Composer chunk and absent from the entry chunk on the tree as of this
 * write (`ComposerView-*.js` 72.33 kB -> 252.64 kB after adding
 * @xyflow/react; `index-*.js` unchanged at 98.79 kB raw).
 */
import { describe, it, expect, beforeAll } from 'vitest'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'

const UI_ROOT = path.resolve(__dirname, '../../..')
const VITE_BIN = path.join(UI_ROOT, 'node_modules', '.bin', 'vite')
const DIST_ASSETS = path.join(UI_ROOT, 'dist', 'assets')

// A literal, xyflow-only runtime string — not an identifier, so minification
// cannot rename it away. Chosen over a raw byte-size threshold because a
// size assertion only proves "something changed," not "what changed."
const REACT_FLOW_MARKER = 'react-flow__pane'

function assetFiles() {
  return fs.readdirSync(DIST_ASSETS)
}

function findChunk(files, prefix, ext) {
  return files.find((f) => f.startsWith(prefix) && f.endsWith(ext))
}

describe('production bundle — React Flow stays out of the entry chunk', () => {
  // A real `vite build` — the same command the `ui` CI job and Docker's
  // `ui-builder` stage run. Explicit `--mode production` + `NODE_ENV`
  // override: vitest sets `process.env.NODE_ENV = 'test'` on ITS OWN
  // process, which this child process otherwise inherits — that flips
  // React and friends into their dev builds (unminified, ~2-3x larger;
  // `vendor-react-*.js` was measured at 313 kB instead of the real 140.91 kB
  // production build before this fix), which would both under-prove the
  // entry-chunk-size assertion below and risk masking the very marker check
  // this file exists for. Generous timeout: a cold build on a loaded CI
  // runner can take longer than the ~700ms measured locally.
  beforeAll(() => {
    execFileSync(VITE_BIN, ['build', '--mode', 'production'], {
      cwd: UI_ROOT,
      stdio: 'pipe',
      env: { ...process.env, NODE_ENV: 'production' },
    })
  }, 60000)

  it('puts the React Flow marker in the Composer chunk, never the entry chunk', () => {
    const files = assetFiles()
    const entryFile = findChunk(files, 'index-', '.js')
    const composerFile = findChunk(files, 'ComposerView-', '.js')
    expect(entryFile, 'entry chunk (index-*.js) not found in dist/assets').toBeTruthy()
    expect(composerFile, 'Composer chunk (ComposerView-*.js) not found in dist/assets').toBeTruthy()

    const entrySrc = fs.readFileSync(path.join(DIST_ASSETS, entryFile), 'utf8')
    const composerSrc = fs.readFileSync(path.join(DIST_ASSETS, composerFile), 'utf8')

    expect(composerSrc).toContain(REACT_FLOW_MARKER)
    expect(entrySrc).not.toContain(REACT_FLOW_MARKER)
  })

  it('keeps the entry chunk small — nothing library-sized snuck in unlazy', () => {
    const files = assetFiles()
    const entryFile = findChunk(files, 'index-', '.js')
    const { size } = fs.statSync(path.join(DIST_ASSETS, entryFile))
    // Measured pre-Task-8 baseline was 98.79 kB raw. Generous headroom for
    // unrelated future growth; still tight enough to catch a whole library
    // (React Flow alone added ~180 kB to the Composer chunk) landing here.
    expect(size).toBeLessThan(150 * 1024)
  })
})
