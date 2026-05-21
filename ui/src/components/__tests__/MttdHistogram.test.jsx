/**
 * Tests for the MTTD distribution histogram.
 */
import React from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import MttdHistogram from '../console/MttdHistogram.jsx'

void React

describe('<MttdHistogram />', () => {
  it('shows empty state when no detected rows', () => {
    render(<MttdHistogram rows={[]} />)
    expect(screen.getByText(/no detected results with MTTD/i)).toBeInTheDocument()
  })

  it('shows empty state when no rows have observed=true', () => {
    render(<MttdHistogram rows={[
      { observed: false, mttd: 30 },
      { observed: null,  mttd: 60 },
    ]} />)
    expect(screen.getByText(/no detected results with MTTD/i)).toBeInTheDocument()
  })

  it('renders 7 bucket columns when data is present', () => {
    const rows = [
      { observed: true, mttd: 10 },
      { observed: true, mttd: 25 },
      { observed: true, mttd: 70 },
    ]
    const { container } = render(<MttdHistogram rows={rows} />)
    const cols = container.querySelectorAll('.mttd-histogram__col')
    expect(cols.length).toBe(7)
  })

  it('places detections in correct buckets', () => {
    const rows = [
      { observed: true, mttd: 5 },     // 0-15
      { observed: true, mttd: 20 },    // 15-30
      { observed: true, mttd: 20 },    // 15-30 again
      { observed: true, mttd: 200 },   // 2-5m
    ]
    render(<MttdHistogram rows={rows} />)
    const counts = Array.from(document.querySelectorAll('.mttd-histogram__count'))
      .map((el) => el.textContent)
    // [0-15s, 15-30s, 30-60s, 60-120s, 2-5m, 5-10m, 10m+]
    expect(counts).toEqual(['1', '2', '', '', '1', '', ''])
  })

  it('computes percentile stats (median, p75, p95, max)', () => {
    const rows = [
      { observed: true, mttd: 10 },
      { observed: true, mttd: 20 },
      { observed: true, mttd: 30 },
      { observed: true, mttd: 40 },
      { observed: true, mttd: 1000 },
    ]
    render(<MttdHistogram rows={rows} />)
    // p50 ≈ 30 (idx floor(5*0.5)=2 → 30)
    // p75 ≈ 40 (idx floor(5*0.75)=3 → 40)
    // p95 ≈ 1000 (idx floor(5*0.95)=4 → 1000)
    // max  = 1000
    expect(screen.getByText('30')).toBeInTheDocument()
    expect(screen.getByText('40')).toBeInTheDocument()
    // Both p95 and max are 1000 — multiple matches expected
    expect(screen.getAllByText('1000').length).toBeGreaterThanOrEqual(1)
  })

  it('highlights the bucket containing the median', () => {
    const rows = [
      { observed: true, mttd: 20 },
      { observed: true, mttd: 25 },
      { observed: true, mttd: 28 },  // median bucket 15-30s
    ]
    const { container } = render(<MttdHistogram rows={rows} />)
    const medianBars = container.querySelectorAll('.mttd-histogram__bar--median')
    expect(medianBars.length).toBe(1)
  })

  it('flags the max stat when over 5min as alert', () => {
    const rows = [
      { observed: true, mttd: 700 }, // > 5min
    ]
    const { container } = render(<MttdHistogram rows={rows} />)
    expect(container.querySelector('.mttd-histogram__stat--alert')).toBeTruthy()
  })

  it('skips rows missing mttd', () => {
    const rows = [
      { observed: true, mttd: 30 },
      { observed: true, mttd: null },
      { observed: true /* no mttd */ },
    ]
    render(<MttdHistogram rows={rows} />)
    expect(screen.getByText(/1 detection/)).toBeInTheDocument()
  })
})
