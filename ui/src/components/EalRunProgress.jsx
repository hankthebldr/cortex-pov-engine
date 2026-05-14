import React, { useState, useEffect, useRef } from 'react'
import { getEalRun } from '../api/client.js'

const POLL_INTERVAL_MS = 2000
const TERMINAL_STATES = new Set(['complete', 'failed', 'aborted'])

/**
 * EalRunProgress — live tail of a single EalCampaignRun.
 *
 * Polls /api/eal/runs/:run_id every 2s until the run reaches a terminal
 * state (complete | failed | aborted), then stops. Shows:
 *
 *   - top-line: status pill + duration + dry-run flag
 *   - step result table (plugin, status, events emitted, bytes sent,
 *     error if any)
 *   - last error message at the bottom if status=failed/aborted
 *
 * SSE / WebSocket-based live updates are a Phase-12 deliverable; 2s
 * polling is plenty for the human-watch use case.
 */
export default function EalRunProgress({ runId, onClose, onMessage }) {
  const [run, setRun] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const pollingRef = useRef(null)
  const stoppedRef = useRef(false)

  useEffect(() => {
    stoppedRef.current = false

    const tick = async () => {
      if (stoppedRef.current) return
      try {
        const data = await getEalRun(runId)
        setRun(data)
        setError(null)
        setLoading(false)
        if (TERMINAL_STATES.has(data?.status)) {
          // One last refresh on terminal; no further polls.
          stop()
          if (data.status === 'complete') {
            onMessage?.(`Run ${data.run_id.slice(0, 8)} complete`, 'success')
          } else {
            onMessage?.(`Run ${data.run_id.slice(0, 8)} ${data.status}`, 'error')
          }
        }
      } catch (err) {
        setError(err.message)
        setLoading(false)
      }
    }

    const stop = () => {
      stoppedRef.current = true
      if (pollingRef.current) {
        clearInterval(pollingRef.current)
        pollingRef.current = null
      }
    }

    tick()  // immediate first fetch
    pollingRef.current = setInterval(tick, POLL_INTERVAL_MS)

    return stop
    // run-id is stable for the lifetime of this component instance (key={runId} on the parent)
    // so we only set up polling once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId])

  if (loading && !run) {
    return (
      <div className="eal-run-progress">
        <p className="muted">Loading run…</p>
      </div>
    )
  }

  if (error && !run) {
    return (
      <div className="eal-run-progress">
        <p className="error">Failed to load run: {error}</p>
        {onClose && (
          <button className="btn btn-sm btn-secondary" onClick={onClose}>Close</button>
        )}
      </div>
    )
  }

  const steps = Array.isArray(run?.step_results) ? run.step_results : []
  const durationSec = computeDurationSec(run)
  const isLive = !!run && !TERMINAL_STATES.has(run.status)

  return (
    <div className="eal-run-progress">
      <header className="eal-run-progress__head">
        <div className="flex-row" style={{ gap: '12px' }}>
          <span className={`pill pill-${tone(run?.status)}`}>{run?.status || '–'}</span>
          {isLive && <span className="muted small">live (polling every 2s)</span>}
          <span className="muted small">run {run?.run_id?.slice(0, 12)}…</span>
          <span className="muted small">campaign {run?.campaign_id}</span>
          <span className="muted small">
            {run?.dry_run ? 'dry-run' : 'live'} · {durationSec != null ? `${durationSec.toFixed(1)}s` : 'pending'}
          </span>
        </div>
        {onClose && (
          <button className="btn btn-sm btn-secondary" onClick={onClose}>Close</button>
        )}
      </header>

      {steps.length === 0 && (
        <p className="muted">No step results yet — waiting for the first plugin to emit…</p>
      )}

      {steps.length > 0 && (
        <table className="cs-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Plugin</th>
              <th>Step</th>
              <th>Status</th>
              <th>Events</th>
              <th>Bytes</th>
              <th>Duration</th>
              <th>Detail / Error</th>
            </tr>
          </thead>
          <tbody>
            {steps.map((s, i) => (
              <tr key={`${s.plugin}:${s.step_id}:${i}`}>
                <td className="mono small">{i + 1}</td>
                <td><code className="mono small">{s.plugin}</code></td>
                <td className="mono small">{s.step_id}</td>
                <td>
                  <span className={`pill pill-${tone(s.status)}`}>{s.status}</span>
                </td>
                <td className="mono small right">{s.events_emitted ?? 0}</td>
                <td className="mono small right">{s.bytes_sent ?? 0}</td>
                <td className="mono small right">
                  {s.duration_seconds != null ? `${s.duration_seconds.toFixed(2)}s` : '–'}
                </td>
                <td className="small">
                  {s.error ? (
                    <code className="error mono small">{s.error}</code>
                  ) : (
                    <code className="muted mono small">{summariseDetail(s.detail)}</code>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {run?.error && (
        <div className="error-banner" style={{ marginTop: '8px' }}>
          <strong>Run-level error:</strong> <code className="mono small">{run.error}</code>
        </div>
      )}
    </div>
  )
}

function tone(status) {
  if (status === 'success' || status === 'complete') return 'success'
  if (status === 'running' || status === 'pending') return 'info'
  if (status === 'error' || status === 'failed' || status === 'aborted') return 'error'
  return 'neutral'
}

function computeDurationSec(run) {
  if (!run?.started_at) return null
  const start = Date.parse(run.started_at)
  const end = run.completed_at ? Date.parse(run.completed_at) : Date.now()
  if (!isFinite(start) || !isFinite(end)) return null
  return (end - start) / 1000
}

function summariseDetail(detail) {
  if (!detail || typeof detail !== 'object') return ''
  // Surface the 2-3 most informative keys per plugin.
  const interesting = [
    'iterations_completed', 'iterations_planned',
    'attempts_run', 'vuln_count', 'clean_count',
    'queries_sent', 'sessions_completed',
    'hosts_probed', 'hosts_skipped_unauthorised',
    'requests_completed', 'total_bytes_sent',
    'success_count', 'blocked_count', 'failure_count',
  ]
  const parts = []
  for (const k of interesting) {
    if (k in detail && detail[k] != null) parts.push(`${k}=${detail[k]}`)
  }
  return parts.join(' · ') || JSON.stringify(detail).slice(0, 80)
}
