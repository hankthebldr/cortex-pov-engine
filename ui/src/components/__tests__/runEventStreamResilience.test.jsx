/**
 * The live run log is what a DC watches during the run. Three ways it misled:
 *
 *  1. An SSE stream that DROPPED (SimCore restart, proxy idle close) closed
 *     itself and never fell back, so the badge said LIVE forever over a stream
 *     that could not deliver another event.
 *  2. A failed poll set mode 'error' AND connected=false, and the badge only
 *     rendered ERR inside the `connected` branch — so it showed "..." (looks
 *     like connecting), and a later successful poll never cleared it.
 *  3. "⏸ pause" was an effect dependency that began with setEvents([]), so
 *     pausing wiped the log; SSE does not replay, so it was gone for good.
 */
import React from 'react'
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act, fireEvent } from '@testing-library/react'
import EventStream from '../console/EventStream.jsx'
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

const badge = () => document.querySelector('.event-stream__mode').textContent

let savedES
beforeEach(() => {
  savedES = globalThis.EventSource
  FakeEventSource.instances = []
})
afterEach(() => {
  if (savedES === undefined) delete globalThis.EventSource
  else globalThis.EventSource = savedES
})

describe('run event stream — the badge tells the truth about the connection', () => {
  it('a dropped SSE stream stops claiming LIVE and falls back to polling the run', async () => {
    globalThis.EventSource = FakeEventSource
    const fetchMock = installRoutes({
      'GET /api/runs/r-1': { id: 'r-1', scenario_id: 'SIM-EDR-001', status: 'running', started_at: '2026-10-04T12:00:00', steps: [] },
    })
    render(<EventStream runId="r-1" />)
    const es = FakeEventSource.instances[0]
    act(() => { es.open() })
    expect(badge()).toBe('LIVE')
    act(() => { es.emit(line('e1', 'step one ran')) })
    expect(screen.getByText('step one ran')).toBeInTheDocument()

    act(() => { es.fail() })   // the stream drops mid-run
    await waitFor(() => expect(badge()).not.toBe('LIVE'))
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(fetchMock.mock.calls[0][0]).toContain('/api/runs/r-1')
    expect(badge()).toBe('POLL')
    // What already arrived over SSE is still on screen.
    expect(screen.getByText('step one ran')).toBeInTheDocument()
  })

  it('a failed poll shows ERR, and a later good poll clears it', async () => {
    delete globalThis.EventSource
    let fail = true
    installRoutes({
      'GET /api/runs/r-2': () => (fail
        ? new Response('bad gateway', { status: 502 })
        : { id: 'r-2', scenario_id: 'SIM-EDR-001', status: 'running', started_at: '2026-10-04T12:00:00', steps: [] }),
    })
    render(<EventStream runId="r-2" />)
    await waitFor(() => expect(badge()).toBe('ERR'))

    fail = false
    await waitFor(() => expect(badge()).toBe('POLL'), { timeout: 5000 })
  })
})

describe('run event stream — pause does not destroy the log', () => {
  it('keeps every line already received when the operator pauses', () => {
    globalThis.EventSource = FakeEventSource
    installRoutes({})
    render(<EventStream runId="r-3" />)
    const es = FakeEventSource.instances[0]
    act(() => { es.open() })
    act(() => {
      es.emit(line('a', 'first line'))
      es.emit(line('b', 'second line'))
    })
    expect(screen.getByText('second line')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /pause/i }))
    expect(screen.getByText('first line')).toBeInTheDocument()
    expect(screen.getByText('second line')).toBeInTheDocument()
    // …and the subscription is actually released while paused.
    expect(es.closed).toBe(true)
  })
})
