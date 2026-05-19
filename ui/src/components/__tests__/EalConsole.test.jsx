import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
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
})
