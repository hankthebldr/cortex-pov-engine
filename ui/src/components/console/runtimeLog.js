/**
 * runtimeLog.js — a run's runtime log: one block per step, in order, then the
 * scenario cleanup. Pure.
 *
 * What was attempted, and how it went on the host. Three sources, all on
 * `GET /api/runs/{id}` (plus the scenario's own steps):
 *
 *   scenario steps   name, technique, the authored account and command;
 *   step_outcomes    the host's record of how each step ended (exit code,
 *                    termination, signal, reason) — authoritative for state;
 *   output           the beacon's transcript: each step's stdout/stderr, the
 *                    account it ran as, its duration, the cleanup it ran when
 *                    the task ended, and the launch markers written outside
 *                    any step.
 *
 * A finished run and a live one are built the same way: a step whose block
 * has a header and no footer yet is Running, with the lines that have arrived.
 * Two things a beacon with live output reports while a step runs:
 *
 *   - a step START (the command it is about to run) arrives as `starts`
 *     (`run.step_starts`, plus the starts heard on the run's stream). Its
 *     command is preferred over the scenario's, and the step reads Running
 *     from that moment;
 *   - output CHUNKS arrive as `live` (step id → text, see liveOutput.js). They
 *     are shown in the Running block and are never stored: once the step's
 *     own frame is in the transcript, that frame is what the block shows.
 *
 * The launch lines the run's page states as a notice (runNotices.js) are left
 * out of the log, so nothing is said twice.
 *
 * Nothing here says anything about a detection.
 */
import { parseStepTranscript, splitStepTranscript, buildStepRows } from './useRunStepTail.js'
import { STEP_STATES } from './runState.js'
import { isRunSettled } from './runStatus.js'
import { isNoticeLine } from './runNotices.js'

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

