/**
 * The flow bar sits under every destination during a customer POV. It used to
 * carry the design prototype's literals — "6 of 11 detections observed" on
 * Runs, "Report ready · 6 observed · 5 pending · 0 missed" on Proof & Export,
 * "3 online" on Agents — whatever had actually run, and "9 of 9 checks pass" on
 * the Launch Gate while SimCore was unreachable. A number nobody measured is
 * the kind that ends up quoted in a readout.
 */
import React from 'react'
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { flowFor } from '../../app/povflow.js'
import AppConsole from '../../AppConsole.jsx'
import { installRoutes } from '../../test/mockFetch.js'
import { markTourSeen } from '../onboarding/onboardingState.js'

void React

describe('flowFor — never quotes a number it was not given', () => {
  // Rows over surfaces that render LIVE SimCore data. Given an empty context,
  // none of them may print a digit.
  const LIVE_ROWS = ['tenants', 'agents', 'library', 'composer', 'adapters', 'ttps', 'uctc',
    'preflight', 'runs', 'coverage', 'proof']

  it.each(LIVE_ROWS)('%s renders words, not figures, when the context is empty', (dest) => {
    const f = flowFor(dest, {})
    expect(`${f.title} | ${f.sub}`).not.toMatch(/\d/)
  })

  it('Proof & Export does not claim a report or a tally that does not exist', () => {
    const f = flowFor('proof', {})
    expect(f.title).not.toMatch(/Report ready/)
    expect(f.sub).not.toMatch(/0 missed/)
  })

  it('Runs quotes the detection tally of the run it was given, verbatim', () => {
    const f = flowFor('runs', { runStep: 'Last run SIM-EDR-001', runDetections: '2 of 9 detections observed' })
    expect(f.sub).toBe('2 of 9 detections observed')
    expect(flowFor('runs', { runStep: 'No run in flight' }).sub).not.toMatch(/6 of 11/)
  })

  it('Agents quotes the online count it was given — 0 is 0, not "3 online"', () => {
    const f = flowFor('agents', { agentCount: 0, agentsOnline: 0 })
    expect(f.title).toBe('0 beacons enrolled')
    expect(f.sub).toBe('0 online')
  })

  it('the Launch Gate never reports passes for a check that did not run', () => {
    expect(flowFor('preflight', {}).title).not.toMatch(/pass/)
    expect(flowFor('preflight', { gate: { state: 'unknown' } }).title).toBe('Readiness not checked yet')
    expect(flowFor('preflight', { gate: { state: 'unreachable' } }).title).toBe('SimCore unreachable')
    const checked = flowFor('preflight', { gate: { state: 'checked', total: 7, pass: 5, warn: 2, unreported: 0 } })
    expect(checked.title).toBe('5 of 7 components ok')
    expect(checked.sub).toBe('2 degraded · 0 not reported')
  })
})

describe('the shell wires real state into the bar', () => {
  beforeEach(() => {
    window.localStorage.clear()
    window.sessionStorage.clear()
    // The first-run tour navigates to the Library on mount; these tests
    // need the destination they asked for.
    markTourSeen()
  })

  it('shows "SimCore unreachable" on the Launch Gate, not "9 of 9 checks pass", when /api/health fails', async () => {
    window.location.hash = '#/preflight'
    installRoutes({
      'GET /api/health': () => new Response('upstream down', { status: 502 }),
      'GET /api/scenarios': { scenarios: [] },
      'GET /api/runs': { runs: [] },
      'GET /api/agents': { agents: [] },
    })
    render(<AppConsole />)
    await waitFor(() => expect(screen.getByTestId('api-down-banner')).toBeInTheDocument())
    const bar = screen.getByTestId('flow-bar')
    expect(bar.textContent).not.toMatch(/9 of 9/)
    expect(bar.textContent).toContain('SimCore unreachable')
  })

  it('does not badge the UC/TC index with a count the shell never fetched', async () => {
    window.location.hash = '#/library'
    installRoutes({
      'GET /api/health': { status: 'ok', components: {} },
      'GET /api/scenarios': { scenarios: [] },
      'GET /api/runs': { runs: [] },
      'GET /api/agents': { agents: [] },
    })
    render(<AppConsole />)
    await waitFor(() => expect(screen.getByTestId('flow-bar')).toBeInTheDocument())
    expect(document.body.textContent).not.toContain('266')
  })
})
