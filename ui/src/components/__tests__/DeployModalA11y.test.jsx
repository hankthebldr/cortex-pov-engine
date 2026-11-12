/**
 * Deploy modal — keyboard and screen-reader contract (UI-23).
 *
 *   - It is aria-modal, and its own comment claimed "the same contract as
 *     ConfirmDialog", but it moved focus in and handled Escape only. Tab walked
 *     out of the dialog into the page behind the backdrop.
 *   - After minting, up to four buttons were all named "Copy" (token, install
 *     line, run line, legacy line). A screen reader lists them as four
 *     identical controls; the agent card's removal Copy was a fifth.
 *   - The copy result is announced through one role=status node (round 1);
 *     pinned here so the names and the announcement stay a pair.
 *
 * jsdom only: focus order under a real browser's sequential navigation, and
 * what a screen reader says, are not exercised here.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import { EnvironmentContext, DEFAULT_ENV } from '../../context/EnvironmentContext.jsx'
import TargetsView from '../console/TargetsView.jsx'
import { focusableIn, trapTabKey } from '../console/focusTrap.js'

void React

const MINTED = {
  id: 9, token: 'cxs_A11yT0kenAbCdEfGhIjKlMnOpQrStUvWx', label: null,
  created_at: '2026-11-10T10:00:00', expires_at: '2026-11-10T11:00:00',
  max_uses: 1, used_count: 0, remaining_uses: 1, revoked: false,
}

function mount(env = {}) {
  installRoutes({
    'GET /api/infra/bundles': { bundles: [] },
    'POST /api/agents/enroll/tokens': MINTED,
    'GET /api/agents/install/attempts': { attempts: [], total: 0, retained_max: 100 },
  })
  return render(
    <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, refreshAgents: vi.fn(), ...env }}>
      <TargetsView />
    </EnvironmentContext.Provider>,
  )
}

async function openDeploy(os) {
  fireEvent.click(screen.getAllByRole('button', { name: /Deploy agent/i })[0])
  const dialog = await screen.findByRole('dialog')
  if (os) fireEvent.click(within(dialog).getByRole('button', { name: new RegExp(os) }))
  return dialog
}

describe('focusTrap — the pure parts', () => {
  it('lists only controls a keyboard can reach, in document order', () => {
    document.body.innerHTML = `
      <div id="d">
        <button id="a">a</button>
        <button id="b" disabled>b</button>
        <a id="c" href="#x">c</a>
        <a id="nolink">not a link</a>
        <input id="e" />
        <input id="hid" type="hidden" />
        <button id="f" tabindex="-1">f</button>
        <span id="g" tabindex="0">g</span>
      </div>`
    expect(focusableIn(document.getElementById('d')).map((n) => n.id)).toEqual(['a', 'c', 'e', 'g'])
    document.body.innerHTML = ''
  })

  it('wraps Tab at the end and Shift+Tab at the start, and pulls a stray focus back in', () => {
    document.body.innerHTML = '<button id="out">out</button><div id="d"><button id="a">a</button><button id="z">z</button></div>'
    const d = document.getElementById('d')
    const ev = (shiftKey) => ({ key: 'Tab', shiftKey, preventDefault: vi.fn() })

    document.getElementById('z').focus()
    let e = ev(false)
    expect(trapTabKey(e, d)).toBe(true)
    expect(document.activeElement.id).toBe('a')
    expect(e.preventDefault).toHaveBeenCalled()

    e = ev(true)
    expect(trapTabKey(e, d)).toBe(true)
    expect(document.activeElement.id).toBe('z')

    // In the middle the browser's own order is left alone.
    document.getElementById('a').focus()
    e = ev(false)
    expect(trapTabKey(e, d)).toBe(false)
    expect(e.preventDefault).not.toHaveBeenCalled()

    document.getElementById('out').focus()
    expect(trapTabKey(ev(false), d)).toBe(true)
    expect(document.activeElement.id).toBe('a')
    document.body.innerHTML = ''
  })

  it('ignores keys that are not Tab', () => {
    document.body.innerHTML = '<div id="d"><button id="a">a</button></div>'
    expect(trapTabKey({ key: 'Enter', preventDefault: vi.fn() }, document.getElementById('d'))).toBe(false)
    document.body.innerHTML = ''
  })
})

describe('Deploy modal — Tab stays inside the dialog (UI-23)', () => {
  beforeEach(() => { window.localStorage.clear() })
  afterEach(() => { vi.restoreAllMocks() })

  it('Tab from the last control wraps to the first; Shift+Tab from the first wraps to the last', async () => {
    mount()
    const dialog = await openDeploy()
    const controls = focusableIn(dialog)
    expect(controls.length).toBeGreaterThan(3)
    const first = controls[0]
    const last = controls[controls.length - 1]

    last.focus()
    fireEvent.keyDown(document, { key: 'Tab' })
    expect(document.activeElement).toBe(first)

    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(last)
  })

  it('focus that escaped behind the backdrop is pulled back on the next Tab', async () => {
    mount()
    const dialog = await openDeploy()
    const opener = screen.getAllByRole('button', { name: /Deploy agent/i })[0]
    opener.focus()
    expect(dialog.contains(document.activeElement)).toBe(false)
    fireEvent.keyDown(document, { key: 'Tab' })
    expect(dialog.contains(document.activeElement)).toBe(true)
  })

  it('Escape still closes it', async () => {
    mount()
    await openDeploy()
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })
})

describe('Deploy modal — every Copy says what it copies (UI-23)', () => {
  beforeEach(() => { window.localStorage.clear() })

  it('Linux: token, install command and legacy command are three different names', async () => {
    mount()
    const dialog = await openDeploy()
    fireEvent.click(within(dialog).getByRole('button', { name: /Mint enrollment token/i }))
    await within(dialog).findByTestId('deploy-token')
    fireEvent.click(within(dialog).getByRole('button', { name: /Legacy: self-asserted agent id/i }))
    const names = within(dialog).getAllByRole('button')
      .map((b) => b.getAttribute('aria-label') || b.textContent.trim())
      .filter((n) => /^Copy/.test(n))
    expect(names).toEqual(['Copy enrollment token', 'Copy install command', 'Copy legacy install command'])
    expect(new Set(names).size).toBe(names.length)
  })

  it('Windows adds the run line, still with no two alike', async () => {
    mount()
    const dialog = await openDeploy('Windows')
    fireEvent.click(within(dialog).getByRole('button', { name: /Mint enrollment token/i }))
    await within(dialog).findByTestId('deploy-token')
    const names = within(dialog).getAllByRole('button')
      .map((b) => b.getAttribute('aria-label') || b.textContent.trim())
      .filter((n) => /^Copy/.test(n))
    expect(names).toContain('Copy run line for the downloaded installer')
    expect(new Set(names).size).toBe(names.length)
  })

  it('the result is announced through one status node inside the dialog', async () => {
    mount()
    const dialog = await openDeploy()
    const live = within(dialog).getByTestId('deploy-copy-live')
    expect(live.getAttribute('role')).toBe('status')
    expect(live.getAttribute('aria-live')).toBe('polite')
  })

  it('an agent card names the agent its removal Copy belongs to', () => {
    mount({ agents: [
      { agent_id: 'jb-1', hostname: 'h1', os: 'linux', status: 'online', last_seen_age_seconds: 1 },
    ] })
    const card = screen.getByText('jb-1').closest('.target-card')
    fireEvent.click(within(card).getByRole('button', { name: /delete/i }))
    expect(within(card).getByRole('button', { name: 'Copy removal command for jb-1' })).toBeTruthy()
  })
})
