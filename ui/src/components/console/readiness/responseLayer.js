/**
 * View model for the "SOC Framework" row under an xsiam_tenant on Readiness.
 *
 * Input is `integrations[i].response_layer` from GET /api/readiness:
 *   undefined — /api/readiness could not be read (unreadable, not "not probed")
 *   null      — nothing stored yet (not probed; NEVER ABSENT)
 *   object    — { layer, state, read_at, stale, proves, does_not_prove }
 *
 * Nothing here turns an unknown into a pass: a stale read is "unknown — run
 * preflight", and only a fresh ABSENT/UNKNOWN is rendered degraded.
 */

export const RUNBOOK_PATH = 'docs/lab/2026-11-11-run-alongside-soc-framework.md'

export const SOCFW_PROVES = 'the SOC Framework is configured on this tenant'
export const SOCFW_DOES_NOT_PROVE = 'that the framework handled any CortexSim case'

const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null)

/** "live · 6 h · score ≤ 40" / "live · 6 h · every aged case". */
export function formatAutoTriage(at) {
  if (!at || typeof at !== 'object') return 'unknown'
  const head = at.live === true ? 'live' : at.live === false ? 'shadow' : 'unknown'
  const parts = [head]
  const w = num(at.window_hours)
  if (w !== null) parts.push(`${w} h`)
  if (at.score_off === true) parts.push('every aged case')
  else if (num(at.score_threshold) !== null) parts.push(`score ≤ ${at.score_threshold}`)
  return parts.join(' · ')
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
  const base = {
    proves: rl.proves || SOCFW_PROVES,
    doesNotProve: rl.does_not_prove || SOCFW_DOES_NOT_PROVE,
    readAt: rl.read_at || null,
    code: layer.code || null,
  }
  if (rl.stale === true) {
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
  const degraded = state !== 'PRESENT'
  const chip = state === 'PRESENT' ? 'chip chip--detected'
    : state === 'PRESENT_UNREAD' ? 'chip chip--pending'
      : state === 'ABSENT' ? 'chip chip--missed' : 'chip chip--pending'
  // chip: PRESENT detected, ABSENT missed, everything else pending
  return { ...base, kind: 'read', state, headline: state, degraded, chip, detail }
}
