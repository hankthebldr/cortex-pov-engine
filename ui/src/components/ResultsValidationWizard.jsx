import React, { useState, useEffect, useCallback, useMemo } from 'react'
import {
  getResultsForRun,
  validateResult,
  updateResultNotes,
} from '../api/client.js'

/**
 * ResultsValidationWizard — guided walk-through for marking each expected
 * detection observed / not-observed in XSIAM.
 *
 * Given a run_id:
 *   1. Fetches /api/results/:run_id → grouped results per step.
 *   2. For each row, surfaces a copy-paste XQL search string scoped to
 *      the plane + technique + simulation_run_id.
 *   3. One-click "mark observed" (sets observed_at; computes MTTD).
 *   4. Free-text notes column (saved on blur).
 *   5. Bulk "mark all observed" for the happy path.
 *
 * v1 XQL templates are static per plane. Phase 11 (custom-rule import)
 * extends this to use the customer's actual rule IDs.
 */
export default function ResultsValidationWizard({ runId, onClose, onMessage }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [busyIds, setBusyIds] = useState(new Set())
  const [filter, setFilter] = useState('all')   // 'all' | 'pending' | 'observed'

  const refresh = useCallback(async () => {
    if (!runId) return
    setLoading(true)
    setError(null)
    try {
      const resp = await getResultsForRun(runId)
      setData(resp)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [runId])

  useEffect(() => { refresh() }, [refresh])

  const flagBusy = useCallback((id, on) => {
    setBusyIds(prev => {
      const next = new Set(prev)
      if (on) next.add(id); else next.delete(id)
      return next
    })
  }, [])

  // ── Mark observed / not-observed ──────────────────────────────────────
  const handleToggleObserved = useCallback(async (result) => {
    flagBusy(result.id, true)
    try {
      const next = !result.observed
      await validateResult(result.id, next)
      onMessage?.(
        next ? `Detection ${result.id} marked observed` : `Detection ${result.id} cleared`,
        'success',
      )
      await refresh()
    } catch (err) {
      onMessage?.(`Validation failed: ${err.message}`, 'error')
    } finally {
      flagBusy(result.id, false)
    }
  }, [flagBusy, onMessage, refresh])

  const handleBulkObserved = useCallback(async () => {
    if (!data?.results) return
    const pending = data.results.filter(r => !r.observed)
    if (pending.length === 0) return
    onMessage?.(`Marking ${pending.length} detection(s) observed…`, 'info')
    for (const r of pending) {
      try { await validateResult(r.id, true) }
      catch { /* keep going */ }
    }
    onMessage?.(`Bulk validation complete`, 'success')
    await refresh()
  }, [data, onMessage, refresh])

  // ── Notes update ──────────────────────────────────────────────────────
  const handleNotesBlur = useCallback(async (result, notesText) => {
    if ((result.notes || '') === notesText) return
    flagBusy(result.id, true)
    try {
      await updateResultNotes(result.id, notesText)
      onMessage?.('Notes saved', 'success')
      await refresh()
    } catch (err) {
      onMessage?.(`Notes save failed: ${err.message}`, 'error')
    } finally {
      flagBusy(result.id, false)
    }
  }, [flagBusy, onMessage, refresh])

  // ── Filtering ─────────────────────────────────────────────────────────
  const filtered = useMemo(() => {
    const rows = data?.results || []
    if (filter === 'pending') return rows.filter(r => !r.observed)
    if (filter === 'observed') return rows.filter(r => r.observed)
    return rows
  }, [data, filter])

  // ── Group by step for visual hierarchy ────────────────────────────────
  const byStep = useMemo(() => {
    const out = new Map()
    for (const r of filtered) {
      const k = r.step_id || '–'
      if (!out.has(k)) out.set(k, { step_id: k, step_name: r.step_name, rows: [] })
      out.get(k).rows.push(r)
    }
    return Array.from(out.values())
  }, [filtered])

  if (loading) return <div className="results-wizard"><p className="muted">Loading…</p></div>
  if (error) {
    return (
      <div className="results-wizard">
        <p className="error">Failed to load results: {error}</p>
        {onClose && <button className="btn btn-sm btn-secondary" onClick={onClose}>Close</button>}
      </div>
    )
  }

  const coverage = data?.coverage
  const mttd = data?.mttd
  const total = data?.results?.length || 0
  const observedCount = data?.results?.filter(r => r.observed).length || 0

  return (
    <section className="results-wizard">
      <header className="results-wizard__head">
        <h2 style={{ margin: 0, fontSize: '18px' }}>
          Validation Wizard
          <span className="muted small" style={{ marginLeft: 8 }}>
            run {runId?.slice?.(0, 12) || runId}…
          </span>
        </h2>
        {onClose && (
          <button className="btn btn-sm btn-secondary" onClick={onClose}>Close</button>
        )}
      </header>

      <div className="results-wizard__summary">
        <div className="kpi">
          <span className="kpi__label">Observed</span>
          <span className="kpi__value">{observedCount}<span className="muted">/{total}</span></span>
        </div>
        <div className="kpi">
          <span className="kpi__label">Coverage</span>
          <span className="kpi__value">
            {total > 0 ? `${Math.round((observedCount / total) * 100)}%` : '–'}
          </span>
        </div>
        {mttd?.median_seconds != null && (
          <div className="kpi">
            <span className="kpi__label">MTTD (median)</span>
            <span className="kpi__value">{Math.round(mttd.median_seconds)}<span className="muted">s</span></span>
          </div>
        )}
        <div className="flex-row" style={{ gap: '6px', marginLeft: 'auto' }}>
          <select
            className="input"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            style={{ width: 'auto' }}
          >
            <option value="all">All</option>
            <option value="pending">Pending only</option>
            <option value="observed">Observed only</option>
          </select>
          <button
            className="btn btn-sm btn-navy"
            onClick={handleBulkObserved}
            disabled={total === 0 || observedCount === total}
            title="Mark every pending detection observed"
          >
            ✓ Mark all observed
          </button>
        </div>
      </div>

      {byStep.length === 0 && (
        <div className="empty-state">
          <p>No expected detections in this run.</p>
        </div>
      )}

      {byStep.map(group => (
        <div key={group.step_id} className="results-wizard__step">
          <h3 className="results-wizard__step-head">
            <span className="mono">{group.step_id}</span>
            {group.step_name && <span className="muted small"> — {group.step_name}</span>}
          </h3>
          <table className="cs-table">
            <thead>
              <tr>
                <th style={{ width: '70px' }}>Plane</th>
                <th style={{ width: '70px' }}>Type</th>
                <th>Expected detection</th>
                <th style={{ width: '300px' }}>XQL template</th>
                <th style={{ width: '90px' }}>MTTD</th>
                <th style={{ width: '180px' }}>Notes</th>
                <th style={{ width: '120px' }}>Status</th>
              </tr>
            </thead>
            <tbody>
              {group.rows.map(r => (
                <ResultRow
                  key={r.id}
                  result={r}
                  busy={busyIds.has(r.id)}
                  onToggle={() => handleToggleObserved(r)}
                  onNotesBlur={(text) => handleNotesBlur(r, text)}
                />
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </section>
  )
}

// ─── Result row ──────────────────────────────────────────────────────────────

function ResultRow({ result, busy, onToggle, onNotesBlur }) {
  const [notes, setNotes] = useState(result.notes || '')

  useEffect(() => {
    // Sync if backend update changed notes (e.g. another tab).
    setNotes(result.notes || '')
  }, [result.notes])

  const xql = xqlTemplate(result)

  return (
    <tr className={result.observed ? 'row-observed' : ''}>
      <td><code className="pill pill-info mono small">{result.plane}</code></td>
      <td><code className="mono small">{result.signal_type}</code></td>
      <td className="small">{result.expected_detection}</td>
      <td>
        <CopyableCode value={xql} />
      </td>
      <td className="mono small right">
        {result.mttd_seconds != null ? `${Math.round(result.mttd_seconds)}s` : '–'}
      </td>
      <td>
        <input
          className="input small"
          value={notes}
          placeholder="…"
          onChange={(e) => setNotes(e.target.value)}
          onBlur={(e) => onNotesBlur(e.target.value)}
        />
      </td>
      <td>
        <button
          className={`btn btn-sm ${result.observed ? 'btn-navy' : 'btn-secondary'}`}
          disabled={busy}
          onClick={onToggle}
          style={{ width: '100%' }}
        >
          {busy ? '…' : result.observed ? '✓ Observed' : 'Mark observed'}
        </button>
      </td>
    </tr>
  )
}

// ─── XQL templates per plane ─────────────────────────────────────────────────
//
// First-pass templates the DC can copy into the XSIAM XQL editor. Static
// per plane today; Phase 11 (custom-rule import) replaces these with the
// customer's actual rule IDs.

function xqlTemplate(result) {
  const plane = (result.plane || '').toUpperCase()
  const tech = result.mitre_technique || result.expected_detection || ''
  const planeMap = {
    NDR: `dataset = panw_ngfw_traffic_raw\n| filter "x-simulation-campaign-id" matches "CMP-.*"\n| comp count() by source_ip, destination_ip`,
    EDR: `dataset = xdr_data\n| filter event_type = "PROCESS"\n| filter mitre_technique = "${tech}"\n| comp count() by host, parent_image, image`,
    CDR: `dataset = cloud_audit_logs\n| filter event_type contains "container"\n| filter mitre_technique = "${tech}"`,
    AIRS: `dataset = airs\n| filter probe_classname matches ".*"\n| filter cortexsim.run_id != null\n| comp count() by probe_classname, outcome`,
    AI_ACCESS: `dataset = ai_access_logs\n| filter provider in ("openai", "anthropic", "gemini")\n| filter "x-simulation-campaign-id" matches "CMP-.*"`,
    BROWSER: `dataset = prisma_browser_dlp\n| filter cortex_canary matches "CANARY-.*"\n| filter outcome in ("blocked", "alerted")`,
    KOI: `dataset = panw_ngfw_traffic_raw\n| filter url matches ".*cortexsim-canary.*"`,
    ANALYTICS: `dataset = xdr_alerts\n| filter alert_source = "stitched"\n| filter cortexsim.campaign_id != null`,
  }
  return planeMap[plane] || `// XQL template for plane "${plane}" not yet defined`
}

// ─── Tiny copy-to-clipboard helper ───────────────────────────────────────────

function CopyableCode({ value }) {
  const [copied, setCopied] = useState(false)
  const onCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {/* clipboard unavailable */}
  }, [value])
  return (
    <div className="xql-block">
      <pre className="mono small">{value}</pre>
      <button className="btn btn-sm btn-secondary xql-block__copy" onClick={onCopy}>
        {copied ? '✓ copied' : 'copy'}
      </button>
    </div>
  )
}
