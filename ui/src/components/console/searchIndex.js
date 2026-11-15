/**
 * searchIndex.js — what ⌘K can find (design v26 §4.5): pages, components,
 * scenarios and TTP cards, each as { id, title, meta, kind, icon, onSelect }.
 *
 * Pure apart from the `navigate` it is handed. `icon` is a name from the
 * rail's line-icon set (/icons/<name>.png) and is drawn with PageIcon.
 *
 * COMPONENTS — the eight things a POV is built from (v26 §1). ONE table:
 * names and icons come from componentModel.js and where each opens from
 * app/componentRoutes.js — the same two the rail's Components submenu and the
 * Components page read — so ⌘K, the header's instance status and the rail
 * cannot send a DC to different places for the same component.
 */

import { COMPONENTS as PARTS } from './componentModel.js'
import { componentRoute } from '../../app/componentRoutes.js'

export const COMPONENTS = Object.freeze(PARTS.map((c) => ({ key: c.key, label: c.name, icon: c.icon })))

/**
 * Where a component opens — `{ destination, params? }` — or null when that
 * destination is not in this build (a search result that leads nowhere is a
 * button without an action).
 */
export function componentDestination(key, isValid = () => true) {
  if (!PARTS.some((c) => c.key === key)) return null
  const route = componentRoute(key)
  return route && isValid(route.destination) ? route : null
}

/**
 * @param {Object} p
 * @param {Array}    p.destinations  the registry (DESTINATIONS)
 * @param {Function} p.isValid       (id) => boolean
 * @param {Array}    p.scenarios     the scenario corpus
 * @param {Array}    p.ttps          TTP card summaries ({ id, name }), [] until loaded
 * @param {Function} p.navigate      (destinationId, params?) => void
 */
export function buildSearchItems({ destinations = [], isValid = () => true, scenarios = [], ttps = [], navigate = () => {} } = {}) {
  // A page that IS a component's page is listed once, as the component. A
  // component opened as a row of another page (`params`) does not hide it.
  const componentPages = new Set(PARTS
    .map((c) => componentRoute(c.key))
    .filter((r) => r && !r.params)
    .map((r) => r.destination))

  const pages = destinations
    .filter((d) => !d.redirect && d.id !== 'readiness' && !componentPages.has(d.id))
    .map((d) => ({
      id: `page-${d.id}`,
      title: d.label,
      kind: 'Page',
      icon: d.icon || null,
      onSelect: () => navigate(d.id),
    }))

  const components = COMPONENTS
    .map((c) => ({ c, to: componentDestination(c.key, isValid) }))
    .filter(({ to }) => to)
    .map(({ c, to }) => ({
      id: `component-${c.key}`,
      title: c.label,
      kind: 'Component',
      icon: c.icon,
      onSelect: () => (to.params ? navigate(to.destination, to.params) : navigate(to.destination)),
    }))

  const scenarioIcon = destinations.find((d) => d.id === 'library')?.icon || null
  const scenarioItems = (Array.isArray(scenarios) ? scenarios : []).map((s) => {
    const id = s.scenario_id || s.id
    return {
      id: `scenario-${id}`,
      title: s.name ? `${id} · ${s.name}` : id,
      meta: s.plane || null,
      kind: 'Scenario',
      icon: scenarioIcon,
      onSelect: () => navigate('library', { open: id }),
    }
  })

  const ttpIcon = destinations.find((d) => d.id === 'ttps')?.icon || null
  const ttpItems = (Array.isArray(ttps) ? ttps : []).map((t) => ({
    id: `ttp-${t.id}`,
    title: t.name && t.name !== t.id ? `${t.id} · ${t.name}` : t.id,
    kind: 'TTP card',
    icon: ttpIcon,
    onSelect: () => navigate('ttps', { open: t.id }),
  }))

  return { pages, components, scenarios: scenarioItems, ttps: ttpItems }
}
