/**
 * componentRoutes.js — where each of the eight components lives in the console.
 *
 * Two questions per component, answered in one place so the rail, the
 * Components page and the router cannot disagree:
 *
 *   componentRoute(key)     the page the rail entry opens
 *   componentConfigure(key) the page where the component is configured, or
 *                           null when the console has nowhere to send the DC
 *
 * Every component has its own page (design v26 §4.2): the rail entry opens
 * it, and Configure opens its Setup tab. The Components page (`components`,
 * `?open=<key>`) is the hub that lists all eight.
 */

const ROUTES = {
  pov: { destination: 'component-pov' },
  agent: { destination: 'component-agents' },
  tenant: { destination: 'component-tenant' },
  http: { destination: 'component-http' },
  bvm: { destination: 'component-bvm' },
  ngfw: { destination: 'component-firewall' },
  xdrc: { destination: 'component-xdrc' },
  engine: { destination: 'component-engine' },
}

// Configure is the component page's Setup tab, for every component.
const CONFIGURE = Object.fromEntries(
  Object.entries(ROUTES).map(([k, r]) => [k, { destination: r.destination, params: { tab: 'setup' } }]),
)

export function componentRoute(key) {
  return ROUTES[key] || { destination: 'components', params: { open: key } }
}

export function componentConfigure(key) {
  return CONFIGURE[key] || null
}

/** Where the ErrorBadge on a component sends the DC: its Setup. */
export function componentFix(key) {
  return CONFIGURE[key] || null
}

/** The component a route belongs to, so the rail can mark it current. */
export function componentKeyForRoute(destination, params = {}) {
  if (destination === 'components') return params?.open || null
  for (const [key, r] of Object.entries(ROUTES)) {
    if (r.destination === destination && !r.params) return key
  }
  return null
}
