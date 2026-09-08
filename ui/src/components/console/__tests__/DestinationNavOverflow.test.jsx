import React from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import DestinationNav, { overflowEdges } from '../DestinationNav.jsx'

/**
 * The rail hid six destinations behind a 1px overlay scrollbar.
 *
 * Measured on the running console at 1280x720: `.rail--nav` had a 337px
 * scrollport for 663px of content and reported `offsetWidth - clientWidth`
 * = 1 — Chromium's overlay scrollbar. Readiness, Runs, EAL, Data Streams,
 * Coverage and TTPs were scrolled off with no cue, and "Tools & Payloads"
 * and "UC / TC Index" sat flush against the cut. That is the failure this
 * file guards: not "the rail scrolls" but "the rail SAYS it scrolls".
 *
 * jsdom has no layout — every scroll metric reads 0 — so the edge decision
 * lives in a pure function and is tested as one. The rendered assertions
 * cover the wiring (the attribute exists, and an unmeasurable rail fails
 * toward showing the cue).
 */
describe('DestinationNav — truncation is announced', () => {
  it('reports no cue when everything fits', () => {
    expect(overflowEdges({ scrollTop: 0, scrollHeight: 400, clientHeight: 400 })).toBe('none')
    expect(overflowEdges({ scrollTop: 0, scrollHeight: 401, clientHeight: 400 })).toBe('none') // within slack
  })

  it('reports the edge that has content beyond it', () => {
    // The measured first-load case: 663px of nav in a 337px scrollport.
    expect(overflowEdges({ scrollTop: 0, scrollHeight: 663, clientHeight: 337 })).toBe('bottom')
    expect(overflowEdges({ scrollTop: 326, scrollHeight: 663, clientHeight: 337 })).toBe('top')
    expect(overflowEdges({ scrollTop: 160, scrollHeight: 663, clientHeight: 337 })).toBe('both')
  })

  it('fails toward showing the cue when the rail cannot be measured', () => {
    // A false "there is more below" costs one scroll. A false "that is
    // everything" costs a destination the DC never opens.
    expect(overflowEdges({ scrollTop: NaN, scrollHeight: 663, clientHeight: 337 })).toBe('both')
    expect(overflowEdges({ scrollTop: 0, scrollHeight: undefined, clientHeight: 337 })).toBe('both')
  })

  it('publishes the state on the nav element so CSS can paint an edge', () => {
    render(
      <DestinationNav
        groups={[{ label: 'Compose', items: [
          { id: 'adapters', label: 'Tools & Payloads', icon: '⚙' },
          { id: 'uctc', label: 'UC / TC Index', icon: '≣' },
        ] }]}
        active="adapters"
      />
    )
    const nav = screen.getByRole('navigation', { name: /console destinations/i })
    expect(nav).toHaveAttribute('data-overflow')
    expect(['none', 'top', 'bottom', 'both']).toContain(nav.getAttribute('data-overflow'))
  })
})
