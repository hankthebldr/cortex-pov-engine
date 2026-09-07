import { describe, it, expect } from 'vitest'

import { DESTINATIONS, navGroups } from '../../../app/destinations.jsx'
import { PHASES, phaseIndexFor } from '../PhaseBar.jsx'

/**
 * The sidebar and the phase bar must tell the same story.
 *
 * The console shows a DC two navigations at once: PhaseBar across the top
 * ("where am I in the run?") and DestinationNav down the left. Those used to
 * disagree — the sidebar grouped by job (Operate / Analyze / Traffic /
 * Infrastructure / Manage) while the phase bar ran Scope → Compose → Preflight
 * → Launch → Observe → Prove, so the same fourteen destinations appeared in two
 * unrelated orders and the DC had to hold both models at once.
 *
 * They are now one order. Nothing in the code *derives* one from the other —
 * destinations.jsx owns the array and PhaseBar.jsx owns the phases — so this
 * file is what keeps them honest. It fails on the next edit that reorders one
 * without the other, which is the only failure mode that matters here: a
 * drifted nav still renders perfectly and simply misleads.
 */
describe('nav order follows the POV run phases', () => {
  const grouped = DESTINATIONS.filter((d) => !d.hidden && d.group)

  it('every grouped destination sits in the group its phase names', () => {
    for (const d of grouped) {
      const i = phaseIndexFor(d.id)
      expect(
        i, `destination '${d.id}' has no phase in PhaseBar's PHASE_BY_DEST — ` +
           'add it there, or drop it from the sidebar'
      ).toBeGreaterThanOrEqual(0)
      expect(
        d.group, `destination '${d.id}' is in nav group '${d.group}' but its ` +
                 `phase is '${PHASES[i].label}'`
      ).toBe(PHASES[i].label)
    }
  })

  it('nav groups appear in phase order, never job order', () => {
    const order = navGroups().map((g) => g.label)
    const phaseRank = new Map(PHASES.map((p, i) => [p.label, i]))

    for (const label of order) {
      expect(
        phaseRank.has(label), `nav group '${label}' is not a PhaseBar phase`
      ).toBe(true)
    }

    const ranks = order.map((l) => phaseRank.get(l))
    expect(ranks, `nav groups render as ${order.join(' → ')}`)
      .toEqual([...ranks].sort((a, b) => a - b))
  })

  it('does not invent a nav group for Launch', () => {
    // Launch is a state the Composer enters after preflight, not a place you
    // navigate to. A sidebar entry for it would offer a destination that does
    // not exist — see the PHASE_BY_DEST comment in PhaseBar.jsx.
    expect(navGroups().map((g) => g.label)).not.toContain('Launch')
  })

  it('Scope comes first and Prove last — the run reads top to bottom', () => {
    const order = navGroups().map((g) => g.label)
    expect(order[0]).toBe('Scope')
    expect(order[order.length - 1]).toBe('Prove')
  })
})
