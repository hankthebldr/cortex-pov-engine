/**
 * CortexSim — Detection Storyline API client.
 *
 * Standalone module (does NOT edit ../api/client.js) that fetches the
 * per-run Detection Storyline — the evidence-backed kill-chain the
 * presenter timeline renders. Backed by GET /api/runs/{id}/storyline
 * (see core/engine/storyline.py → build_storyline).
 *
 * Error shape mirrors client.js: FastAPI returns {detail} where detail
 * may be a plain string OR a structured {error, code, detail} object.
 */

const BASE_URL = typeof window !== 'undefined' ? window.location.origin : ''

/**
 * Normalize the backend builder shape (core/engine/detection_storyline.py →
 * build_storyline) into the flat view-model DetectionStoryline.jsx consumes.
 *
 * The builder is the single source of truth; it nests per-detection fields
 * under `expected_detection`/`observed` and rolls counts up under `summary`.
 * The presenter view reads a flatter shape, so we map at this client edge
 * rather than forking the builder (keeps the SSE-fold path and the snapshot
 * path reading the same server contract). `provenance` is derived here:
 * machine-verified = a detected row carrying a real alert id (connector
 * reconcile), attested = detected via the human checkbox (no alert evidence).
 *
 * @param {Object} s builder output (already envelope-unwrapped)
 * @returns {Object} flat storyline view-model
 */
function normalizeStoryline(s) {
  const summary = s.summary || {}
  const smttd = summary.mttd || {}

  let machineVerified = 0
  let attested = 0

  const steps = (s.steps || []).map((step) => ({
    step_id: step.step_id,
    index: step.step_index,
    name: step.step_name,
    mitre_technique: step.mitre_technique,
    stitch_group: step.stitch_group, // absent until correlation stitch lands
    detections: (step.detections || []).map((e) => {
      const expected = e.expected_detection || {}
      const observed = e.observed || null
      const alertId = observed ? observed.alert_id || null : null
      if (e.status === 'detected') {
        if (alertId) machineVerified += 1
        else attested += 1
      }
      return {
        step_id: e.step_id,
        step_index: e.step_index,
        status: e.status,
        mttd_seconds: e.mttd_seconds,
        detection_type: expected.type,
        plane: expected.plane,
        expected_description: expected.description,
        detection_id: expected.detection_id,
        ttp_ref: e.ttp_ref,
        observed_at: observed ? observed.observed_at : null,
        alert_external_id: alertId,
        deep_link: observed ? observed.deep_link || null : null,
        evidence_count: (e.evidence_refs || []).length,
        observed_method: observed ? (alertId ? 'connector' : 'human') : null,
      }
    }),
  }))

  return {
    run_id: s.run_id,
    scenario_id: s.scenario_id,
    run_status: s.run_status,
    steps,
    coverage: {
      detected: summary.detected || 0,
      expected: summary.total || 0,
      pct: summary.coverage_pct != null ? summary.coverage_pct : 0,
    },
    mttd: {
      p50: smttd.median != null ? smttd.median : null,
      p90: smttd.p90 != null ? smttd.p90 : null,
      count: smttd.count || 0,
    },
    provenance: { machine_verified: machineVerified, attested },
  }
}

/**
 * GET /api/runs/:runId/storyline
 *
 * Fetches the per-run Detection Storyline and returns the flat view-model the
 * presenter renders (see normalizeStoryline). The endpoint wraps its payload as
 * `{ detection_storyline: ... }`; we unwrap it here so callers read top-level
 * keys (storyline.steps, storyline.coverage, storyline.run_id).
 *
 * @param {string} runId
 * @returns {Promise<Object>}
 */
export async function getStoryline(runId) {
  const res = await fetch(
    `${BASE_URL}/api/runs/${encodeURIComponent(runId)}/storyline`,
    { headers: { Accept: 'application/json' } },
  )

  if (!res.ok) {
    let message = `HTTP ${res.status} — ${res.statusText}`
    try {
      const body = await res.json()
      if (body && body.detail && typeof body.detail === 'object') {
        const d = body.detail
        const code = d.code ? ` [${d.code}]` : ''
        message = `${d.error || d.detail || message}${code}`
      } else if (body) {
        message = body.detail || body.error || body.message || message
      }
    } catch {
      /* body was not JSON — keep the HTTP status message */
    }
    throw new Error(message)
  }

  const body = await res.json()
  return normalizeStoryline(body.detection_storyline || body)
}

/**
 * URL of the scoped per-run SSE stream. The presenter view attaches an
 * EventSource here to refetch the storyline the instant a real alert
 * reconciles (falls back to interval polling when SSE is unavailable).
 *
 * @param {string} runId
 * @returns {string}
 */
export function storylineEventsUrl(runId) {
  return `${BASE_URL}/api/runs/${encodeURIComponent(runId)}/events`
}
