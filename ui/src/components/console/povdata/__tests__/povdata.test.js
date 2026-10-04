/**
 * povdata — what is left of the design prototype's seed catalogs is vocabulary
 * (lane names, plane names, tints, band geometry), and it has to stay that way.
 *
 * The console used to render seed records — a tenant called Acme Financial, a
 * jumpbox that answered every poll, twelve components, a fixed coverage matrix
 * — as if they were this instance's state, while the real provider said
 * otherwise. These guards fail if a module grows a record-shaped export again.
 */
import { describe, it, expect } from 'vitest'
import * as planes from '../planes.js'
import * as corpus from '../corpus.js'

describe('povdata is vocabulary, not state', () => {
  it('exports only lane vocabulary and plane vocabulary', () => {
    expect(Object.keys(corpus).sort()).toEqual(['COMPOSER_LANES', 'LANE_BANDS'])
    expect(Object.keys(planes).sort()).toEqual(['PLANES', 'planeLabel', 'planeTint'])
  })

  it('plane rows carry no counts — a count is a fact about an instance', () => {
    for (const row of planes.PLANES) {
      expect(row).toHaveLength(3)
      expect(row.some((v) => typeof v === 'number')).toBe(false)
    }
  })

  it('lane bands stack without gaps, in lane order', () => {
    let y = 0
    corpus.LANE_BANDS.forEach((b, i) => {
      expect(b.key).toBe(corpus.COMPOSER_LANES[i][0])
      expect(b.top).toBe(y)
      y += b.h
    })
  })

  it('planeTint / planeLabel resolve known codes and fall back for unknown ones', () => {
    expect(planes.planeLabel('EDR')).toMatch(/Endpoint/)
    expect(planes.planeTint('EDR')).toMatch(/^#/)
    expect(planes.planeLabel('NOPE')).toBe('NOPE')
    expect(planes.planeTint('NOPE')).toBe('var(--bd2)')
  })
})
