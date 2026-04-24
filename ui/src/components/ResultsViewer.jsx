import React, { useState, useEffect, useCallback } from 'react'
import { getRuns, getResultsForRun, validateResult, downloadReport } from '../api/client.js'

// --- Helpers ----------------------------------------------------------------

function StatusBadge({ status }) {
  const map = {
    complete: { cls: 'badge-success', label: 'Complete' },
    running:  { cls: 'badge-teal',    label: 'Running' },
    pending:  { cls: 'badge-steel',   label: 'Pending' },
    failed:   { cls: 'badge-danger',  label: 'Failed' },
  }
  const { cls, label } = map[status] || { cls: 'badge-steel', label: status || '—' }
  return <span className={`badge ${cls}`}>{label}</span>
}

function formatDuration(startedAt, completedAt) {
  if (!startedAt) return '—'
  const start = new Date(startedAt)
  const end = completedAt ? new Date(completedAt) : new Date()
  const secs = Math.round((end - start) / 1000)
  if (secs < 60) return `${secs}s`
  if (secs < 3600) return `${Math.floor(secs / 60)}m ${secs % 60}s`
  return `${Math.floor(secs / 3600)}h ${Math.floor((secs % 3600) / 60)}m`
}

function formatTime(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    })
  } catch {
    return iso
  }
}

function formatMTTD(seconds) {
  if (seconds == null) return null
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
}

// --- Coverage Bar -----------------------------------------------------------

