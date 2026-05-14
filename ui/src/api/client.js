/**
 * CortexSim API Client
 * Base URL: same origin as the served SPA (FastAPI serves both UI and API)
 */

const BASE_URL = window.location.origin

/**
 * Core fetch wrapper — handles JSON parsing and structured error extraction.
 * On non-2xx response, throws Error with the message from JSON body.
 */
async function request(path, options = {}) {
  const url = `${BASE_URL}${path}`
  const defaults = {
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    },
  }
  const config = {
    ...defaults,
    ...options,
    headers: { ...defaults.headers, ...(options.headers || {}) },
  }

  const response = await fetch(url, config)

  if (!response.ok) {
    let errorMessage = `HTTP ${response.status} — ${response.statusText}`
    try {
      const errorBody = await response.json()
      errorMessage = errorBody.detail || errorBody.error || errorBody.message || errorMessage
    } catch {
      // Response body was not JSON — keep the HTTP status message
    }
    throw new Error(errorMessage)
  }

  // For blob responses (downloads), return raw Response
  if (options._returnBlob) {
    return response
  }

  // Empty body (204 No Content, etc.)
  const contentLength = response.headers.get('content-length')
  if (response.status === 204 || contentLength === '0') {
    return null
  }

  return response.json()
}

// ─── Health ──────────────────────────────────────────────────────────────────

/**
 * GET /api/health
 * Returns: { status: "ok", version: "1.0.0", hostname: "..." }
 */
export async function getHealth() {
  return request('/api/health')
}

// ─── Scenarios ───────────────────────────────────────────────────────────────

/**
 * GET /api/scenarios[?plane=...&uc_ref=...]
 * @param {Object} [params]
 * @param {string} [params.plane]   Detection plane filter (e.g. "CDR")
 * @param {string} [params.uc_ref]  UC reference filter (e.g. "UCS-CDR-03")
 * @returns {Promise<Array>}
 */
export async function getScenarios(params = {}) {
  const qs = new URLSearchParams()
  if (params.plane)  qs.set('plane',  params.plane)
  if (params.uc_ref) qs.set('uc_ref', params.uc_ref)
  const query = qs.toString() ? `?${qs.toString()}` : ''
  return request(`/api/scenarios${query}`)
}

/**
 * GET /api/scenarios/:id
 * @param {string|number} id
 * @returns {Promise<Object>}
 */
export async function getScenario(id) {
  return request(`/api/scenarios/${id}`)
}

/**
 * GET /api/scenarios/:id/download?format=bash|k8s
 * Returns a Blob for file download.
 * @param {string|number} id
 * @param {'bash'|'k8s'} format
 * @returns {Promise<Blob>}
 */
export async function downloadScenario(id, format = 'bash') {
  const response = await request(`/api/scenarios/${id}/download?format=${format}`, {
    _returnBlob: true,
  })
  return response.blob()
}

// ─── Runs ─────────────────────────────────────────────────────────────────────

/**
 * POST /api/run
 * @param {Object} body
 * @param {string} body.scenario_id
 * @param {'pull'|'push'} body.mode
 * @param {string} [body.target_agent_id]
 * @param {string} [body.identity]
 * @returns {Promise<Object>}
 */
