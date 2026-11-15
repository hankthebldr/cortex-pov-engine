/**
 * runtimeLog.js — a run's runtime log: one block per step, in order. Pure.
 *
 * What was attempted, and how it went on the host. Three sources, all on
 * `GET /api/runs/{id}` (plus the scenario's own steps):
 *
 *   scenario steps   name, technique, the authored account and command;
 *   step_outcomes    the host's record of how each step ended (exit code,
 *                    termination, signal, reason) — authoritative for state;
 *   output           the beacon's transcript: each step's stdout/stderr, the
 *                    account it ran as, its duration, and the launch markers
 *                    written outside any step.
 *
 * A finished run and a live one are built the same way: a step whose block
 * has a header and no footer yet is Running, with the lines that have arrived.
 * So two things a later beacon can report need no new shape here:
 *
 *   - a step START (the command it is about to run) arrives as `starts`
 *     (`run.step_starts` when the record carries it). Its command is preferred
 *     over the scenario's, and the step reads Running from that moment;
 *   - output CHUNKS streamed while a step runs append to the same transcript,
 *     so they show up in the Running block's lines.
 *
 * Nothing here says anything about a detection.
 */
import { parseStepTranscript, splitStepTranscript, buildStepRows } from './useRunStepTail.js'
import { STEP_STATES } from './runState.js'

const VARIANT = /^!! PLATFORM VARIANT:\s*step=(\S+)\s+ran its (\S+) variant/
// `!! STEP NOT RUN: PLATFORM_UNSATISFIABLE step=<id> …`: inside the refused
// step's own block on a current beacon, a bare line on an older one.
const NOT_RUN = /^!! STEP NOT RUN:\s*([A-Z][A-Z0-9_]*)(?:\s+step=(\S+))?/

/** Host terminations where something other than the step itself ended it. */
const ENDED_FOR_IT = new Set(['signaled', 'anchor_terminated', 'beacon_lost'])

/** A step sent in-process (EAL) has no beacon record; its ledger entry says how it went. */
const EAL_STATE = Object.freeze({
  dispatched: 'ran', dry_run: 'ran', skipped: 'did_not_run',
  not_delivered: 'failed', error: 'failed', pending: 'queued',
})

function startsById(starts) {
  const map = new Map()
  const list = Array.isArray(starts)
    ? starts
    : starts && typeof starts === 'object'
      ? Object.entries(starts).map(([step_id, v]) => ({ step_id, ...(v && typeof v === 'object' ? v : { command: v }) }))
      : []
  for (const s of list) {
    if (s && s.step_id) map.set(s.step_id, s)
  }
  return map
}

/** The command that was attempted, and where it was read from. */
function commandOf(step, start, variantOs) {
  if (start && typeof start.command === 'string' && start.command.trim()) {
    return { command: start.command, commandFrom: 'agent' }
  }
  const variant = variantOs && step?.platform_variants ? step.platform_variants[variantOs] : null
  if (typeof variant === 'string' && variant.trim()) return { command: variant, commandFrom: 'variant' }
  if (typeof step?.command === 'string' && step.command.trim()) {
    return { command: step.command, commandFrom: 'scenario' }
  }
  if (step?.eal?.plugin) return { command: `EAL plugin ${step.eal.plugin}`, commandFrom: 'scenario' }
  return { command: null, commandFrom: null }
}

function stateKey(row, seg, started, runAborted) {
  const term = row.termination
  if (term === 'not_run' || seg?.notRun) return 'did_not_run'
  if (term === 'aborted') return 'aborted'
  if (term === 'timeout' || seg?.timedOut) return 'timed_out'
  if (ENDED_FOR_IT.has(term)) return 'failed'
  switch (row.status) {
    case 'ok': return 'ran'
    case 'error':
    case 'severed': return 'failed'
    case 'staged': return 'staged'
    case 'running': return 'running'
    case 'not_reached':
      // The run ended. A step that had started never reported its end.
      if (started) return runAborted ? 'aborted' : 'failed'
      return 'did_not_run'
    default:
      return started ? 'running' : 'queued'
  }
}

function reasonOf(key, row, outcome, started, runStatus) {
  if (outcome?.reason) return String(outcome.reason)
  if (row.termination === 'beacon_lost') return 'agent stopped reporting'
  if (key === 'failed' && row.status === 'not_reached' && started) return 'did not finish'
  if (key === 'did_not_run') {
    if (runStatus === 'aborted') return 'run aborted'
    if (runStatus === 'failed') return 'run stopped first'
    return null
  }
  return null
}

/**
 * @param {Object}  args
 * @param {Array}   args.steps   the run's scenario steps, in order (may be empty)
 * @param {Object}  args.run     `GET /api/runs/{id}` (output, step_outcomes, …)
 * @param {Array|Object} [args.starts]  the beacon's record of each step's
 *        start: `[{ step_id, command, started_at? }]`. Defaults to
 *        `run.step_starts`; absent on a SimCore that does not record it.
 * @returns {{ blocks: Array, preface: string[][] }}
 *   `preface`: transcript lines written before any step (launch markers).
 */
