/**
 * ResultOfBar — whose results these are (design v26 §4.6).
 *
 * Results always opens with this bar: the scenario, the exact run of it, where
 * it ran, how it ended, and what it was read back from. A switcher moves
 * between runs that have been read back (the URL's `?run=`), so a result is
 * never shown without the run it belongs to.
 */
import React from 'react'
import { runIdOf } from '../../api/ids.js'
import { absoluteTime, runAgent } from './runRows.js'
import { readBackOf } from './runLifecycle.js'
import { Button } from './kit/index.js'
import '../../styles/destinations/run-lifecycle.css'

void React

function shortId(id) {
  const s = String(id || '')
  return s.length > 12 ? s.slice(0, 8) : s
}

function optionLabel(r) {
  const when = absoluteTime(r.started_at)
  const det = typeof r.detected_count === 'number' && typeof r.expected_detections === 'number' && r.expected_detections > 0
    ? ` · ${r.detected_count} of ${r.expected_detections} detected`
    : ''
  return `${r.scenario_id || '(scenario unknown)'} · run ${shortId(runIdOf(r))}${when ? ` · ${when}` : ''}${det}`
}

export default function ResultOfBar({
  run,
  lifecycle,
  scenarioName = null,
  readBackRuns = [],
  onSwitch = () => {},
  onOpenRun = () => {},
}) {
  if (!run) return null
  const id = runIdOf(run)
  const rb = lifecycle?.readBack || readBackOf(run)
  const inList = readBackRuns.some((r) => runIdOf(r) === id)
  const ended = lifecycle?.ended ? lifecycle.ended.toLowerCase() : 'still running'
  const facts = [
    run.scenario_id || '(scenario unknown)',
    `run ${shortId(id)}`,
    `on ${runAgent(run)}`,
    `${ended}${run.completed_at ? ` ${absoluteTime(run.completed_at)}` : ''}`,
  ]

  return (
    <section className="result-of" aria-label="Result of" data-testid="result-of">
      <div className="result-of__main">
        <div className="result-of__eyebrow">Result of</div>
        <div className="result-of__title">{scenarioName || run.scenario_id || id}</div>
        <div className="result-of__facts mono" title={id}>{facts.join(' · ')}</div>
        <div className={'result-of__readback' + (rb ? ' result-of__readback--yes' : '')}>
          {rb
            ? `Read back from ${rb.from}${rb.at ? ` · ${absoluteTime(rb.at)}` : ''}`
            : 'Not read back from the tenant'}
        </div>
      </div>
      {readBackRuns.length > 0 && (
        <label className="result-of__switch">
          <span className="result-of__switch-k">Read-back runs</span>
          <select
            aria-label="Result of run"
            value={inList ? id : ''}
            onChange={(e) => { if (e.target.value) onSwitch(e.target.value) }}
          >
            {!inList && <option value="" disabled>Pick a read-back run</option>}
            {readBackRuns.map((r) => (
              <option key={runIdOf(r)} value={runIdOf(r)}>{optionLabel(r)}</option>
            ))}
          </select>
        </label>
      )}
      <Button variant="secondary" onClick={onOpenRun} aria-label="Open run">Open</Button>
    </section>
  )
}
