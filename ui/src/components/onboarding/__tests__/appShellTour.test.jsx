// @vitest-environment jsdom
import React, { useState } from 'react'
import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import AppShell from '../../console/AppShell.jsx'
import { markTourSeen } from '../onboardingState.js'
import { markFirstRunSeen } from '../../console/HelpOverlay.jsx'

void React

function makeStorageStub() {
  const m = new Map()
  return {
    get length() { return m.size },
    key: (i) => Array.from(m.keys())[i] ?? null,
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => { m.set(k, String(v)) },
    removeItem: (k) => { m.delete(k) },
    clear: () => { m.clear() },
  }
}
beforeAll(() => {
  if (!window.localStorage) {
    Object.defineProperty(window, 'localStorage', {
      value: makeStorageStub(), writable: true, configurable: true,
    })
  }
})
beforeEach(() => { window.localStorage.clear() })

const GROUPS = [{ label: 'Operate', items: [{ id: 'library', label: 'Library' }] }]

describe('AppShell first-run', () => {
  it('starts the tour on a fresh profile, not the help overlay', async () => {
    render(<AppShell destination="library" navGroups={GROUPS} paletteItems={[]}><div /></AppShell>)
    expect(await screen.findByTestId('tour-spotlight')).toBeTruthy()
    expect(screen.queryByText(/Keyboard shortcuts/i)).toBeNull()
  })

  it('does not start the tour when it has already been seen', async () => {
    markTourSeen()
    render(<AppShell destination="library" navGroups={GROUPS} paletteItems={[]}><div /></AppShell>)
    await new Promise((r) => setTimeout(r, 500))
    expect(screen.queryByTestId('tour-spotlight')).toBeNull()
  })

  it('does not start the tour for a user who already dismissed the help overlay', async () => {
    markFirstRunSeen()
    render(<AppShell destination="library" navGroups={GROUPS} paletteItems={[]}><div /></AppShell>)
    await new Promise((r) => setTimeout(r, 500))
    expect(screen.queryByTestId('tour-spotlight')).toBeNull()
  })
})

// ── I4 — spec §6: "Any navigation the tour did not initiate → exit and mark
// seen." The tour's cutout is `pointer-events: none` and stops 1/3/5
// spotlight a real nav button, so a user clicking the highlighted control —
// or any other nav control, or ⌘K, or a breadcrumb — navigates the app
// while the tour is still up. Nothing previously noticed; Next/Back went on
// to probe anchors in whatever destination the tour THOUGHT it was still
// on. AppShell is a controlled component (`destination` is a prop), so this
// harness owns that state and updates it the way the real router does. ──
const GROUPS_WITH_AGENTS = [{
  label: 'Operate',
  items: [{ id: 'library', label: 'Library' }, { id: 'agents', label: 'Agents' }],
}]

function ControlledAppShell({ initial = 'library' }) {
  const [dest, setDest] = useState(initial)
  return (
    <AppShell destination={dest} onNavigate={(id) => setDest(id)} navGroups={GROUPS_WITH_AGENTS} paletteItems={[]}>
      <div />
    </AppShell>
  )
}

describe('AppShell tour — exits on navigation it did not initiate (I4)', () => {
  it('exits and marks seen when the user navigates via the persistent nav rail mid-tour', async () => {
    render(<ControlledAppShell />)
    await screen.findByTestId('tour-spotlight')

    // Stop 1 is anchored on nav-library; the user instead clicks nav-agents
    // directly — a navigation the tour itself never requested.
    fireEvent.click(screen.getByTestId('dest-button-agents'))

    await waitFor(() => expect(screen.queryByTestId('tour-spotlight')).toBeNull())
    expect(window.localStorage.getItem('cortexsim.onboarding.tourSeenV1')).toBe('true')
  })
})
