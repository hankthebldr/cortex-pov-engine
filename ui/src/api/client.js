/**
 * CortexSim API Client
 * Base URL: same origin as the served SPA (FastAPI serves both UI and API)
 */

const BASE_URL = window.location.origin

/**
 * Render FastAPI's *validation* error body (422) as operator-readable text.
 *
 * Pydantic emits `detail: [{loc: ["body","target_agent_id"], msg: "Input
 * should be a valid string", ...}]`. Collapsing that to "HTTP 422 —
 * Unprocessable Entity" throws away the only part that tells the DC what to
 * fix, so name the offending field and quote the validator verbatim.
 *
 * @param {Array} detail  FastAPI `detail` array
 * @returns {string|null} e.g. "target_agent_id: Input should be a valid string"
 */
function formatValidationDetail(detail) {
  if (!Array.isArray(detail) || detail.length === 0) return null
  const parts = detail
    .map((d) => {
      if (!d || typeof d !== 'object') return String(d ?? '')
      // Drop the leading "body"/"query" segment — the operator cares about the field.
      const loc = Array.isArray(d.loc) ? d.loc.filter((s) => s !== 'body' && s !== 'query') : []
      const field = loc.join('.')
      const msg = d.msg || d.type || 'invalid value'
      return field ? `${field}: ${msg}` : msg
    })
    .filter(Boolean)
  return parts.length ? parts.join('; ') : null
}

/**
 * Build an Error that CARRIES the project's structured error contract
 * (`{error, code, detail}`) instead of flattening it to a status line.
 *
 * `err.message` stays the human sentence a toast renders, and `err.code` /
 * `err.detail` / `err.status` ride along so a caller that wants to explain the
 * failure precisely (the launch panel) can, without re-parsing prose.
 */
function apiError(message, { code = null, detail = null, status = null } = {}) {
  const err = new Error(message)
  err.code = code
  err.detail = detail
  err.status = status
  return err
}

/**
 * How long the console waits for SimCore before calling it a failure.
 *
 * WHY THIS EXISTS: `fetch` has no default timeout, so a SimCore that accepts the
 * connection and never answers hangs the calling surface FOREVER — a spinner
 * that spins for the length of a customer meeting. That is not hypothetical
 * here: a blocking tenant call inside an async handler was measured stalling
 * `/api/health` for 28 seconds, during which every console request queued behind
 * it. A spinner and a blank screen are the same outcome to the operator, and
 * neither says which one it is. A named REQUEST_TIMEOUT does.
 */
export const REQUEST_TIMEOUT_MS = 20_000
/** Reports and bundles are generated on demand and legitimately take longer. */
export const DOWNLOAD_TIMEOUT_MS = 60_000

/**
 * Attach an abort signal unless the caller brought their own.
 *
 * A caller-supplied signal WINS — `useShelf` owns a 180 s staging deadline and
 * a 20 s default would cut a legitimate large download in half.
 */
function armTimeout(options, ms) {
  if (options.signal) return { signal: options.signal, cleanup: () => {}, timedOut: () => false }
  if (typeof AbortController === 'undefined') {
    return { signal: undefined, cleanup: () => {}, timedOut: () => false }
  }
  const controller = new AbortController()
  let fired = false
  const timer = setTimeout(() => { fired = true; controller.abort() }, ms)
  return {
    signal: controller.signal,
    cleanup: () => clearTimeout(timer),
    timedOut: () => fired,
  }
}

/**
 * Core fetch wrapper — handles JSON parsing and structured error extraction.
 * On non-2xx response, throws Error with the message from JSON body.
 */
