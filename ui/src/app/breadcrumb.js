/**
 * breadcrumb.js — where the DC is, as journey › page › object (design C9).
 *
 *   journey   the rail group the page belongs to ("Run & observe"); its crumb
 *             opens that journey's first rail page
 *   page      the rail item, then the page itself when the page is a tab or
 *             a sub-page of it ("Catalog › UC / TC index",
 *             "Components › Agents")
 *   object    the run, scenario, card, test case, package or component the
 *             URL opens, when it names one
 *
 * Read entirely off the destination registry, so the rail, the tab strip and
 * the breadcrumb cannot disagree about where a page lives. Pure: the header
 * renders what this returns.
 */
import { DESTINATIONS, JOURNEYS, getDestination, navOwner } from './destinations.jsx'
import { openParam, uctcOpen } from './openParam.js'
import { COMPONENTS } from '../components/console/componentModel.js'

/** The first rail page of a journey: where the journey's crumb leads. */
function journeyHome(groupId) {
  return DESTINATIONS.find((d) => d.group === groupId && !d.hidden)?.id || null
}

const SCENARIO_PAGES = { library: [], composer: ['from'], preflight: ['arm'], guided: ['arm'] }

// A run id is a UUID; the crumb shows its first eight characters, as the run
// list and the "Result of" bar do, and keeps the whole id in its title.
function shortRunId(id) {
  const s = String(id || '')
  return s.length > 12 ? s.slice(0, 8) : s
}

/**
 * The object the URL opens on `destinationId`, as the last crumb
 * (`{ label, title? }`), or null when the URL names none.
 */
export function objectCrumb(destinationId, params = {}) {
  const p = params || {}
  const named = (label) => ({ label })
  if (destinationId === 'runs' && p.compare === '1') return named('Compare runs')
  if (destinationId === 'runs' || destinationId === 'proof') {
    const run = openParam(p, 'run')
    return run ? { label: `Run ${shortRunId(run)}`, title: `Run ${run}` } : null
  }
  if (SCENARIO_PAGES[destinationId]) {
    const id = openParam(p, ...SCENARIO_PAGES[destinationId])
    return id ? named(`Scenario ${id}`) : null
  }
  if (destinationId === 'components') {
    const key = openParam(p)
    return key ? named(COMPONENTS.find((c) => c.key === key)?.name || key) : null
  }
  if (destinationId === 'ttps') {
    const id = openParam(p, 'ttp')
    return id ? named(`TTP card ${id}`) : null
  }
  if (destinationId === 'uctc') {
    const { uc, tc } = uctcOpen(p)
    if (tc) return named(`Test case ${tc}`)
    return uc ? named(`Use case ${uc}`) : null
  }
  if (destinationId === 'adapters') {
    const id = openParam(p, 'tool')
    return id ? named(`Package ${id}`) : null
  }
  const id = openParam(p)
  return id ? named(id) : null
}

/**
 * The crumbs for a route, outermost first. Every crumb but the last carries
 * the `destination` (and `params`, when it needs any) it opens; the last is
 * where the DC is and carries no link.
 *
 * @param {string} destinationId
 * @param {Object} [params]
 * @returns {Array<{label:string, title?:string, destination?:string, params?:Object}>}
 */
export function breadcrumbFor(destinationId, params = {}) {
  const dest = getDestination(destinationId)
  if (!dest) return []
  const owner = getDestination(navOwner(dest.id)) || dest
  const journey = JOURNEYS.find((j) => j.id === owner.group)

  const crumbs = []
  if (journey) crumbs.push({ label: journey.label, destination: journeyHome(journey.id) })
  if (owner.id !== dest.id && owner.label !== dest.label) {
    crumbs.push({ label: owner.label, destination: owner.id })
  }
  crumbs.push({ label: dest.label, destination: dest.id })
  const object = objectCrumb(dest.id, params)
  if (object) crumbs.push(object)

  // The last crumb is where the DC is: it keeps its label, never a link.
  const here = crumbs.pop()
  return [...crumbs, here.title ? { label: here.label, title: here.title } : { label: here.label }]
}