export async function postRun(body) {
  return request('/api/run', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

/**
 * GET /api/runs
 * @returns {Promise<Array>}
 */
export async function getRuns() {
  return request('/api/runs')
}

/**
 * GET /api/runs/:runId
 * @param {string|number} runId
 * @returns {Promise<Object>}
 */
export async function getRun(runId) {
  return request(`/api/runs/${runId}`)
}

// ─── Results ─────────────────────────────────────────────────────────────────

/**
 * GET /api/results
 * @returns {Promise<Array>}
 */
export async function getResults() {
  return request('/api/results')
}

/**
 * GET /api/results/:runId
 * Returns results with coverage stats and MTTD data.
 * @param {string|number} runId
 * @returns {Promise<Object>} { results, coverage, mttd }
 */
export async function getResultsForRun(runId) {
  return request(`/api/results/${runId}`)
}

/**
 * PUT /api/results/:resultId/validate
 * DC marks a detection as observed/not observed. Sets observed_at for MTTD.
 * @param {number} resultId
 * @param {boolean} observed
 * @param {string} [notes]
 * @returns {Promise<Object>}
 */
export async function validateResult(resultId, observed, notes) {
  return request(`/api/results/${resultId}/validate`, {
    method: 'PUT',
    body: JSON.stringify({ observed, notes: notes || null }),
  })
}

/**
 * PUT /api/results/:resultId/notes
 * @param {number} resultId
 * @param {string} notes
 * @returns {Promise<Object>}
 */
export async function updateResultNotes(resultId, notes) {
  return request(`/api/results/${resultId}/notes`, {
    method: 'PUT',
    body: JSON.stringify({ notes }),
  })
}

// ─── Tools ───────────────────────────────────────────────────────────────────

/**
 * GET /api/tools
 * @returns {Promise<Array>}
 */
export async function getTools() {
  return request('/api/tools')
}

/**
 * POST /api/tools/:toolName/install
 * @param {string} toolName
 * @returns {Promise<Object>}
 */
export async function installTool(toolName) {
  return request(`/api/tools/${toolName}/install`, { method: 'POST' })
}

/**
 * POST /api/tools/:toolName/start
 * @param {string} toolName
 * @param {Object} [params]  Runtime params (e.g. { port, xsiam_endpoint })
 * @returns {Promise<Object>}
 */
export async function startTool(toolName, params = {}) {
  return request(`/api/tools/${toolName}/start`, {
    method: 'POST',
    body: JSON.stringify(params),
  })
}

/**
 * POST /api/tools/:toolName/stop
 * @param {string} toolName
 * @returns {Promise<Object>}
 */
export async function stopTool(toolName) {
  return request(`/api/tools/${toolName}/stop`, { method: 'POST' })
}

/**
 * GET /api/tools/:toolName/status
 * @param {string} toolName
 * @returns {Promise<Object>}
 */
export async function getToolStatus(toolName) {
  return request(`/api/tools/${toolName}/status`)
}

// ─── Reports ────────────────────────────────────────────────────────────────

/**
 * GET /api/runs/:runId/report?format=markdown
 * Returns markdown report as downloadable blob.
 * @param {string} runId
 * @returns {Promise<Blob>}
 */
export async function downloadReport(runId) {
  const response = await request(`/api/runs/${runId}/report?format=markdown`, {
    _returnBlob: true,
  })
  return response.blob()
}

/**
 * Phase 8 — POV deliverable artifacts.
 *
 * Three endpoints emit the worked-example shape from
 * lab_cortex_analytics_pov/.
 */
export async function downloadReportMatrix(runId) {
  const response = await request(`/api/runs/${runId}/report/matrix`, {
    _returnBlob: true,
  })
  return response.blob()
}

export async function downloadReportNavigator(runId) {
  const response = await request(`/api/runs/${runId}/report/navigator`, {
    _returnBlob: true,
  })
  return response.blob()
}

export async function downloadReportBundle(runId) {
  const response = await request(`/api/runs/${runId}/report/bundle`, {
    _returnBlob: true,
  })
  return response.blob()
}

/**
 * GET /api/runs/:runId/report?format=json
 * Returns structured report data.
 * @param {string} runId
 * @returns {Promise<Object>}
 */
export async function getReportJSON(runId) {
  return request(`/api/runs/${runId}/report?format=json`)
}

// ─── Infra (IaC Topology Generator) ──────────────────────────────────────────

/**
 * GET /api/infra/modules?provider=aws
 * @param {string} provider
 * @returns {Promise<{modules: Array, total: number}>}
 */
export async function getInfraModules(provider = 'aws') {
  return request(`/api/infra/modules?provider=${encodeURIComponent(provider)}`)
}

/**
 * POST /api/infra/generate
 * @param {Object} body  { provider, region, modules, params }
 * @returns {Promise<Object>}
 */
export async function generateInfra(body) {
  return request('/api/infra/generate', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

/**
 * GET /api/infra/bundles
 * @returns {Promise<{bundles: Array, total: number}>}
 */
export async function getInfraBundles() {
  return request('/api/infra/bundles')
}

/**
 * GET /api/infra/bundles/:bundle_id/download
 * Returns the tar.gz as a Blob for download.
 * @param {string} bundleId
 * @returns {Promise<Blob>}
 */
export async function downloadInfraBundle(bundleId) {
  const response = await request(`/api/infra/bundles/${bundleId}/download`, {
    _returnBlob: true,
  })
  return response.blob()
}

// ─── MITRE ──────────────────────────────────────────────────────────────────

/**
 * GET /api/mitre/coverage
 * Returns MITRE ATT&CK coverage data for heatmap visualization.
 * @returns {Promise<Object>} { techniques, by_tactic, summary }
 */
export async function getMitreCoverage() {
  return request('/api/mitre/coverage')
}

// ─── Agents ──────────────────────────────────────────────────────────────────

/**
 * GET /api/agents
 * @returns {Promise<Array>}
 */
export async function getAgents() {
  return request('/api/agents')
}

// ─── EAL Traffic Simulator ───────────────────────────────────────────────────
//
// API surface for the plugin-based EAL simulator (core/eal_simulator/).
// See core/api/eal.py and docs/wiki/EAL-Simulator.md for the contract.

/**
 * GET /api/eal/plugins
 * @returns {Promise<{plugins: Array, total: number}>}
 */
export async function getEalPlugins() {
  return request('/api/eal/plugins')
}

/**
 * GET /api/eal/plugins/:name
 * Returns the plugin's metadata + Pydantic JSON schema for its params model.
 * @param {string} name  Plugin Meta.name (e.g. "c2_http_beacon")
 */
export async function getEalPlugin(name) {
  return request(`/api/eal/plugins/${encodeURIComponent(name)}`)
}

/**
 * POST /api/eal/campaigns
 * @param {Object} campaign  Full Campaign object (see core/eal_simulator/campaign.py)
 */
export async function postEalCampaign(campaign) {
  return request('/api/eal/campaigns', {
    method: 'POST',
    body: JSON.stringify(campaign),
  })
}

/**
 * GET /api/eal/campaigns
 * @returns {Promise<{campaigns: Array, total: number}>}
 */
export async function getEalCampaigns() {
  return request('/api/eal/campaigns')
}

/**
 * GET /api/eal/campaigns/:id
 */
export async function getEalCampaign(campaignId) {
  return request(`/api/eal/campaigns/${encodeURIComponent(campaignId)}`)
}

/**
 * POST /api/eal/campaigns/:id/launch
 * @param {string} campaignId
 * @param {Object} [opts]  { dry_run?: boolean, operator?: string }
 * @returns {Promise<{run_id, campaign_id, status, dry_run}>}
 */
export async function launchEalCampaign(campaignId, opts = {}) {
  return request(`/api/eal/campaigns/${encodeURIComponent(campaignId)}/launch`, {
    method: 'POST',
    body: JSON.stringify(opts),
  })
}

/**
 * GET /api/eal/runs[?campaign_id=...]
 */
export async function getEalRuns(params = {}) {
  const qs = new URLSearchParams()
  if (params.campaign_id) qs.set('campaign_id', params.campaign_id)
  const query = qs.toString() ? `?${qs.toString()}` : ''
  return request(`/api/eal/runs${query}`)
}

/**
 * GET /api/eal/runs/:run_id
 * Single run detail (status, dry_run, step_results, error, operator, timestamps).
 */
export async function getEalRun(runId) {
  return request(`/api/eal/runs/${encodeURIComponent(runId)}`)
}
