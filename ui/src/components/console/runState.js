/**
 * runState.js — how a step, and a whole run, executed. Pure, no React.
 *
 * Runs shows what was attempted, so every word here is about execution:
 * Queued · Running · Ran · Failed · Timed out · Did not run · Aborted · Staged.
 * None of them says anything about a detection; that is Results' vocabulary.
 * A state is a kit StatusIcon tone and one word.
 */
import { PHASE } from './runLifecycle.js'
import { isRunComplete } from './runStatus.js'

export const STEP_STATES = Object.freeze({
  queued:      { status: 'off',      word: 'Queued' },
  running:     { status: 'running',  word: 'Running' },
  ran:         { status: 'up',       word: 'Ran' },
  failed:      { status: 'down',     word: 'Failed' },
  timed_out:   { status: 'down',     word: 'Timed out' },
  did_not_run: { status: 'off',      word: 'Did not run' },
  aborted:     { status: 'degraded', word: 'Aborted' },
  // A push bundle runs offline; nothing on this instance ran or queued it.
  staged:      { status: 'off',      word: 'Staged' },
})

/** The chain legend's order, and the tone of an edge into a step. */
export const EXEC_VOCABULARY = Object.freeze({
  states: STEP_STATES,
  order: ['running', 'ran', 'failed', 'timed_out', 'aborted', 'did_not_run', 'queued', 'staged'],
  fallback: 'queued',
  edge: { failed: 'crit', timed_out: 'crit' },
})

const UNKNOWN = Object.freeze({ key: 'unknown', status: 'unknown', word: 'Unknown' })
const named = (key) => ({ key, ...STEP_STATES[key] })

/**
 * The run's own state, for a Runs row and the run's page.
 *
 * @param {Object} run        a run row (the summary shape is enough)
 * @param {Object} lifecycle  `runLifecycle(run)`
 * @returns {{key: string, status: string, word: string}}
 */
export function runState(run, lifecycle) {
  const s = String(run?.status || '').toLowerCase()
  if (s === 'pending') return named('queued')
  if (s === 'running') return named('running')
  if (s === 'staged') return named('staged')
  if (s === 'aborted') return named('aborted')
  // It ended before any step ran (or none that carries a detection did).
  if (lifecycle?.phase === PHASE.NOTHING) return named('did_not_run')
  if (isRunComplete(s)) return named('ran')
  if (s === 'failed') return named('failed')
  return UNKNOWN
}

/**
 * Whether the run has been reviewed, or null where the question does not
 * apply yet (still running, or nothing ran).
 *
 * @returns {{status: string, word: string}|null}
 */
export function reviewState(lifecycle) {
  switch (lifecycle?.phase) {
    case PHASE.READY: return { status: 'up', word: 'Reviewed' }
    case PHASE.READING: return { status: 'running', word: 'Reviewing…' }
    case PHASE.UNREAD: return { status: 'off', word: 'Not reviewed' }
    default: return null
  }
}
