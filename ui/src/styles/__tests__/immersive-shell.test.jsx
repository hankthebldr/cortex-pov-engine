/**
 * immersive-shell.test.jsx — the immersive-mode layout rules in
 * povengine-shell.css must beat cortex-console.css by SPECIFICITY.
 *
 * The live workspace layout is cortex-console.css (`.theme-console .workspace`
 * / `.main` / `.view`, all (0,2,0)). povengine-shell.css happens to load after
 * it today (main.jsx), so a same-specificity override would "work" — until a
 * sheet is reordered, split or code-split, at which point the Composer quietly
 * gets its 202px rail column, 24px padding and page scroll back. Two layers:
 *
 *  1. Cascade. The real cortex-console.css is loaded (vitest `css.include`);
 *     povengine-shell.css is injected BEFORE it in document order, so every
 *     assertion below can only pass on specificity. Resolved with the repo's
 *     own cssCascade.js (jsdom's getComputedStyle ignores specificity).
 *  2. Static sweep. cssCascade skips @media blocks, and the <=1000px block is
 *     where cortex-console.css re-forces a 62px rail column and pins `.main`
 *     to `grid-column: 2`. So every selector in the eager global sheets whose
 *     subject is one of these elements — media blocks included — is checked
 *     against the immersive selector for the same element.
 */
import { describe, it, expect, beforeAll, afterAll } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import '../cortex-tokens.css'
import '../cortex-theme.css'
import '../cortex-console.css'
import { resolveProperty, invalidateRuleCache } from '../../test/cssCascade.js'

const STYLES_DIR = path.resolve(__dirname, '..')
const SHELL_CSS = path.join(STYLES_DIR, 'povengine-shell.css')
// The eager, global sheets main.jsx loads for every destination. Destination
// sheets (styles/destinations/*) are scoped to their own surface.
const GLOBAL_SHEETS = ['cortex-console.css', 'povengine-shell.css', 'povengine-surfaces.css', 'povengine-minimal.css']

// Same selector and specificity as cortex-console.css's `.view` padding rule.
// If the injected sheet really is EARLIER in cascade order, cortex-console's
// 24px wins this tie — which the control test asserts, proving the ordering
// every other assertion here relies on.
const ORDER_PROBE = '\n.theme-console .view { padding: 7px; }\n'

let injected = null
beforeAll(() => {
  injected = document.createElement('style')
  injected.setAttribute('data-test', 'povengine-shell')
  injected.textContent = fs.readFileSync(SHELL_CSS, 'utf8') + ORDER_PROBE
  // EARLIER in cascade order than cortex-console.css, so nothing below can win
  // on order. jsdom orders `document.styleSheets` by insertion, not by DOM
  // position (a `prepend` lands LAST), so the already-imported sheets are
  // detached and re-inserted after this one.
  const imported = Array.from(document.head.querySelectorAll('style'))
  imported.forEach((n) => n.remove())
  document.head.appendChild(injected)
  imported.forEach((n) => document.head.appendChild(n))
  invalidateRuleCache()
})
afterAll(() => {
  injected?.remove()
  invalidateRuleCache()
  document.body.innerHTML = ''
})

function mountShell({ immersive }) {
  document.body.innerHTML = `
    <div class="theme-console pov-shell shell shell--no-phasebar${immersive ? ' pov-shell--immersive' : ''}" data-theme="dark">
      <div class="pov-workspace workspace${immersive ? ' pov-workspace--immersive' : ''}">
        <main class="pov-main main" id="cortexsim-main">
          <div class="view">
            <div class="api-down" data-testid="banner"></div>
            <div class="surface-root" data-testid="surface"></div>
          </div>
        </main>
      </div>
    </div>`
  const q = (s) => document.body.querySelector(s)
  return {
    workspace: q('.workspace'),
    main: q('.main'),
    view: q('.view'),
    banner: q('[data-testid="banner"]'),
    surface: q('[data-testid="surface"]'),
  }
}

describe('immersive layout wins the cascade on specificity alone', () => {
  it('control: without the immersive classes the cortex-console layout applies', () => {
    const { workspace, view } = mountShell({ immersive: false })
    // cssCascade substitutes only a whole-value var(), so the rail track stays symbolic.
    expect(resolveProperty(workspace, 'grid-template-columns')).toBe('var(--w-rail) 1fr')
    // 24px, not the ORDER_PROBE's 7px: the injected shell sheet loses a tie.
    expect(resolveProperty(view, 'padding')).toBe('24px')
    expect(resolveProperty(view, 'overflow-y')).toBe('auto')
  })

  it('the workspace drops the rail column and does not animate the mode change', () => {
    const { workspace } = mountShell({ immersive: true })
    expect(resolveProperty(workspace, 'grid-template-columns')).toBe('minmax(0, 1fr)')
    expect(resolveProperty(workspace, 'transition')).toBe('none')
  })

  it('main spans the whole row and gives its lone view a filling row', () => {
    const { main } = mountShell({ immersive: true })
    expect(resolveProperty(main, 'grid-column')).toBe('1 / -1')
    expect(resolveProperty(main, 'grid-template-rows')).toBe('minmax(0, 1fr)')
  })

  it('the view is unpadded, does not scroll, and is a flex column on the page surface', () => {
    const { view } = mountShell({ immersive: true })
    expect(resolveProperty(view, 'padding')).toMatch(/^0(px)?$/)
    expect(resolveProperty(view, 'overflow-y')).toBe('hidden')
    expect(resolveProperty(view, 'overflow-x')).toBe('hidden')
    expect(resolveProperty(view, 'display')).toBe('flex')
    expect(resolveProperty(view, 'flex-direction')).toBe('column')
    expect(resolveProperty(view, 'background')).toBeTruthy()
  })

  it('banners keep their height; the surface (last child) takes the remainder', () => {
    const { banner, surface } = mountShell({ immersive: true })
    expect(resolveProperty(banner, 'flex')).toMatch(/^(none|0 0 auto)$/)
    expect(resolveProperty(surface, 'flex')).toMatch(/^1 1 auto$/)
    expect(resolveProperty(surface, 'min-height')).toMatch(/^0(px)?$/)
  })
})

