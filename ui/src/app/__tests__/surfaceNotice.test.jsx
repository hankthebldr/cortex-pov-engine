/**
 * UI-04 — the Library and guided-flow mounts passed `onError={() => {}}`.
 *
 * Every failure reported through that prop reached nothing on screen: a
 * deep-linked scenario that 404s opened no drawer and said nothing, and an
 * armed scenario that failed to load left the guided flow reading "Arm a
 * scenario from Simulate to begin" — the wrong fix for what actually happened.
 */
import React from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import { getDestination } from '../destinations.jsx'

void React

const NOT_FOUND = () => new Response(JSON.stringify({
  detail: { error: 'Scenario not found', code: 'SCENARIO_NOT_FOUND', detail: "scenario_id='SIM-GONE-001'" },
}), { status: 404, headers: { 'content-type': 'application/json' } })

const BASE_ROUTES = {
  'GET /api/scenarios': { scenarios: [], total: 0 },
  'GET /api/scenarios/SIM-GONE-001': NOT_FOUND,
  'GET /api/runs': { runs: [], total: 0 },
  'GET /api/agents': { agents: [], total: 0 },
  'GET /api/tools/adapters': { adapters: [], total: 0 },
}

describe('destination error wiring', () => {
  it('Library: a deep-linked scenario that fails to load is said on screen', async () => {
    installRoutes(BASE_ROUTES)
    const Library = getDestination('library').Component
    render(<Library params={{ open: 'SIM-GONE-001' }} onNavigate={() => {}} />)
    const notice = await screen.findByTestId('library-notice')
    expect(notice).toHaveAttribute('role', 'alert')
    expect(notice).toHaveTextContent('Scenario not found')
    expect(notice).toHaveTextContent('SCENARIO_NOT_FOUND')
  })

  it('guided flow: an armed scenario that fails to load is named, not reported as "nothing armed"', async () => {
    installRoutes(BASE_ROUTES)
    const Guided = getDestination('preflight').Component
    render(<Guided params={{ arm: 'SIM-GONE-001' }} onNavigate={() => {}} />)
    const notice = await screen.findByTestId('guided-notice')
    expect(notice).toHaveTextContent('Could not load armed scenario SIM-GONE-001')
    expect(notice).toHaveTextContent('Scenario not found')
  })
})
