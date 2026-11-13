/**
 * ReadBackNotice — what stands in for results until a run is read back
 * (design v26 §4.6, §7).
 *
 * A run that ended has executed steps, not detections. Until the tenant has
 * been read for it there is nothing to show, so Results and the run's
 * Evidence tab show this instead of a scorecard: where the run is, why there
 * are no results, and Read back when it can be read back. If the last read
 * back read nothing, its reason is stated here too.
 */
import React from 'react'
import { PHASE } from './runLifecycle.js'
import { Button } from './kit/index.js'
import '../../styles/destinations/run-lifecycle.css'

void React

const COPY = {
  [PHASE.RUNNING]: 'This run is still running. It can be read back from the tenant once it finishes.',
  [PHASE.NOTHING]: 'No step of this run reached its agent, so there is nothing to read back. Check the agent and run it again.',
  [PHASE.UNREAD]: 'This run has not been read back from the tenant, so it has no results yet. Reading back is read-only: it reads alerts from Cortex and changes nothing there.',
  [PHASE.READING]: 'Reading alerts back from the tenant and matching them to what this run expected…',
}

export default function ReadBackNotice({ lifecycle, outcome = null, onReadBack = () => {} }) {
  if (!lifecycle || lifecycle.phase === PHASE.READY) return null
  const why = outcome && !outcome.ok && lifecycle.phase === PHASE.UNREAD ? outcome.message : null
  return (
    <div className="readback-notice" data-testid="readback-notice" data-phase={lifecycle.phase}>
      <div className="readback-notice__text">
        <p>{COPY[lifecycle.phase]}</p>
        {why && <p className="readback-notice__why" role="status">{why}</p>}
      </div>
      {lifecycle.phase === PHASE.UNREAD && (
        <Button variant="primary" onClick={onReadBack}>Read back</Button>
      )}
      {lifecycle.phase === PHASE.READING && (
        <Button variant="secondary" disabled>Reading…</Button>
      )}
    </div>
  )
}
