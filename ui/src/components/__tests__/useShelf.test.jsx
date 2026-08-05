import React from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import useShelf, { SHELF_CHANGED_EVENT } from '../console/useShelf.js'
import { installRoutes } from '../../test/mockFetch.js'

void React

const ADAPTER = { adapter_id: 'TOOL-LINPEAS', tier: 4 }

const DECLARED = {
  name: 'linpeas.sh', url: 'https://github.com/x/linpeas.sh', kind: 'file',
  sha256: 'a'.repeat(64), pinned: true, license: 'MIT', adapter_id: 'TOOL-LINPEAS',
  stage_path: '/tmp/linpeas.sh', mode: '0755',
}
const STAGED = { name: 'linpeas.sh', sha256: 'a'.repeat(64), size_bytes: 10, adapter_id: 'TOOL-LINPEAS', pinned: true, license: 'MIT' }

function Probe() {
  const shelf = useShelf({ adapters: [ADAPTER] })
  return (
    <div>
      <span data-testid="available">{String(shelf.available)}</span>
      <span data-testid="staged">{shelf.counts.staged}</span>
      <span data-testid="unstaged">{shelf.counts.unstaged}</span>
      <span data-testid="job">{shelf.jobs['TOOL-LINPEAS']?.state || 'none'}</span>
      <span data-testid="job-code">{shelf.jobs['TOOL-LINPEAS']?.error?.code || ''}</span>
      <button type="button" onClick={() => shelf.stage('TOOL-LINPEAS')}>stage</button>
    </div>
  )
}

describe('useShelf', () => {
  it('reads the shelf and derives counts', async () => {
    installRoutes({
      'GET /api/shelf/payloads': { payloads: [], total: 0, dist_dir: '/app/payloads', auth: 'open', writable: true, declared: [DECLARED] },
      'GET /api/shelf/artifacts': { artifacts: [DECLARED], staged: [], unstaged: ['linpeas.sh'], unpinned: [] },
    })
    render(<Probe />)
    await waitFor(() => expect(screen.getByTestId('available')).toHaveTextContent('true'))
    expect(screen.getByTestId('unstaged')).toHaveTextContent('1')
  })

  it('marks the shelf unavailable — not empty — when both mounts 404', async () => {
    installRoutes({})
    render(<Probe />)
    await waitFor(() => expect(screen.getByTestId('available')).toHaveTextContent('false'))
  })

  it('falls back to the K8s-namespaced mount when /api/shelf 404s', async () => {
    installRoutes({
      'GET /api/k8s/payloads': { payloads: [STAGED], total: 1, dist_dir: '/app/payloads', auth: 'open', writable: true, declared: [DECLARED] },
    })
    render(<Probe />)
    await waitFor(() => expect(screen.getByTestId('available')).toHaveTextContent('true'))
    expect(screen.getByTestId('staged')).toHaveTextContent('1')
  })

  it('re-reads the shelf after a successful stage rather than assuming', async () => {
    let staged = false
    installRoutes({
      'GET /api/shelf/payloads': () => ({
        payloads: staged ? [STAGED] : [], total: staged ? 1 : 0,
        dist_dir: '/app/payloads', auth: 'open', writable: true, declared: [DECLARED],
      }),
      'GET /api/shelf/artifacts': () => ({ artifacts: [DECLARED] }),
      'POST /api/shelf/stage': () => { staged = true; return { status: 'staged', payload: STAGED, warnings: [] } },
    })
    render(<Probe />)
    await waitFor(() => expect(screen.getByTestId('unstaged')).toHaveTextContent('1'))
    fireEvent.click(screen.getByText('stage'))
    await waitFor(() => expect(screen.getByTestId('job')).toHaveTextContent('done'))
    await waitFor(() => expect(screen.getByTestId('staged')).toHaveTextContent('1'))
  })

  it('keeps a failed stage as a NAMED failure carrying the server code', async () => {
    installRoutes({
      'GET /api/shelf/payloads': { payloads: [], declared: [DECLARED], writable: true, auth: 'open', dist_dir: '/x' },
      'GET /api/shelf/artifacts': { artifacts: [DECLARED] },
      'POST /api/shelf/stage': () => new Response(
        JSON.stringify({ detail: { error: 'Upstream changed', code: 'PAYLOAD_PIN_MISMATCH', detail: 'expected≠actual' } }),
        { status: 409, headers: { 'content-type': 'application/json' } },
      ),
    })
    render(<Probe />)
    await waitFor(() => expect(screen.getByTestId('available')).toHaveTextContent('true'))
    fireEvent.click(screen.getByText('stage'))
    await waitFor(() => expect(screen.getByTestId('job')).toHaveTextContent('failed'))
    expect(screen.getByTestId('job-code')).toHaveTextContent('PAYLOAD_PIN_MISMATCH')
    // A failure must NOT flip anything to staged.
    expect(screen.getByTestId('staged')).toHaveTextContent('0')
  })

  it('refreshes when another instance announces a shelf change', async () => {
    let staged = false
    installRoutes({
      'GET /api/shelf/payloads': () => ({
        payloads: staged ? [STAGED] : [], dist_dir: '/x', auth: 'open', writable: true, declared: [DECLARED],
      }),
      'GET /api/shelf/artifacts': () => ({ artifacts: [DECLARED] }),
    })
    render(<Probe />)
    await waitFor(() => expect(screen.getByTestId('staged')).toHaveTextContent('0'))
    staged = true
    await act(async () => { window.dispatchEvent(new CustomEvent(SHELF_CHANGED_EVENT)) })
    await waitFor(() => expect(screen.getByTestId('staged')).toHaveTextContent('1'))
  })

  it('sets no timers — the stage endpoint is synchronous, so nothing polls', async () => {
    installRoutes({
      'GET /api/shelf/payloads': { payloads: [], declared: [], writable: true, auth: 'open', dist_dir: '/x' },
      'GET /api/shelf/artifacts': { artifacts: [] },
    })
    const fetchSpy = globalThis.fetch
    render(<Probe />)
    await waitFor(() => expect(screen.getByTestId('available')).toHaveTextContent('true'))
    const callsAfterMount = fetchSpy.mock.calls.length
    await new Promise((r) => setTimeout(r, 1200))
    expect(fetchSpy.mock.calls.length).toBe(callsAfterMount)
  })
})
