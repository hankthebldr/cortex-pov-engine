/**
 * The project mandates structured JSON errors — {"error","code","detail"} —
 * on every response. The client was flattening all of it to "HTTP 422 —
 * Unprocessable Entity", so the actionable half never reached the operator.
 *
 * Also covers the two "never blank-screen" guarantees: the surface boundary
 * and the global unreachable banner.
 */
import React from 'react'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import { getHealth, postRun } from '../../api/client.js'
import SurfaceError, { SurfaceBoundary } from '../console/SurfaceError.jsx'
import useResultsData from '../console/useResultsData.js'
import { renderHook } from '@testing-library/react'

void React

describe('api client — structured error contract', () => {
  it('names the offending field on a Pydantic 422 instead of the status line', async () => {
    installRoutes({
      'POST /api/run': () => new Response(JSON.stringify({
        detail: [{
          type: 'string_type',
          loc: ['body', 'target_agent_id'],
          msg: 'Input should be a valid string',
          input: 1,
        }],
      }), { status: 422, headers: { 'content-type': 'application/json' } }),
    })

    await expect(postRun({ scenario_id: 'SIM-EDR-001' }))
      .rejects.toThrow('target_agent_id: Input should be a valid string')
  })

  it('joins multiple validation failures rather than showing only the first', async () => {
    installRoutes({
      'POST /api/run': () => new Response(JSON.stringify({
        detail: [
          { loc: ['body', 'scenario_id'], msg: 'Field required' },
          { loc: ['body', 'mode'], msg: 'Input should be pull or push' },
        ],
      }), { status: 422, headers: { 'content-type': 'application/json' } }),
    })

    await expect(postRun({})).rejects.toThrow(
      'scenario_id: Field required; mode: Input should be pull or push',
    )
  })

  it('carries code / status on the Error so callers need not re-parse prose', async () => {
    installRoutes({
      'GET /api/health': () => new Response(JSON.stringify({
        detail: { error: 'Tenant vault locked', code: 'VAULT_LOCKED', detail: 'unlock first' },
      }), { status: 503, headers: { 'content-type': 'application/json' } }),
    })

    const err = await getHealth().catch((e) => e)
    expect(err.message).toContain('Tenant vault locked')
    expect(err.code).toBe('VAULT_LOCKED')
    expect(err.status).toBe(503)
    expect(err.detail).toBe('unlock first')
  })

  it('tags validation failures with a code too', async () => {
    installRoutes({
      'POST /api/run': () => new Response(JSON.stringify({
        detail: [{ loc: ['body', 'mode'], msg: 'Field required' }],
      }), { status: 422, headers: { 'content-type': 'application/json' } }),
    })
    const err = await postRun({}).catch((e) => e)
    expect(err.code).toBe('VALIDATION_ERROR')
  })

  it('falls back to the status line when the body is not JSON', async () => {
    installRoutes({
      'GET /api/health': () => new Response('<html>502 Bad Gateway</html>', { status: 502 }),
    })
    await expect(getHealth()).rejects.toThrow(/HTTP 502/)
  })
})

describe('useResultsData — scorecard alert column', () => {
  it('reads expected_detection, the field /api/results actually returns', async () => {
    installRoutes({
      'GET /api/results/run-1': {
        results: [{
          id: 1,
          plane: 'EDR',
          detection_type: 'BIOC',
          expected_detection: 'Credential extraction tool downloaded and executed',
          observed: null,
        }],
      },
    })
    const { result } = renderHook(() => useResultsData('run-1'))
    await waitFor(() => expect(result.current.rows).toHaveLength(1))
    // Previously "(unnamed)" on every row of the scorecard a customer reads.
    expect(result.current.rows[0].alert)
      .toBe('Credential extraction tool downloaded and executed')
  })
})

describe('never blank-screen', () => {
  const Boom = () => { throw new Error('surface exploded') }

  afterEach(() => { vi.restoreAllMocks() })

  it('renders an explained error state instead of an empty main', () => {
    // React logs the caught error; silence it so the run stays readable.
    vi.spyOn(console, 'error').mockImplementation(() => {})
    render(
      <SurfaceBoundary resetKey="coverage" title="Coverage">
        <Boom />
      </SurfaceBoundary>,
    )
    const el = screen.getByTestId('surface-error')
    expect(el.textContent).toContain('Coverage could not load')
    expect(el.textContent).toContain('surface exploded')
    expect(screen.getByRole('button', { name: /Retry/i })).toBeTruthy()
  })

  it('re-arms on navigation so one broken surface does not poison the rest', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const { rerender } = render(
      <SurfaceBoundary resetKey="coverage" title="Coverage">
        <Boom />
      </SurfaceBoundary>,
    )
    expect(screen.getByTestId('surface-error')).toBeTruthy()

    rerender(
      <SurfaceBoundary resetKey="library" title="Library">
        <div>library surface</div>
      </SurfaceBoundary>,
    )
    expect(screen.queryByTestId('surface-error')).toBeNull()
    expect(screen.getByText('library surface')).toBeTruthy()
  })

  it('surfaces the structured code in the error state', () => {
    const err = Object.assign(new Error('SimCore restarting'), { code: 'UPSTREAM_DOWN' })
    render(<SurfaceError title="Runs" error={err} />)
    expect(screen.getByTestId('surface-error').textContent).toContain('UPSTREAM_DOWN')
  })
})
