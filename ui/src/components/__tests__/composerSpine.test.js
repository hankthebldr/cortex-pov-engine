/**
 * composerSpine — the connection-legality predicate for the Composer canvas.
 *
 * Pure, no React — the spine invariant (one root, acyclic) must be provable
 * without mounting a canvas, and a refusal must always carry a DC-readable
 * reason, or a silently dropped drag reads as a broken canvas.
 */
import { describe, it, expect } from 'vitest'
import { canConnect, topologicallySortSteps } from '../console/composerSpine.js'

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

  // Review finding (Task 10, addition C): this fixture's `fromId === toId`
  // ('a','a') trips the SELF_REF branch before `canConnect` ever reaches the
  // root check below — so despite its old name this was never a distinct
  // regression guard on SECOND_ROOT, just a second SELF_REF case that only
  // asserted `ok === false`. Kept, renamed to what it actually proves, with
  // the code now pinned so a future refactor can't silently swap which
  // branch fires first without a test noticing.
  it('refuses a self reference even when framed as "giving the root a parent"', () => {
    const twoRoots = [{ id: 'a', causalityParent: null }, { id: 'b', causalityParent: null }]
    const r = canConnect(twoRoots, 'a', 'a')
    expect(r.ok).toBe(false)
    expect(r.code).toBe('SELF_REF')
  })

  it('refuses giving the chain\'s only root a parent — SECOND_ROOT, distinct from SELF_REF', () => {
    // `fromId` must NOT be a descendant of the root, or `ancestorsOf` trips
    // CYCLE first (as it correctly does for any real node in a single
    // connected tree — the root is every node's ancestor). A step whose
    // declared parent is missing from `steps` (e.g. that step was deleted
    // elsewhere in the draft) has no ancestor path to the root at all, which
    // isolates the SECOND_ROOT branch without also being a self-reference.
    const withDanglingRef = [...chain, { id: 's4', causalityParent: 'deleted-step' }]
    const r = canConnect(withDanglingRef, 's4', 's1')
    expect(r.ok).toBe(false)
    expect(r.code).toBe('SECOND_ROOT')
    expect(r.reason).toMatch(/root/i)
  })

  // Renamed (Minor 7, 2026-09 final-fix wave) — the old title, "refuses a
  // second parent for an already-parented step", contradicted its own first
  // assertion: re-asserting s2's EXISTING parent (s1) is `{ok: true}`, not a
  // refusal. What this test actually pins is two-part: re-asserting an
  // existing parent is an idempotent no-op (first case), and a DIFFERENT
  // reparent that would still cycle is refused regardless (second case).
  it('treats re-asserting an existing parent as an idempotent no-op, and still refuses a cycling reparent', () => {
    const r = canConnect(chain, 's1', 's2')   // s2 already has s1... via s1
    expect(r).toEqual({ ok: true })            // same parent is idempotent
    const r2 = canConnect(chain, 's3', 's2')   // s2 -> parent s3 would cycle
    expect(r2.ok).toBe(false)
  })

  // Task 10, addition C: the interface used to document a fourth code,
  // ALREADY_PARENTED, that no branch of `canConnect` has ever emitted (see
  // `composerSpine.js`'s header for why no genuine case exists — re-parenting
  // onto a new parent is the feature, not a violation). This is the
  // regression guard for that removal: it fails the moment anyone adds a
  // branch returning that code without also fixing the documented interface.
  it('never emits the removed ALREADY_PARENTED code, over every (from,to) pair in the fixture', () => {
    for (const from of chain) {
      for (const to of chain) {
        const r = canConnect(chain, from.id, to.id)
        if (!r.ok) expect(r.code).not.toBe('ALREADY_PARENTED')
      }
    }
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

describe('topologicallySortSteps — the array-order half of the spine invariant (Task 10)', () => {
  /** parent-index < child-index for every parented step — the exact rule
   *  core/engine/scenario_loader.py:394 enforces at load time. */
  function isOrderValid(steps) {
    const indexOf = new Map(steps.map((s, i) => [s.id, i]))
    return steps.every(
      (s) => !s.causalityParent || indexOf.get(s.causalityParent) < indexOf.get(s.id),
    )
  }

  it('is a no-op (same reference) when the array already satisfies the invariant', () => {
    expect(topologicallySortSteps(chain)).toBe(chain)
  })

  it('leaves a single root, no-parent array untouched', () => {
    const roots = [{ id: 'a', causalityParent: null }, { id: 'b', causalityParent: null }]
    expect(topologicallySortSteps(roots)).toBe(roots)
  })

  it('moves a parent that currently sits AFTER its child to precede it', () => {
    // A fork off the single root: s2 and s3 both start as children of s1.
    const fork = [
      { id: 's1', causalityParent: null },
      { id: 's2', causalityParent: 's1' },
      { id: 's3', causalityParent: 's1' },
    ]
    // canConnect approves re-parenting s2 onto s3 — no cycle, no self-ref,
    // s1 stays the (untouched) root — even though s3 (idx 2) sits AFTER s2
    // (idx 1) today. This is the exact edge `setCausalityParent`'s own
    // forward-ref guard would otherwise silently refuse (Task 10 addition A).
    expect(canConnect(fork, 's3', 's2')).toEqual({ ok: true })

    const reparented = fork.map((s) => (s.id === 's2' ? { ...s, causalityParent: 's3' } : s))
    expect(isOrderValid(reparented)).toBe(false) // proves the gap: s3 is still after s2

    const sorted = topologicallySortSteps(reparented)
    expect(isOrderValid(sorted)).toBe(true)
    expect(sorted.find((s) => s.id === 's2').causalityParent).toBe('s3')
    expect(sorted.map((s) => s.id)).toEqual(['s1', 's3', 's2'])
  })

  it('preserves relative order among steps that do not need to move', () => {
    // s3 and s4 are independent roots; only s2's re-parent onto s4 should
    // force any movement, and only as much as the invariant requires.
    const steps = [
      { id: 's1', causalityParent: null },
      { id: 's2', causalityParent: 's4' },
      { id: 's3', causalityParent: null },
      { id: 's4', causalityParent: null },
    ]
    const sorted = topologicallySortSteps(steps)
    expect(isOrderValid(sorted)).toBe(true)
    // s1 and s3 never depended on anything moving and keep their relative order.
    const ids = sorted.map((s) => s.id)
    expect(ids.indexOf('s1')).toBeLessThan(ids.indexOf('s2'))
    expect(ids.indexOf('s3')).toBeGreaterThan(-1)
    // Ancestor-first placement: s4 is pulled in just ahead of the step that
    // needs it; nothing else moves.
    expect(ids).toEqual(['s1', 's4', 's2', 's3'])
  })

  // Review finding: the old sort was a level-batched Kahn sort, so it emitted
  // every depth-N step before any depth-N+1 step and RE-ORDERED arrays that
  // were already legal. Array order is execution order — the DC's chain ran in
  // a different order after an edit that never touched those steps.
  it('returns a valid BRANCHING array unchanged (same reference), not level-sorted', () => {
    const branching = [
      { id: 'step-01', causalityParent: null },
      { id: 'step-02', causalityParent: 'step-01' },
      { id: 'step-03', causalityParent: 'step-02' },
      { id: 'step-04', causalityParent: 'step-01' }, // a new branch off the root
    ]
    expect(isOrderValid(branching)).toBe(true)
    expect(topologicallySortSteps(branching)).toBe(branching)
  })

  it('one "+ Add step" on a branching corpus chain (SIM-EDR-019 spine) leaves existing order alone', () => {
    // scenarios/edr/edr-019-akira-howling-scorpius-vcenter-to-esxi.yml:
    // 01 → 02 → 03 → 04 and 01 → 05 → 06 → 07.
    const edr019 = [
      { id: 'step-01', causalityParent: null },
      { id: 'step-02', causalityParent: 'step-01' },
      { id: 'step-03', causalityParent: 'step-02' },
      { id: 'step-04', causalityParent: 'step-03' },
      { id: 'step-05', causalityParent: 'step-01' },
      { id: 'step-06', causalityParent: 'step-05' },
      { id: 'step-07', causalityParent: 'step-06' },
    ]
    // ComposerView's addBlank appends, parents to the selected step, then sorts.
    const added = [...edr019, { id: 'step-08', causalityParent: 'step-03' }]
    expect(topologicallySortSteps(added)).toBe(added)
    expect(topologicallySortSteps(edr019)).toBe(edr019)
  })

  it('re-parenting onto a LATER step moves only that ancestor ahead: [01, 02<-03, 03<-01] → [01, 03, 02]', () => {
    const steps = [
      { id: 'step-01', causalityParent: null },
      { id: 'step-02', causalityParent: 'step-03' },
      { id: 'step-03', causalityParent: 'step-01' },
    ]
    const sorted = topologicallySortSteps(steps)
    expect(sorted.map((s) => s.id)).toEqual(['step-01', 'step-03', 'step-02'])
    expect(isOrderValid(sorted)).toBe(true)
  })

  it('keeps the relative order of an unrelated sibling branch while repairing another', () => {
    // s2 → s3 is one branch; s4 is an unrelated sibling. Only s5 is out of
    // order (its parent s6 sits after it). The level sort emitted
    // [s1, s2, s4, s6, s3, s5] — moving s3 behind s4, a step it has nothing
    // to do with.
    const steps = [
      { id: 's1', causalityParent: null },
      { id: 's2', causalityParent: 's1' },
      { id: 's3', causalityParent: 's2' },
      { id: 's4', causalityParent: 's1' },
      { id: 's5', causalityParent: 's6' },
      { id: 's6', causalityParent: 's1' },
    ]
    const sorted = topologicallySortSteps(steps)
    expect(isOrderValid(sorted)).toBe(true)
    expect(sorted.map((s) => s.id)).toEqual(['s1', 's2', 's3', 's4', 's6', 's5'])
  })

  it('never drops or duplicates a step on a dangling parent or a cycle', () => {
    const dangling = [
      { id: 's1', causalityParent: null },
      { id: 's2', causalityParent: 'gone' },
      { id: 's3', causalityParent: 's1' },
    ]
    // A dangling parent imposes no order — the array is already legal.
    expect(topologicallySortSteps(dangling)).toBe(dangling)

    const cyclic = [
      { id: 's1', causalityParent: null },
      { id: 's2', causalityParent: 's3' },
      { id: 's3', causalityParent: 's2' },
      { id: 's4', causalityParent: 'gone' },
    ]
    const out = topologicallySortSteps(cyclic)
    expect(out).toHaveLength(cyclic.length)
    expect(new Set(out).size).toBe(cyclic.length)
    expect([...out].sort((a, b) => a.id.localeCompare(b.id))).toEqual(cyclic)

    const selfRef = [{ id: 's1', causalityParent: 's1' }, { id: 's2', causalityParent: null }]
    expect(topologicallySortSteps(selfRef)).toBe(selfRef)
  })

  it('every canConnect-approved edge, once applied, yields a loader-legal array order', () => {
    // Sweep every (from,to) pair a small fork-shaped fixture's canConnect
    // approves, apply each as setCausalityParent would, and confirm the
    // post-sort array always satisfies the loader's rule — the property
    // Task 10 addition A exists to guarantee, not just one example of it.
    const base = [
      { id: 's1', causalityParent: null },
      { id: 's2', causalityParent: 's1' },
      { id: 's3', causalityParent: 's1' },
      { id: 's4', causalityParent: 's3' },
    ]
    let exercised = 0
    for (const from of base) {
      for (const to of base) {
        if (from.id === to.id) continue
        if (!canConnect(base, from.id, to.id).ok) continue
        const applied = base.map((s) => (s.id === to.id ? { ...s, causalityParent: from.id } : s))
        expect(isOrderValid(topologicallySortSteps(applied))).toBe(true)
        exercised += 1
      }
    }
    // Guard the guard: if nothing in the sweep was ever approved, the test
    // above would pass vacuously and prove nothing.
    expect(exercised).toBeGreaterThan(0)
  })
})
