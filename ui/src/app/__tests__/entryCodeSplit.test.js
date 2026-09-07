/**
 * entryCodeSplit.test.js — locks the entry-chunk code split so it cannot
 * silently regress.
 *
 * WHY THIS MATTERS: `main.jsx` renders exactly ONE root per session — the
 * dark Mission Ops Console (`AppConsole`) by default, or the legacy
 * light-theme `App` under `?theme=legacy`. For a long time it *statically*
 * imported BOTH, then picked one at runtime, so every console visitor
 * downloaded the entire legacy UI (≈2,440 lines across 9 legacy-only
 * components: PlaneSelector, ScenarioBrowser, UCTCMapper, LaunchPanel,
 * ToolStatusPanel, ResultsViewer, MitreHeatmap, InfraGenerator,
 * ResultsValidationWizard) they would never see — baked into the entry
 * chunk.
 *
 * The fix is asymmetric ON PURPOSE. The console is the DEFAULT root ~every
 * session renders, so it stays a STATIC import: it rides the entry chunk and
 * is fetched immediately, with no "load a shim, then fetch the real root"
 * serial round-trip. Only the legacy App — the rarely-used escape hatch — is
 * `React.lazy`, so its private component tree is a separate chunk that loads
 * solely when a session opts into `?theme=legacy`, never for a console
 * session. Making BOTH lazy (the tempting "symmetry") would re-add exactly
 * that entry→root waterfall for the 99% case, which is why the console-static
 * assertion below is a guard, not an accident.
 *
 * That same legacy static import was also why Rollup could not hoist
 * `EalConsole` into its own chunk: a module that is BOTH statically imported
 * (by legacy `App.jsx`) and dynamically imported (by `destinations.jsx`)
 * "will not move into another chunk" — it folds into the static importer's
 * chunk and the dynamic `import()` is a no-op. Making `App.jsx`'s EalConsole
 * import dynamic too makes EalConsole purely dynamic → its own shared chunk,
 * no build warning.
 *
 * These are source-level assertions (not a bundle-size probe) because the
 * cause is exact and textual: a static `import Foo from '...'` at the top of
 * an entry module. Guard the cause, and the size win follows deterministically
 * from Rollup's chunking rules.
 */
import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'

const SRC_DIR = path.resolve(__dirname, '../..')

/** Strip line + block comments so a mention of `import App` inside prose
 * (main.jsx and App.jsx both carry doc comments naming these modules) can
 * never satisfy or defeat an assertion. */
function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/[^\n]*/g, '$1')
}

function read(rel) {
  return stripComments(fs.readFileSync(path.join(SRC_DIR, rel), 'utf8'))
}

describe('entry-chunk code splitting', () => {
  const mainSrc = read('main.jsx')

  it('keeps the console root a STATIC entry import (no entry→root waterfall)', () => {
    // The default root must be in the entry chunk, fetched immediately — NOT
    // behind a lazy() that forces the browser to run a shim before it can even
    // request the console. Guarding against a well-meaning "make both lazy".
    expect(mainSrc).toMatch(/import\s+AppConsole\s+from\s+['"]\.\/AppConsole\.jsx['"]/)
    expect(mainSrc).not.toMatch(/lazy\(\s*\(\s*\)\s*=>\s*import\(\s*['"]\.\/AppConsole\.jsx['"]\s*\)\s*\)/)
  })

  it('lazy-loads the legacy root (no static App import)', () => {
    // This is the big one: the legacy tree must not ride in the entry chunk.
    expect(mainSrc).not.toMatch(/import\s+App\s+from\s+['"]\.\/App\.jsx['"]/)
    expect(mainSrc).toMatch(/import\(\s*['"]\.\/App\.jsx['"]\s*\)/)
  })

  it('mounts the chosen root under a Suspense boundary', () => {
    // The legacy root is lazy, so main.jsx MUST wrap the render in Suspense or
    // a ?theme=legacy first paint throws a "lazy component suspended" error.
    expect(mainSrc).toMatch(/<Suspense/)
  })

  it('legacy App.jsx imports EalConsole dynamically, not statically', () => {
    // The dual static+dynamic import of EalConsole is what blocked Rollup
    // from giving it its own chunk. App.jsx must reach it via import().
    const appSrc = read('App.jsx')
    expect(appSrc).not.toMatch(/import\s+EalConsole\s+from\s+['"]\.\/components\/EalConsole\.jsx['"]/)
    expect(appSrc).toMatch(/import\(\s*['"]\.\/components\/EalConsole\.jsx['"]\s*\)/)
  })
})
