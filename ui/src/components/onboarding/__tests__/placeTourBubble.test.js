import { describe, it, expect } from 'vitest'
import { placeTourBubble } from '../TourSpotlight.jsx'

const VP = { width: 1400, height: 900 }
const BUBBLE = { width: 300, height: 160 }

describe('placeTourBubble', () => {
  it('places the bubble to the RIGHT of a left-rail anchor (the Library case)', () => {
    // A sidebar item: narrow, far left, mid-height.
    const anchor = { top: 170, left: 130, right: 205, bottom: 200, width: 75, height: 30 }
    const pos = placeTourBubble(anchor, BUBBLE, VP)
    expect(pos.left).toBe(205 + 12)        // right edge of anchor + gap
    expect(pos.top).toBe(170)              // aligned with the anchor top
    // and fully on-screen
    expect(pos.left + BUBBLE.width).toBeLessThanOrEqual(VP.width)
  })

  it('flips to the LEFT of the anchor when there is no room on the right', () => {
    const anchor = { top: 100, left: 1300, right: 1360, bottom: 130, width: 60, height: 30 }
    const pos = placeTourBubble(anchor, BUBBLE, VP)
    expect(pos.left).toBe(1300 - 12 - 300) // left edge - gap - bubble width
    expect(pos.left).toBeGreaterThanOrEqual(10)
  })

  it('never lets the bubble run off the right/bottom edges', () => {
    const anchor = { top: 880, left: 1360, right: 1395, bottom: 895, width: 35, height: 15 }
    const pos = placeTourBubble(anchor, BUBBLE, VP)
    expect(pos.left + BUBBLE.width).toBeLessThanOrEqual(VP.width - 10 + 0.001)
    expect(pos.top + BUBBLE.height).toBeLessThanOrEqual(VP.height - 10 + 0.001)
  })

  it('never lets the bubble run off the top/left edges', () => {
    const anchor = { top: -50, left: -20, right: 5, bottom: -20, width: 25, height: 30 }
    const pos = placeTourBubble(anchor, BUBBLE, VP)
    expect(pos.left).toBeGreaterThanOrEqual(10)
    expect(pos.top).toBeGreaterThanOrEqual(10)
  })

  it('falls back to viewport defaults when innerWidth/Height are 0 (jsdom)', () => {
    const anchor = { top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 }
    const pos = placeTourBubble(anchor, { width: 0, height: 0 }, { width: 0, height: 0 })
    expect(Number.isFinite(pos.left)).toBe(true)
    expect(Number.isFinite(pos.top)).toBe(true)
  })
})
