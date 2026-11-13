/**
 * runLifecycle.js — where a run is (design v26 §4.6). Pure, no React.
 *
 *   Launched → Running → Finished / Failed / Aborted → Read back → Results
 *
 * and the phase that decides what a DC can do with it:
 *
 *   running   it is executing (or queued for its agent)   → Open
 *   nothing   it ended before any step ran, or no step    → — (Run, on its page)
 *             that carries a detection ran
 *   unread    it ended; the tenant has not been read      → Read back
 *   reading   a read back is in flight                    → "Reading…"
 *   ready     SimCore scored it from a tenant read        → Open results
 *
 * AUTHORED IS NOT PROVEN. A run that ended has executed steps, not detections.
 * Until SimCore has read the tenant for it, there is nothing to claim, so the
 * console says "not read back" rather than "0 of 10". "Read back" is a fact
 * SimCore recorded on the run, never an inference from the run having ended:
 *
 *   tc_verdict_detail.source = "reconcile:<connector>:<integration>"
 *       the alert read-back (POST /reconcile, or the auto sweep) scored it;
 *   tc_verdict_detail.source = "reconcile:manual-import"
 *       the DC imported the tenant's alerts by hand (POST /observations);
 *   tc_verdict_detail.verification.queries_issued > 0
 *       Tier-2 verification ran XQL against a tenant.
 *
 * A verify with no tenant credential re-scores the run too, but issues no
 * query, so it is not a read back. The marker is the latest score's: a later
 * re-score from another source (say, a verify without a credential after a
 * reconcile) replaces it, and the run reads unread again until it is read
 * back — the safe direction to be wrong in.
 */
import { isRunComplete, isRunTerminal } from './runStatus.js'
import { VERDICT_REASONS } from './verdictReason.js'

export const STAGE_LABELS = Object.freeze(['Launched', 'Running', 'Finished', 'Read back', 'Results'])

export const PHASE = Object.freeze({
  RUNNING: 'running',
  NOTHING: 'nothing',
  UNREAD: 'unread',
  READING: 'reading',
  READY: 'ready',
})

/** Why a run in phase `nothing` has nothing to read back. */
export const NOTHING = Object.freeze({
  UNDELIVERED: 'undelivered',     // no step reached its agent
  NOT_EXECUTED: 'not_executed',   // no step that carries a detection ran
})

/** What a DC is told about a run where no step that carries a detection ran. */
export const NOT_EXECUTED_COPY = Object.freeze({
  result: VERDICT_REASONS.not_executed.label,
  title: 'no step that carries a detection ran — nothing to read back',
  step: 'no detection step ran',
  message: 'No step that carries a detection ran, so nothing was tested and there is nothing '
    + 'to read back. Run the scenario again.',
})

/** What the run was read back from, or null when the tenant was never read for it. */
export function readBackOf(run) {
  const d = run && run.tc_verdict_detail
  if (!d || typeof d !== 'object') return null
  const src = typeof d.source === 'string' ? d.source : ''
  if (src.startsWith('reconcile:')) {
    const from = src.slice('reconcile:'.length)
    return {
      from: from === 'manual-import' ? 'a manual import' : (from || 'the tenant'),
      at: d.scored_at || null,
      how: 'reconcile',
    }
  }
  const v = d.verification
  if (v && typeof v === 'object' && Number(v.queries_issued) > 0) {
    return { from: v.tenant || 'the tenant', at: v.last_attempt_at || d.scored_at || null, how: 'verify' }
  }
  return null
}

function endedLabel(status) {
  const s = String(status || '').toLowerCase()
  if (isRunComplete(s)) return 'Finished'
  if (s === 'failed') return 'Failed'
  if (s === 'aborted') return 'Aborted'
  if (s === 'staged') return 'Staged'
  return null
}

// The beacon's per-step frame (agent/beacon/client.go::formatStepOutput).
// Launch writes `!! TARGET_…` markers into the output before any step runs, so
// "the output is not empty" does not mean a step ran; a step frame does.
const STEP_FRAME = /^(=== STEP \d+\/\d+ ·|--- exit_code=-?\d+)/m

/**
 * A pull run that ended before its agent took the task ran no step: no
 * delivery stamp, and no step frame in its transcript (the frame check keeps
 * an older SimCore, which has no delivery stamp, from reading as "nothing").
 */
function ranNothing(run) {
  if (String(run.mode || '').toLowerCase() === 'push') return false
  if (run.delivered_at) return false
  const text = typeof run.output === 'string' ? run.output
    : typeof run.output_tail === 'string' ? run.output_tail : ''
  return !STEP_FRAME.test(text)
}

/**
 * Why a settled run that was never read back has nothing to read back, or null.
 *
 * `not_executed` is SimCore's verdict reason for a run where no step that
 * carries a detection ran (`verifier.score_run`, set at completion). Reconcile
 * and verify skip rows the host says never ran, so reading the tenant cannot
 * change that verdict; only a re-run can. `soak_pending` is different: a row
 * that ran is soaking, and a read back after its window closes can score it.
 */
function nothingToReadBack(run) {
  if (ranNothing(run)) return NOTHING.UNDELIVERED
  const d = run.tc_verdict_detail
  if (d && typeof d === 'object' && d.reason === NOTHING.NOT_EXECUTED) return NOTHING.NOT_EXECUTED
  return null
}

const STATE_WORD = { done: 'done', current: 'now', failed: 'failed', todo: 'not yet' }

/**
 * @param {Object} run          a run row (the summary shape is enough)
 * @param {{reading?: boolean}} [opts]  a read back is in flight for this run
 * @returns {{ phase: string, ended: string|null, readBack: Object|null,
 *             nothing: string|null,
 *             stages: Array<{label: string, state: string, word: string}> }}
 *   `nothing` is a NOTHING value in phase `nothing`, else null.
 */
export function runLifecycle(run, { reading = false } = {}) {
  const status = String(run?.status || '').toLowerCase()
  const ended = endedLabel(status)
  const settled = isRunTerminal(status) || status === 'staged'
  const readBack = readBackOf(run)
  const nothing = settled && !readBack ? nothingToReadBack(run || {}) : null

  let phase
  if (!settled) phase = PHASE.RUNNING
  else if (readBack) phase = PHASE.READY
  else if (nothing) phase = PHASE.NOTHING
  else if (reading) phase = PHASE.READING
  else phase = PHASE.UNREAD
  // A read back of a run that was already read back shows as reading too.
  if (settled && reading && phase === PHASE.READY) phase = PHASE.READING

  // Index of the stage the run is on: 0 launched · 1 running · 2 ended ·
  // 3 read back · 4 results. Ready has every stage behind it.
  const at = {
    [PHASE.RUNNING]: status === 'running' ? 1 : 0,
    [PHASE.NOTHING]: 2,
    [PHASE.UNREAD]: 2,
    [PHASE.READING]: 3,
    [PHASE.READY]: 5,
  }[phase]
  const endedBad = ended === 'Failed' || ended === 'Aborted'

  const stages = STAGE_LABELS.map((label, i) => {
    const name = i === 2 ? (ended || label) : label
    let state = i < at ? 'done' : i === at ? 'current' : 'todo'
    if (i === 2 && endedBad && at >= 2) state = 'failed'
    return { label: name, state, word: STATE_WORD[state] }
  })

  return { phase, ended, readBack, stages, nothing: phase === PHASE.NOTHING ? nothing : null }
}
