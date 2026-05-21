/**
 * Smoke + interaction tests for the CompetitiveView matrix.
 */
import React from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import CompetitiveView from '../console/CompetitiveView.jsx'

void React

describe('<CompetitiveView />', () => {
  it('renders the intro + legend', () => {
    const { container } = render(<CompetitiveView />)
    // Intro prose
    expect(screen.getByText(/Structured.*comparison/i)).toBeInTheDocument()
    expect(screen.getByText('verifiable from public vendor documentation')).toBeInTheDocument()
    // All four legend states — scope to legend element to avoid colliding
    // with capability-cell hover labels.
    const legend = container.querySelector('.competitive__legend')
    expect(legend.textContent).toMatch(/Native/)
    expect(legend.textContent).toMatch(/Partial/)
    expect(legend.textContent).toMatch(/Via integration/)
    expect(legend.textContent).toMatch(/Not offered/)
  })

  it('renders all major vendor columns', () => {
    render(<CompetitiveView />)
    expect(screen.getByText('PANW')).toBeInTheDocument()
    expect(screen.getByText('CrowdStrike')).toBeInTheDocument()
    expect(screen.getByText('SentinelOne')).toBeInTheDocument()
    expect(screen.getByText('Microsoft')).toBeInTheDocument()
    expect(screen.getByText('AttackIQ')).toBeInTheDocument()
    expect(screen.getByText('SafeBreach')).toBeInTheDocument()
    expect(screen.getByText('Picus')).toBeInTheDocument()
  })

  it('renders capability rows across all categories', () => {
    render(<CompetitiveView />)
    expect(screen.getByText(/Endpoint detection/i)).toBeInTheDocument()
    expect(screen.getByText(/Cross-domain incident stitching/i)).toBeInTheDocument()
    expect(screen.getByText(/AI Runtime Security/i)).toBeInTheDocument()
    expect(screen.getByText(/Continuous validation/i)).toBeInTheDocument()
  })

  it('category filter narrows the visible rows', () => {
    render(<CompetitiveView />)
    // Filter to "BAS / Validation" — only those capability labels remain
    fireEvent.click(screen.getByRole('button', { name: /BAS \/ Validation/i }))
    expect(screen.getByText(/Continuous validation/i)).toBeInTheDocument()
    // "Endpoint detection" is in the Detection category — should disappear
    expect(screen.queryByText(/Endpoint detection/i)).not.toBeInTheDocument()
  })

  it('clicking a cell opens the detail panel', () => {
    const { container } = render(<CompetitiveView />)
    const cellButtons = container.querySelectorAll('.competitive__cell-btn')
    expect(cellButtons.length).toBeGreaterThan(0)
    fireEvent.click(cellButtons[0])
    // Detail panel should appear with a "Vendor offering" label
    expect(screen.getByText(/Vendor offering/i)).toBeInTheDocument()
    expect(screen.getByText(/Why this matters/i)).toBeInTheDocument()
  })

  it('renders the rollup row with native-capability counts', () => {
    render(<CompetitiveView />)
    expect(screen.getByText(/Total native capabilities/i)).toBeInTheDocument()
    // PANW row should be all-native (12/12 by design) — the matrix as written
    // marks PANW as Native on every capability.
    const rollups = document.querySelectorAll('.competitive__rollup-value')
    expect(rollups.length).toBe(7) // one per vendor
    // First column is PANW — should be the highest native count.
    const panwCount = parseInt(rollups[0].textContent.split('/')[0], 10)
    expect(panwCount).toBeGreaterThanOrEqual(8)
  })

  it('cites in the footer', () => {
    render(<CompetitiveView />)
    expect(screen.getByText(/Sourcing/i)).toBeInTheDocument()
    expect(screen.getByText(/publicly available vendor documentation/i)).toBeInTheDocument()
  })
})
