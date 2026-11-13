/**
 * useRunStepTail — one row per scenario step for a run in flight (or done).
 *
 * Two sources, two different trust levels, kept apart on purpose:
 *   - `step_outcomes` on `GET /api/runs/{id}` is the HOST's record of how a
 *     step ended (`rc`, `termination`, `signal`). It is authoritative for
 *     status and exit code.
 *   - the beacon's transcript in `Run.output` carries what the host record
 *     does not: the identity the step actually ran as, the step's wall-clock
 *     duration, and the `IDENTITY NOT HONOURED` marker. Its per-step grammar
 *     is fixed in `agent/beacon/client.go::formatStepOutput`:
 *
 *         === STEP i/N · <step-id> · <technique> · identity=<who> ===
 *         !! IDENTITY NOT HONOURED: <why>          (only when degraded)
 *         …stdout/stderr…
 *         --- exit_code=<rc> duration=<go duration> ---
 *
 * `parseStepTranscript` is pure and pinned by a test on those exact strings,
 * so a change to the beacon's format fails a test here instead of silently
 * emptying the pane.
 *
 * Status vocabulary per row:
 *   queued      — no evidence yet and the run is not terminal
 *   running     — a header without its footer, or first output recorded
 *   ok          — exit 0
 *   error       — non-zero exit, host says it exited
 *   severed     — the host killed it (block-mode prevention, or abort)
 *   not_reached — the run ended and this step never produced evidence
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { getRun } from '../../api/client.js'
import { isRunTerminal, isRunSettled } from './runStatus.js'
import useRunEventStream from './useRunEventStream.js'

const HEADER = /^=== STEP (\d+)\/(\d+) · (\S+) · (\S*) · identity=(\S*) ===\s*$/
const FOOTER = /^--- exit_code=(-?\d+) duration=(\S+) ---\s*$/
const IDENTITY_MARKER = '!! IDENTITY NOT HONOURED:'
const RUNTIME_MARKER = '!! RUNTIME_DEPENDENCY_MISSING:'

/**
 * @param {string|null|undefined} output  Run.output as the beacon POSTed it
 * @returns {{ order: string[], steps: Object<string, {
 *   index:number, total:number, technique:string, identity:string,
 *   exitCode:number|null, duration:string|null, finished:boolean,
 *   identityNote:string|null, runtimeNote:string|null }> }}
 */
export function parseStepTranscript(output) {
  const steps = {}
  const order = []
  if (!output || typeof output !== 'string') return { order, steps }
  let current = null
  for (const rawLine of output.split('\n')) {
    const line = rawLine.replace(/\r$/, '')
    const h = HEADER.exec(line)
    if (h) {
      const id = h[3]
      current = {
        index: Number(h[1]), total: Number(h[2]), technique: h[4] || '',
        identity: h[5] || '', exitCode: null, duration: null, finished: false,
        identityNote: null, runtimeNote: null,
      }
      if (!steps[id]) order.push(id)
      steps[id] = current
      continue
    }
    if (!current) continue
    if (line.startsWith(IDENTITY_MARKER)) {
      current.identityNote = line.slice(IDENTITY_MARKER.length).trim() || 'identity not honoured'
      continue
    }
    if (line.startsWith(RUNTIME_MARKER)) {
      current.runtimeNote = line.slice(RUNTIME_MARKER.length).trim() || 'runtime dependency missing'
      continue
    }
    const f = FOOTER.exec(line)
    if (f) {
      current.exitCode = Number(f[1])
      current.duration = f[2]
      current.finished = true
      current = null
    }
  }
  return { order, steps }
}

/**
 * Join scenario steps, the host's step outcomes and the transcript into rows.
 * Pure; the hook below only feeds it.
 */
