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
 * One line per alert: name · source · step · time. Alerts that are the same
 * on the page (same name, severity, source and step) share a line with a
 * count, "Staged Malware Activity ×4", at the time of the first; the title
 * still counts every alert. No alerts, no section.
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
 * One entry per distinct alert, in first-seen order: `{ alert, count }`, where
 * `alert` is the first of its kind. Two alerts are the same kind when their
 * name, severity, source and step all agree.
 *
 * @param {Array<object>} alerts
 * @returns {Array<{ alert: object, count: number, key: string }>}
 */
export function groupAlerts(alerts) {
  const groups = []
  const byKey = new Map()
  for (const alert of alerts) {
    const key = JSON.stringify([
      alert.alert_name ?? null, alert.severity ?? null,
      alert.alert_source ?? null, alert.step_id ?? null,
    ])
    const group = byKey.get(key)
    if (group) {
      group.count += 1
    } else {
      const first = { alert, count: 1, key }
      byKey.set(key, first)
      groups.push(first)
    }
  }
  return groups
}

/**
 * @param {{ alerts?: Array<{alert_external_id?: string, alert_name?: string,
 *   alert_source?: string, severity?: string, step_id?: string,
 *   observed_at?: string}> }} props
 */
export default function AlsoRaised({ alerts = [] }) {
  const list = Array.isArray(alerts) ? alerts : []
  if (list.length === 0) return null
  return (
    <section className="also-raised" data-testid="also-raised" aria-label={ALSO_RAISED_TITLE}>
      <h2 className="also-raised__title">{ALSO_RAISED_TITLE} ({list.length})</h2>
      <p className="also-raised__note">{ALSO_RAISED_NOTE}</p>
      <ul className="also-raised__list">
        {groupAlerts(list).map(({ alert: a, count, key }) => (
          <li key={key} className="also-raised__item">
            <span className="also-raised__name">
              {a.alert_name || 'Unnamed alert'}
              {count > 1 && <span className="also-raised__count"> ×{count}</span>}
            </span>
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
