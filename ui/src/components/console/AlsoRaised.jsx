/**
 * AlsoRaised — the alerts Cortex raised for this run that match none of its
 * expected detections (#346).
 *
 * `GET /api/results/{run_id}` lists them under `additional_detections`: on the
 * run's endpoint, while the run held it, matching no expected row (an agent
 * module's verdict, an analytics rule the scenario does not declare). They are
 * real detections and were shown nowhere. They are not coverage: Detection
 * Accuracy counts expected rows only, and the one line of copy says so.
 *
 * One line per alert: name · source · step · time. No alerts, no section.
 */
import React from 'react'
import { absoluteTime } from './runRows.js'
import { parseServerTime } from '../../api/time.js'

void React

export const ALSO_RAISED_TITLE = 'Also raised by Cortex'
export const ALSO_RAISED_NOTE = 'Not counted in coverage.'

/** The machine-readable instant: server times are UTC with no zone written. */
function isoInstant(value) {
  const t = parseServerTime(value)
  return Number.isNaN(t) ? undefined : new Date(t).toISOString()
}

/**
 * @param {{ alerts?: Array<{alert_external_id?: string, alert_name?: string,
 *   alert_source?: string, step_id?: string, observed_at?: string}> }} props
 */
export default function AlsoRaised({ alerts = [] }) {
  const list = Array.isArray(alerts) ? alerts : []
  if (list.length === 0) return null
  return (
    <section className="also-raised" data-testid="also-raised" aria-label={ALSO_RAISED_TITLE}>
      <h2 className="also-raised__title">{ALSO_RAISED_TITLE} ({list.length})</h2>
      <p className="also-raised__note">{ALSO_RAISED_NOTE}</p>
      <ul className="also-raised__list">
        {list.map((a, i) => (
          <li
            key={a.alert_external_id || `${a.observed_at}-${a.alert_name}-${i}`}
            className="also-raised__item"
          >
            <span className="also-raised__name">{a.alert_name || 'Unnamed alert'}</span>
            <span className="also-raised__sep" aria-hidden="true"> · </span>
            <span>{a.alert_source || 'source unknown'}</span>
            <span className="also-raised__sep" aria-hidden="true"> · </span>
            <span className="mono">{a.step_id || 'step unknown'}</span>
            <span className="also-raised__sep" aria-hidden="true"> · </span>
            <time className="mono" dateTime={isoInstant(a.observed_at)}>
              {absoluteTime(a.observed_at) || 'time unknown'}
            </time>
          </li>
        ))}
      </ul>
    </section>
  )
}
