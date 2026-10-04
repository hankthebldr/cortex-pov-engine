/**
 * Every destination renders itself, and only itself.
 *
 * THE FAILURE MODE THIS EXISTS FOR
 * The redesign shipped this bug twice, in two different files, and both times
 * it was invisible in review: an edit sliced out a section's CLOSING tags, so
 * one destination's markup swallowed the ones after it — or the reverse, a
 * section's opening conditional was consumed and it rendered unconditionally on
 * every page. The symptoms were "eight screens went blank" and "the run detail
 * header appeared on all seventeen destinations". Neither throws. Neither fails
 * a unit test of the component in isolation. Both are only visible by walking
 * every destination and asking what actually rendered.
 *
 * So this walks all of them. It asserts three things per surface:
 *   1. it mounts without throwing,
 *   2. it produces exactly ONE top-level heading, and
 *   3. no OTHER destination's title is on the page.
 *
 * (3) is the one that catches the leak. A destination that renders its own
 * heading correctly AND a second destination's heading underneath is the exact
 * shape of both historical bugs.
 *
 * SimCore is stubbed empty on purpose: the degraded path is where these
 * defects hide, because a surface with no data has the least on screen to make
 * a leak obvious.
 */
import React from 'react'
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import { EnvironmentContext, DEFAULT_ENV } from '../../context/EnvironmentContext.jsx'
import { DESTINATIONS } from '../destinations.jsx'

void React

// The heading each destination is expected to own. Where a surface titles
// itself from data (a run id), the expectation is the stable part of it.
const TITLES = {
  start: /^get started$/i,
  library: /^library$/i,
  runs: /^runs$/i,
  proof: /.+/,
  ttps: /ttp cards/i,
  agents: /agents/i,
  tenants: /^tenant$/i,
  composer: /composer/i,
  preflight: /launch gate/i,
  coverage: /coverage/i,
  adapters: /^packages$/i,
  streams: /data\s*streams/i,
  uctc: /uc \/ tc index/i,
  overview: /prove what the stack/i,
}

// Titles that must NOT appear on a destination that is not theirs. Deliberately
// the distinctive ones — a generic word would false-positive on body copy.
const FOREIGN = [
  ['start', /^get started$/i],
  ['preflight', /launch gate/i],
  ['overview', /prove what the stack/i],
  ['tenants', /^tenant$/i],
]

// The five tasks and the Manage pair are the rail; their tab pages are walked
// too, because a tab is a page a DC lands on as often as a rail item. Redirect
// stubs (retired seed pages) render nothing by design, and the legacy aliases
// (guided/eal/environments/readiness) compose other surfaces and are covered
// by their own suites.
const RAIL = DESTINATIONS.filter((d) => !d.hidden && d.group)
const WALKED = DESTINATIONS.filter((d) => TITLES[d.id])

function mount(dest) {
  const Surface = dest.Component
  return render(
    <EnvironmentContext.Provider value={DEFAULT_ENV}>
      <Surface params={{}} setParams={() => {}} onNavigate={() => {}} />
    </EnvironmentContext.Provider>,
  )
}

describe('every destination renders itself, and only itself', () => {
  beforeEach(() => {
    installRoutes({
      'GET /api/scenarios': { scenarios: [] },
      'GET /api/agents': [],
      'GET /api/tenants': [],
      'GET /api/runs': { runs: [] },
      'GET /api/health': { status: 'ok' },
      'GET /api/ttps': { ttps: [] },
      'GET /api/tools/adapters': { adapters: [] },
    })
  })
  afterEach(cleanup)

  it('the rail is the five tasks plus the Manage pair', () => {
    expect(RAIL.map((d) => d.label)).toEqual(
      ['Get started', 'Simulate', 'Runs', 'Results', 'Catalog', 'Agents', 'Tenant'],
    )
  })

  it('retired seed pages redirect instead of rendering', () => {
    const retired = DESTINATIONS.filter((d) => d.redirect).map((d) => d.id).sort()
    expect(retired).toEqual(['cli', 'scope', 'setup', 'validation'])
  })

  it.each(WALKED.map((d) => [d.id, d]))('%s mounts and owns its heading', async (id, dest) => {
    mount(dest)
    // Lazy surfaces resolve their chunk first; a heading is the signal the real
    // component (not the Suspense fallback) is on screen.
    await waitFor(
      () => expect(screen.queryAllByRole('heading', { level: 1 }).length).toBeGreaterThan(0),
      { timeout: 5000 },
    )
    const h1s = screen.queryAllByRole('heading', { level: 1 })
    expect(h1s.length, `${id} rendered ${h1s.length} top-level headings: ` +
      h1s.map((h) => `"${h.textContent.trim()}"`).join(', ')).toBe(1)
    expect(h1s[0].textContent).toMatch(TITLES[id])
  })

  it.each(WALKED.map((d) => [d.id, d]))('%s does not leak another destination onto the page', async (id, dest) => {
    mount(dest)
    await waitFor(
      () => expect(screen.queryAllByRole('heading', { level: 1 }).length).toBeGreaterThan(0),
      { timeout: 5000 },
    )
    for (const [otherId, pattern] of FOREIGN) {
      if (otherId === id) continue
      const heading = screen.queryAllByRole('heading').find((h) => pattern.test(h.textContent))
      expect(heading, `${id} is rendering ${otherId}'s heading "${heading?.textContent}"`).toBeUndefined()
    }
  })
})
