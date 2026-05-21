/**
 * Smoke + interaction tests for DetectionDrawer — the Evidence scorecard
 * drill-down panel.
 */
import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import DetectionDrawer from '../console/DetectionDrawer.jsx'

void React

const baseRow = {
  id: 42,
  tid: 'T1552.001',
  plane: 'EDR',
  detectionType: 'BIOC',
  alert: 'Recursive AKIA grep from www-data',
  mttd: 38,
  alertId: 'a9f4-21c8-deadbeef',
  observed: true,
  notes: '',
  executedAt: '2026-05-20T12:41:07Z',
  observedAt: '2026-05-20T12:41:45Z',
}

describe('<DetectionDrawer />', () => {
  it('renders an empty shell when row is null', () => {
    const { container } = render(<DetectionDrawer row={null} open={false} />)
    expect(container.querySelector('.detection-drawer')).toBeTruthy()
    expect(screen.queryByText('Operator notes')).not.toBeInTheDocument()
  })

  it('renders TID, alert, MTTD, plane when row is supplied + open', () => {
    render(<DetectionDrawer row={baseRow} open onClose={() => {}} />)
    expect(screen.getByText('T1552.001')).toBeInTheDocument()
    expect(screen.getByText(/Recursive AKIA grep/)).toBeInTheDocument()
    expect(screen.getByText('EDR · BIOC')).toBeInTheDocument()
    expect(screen.getByText('38s')).toBeInTheDocument()  // MTTD < 1min
  })

  it('formats MTTD over 1 minute as "Xm Ys"', () => {
    const slow = { ...baseRow, mttd: 75 }
    render(<DetectionDrawer row={slow} open onClose={() => {}} />)
    expect(screen.getByText('1m 15s')).toBeInTheDocument()
  })

  it('Close button calls onClose', () => {
    const onClose = vi.fn()
    render(<DetectionDrawer row={baseRow} open onClose={onClose} />)
    fireEvent.click(screen.getByRole('button', { name: /close detection detail/i }))
    expect(onClose).toHaveBeenCalled()
  })

  it('Mark detected fires onValidate(id, true, notes)', () => {
    const onValidate = vi.fn()
    render(<DetectionDrawer row={{ ...baseRow, observed: null }} open onValidate={onValidate} />)
    const textarea = screen.getByLabelText(/Operator notes/i)
    fireEvent.change(textarea, { target: { value: 'observed in console at 12:41:45Z' } })
    fireEvent.click(screen.getByRole('button', { name: /mark detected/i }))
    expect(onValidate).toHaveBeenCalledWith(42, true, 'observed in console at 12:41:45Z')
  })

  it('Mark missed fires onValidate(id, false, notes)', () => {
    const onValidate = vi.fn()
    render(<DetectionDrawer row={{ ...baseRow, observed: null }} open onValidate={onValidate} />)
    fireEvent.click(screen.getByRole('button', { name: /mark missed/i }))
    expect(onValidate).toHaveBeenCalledWith(42, false, null)
  })

  it('Reset button appears when observed != null and fires onValidate(id, null, null)', () => {
    const onValidate = vi.fn()
    render(<DetectionDrawer row={baseRow} open onValidate={onValidate} />)
    const reset = screen.getByRole('button', { name: /reset/i })
    fireEvent.click(reset)
    expect(onValidate).toHaveBeenCalledWith(42, null, null)
  })

  it('Reset button does NOT appear when status is pending', () => {
    render(<DetectionDrawer row={{ ...baseRow, observed: null }} open onValidate={() => {}} />)
    expect(screen.queryByRole('button', { name: /reset/i })).not.toBeInTheDocument()
  })

  it('hydrates the notes textarea from row.notes on mount', () => {
    const withNotes = { ...baseRow, notes: 'previously saved' }
    render(<DetectionDrawer row={withNotes} open onValidate={() => {}} />)
    const textarea = screen.getByLabelText(/Operator notes/i)
    expect(textarea.value).toBe('previously saved')
  })

  it('clicking copy attempts to write the alertId to clipboard', () => {
    const writeText = vi.fn().mockResolvedValue()
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })
    render(<DetectionDrawer row={baseRow} open onValidate={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /copy alert id/i }))
    expect(writeText).toHaveBeenCalledWith('a9f4-21c8-deadbeef')
  })
})