function CoverageBar({ label, observed, total }) {
  const pct = total > 0 ? Math.round((observed / total) * 100) : 0
  const fillCls = pct >= 75 ? 'fill-success' : pct >= 40 ? 'fill-warning' : 'fill-danger'

  return (
    <div style={{ marginBottom: '8px' }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between',
        alignItems: 'center', marginBottom: '4px',
      }}>
        <span style={{ fontSize: '12px', fontWeight: 600, color: 'var(--cortex-navy)' }}>
          {label}
        </span>
        <span style={{
          fontSize: '12px', fontFamily: 'var(--font-mono)',
          color: pct >= 75 ? 'var(--cortex-success)' : pct >= 40 ? 'var(--cortex-warning)' : 'var(--cortex-danger)',
          fontWeight: 700,
        }}>
          {pct}% ({observed}/{total})
        </span>
      </div>
      <div className="coverage-bar">
        <div className={`coverage-bar-fill ${fillCls}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

// --- MTTD Summary -----------------------------------------------------------

function MTTDSummary({ mttd }) {
  if (!mttd) return null
  return (
    <div style={{
      background: 'var(--cortex-navy)', color: 'white',
      borderRadius: 'var(--radius-md)', padding: '12px 16px',
      marginBottom: '16px',
    }}>
      <div style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.5px', opacity: 0.7, marginBottom: '6px' }}>
        Mean Time to Detect (MTTD)
      </div>
      <div style={{ display: 'flex', gap: '24px', alignItems: 'baseline' }}>
        <div>
          <span style={{ fontSize: '24px', fontWeight: 700, fontFamily: 'var(--font-mono)', color: 'var(--cortex-teal)' }}>
            {formatMTTD(mttd.avg_seconds)}
          </span>
          <span style={{ fontSize: '11px', opacity: 0.7, marginLeft: '6px' }}>avg</span>
        </div>
        <div style={{ fontSize: '12px', opacity: 0.8, fontFamily: 'var(--font-mono)' }}>
          min {formatMTTD(mttd.min_seconds)} · max {formatMTTD(mttd.max_seconds)} · {mttd.count} detections
        </div>
      </div>
    </div>
  )
}

// --- Interactive Detection Row ----------------------------------------------

function DetectionRow({ result, onValidate }) {
  const [saving, setSaving] = useState(false)
  const [notesOpen, setNotesOpen] = useState(false)
  const [notesText, setNotesText] = useState(result.notes || '')
  const observed = result.observed === true

  const handleToggle = async () => {
    setSaving(true)
    try {
      await onValidate(result.id, !observed, notesText || null)
    } finally {
      setSaving(false)
    }
  }

  const handleNotesSave = async () => {
    setSaving(true)
    try {
      await onValidate(result.id, observed, notesText)
      setNotesOpen(false)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div style={{
      display: 'flex', alignItems: 'flex-start', gap: '10px',
      padding: '10px 0', borderBottom: '1px solid var(--cortex-border)',
      opacity: saving ? 0.6 : 1,
    }}>
      {/* Clickable observed toggle */}
      <button
        onClick={handleToggle}
        disabled={saving}
        title={observed ? 'Mark as NOT detected' : 'Mark as detected in XSIAM'}
        style={{
          width: '24px', height: '24px', borderRadius: '4px',
          border: observed ? '2px solid var(--cortex-success)' : '2px solid var(--cortex-border)',
          background: observed ? 'var(--cortex-success)' : 'transparent',
          color: observed ? 'white' : 'var(--cortex-steel)',
          cursor: 'pointer', flexShrink: 0, marginTop: '2px',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: '14px', fontWeight: 700,
        }}
      >
        {observed ? '✓' : ''}
      </button>

      <div style={{ flex: 1, minWidth: 0 }}>
        {/* Step + signal type badges */}
        <div style={{ display: 'flex', gap: '6px', alignItems: 'center', marginBottom: '3px', flexWrap: 'wrap' }}>
          {result.step_id && (
            <span className="badge badge-steel" style={{ fontSize: '10px' }}>
              {result.step_id}
            </span>
          )}
          <span className="badge badge-navy" style={{ fontSize: '10px' }}>
            {result.signal_type || '—'}
          </span>
          <span className="badge badge-steel" style={{ fontSize: '10px' }}>
            {result.plane || '—'}
          </span>
          {result.mttd_seconds != null && (
            <span style={{
              fontSize: '11px', fontFamily: 'var(--font-mono)',
              color: 'var(--cortex-teal)', fontWeight: 600,
            }}>
              MTTD: {formatMTTD(result.mttd_seconds)}
            </span>
          )}
        </div>

        {/* Step name */}
        {result.step_name && (
          <div style={{ fontSize: '11px', fontWeight: 600, color: 'var(--cortex-navy)', marginBottom: '2px' }}>
            {result.step_name}
          </div>
        )}

        {/* Expected detection description */}
        <p style={{ fontSize: '12px', color: '#1A2B3C', margin: 0 }}>
          {result.expected_detection || '—'}
        </p>

        {/* Notes display/edit */}
        {result.notes && !notesOpen && (
          <p style={{
            fontSize: '11px', color: 'var(--cortex-steel)',
            margin: '3px 0 0', fontStyle: 'italic', cursor: 'pointer',
          }} onClick={() => setNotesOpen(true)} title="Click to edit notes">
            {result.notes}
          </p>
        )}

        {/* Action buttons */}
        <div style={{ display: 'flex', gap: '8px', marginTop: '4px' }}>
          {!notesOpen && (
            <button
              onClick={() => setNotesOpen(true)}
              style={{
                fontSize: '11px', color: 'var(--cortex-teal)',
                background: 'none', border: 'none', cursor: 'pointer',
                padding: 0, textDecoration: 'underline',
              }}
            >
              {result.notes ? 'Edit notes' : 'Add notes'}
            </button>
          )}
        </div>

        {/* Notes editor */}
        {notesOpen && (
          <div style={{ marginTop: '6px', display: 'flex', gap: '6px' }}>
            <input
              type="text"
              value={notesText}
              onChange={e => setNotesText(e.target.value)}
              placeholder="Alert name, XQL query used, XSIAM alert ID..."
              style={{
                flex: 1, fontSize: '12px', padding: '4px 8px',
                border: '1px solid var(--cortex-border)', borderRadius: '4px',
                fontFamily: 'var(--font-mono)',
              }}
              onKeyDown={e => e.key === 'Enter' && handleNotesSave()}
            />
            <button className="btn btn-sm" onClick={handleNotesSave} disabled={saving}
              style={{ fontSize: '11px', padding: '4px 8px' }}>
              Save
            </button>
            <button className="btn btn-secondary btn-sm" onClick={() => setNotesOpen(false)}
              style={{ fontSize: '11px', padding: '4px 8px' }}>
              Cancel
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

// --- Run Detail Drawer ------------------------------------------------------

function RunDetail({ run }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchResults = useCallback(() => {
    if (!run?.run_id) return
    setLoading(true)
    getResultsForRun(run.run_id)
      .then(d => setData(d))
      .catch(err => setError(err.message))
      .finally(() => setLoading(false))
  }, [run?.run_id])

  useEffect(() => { fetchResults() }, [fetchResults])

  const handleValidate = async (resultId, observed, notes) => {
    await validateResult(resultId, observed, notes)
    fetchResults() // Refresh to get updated coverage + MTTD stats
  }

  const handleDownloadReport = async () => {
    try {
      const blob = await downloadReport(run.run_id)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `cortexsim-report-${run.scenario_id}-${run.run_id.slice(0, 8)}.md`
      a.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      console.error('Report download failed:', err)
    }
  }

  const results = data?.results || []
  const coverage = data?.coverage || {}
  const mttd = data?.mttd || null

  // Group results by step
  const byStep = {}
  results.forEach(r => {
    const key = r.step_id || 'unknown'
    if (!byStep[key]) byStep[key] = { step_name: r.step_name, results: [] }
    byStep[key].results.push(r)
  })

  return (
    <div style={{
      background: 'var(--cortex-light-bg)',
      border: '1px solid var(--cortex-border)',
      borderRadius: 'var(--radius-md)',
      margin: '4px 0 12px 0',
      padding: '16px 20px',
    }}>
      {loading ? (
        <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
          <div className="spinner" />
          <span className="text-muted" style={{ fontSize: '12px' }}>Loading results…</span>
        </div>
      ) : error ? (
        <p style={{ fontSize: '12px', color: 'var(--cortex-danger)' }}>{error}</p>
      ) : results.length === 0 ? (
        <p style={{ fontSize: '12px', color: 'var(--cortex-steel)' }}>
          No detection results recorded for this run.
        </p>
      ) : (
        <>
          {/* MTTD headline */}
          <MTTDSummary mttd={mttd} />

          {/* Report download */}
          <div style={{ marginBottom: '12px', textAlign: 'right' }}>
            <button
              className="btn btn-sm"
              onClick={handleDownloadReport}
              style={{ fontSize: '12px' }}
            >
              &#8681; Download POV Report
            </button>
          </div>

          {/* Coverage summary */}
          <div style={{ marginBottom: '16px' }}>
            <p className="section-label">Detection Coverage</p>
            <CoverageBar
              label="Overall"
              observed={coverage.observed || 0}
              total={coverage.total || 0}
            />
            {coverage.by_type && Object.entries(coverage.by_type).map(([type, stats]) => (
              <CoverageBar
                key={type}
                label={type}
                observed={stats.observed}
                total={stats.total}
              />
            ))}
          </div>

          <hr className="divider" />

          {/* Results grouped by step — interactive validation */}
          <div>
            <p className="section-label" style={{ marginBottom: '4px' }}>
              Validate Detections — check each detection you confirmed in XSIAM ({coverage.observed || 0}/{coverage.total || 0})
            </p>
            <p style={{ fontSize: '11px', color: 'var(--cortex-steel)', marginBottom: '12px' }}>
              Click the checkbox when you see the alert in XSIAM. MTTD is calculated automatically.
            </p>

            {Object.entries(byStep).map(([stepId, { step_name, results: stepResults }]) => (
              <div key={stepId} style={{ marginBottom: '8px' }}>
                {stepResults.map(r => (
                  <DetectionRow key={r.id} result={r} onValidate={handleValidate} />
                ))}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

// --- Main Component ---------------------------------------------------------

export default function ResultsViewer({ runs: propRuns, onClose }) {
  const [runs, setRuns] = useState(propRuns || [])
  const [loading, setLoading] = useState(false)
  const [expandedRun, setExpandedRun] = useState(null)

  const refresh = useCallback(() => {
    setLoading(true)
    getRuns()
      .then(data => {
        const list = data?.runs || (Array.isArray(data) ? data : [])
        setRuns(list)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { refresh() }, [])

  useEffect(() => {
    if (propRuns) setRuns(propRuns)
  }, [propRuns])

  const handleRowClick = (run) => {
    setExpandedRun(prev => prev?.run_id === run.run_id ? null : run)
  }

  return (
    <div className="panel-card">
      <div className="panel-card-header">
        <h3>Run History & Detection Validation</h3>
        <div style={{ display: 'flex', gap: '8px' }}>
          <button
            className="btn btn-secondary btn-sm"
            onClick={refresh}
            disabled={loading}
            title="Refresh run list"
          >
            {loading ? <span className="spinner" /> : '⟳ Refresh'}
          </button>
          {onClose && (
            <button
              className="btn btn-secondary btn-sm"
              onClick={onClose}
              title="Close run history"
            >
              ✕ Close
            </button>
          )}
        </div>
      </div>

      <div className="panel-card-body" style={{ padding: 0 }}>
        {loading && runs.length === 0 ? (
          <div className="empty-state">
            <div className="spinner" style={{ margin: '0 auto var(--space-4)' }} />
            <p>Loading run history…</p>
          </div>
        ) : runs.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon">&#9654;</div>
            <p>No runs yet. Launch a scenario to see results here.</p>
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Scenario</th>
                  <th>Mode</th>
                  <th>Status</th>
                  <th>Started</th>
                  <th>Duration</th>
                </tr>
              </thead>
              <tbody>
                {runs.map(run => {
                  const isExpanded = expandedRun?.run_id === run.run_id
                  return (
                    <React.Fragment key={run.run_id || run.id}>
                      <tr
                        onClick={() => handleRowClick(run)}
                        className={isExpanded ? 'row-selected' : ''}
                        title="Click to validate detection results"
                        style={{ cursor: 'pointer' }}
                      >
                        <td>
                          <div style={{ fontSize: '13px', fontWeight: 500, color: 'var(--cortex-navy)' }}>
                            {run.scenario_id}
                          </div>
                          <div style={{ fontSize: '11px', color: 'var(--cortex-steel)', fontFamily: 'var(--font-mono)' }}>
                            {run.run_id?.slice(0, 8)}…
                            {run.identity_context ? ` · ${run.identity_context}` : ''}
                          </div>
                        </td>
                        <td>
                          <span className={`badge ${run.mode === 'push' ? 'badge-teal' : 'badge-navy'}`}>
                            {run.mode || '—'}
                          </span>
                        </td>
                        <td><StatusBadge status={run.status} /></td>
                        <td><span style={{ fontSize: '12px' }}>{formatTime(run.started_at)}</span></td>
                        <td>
                          <span className="text-mono" style={{ fontSize: '12px' }}>
                            {formatDuration(run.started_at, run.completed_at)}
                          </span>
                        </td>
                      </tr>
                      {isExpanded && (
                        <tr>
                          <td colSpan={5} style={{ padding: '4px 16px 4px', background: 'var(--cortex-light-bg)' }}>
                            <RunDetail run={run} />
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
