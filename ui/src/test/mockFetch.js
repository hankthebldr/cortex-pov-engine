/**
 * Helpers for mocking the fetch calls made by ``src/api/client.js``.
 *
 * The client uses ``window.location.origin`` as base URL, which in jsdom is
 * ``http://localhost``.  All paths start with ``/api`` so route-matching is
 * straightforward.
 */
import { vi } from 'vitest'

/**
 * Install a route table on globalThis.fetch.
 *
 * @param {Object<string, any|function>} routes  Map of "METHOD /path" → response
 *   The value can be:
 *     - a plain object (returned as JSON, status 200)
 *     - a Response (returned as-is)
 *     - a function (url, init) → Response | object | Promise<…>
 *
 * Example:
 *   installRoutes({
 *     'GET /api/scenarios': { scenarios: [{ scenario_id: 'SIM-EDR-001', plane: 'EDR' }] },
 *     'POST /api/run': { run_id: 'r-1', mode: 'push' },
 *   })
 */
export function installRoutes(routes) {
  const handler = vi.fn(async (input, init = {}) => {
    const url = typeof input === 'string' ? input : input.url
    const method = (init.method || 'GET').toUpperCase()
    const path = new URL(url, 'http://localhost').pathname
    const key = `${method} ${path}`
    const matcher =
      routes[key] ??
      routes[`${method} ${path.replace(/\/\d+/g, '/:id')}`] ??
      Object.entries(routes).find(([k]) => {
        if (!k.includes(':')) return false
        const [m, pat] = k.split(' ')
        if (m !== method) return false
        const regex = new RegExp(
          '^' + pat.replace(/:[^/]+/g, '[^/]+') + '$',
        )
        return regex.test(path)
      })?.[1]

    if (matcher === undefined) {
      return new Response(
        JSON.stringify({ error: `no mock for ${key}`, code: 'NO_MOCK' }),
        { status: 404, headers: { 'content-type': 'application/json' } },
      )
    }

    const resolved =
      typeof matcher === 'function' ? await matcher(url, init) : matcher
    if (resolved instanceof Response) return resolved
    return new Response(JSON.stringify(resolved), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })
  })
  globalThis.fetch = handler
  return handler
}

/** Convenience: shape that PlaneSelector expects. */
export const scenarioFixtures = [
  { scenario_id: 'SIM-EDR-001', name: 'Credential Dumping', plane: 'EDR', mitre_tactic: 'TA0006', mitre_technique: 'T1003.008' },
  { scenario_id: 'SIM-EDR-002', name: 'Reverse Shell', plane: 'EDR', mitre_tactic: 'TA0011', mitre_technique: 'T1059.004' },
  { scenario_id: 'SIM-CDR-001', name: 'K8s Privesc', plane: 'CDR', mitre_tactic: 'TA0004', mitre_technique: 'T1611' },
  { scenario_id: 'SIM-NDR-001', name: 'C2 Beacon', plane: 'NDR', mitre_tactic: 'TA0011', mitre_technique: 'T1071.001' },
  { scenario_id: 'SIM-ITDR-001', name: 'Kerberoast', plane: 'ITDR', mitre_tactic: 'TA0006', mitre_technique: 'T1558.003' },
]
