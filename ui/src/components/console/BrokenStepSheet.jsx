/**
 * BrokenStepSheet — what a Broken step on Run detail opens (design v26 §4.6):
 * the likely causes in the order to check, each line opening the place that
 * fixes it. The causes come from the host's record of the step
 * (brokenStep.brokenStepCauses); the record itself is shown above them, so the
 * DC sees what the list was read from.
 *
 * A modal dialog: focus moves to the first cause, Tab stays inside, Esc and
 * the backdrop close it, and focus returns to the card that opened it.
 */
import React, { useEffect, useMemo, useRef } from 'react'
import '../../styles/broken-step-sheet.css'
import StatusIcon from './kit/StatusIcon.jsx'
import { brokenStepCauses } from './brokenStep.js'

void React

const FOCUSABLE = 'button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'

function recordItems(row) {
  const items = []
  if (row.rc != null) items.push(['exit code', String(row.rc)])
  if (row.termination) items.push(['ended', row.termination.replace(/_/g, ' ')])
  if (row.signal) items.push(['signal', String(row.signal)])
  if (row.status === 'severed') items.push(['agent', 'stopped reporting'])
  return items
}

export default function BrokenStepSheet({
  open,
  step = null,
  row = null,
  runId,
  scenarioId = null,
  onClose = () => {},
  onNavigate = () => {},
}) {
  const dialogRef = useRef(null)
  const openerRef = useRef(null)
  const causes = useMemo(
    () => (row ? brokenStepCauses(row, { runId, scenarioId }) : []),
    [row, runId, scenarioId],
  )

  useEffect(() => {
    if (!open) return undefined
    openerRef.current = document.activeElement
    const first = dialogRef.current?.querySelector('.broken-sheet__cause')
    if (first) first.focus()
    return () => {
      const opener = openerRef.current
      if (opener && opener.isConnected && opener.focus) opener.focus()
    }
  }, [open])

  useEffect(() => {
    if (!open) return undefined
    const onKey = (e) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
        return
      }
      if (e.key !== 'Tab' || !dialogRef.current) return
      const items = [...dialogRef.current.querySelectorAll(FOCUSABLE)]
      if (!items.length) return
      const first = items[0]
      const last = items[items.length - 1]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open || !row) return null
  const stepId = step?.id || row.id
  const record = recordItems(row)

  return (
    <div
      className="broken-sheet__backdrop"
      data-testid="broken-step-sheet"
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        ref={dialogRef}
        className="broken-sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby="broken-sheet-title"
        aria-describedby="broken-sheet-lede"
      >
        <header className="broken-sheet__head">
          <StatusIcon status="down" word="Broken" />
          <div className="broken-sheet__id">
            <h2 id="broken-sheet-title" className="broken-sheet__title">
              <span className="mono">{stepId}</span>
              {step?.name ? <> · {step.name}</> : null}
            </h2>
            <p id="broken-sheet-lede" className="broken-sheet__lede">
              Likely causes, in the order to check. Each opens the place that fixes it.
            </p>
          </div>
          <button type="button" className="broken-sheet__close" aria-label="Close" onClick={onClose}>✕</button>
        </header>

        {record.length > 0 && (
          <dl className="broken-sheet__record" aria-label="What the host recorded">
            {record.map(([k, v]) => (
              <div key={k} className="broken-sheet__fact">
                <dt>{k}</dt>
                <dd className="mono">{v}</dd>
              </div>
            ))}
          </dl>
        )}

        <ol className="broken-sheet__causes">
          {causes.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                className="broken-sheet__cause"
                data-cause={c.id}
                onClick={() => {
                  onClose()
                  onNavigate(c.place.to.destination, c.place.to.params)
                }}
              >
                <span className="broken-sheet__cause-title">{c.title}</span>
                <span className="broken-sheet__cause-detail">{c.detail}</span>
                <span className="broken-sheet__cause-place">{c.place.label} ›</span>
              </button>
            </li>
          ))}
        </ol>
      </div>
    </div>
  )
}
