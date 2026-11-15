/**
 * detectionState.js — what became of one expected detection, as a kit
 * StatusIcon tone and one word. Pure.
 *
 * Results shows what Cortex saw, so every word here is about a detection:
 * Detected · Prevented · Missed · Pending · Not reached · Not executed ·
 * Not reviewed. (How a step executed is Runs' vocabulary, runState.js.)
 *
 * The key is the one `outcomes.js::resultStatus` decides from the server's
 * outcome axis; this only names it. A row nothing has judged is Pending on a
 * reviewed run (the review did not settle it) and Not reviewed on a run that
 * has not been reviewed.
 *
 * A block-mode `prevent` expectation the agent only detected is its own
 * state, Not prevented: Cortex saw it and the block did not happen, which is
 * a failure of the control and must never read as a pass.
 */
import { resultStatus, outcomeKey, isAwaitingReconcile, isAttestationRefused } from '../../outcomes.js'

export const DETECTION_STATES = Object.freeze({
  detected:     { status: 'up',      word: 'Detected' },
  prevented:    { status: 'up',      word: 'Prevented' },
  missed:       { status: 'down',    word: 'Missed' },
  pending:      { status: 'unknown', word: 'Pending' },
  not_reached:  { status: 'off',     word: 'Not reached' },
  not_executed: { status: 'off',     word: 'Not executed' },
  not_reviewed: { status: 'off',     word: 'Not reviewed' },
  detected_not_prevented: { status: 'down', word: 'Not prevented', title: 'Detected, but not prevented' },
})

/**
 * @param {Object} row  a result row (`outcome`, `observed`, …); `row.status`
 *        is used when the caller already resolved it
 * @param {Object}  [opts]
 * @param {boolean} [opts.reviewed=true]  the run has been reviewed
 * @returns {{key: string, status: string, word: string, title?: string}}
 */
export function detectionState(row, { reviewed = true } = {}) {
  const status = outcomeKey(row?.status ?? resultStatus(row))
  const key = !reviewed && isAwaitingReconcile(status, row) ? 'not_reviewed' : status
  const state = DETECTION_STATES[key]
  if (state) return { key, ...state }
  // A status this console does not name is shown as it was sent, never as a pass.
  return { key, status: 'unknown', word: String(row?.status || 'Unknown') }
}

/**
 * The hand marks a row offers, as kit verbs, and the `observed` value each
 * sends to `PUT /api/results/{id}/validate`. The API takes two: attest a
 * detection (`true`), and withdraw that attestation (`false`), which puts the
 * row back to unjudged and never writes a miss (a miss is the engine's word).
 * It refuses `null`.
 */
export const MARK = Object.freeze({ 'Mark detected': true, Reset: false })

/**
 * Which of those a row offers: Mark detected until it reads detected; Reset
 * on a row a hand mark made detected. A row matched to an alert is the
 * engine's, and a row the server will not attest (a step that never ran, a
 * prevention) offers neither.
 *
 * @param {Object} row  a scorecard row (`id`, `observed`, `alertId`, `outcome`, …)
 * @returns {string[]}
 */
export function rowActions(row) {
  if (!row || row.id == null || isAttestationRefused(row)) return []
  if (row.observed !== true) return ['Mark detected']
  return row.alertId ? [] : ['Reset']
}
