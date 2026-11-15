import React, { useState, useEffect } from 'react'
import DetectionTypeChip, { detectionTypeToken } from './DetectionTypeChip.jsx'
import { updateResultNotes } from '../../api/client.js'
import { isAttestationRefused } from '../../outcomes.js'
import { detectionState } from './detectionState.js'
import { tidySeconds } from './MttdHistogram.jsx'
import { Button, Icon, StatusIcon } from './kit/index.js'

/**
 * DetectionDrawer — one expected detection, opened from its scorecard row.
 *
 * Slides in from the right on Results › Detections. It carries the whole
 * record so a DC can:
 *
 *   - read the state (a StatusIcon and one word), the timing and the MTTD
 *   - copy the alert id
 *   - keep a note for the POV report
 *   - mark the detection by hand (Mark detected · Mark missed · Reset)
 *
 * Props:
 *   row        — the scorecard row object (see useResultsData)
 *   open       — boolean
 *   reviewed   — the run has been reviewed (default true)
 *   onClose    — () => void
 *   onValidate — (resultId, observed: boolean|null, notes: string) => void
 */
export default function DetectionDrawer({
  row,
  open,
  reviewed = true,
  onClose = () => {},
  onValidate = () => {},
}) {
  const [notes, setNotes] = useState('')
  const [copied, setCopied] = useState(false)
  const [noteState, setNoteState] = useState(null) // null | 'saving' | 'saved' | error text

  // Reset notes when the row changes — no leaking state across rows.
  useEffect(() => {
    setNotes(row?.notes || '')
    setCopied(false)
    setNoteState(null)
  }, [row?.id])

  if (!row) return <aside className="detection-drawer" />

  // Outcome first (outcomes.js::resultStatus): a not_reached row is never Missed.
  const state = detectionState(row, { reviewed })

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

  // A note is saved on its own (PUT /notes), so it works on every row --
  // including the ones whose attestation controls are hidden (round 4).
  const handleSaveNote = async () => {
    if (row.id == null) return
    setNoteState('saving')
    try {
      await updateResultNotes(row.id, notes)
      setNoteState('saved')
    } catch (err) {
      setNoteState(err?.message || String(err))
    }
  }

  const handleSave = (observed) => {
    if (row.id == null) return
    onValidate(row.id, observed, notes || null)
  }

  const fmtMttd = (s) => {
    if (s == null) return '—'
    if (s < 60) return `${tidySeconds(s)}s`
    const mm = Math.floor(s / 60)
    const ss = Math.floor(s % 60).toString().padStart(2, '0')
    return `${mm}m ${ss}s`
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
          className="detection-drawer__close"
          onClick={onClose}
          aria-label="Close detection detail"
          title="Close"
        >
          <Icon name="x" size={12} />
        </button>
      </div>

      <div className="detection-drawer__status-row">
        <span className="detection-drawer__state" title={state.title}>
          <StatusIcon status={state.status} word={state.word} />
        </span>
        <span className="mono detection-drawer__plane">
          <span>{row.plane}</span>
          {row.detectionType && (
            detectionTypeToken(row.detectionType)
              ? <DetectionTypeChip type={row.detectionType} />
              : <span>{` · ${row.detectionType}`}</span>
          )}
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
        <div className="detection-drawer__section-title">Alert ID</div>
        <div className="detection-drawer__alert-id">
          <span className="mono" title={row.alertId ? 'Search this ID in Cortex to open the alert' : undefined}>
            {row.alertId || '—'}
          </span>
          {row.alertId && (
            <Button verb="Copy" onClick={handleCopyAlertId} aria-label="Copy alert ID">
              {copied ? 'Copied' : 'Copy'}
            </Button>
          )}
        </div>
      </div>

      <div className="detection-drawer__section detection-drawer__section--notes">
        <label
          htmlFor="detection-notes"
          className="detection-drawer__section-title"
          title="Shown in the POV report, under this detection"
        >
          Notes
        </label>
        <textarea
          id="detection-notes"
          className="detection-drawer__notes"
          placeholder="What you observed"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={6}
        />
        <div className="detection-drawer__notes-actions">
          <Button verb="Save" onClick={handleSaveNote} disabled={row.id == null || noteState === 'saving'}>
            Save note
          </Button>
          {noteState && noteState !== 'saving' && (
            <span
              className="detection-drawer__note-state"
              role="status"
              title={noteState === 'saved' ? undefined : noteState}
            >
              {noteState === 'saved' ? 'Saved' : 'Not saved'}
            </span>
          )}
        </div>
      </div>

      {isAttestationRefused(row) ? (
        <div
          className="detection-drawer__hint"
          data-testid="drawer-attestation-refused"
          title="The step never ran, or it is a prevention. A hand mark cannot stand in for the alert; review against the tenant."
        >
          Cannot be marked by hand
        </div>
      ) : (
      <div className="detection-drawer__actions">
        <Button verb="Mark detected" onClick={() => handleSave(true)} disabled={row.id == null} />
        <Button verb="Mark missed" onClick={() => handleSave(false)} disabled={row.id == null} />
        {row.observed != null && (
          <Button verb="Reset" onClick={() => handleSave(null)} disabled={row.id == null} title="Back to unjudged" />
        )}
      </div>
      )}
    </aside>
  )
}
