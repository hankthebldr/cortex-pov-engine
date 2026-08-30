import React, { useState, useEffect, useCallback } from 'react'
import { getRuns, getResultsForRun, validateResult, downloadReport } from '../api/client.js'
import { runIdOf } from '../api/ids.js'
import Term from './onboarding/Term.jsx'

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
  const pctCls = pct >= 75 ? 'rv-cov__pct--good' : pct >= 40 ? 'rv-cov__pct--warn' : 'rv-cov__pct--bad'

  return (
    <div className="rv-cov">
      <div className="rv-cov__head">
        <span className="rv-cov__label">
          {label}
        </span>
        <span className={`rv-cov__pct ${pctCls}`}>
          {pct}% ({observed}/{total})
        </span>
      </div>
      <div className="coverage-bar">
        {/* width is computed from live coverage data — cannot be a static class */}
        <div className={`coverage-bar-fill ${fillCls}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

// --- MTTD Summary -----------------------------------------------------------

function MTTDSummary({ mttd }) {
  if (!mttd) return null
  return (
    <div className="rv-mttd">
      <div className="rv-mttd__label">
        Mean Time to Detect (<Term k="mttd">MTTD</Term>)
      </div>
      <div className="rv-mttd__body">
        <div>
          <span className="rv-mttd__avg">
            {formatMTTD(mttd.avg_seconds)}
          </span>
          <span className="rv-mttd__avg-unit">avg</span>
        </div>
        <div className="rv-mttd__stats">
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
    <div className={`rv-det-row${saving ? ' rv-det-row--saving' : ''}`}>
      {/* Clickable observed toggle */}
      <button
        onClick={handleToggle}
        disabled={saving}
        title={observed ? 'Mark as NOT detected' : 'Mark as detected in XSIAM'}
        className={`rv-det-toggle${observed ? ' rv-det-toggle--observed' : ''}`}
      >
        {observed ? '✓' : ''}
      </button>

      <div className="rv-det-row__body">
        {/* Step + signal type badges */}
        <div className="rv-det-row__badges">
          {result.step_id && (
            <span className="badge badge-steel badge--sm">
              {result.step_id}
            </span>
          )}
          <span className="badge badge-navy badge--sm">
            {result.signal_type || '—'}
          </span>
          <span className="badge badge-steel badge--sm">
            {result.plane || '—'}
          </span>
          {result.mttd_seconds != null && (
            <span className="rv-det-row__mttd">
              MTTD: {formatMTTD(result.mttd_seconds)}
            </span>
          )}
        </div>

        {/* Step name */}
        {result.step_name && (
          <div className="rv-det-row__step-name">
            {result.step_name}
          </div>
        )}

        {/* Expected detection description */}
        <p className="rv-det-row__desc">
          {result.expected_detection || '—'}
        </p>

        {/* Notes display/edit */}
        {result.notes && !notesOpen && (
          <p className="rv-det-row__notes" onClick={() => setNotesOpen(true)} title="Click to edit notes">
            {result.notes}
          </p>
        )}

        {/* Action buttons */}
        <div className="rv-det-row__actions">
          {!notesOpen && (
            <button
              onClick={() => setNotesOpen(true)}
              className="rv-det-row__notes-toggle"
            >
              {result.notes ? 'Edit notes' : 'Add notes'}
            </button>
          )}
        </div>

        {/* Notes editor */}
        {notesOpen && (
          <div className="rv-det-row__notes-editor">
            <input
              type="text"
              value={notesText}
              onChange={e => setNotesText(e.target.value)}
              placeholder="Alert name, XQL query used, XSIAM alert ID..."
              className="rv-det-row__notes-input"
              onKeyDown={e => e.key === 'Enter' && handleNotesSave()}
            />
            <button className="btn btn-sm rv-det-row__btn-sm" onClick={handleNotesSave} disabled={saving}>
              Save
            </button>
            <button className="btn btn-secondary btn-sm rv-det-row__btn-sm" onClick={() => setNotesOpen(false)}>
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
    <div className="rv-run-detail">
      {loading ? (
        <div className="rv-run-detail__loading">
          <div className="spinner" />
          <span className="text-muted rv-run-detail__loading-text">Loading results…</span>
        </div>
      ) : error ? (
        <p className="rv-run-detail__error">{error}</p>
      ) : results.length === 0 ? (
        <p className="rv-run-detail__empty">
          No detection results recorded for this run.
        </p>
      ) : (
        <>
          {/* MTTD headline */}
          <MTTDSummary mttd={mttd} />

          {/* Report download */}
          <div className="rv-run-detail__report-row">
            <button
              className="btn btn-sm rv-run-detail__report-btn"
              onClick={handleDownloadReport}
            >
              &#8681; Download POV Report
            </button>
          </div>

          {/* Coverage summary */}
          <div className="rv-run-detail__coverage">
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
            <p className="section-label rv-run-detail__validate-label">
              Validate Detections — check each detection you confirmed in XSIAM ({coverage.observed || 0}/{coverage.total || 0})
            </p>
            <p className="rv-run-detail__validate-hint">
              Click the checkbox when you see the alert in XSIAM. MTTD is calculated automatically.
            </p>

            {Object.entries(byStep).map(([stepId, { step_name, results: stepResults }]) => (
              <div key={stepId} className="rv-run-detail__step-group">
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
        <div className="rv-header__actions">
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

      <div className="panel-card-body rv-body">
        {loading && runs.length === 0 ? (
          <div className="empty-state">
            <div className="spinner rv-loading-spinner" />
            <p>Loading run history…</p>
          </div>
        ) : runs.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon">&#9654;</div>
            <p>No runs yet. Launch a scenario to see results here.</p>
          </div>
        ) : (
          <div className="rv-table-wrap">
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
                    <React.Fragment key={runIdOf(run)}>
                      <tr
                        onClick={() => handleRowClick(run)}
                        className={`rv-row${isExpanded ? ' row-selected' : ''}`}
                        title="Click to validate detection results"
                      >
                        <td>
                          <div className="rv-row__scenario-id">
                            {run.scenario_id}
                          </div>
                          <div className="rv-row__run-id">
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
                        <td><span className="rv-row__time">{formatTime(run.started_at)}</span></td>
                        <td>
                          <span className="text-mono rv-row__time">
                            {formatDuration(run.started_at, run.completed_at)}
                          </span>
                        </td>
                      </tr>
                      {isExpanded && (
                        <tr>
                          <td colSpan={5} className="rv-row__detail-cell">
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
