/**
 * runLifecycle.js — where a run is (design v26 §4.6). Pure, no React.
 *
 *   Launched → Running → Finished / Failed / Aborted → Read back → Results
 *
 * and the phase that decides what a DC can do with it:
 *
 *   running   it is executing (or queued for its agent)   → Open
 *   nothing   it ended before any step ran                → —
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

export const STAGE_LABELS = Object.freeze(['Launched', 'Running', 'Finished', 'Read back', 'Results'])

export const PHASE = Object.freeze({
  RUNNING: 'running',
  NOTHING: 'nothing',
  UNREAD: 'unread',
  READING: 'reading',
  READY: 'ready',
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

/** A pull run that ended before its agent took the task ran no step. */
function ranNothing(run) {
  if (String(run.mode || '').toLowerCase() === 'push') return false
  if (run.delivered_at) return false
  const bytes = Number(run.output_bytes)
  if (Number.isFinite(bytes) && bytes > 0) return false
  if (typeof run.output === 'string' && run.output.length > 0) return false
  return true
}

const STATE_WORD = { done: 'done', current: 'now', failed: 'failed', todo: 'not yet' }

/**
 * @param {Object} run          a run row (the summary shape is enough)
 * @param {{reading?: boolean}} [opts]  a read back is in flight for this run
 * @returns {{ phase: string, ended: string|null, readBack: Object|null,
 *             stages: Array<{label: string, state: string, word: string}> }}
 */
export function runLifecycle(run, { reading = false } = {}) {
  const status = String(run?.status || '').toLowerCase()
  const ended = endedLabel(status)
  const settled = isRunTerminal(status) || status === 'staged'
  const readBack = readBackOf(run)

  let phase
  if (!settled) phase = PHASE.RUNNING
  else if (readBack) phase = PHASE.READY
  else if (ranNothing(run || {})) phase = PHASE.NOTHING
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

  return { phase, ended, readBack, stages }
}
