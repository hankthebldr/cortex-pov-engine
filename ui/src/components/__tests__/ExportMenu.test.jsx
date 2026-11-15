/**
 * Tests for ExportMenu — the report downloads on Results: one secondary kit
 * Download button that opens the list of artifacts.
 */
import React from 'react'
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import ExportMenu from '../console/ExportMenu.jsx'
import { installRoutes } from '../../test/mockFetch.js'

void React

// jsdom doesn't give a real Blob URL plumbing — provide noop createObjectURL.
// Also stub HTMLAnchorElement.click so the synthetic download attempt
// doesn't trip jsdom's "Not implemented: navigation" warning.
beforeEach(() => {
  URL.createObjectURL = vi.fn(() => 'blob:mock')
  URL.revokeObjectURL = vi.fn()
  HTMLAnchorElement.prototype.click = vi.fn()
})

// Use Response so the client's blob() pipeline works without further mocks.
const blob = (body, type = 'text/plain') =>
  new Response(new Blob([body], { type }), { status: 200, headers: { 'content-type': type } })

const trigger = () => screen.getByRole('button', { name: 'Download' })

describe('<ExportMenu />', () => {
  it('is one secondary kit button carrying the Download verb and its icon', () => {
    render(<ExportMenu runId="r-1" />)
    expect(trigger()).toHaveClass('kit-btn', 'kit-btn--secondary')
    expect(trigger()).toHaveAttribute('data-verb', 'Download')
    expect(trigger().querySelector('svg')).toHaveAttribute('data-icon', 'download')
    expect(trigger()).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('menu')).toBeNull()
  })

  it('is disabled when no runId is supplied', () => {
    render(<ExportMenu runId={null} />)
    expect(trigger()).toBeDisabled()
  })

  it('opens the list of artifacts, the bundle first', () => {
    render(<ExportMenu runId="r-1" />)
    fireEvent.click(trigger())
    expect(trigger()).toHaveAttribute('aria-expanded', 'true')
    const items = screen.getAllByRole('menuitem').map((i) => i.textContent)
    expect(items[0]).toMatch(/POV briefing bundle/)
    expect(screen.getByText(/narrative report/i)).toBeInTheDocument()
    expect(screen.getByText(/detection scoring matrix/i)).toBeInTheDocument()
    expect(screen.getByText(/att&ck navigator layer/i)).toBeInTheDocument()
  })

  it('closes the list on Escape', () => {
    render(<ExportMenu runId="r-1" />)
    fireEvent.click(trigger())
    expect(screen.getByRole('menu')).toBeInTheDocument()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('menu')).toBeNull()
  })

  it('the bundle item calls the bundle endpoint', async () => {
    installRoutes({
      'GET /api/runs/r-1/report/bundle': blob('tar-gz-bytes', 'application/gzip'),
    })
    render(<ExportMenu runId="r-1" />)
    fireEvent.click(trigger())
    fireEvent.click(screen.getByText(/POV briefing bundle/i))
    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/runs/r-1/report/bundle'),
        expect.any(Object),
      )
    })
  })

  it('markdown menu item calls the markdown endpoint', async () => {
    installRoutes({
      'GET /api/runs/r-1/report': blob('# report', 'text/markdown'),
    })
    render(<ExportMenu runId="r-1" />)
    fireEvent.click(trigger())
    fireEvent.click(screen.getByText(/narrative report/i))
    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/runs/r-1/report?format=markdown'),
        expect.any(Object),
      )
    })
  })

  it('navigator menu item calls the navigator endpoint', async () => {
    installRoutes({
      'GET /api/runs/r-1/report/navigator': blob('{}', 'application/json'),
    })
    render(<ExportMenu runId="r-1" />)
    fireEvent.click(trigger())
    fireEvent.click(screen.getByText(/att&ck navigator layer/i))
    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/runs/r-1/report/navigator'),
        expect.any(Object),
      )
    })
  })

  it('surfaces a failed download through onError', async () => {
    installRoutes({
      'GET /api/runs/r-1/report/bundle': new Response('boom', { status: 500 }),
    })
    const onError = vi.fn()
    render(<ExportMenu runId="r-1" onError={onError} />)
    fireEvent.click(trigger())
    fireEvent.click(screen.getByText(/POV briefing bundle/i))
    await waitFor(() => {
      expect(onError).toHaveBeenCalled()
    })
  })
})
