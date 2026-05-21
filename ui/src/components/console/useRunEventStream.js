import { useState, useEffect, useRef } from 'react'

/**
 * useRunEventStream — subscribes to per-run event stream from the backend.
 *
 * Tries the streaming endpoint first (`/api/runs/:id/events`); if the
 * backend doesn't implement it yet (older SimCore builds), falls back
 * to a polling shape via `/api/runs/:id` + a synthetic event projector
 * that derives plausible log lines from the run's current step + results.
 *
 * The synthetic projector is intentionally conservative — it only
 * surfaces what's already in the run record, marked with a [SYNTH]
 * prefix so DCs know they're not seeing real agent stdout. Live
 * production environments will hit the real SSE/WS endpoint.
 *
 * Each event:
 *   {
 *     id: string,
 *     timestamp: ISO string,
 *     level: 'info' | 'warn' | 'error' | 'detect' | 'step',
 *     stepIndex: number | null,
 *     message: string,
 *     synthetic: boolean,
 *   }
 *
 * @param {string|null} runId
 * @param {object} options
 *   - paused (boolean) — pause streaming (default false)
 *   - maxEvents (number) — ring buffer cap (default 500)
 */
const BASE_URL = typeof window !== 'undefined' ? window.location.origin : ''
const POLL_INTERVAL_MS = 3_000

export default function useRunEventStream(runId, { paused = false, maxEvents = 500 } = {}) {
  const [events, setEvents] = useState([])
  const [connected, setConnected] = useState(false)
  const [mode, setMode] = useState('idle') // 'idle' | 'stream' | 'poll' | 'error'
  const seenIds = useRef(new Set())
  const lastTimestamp = useRef(null)

  // Append helper with ring-buffer trim.
  const append = (incoming) => {
    if (!Array.isArray(incoming) || incoming.length === 0) return
    const fresh = incoming.filter((e) => e && e.id && !seenIds.current.has(e.id))
    if (fresh.length === 0) return
    for (const e of fresh) seenIds.current.add(e.id)
    setEvents((prev) => {
      const next = prev.concat(fresh)
      if (next.length > maxEvents) return next.slice(next.length - maxEvents)
      return next
    })
  }

  useEffect(() => {
    // Reset when run changes.
    setEvents([])
    seenIds.current = new Set()
    lastTimestamp.current = null
    setConnected(false)
    setMode('idle')
    if (!runId || paused) return undefined

    let cancelled = false
    let eventSource = null
    let pollTimer = null

    // Try SSE first.
    try {
      const url = `${BASE_URL}/api/runs/${runId}/events`
      eventSource = new EventSource(url)
      let sseAlive = false

      eventSource.onopen = () => {
        if (cancelled) return
        sseAlive = true
        setConnected(true)
        setMode('stream')
      }

      eventSource.onmessage = (e) => {
        if (cancelled) return
        try {
          const evt = JSON.parse(e.data)
          if (evt && evt.id) {
            evt.synthetic = !!evt.synthetic
            append([evt])
          }
        } catch {
          /* ignore malformed lines */
        }
      }

      eventSource.onerror = () => {
        // Connection failed / 404. Tear down and fall through to polling.
        if (eventSource) {
          eventSource.close()
          eventSource = null
        }
        if (!sseAlive && !cancelled) {
          startPolling()
        }
      }
    } catch {
      startPolling()
    }

    function startPolling() {
      if (cancelled) return
      setMode('poll')
      setConnected(true)
      const tick = async () => {
        if (cancelled) return
        try {
          const r = await fetch(`${BASE_URL}/api/runs/${runId}`)
          if (!r.ok) throw new Error(`http ${r.status}`)
          const run = await r.json()
          const synthetic = projectEvents(run, lastTimestamp.current)
          if (synthetic.length > 0) {
            lastTimestamp.current = synthetic[synthetic.length - 1].timestamp
            append(synthetic)
          }
        } catch {
          setMode('error')
          setConnected(false)
        }
        if (!cancelled) {
          pollTimer = setTimeout(tick, POLL_INTERVAL_MS)
        }
      }
      tick()
    }

    return () => {
      cancelled = true
      if (eventSource) eventSource.close()
      if (pollTimer) clearTimeout(pollTimer)
    }
  }, [runId, paused, maxEvents])

  const reset = () => {
    setEvents([])
    seenIds.current = new Set()
    lastTimestamp.current = null
  }

  return { events, connected, mode, reset }
}

/* ─── Synthetic event projector ─────────────────────────────────────── */

/**
 * Derive plausible agent-side events from a run snapshot. Only emits
 * events newer than `sinceIso` so successive polls don't replay old
 * lines. Marks every emission `synthetic: true` so the UI can flag
 * "this is a projection, not real agent stdout."
 */
function projectEvents(run, sinceIso) {
  if (!run) return []
  const events = []
  const baseTs = run.started_at || new Date().toISOString()

  // Run lifecycle events
  events.push({
    id: `run-${run.id || run.run_id}-start`,
    timestamp: baseTs,
    level: 'info',
    stepIndex: null,
    message: `[SYNTH] run started · scenario=${run.scenario_id} mode=${run.mode || 'pull'}`,
    synthetic: true,
  })

  const steps = run.steps || run.expected_steps || []
  const current = run.current_step ?? run.step ?? 0
  for (let i = 0; i < steps.length; i++) {
    const step = steps[i]
    const stepTs = step.executed_at || step.timestamp || baseTs
    if (i < current || step.status === 'done') {
      events.push({
        id: `run-${run.id}-step-${i + 1}-exec`,
        timestamp: stepTs,
        level: 'step',
        stepIndex: i,
        message: `[SYNTH] step ${i + 1}/${steps.length} executed · ${step.mitre_technique || step.id || 'unnamed'} · identity=${step.identity || 'root'}`,
        synthetic: true,
      })
    } else if (i + 1 === current || step.status === 'pending') {
      events.push({
        id: `run-${run.id}-step-${i + 1}-pending`,
        timestamp: stepTs,
        level: 'info',
        stepIndex: i,
        message: `[SYNTH] step ${i + 1}/${steps.length} pending · awaiting agent heartbeat for ${step.mitre_technique || step.id || 'unnamed'}`,
        synthetic: true,
      })
    }
  }

  // Detection signal events
  const results = run.results || []
  for (const r of results) {
    if (!r.observed_at) continue
    events.push({
      id: `result-${r.id || r.result_id}`,
      timestamp: r.observed_at,
      level: 'detect',
      stepIndex: r.step_index ?? null,
      message: `[SYNTH] detection: ${r.plane || '?'} ${r.detection_type || ''} · ${r.expected_description || r.description || '(unnamed)'} · MTTD ${r.mttd_seconds != null ? r.mttd_seconds + 's' : '?'}`,
      synthetic: true,
    })
  }

  // Sort + filter to only newer than sinceIso
  events.sort((a, b) => (a.timestamp < b.timestamp ? -1 : 1))
  if (sinceIso) {
    return events.filter((e) => e.timestamp > sinceIso)
  }
  return events
}
