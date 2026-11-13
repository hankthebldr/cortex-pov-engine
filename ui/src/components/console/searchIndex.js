/**
 * searchIndex.js — what ⌘K can find (design v26 §4.5): pages, components,
 * scenarios and TTP cards, each as { id, title, meta, kind, icon, onSelect }.
 *
 * Pure apart from the `navigate` it is handed. `icon` is a name from the
 * rail's line-icon set (/icons/<name>.png) and is drawn with PageIcon.
 *
 * COMPONENTS — the eight things a POV is built from (v26 §1). Each names the
 * page that manages it (`page`) and, while that page does not exist yet, the
 * place it is set up today (`fallback`). A component with neither is not
 * listed: a search result that leads nowhere is a button without an action.
 * When the component pages land (v26 §11 rows 3–4) only `page` changes here,
 * and the header's instance status follows POVengine's entry automatically.
 */

export const COMPONENTS = Object.freeze([
  { key: 'povengine', label: 'POVengine',      icon: 'settings-edit',    page: null,       fallback: 'preflight' },
  { key: 'agents',    label: 'Agents',         icon: 'fingerprint-scan', page: 'agents',   fallback: null },
  { key: 'tenant',    label: 'Tenant',         icon: 'people',           page: 'tenants',  fallback: null },
  { key: 'http',      label: 'HTTP collector', icon: 'doc-search',       page: null,       fallback: 'start' },
  { key: 'bvm',       label: 'Broker VM',      icon: 'building',         page: null,       fallback: 'start' },
  { key: 'firewall',  label: 'Firewall',       icon: 'threat-network',   page: 'firewall', fallback: null },
  { key: 'xdrc',      label: 'XDR Collector',  icon: 'apps-grid',        page: null,       fallback: null },
  { key: 'engine',    label: 'Cortex Engine',  icon: 'keyboard',         page: null,       fallback: null },
])

/** The destination a component opens, or null when the console has no place for it yet. */
export function componentDestination(key, isValid = () => true) {
  const c = COMPONENTS.find((x) => x.key === key)
  if (!c) return null
  if (c.page && isValid(c.page)) return c.page
  if (c.fallback && isValid(c.fallback)) return c.fallback
  return null
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
  // A page that IS a component's page is listed once, as the component.
  const componentPages = new Set(COMPONENTS.map((c) => c.page).filter(Boolean))

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
      onSelect: () => navigate(to),
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