// ── Static sweep, @media blocks included ────────────────────────────────────

function blankComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
}

/** Every selector in a sheet (media/supports blocks flattened in). */
function selectorsOf(css) {
  const out = []
  const re = /([^{}@;]+)\{/g
  let m
  while ((m = re.exec(blankComments(css)))) {
    const text = m[1].trim()
    if (!text || /^(from|to|\d+%)/.test(text)) continue
    for (const s of text.split(',')) {
      const sel = s.trim()
      if (sel) out.push(sel)
    }
  }
  return out
}

/** (a,b,c) specificity; `:has()/:is()/:not()` count as their argument. */
function specificity(selector) {
  let s = selector.replace(/:(?:has|is|not)\(([^()]*)\)/g, (_, inner) => ` ${inner.replace(/^[\s>+~]+/, '')} `)
  const a = (s.match(/#[\w-]+/g) || []).length
  const pseudoEl = (s.match(/::[\w-]+/g) || []).length
  s = s.replace(/::[\w-]+/g, ' ')
  const b = (s.match(/\.[\w-]+|\[[^\]]*\]|:[\w-]+(\([^)]*\))?/g) || []).length
  s = s.replace(/#[\w-]+|\.[\w-]+|\[[^\]]*\]|:[\w-]+(\([^)]*\))?/g, ' ')
  const c = (s.match(/(^|[\s>+~])[a-zA-Z][\w-]*/g) || []).length + pseudoEl
  return [a, b, c]
}
const cmp = (x, y) => (x[0] - y[0]) || (x[1] - y[1]) || (x[2] - y[2])

/** The subject (rightmost) compound of a selector, parenthesised args removed. */
function subjectOf(selector) {
  const flat = selector.replace(/\([^()]*\)/g, '()')
  const parts = flat.split(/\s*[>+~]\s*|\s+/).filter(Boolean)
  return parts[parts.length - 1] || ''
}

const ELEMENTS = {
  workspace: { classRe: /\.(workspace|workspace--rail-collapsed)(?![\w-])/, immersive: '.theme-console .workspace.pov-workspace--immersive' },
  main: { classRe: /\.main(?![\w-])/, immersive: '.theme-console .pov-workspace--immersive > .main' },
  view: { classRe: /\.view(?![\w-])/, immersive: '.theme-console .pov-workspace--immersive > .main > .view' },
}

describe('static sweep: no global-sheet rule out-specifies the immersive overrides', () => {
  const sheets = GLOBAL_SHEETS.map((f) => ({ file: f, css: fs.readFileSync(path.join(STYLES_DIR, f), 'utf8') }))

  it('the immersive selectors exist, including inside the <=1000px block', () => {
    const shell = blankComments(fs.readFileSync(SHELL_CSS, 'utf8'))
    for (const { immersive } of Object.values(ELEMENTS)) {
      expect(shell, `missing ${immersive}`).toContain(immersive)
    }
    const media = shell.slice(shell.indexOf('@media (max-width: 1000px)'))
    expect(media).toContain(ELEMENTS.workspace.immersive)
    expect(media).toContain(ELEMENTS.main.immersive)
  })

  for (const [name, { classRe }] of Object.entries(ELEMENTS)) {
    it(`every immersive ${name} rule (media blocks included) out-specifies every competing ${name} rule`, () => {
      // Read from the file, never hardcoded: weakening a selector in the CSS
      // must fail here, not pass against a copy of the old selector.
      const mine = selectorsOf(fs.readFileSync(SHELL_CSS, 'utf8'))
        .filter((sel) => sel.includes('immersive') && classRe.test(subjectOf(sel)))
      expect(mine.length, `no immersive ${name} rule found`).toBeGreaterThan(0)

      const competitors = []
      for (const { file, css } of sheets) {
        for (const sel of selectorsOf(css)) {
          if (sel.includes('immersive')) continue
          if (!classRe.test(subjectOf(sel))) continue
          competitors.push({ file, sel, spec: specificity(sel) })
        }
      }
      // Sanity: the rules this file exists to beat must have been found, or
      // the sweep is silently checking nothing.
      expect(competitors.length, `no competing ${name} rules found`).toBeGreaterThan(0)

      const failures = []
      for (const m of mine) {
        const ms = specificity(m)
        for (const c of competitors) {
          if (cmp(c.spec, ms) >= 0) failures.push(`${m} (${ms}) does not beat ${c.file}: ${c.sel} (${c.spec})`)
        }
      }
      expect(failures, failures.join('\n')).toEqual([])
    })
  }
})
