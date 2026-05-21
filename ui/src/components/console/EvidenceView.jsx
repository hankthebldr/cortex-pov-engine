import React, { useCallback, useEffect, useMemo, useState } from 'react'
import useResultsData from './useResultsData.js'
import DetectionDrawer from './DetectionDrawer.jsx'
import { downloadReport } from '../../api/client.js'

/**
 * EvidenceView — the Evidence tab.
 *
 * Layout:
 *   ▸ view head with run id + timestamp + Validate-all + Export CTAs
 *   ▸ KPI row: Coverage % · Median MTTD · Stitch count · Pending count
 *   ▸ Scorecard table with per-row validate (Detected / Missed / Reset)
 *
 * Props:
 *   activeRun  — { runId, scenarioId, ... }
 *   lastRun    — fallback run when no live run
 *   onError    — (msg) => void
 */
export default function EvidenceView({ activeRun, lastRun, onError = () => {} }) {
  const targetRunId = activeRun?.runId || lastRun?.runId || null
  const targetScenarioId = activeRun?.scenarioId || lastRun?.scenarioId || null

  const { rows, kpis, loading, validate, refresh } = useResultsData(targetRunId)
  const [exporting, setExporting] = useState(false)
  const [selectedRowId, setSelectedRowId] = useState(null)

  // Reset drilldown selection when the underlying run changes.
  useEffect(() => { setSelectedRowId(null) }, [targetRunId])

  const selectedRow = useMemo(
    () => (selectedRowId == null ? null : rows.find((r) => r.id === selectedRowId) || null),
    [rows, selectedRowId],
  )

  const handleExport = useCallback(async () => {
    if (!targetRunId) {
      onError('No run selected for export')
      return
    }
    setExporting(true)
    try {
      const blob = await downloadReport(targetRunId)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `cortexsim-pov-${targetRunId}.md`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch (err) {
      onError(err.message || 'Export failed')
    } finally {
      setExporting(false)
    }
  }, [targetRunId, onError])

  const handleValidateAll = useCallback(async () => {
    // Mark every pending row as detected (DC sweeps after a successful run).
    const pending = rows.filter((r) => r.observed == null && r.id != null)
    for (const r of pending) {
      await validate(r.id, true, null)
    }
    refresh()
  }, [rows, validate, refresh])

  if (!targetRunId) {
    return (
      <div className="evidence" style={{ paddingTop: 80 }}>
        <h1 style={{
          fontFamily: 'var(--font-display)',
          fontSize: 28, fontWeight: 400, color: 'var(--c-text)', marginBottom: 12,
        }}>No run to validate</h1>
        <p style={{
          fontFamily: 'var(--font-narrative)',
          fontSize: 15, fontWeight: 300,
          color: 'var(--c-text-secondary)', lineHeight: 1.6, maxWidth: 640,
        }}>
          Launch a scenario from the Operations tab. The detection scorecard
          and POV report export will appear here once results begin to arrive.
        </p>
      </div>
    )
  }

  return (
    <div className="evidence">
      <div className="view-head">
        <div>
          <h1>Evidence</h1>
          <div className="view-head__meta">
            Run <strong className="mono">{targetRunId}</strong>
            {targetScenarioId && (
              <> · <span className="mono">{targetScenarioId}</span></>
            )}
            {loading && <> · <span className="mono">syncing…</span></>}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            className="btn"
            onClick={handleValidateAll}
            disabled={kpis.pending === 0}
            title="Mark all pending detections as observed"
          >
            Validate all
            {kpis.pending > 0 && (
              <span className="kbd">{kpis.pending}</span>
            )}
          </button>
          <button
            className="btn btn--primary"
            onClick={handleExport}
            disabled={exporting}
          >
            {exporting ? 'Exporting…' : 'Export POV report'}
          </button>
        </div>
      </div>

      <KpiRow kpis={kpis} />

      <Scorecard
        rows={rows}
        loading={loading}
        selectedRowId={selectedRowId}
        onSelectRow={setSelectedRowId}
        onValidate={validate}
      />

      <DetectionDrawer
        row={selectedRow}
        open={!!selectedRow}
        onClose={() => setSelectedRowId(null)}
        onValidate={(id, observed, notes) => {
          validate(id, observed, notes)
        }}
      />
    </div>
  )
}

/* ─── KPI row ─────────────────────────────────────────────────────────── */

function KpiRow({ kpis }) {
  return (
    <div className="kpi-row">
      <Kpi
        label="Coverage"
        value={kpis.coverage}
        suffix="%"
        valueClass="kpi__value--detected"
        meta={`${kpis.detected} / ${kpis.total} detections confirmed`}
      />
      <Kpi
        label="MTTD · median"
        value={kpis.median != null ? kpis.median : '—'}
        suffix={kpis.median != null ? 's' : ''}
        valueClass="kpi__value--signal"
        meta={kpis.median != null ? 'across confirmed detections' : 'no detections yet'}
      />
      <Kpi
        label="XSIAM Stitch"
        value={kpis.stitched}
        suffix={` / ${kpis.total > 0 ? kpis.total : 0}`}
        meta="analytics-plane detections observed"
      />
      <Kpi
        label="Pending"
        value={kpis.pending}
        valueClass="kpi__value--pending"
        meta="awaiting validation"
      />
    </div>
  )
}

function Kpi({ label, value, suffix = '', meta = '', valueClass = '' }) {
  return (
    <div className="kpi">
      <div className="kpi__label">{label}</div>
      <div className={'kpi__value ' + valueClass}>
        {value}
        {suffix && (
          <span style={{ fontSize: 22, color: 'var(--c-text-muted)' }}>{suffix}</span>
        )}
      </div>
      {meta && <div className="kpi__meta">{meta}</div>}
    </div>
  )
}

/* ─── Scorecard ────────────────────────────────────────────────────────── */

function Scorecard({ rows, loading, selectedRowId, onSelectRow, onValidate }) {
  if (rows.length === 0 && !loading) {
    return (
      <div className="scorecard">
        <div className="scorecard__head">
          <div>TID</div>
          <div>Plane</div>
          <div>Alert</div>
          <div style={{ textAlign: 'right' }}>MTTD</div>
          <div>Alert ID</div>
          <div>Status</div>
        </div>
        <div style={{
          padding: 32,
          textAlign: 'center',
          color: 'var(--c-text-muted)',
          fontFamily: 'var(--font-mono)',
          fontSize: 11,
          letterSpacing: '0.04em',
        }}>
          no results yet — Cortex Data Lake ingestion typically takes 30–120s
        </div>
      </div>
    )
  }

  return (
    <div className="scorecard">
      <div className="scorecard__head">
        <div>TID</div>
        <div>Plane</div>
        <div>Alert</div>
        <div style={{ textAlign: 'right' }}>MTTD</div>
        <div>Alert ID</div>
        <div>Status</div>
      </div>
      {rows.map((r, i) => (
        <ScorecardRow
          key={r.id ?? i}
          row={r}
          isSelected={r.id != null && r.id === selectedRowId}
          onSelect={() => r.id != null && onSelectRow(r.id)}
          onValidate={onValidate}
        />
      ))}
    </div>
  )
}

function ScorecardRow({ row, isSelected, onSelect, onValidate }) {
  const status = row.observed === true
    ? 'detected'
    : row.observed === false
    ? 'missed'
    : 'pending'
  const statusLabel = status === 'detected' ? 'Detected' : status === 'missed' ? 'Missed' : 'Pending'

  // Inline-button clicks shouldn't propagate to the row click (avoid
  // double-firing validate + selecting the row at the same time).
  const stop = (fn) => (e) => { e.stopPropagation(); fn() }

  return (
    <div
      className={'scorecard__row' + (isSelected ? ' scorecard__row--selected' : '')}
      data-status={status}
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect() } }}
      aria-label={`Open detection detail for ${row.tid}`}
    >
      <div className="scorecard__tid">{row.tid}</div>
      <div className="scorecard__plane">
        {row.plane}{row.detectionType && ` · ${row.detectionType}`}
      </div>
      <div className="scorecard__alert">{row.alert}</div>
      <div className="scorecard__mttd">
        {row.mttd != null ? formatMttd(row.mttd) : '—'}
      </div>
      <div className="scorecard__id">{row.alertId || '—'}</div>
      <div className="scorecard__status-cell">
        <span className={`scorecard__status scorecard__status--${status}`}>
          {statusLabel}
        </span>
        {row.id != null && (
          <div className="scorecard__row-actions">
            {row.observed !== true && (
              <button
                className="row-action row-action--detected"
                onClick={stop(() => onValidate(row.id, true, null))}
                title="Mark as detected"
              >✓</button>
            )}
            {row.observed !== false && (
              <button
                className="row-action row-action--missed"
                onClick={stop(() => onValidate(row.id, false, null))}
                title="Mark as missed"
              >✗</button>
            )}
            {row.observed != null && (
              <button
                className="row-action"
                onClick={stop(() => onValidate(row.id, null, null))}
                title="Reset to pending"
              >○</button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function formatMttd(seconds) {
  if (seconds == null) return '—'
  if (seconds < 60) return `0:${String(Math.floor(seconds)).padStart(2, '0')}`
  const mm = Math.floor(seconds / 60)
  const ss = Math.floor(seconds % 60)
  return `${mm}:${String(ss).padStart(2, '0')}`
}
