/**
 * Unit tests for DetectionTypeChip (GAP-2).
 *
 * The detection-type vocabulary now spans BIOC | XQL | Analytics |
 * Correlation | IOC. The chip must render a distinct variant class per type
 * (Correlation is the XSIAM differentiator and gets its own treatment), accept
 * mixed casing, and fall back gracefully for unknown types.
 */
import React from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import DetectionTypeChip, { detectionTypeToken } from '../console/DetectionTypeChip.jsx'

void React

describe('detectionTypeToken', () => {
  it('normalizes known types regardless of casing', () => {
    expect(detectionTypeToken('BIOC')).toBe('bioc')
    expect(detectionTypeToken('xql')).toBe('xql')
    expect(detectionTypeToken('Correlation')).toBe('correlation')
    expect(detectionTypeToken('analytics')).toBe('analytics')
    expect(detectionTypeToken('IOC')).toBe('ioc')
  })

  it('resolves common aliases', () => {
    expect(detectionTypeToken('corr')).toBe('correlation')
    expect(detectionTypeToken('correlation_rule')).toBe('correlation')
    expect(detectionTypeToken('xql_query')).toBe('xql')
  })

  it('returns null for unknown / empty input', () => {
    expect(detectionTypeToken('')).toBe(null)
    expect(detectionTypeToken('mystery')).toBe(null)
    expect(detectionTypeToken(null)).toBe(null)
  })
})

describe('DetectionTypeChip', () => {
  it('renders the canonical label and variant class for each type', () => {
    const cases = [
      ['bioc',        'BIOC',        'det-chip--bioc'],
      ['xql',         'XQL',         'det-chip--xql'],
      ['Analytics',   'Analytics',   'det-chip--analytics'],
      ['correlation', 'Correlation', 'det-chip--correlation'],
      ['IOC',         'IOC',         'det-chip--ioc'],
    ]
    for (const [type, label, cls] of cases) {
      const { container, unmount } = render(<DetectionTypeChip type={type} />)
      const chip = container.querySelector('.det-chip')
      expect(chip).toBeTruthy()
      expect(chip).toHaveClass(cls)
      expect(chip.textContent).toBe(label)
      unmount()
    }
  })

  it('gives Correlation a distinct variant class from the others', () => {
    const { container } = render(
      <>
        <DetectionTypeChip type="Correlation" />
        <DetectionTypeChip type="BIOC" />
      </>
    )
    const corr = container.querySelector('.det-chip--correlation')
    const bioc = container.querySelector('.det-chip--bioc')
    expect(corr).toBeTruthy()
    expect(bioc).toBeTruthy()
    expect(corr).not.toBe(bioc)
  })

  it('falls back to a neutral chip showing the raw value for unknown types', () => {
    render(<DetectionTypeChip type="weird" />)
    expect(screen.getByText('WEIRD')).toBeInTheDocument()
  })

  it('renders nothing when given an empty type', () => {
    const { container } = render(<DetectionTypeChip type="" />)
    expect(container.querySelector('.det-chip')).toBeFalsy()
  })
})
