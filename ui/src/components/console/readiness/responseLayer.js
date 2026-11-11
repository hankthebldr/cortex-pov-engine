/**
 * View model for the "SOC Framework" row under an xsiam_tenant on Readiness.
 *
 * Input is `integrations[i].response_layer` from GET /api/readiness:
 *   undefined — /api/readiness could not be read (unreadable, not "not probed")
 *   null      — nothing stored yet (not probed; NEVER ABSENT)
 *   object    — { layer, state, stage, read_at, stale, proves, does_not_prove }
 *
 * Nothing here turns an unknown into a pass. A stale read (or a missing stale
 * flag) is "unknown — run preflight". Only a PRESENT layer whose stored stage
 * verdict is `ok` renders healthy: an XQL failure leaves the layer PRESENT while
 * the stage is degraded, and a layer with no stored verdict is unknown.
 */

export const RUNBOOK_PATH = 'docs/lab/2026-11-11-run-alongside-soc-framework.md'

export const SOCFW_PROVES = 'the SOC Framework is configured on this tenant'
export const SOCFW_DOES_NOT_PROVE = 'that the framework handled any CortexSim case'

const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null)

// What a reader is told next to a value, by its source (mirrors
// core/connectors/socfw.py::provenance_note). A value with no recorded source
// is never shown as read.
const PROVENANCE_NOTE = {
  tenant_config: '',
  xql_evidence: ' (seen in the execution log, last 24 h)',
  mode_inferred: ' (inferred from mode, not read)',
  shipped_default: ' (shipped default, not read)',
}
export function provenanceNote(source) {
  return Object.prototype.hasOwnProperty.call(PROVENANCE_NOTE, source)
    ? PROVENANCE_NOTE[source] : ' (source not recorded)'
}

/**
 * "live (inferred from mode, not read) · 6 h (shipped default, not read) ·
 * score ≤ 40 (shipped default, not read)". Not live is "off"; a missing window
 * or threshold is "… not read", never omitted.
 */
export function formatAutoTriage(at) {
  if (!at || typeof at !== 'object') return 'unknown (not read)'
  const head = at.live === true ? 'live' : at.live === false ? 'off' : 'unknown'
  const parts = [typeof at.live === 'boolean' ? `${head}${provenanceNote(at.live_source)}` : head]
  const w = num(at.window_hours)
  parts.push(w !== null ? `${w} h${provenanceNote(at.window_source)}` : 'window not read')
  if (at.score_off === true) {
    parts.push(`every aged case${provenanceNote(at.threshold_source)}`)
  } else {
    const t = num(at.score_threshold)
    parts.push(t !== null ? `score ≤ ${t}${provenanceNote(at.threshold_source)}` : 'threshold not read')
  }
  return parts.join(' · ')
}

function stageOf(rl) {
  const s = rl.stage
  return s && typeof s === 'object' && !Array.isArray(s) && typeof s.status === 'string' ? s : null
}

export function describeResponseLayer(rl) {
  if (rl === undefined) {
    return {
      kind: 'unreadable', headline: 'unknown — /api/readiness could not be read', degraded: false,
      chip: 'chip', detail: null,
    }
  }
  if (rl === null) {
    return { kind: 'not_probed', headline: 'not probed', degraded: false, chip: 'chip', detail: null }
  }
  if (!rl.layer || typeof rl.layer !== 'object' || Array.isArray(rl.layer)) {
    // A state with no layer behind it is a claim without evidence: never PRESENT.
    return {
      kind: 'malformed', state: rl.state || null, degraded: true, chip: 'chip chip--pending',
      headline: 'unknown — stored response_layer has no readable layer', detail: null,
      code: null, readAt: rl.read_at || null,
    }
  }
  const layer = rl.layer
  const state = rl.state || layer.state || 'UNKNOWN'
  const stage = stageOf(rl)
  const base = {
    proves: rl.proves || SOCFW_PROVES,
    doesNotProve: rl.does_not_prove || SOCFW_DOES_NOT_PROVE,
    readAt: rl.read_at || null,
    // A failed stage's code names what went wrong; the layer's code only its assembly.
    code: (stage && stage.status !== 'ok' && stage.code) || layer.code || (stage && stage.code) || null,
    stageDetail: stage && stage.status !== 'ok' && stage.detail ? stage.detail : null,
  }
  // Only an explicit `stale: false` vouches for freshness.
  if (rl.stale !== false) {
    return {
      ...base, kind: 'stale', state, headline: 'unknown — run preflight', degraded: false,
      chip: 'chip', detail: null,
    }
  }
  const detail = {
    version: layer.framework_version || null,
    mode: layer.mode || null,
    autoTriage: formatAutoTriage(layer.auto_triage),
    aiClose: layer.ai_close || 'unknown',
    leftoverLists: layer.leftover_lists === true,
  }
  const healthy = state === 'PRESENT' && stage !== null && stage.status === 'ok'
  let headline = state
  if (state === 'PRESENT' && !healthy) {
    headline = stage === null ? 'PRESENT — stage verdict not stored' : `PRESENT — stage ${stage.status}`
  }
  // chip: healthy PRESENT detected, ABSENT missed, everything else pending
  const chip = healthy ? 'chip chip--detected'
    : state === 'ABSENT' ? 'chip chip--missed' : 'chip chip--pending'
  return { ...base, kind: 'read', state, headline, degraded: !healthy, chip, detail }
}
