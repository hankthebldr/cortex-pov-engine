// @vitest-environment jsdom
import React from 'react'
import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
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
