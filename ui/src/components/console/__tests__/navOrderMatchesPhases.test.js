import { describe, it, expect } from 'vitest'

import { DESTINATIONS, navGroups } from '../../../app/destinations.jsx'
import { PHASES, flowFor } from '../../../app/povflow.js'

/**
 * The rail and the flow bar must tell the same story.
 *
 * There used to be TWO navigations on screen at once: a PhaseBar across the
 * top ("where am I in the run?") and a job-grouped sidebar down the left
 * (Operate / Analyze / Traffic / Infrastructure / Manage). The same fourteen
 * destinations appeared in two unrelated orders and a DC had to hold both
 * models at once. That was the redesign's original complaint.
 *
 * There is one model now: the rail groups ARE the phases, and the flow bar at
 * the foot names the current phase and the next action. But nothing in the
 * code DERIVES one from the other — destinations.jsx owns the registry and
 * povflow.js owns the phases — so this file is what keeps them honest. It
 * fails on the next edit that moves a destination between groups without
 * updating its flow entry, which is the only failure mode that matters here: a
 * drifted rail still renders perfectly and simply misleads.
 */
describe('nav order follows the POV run phases', () => {
  const grouped = DESTINATIONS.filter((d) => !d.hidden && d.group)

  // 'Start here' is not a phase. Overview and the Setup Wizard sit BEFORE the
  // run order rather than inside it, which is why they carry no phase numeral
  // in the rail and why Overview's flow index is -1.
  const PRELUDE = 'Start here'

  it('every grouped destination sits in the group its phase names', () => {
    for (const d of grouped) {
      const { phase } = flowFor(d.id)
      if (d.group === PRELUDE) {
        expect(
          phase, `'${d.id}' is in '${PRELUDE}' but claims phase ${phase}`,
        ).toBeLessThanOrEqual(0)
        continue
      }
      expect(
        phase, `destination '${d.id}' has no phase in povflow's map — ` +
               'add it there, or drop it from the rail',
      ).toBeGreaterThanOrEqual(0)
      expect(
        d.group, `destination '${d.id}' is in rail group '${d.group}' but its ` +
                 `phase is '${PHASES[phase].label}'`,
      ).toBe(PHASES[phase].label)
    }
  })

  it('rail groups appear in phase order, never job order', () => {
    const order = navGroups().map((g) => g.label)
    expect(order[0]).toBe(PRELUDE)

    const phaseRank = new Map(PHASES.map((p, i) => [p.label, i]))
    const phaseGroups = order.slice(1)
    for (const label of phaseGroups) {
      expect(phaseRank.has(label), `rail group '${label}' is not a phase`).toBe(true)
    }
    const ranks = phaseGroups.map((l) => phaseRank.get(l))
    expect(ranks, `rail groups render as ${order.join(' → ')}`)
      .toEqual([...ranks].sort((a, b) => a - b))
  })

  it('does not invent a rail group for Launch', () => {
    // Launch is a state the Composer enters after preflight, not a place you
    // navigate to. A rail entry for it would offer a destination that does not
    // exist — so the rail numerals read 1, 2, 3, 5, 6 with 4 deliberately absent.
    expect(navGroups().map((g) => g.label)).not.toContain('Launch')
  })

  it('numbers the phase groups and leaves the prelude unnumbered', () => {
    const groups = navGroups()
    expect(groups.find((g) => g.label === PRELUDE).num).toBe('')
    const nums = groups.filter((g) => g.label !== PRELUDE).map((g) => g.num)
    expect(nums).toEqual(['1', '2', '3', '5', '6'])
  })

  it('reads top to bottom: the prelude, then Scope first and Prove last', () => {
    const order = navGroups().map((g) => g.label)
    expect(order[0]).toBe(PRELUDE)
    expect(order[1]).toBe('Scope')
    expect(order[order.length - 1]).toBe('Prove')
  })

  it('every rail destination has a real icon asset, never a glyph', () => {
    // The DS forbids Unicode-glyph icons in brand material, and the previous
    // rail was built entirely from them. A missing icon must be null rather
    // than an interpolated `undefined`, which fired a 404 per render.
    for (const g of navGroups()) {
      for (const item of g.items) {
        expect(item.iconUrl, `'${item.id}' has no rail icon`).toMatch(/^\/icons\/[a-z-]+\.png$/)
      }
    }
  })

  it('gives every phase-bar CTA a destination that exists', () => {
    // The CTA rule is `Next: <destination as the rail names it>`. A rename
    // that misses one produces a null ctaDest rather than a wrong jump, and
    // this asserts none of them is null except on the terminal surface.
    const ids = new Set(DESTINATIONS.map((d) => d.id))
    for (const d of grouped) {
      const { cta, ctaDest } = flowFor(d.id)
      if (d.id === 'proof') {
        expect(cta).toBe('Export report')
        continue
      }
      expect(cta, `'${d.id}' CTA`).toMatch(/^Next: /)
      expect(ids.has(ctaDest), `'${d.id}' points at unknown destination '${ctaDest}'`).toBe(true)
    }
  })
})
