/**
 * Smoke + interaction tests for StackCoverageView.
 *
 * Covers matrix derivation from scenarios, cell click → panel, and the
 * filter cross-link callback.
 */
import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import StackCoverageView, { cellFillPercent } from '../console/StackCoverageView.jsx'
import { installRoutes } from '../../test/mockFetch.js'

void React

const fixtureScenarios = {
  scenarios: [
    {
      scenario_id: 'SIM-EDR-001',
      name: 'LSASS dump',
      plane: 'EDR',
      mitre_tactic: 'TA0006',
      threat_report: 'Unit42 — Mimikatz',
      steps: [
        { expected_detections: [{ plane: 'EDR', type: 'BIOC' }] },
      ],
    },
    {
      scenario_id: 'SIM-MP-004',
      name: 'APT29 cloud cred theft',
      plane: 'ANALYTICS',
      mitre_tactic: 'TA0006',
      threat_report: 'Unit42 — APT29 cloud TTPs',
      steps: [
        { expected_detections: [
          { plane: 'EDR', type: 'BIOC' },
          { plane: 'CDR', type: 'Analytics' },
          { plane: 'ANALYTICS', type: 'Analytics' },
        ] },
      ],
    },
    {
      scenario_id: 'SIM-CDR-002',
      name: 'Container escape',
      plane: 'CDR',
      mitre_tactic: 'TA0004',
      steps: [],
    },
  ],
}

describe('<StackCoverageView />', () => {
  it('renders intro stats from the fetched scenario list', async () => {
    installRoutes({ 'GET /api/scenarios': fixtureScenarios })
    render(<StackCoverageView />)
    await waitFor(() => {
      expect(screen.getByText(/3/)).toBeInTheDocument()
    })
    expect(screen.getByText(/scenarios in library/i)).toBeInTheDocument()
    expect(screen.getByText(/kill chain tactics/i)).toBeInTheDocument()
    expect(screen.getByText(/products with coverage/i)).toBeInTheDocument()
  })

  it('renders the product × tactic header row', async () => {
    installRoutes({ 'GET /api/scenarios': fixtureScenarios })
    render(<StackCoverageView />)
    await waitFor(() => {
      expect(screen.getByText('Cortex XDR')).toBeInTheDocument()
    })
    expect(screen.getByText('Cortex XSIAM')).toBeInTheDocument()
    expect(screen.getByText('Cortex Cloud')).toBeInTheDocument()
    expect(screen.getByText('Strata NGFW')).toBeInTheDocument()
  })

  it('places SIM-EDR-001 in the Cortex XDR / TA0006 cell', async () => {
    installRoutes({ 'GET /api/scenarios': fixtureScenarios })
    const { container } = render(<StackCoverageView />)
    await waitFor(() => {
      expect(screen.getByText('Cortex XDR')).toBeInTheDocument()
    })
    // The aria-label format includes the count; assert via accessibility.
    const xdrTa0006Cell = container.querySelector(
      '[aria-label*="Cortex XDR Cred Access"]'
    )
    expect(xdrTa0006Cell).toBeTruthy()
    // Both SIM-EDR-001 + SIM-MP-004's expected_detections touch EDR for TA0006 → 2
    expect(xdrTa0006Cell.getAttribute('aria-label')).toMatch(/2 scenarios/)
  })

  it('clicking a populated cell opens the drilldown panel', async () => {
    installRoutes({ 'GET /api/scenarios': fixtureScenarios })
    const { container } = render(<StackCoverageView />)
    await waitFor(() => {
      expect(screen.getByText('Cortex XDR')).toBeInTheDocument()
    })
    const xdrTa0006Cell = container.querySelector(
      '[aria-label*="Cortex XDR Cred Access"]'
    )
    fireEvent.click(xdrTa0006Cell)
    expect(screen.getByRole('button', { name: /filter operations/i })).toBeInTheDocument()
    expect(screen.getByText('SIM-EDR-001')).toBeInTheDocument()
  })

  it('disabled cells (zero count) do not open the panel on click', async () => {
    installRoutes({ 'GET /api/scenarios': fixtureScenarios })
    const { container } = render(<StackCoverageView />)
    await waitFor(() => {
      expect(screen.getByText('Cortex XDR')).toBeInTheDocument()
    })
    // Cortex ITDR has no plane in any fixture scenario.
    const itdrCells = container.querySelectorAll(
      '[aria-label*="Cortex ITDR"][aria-label*="0 scenarios"]'
    )
    expect(itdrCells.length).toBeGreaterThan(0)
    fireEvent.click(itdrCells[0])
    // Panel should not have opened.
    expect(screen.queryByRole('button', { name: /filter operations/i })).not.toBeInTheDocument()
  })

  it('Filter Operations button fires onFilterByCell with scenario ids', async () => {
    installRoutes({ 'GET /api/scenarios': fixtureScenarios })
    const onFilterByCell = vi.fn()
    const { container } = render(<StackCoverageView onFilterByCell={onFilterByCell} />)
    await waitFor(() => {
      expect(screen.getByText('Cortex XDR')).toBeInTheDocument()
    })
    const cell = container.querySelector('[aria-label*="Cortex XDR Cred Access"]')
    fireEvent.click(cell)
    fireEvent.click(screen.getByRole('button', { name: /filter operations/i }))
    expect(onFilterByCell).toHaveBeenCalled()
    const args = onFilterByCell.mock.calls[0]
    expect(args[0]).toBe('cortex-xdr')
    expect(args[1]).toBe('TA0006')
    expect(args[2]).toContain('SIM-EDR-001')
  })
})

