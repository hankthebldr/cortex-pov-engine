import React from 'react'
import { chipFor } from './doorModel.js'

const TONE_CLASS = {
  ready:   'chip--detected',
  partial: 'chip--pending',
  needs:   'chip--missed',
  unknown: '',
  error:   'chip--missed',
}

/**
 * DoorChip — the Library's per-scenario ingestion-door readiness chip.
 * `readiness` is the scenario's entry from GET /api/doors/readiness (or
 * undefined when the fetch has not landed / failed — rendered NOT CHECKED).
 * `error` is the fetch failure, shown in the tooltip so a dead endpoint is not
 * indistinguishable from "nothing reported".
 */
export default function DoorChip({ scenarioId, readiness, doorRows = null, agentId = null, error = null }) {
  const chip = chipFor(readiness, doorRows, agentId)
  const title = error && (readiness === undefined || readiness === null)
    ? `door readiness unavailable: ${error}`
    : chip.title
  return (
    <span
      className={`chip door-chip door-chip--${chip.tone} ${TONE_CLASS[chip.tone] || ''}`.trim()}
      title={title}
      data-testid={`door-chip-${scenarioId}`}
      data-tone={chip.tone}
    >
      {chip.label}
    </span>
  )
}
