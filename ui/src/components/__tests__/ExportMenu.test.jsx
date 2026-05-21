/**
 * Tests for the ExportMenu split-button on the Evidence header.
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

describe('<ExportMenu />', () => {
  it('renders the split primary + chevron when a runId is present', () => {
    render(<ExportMenu runId="r-1" />)
    expect(screen.getByText(/export pov briefing/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/show all export options/i)).toBeInTheDocument()
  })

  it('disables both buttons when no runId is supplied', () => {
    render(<ExportMenu runId={null} />)
    const primary = screen.getByText(/export pov briefing/i)
    expect(primary).toBeDisabled()
  })

  it('opens the dropdown when the chevron is clicked', () => {
    render(<ExportMenu runId="r-1" />)
    fireEvent.click(screen.getByLabelText(/show all export options/i))
    expect(screen.getByRole('menu')).toBeInTheDocument()
    expect(screen.getByText(/narrative report/i)).toBeInTheDocument()
    expect(screen.getByText(/detection scoring matrix/i)).toBeInTheDocument()
    expect(screen.getByText(/att&ck navigator layer/i)).toBeInTheDocument()
  })

  it('closes the dropdown on Escape', () => {
    render(<ExportMenu runId="r-1" />)
    fireEvent.click(screen.getByLabelText(/show all export options/i))
    expect(screen.getByRole('menu')).toBeInTheDocument()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('menu')).toBeNull()
  })

  it('primary button calls the bundle endpoint', async () => {
    installRoutes({
      'GET /api/runs/r-1/report/bundle': blob('tar-gz-bytes', 'application/gzip'),
    })
    render(<ExportMenu runId="r-1" />)
    fireEvent.click(screen.getByText(/export pov briefing/i))
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
    fireEvent.click(screen.getByLabelText(/show all export options/i))
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
    fireEvent.click(screen.getByLabelText(/show all export options/i))
    fireEvent.click(screen.getByText(/att&ck navigator layer/i))
    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/runs/r-1/report/navigator'),
        expect.any(Object),
      )
    })
  })

  it('surfaces export failures through onError', async () => {
    installRoutes({
      'GET /api/runs/r-1/report/bundle': new Response('boom', { status: 500 }),
    })
    const onError = vi.fn()
    render(<ExportMenu runId="r-1" onError={onError} />)
    fireEvent.click(screen.getByText(/export pov briefing/i))
    await waitFor(() => {
      expect(onError).toHaveBeenCalled()
    })
  })
})
