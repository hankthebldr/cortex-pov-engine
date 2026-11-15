import { describe, it, expect } from 'vitest'

import fs from 'node:fs'
import path from 'node:path'

import React from 'react'
import { render } from '@testing-library/react'
import {
  DESTINATIONS, SECTIONS, getDestination, navGroups, navOwner, sectionFor, isValidDestination,
} from '../../../app/destinations.jsx'

void React

/**
 * The rail is the three jobs a DC does, in order, plus Reference (design v26
 * §1): 1 Onboard & connect · 2 Compose & simulate · 3 Run & observe. Every page
 * belongs to exactly one rail item. Nothing DERIVES the tab strips from the
 * rail — `SECTIONS` and `navParent` are written by hand in destinations.jsx —
 * so this file keeps them consistent: a page whose parent is not on the rail,
 * or a tab that points at nothing, renders perfectly and simply strands the DC.
 */
describe('task rail', () => {
  const rail = DESTINATIONS.filter((d) => !d.hidden && d.group)
  const railIds = new Set(rail.map((d) => d.id))

  it('is three numbered journeys, then Reference', () => {
    const groups = navGroups()
    expect(groups.map((g) => [g.num, g.label])).toEqual([
      ['1', 'Onboard & connect'],
      ['2', 'Compose & simulate'],
      ['3', 'Run & observe'],
      ['', 'Reference'],
    ])
    expect(groups[0].items.map((i) => i.label)).toEqual(['Get started', 'Components'])
    expect(groups[1].items.map((i) => i.label)).toEqual(['Scenarios', 'Build a chain', 'Launch gate'])
    expect(groups[2].items.map((i) => i.label)).toEqual(['Runs', 'Results', 'Dashboards'])
    expect(groups[3].items.map((i) => i.label)).toEqual(['Catalog'])
  })

  it("Simulate's tabs are rail items now, so Scenarios has no tab strip", () => {
    expect(SECTIONS.library).toBeUndefined()
    for (const id of ['library', 'composer', 'preflight']) {
      expect(sectionFor(id), id).toBeNull()
      expect(navOwner(id), id).toBe(id)
    }
  })

  it('Components expands into the eight components, each leading to a real page', () => {
    const comp = navGroups().flatMap((g) => g.items).find((i) => i.id === 'components')
    expect(comp.children.map((c) => c.label)).toEqual([
      'POVengine', 'Agents', 'Tenant', 'HTTP collector', 'Broker VM', 'Firewall', 'XDR Collector', 'Cortex Engine',
    ])
    for (const child of comp.children) {
      expect(isValidDestination(child.destination), `${child.label} leads to '${child.destination}'`).toBe(true)
      expect(getDestination(child.destination).redirect, `${child.label} leads to a redirect`).toBeFalsy()
    }
  })

  it('a component page lights up Components', () => {
    for (const id of ['component-agents', 'component-tenant', 'component-firewall']) {
      expect(navOwner(id), id).toBe('components')
    }
  })

  it('every component in the submenu opens its own component page', () => {
    const comp = navGroups().flatMap((g) => g.items).find((i) => i.id === 'components')
    const to = Object.fromEntries(comp.children.map((c) => [c.key, c.destination]))
    expect(to).toEqual({
      pov: 'component-pov', agent: 'component-agents', tenant: 'component-tenant', http: 'component-http',
      bvm: 'component-bvm', ngfw: 'component-firewall', xdrc: 'component-xdrc', engine: 'component-engine',
    })
    for (const id of Object.values(to)) expect(navOwner(id), id).toBe('components')
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

  it('Dashboards is a rail item under Run & observe, on its old route', () => {
    const observe = navGroups().find((g) => g.label === 'Run & observe')
    const dash = observe.items.find((i) => i.id === 'coverage')
    expect(dash.label).toBe('Dashboards')
    expect(getDestination('coverage').hidden).toBeFalsy()
    expect(navOwner('coverage')).toBe('coverage')
    expect(sectionFor('coverage')).toBeNull()
    // Results kept only its report: the Coverage tab moved out with the page.
    expect(SECTIONS.proof.map(([id]) => id)).toEqual(['proof'])
  })

  it('the legacy readiness id lights up the Launch gate', () => {
    expect(navOwner('readiness')).toBe('preflight')
    expect(sectionFor('readiness')).toBeNull()
  })

  it('tasks without tabs render no strip', () => {
    for (const id of ['start', 'components', 'runs', 'component-agents', 'component-tenant']) expect(sectionFor(id)).toBeNull()
  })

  it('retired routes forward to the page that replaced them', () => {
    const forwards = (id, params = {}) => {
      const calls = []
      const Redirect = getDestination(id).Component
      render(<Redirect params={params} onNavigate={(...a) => calls.push(a)} />)
      return calls[0]
    }
    expect(forwards('setup')[0]).toBe('start')
    expect(forwards('scope')[0]).toBe('start')
    expect(forwards('validation')[0]).toBe('proof')
    // The Manage pages became component pages (v26 §5). A bookmark lands on
    // the management tab it used to be, keeping its own params.
    expect(forwards('agents', { open: 'jb-01' })).toEqual(['component-agents', { tab: 'fleet', open: 'jb-01' }])
    expect(forwards('tenants')).toEqual(['component-tenant', { tab: 'readback' }])
    expect(forwards('firewall')).toEqual(['component-firewall', { tab: 'sources' }])
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