export function buildRuntimeLog({ steps = [], run = null, starts = null } = {}) {
  const output = typeof run?.output === 'string' ? run.output : ''
  const segs = new Map()        // step id → its transcript block
  const notes = new Map()       // step id ('' = before any step) → lines after it
  const variants = new Map()    // step id → the OS whose variant ran
  const refused = new Map()     // step id → why the host would not run it
  let anchor = ''
  for (const seg of splitStepTranscript(output)) {
    if (seg.kind === 'step') {
      const prev = segs.get(seg.id)
      segs.set(seg.id, prev ? { ...seg, lines: [...prev.lines, ...seg.lines] } : seg)
      anchor = seg.id
      for (const line of seg.lines) {
        const m = NOT_RUN.exec(line.trim())
        if (m) refused.set(seg.id, m[1])
      }
      continue
    }
    for (const line of seg.lines) {
      const text = line.trim()
      const v = VARIANT.exec(text)
      if (v) variants.set(v[1], v[2])
      const n = NOT_RUN.exec(text)
      if (n && n[2]) refused.set(n[2], n[1])
    }
    // A chunk that opens or ends on a blank line is still one note.
    const lines = seg.lines.slice()
    while (lines.length && !lines[0].trim()) lines.shift()
    if (!lines.length) continue
    if (!notes.has(anchor)) notes.set(anchor, [])
    notes.get(anchor).push(lines)
  }

  const authored = (Array.isArray(steps) ? steps : []).filter((s) => s && s.id)
  const byId = new Map(authored.map((s) => [s.id, s]))
  // A step the transcript names and the scenario no longer lists keeps its output.
  const known = [...authored, ...[...segs.keys()].filter((id) => !byId.has(id)).map((id) => ({ id }))]
  const rows = buildStepRows(known, run, parseStepTranscript(output))

  const outcomes = new Map()
  for (const o of run?.step_outcomes || []) if (o?.step_id) outcomes.set(o.step_id, o)
  const startRecords = startsById(starts ?? run?.step_starts)
  const ledger = new Map()
  for (const e of Array.isArray(run?.channel_dispatch) ? run.channel_dispatch : []) {
    if (e && e.channel === 'eal' && e.step_id) ledger.set(e.step_id, e)
  }
  const runStatus = String(run?.status || '').toLowerCase()

  const blocks = rows.map((row) => {
    const step = byId.get(row.id) || {}
    const seg = segs.get(row.id) || null
    const outcome = outcomes.get(row.id) || null
    const start = startRecords.get(row.id) || null
    const eal = ledger.get(row.id) || null
    const started = !!(seg || start || outcome?.started_at)

    let key
    let reason
    let lines = seg ? seg.lines : []
    if (eal && !seg && !outcome) {
      key = EAL_STATE[eal.status] || 'queued'
      reason = eal.status === 'dry_run' ? 'dry run' : (key === 'ran' ? null : eal.code || eal.status || null)
      if (eal.detail) lines = [String(eal.detail)]
    } else {
      // A block that carries its own exit code is a finished step, even where
      // the host record has none (the refused-step frame, an older SimCore).
      key = row.rc == null && seg?.finished && seg.exitCode != null
        ? (seg.exitCode === 0 ? 'ran' : 'failed')
        : refused.has(row.id) && !seg ? 'did_not_run'
          : stateKey(row, seg, started, runStatus === 'aborted')
      reason = (key === 'did_not_run' && !outcome?.reason && refused.get(row.id))
        || reasonOf(key, row, outcome, started, runStatus)
    }

    const exit = row.rc != null ? row.rc : (seg?.exitCode ?? null)
    const duration = row.duration || (seg?.duration && seg.duration !== '0s' ? seg.duration : null)
    return {
      id: row.id,
      index: row.index,
      name: row.name || step.name || null,
      technique: row.technique || seg?.technique || null,
      // The frame's `identity=` is the account the step ASKED for. When the
      // host could not honour it, the step ran as the agent's own account.
      account: row.identityNote ? 'the agent account' : (row.identity || null),
      accountNote: row.identityNote ? `not ${row.identity || 'the authored account'}` : null,
      accountDetail: row.identityNote || null,
      ...commandOf(step, start, variants.get(row.id)),
      state: { key, ...STEP_STATES[key] },
      exit: key === 'did_not_run' ? null : exit,
      signal: row.signal || null,
      duration,
      reason,
      lines,
      notes: notes.get(row.id) || [],
      eal: !!eal && !seg && !outcome,
      row,
    }
  })

  // The beacon reports a step when it ENDS, so while the run is on its agent
  // the first step with no record yet is the one executing.
  const onAgent = runStatus === 'running' && !!run?.delivered_at
  if (onAgent && !blocks.some((b) => b.state.key === 'running')) {
    const next = blocks.find((b) => b.state.key === 'queued' && !b.eal)
    if (next) next.state = { key: 'running', ...STEP_STATES.running }
  }

  return { blocks, preface: notes.get('') || [] }
}

/** "exit 0 · signal 9 · 1.2s · PLATFORM_UNSATISFIABLE" — what the host recorded, in one line. */
export function blockMeta(block) {
  return [
    block.exit != null ? `exit ${block.exit}` : null,
    block.signal ? `signal ${block.signal}` : null,
    block.duration || null,
    block.reason || null,
  ].filter(Boolean).join(' · ')
}
