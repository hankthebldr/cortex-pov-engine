import React, { useEffect, useRef, useState } from 'react'
import {
  downloadReport,
  downloadReportMatrix,
  downloadReportNavigator,
  downloadReportBundle,
  downloadReportScorecard,
} from '../../api/client.js'
import { Button } from './kit/index.js'

/**
 * ExportMenu — the report downloads of one run, on Results.
 *
 * One secondary kit Button, Download, opens the list: the full POV bundle
 * first — the artifact a DC walks out of a meeting with (markdown narrative,
 * scoring matrix, ATT&CK Navigator layer and a manifest in one tar.gz) — then
 * each artifact on its own, for when the customer asked only for the Navigator
 * layer, say.
 *
 * Props:
 *   runId     — current run id (required to download)
 *   onError   — (msg) => void surfaced to the toast strip
 *   disabled  — disables the whole menu (used while another export is in flight)
 */
export default function ExportMenu({ runId, onError = () => {}, disabled = false }) {
  const [open, setOpen]   = useState(false)
  const [busy, setBusy]   = useState(null) // 'bundle' | 'markdown' | …
  const wrapRef = useRef(null)

  // Close on outside click + escape.
  useEffect(() => {
    if (!open) return undefined
    const onDocClick = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false)
    }
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDocClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDocClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const doExport = async (kind, fetcher, ext, namePrefix = 'pov') => {
    if (!runId) { onError('No run selected for export'); return }
    setBusy(kind)
    try {
      const blob = await fetcher(runId)
      const url  = URL.createObjectURL(blob)
      const a    = document.createElement('a')
      a.href     = url
      a.download = `cortexsim-${namePrefix}-${runId}.${ext}`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch (err) {
      onError(err.message || `${kind} export failed`)
    } finally {
      setBusy(null)
      setOpen(false)
    }
  }

  const busyLabel = (kind, fallback) => (busy === kind ? 'Downloading…' : fallback)

  return (
    <div className="export-menu" ref={wrapRef}>
      <Button
        verb="Download"
        className="export-menu__trigger"
        onClick={() => setOpen((v) => !v)}
        disabled={disabled || busy != null || !runId}
        aria-haspopup="menu"
        aria-expanded={open}
        title="Download this run's report"
      >
        {busy != null ? 'Downloading…' : undefined}
      </Button>

      {open && (
        <div className="export-menu__dropdown" role="menu">
          <div className="export-menu__section-label">Briefing</div>

          <MenuItem
            label={busyLabel('bundle', 'POV briefing bundle (all)')}
            sub=".tar.gz · narrative + matrix + Navigator + manifest"
            onClick={() => doExport('bundle', downloadReportBundle, 'tar.gz')}
            disabled={busy != null}
            primary
          />
          <MenuItem
            label={busyLabel('markdown', 'Narrative report')}
            sub=".md · Cortex-branded markdown POV summary"
            onClick={() => doExport('markdown', downloadReport, 'md')}
            disabled={busy != null}
          />
          <MenuItem
            label={busyLabel('matrix', 'Detection scoring matrix')}
            sub=".csv · per-detection scoring spreadsheet"
            onClick={() => doExport('matrix', downloadReportMatrix, 'csv')}
            disabled={busy != null}
          />
          <MenuItem
            label={busyLabel('navigator', 'ATT&CK Navigator layer')}
            sub=".json · v4.5 layer for attack-navigator.mitre.org"
            onClick={() => doExport('navigator', downloadReportNavigator, 'json')}
            disabled={busy != null}
          />

          <div className="export-menu__section-label">Executive scorecard</div>

          <MenuItem
            label={busyLabel('scorecard-md', 'Executive scorecard')}
            sub=".md · CISO efficacy one-pager (markdown)"
            onClick={() => doExport('scorecard-md', (id) => downloadReportScorecard(id), 'md', 'scorecard')}
            disabled={busy != null}
          />
          <MenuItem
            label={busyLabel('scorecard-html', 'Executive scorecard')}
            sub=".html · CISO efficacy one-pager (styled)"
            onClick={() => doExport('scorecard-html', (id) => downloadReportScorecard(id, { html: true }), 'html', 'scorecard')}
            disabled={busy != null}
          />
        </div>
      )}
    </div>
  )
}

function MenuItem({ label, sub, onClick, disabled, primary = false }) {
  return (
    <button
      type="button"
      role="menuitem"
      className={'export-menu__item' + (primary ? ' export-menu__item--primary' : '')}
      onClick={onClick}
      disabled={disabled}
    >
      <div className="export-menu__item-label">{label}</div>
      <div className="export-menu__item-sub">{sub}</div>
    </button>
  )
}
