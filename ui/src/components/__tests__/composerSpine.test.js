/**
 * composerSpine — the connection-legality predicate for the Composer canvas.
 *
 * Pure, no React — the spine invariant (one root, acyclic) must be provable
 * without mounting a canvas, and a refusal must always carry a DC-readable
 * reason, or a silently dropped drag reads as a broken canvas.
 */
import { describe, it, expect } from 'vitest'
import { canConnect } from '../console/composerSpine.js'

const chain = [
  { id: 's1', causalityParent: null },   // the single root
  { id: 's2', causalityParent: 's1' },
  { id: 's3', causalityParent: 's2' },
]

describe('canConnect — the spine invariant (spec D2)', () => {
  it('allows re-parenting a step onto another branch', () => {
    expect(canConnect(chain, 's1', 's3')).toEqual({ ok: true })
  })

  it('refuses a self reference', () => {
    const r = canConnect(chain, 's2', 's2')
    expect(r.ok).toBe(false)
    expect(r.code).toBe('SELF_REF')
    expect(r.reason).toMatch(/itself/i)
  })

  it('refuses a cycle', () => {
    // s3 descends from s1; making s1 a child of s3 closes the loop.
    const r = canConnect(chain, 's3', 's1')
    expect(r.ok).toBe(false)
    expect(r.code).toBe('CYCLE')
    expect(r.reason).toMatch(/cycle|loop/i)
  })

  it('refuses an edge that would orphan the only root', () => {
    // Giving the root a parent leaves no step linking from the CGO anchor.
    const twoRoots = [{ id: 'a', causalityParent: null }, { id: 'b', causalityParent: null }]
    const r = canConnect(twoRoots, 'a', 'a')
    expect(r.ok).toBe(false)
  })

  it('refuses a second parent for an already-parented step', () => {
    const r = canConnect(chain, 's1', 's2')   // s2 already has s1... via s1
    expect(r).toEqual({ ok: true })            // same parent is idempotent
    const r2 = canConnect(chain, 's3', 's2')   // s2 -> parent s3 would cycle
    expect(r2.ok).toBe(false)
  })

  it('always returns a reason when it refuses — a silent no-op reads as a broken canvas', () => {
    for (const [f, t] of [['s2', 's2'], ['s3', 's1']]) {
      const r = canConnect(chain, f, t)
      expect(r.ok).toBe(false)
      expect(typeof r.reason).toBe('string')
      expect(r.reason.length).toBeGreaterThan(10)
    }
  })
})