// ── The command's one-line preview ───────────────────────────────────────────
//
// A step that needs a scratch folder opens with the same set-up every time
// (scenario sandbox rule). On POSIX it is one line: `C=$(mktemp -d …) || exit
// 1; trap 'rm -rf "$C"' EXIT`, or for a folder several steps share, the line
// that makes it fresh (or checks it is still the run's own) and stops the step
// with `sandbox … could not be created fresh` / `is missing or not ours`. On
// Windows it is the block that makes `$sandbox` with its ACL and refuses a
// folder that was already there. None of it says what the step does, so the
// preview starts after it.
const POSIX_SCRATCH = /^\w+=\$\(mktemp -d\b[^)]*\)\s*\|\|\s*exit 1;\s*trap '[^']*' EXIT\b;?\s*/
const POSIX_SANDBOX = /\|\|\s*\{\s*echo "sandbox [^"]*(?:could not be created fresh|is missing or not ours|is not empty)[^"]*"[^;]*;\s*exit 1;\s*\}\s*$/
const WINDOWS_SCRATCH = [
  /^\$sandbox\s*=/,
  /^if \(Test-Path -LiteralPath \$sandbox\) \{ cmd\.exe \/c rmdir /,
  /^\$me\s*=\s*\[System\.Security\.Principal\.WindowsIdentity\]/,
  /^\$sd\s*=\s*New-Object System\.Security\.AccessControl\.DirectorySecurity/,
  /^\$sd\.SetSecurityDescriptorSddlForm\(/,
  /^\$null\s*=\s*\[System\.IO\.Directory\]::CreateDirectory\(\$sandbox\b/,
  /^\$acl\s*=\s*Get-Acl -LiteralPath \$sandbox\b/,
  /^\$ids\s*=\s*if \(\$acl\)/,
  /^if \(.*\) \{ Write-Error "sandbox \$sandbox (?:could not be created fresh|is not empty)/,
]

/** What is left of a line once its scratch-folder set-up is taken off; '' when it is all set-up. */
function afterScratch(line) {
  const m = POSIX_SCRATCH.exec(line)
  if (m) return line.slice(m[0].length).trim()
  if (POSIX_SANDBOX.test(line)) return ''
  return WINDOWS_SCRATCH.some((re) => re.test(line)) ? '' : line
}

/**
 * The line a command's preview shows: the first that does something. Blank
 * lines, comments and the scratch-folder set-up are passed over; a command
 * that is nothing else shows its first line.
 */
export function commandLead(command) {
  if (typeof command !== 'string') return ''
  const lines = command.split('\n').map((l) => l.trim()).filter(Boolean)
  for (const line of lines) {
    if (line.startsWith('#')) continue
    const rest = afterScratch(line)
    if (rest) return rest
  }
  return lines[0] || ''
}

function startsById(starts) {
  const map = new Map()
  const list = Array.isArray(starts)
    ? starts
    : starts && typeof starts === 'object'
      ? Object.entries(starts).map(([step_id, v]) => ({ step_id, ...(v && typeof v === 'object' ? v : { command: v }) }))
      : []
  for (const s of list) {
    // The first start for a step is the record (engine/live_output.py).
    if (s && s.step_id && !map.has(s.step_id)) map.set(s.step_id, s)
  }
  return map
}

function liveText(live, id) {
  if (!live) return ''
  const text = live instanceof Map ? live.get(id) : live[id]
  return typeof text === 'string' ? text : ''
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

// ── Cleanup ──────────────────────────────────────────────────────────────────

const CLEANUP_STATES = Object.freeze({
  clean:   { status: 'up',   word: 'Clean' },
  failed:  { status: 'down', word: 'Failed' },
  not_run: { status: 'off',  word: 'Not run' },
  pending: { status: 'off',  word: 'Pending' },
})

/** The beacon's reason codes, in words. Anything else is shown as it was given. */
const CLEANUP_REASONS = Object.freeze({
  NO_STEP_STARTED: 'no step started',
  COMMAND_FAILED: null,               // the state already says a command failed
  COMMAND_TIMEOUT: 'a command timed out',
  SHUTDOWN_BUDGET_SPENT: 'agent shut down first',
})

function cleanupReason(entries) {
  for (const e of entries) {
    if (e.reason == null || e.reason === '') continue
    const code = String(e.reason)
    if (code in CLEANUP_REASONS) {
      if (CLEANUP_REASONS[code]) return CLEANUP_REASONS[code]
      continue
    }
    return code
  }
  return null
}

/**
 * The cleanup block: what `Run.cleanup` records (one entry per task) and the
 * `=== CLEANUP` section(s) the beacon wrote, or null when the run carries
 * neither.
 */
function buildCleanup(run, sections, authored) {
  const entries = (Array.isArray(run?.cleanup) ? run.cleanup : []).filter((e) => e && typeof e === 'object')
  if (!entries.length && !sections.length) return null

  const ranCommands = sections.flatMap((s) => s.commands).map((c) => ({
    command: c.command,
    lines: c.lines.filter((l, i, all) => l.trim() || i < all.length - 1),
    exit: c.exitCode,
    duration: c.duration && c.duration !== '0s' ? c.duration : null,
    notRun: c.finished && c.exitCode == null,
  }))
  const fromSection = {
    total: sections.reduce((n, s) => n + (s.total || s.commands.length), 0),
    failed: ranCommands.filter((c) => c.exit != null && c.exit !== 0).length,
    cut: ranCommands.some((c) => c.notRun),
  }

  const settled = isRunSettled(run?.status)
  const reported = entries.filter((e) => e.status && e.status !== 'pending')
  const total = entries.reduce((n, e) => n + (Number(e.commands) || 0), 0) || fromSection.total
  let key
  let failed = 0
  let reason = null
  if (reported.length) {
    failed = reported.reduce((n, e) => n + (Number(e.failed) || 0), 0)
    if (reported.some((e) => e.status === 'failed')) key = 'failed'
    else if (entries.some((e) => e.status === 'pending') && !settled) key = 'pending'
    else if (reported.every((e) => e.status === 'not_run')) key = 'not_run'
    else key = 'clean'
    reason = cleanupReason(reported)
  } else if (sections.length) {
    // The section is in the transcript and its outcome was never recorded.
    failed = fromSection.failed
    key = failed || fromSection.cut ? 'failed' : 'clean'
  } else if (settled) {
    key = 'not_run'
    reason = 'not reported'
  } else {
    key = 'pending'
  }

  const state = { key, ...CLEANUP_STATES[key] }
  if (key === 'failed' && failed > 0 && total > 0) state.word = `${failed} of ${total} failed`

  const commands = ranCommands.length
    ? ranCommands
    : (Array.isArray(authored) ? authored : [])
      .filter((c) => typeof c === 'string' && c.trim())
      .map((command) => ({ command, lines: [], exit: null, duration: null, notRun: false }))
  return {
    state,
    total: total || commands.length,
    failed,
    reason,
    commands,
    commandsFrom: ranCommands.length ? 'agent' : commands.length ? 'scenario' : null,
  }
}

/** "3 commands · a command timed out" — the cleanup block's one line of detail. */
export function cleanupMeta(cleanup) {
  return [
    cleanup.total ? `${cleanup.total} command${cleanup.total === 1 ? '' : 's'}` : null,
    cleanup.reason || null,
  ].filter(Boolean).join(' · ')
}

/**
 * @param {Object}  args
 * @param {Array}   args.steps   the run's scenario steps, in order (may be empty)
 * @param {Object}  args.run     `GET /api/runs/{id}` (output, step_outcomes, …)
 * @param {Array|Object} [args.starts]  the beacon's record of each step's
 *        start: `[{ step_id, command, started_at? }]`. Defaults to
 *        `run.step_starts`; absent on a SimCore that does not record it.
 * @param {Map|Object} [args.live]  step id → what a running step has printed
 *        so far (liveOutput.js)
 * @param {string[]} [args.cleanupCommands]  the scenario's own cleanup
 *        commands, shown until the beacon's cleanup section says what ran
 * @returns {{ blocks: Array, preface: string[][], cleanup: Object|null }}
 *   `preface`: transcript lines written before any step (launch markers).
 */
export function buildRuntimeLog({ steps = [], run = null, starts = null, live = null, cleanupCommands = null } = {}) {
  const output = typeof run?.output === 'string' ? run.output : ''
  const segs = new Map()        // step id → its transcript block
  const notes = new Map()       // step id ('' = before any step) → lines after it
  const variants = new Map()    // step id → the OS whose variant ran
  const refused = new Map()     // step id → why the host would not run it
  const cleanups = []           // the beacon's cleanup sections
  let anchor = ''
  for (const seg of splitStepTranscript(output)) {
    if (seg.kind === 'cleanup') {
      cleanups.push(seg)
      continue
    }
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
    // The run's page states these as its notice; a chunk that opens or ends on
    // a blank line is still one note.
    const lines = seg.lines.filter((line) => !isNoticeLine(line))
    while (lines.length && !lines[0].trim()) lines.shift()
    while (lines.length && !lines[lines.length - 1].trim()) lines.pop()
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
    // Live text belongs to a step that has not ended: once its own frame is in
    // the transcript (or the host recorded its end), that is what is shown.
    const ended = !!seg?.finished || row.rc != null || (!!outcome?.termination && outcome.termination !== 'not_run')
    const streamed = ended ? '' : liveText(live, row.id)
    const started = !!(seg || start || outcome?.started_at || streamed)

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
    if (streamed) {
      const more = streamed.replace(/\r/g, '').split('\n')
      while (more.length && !more[more.length - 1]) more.pop()
      lines = [...lines, ...more]
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
      live: !!streamed,
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

  return {
    blocks,
    preface: notes.get('') || [],
    cleanup: buildCleanup(run, cleanups, cleanupCommands),
  }
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
