/**
 * ReadBackButton — Read back from Results, behind a confirm (decision D5).
 *
 * Read back is the action that can change a run's verdict, so on a run that
 * already has a verdict it asks first. It goes through the console's one
 * Read back path (useReadBack), never straight to the API.
 */
import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react'
import ReadBackButton from '../ReadBackButton.jsx'
import { installRoutes } from '../../../test/mockFetch.js'

void React

afterEach(() => { cleanup(); vi.restoreAllMocks() })

const BODY = "Read back reads the tenant and may change this run's verdict, marking detections "
  + 'detected or missed. Refreshing tenant evidence never does.'

const posts = (handler) => handler.mock.calls
  .map(([input, init = {}]) => {
    const url = typeof input === 'string' ? input : input.url
    return { method: (init.method || 'GET').toUpperCase(), url: new URL(url, 'http://localhost') }
  })
  .filter((c) => c.method === 'POST')

const SUMMARY = { observed: 1, denominator: 2, queries_issued: 1, source: 'xsiam' }

describe('ReadBackButton', () => {
  it('the confirm copy is verbatim and cancel posts nothing', async () => {
    const handler = installRoutes({ 'POST /api/runs/r1/reconcile': SUMMARY })
    render(<ReadBackButton runId="r1" />)
    fireEvent.click(screen.getByRole('button', { name: 'Read back' }))
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent('Read back this run?')
    expect(dialog).toHaveTextContent(BODY)
    fireEvent.click(screen.getByRole('button', { name: /^Cancel/ }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(posts(handler)).toEqual([])
  })

  it('confirm posts /api/runs/r1/reconcile?connector=xsiam once, even when confirmed twice', async () => {
    let release
    const gate = new Promise((r) => { release = r })
    const handler = installRoutes({ 'POST /api/runs/r1/reconcile': async () => { await gate; return SUMMARY } })
    const onDone = vi.fn()
    render(<ReadBackButton runId="r1" onDone={onDone} />)
    fireEvent.click(screen.getByRole('button', { name: 'Read back' }))
    const confirm = screen.getByRole('dialog').querySelector('.btn--primary')
    fireEvent.click(confirm)
    fireEvent.click(confirm)
    // While the read is in flight the button says so and cannot be used.
    const busy = await screen.findByRole('button', { name: 'Reading…' })
    expect(busy).toBeDisabled()
    release()
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1))
    const sent = posts(handler)
    expect(sent).toHaveLength(1)
    expect(sent[0].url.pathname).toBe('/api/runs/r1/reconcile')
    expect(sent[0].url.searchParams.get('connector')).toBe('xsiam')
    expect(onDone.mock.calls[0][0]).toMatchObject({ ok: true })
  })

  it('opens with Cancel focused, so a reflexive Enter changes no verdict', async () => {
    installRoutes({})
    render(<ReadBackButton runId="r1" />)
    fireEvent.click(screen.getByRole('button', { name: 'Read back' }))
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('button', { name: /^Cancel/ })))
  })
})
