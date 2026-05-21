import React, { useState, useEffect } from 'react'

/**
 * DetectionDrawer — operator drill-down for a single scorecard row.
 *
 * Slides in from the right when a DC clicks a scorecard row in the
 * Evidence tab. Surfaces the full detection record so the DC can:
 *
 *   - Read the expected description vs alert title + observation timestamps
 *   - Inspect the raw alert id (with a copy-to-clipboard affordance)
 *   - Edit operator notes (validation context for the POV report)
 *   - Validate / un-validate with notes attached in one motion
 *   - See an MTTD breakdown (executed_at → observed_at)
 *
 * Props:
 *   row        — the scorecard row object (see useResultsData)
 *   open       — boolean
 *   onClose    — () => void
 *   onValidate — (resultId, observed: boolean|null, notes: string) => void
 */
export default function DetectionDrawer({
  row,
  open,
  onClose = () => {},
  onValidate = () => {},
}) {
  const [notes, setNotes] = useState('')
  const [copied, setCopied] = useState(false)

  // Reset notes when the row changes — no leaking state across rows.
  useEffect(() => {
    setNotes(row?.notes || '')
    setCopied(false)
  }, [row?.id])

  if (!row) return <aside className="detection-drawer" />

  const status = row.observed === true
    ? 'detected'
    : row.observed === false
    ? 'missed'
    : 'pending'

  const statusLabel = status === 'detected' ? 'Detected'
    : status === 'missed' ? 'Missed'
    : 'Pending'

  const handleCopyAlertId = () => {
    if (!row.alertId) return
    try {
      navigator.clipboard.writeText(row.alertId)
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    } catch {
      /* clipboard blocked — silently skip */
    }
  }

  const handleSave = (observed) => {
    if (row.id == null) return
    onValidate(row.id, observed, notes || null)
  }

  const fmtMttd = (s) => {
    if (s == null) return '—'
    const mm = Math.floor(s / 60)
    const ss = Math.floor(s % 60).toString().padStart(2, '0')
    return mm > 0 ? `${mm}m ${ss}s` : `${ss}s`
  }

  return (
    <aside
      className={'detection-drawer' + (open ? ' detection-drawer--open' : '')}
      role="complementary"
      aria-label="Detection detail"
    >
      <div className="detection-drawer__head">
        <div>
          <div className="detection-drawer__eyebrow mono">{row.tid}</div>
          <div className="detection-drawer__title">
            {row.alert || 'Detection detail'}
          </div>
        </div>
        <button
          type="button"
          className="btn"
          onClick={onClose}
          aria-label="Close detection detail"
        >
          <span>Close</span>
          <span className="kbd">esc</span>
        </button>
      </div>

      <div className="detection-drawer__status-row">
        <span className={`scorecard__status scorecard__status--${status}`}>
          {statusLabel}
        </span>
        <span className="mono detection-drawer__plane">
          {row.plane}{row.detectionType ? ` · ${row.detectionType}` : ''}
        </span>
      </div>

      <div className="detection-drawer__section">
        <div className="detection-drawer__section-title">Timing</div>
        <dl className="detection-drawer__kv">
          <dt>Executed at</dt>
          <dd className="mono">{row.executedAt || '—'}</dd>
          <dt>Observed at</dt>
          <dd className="mono">{row.observedAt || '—'}</dd>
          <dt>MTTD</dt>
          <dd className="mono detection-drawer__mttd">
            {fmtMttd(row.mttd)}
          </dd>
        </dl>
      </div>

      <div className="detection-drawer__section">
        <div className="detection-drawer__section-title">Alert reference</div>
        <div className="detection-drawer__alert-id">
          <span className="mono">{row.alertId || '—'}</span>
          {row.alertId && (
            <button
              type="button"
              className="detection-drawer__copy"
              onClick={handleCopyAlertId}
              aria-label="Copy alert ID to clipboard"
              title="Copy alert ID to clipboard"
            >
              {copied ? '✓ copied' : 'copy'}
            </button>
          )}
        </div>
        {row.alertId && (
          <div className="detection-drawer__hint mono">
            Paste this ID into the Cortex console search to jump to the
            originating alert + causality graph.
          </div>
        )}
      </div>

      <div className="detection-drawer__section detection-drawer__section--notes">
        <label htmlFor="detection-notes" className="detection-drawer__section-title">
          Operator notes
        </label>
        <textarea
          id="detection-notes"
          className="detection-drawer__notes"
          placeholder="What did you observe? Edge cases, false-positive risk, follow-up actions for the customer brief…"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={6}
        />
        <div className="detection-drawer__hint mono">
          Notes are saved with the validation status and appear in the POV
          report under the matching scorecard row.
        </div>
      </div>

      <div className="detection-drawer__actions">
        <button
          type="button"
          className={'btn' + (status === 'detected' ? ' btn--primary' : '')}
          onClick={() => handleSave(true)}
          disabled={row.id == null}
        >
          ✓ Mark detected
        </button>
        <button
          type="button"
          className="btn btn--danger"
          onClick={() => handleSave(false)}
          disabled={row.id == null}
        >
          ✗ Mark missed
        </button>
        {row.observed != null && (
          <button
            type="button"
            className="btn"
            onClick={() => handleSave(null)}
            disabled={row.id == null}
            title="Reset to pending"
          >
            ○ Reset
          </button>
        )}
      </div>
    </aside>
  )
}