export async function request(path, options = {}) {
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

  const budgetMs = options._timeoutMs
    ?? (options._returnBlob ? DOWNLOAD_TIMEOUT_MS : REQUEST_TIMEOUT_MS)
  const timeout = armTimeout(options, budgetMs)
  if (timeout.signal) config.signal = timeout.signal

  let response
  try {
    response = await fetch(url, config)
  } catch (err) {
    if (timeout.timedOut()) {
      throw apiError(
        `SimCore did not answer ${path} in ${Math.round(budgetMs / 1000)}s. `
        + 'Check /api/health — a blocked event loop or an unreachable dependency stalls every '
        + 'request behind it.',
        { code: 'REQUEST_TIMEOUT', detail: { path, timeout_ms: budgetMs }, status: null },
      )
    }
    throw err
  } finally {
    timeout.cleanup()
  }

  if (!response.ok) {
    const fallback = `HTTP ${response.status} — ${response.statusText}`
    let errorMessage = fallback
    let code = null
    let detail = null
    try {
      const errorBody = await response.json()
      // FastAPI returns errors as { detail: ... } where detail is one of:
      //   • an ARRAY  — Pydantic request-validation failures (422)
      //   • an OBJECT — this project's {error, code, detail} contract
      //   • a STRING  — a plain HTTPException message
      // All three carry the actionable part; only the status line does not.
      if (Array.isArray(errorBody.detail)) {
        detail = errorBody.detail
        errorMessage = formatValidationDetail(errorBody.detail) || fallback
        code = 'VALIDATION_ERROR'
      } else if (errorBody.detail && typeof errorBody.detail === 'object') {
        const d = errorBody.detail
        code = d.code || null
        detail = d.detail ?? null
        errorMessage = `${d.error || d.detail || fallback}${d.code ? ` [${d.code}]` : ''}`
      } else {
        code = errorBody.code || null
        detail = errorBody.detail ?? null
        errorMessage = errorBody.detail || errorBody.error || errorBody.message || fallback
      }
    } catch {
      // Response body was not JSON — keep the HTTP status message
    }
    throw apiError(errorMessage, { code, detail, status: response.status })
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
  if (params.plane)   qs.set('plane',   params.plane)
  if (params.uc_ref)  qs.set('uc_ref',  params.uc_ref)
  if (params.ttp_ref) qs.set('ttp_ref', params.ttp_ref)
  const query = qs.toString() ? `?${qs.toString()}` : ''
  const data = await request(`/api/scenarios${query}`)
  // List endpoints wrap as {scenarios: [...], total: N}; components expect bare arrays.
  return Array.isArray(data) ? data : data?.scenarios ?? []
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
 * GET /api/scenarios/:id/infra-hints
 *
 * Resolve a scenario's external_tools[] into IaC generator hints
 * (adapter_refs the scenario declares + suggested_modules derived
 * from each adapter's install.iac_module). Used by the LabView's
 * "Hint from scenario" affordance to auto-fill the auto-pull picker.
 *
 * @param {string} scenarioId
 * @returns {Promise<{
 *   scenario_id: string,
 *   plane: string,
 *   adapter_refs: string[],
 *   resolved_adapters: Array<{adapter_ref, name, tier, safety_class, iac_module}>,
 *   unresolved_refs: string[],
 *   suggested_modules: string[],
 * }>}
 */
export async function getScenarioInfraHints(scenarioId) {
  return request(`/api/scenarios/${encodeURIComponent(scenarioId)}/infra-hints`)
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
  const data = await request('/api/runs')
  return Array.isArray(data) ? data : data?.runs ?? []
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
  const data = await request('/api/results')
  return Array.isArray(data) ? data : data?.results ?? []
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
  const data = await request('/api/tools')
  return Array.isArray(data) ? data : data?.tools ?? []
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

// ─── Tool-adapter catalog (tools/packs/*.yml) ───────────────────────────────

/**
 * GET /api/tools/adapters
 *
 * List the static tool-adapter catalog. Filters compose with logical AND
 * server-side; unknown values quietly return an empty list.
 *
 * @param {Object} [filters]
 * @param {string} [filters.plane]         e.g. 'EDR' | 'CDR' | 'NDR' | 'ITDR' | ...
 * @param {number} [filters.tier]          1..5
 * @param {string} [filters.safety_class]  'safe' | 'dual-use-lab-only' | 'c2-framework' | 'destructive'
 * @param {string} [filters.category]      controlled-vocab category
 * @returns {Promise<{adapters: Array<Object>, total: number}>}
 */
export async function getToolAdapters(filters = {}) {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(filters)) {
    if (v !== undefined && v !== null && v !== '') qs.append(k, String(v))
  }
  const suffix = qs.toString() ? `?${qs.toString()}` : ''
  return request(`/api/tools/adapters${suffix}`)
}

/**
 * GET /api/tools/adapters/:adapterId
 *
 * Full ToolAdapterSchema for a single entry — used by the drill-down panel.
 *
 * @param {string} adapterId  e.g. 'TOOL-NMAP'
 * @returns {Promise<Object>}
 */
export async function getToolAdapter(adapterId) {
  return request(`/api/tools/adapters/${encodeURIComponent(adapterId)}`)
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
 * GET /api/runs/:runId/report?format=scorecard | scorecard-html
 * Executive efficacy scorecard (the CISO one-pager) as a downloadable blob.
 * Markdown (text/plain) by default; pass { html: true } for the HTML render.
 * @param {string} runId
 * @param {{ html?: boolean }} [opts]
 * @returns {Promise<Blob>}
 */
export async function downloadReportScorecard(runId, { html = false } = {}) {
  const format = html ? 'scorecard-html' : 'scorecard'
  const response = await request(`/api/runs/${runId}/report?format=${format}`, {
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
 * @param {Object} body
 * @param {string} body.provider          'aws' | 'gcp' | 'azure'
 * @param {string} body.region            cloud region (e.g. 'us-east-1')
 * @param {string[]} body.modules         IaC modules the operator picked
 * @param {string[]} [body.adapter_refs]  optional adapter_ref ids — backend
 *                                        auto-includes each adapter's iac_module
 * @param {Object} body.params            project / SSH / sizing params
 * @returns {Promise<Object>}             response includes auto_included_modules[]
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
  const data = await request('/api/agents')
  return Array.isArray(data) ? data : data?.agents ?? []
}

/**
 * DELETE /api/agents/:agentId — prune a registered beacon from the registry.
 * @param {string} agentId
 * @returns {Promise<{status:string, agent_id:string}>}
 */
export async function deleteAgent(agentId) {
  return request(`/api/agents/${encodeURIComponent(agentId)}`, { method: 'DELETE' })
}

/**
 * Build the installer download URL for an agent (bash .sh / PowerShell .ps1).
 * Server URL is auto-derived server-side from the request.
 *
 * Pass `token` to get the ENROLLMENT installer (SimCore assigns the agent id);
 * pass `id` for the legacy self-asserted path. Prefer the token: an operator
 * who invents ids produces duplicates and typos, and the token is revocable.
 *
 * @param {{os?:string, id?:string, interval?:number, token?:string}} opts
 */
export function agentInstallUrl({ os = 'linux', id = null, interval = 10, token = null } = {}) {
  const q = new URLSearchParams({ os, interval: String(interval) })
  if (token) q.set('token', token)
  if (id) q.set('id', id)
  return `/api/agents/install?${q.toString()}`
}

// ─── Agent enrollment tokens ─────────────────────────────────────────────────
// The documented front door for onboarding: mint a TTL/max-uses/revocable
// token, hand the jumpbox ONE line, and let SimCore assign the agent id.

/**
 * POST /api/agents/enroll/tokens — mint an enrollment token.
 *
 * The full token value is returned EXACTLY once, here; every later read shows
 * only its tail. Callers must surface it immediately or it is unrecoverable.
 *
 * @param {{label?:string, ttl_seconds?:number, max_uses?:number}} opts
 * @returns {Promise<{id:number, token:string, label:?string, expires_at:?string,
 *                    max_uses:number, used_count:number, remaining_uses:number}>}
 */
export async function mintEnrollmentToken({ label = null, ttl_seconds = 3600, max_uses = 1 } = {}) {
  return request('/api/agents/enroll/tokens', {
    method: 'POST',
    body: JSON.stringify({ label, ttl_seconds, max_uses }),
  })
}

/**
 * GET /api/agents/enroll/tokens — list minted tokens (tails only, with a
 * server-derived `valid` flag folding revoked/expired/exhausted into one bit).
 * @returns {Promise<Array>}
 */
export async function listEnrollmentTokens() {
  const data = await request('/api/agents/enroll/tokens')
  return Array.isArray(data) ? data : data?.tokens ?? []
}

/**
 * DELETE /api/agents/enroll/tokens/:id — revoke a token so it can no longer
 * be redeemed. Already-enrolled agents are unaffected.
 * @param {number|string} tokenId
 */
export async function revokeEnrollmentToken(tokenId) {
  return request(`/api/agents/enroll/tokens/${encodeURIComponent(tokenId)}`, { method: 'DELETE' })
}

// ─── TTP browser (detection_scanner/ttps/*.json) ────────────────────────────

/**
 * GET /api/ttps
 *
 * List the TTP corpus as slim summary cards. Filters compose with
 * logical AND server-side; unknown values quietly return an empty list.
 *
 * @param {Object} [filters]
 * @param {string} [filters.status]   'active' | 'draft' | 'deprecated'
 * @param {string} [filters.tactic]   MITRE tactic id (e.g. 'TA0006')
 * @param {string} [filters.platform] 'linux' | 'windows' | 'macos' | ...
 * @returns {Promise<{ttps: Array<Object>, total: number}>}
 */
export async function getTtps(filters = {}) {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(filters)) {
    if (v !== undefined && v !== null && v !== '') qs.append(k, String(v))
  }
  const suffix = qs.toString() ? `?${qs.toString()}` : ''
  return request(`/api/ttps${suffix}`)
}

/**
 * GET /api/ttps/:ttp_id
 *
 * Full TTP card document + reverse cross-references to the tool-adapter
 * catalog (``referenced_by_adapters``).
 *
 * @param {string} ttpId  e.g. 'TTP-2026-0004'
 * @returns {Promise<Object>}
 */
export async function getTtp(ttpId) {
  return request(`/api/ttps/${encodeURIComponent(ttpId)}`)
}

/**
 * GET /api/ttps/:ttp_id/runs
 *
 * Run history — every Run whose seeded Results cite this TTP, rolled
 * up to one entry per run with expected/observed counts + min MTTD.
 *
 * @param {string} ttpId
 * @param {Object} [opts]
 * @param {number} [opts.limit=20]
 * @returns {Promise<{ttp_id: string, runs: Array, total: number}>}
 */
export async function getTtpRuns(ttpId, { limit } = {}) {
  const qs = limit ? `?limit=${encodeURIComponent(limit)}` : ''
  return request(`/api/ttps/${encodeURIComponent(ttpId)}/runs${qs}`)
}

/**
 * GET /api/ttps/_schema — full JSON Schema for live-validating the
 * authoring wizard payload. Cacheable client-side; the schema rarely
 * changes.
 */
export async function getTtpSchema() {
  return request('/api/ttps/_schema')
}

/**
 * POST /api/ttps — create a new draft TTP card.
 * The backend forces ``status: draft`` regardless of payload value.
 */
export async function createTtp(payload) {
  return request('/api/ttps', { method: 'POST', body: JSON.stringify(payload) })
}

/**
 * PUT /api/ttps/:ttp_id — update an existing TTP in place.
 * The payload's ``id`` must equal ``ttpId``.
 */
export async function updateTtp(ttpId, payload) {
  return request(`/api/ttps/${encodeURIComponent(ttpId)}`, {
    method: 'PUT', body: JSON.stringify(payload),
  })
}

/**
 * POST /api/ttps/:ttp_id/promote — move a draft to active.
 * Idempotent — already-active cards return ``moved: false``.
 */
export async function promoteTtp(ttpId) {
  return request(`/api/ttps/${encodeURIComponent(ttpId)}/promote`, {
    method: 'POST',
  })
}

/**
 * POST /api/ttps/_reload — hot-reload the catalog from disk.
 * Useful when the operator edits a TTP file out-of-band.
 */
export async function reloadTtpCatalog() {
  return request('/api/ttps/_reload', { method: 'POST' })
}

// ─── UC / TC Index ───────────────────────────────────────────────────────────
//
// Read-only browser over the FY27 v2.2 master Use-Case / Test-Case index
// (docs/uc_tc_mapping/_v2.2-source/), joined to the engine's own evidence
// (Scenario.tc_refs → Run.tc_verdict). See core/api/uctc.py.
//
// Every response carries the envelope { index_loaded, index_version, … }.
// When the snapshot is missing from a deploy the API returns 200 with
// ``index_loaded: false`` and empty collections — callers MUST render that
// as a degraded state, never as "0 test cases".
//
// All list helpers return the RAW wrapper object (like getTtps), not a bare
// array, so the envelope survives to the surface.

/** Build a querystring from a filter bag, dropping empty values. */
function _qs(filters = {}) {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(filters)) {
    if (v !== undefined && v !== null && v !== '') qs.append(k, String(v))
  }
  const s = qs.toString()
  return s ? `?${s}` : ''
}

/**
 * GET /api/uctc/summary
 *
 * Header numbers in one call — index totals, per-validation-class /
 * tier / priority / sheet breakdowns, and the evidence rollup.
 *
 * @returns {Promise<{index_loaded: boolean, index_version: ?string,
 *                    totals: Object, by_validation_class: Object,
 *                    by_tier: Object, by_priority: Object,
 *                    by_sheet: Object, evidence: Object}>}
 */
export async function getUcTcSummary() {
  return request('/api/uctc/summary')
}

/**
 * GET /api/uctc/use-cases
 *
 * The 49 use cases with per-UC counts + coverage percentages.
 *
 * @param {Object} [filters]
 * @param {string} [filters.sheet]            'SecOps' | 'Cloud'
 * @param {string} [filters.subdomain]        FY27 subdomain
 * @param {string} [filters.evidenced]        'all' | 'yes' | 'no' | 'partial'
 * @param {boolean} [filters.include_inactive]
 * @returns {Promise<{use_cases: Array<Object>, total: number}>}
 */
export async function getUcTcUseCases(filters = {}) {
  return request(`/api/uctc/use-cases${_qs(filters)}`)
}

/**
 * GET /api/uctc/use-cases/:uc_id
 *
 * One use case + its UCS groups + every child test case (TCSummary).
 *
 * @param {string} ucId  e.g. 'UC-EDR'
 */
export async function getUcTcUseCase(ucId) {
  return request(`/api/uctc/use-cases/${encodeURIComponent(ucId)}`)
}

/**
 * GET /api/uctc/test-cases
 *
 * The main table — all 266 TCSummary rows. Filters compose with logical
 * AND server-side; unknown values quietly return an empty list. There is
 * no pagination: the surface fetches once and filters client-side.
 *
 * @param {Object} [filters]
 * @param {string}  [filters.uc_id]
 * @param {string}  [filters.ucs_id]
 * @param {string}  [filters.validation_class]  comma-list, e.g. 'DET,HNT'
 * @param {string}  [filters.tier]
 * @param {string}  [filters.priority]
 * @param {string}  [filters.sheet]
 * @param {string}  [filters.pov_scenario_id]
 * @param {boolean} [filters.evidenced]
 * @param {boolean} [filters.scoreable]
 * @param {string}  [filters.plane]            implies evidenced
 * @param {boolean} [filters.include_inactive]
 * @returns {Promise<{test_cases: Array<Object>, total: number, index_total: number}>}
 */
export async function getUcTcTestCases(filters = {}) {
  return request(`/api/uctc/test-cases${_qs(filters)}`)
}

/**
 * GET /api/uctc/test-cases/:tc_id
 *
 * Full test-case detail — measurement contract, entitlements, parent UC +
 * UCS siblings, POV payload, the scenarios that evidence it, and the run
 * verdict rollup. This is the endpoint that turns "mapped" into "proven".
 *
 * @param {string} tcId  e.g. 'TC-EDR-03'
 */
export async function getUcTcTestCase(tcId) {
  return request(`/api/uctc/test-cases/${encodeURIComponent(tcId)}`)
}

/**
 * GET /api/uctc/coverage
 *
 * Every rollup in one payload — by use case (sorted worst-first), by
 * plane, by validation class, by tier, by priority, by sheet.
 *
 * @param {Object} [opts]
 * @param {boolean} [opts.include_inactive]
 */
export async function getUcTcCoverage(opts = {}) {
  return request(`/api/uctc/coverage${_qs(opts)}`)
}

/**
 * GET /api/uctc/gaps
 *
 * The UNEVIDENCED test cases — the gap is the point of the surface.
 * Defaults to the actionable DET/HNT slice.
 *
 * @param {Object} [filters]
 * @param {string}  [filters.validation_class]     default 'DET,HNT'
 * @param {string}  [filters.priority]
 * @param {string}  [filters.uc_id]
 * @param {boolean} [filters.include_unscoreable]  default true
 * @param {boolean} [filters.include_inactive]
 * @returns {Promise<{gaps: Array<Object>, total: number,
 *                    by_use_case: Array, by_priority: Object, scope: Object}>}
 */
export async function getUcTcGaps(filters = {}) {
  return request(`/api/uctc/gaps${_qs(filters)}`)
}

/**
 * GET /api/uctc/by-scenario/:scenario_id
 *
 * Forward view for a Library-inspector deep link — the test cases one
 * engine scenario claims to evidence, plus its derived entitlements.
 *
 * @param {string} scenarioId  e.g. 'SIM-EDR-001'
 */
export async function getUcTcByScenario(scenarioId) {
  return request(`/api/uctc/by-scenario/${encodeURIComponent(scenarioId)}`)
}

// ─── Assertions — the POS / PLT / AUT proof mechanism ────────────────────────
//
// Scenarios prove DET/HNT rows of the UC/TC index. POS/PLT/AUT rows are not
// detections — a posture finding must be discovered, a platform capability
// must be present, an automation outcome must occur inside a budget — so they
// are proven by ``assertions/{pos,plt,aut}/*.yml`` instead. See
// core/api/assertions.py and docs/uc_tc_mapping/assertions.md.

/**
 * GET /api/assertions
 *
 * Every loaded assertion, PLUS every artifact the loader refused and why
 * (``rejected[]``). The falsifiability guard is only worth something if the
 * artifacts it blocked are visible, so callers must render that list.
 *
 * The endpoint is optional: a SimCore built before the router landed answers
 * 404, which callers treat as "mechanism not deployed" — never as zero
 * coverage.
 *
 * @param {Object} filters  {validation_class, tc_ref, kind, status, probe}
 * @returns {Promise<{assertions: Array, count: number, total: number,
 *                    by_validation_class: Object, rejected: Array,
 *                    warnings: Array, probes: Array}>}
 */
export async function getAssertions(filters = {}) {
  return request(`/api/assertions${_qs(filters)}`)
}

/**
 * GET /api/assertions/:assertion_id
 * @param {string} assertionId  e.g. 'PLT-IR-008'
 */
export async function getAssertion(assertionId) {
  return request(`/api/assertions/${encodeURIComponent(assertionId)}`)
}

/**
 * GET /api/assertions/runs
 *
 * Recorded evaluations. An AUTHORED assertion is a claim; only a run carries a
 * ``tc_verdict``, and only a credential-backed non-dry run can carry anything
 * other than ``pending``. Callers must not present authorship as measurement.
 *
 * @param {Object} filters  {assertion_id, limit}
 * @returns {Promise<{runs: Array, count: number}>}
 */
export async function getAssertionRuns(filters = {}) {
  return request(`/api/assertions/runs${_qs(filters)}`)
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

// ─── XSIAM Tenant Management ─────────────────────────────────────────────────
//
// Tenant CRUD uses the generic /api/credentials/integrations surface (kind=xsiam_tenant).
// Live-tenant operations (/test, /metrics, /xql) use the typed /api/xsiam router.

/**
 * GET /api/credentials/integrations?kind=xsiam_tenant
 * Returns registered XSIAM tenants (credentials redacted).
 * @returns {Promise<Array>}
 */
export async function listXsiamTenants() {
  const data = await request('/api/credentials/integrations?kind=xsiam_tenant')
  return Array.isArray(data) ? data : data?.integrations ?? []
}

/**
 * PUT /api/credentials/integrations
 * Register or replace an XSIAM tenant. The API key is accepted but never returned.
 * @param {{ name, base_url, region, api_key_id, api_key, auth_mode? }} tenant
 * @returns {Promise<Object>}  IntegrationCredential row (redacted)
 */
export async function registerXsiamTenant({ name, base_url, region, api_key_id, api_key, auth_mode = 'standard' }) {
  return request('/api/credentials/integrations', {
    method: 'PUT',
    body: JSON.stringify({
      name,
      kind: 'xsiam_tenant',
      plaintext_secret: api_key,
      config: { base_url, region, auth_mode, api_key_id },
      secret_type_hint: 'xsiam_api_key',
      description: `XSIAM tenant: ${name}`,
    }),
  })
}

/**
 * DELETE /api/credentials/integrations/:name
 * Remove a registered tenant and its encrypted key.
 * @param {string} name
 * @returns {Promise<null>}
 */
export async function deleteXsiamTenant(name) {
  return request(`/api/credentials/integrations/${encodeURIComponent(name)}`, { method: 'DELETE' })
}

/**
 * POST /api/xsiam/tenants/:name/test
 * Run a live liveness/health probe against the tenant.
 * @param {string} name
 * @returns {Promise<{ ok: boolean, status: object, last_verified_at: string }>}
 */
export async function testXsiamTenant(name) {
  return request(`/api/xsiam/tenants/${encodeURIComponent(name)}/test`, { method: 'POST' })
}

/**
 * GET /api/xsiam/tenants/:name/health
 * Cached health pill data (last verified status + timestamp).
 * @param {string} name
 * @returns {Promise<Object>}
 */
export async function getXsiamTenantHealth(name) {
  return request(`/api/xsiam/tenants/${encodeURIComponent(name)}/health`)
}

/**
 * GET /api/xsiam/tenants/:name/metrics
 * Run the curated ingestion-health XQL and return shaped per-source metrics.
 * @param {string} name
 * @returns {Promise<{ sources: Array, remaining_quota: number }>}
 */
export async function getXsiamTenantMetrics(name) {
  return request(`/api/xsiam/tenants/${encodeURIComponent(name)}/metrics`)
}

/**
 * POST /api/xsiam/tenants/:name/xql
 * Start an ad-hoc XQL query. Returns a query_id for polling.
 * @param {string} name
 * @param {{ query: string, timeframe?: object }} body
 * @returns {Promise<{ query_id: string }>}
 */
export async function startXsiamXql(name, body) {
  return request(`/api/xsiam/tenants/${encodeURIComponent(name)}/xql`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

/**
 * GET /api/xsiam/tenants/:name/xql/:queryId
 * Poll an XQL query result.
 * @param {string} name
 * @param {string} queryId
 * @returns {Promise<{ status: string, results: object, remaining_quota: number }>}
 */
export async function getXsiamXqlResults(name, queryId) {
  return request(`/api/xsiam/tenants/${encodeURIComponent(name)}/xql/${encodeURIComponent(queryId)}`)
}

// ─── Readiness ───────────────────────────────────────────────────────────────
//
// The surface that answers "am I ready to run this in front of a customer?"
// BEFORE the customer is watching. Every helper below is OPTIONAL on the
// server: a SimCore that predates the endpoint answers 404, and the caller
// renders that as a NAMED CAPABILITY GAP — never as a zero, and never as ok.

/**
 * GET /api/credentials/integrations[?kind=…]
 *
 * ALL integration credentials, not just `xsiam_tenant`. The console has to show
 * both Cortex credential kinds side by side: `xsiam` buys alert read-back and
 * `xsiam_tenant` buys XQL, and configuring the first does not authorise the
 * second. A surface that lists only one lets a DC believe they have a capability
 * they never registered.
 *
 * @param {string|null} kind
 * @returns {Promise<Array>}
 */
export async function listIntegrations(kind = null) {
  const suffix = kind ? `?kind=${encodeURIComponent(kind)}` : ''
  const data = await request(`/api/credentials/integrations${suffix}`)
  return Array.isArray(data) ? data : data?.integrations ?? []
}

/**
 * GET /api/connectors — the read-back connector kinds and whether each has a
 * usable credential. Returns the RAW body so `connectors[]` keeps its envelope.
 */
export async function getConnectors() {
  return request('/api/connectors')
}

/**
 * POST /api/xsiam/tenants/:name/preflight — the staged, read-only connection
 * check (config → dns → tls → auth → scope → datasets → quota → clock).
 *
 * Resolves `{ _unavailable: true }` on 404 rather than throwing: a SimCore
 * without the endpoint has not FAILED preflight, it cannot RUN one, and the two
 * must not render the same.
 */
/**
 * Preflight ONE registered xsiam_tenant integration by name.
 *
 * There is no `/api/xsiam/tenants/{name}/preflight` — that path was never
 * implemented, and because `/api/xsiam/...` has other routes it answers 405
 * rather than 404, so the `_unavailable` fallback below never fired and the
 * button just threw. The real surface is the same connector preflight every
 * other kind uses; it takes the integration name as a query param.
 */
export async function preflightXsiamTenant(name) {
  const qs = name ? `?integration=${encodeURIComponent(name)}` : ''
  try {
    return await request(`/api/connectors/xsiam_tenant/preflight${qs}`, { method: 'POST' })
  } catch (err) {
    // A deploy that predates the connector preflight router answers 404. That
    // is "this tenant CANNOT be checked", not "this tenant FAILED a check", and
    // collapsing the two would let an unverifiable credential read as a broken
    // one. Note the old code caught 404 on a path that never existed at all —
    // which answers 405, so this branch could never fire.
    if (err && (err.status === 404 || err.status === 405)) {
      return { _unavailable: true, tenant: name }
    }
    throw err
  }
}

/**
 * POST /api/connectors/:kind/preflight — same staged check for the reconcile
 * credential, whose first exercise is otherwise a live run mid-demo.
 */
export async function preflightConnector(kind) {
  try {
    return await request(`/api/connectors/${encodeURIComponent(kind)}/preflight`, { method: 'POST' })
  } catch (err) {
    if (err && err.status === 404) return { _unavailable: true, kind }
    throw err
  }
}

/**
 * GET /api/agents/install/attempts — enrolment telemetry, so "ran the one-liner,
 * nothing appeared" has an answer on the readiness surface.
 */
export async function getInstallAttempts() {
  try {
    const data = await request('/api/agents/install/attempts')
    return Array.isArray(data) ? data : data?.attempts ?? []
  } catch (err) {
    if (err && err.status === 404) return null
    throw err
  }
}
