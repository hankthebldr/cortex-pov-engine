/**
 * A run's event stream is what the run's page follows while the run is live.
 * Three ways it misled:
 *
 *  1. An SSE stream that DROPPED (SimCore restart, proxy idle close) closed
 *     itself and never fell back, so the page said Live forever over a stream
 *     that could not deliver another event.
 *  2. A failed poll left the state reading as "connecting", and a later
 *     successful poll never cleared it.
 *  3. Pausing was an effect dependency that began by emptying the buffer; SSE
 *     does not replay, so what had arrived was gone for good.
 */
import React from 'react'
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import useRunEventStream from '../console/useRunEventStream.js'
import { installRoutes } from '../../test/mockFetch.js'

void React

class FakeEventSource {
  static instances = []
  constructor(url) {
    this.url = url
    this.readyState = 0
    this.closed = false
    FakeEventSource.instances.push(this)
  }
  open() { this.readyState = 1; this.onopen && this.onopen({}) }
  emit(evt) { this.onmessage && this.onmessage({ data: JSON.stringify(evt) }) }
  fail() { this.onerror && this.onerror({}) }
  close() { this.readyState = 2; this.closed = true }
}

const line = (id, message) => ({
  id, timestamp: '2026-10-04T12:00:00', level: 'info', stepIndex: 0, message, synthetic: false,
})

/** The hook, as a page reads it: how the run is followed, and what has arrived. */
function Probe({ runId, paused = false }) {
  const { events, connected, mode } = useRunEventStream(runId, { paused })
  return (
    <div>
      <span data-testid="mode">{mode}</span>
      <span data-testid="connected">{String(connected)}</span>
      <ul>{events.map((e) => <li key={e.id}>{e.message}</li>)}</ul>
    </div>
  )
}
const mode = () => screen.getByTestId('mode').textContent
const connected = () => screen.getByTestId('connected').textContent

let savedES
beforeEach(() => {
  savedES = globalThis.EventSource
  FakeEventSource.instances = []
})
afterEach(() => {
  if (savedES === undefined) delete globalThis.EventSource
  else globalThis.EventSource = savedES
})

describe('run event stream — it tells the truth about the connection', () => {
  it('a dropped SSE stream stops claiming to be live and falls back to polling the run', async () => {
    globalThis.EventSource = FakeEventSource
    const fetchMock = installRoutes({
      'GET /api/runs/r-1': { id: 'r-1', scenario_id: 'SIM-EDR-001', status: 'running', started_at: '2026-10-04T12:00:00', steps: [] },
    })
    render(<Probe runId="r-1" />)
    const es = FakeEventSource.instances[0]
    act(() => { es.open() })
    expect(mode()).toBe('stream')
    act(() => { es.emit(line('e1', 'step one ran')) })
    expect(screen.getByText('step one ran')).toBeInTheDocument()

    act(() => { es.fail() })   // the stream drops mid-run
    await waitFor(() => expect(mode()).not.toBe('stream'))
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(fetchMock.mock.calls[0][0]).toContain('/api/runs/r-1')
    expect(mode()).toBe('poll')
    // What already arrived over SSE is still held.
    expect(screen.getByText('step one ran')).toBeInTheDocument()
  })

  it('a failed poll reads as an error, and a later good poll clears it', async () => {
    delete globalThis.EventSource
    let fail = true
    installRoutes({
      'GET /api/runs/r-2': () => (fail
        ? new Response('bad gateway', { status: 502 })
        : { id: 'r-2', scenario_id: 'SIM-EDR-001', status: 'running', started_at: '2026-10-04T12:00:00', steps: [] }),
    })
    render(<Probe runId="r-2" />)
    await waitFor(() => expect(mode()).toBe('error'))
    expect(connected()).toBe('false')

    fail = false
    await waitFor(() => expect(mode()).toBe('poll'), { timeout: 5000 })
    expect(connected()).toBe('true')
  })
})

describe('run event stream — pausing does not destroy what arrived', () => {
  it('keeps every event already received, and releases the subscription', () => {
    globalThis.EventSource = FakeEventSource
    installRoutes({})
    const { rerender } = render(<Probe runId="r-3" />)
    const es = FakeEventSource.instances[0]
    act(() => { es.open() })
    act(() => {
      es.emit(line('a', 'first line'))
      es.emit(line('b', 'second line'))
    })
    expect(screen.getByText('second line')).toBeInTheDocument()

    rerender(<Probe runId="r-3" paused />)
    expect(screen.getByText('first line')).toBeInTheDocument()
    expect(screen.getByText('second line')).toBeInTheDocument()
    expect(es.closed).toBe(true)
  })
})
