import { describe, it, expect } from 'vitest'

import fs from 'node:fs'
import path from 'node:path'

import {
  DESTINATIONS, SECTIONS, navGroups, navOwner, sectionFor, isValidDestination,
} from '../../../app/destinations.jsx'

/**
 * The rail is a task list, and every page belongs to exactly one task.
 *
 * The rail used to carry 17 destinations grouped by POV phase, with a flow bar
 * restating the same model at the foot of the shell. It is five tasks plus a
 * Manage pair now, and every other page is a tab inside one of them. Nothing
 * DERIVES the tab strips from the rail — `SECTIONS` and `navParent` are written
 * by hand in destinations.jsx — so this file keeps them consistent: a page
 * whose parent is not on the rail, or a tab that points at nothing, renders
 * perfectly and simply strands the DC.
 */
describe('task rail', () => {
  const rail = DESTINATIONS.filter((d) => !d.hidden && d.group)
  const railIds = new Set(rail.map((d) => d.id))

  it('is the five tasks, then the Manage pair', () => {
    const groups = navGroups()
    expect(groups.map((g) => g.label)).toEqual(['', 'Manage'])
    expect(groups[0].items.map((i) => i.label)).toEqual(['Get started', 'Simulate', 'Runs', 'Results', 'Catalog'])
    expect(groups[1].items.map((i) => i.label)).toEqual(['Agents', 'Tenant'])
  })

  it('every off-rail page names a parent that is on the rail', () => {
    for (const d of DESTINATIONS.filter((x) => x.hidden)) {
      expect(railIds.has(d.navParent), `'${d.id}' has parent '${d.navParent}', which is not a rail item`).toBe(true)
      expect(navOwner(d.id)).toBe(d.navParent)
    }
  })

  it('every tab is a real destination whose owner is the task that shows it', () => {
    for (const [owner, tabs] of Object.entries(SECTIONS)) {
      expect(railIds.has(owner)).toBe(true)
      for (const [id] of tabs) {
        expect(isValidDestination(id), `tab '${id}' is not a destination`).toBe(true)
        expect(navOwner(id), `tab '${id}' lights up the wrong rail item`).toBe(owner)
        expect(sectionFor(id).active).toBe(id)
      }
    }
  })

  it('the legacy readiness id lands on the Launch gate tab', () => {
    expect(sectionFor('readiness')).toMatchObject({ owner: 'library', active: 'preflight' })
  })

  it('tasks without tabs render no strip', () => {
    for (const id of ['start', 'runs', 'agents', 'tenants']) expect(sectionFor(id)).toBeNull()
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

  /**
   * A DESTINATION ID IS A ROUTE, NOT A LABEL.
   *
   * This caught a live one. Renaming `adapters` to `packages` — purely to match
   * the rail's new label — silently broke `#/adapters`. It did not 404: the
   * hash router validates the id and falls back to the default destination, so
   * a deep link, a bookmark, a ⌘K entry and an e2e fixture all quietly landed
   * on the Library instead. The only thing that noticed was the e2e suite,
   * which needs a live SimCore and therefore only runs in CI.
   *
   * So the unit suite reads the e2e specs' own routes and checks them against
   * the registry. It is a slightly unusual coupling and it is deliberate: the
   * routes those specs navigate are the closest thing this repo has to a
   * published URL contract, and a rename that orphans one should fail in
   * seconds locally rather than in a CI job that needs a backend.
   */
  describe('route ids the e2e suite depends on still resolve', () => {
    const E2E_DIR = path.resolve(__dirname, '../../../../tests/e2e')

    const routes = (() => {
      const found = new Map()
      for (const file of fs.readdirSync(E2E_DIR).filter((f) => f.endsWith('.ts'))) {
        const src = fs.readFileSync(path.join(E2E_DIR, file), 'utf8')
        // `goto('/#/adapters?tool=…')` and `{ dest: 'adapters' }`
        for (const m of src.matchAll(/#\/([a-z][a-z-]*)/g)) found.set(m[1], file)
        for (const m of src.matchAll(/\bdest:\s*'([a-z][a-z-]*)'/g)) found.set(m[1], file)
      }
      return [...found.entries()]
    })()

    it('finds routes to check', () => {
      expect(routes.length).toBeGreaterThan(3)
    })

    it.each(routes)('#/%s (used by %s) is a real destination', (id) => {
      expect(
        isValidDestination(id),
        `'${id}' is not in the registry, so the router silently falls back to ` +
        "the default destination. If this id was renamed, keep the old one as a " +
        'hidden destination rather than letting the route disappear.',
      ).toBe(true)
    })
  })
})
