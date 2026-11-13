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
 * Until each component has its own page (design v26 slices 3–4), a rail entry
 * opens the page the component is managed on today — Agents, Tenant,
 * Firewall — and the rest open their row on the Components page, which says
 * what POVengine can and cannot see about them. No entry is a dead end.
 */

const ROUTES = {
  pov: { destination: 'components', params: { open: 'pov' } },
  agent: { destination: 'agents' },
  tenant: { destination: 'tenants' },
  http: { destination: 'components', params: { open: 'http' } },
  bvm: { destination: 'components', params: { open: 'bvm' } },
  ngfw: { destination: 'firewall' },
  xdrc: { destination: 'components', params: { open: 'xdrc' } },
  engine: { destination: 'components', params: { open: 'engine' } },
}

// The HTTP collector's targets are managed on the Tenant page today
// (CollectorTargetsPanel); the Broker VM is a guided step on Get started.
// POVengine, XDR Collector and Cortex Engine have no page to configure on yet.
const CONFIGURE = {
  agent: { destination: 'agents' },
  tenant: { destination: 'tenants' },
  http: { destination: 'tenants' },
  bvm: { destination: 'start', params: { step: 'bvm' } },
  ngfw: { destination: 'firewall' },
}

// Where a problem on a component is explained when it has no Configure page:
// POVengine's degraded components are listed, with remediation, on the
// Launch gate.
const FIX = {
  pov: { destination: 'preflight' },
}

export function componentRoute(key) {
  return ROUTES[key] || { destination: 'components', params: { open: key } }
}

export function componentConfigure(key) {
  return CONFIGURE[key] || null
}

/** Where the ErrorBadge on a component sends the DC. */
export function componentFix(key) {
  return CONFIGURE[key] || FIX[key] || null
}

/** The component a route belongs to, so the rail can mark it current. */
export function componentKeyForRoute(destination, params = {}) {
  if (destination === 'components') return params?.open || null
  for (const [key, r] of Object.entries(ROUTES)) {
    if (r.destination === destination && !r.params) return key
  }
  return null
}