/**
 * Coverage-legibility regression (operator-flagged): "a 1-scenario cell
 * and a 3-scenario cell can now be visually identical." The prior fix
 * bucketed cell intensity into 3 discrete tiers, normalized against the
 * MATRIX-WIDE max count — so whenever any other cell in the matrix ran
 * high enough (max >= ~8), a count of 1 and a count of 3 both fell under
 * the same "low" tier (intensity < 0.4) and rendered pixel-identical.
 * These scenarios build a matrix with a busy cell (8 scenarios in one
 * product/tactic pair) alongside a 1-scenario cell and a 3-scenario cell
 * elsewhere, which is exactly the shape that used to collapse.
 */
function edrScenario(id, tactic) {
  return { scenario_id: id, name: id, plane: 'EDR', mitre_tactic: tactic, steps: [] }
}

const legibilityFixture = {
  scenarios: [
    // Busy cell: pushes the old matrix-wide maxCount to 8, which used to
    // wash out every "low" cell elsewhere into the same visual bucket.
    ...Array.from({ length: 8 }, (_, i) => edrScenario(`SIM-EDR-BUSY-${i}`, 'TA0001')),
    // 1-scenario cell.
    edrScenario('SIM-EDR-LOW', 'TA0002'),
    // 3-scenario cell.
    edrScenario('SIM-EDR-MID-1', 'TA0003'),
    edrScenario('SIM-EDR-MID-2', 'TA0003'),
    edrScenario('SIM-EDR-MID-3', 'TA0003'),
  ],
}

describe('<StackCoverageView /> coverage-cell legibility', () => {
  it('renders visually distinct fills for a 1-count and a 3-count cell even when another cell in the matrix is much busier', async () => {
    installRoutes({ 'GET /api/scenarios': legibilityFixture })
    const { container } = render(<StackCoverageView />)
    await waitFor(() => {
      expect(screen.getByText('Cortex XDR')).toBeInTheDocument()
    })

    const lowCell = container.querySelector('[aria-label*="Cortex XDR Execution"]')
    const midCell = container.querySelector('[aria-label*="Cortex XDR Persistence"]')
    expect(lowCell).toBeTruthy()
    expect(midCell).toBeTruthy()
    expect(lowCell.getAttribute('aria-label')).toMatch(/1 scenarios/)
    expect(midCell.getAttribute('aria-label')).toMatch(/3 scenarios/)

    // The count text is always present (never the ONLY carrier of the
    // distinction — that's the colour-blind-safe channel) — but the
    // cardinal defect here is that the FILL used to be identical too.
    // Assert the fill channel itself, independent of maxCount elsewhere.
    expect(lowCell.style.background).toBeTruthy()
    expect(midCell.style.background).toBeTruthy()
    expect(lowCell.style.background).not.toBe(midCell.style.background)
  })

  it('the low end (count=1) stays perceivably tinted, not collapsed to the empty-cell background', () => {
    const emptyPct = cellFillPercent(0)
    const onePct = cellFillPercent(1)
    expect(emptyPct).toBe(0)
    // A visible floor — not merely nonzero, but far enough above zero to
    // read as "present" rather than a rounding artifact.
    expect(onePct).toBeGreaterThanOrEqual(20)
  })

  it('cellFillPercent is strictly increasing in count, independent of any matrix-wide max', () => {
    const counts = [1, 2, 3, 4, 6, 8, 12, 20]
    const pcts = counts.map(cellFillPercent)
    for (let i = 1; i < pcts.length; i++) {
      expect(pcts[i]).toBeGreaterThan(pcts[i - 1])
    }
  })
})
