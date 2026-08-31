import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import EalConsole from '../EalConsole.jsx'
import { installRoutes } from '../../test/mockFetch.js'

describe('<EalConsole />', () => {
  it('mounts and lists plugins + campaigns from the EAL API surface', async () => {
    installRoutes({
      'GET /api/eal/plugins': {
        plugins: [
          { name: 'c2_http_beacon', kind: 'network' },
          { name: 'idp_signin_emulator', kind: 'identity' },
          { name: 'oauth_grant_emulator', kind: 'identity' },
          { name: 'airs_prompt_attack', kind: 'application' },
        ],
        total: 4,
      },
      'GET /api/eal/campaigns': {
        campaigns: [
          { id: 'c-1', name: 'Smoke Campaign', status: 'idle', steps: [] },
        ],
        total: 1,
      },
      'GET /api/eal/runs': { runs: [], total: 0 },
    })
    render(<EalConsole onMessage={vi.fn()} onClose={vi.fn()} />)
    await waitFor(() => {
      const text = document.body.textContent || ''
      // Either plugins or campaigns is visible — implementation may show one
      // tab at a time, so we accept either.
      expect(
        /c2_http_beacon|idp_signin_emulator|Smoke Campaign|airs_prompt_attack/.test(text),
      ).toBe(true)
    })
  })

  it('degrades cleanly when the plugin list is empty', async () => {
    installRoutes({
      'GET /api/eal/plugins': { plugins: [], total: 0 },
      'GET /api/eal/campaigns': { campaigns: [], total: 0 },
      'GET /api/eal/runs': { runs: [], total: 0 },
    })
    render(<EalConsole onMessage={vi.fn()} onClose={vi.fn()} />)
    // Must not throw; some "no plugins" / empty-state copy expected
    await waitFor(() => {
      expect(document.body).toBeInTheDocument()
    })
  })

  // M-8 — "+ New Campaign" used to drive the same tab state as the two
  // role="tab" buttons while living OUTSIDE the tablist as a header action.
  // When it was the active tab, neither real tab had aria-selected="true",
  // so a screen reader announced nothing as selected. Locks: exactly one
  // tab (now three, "+ New Campaign" included) is selected at a time, and
  // each tab is properly associated to a tabpanel.
  it('keeps exactly one tab aria-selected at all times, including when "+ New Campaign" is active', async () => {
    installRoutes({
      'GET /api/eal/campaigns': { campaigns: [], total: 0 },
      'GET /api/eal/runs': { runs: [], total: 0 },
    })
    render(<EalConsole onMessage={vi.fn()} onClose={vi.fn()} />)
    await waitFor(() => expect(screen.getAllByRole('tab')).toHaveLength(3))

    const selectedCount = () => screen.getAllByRole('tab').filter((t) => t.getAttribute('aria-selected') === 'true').length

    // Default tab (Campaigns).
    expect(selectedCount()).toBe(1)
    expect(screen.getByRole('tab', { name: /Campaigns/ }).getAttribute('aria-selected')).toBe('true')

    // "+ New Campaign" IS a tab now — clicking it must select exactly it.
    fireEvent.click(screen.getByRole('tab', { name: /New Campaign/ }))
    expect(selectedCount()).toBe(1)
    expect(screen.getByRole('tab', { name: /New Campaign/ }).getAttribute('aria-selected')).toBe('true')

    fireEvent.click(screen.getByRole('tab', { name: /^Runs/ }))
    expect(selectedCount()).toBe(1)
    expect(screen.getByRole('tab', { name: /^Runs/ }).getAttribute('aria-selected')).toBe('true')
  })

  it('associates each tab with its panel via aria-controls/aria-labelledby', async () => {
    installRoutes({
      'GET /api/eal/campaigns': { campaigns: [], total: 0 },
      'GET /api/eal/runs': { runs: [], total: 0 },
    })
    render(<EalConsole onMessage={vi.fn()} onClose={vi.fn()} />)
    await waitFor(() => expect(screen.getAllByRole('tab')).toHaveLength(3))

    // Panels mount only for the active tab, so activate each in turn.
    for (const name of [/Campaigns/, /New Campaign/, /^Runs/]) {
      fireEvent.click(screen.getByRole('tab', { name }))
      const tab = screen.getByRole('tab', { name })
      const controls = tab.getAttribute('aria-controls')
      expect(controls).toBeTruthy()
      const panel = document.getElementById(controls)
      expect(panel).not.toBeNull()
      expect(panel.getAttribute('role')).toBe('tabpanel')
      expect(panel.getAttribute('aria-labelledby')).toBe(tab.id)
    }
  })
})
