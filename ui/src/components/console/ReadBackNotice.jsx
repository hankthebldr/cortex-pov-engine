/**
 * ReadBackNotice — what stands in for results until a run is reviewed
 * (design v26 §4.6, §7).
 *
 * A run that ended has executed steps, not detections. Until the tenant has
 * been read for it there is nothing to show, so Results and the run's
 * Evidence tab show this instead of a scorecard: where the run is, why there
 * are no results, and Review when it can be reviewed. If the last review
 * read nothing, its reason is stated here too.
 */
import React from 'react'
import { PHASE, NOTHING, NOT_EXECUTED_COPY } from './runLifecycle.js'
import { Button } from './kit/index.js'
import '../../styles/destinations/run-lifecycle.css'

void React

const COPY = {
  [PHASE.RUNNING]: 'Running — review it once it finishes',
  [PHASE.NOTHING]: 'Nothing reached the agent — check it',
  [PHASE.UNREAD]: 'Not reviewed — no results yet',
  [PHASE.READING]: 'Reviewing against the tenant…',
}

// The one detail a DC may want, kept off the line (Henry, 2026-11-15: one
// short line per state, detail in a tooltip).
const DETAIL = {
  [PHASE.UNREAD]: 'Review reads alerts from Cortex and changes nothing there.',
  [PHASE.READING]: 'Matching the tenant\'s alerts to what this run expected.',
}

export default function ReadBackNotice({ lifecycle, outcome = null, onReadBack = () => {} }) {
  if (!lifecycle || lifecycle.phase === PHASE.READY) return null
  const why = outcome && !outcome.ok && lifecycle.phase === PHASE.UNREAD ? outcome.message : null
  const text = lifecycle.nothing === NOTHING.NOT_EXECUTED ? NOT_EXECUTED_COPY.message : COPY[lifecycle.phase]
  return (
    <div className="readback-notice" data-testid="readback-notice" data-phase={lifecycle.phase}>
      <div className="readback-notice__text">
        <p title={DETAIL[lifecycle.phase]}>{text}</p>
        {why && <p className="readback-notice__why" role="status">{why}</p>}
      </div>
      {lifecycle.phase === PHASE.UNREAD && (
        <Button variant="primary" verb="Review" onClick={onReadBack} />
      )}
      {lifecycle.phase === PHASE.READING && (
        <Button variant="secondary" verb="Review" disabled>Reviewing…</Button>
      )}
    </div>
  )
}
