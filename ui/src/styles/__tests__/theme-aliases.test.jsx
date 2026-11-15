/**
 * The legacy --cortex-* aliases follow the shell's theme.
 *
 * cortex-theme.css maps the old names onto the token contract
 * (`--cortex-steel: var(--tx2)`, `--cortex-white: var(--s1)`, …). Declared on
 * :root only, each alias was computed once against :root's LIGHT tokens, and
 * the dark shell (`.theme-console[data-theme="dark"]`, which is where the
 * theme lives) inherited those light values. So the Storyline and Causality
 * views, which still style themselves through the aliases, rendered white
 * cards and #595959 text inside the dark console (about 2.4:1).
 *
 * These read the real stylesheets through cssCascade.js, which substitutes a
 * custom property's var() where it is declared, as a browser does. They check
 * `color`, because jsdom drops a `background:` shorthand that contains var().
 */
import { describe, it, expect, afterEach } from 'vitest'
import '../cortex-tokens.css'
import '../cortex-theme.css'
import '../cortex-console.css'
import '../../components/DetectionStoryline.css'
import '../../components/CausalityGraph.css'
import { resolveProperty, invalidateRuleCache } from '../../test/cssCascade.js'

function mountShell(theme) {
  invalidateRuleCache()
  const shell = document.createElement('div')
  shell.className = 'theme-console'
  shell.setAttribute('data-theme', theme)
  const storyline = document.createElement('section')
  storyline.className = 'storyline'
  const node = document.createElement('div')
  node.className = 'storyline-step__node'
  const legend = document.createElement('div')
  legend.className = 'cg-legend'
  shell.append(storyline, node, legend)
  document.body.appendChild(shell)
  return { storyline, node, legend }
}

afterEach(() => {
  document.body.innerHTML = ''
})

const ink = (el) => String(resolveProperty(el, 'color')).toLowerCase()

describe('legacy --cortex-* aliases follow the shell theme', () => {
  it('dark shell: storyline and causality text use the dark-theme ink', () => {
    const { storyline, node, legend } = mountShell('dark')
    expect(ink(storyline)).toBe('#ffffff')
    expect(ink(node)).toBe('#c7c7c7')
    expect(ink(legend)).toBe('#c7c7c7')
  })

  it('light shell: the same rules use the light-theme ink', () => {
    const { storyline, node, legend } = mountShell('light')
    expect(ink(storyline)).toBe('#000000')
    expect(ink(node)).toBe('#595959')
    expect(ink(legend)).toBe('#595959')
  })
})
