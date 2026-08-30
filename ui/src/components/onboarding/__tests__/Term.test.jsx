// @vitest-environment jsdom
import React from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import Term from '../Term.jsx'
import { GLOSSARY, lookup } from '../glossary.js'

void React

describe('glossary', () => {
  it('defines every term the tour and console reference', () => {
    for (const key of ['mttd', 'abioc', 'bioc', 'cgo-anchor', 'tenant-verified',
                       'moat-tier', 'xdm-substrate', 's-13', 'detection-type',
                       'push-bundle', 'identity-harness']) {
      expect(GLOSSARY[key], `missing glossary key: ${key}`).toBeTruthy()
      expect(GLOSSARY[key].definition.length).toBeGreaterThan(20)
    }
  })

  it('lookup returns null for an unknown key rather than throwing', () => {
    expect(lookup('not-a-real-term')).toBeNull()
  })
})

describe('Term', () => {
  it('renders its children as the visible text', () => {
    render(<Term k="mttd">MTTD</Term>)
    expect(screen.getByText('MTTD')).toBeTruthy()
  })

  it('exposes the definition as an accessible description on focus', () => {
    render(<Term k="mttd">MTTD</Term>)
    const el = screen.getByText('MTTD')
    fireEvent.focus(el)
    expect(screen.getByRole('tooltip').textContent).toContain(GLOSSARY.mttd.definition)
  })

  it('renders plain text with no tooltip when the key is unknown', () => {
    render(<Term k="nope">Nope</Term>)
    const el = screen.getByText('Nope')
    fireEvent.focus(el)
    expect(screen.queryByRole('tooltip')).toBeNull()
  })
})
