/**
 * deploy-modal-scroll.test.jsx — the deploy modal must fit the viewport and
 * scroll inside itself.
 *
 * Found by loading the built bundle in a browser, not by a unit test: the
 * backdrop is `position: fixed; display: grid; place-items: center` and the
 * modal had no max-height and no overflow. With Windows selected and a token
 * minted, the modal measured 1484px tall in a 900px viewport at c852eac (2079px
 * once round 2 added the download-verify-run path). Nothing scrolled — not the
 * modal, not the backdrop, not the document — so everything below the fold was
 * unreachable: the installer download, its run line, the legacy disclosure and
 * the whole install-attempts list. jsdom never lays anything out, so 1,800
 * green tests said nothing.
 *
 * This pins the CSS contract with the repo's real-stylesheet cascade resolver.
 * It does not prove a pixel: the measurement above came from a browser, and a
 * Playwright check of the modal at a short viewport is still owed.
 */
import { describe, it, expect, afterEach } from 'vitest'
import '../cortex-tokens.css'
import '../cortex-theme.css'
import '../cortex-console.css'
import { resolveProperty } from '../../test/cssCascade.js'

afterEach(() => { document.body.innerHTML = '' })

function mountModal() {
  const shell = document.createElement('div')
  shell.className = 'theme-console'
  shell.innerHTML = `
    <div class="targets">
      <div class="deploy-backdrop">
        <div class="deploy-modal" role="dialog">
          <div class="deploy-modal__head"><h2>Deploy a pull agent</h2></div>
        </div>
      </div>
    </div>`
  document.body.appendChild(shell)
  return {
    backdrop: shell.querySelector('.deploy-backdrop'),
    modal: shell.querySelector('.deploy-modal'),
    head: shell.querySelector('.deploy-modal__head'),
  }
}

describe('deploy modal — reachable at any viewport height', () => {
  it('the modal is capped to the viewport and scrolls its own overflow', () => {
    const { modal } = mountModal()
    expect(resolveProperty(modal, 'overflow-y')).toBe('auto')
    const max = resolveProperty(modal, 'max-height')
    expect(max).toBeTruthy()
    expect(max).toMatch(/100vh|100dvh/)
  })

  it('the title and the close button stay in view while it scrolls', () => {
    const { head } = mountModal()
    expect(resolveProperty(head, 'position')).toBe('sticky')
    // An opaque background, or the scrolled content shows through the title.
    expect(resolveProperty(head, 'background') || resolveProperty(head, 'background-color')).toBeTruthy()
  })

  it('the backdrop is still the fixed, centring layer', () => {
    const { backdrop } = mountModal()
    expect(resolveProperty(backdrop, 'position')).toBe('fixed')
  })
})
