/**
 * outcomes.js — the ONE place the console names a detection outcome and words
 * an operator attestation.
 *
 * Rendered, never inferred: every key here is a value the server already sent
 * (storyline `status`, graph node `status`, `Run.agent_profile_mode`, …). The
 * console picks a glyph and a label for it; it does not decide an outcome.
 * The Storyline, the Causality graph and the Run detail all import from here
 * so the three surfaces cannot drift apart.
 */

export const OUTCOME_META = {
  prevented:    { glyph: '🛡', label: 'Prevented' },
  detected:     { glyph: '✅', label: 'Detected' },
  missed:       { glyph: '❌', label: 'Missed' },
  pending:      { glyph: '⏳', label: 'Pending' },
  not_reached:  { glyph: '⏭', label: 'Not reached' },
  not_executed: { glyph: '⚠', label: 'Not executed' },
  // A block-mode `prevent` expectation the agent only detected: Cortex saw it,
  // but the prevention FAILED — a fail glyph, never ✅.
  detected_not_prevented: { glyph: '❌', label: 'Detected, not prevented' },
}

/** `Result.outcome` vocabulary (core/engine/outcome.py::OUTCOMES). */
const RESULT_OUTCOMES = new Set([
  'prevented', 'detected', 'missed', 'pending', 'not_reached', 'not_executed',
])
export const DETECTED_NOT_PREVENTED = 'DETECTED_NOT_PREVENTED'

/**
 * The ONE mapping from a Result row (or a storyline entry) to the status key
 * `OUTCOME_META` renders.
 *
 * The server's `outcome` wins whenever it is present: `_seed_results` writes
 * `observed=false` on every row, so reading `observed` alone renders a step
 * that never ran (`not_reached`) or a detector still soaking as ❌ Missed.
 * Only when `outcome` is null/undefined does the legacy mapping apply —
 * `legacyStatus` when the caller already holds a server-decided one (the
 * storyline's `status`), else `observed` true/false/null → detected/missed/
 * pending. An unrecognised outcome raises: it must never read as benign.
 */
export function resultStatus(result, legacyStatus) {
  const r = result || {}
  const outcome = r.outcome
  if (outcome != null) {
    if (!RESULT_OUTCOMES.has(outcome)) {
      throw new Error(`unrecognised result outcome: ${JSON.stringify(outcome)}`)
    }
    if (outcome === 'detected' && r.outcome_reason === DETECTED_NOT_PREVENTED) {
      return 'detected_not_prevented'
    }
    return outcome
  }
  if (legacyStatus !== undefined) return legacyStatus
  if (r.observed === true) return 'detected'
  if (r.observed === false) return 'missed'
  return 'pending'
}

/** Every status Cortex saw (a coverage hit) — the prevention verdict aside. */
export function isHitStatus(status) {
  const key = outcomeKey(status)
  return key === 'detected' || key === 'prevented' || key === 'detected_not_prevented'
}

/** `not-executed` (legacy CSS spelling) and `not_executed` (server) are one key. */
export function outcomeKey(status) {
  return String(status || 'pending').replace(/-/g, '_')
}

/** `{glyph, label}` for a status; an unknown status keeps its own text. */
export function outcomeMeta(status) {
  const key = outcomeKey(status)
  return OUTCOME_META[key] || { glyph: '', label: String(status || 'Idle') }
}

/** CSS-safe modifier: `not_reached` -> `not-reached`. */
export function outcomeClass(status) {
  return outcomeKey(status).replace(/_/g, '-')
}

// ─── Attestations ────────────────────────────────────────────────────────────

/** true|false|null -> the wording the server's own report uses. */
export function triStateLabel(v) {
  if (v === true) return 'enabled'
  if (v === false) return 'disabled'
  return 'not attested'
}

/** Select value <-> tri-state. '' = not attested. */
export function triStateToValue(v) {
  return v === true ? 'enabled' : v === false ? 'disabled' : ''
}
export function triStateFromValue(s) {
  return s === 'enabled' ? true : s === 'disabled' ? false : null
}

/** Does this run carry anything an operator declared? Same gate as the report. */
export function hasAttestations(run) {
  if (!run) return false
  return (
    (run.agent_profile_mode != null && run.agent_profile_mode !== 'unknown')
    || run.bas_mode_attested != null
    || run.soakless_attested != null
    || !!run.attested_by
  )
}

/** naive-UTC ISO from the server -> "...Z"; absent -> "unknown". */
function attestedAt(run) {
  if (!run.attested_at) return 'unknown'
  const s = String(run.attested_at)
  return /[zZ]$|[+-]\d\d:?\d\d$/.test(s) ? s : `${s}Z`
}

/**
 * The four canary shapes the server report uses; never "null" / "None".
 */
export function canaryLine(run) {
  const status = run.block_mode_status || null
  const cid = run.canary_run_id || null
  if (status && cid) return `Canary: ${status} (run ${cid})`
  if (status) return `Canary: ${status}`
  if (cid) return `Canary: run ${cid} (status unknown)`
  return 'Canary: not run'
}

/**
 * The Attestations panel lines. Every line that restates an operator
 * declaration says so in words ("not verified" / "attested") — an attestation
 * must never read as a verification.
 */
export function attestationLines(run) {
  const mode = run.agent_profile_mode || 'unknown'
  return {
    profile: `Agent profile mode: ${mode} — attested by ${run.attested_by || 'unknown'} at ${attestedAt(run)}, not verified`,
    bas: `BAS mode: ${triStateLabel(run.bas_mode_attested)}${run.bas_mode_attested != null ? ' — attested' : ''}`,
    soakless: `Soakless: ${triStateLabel(run.soakless_attested)}${run.soakless_attested != null ? ' — attested' : ''}`,
    canary: canaryLine(run),
  }
}

/** Newest run by started_at (ISO strings sort lexically); null when none. */
export function newestRun(runs) {
  const list = Array.isArray(runs) ? runs : []
  let best = null
  for (const r of list) {
    if (!r) continue
    if (!best || String(r.started_at || '') > String(best.started_at || '')) best = r
  }
  return best
}

/** Two runs are a pair when either points at the other. */
export function arePaired(a, b) {
  if (!a || !b) return false
  const ia = a.run_id ?? a.id
  const ib = b.run_id ?? b.id
  return (!!a.paired_with_run_id && String(a.paired_with_run_id) === String(ib))
    || (!!b.paired_with_run_id && String(b.paired_with_run_id) === String(ia))
}
