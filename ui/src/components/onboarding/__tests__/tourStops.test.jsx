// @vitest-environment jsdom
import React from 'react'
import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { TOUR_STOPS } from '../tourStops.js'
import DestinationNav from '../../console/DestinationNav.jsx'

void React

describe('TOUR_STOPS', () => {
  it('is the five-stop activation path in order', () => {
    expect(TOUR_STOPS.map((s) => s.id)).toEqual([
      'library', 'scenario', 'agents-empty', 'enroll', 'runs',
    ])
  })

  it('ends on the agents destination holding the enroll anchor', () => {
    const enroll = TOUR_STOPS.find((s) => s.id === 'enroll')
    expect(enroll.destination).toBe('agents')
    expect(enroll.anchor).toBe('agent-enroll')
  })

  it('gives every stop a non-empty title and body', () => {
    for (const s of TOUR_STOPS) {
      expect(s.title.length, `${s.id} title`).toBeGreaterThan(0)
      expect(s.body.length, `${s.id} body`).toBeGreaterThan(20)
    }
  })

  it('never claims a run proves detection efficacy', () => {
    const all = TOUR_STOPS.map((s) => s.body).join(' ').toLowerCase()
    expect(all).not.toContain('proves')
    expect(all).not.toContain('verified')
  })
})

describe('nav anchors', () => {
  it('DestinationNav renders a data-tour-id per destination', () => {
    const groups = [{ label: 'Operate', items: [{ id: 'library', label: 'Library' }] }]
    const { container } = render(<DestinationNav groups={groups} active="library" />)
    expect(container.querySelector('[data-tour-id="nav-library"]')).toBeTruthy()
  })
})
