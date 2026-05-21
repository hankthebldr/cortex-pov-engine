/**
 * Smoke + interaction tests for FilterPalette.
 *
 * Verifies facet derivation from a live scenario list and that clicking a
 * filter chip calls onToggle with the right (field, value) pair.
 */
import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import FilterPalette from '../console/FilterPalette.jsx'

void React

const baseFilter = {
  plane: null,
  technique: null,
  tactics:      new Set(),
  techniques:   new Set(),
  actors:       new Set(),
  difficulties: new Set(),
  identities:   new Set(),
  detTypes:     new Set(),
  tags:         new Set(),
}

const scenarios = [
  {
    scenario_id: 'SIM-EDR-001',
    name: 'LSASS dump',
    plane: 'EDR',
    mitre_tactic: 'TA0006',
    mitre_technique: 'T1003.001',
    threat_report: 'Unit42 — Mimikatz',
    difficulty: 'advanced',
    execution_identity: { default: 'root' },
    detection_types: ['BIOC'],
  },
  {
    scenario_id: 'SIM-MP-004',
    name: 'APT29 Cloud',
    plane: 'ANALYTICS',
    mitre_tactic: 'TA0006',
    mitre_technique: 'T1552.001',
    threat_report: 'Unit42 — APT29 Cloud TTPs',
    difficulty: 'intermediate',
    execution_identity: { default: 'www-data' },
    detection_types: ['BIOC', 'Analytics'],
  },
]

describe('<FilterPalette />', () => {
  it('renders nothing visible when closed', () => {
    const { container } = render(
      <FilterPalette open={false} filter={baseFilter} scenarios={scenarios} />
    )
    expect(container.querySelector('.cmd-palette-backdrop')).toBeTruthy()
    expect(container.querySelector('.cmd-palette-backdrop.is-open')).toBeFalsy()
  })

  it('renders derived facet groups when open', () => {
    render(<FilterPalette open filter={baseFilter} scenarios={scenarios} />)
    expect(screen.getByText(/MITRE Tactic/i)).toBeInTheDocument()
    expect(screen.getByText(/MITRE Technique/i)).toBeInTheDocument()
    expect(screen.getByText(/Threat Actor/i)).toBeInTheDocument()
    expect(screen.getByText(/Difficulty/i)).toBeInTheDocument()
    expect(screen.getByText(/Identity/i)).toBeInTheDocument()
    expect(screen.getByText(/Detection Type/i)).toBeInTheDocument()
  })

  it('renders facet values from scenarios with counts', () => {
    render(<FilterPalette open filter={baseFilter} scenarios={scenarios} />)
    // TA0006 appears in both scenarios → count 2
    const tacticChip = screen.getByTitle(/TA0006 · 2 scenarios/)
    expect(tacticChip).toBeInTheDocument()
  })

  it('renders match-vs-total in the footer', () => {
    const { container } = render(
      <FilterPalette
        open
        filter={baseFilter}
        scenarios={scenarios}
        matchCount={2}
        totalCount={2}
      />
    )
    // The 2 also appears in chip counts (TA0006 has 2 scenarios), so scope
    // strictly to the footer's count strong element.
    const count = container.querySelector('.filter-palette__count')
    expect(count).toBeTruthy()
    expect(count.textContent).toBe('2')
    expect(screen.getByText(/2 scenarios match/i)).toBeInTheDocument()
  })

  it('calls onToggle with (field, value) when a chip is clicked', () => {
    const onToggle = vi.fn()
    render(
      <FilterPalette
        open
        filter={baseFilter}
        scenarios={scenarios}
        onToggle={onToggle}
      />
    )
    fireEvent.click(screen.getByTitle(/TA0006/))
    expect(onToggle).toHaveBeenCalledWith('tactics', 'TA0006')
  })

  it('highlights an active chip via filter prop', () => {
    const filter = { ...baseFilter, tactics: new Set(['TA0006']) }
    const { container } = render(
      <FilterPalette open filter={filter} scenarios={scenarios} />
    )
    const active = container.querySelectorAll('.filter-chip--active')
    expect(active.length).toBeGreaterThanOrEqual(1)
  })

  it('clear-all is disabled when no filter is active', () => {
    render(
      <FilterPalette
        open
        filter={baseFilter}
        scenarios={scenarios}
        matchCount={2}
        totalCount={2}
      />
    )
    const clear = screen.getByRole('button', { name: /clear all/i })
    expect(clear).toBeDisabled()
  })

  it('clear-all calls onClearAll when filter is active', () => {
    const onClearAll = vi.fn()
    render(
      <FilterPalette
        open
        filter={{ ...baseFilter, tactics: new Set(['TA0006']) }}
        scenarios={scenarios}
        matchCount={1}
        totalCount={2}
        onClearAll={onClearAll}
      />
    )
    fireEvent.click(screen.getByRole('button', { name: /clear all/i }))
    expect(onClearAll).toHaveBeenCalled()
  })

  it('search query filters visible facet values', () => {
    const { container } = render(
      <FilterPalette open filter={baseFilter} scenarios={scenarios} />
    )
    const input = screen.getByPlaceholderText(/Filter scenarios/i)
    // Query for "T1003" — matches the T1003.001 technique facet value only.
    // Tactics (TA0006), actors (Unit42), identities (root/www-data), etc.
    // should be filtered out.
    fireEvent.change(input, { target: { value: 'T1003' } })
    const chips = container.querySelectorAll('.filter-chip')
    expect(chips.length).toBeGreaterThan(0)
    for (const chip of chips) {
      expect(chip.textContent.toLowerCase()).toContain('t1003')
    }
    expect(screen.queryByTitle(/TA0006 ·/)).not.toBeInTheDocument()
  })
})
