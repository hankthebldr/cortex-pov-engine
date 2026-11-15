/**
 * Reports API — GET /api/reports/executions.
 *
 * Its own module so it ships in the Reports chunk: `client.js` is part of the
 * entry chunk, which has a size budget (composerChunkBoundary.test.js).
 */
import { filenameFromDisposition, request } from './client.js'

/** The query string for the route; unset filters are left out. */
function executionsQuery({ since, until, plane, status, agent, includeDrafts } = {}, format) {
  const qs = new URLSearchParams()
  if (since) qs.set('since', since)
  if (until) qs.set('until', until)
  if (plane) qs.set('plane', plane)
  if (status) qs.set('status', status)
  if (agent) qs.set('agent', agent)
  if (includeDrafts) qs.set('include_drafts', 'true')
  if (format) qs.set('format', format)
  const s = qs.toString()
  return s ? `?${s}` : ''
}

/**
 * Every run on this engine, rolled up.
 *
 * @param {{since?: string, until?: string, plane?: string, status?: string,
 *   agent?: string, includeDrafts?: boolean}} [filters]  `since` / `until` are ISO-8601
 * @returns {Promise<Object>} `{generated_at, window, filters, filter_options, empty,
 *   engine_runs, totals, by_scenario, by_technique, by_test_case, by_plane, by_agent,
 *   detection_sources}`
 */
export async function getExecutionsReport(filters = {}) {
  return request(`/api/reports/executions${executionsQuery(filters)}`)
}

/**
 * The same report as a file: one CSV row per scenario, or the JSON body.
 *
 * @param {Object} [filters]  as `getExecutionsReport`
 * @param {'csv'|'json'} [format='csv']
 * @returns {Promise<{blob: Blob, filename: string}>}
 */
export async function downloadExecutionsReport(filters = {}, format = 'csv') {
  const fmt = format === 'json' ? 'json' : 'csv'
  const response = await request(`/api/reports/executions${executionsQuery(filters, fmt)}`, {
    _returnBlob: true,
  })
  const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, '')
  const filename = filenameFromDisposition(response.headers?.get?.('Content-Disposition'))
    || `cortexsim-executions-${stamp}.${fmt}`
  return { blob: await response.blob(), filename }
}