export function buildStepRows(steps = [], run = null, parsed = { order: [], steps: {} }) {
  const outcomes = new Map()
  for (const o of run?.step_outcomes || []) if (o?.step_id) outcomes.set(o.step_id, o)
  const terminal = isRunTerminal(run?.status)
  const pushMode = String(run?.mode || '').toLowerCase() === 'push'
  const known = Array.isArray(steps) && steps.length
    ? steps.map((s) => ({ id: s.id, name: s.name, identity: s.identity, technique: s.mitre_technique }))
    : parsed.order.map((id) => ({ id }))

  return known.map((s, i) => {
    const o = outcomes.get(s.id) || null
    const t = parsed.steps[s.id] || null
    let status = 'queued'
    let rc = null
    if (o && o.rc != null) {
      rc = Number(o.rc)
      status = o.termination === 'severed' ? 'severed' : rc === 0 ? 'ok' : 'error'
    } else if (t && t.finished) {
      rc = t.exitCode
      status = rc === 0 ? 'ok' : 'error'
    } else if (t || (o && o.started_at)) {
      status = terminal ? 'not_reached' : 'running'
    } else if (pushMode) {
      // A push bundle executes off this SimCore: no beacon queues the step
      // and no host records it. 'staged' is the honest state — never
      // 'queued' (a beacon will come) and never 'not_reached' (the chain ran
      // here and stopped short). Host evidence above still wins when a DC
      // reports it back.
      status = 'staged'
    } else if (terminal) {
      status = 'not_reached'
    }
    return {
      id: s.id,
      index: i + 1,
      name: s.name || null,
      technique: (t && t.technique) || s.technique || null,
      identity: (t && t.identity) || s.identity || null,
      identityNote: t?.identityNote || null,
      runtimeNote: t?.runtimeNote || null,
      status,
      rc,
      duration: t?.duration || null,
      reason: o?.reason || null,
      signal: o?.signal || null,
      // How the host says the step ended (exited · signaled · timeout · …);
      // the Broken step's troubleshooting sheet reads its causes from it.
      termination: o?.termination || null,
    }
  })
}

/** Operator-facing labels for the `status` a step row carries. One copy. */
export const STEP_STATUS_LABEL = Object.freeze({
  queued: 'queued', running: 'running', ok: 'ok', error: 'error',
  severed: 'severed', not_reached: 'not reached', staged: 'staged',
})

const POLL_MS = 3_000

/**
 * @param {string|null} runId
 * @param {Array} steps  the launched scenario's steps, in order (may be empty)
 */
export default function useRunStepTail(runId, steps = []) {
  const [run, setRun] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)
  const { events } = useRunEventStream(runId)
  const inflight = useRef(false)
  const runRef = useRef(null)

  useEffect(() => { setRun(null); setError(null); runRef.current = null }, [runId])

  useEffect(() => {
    if (!runId) return undefined
    let cancelled = false
    const fetchOnce = async () => {
      if (inflight.current) return
      inflight.current = true
      setLoading(true)
      try {
        const r = await getRun(runId)
        if (!cancelled) { runRef.current = r; setRun(r); setError(null) }
      } catch (err) {
        if (!cancelled) setError(err?.message || 'could not load the run')
      } finally {
        inflight.current = false
        if (!cancelled) setLoading(false)
      }
    }
    fetchOnce()
    const t = setInterval(() => {
      // Settled, not merely terminal: a staged push bundle never advances
      // here either, and polling it every 3 s for ever is a leak.
      if (runRef.current && isRunSettled(runRef.current.status)) return
      fetchOnce()
    }, POLL_MS)
    return () => { cancelled = true; clearInterval(t) }
  }, [runId])

  // Every stream frame (status, output chunk, observation) is a reason to
  // re-read the record; the record, not the frame, is what the rows show.
  const eventCount = events.length
  useEffect(() => {
    if (!runId || eventCount === 0) return undefined
    const t = setTimeout(async () => {
      if (inflight.current) return
      inflight.current = true
      try {
        const r = await getRun(runId)
        runRef.current = r
        setRun(r)
        setError(null)
      } catch (err) {
        setError(err?.message || 'could not load the run')
      } finally {
        inflight.current = false
      }
    }, 250)
    return () => clearTimeout(t)
  }, [runId, eventCount])

  const rows = useMemo(
    () => buildStepRows(steps, run, parseStepTranscript(run?.output)),
    [steps, run],
  )

  return { run, rows, error, loading, terminal: isRunSettled(run?.status) }
}
