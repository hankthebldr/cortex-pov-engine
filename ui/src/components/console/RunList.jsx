/**
 * RunList — the run-history table on the Runs destination.
 *
 * A row answers "what · when · where · how long · how much proved" without
 * being opened. Every cell is a field `GET /api/runs` returns, shaped by the
 * pure helpers in `runRows.js`; nothing here reads the seed catalogs.
 *
 * Controls live in the URL (`?scenario=&status=&page=`) through the
 * surface's `setParams`, so a filtered page is a deep link a DC can paste
 * into a hand-over. Consecutive repeats of one scenario collapse into a
 * group once they reach three — 83 attempts at SIM-EDR-001 become one line
 * with a status breakdown and a `+82 more` expander, not 83 lines of uuid.
 *
 * Honesty rules the cells carry:
 *   - a failed/aborted run with no hit shows `unproven`, never `0 / 10`;
 *   - a run with no Result rows shows a dash, never `0 / 0`;
 *   - timestamps are UTC and say so in the hover title.
 */
import React, { useEffect, useMemo, useState } from 'react'
import { runIdOf } from '../../api/ids.js'
import { isRunTerminal, isRunUnproven } from './runStatus.js'
import {
  relativeTime, absoluteTime, runDuration, detectionCell, runAgent,
  filterRuns, groupRuns, pageOf, statusKey, PAGE_SIZE,
} from './runRows.js'

const STATUS_OPTIONS = ['running', 'complete', 'failed', 'aborted', 'pending', 'staged']

function chipClass(status) {
  const s = statusKey(status)
  if (s === 'running') return ' run-list__status-chip--live'
  if (s === 'complete') return ' run-list__status-chip--done'
  if (isRunUnproven(s)) return ' run-list__status-chip--unproven'
  return ' run-list__status-chip--pending'
}

function shortId(id) {
  const s = String(id || '')
  return s.length > 12 ? s.slice(0, 8) : s
}

function RunRow({ run, now, onOpen, inGroup = false }) {
  const id = runIdOf(run)
  const det = detectionCell(run)
  return (
    <button
      type="button"
      role="row"
      className={'run-list__row' + (inGroup ? ' run-list__row--grouped' : '')}
      data-testid="run-row"
      data-run-id={id}
      onClick={() => onOpen(id)}
    >
      <span role="cell" className="run-list__scenario">
        <span className="mono run-list__scenario-id">{run.scenario_id || '(scenario unknown)'}</span>
        <span className="mono run-list__id" title={id}>{shortId(id)}</span>
      </span>
      <span role="cell" className="mono run-list__started">
        <time dateTime={run.started_at || undefined} title={absoluteTime(run.started_at)} data-testid="run-started">
          {relativeTime(run.started_at, now)}
        </time>
      </span>
      <span role="cell" className="mono run-list__agent" title={run.mode ? `${run.mode} mode` : undefined}>
        {runAgent(run)}
      </span>
      <span role="cell" className="mono run-list__duration" data-testid="run-duration">
        {runDuration(run, now)}
      </span>
      <span
        role="cell"
        className={`mono run-list__det run-list__det--${det.tone}`}
        title={det.title}
        data-testid="run-detections"
      >
        {det.text}
      </span>
      <span role="cell" className="run-list__status">
        <span className={'chip run-list__status-chip' + chipClass(run.status)} data-testid="run-status">
          {run.status || 'unknown'}
        </span>
      </span>
    </button>
  )
}

function GroupRow({ group, now, onOpen, expanded, onToggle }) {
  const rest = group.rows.length - 1
  const breakdown = Object.entries(group.byStatus)
    .sort((a, b) => b[1] - a[1])
    .map(([k, n]) => `${n} ${k}`)
    .join(' · ')
  return (
    <div className="run-list__group" data-testid="run-group" data-scenario-id={group.scenarioId}>
      <RunRow run={group.newest} now={now} onOpen={onOpen} />
      <button
        type="button"
        className="run-list__group-toggle"
        aria-expanded={expanded}
        data-testid="run-group-toggle"
        onClick={onToggle}
      >
        <span className="mono">{expanded ? '−' : '+'}{rest} more</span>
        <span className="run-list__group-what">
          {group.rows.length} consecutive runs of <span className="mono">{group.scenarioId}</span>
        </span>
        <span className="mono run-list__group-breakdown">{breakdown}</span>
      </button>
      {expanded && group.rows.slice(1).map((r) => (
        <RunRow key={runIdOf(r)} run={r} now={now} onOpen={onOpen} inGroup />
      ))}
    </div>
  )
}

export default function RunList({ runs = [], params = {}, setParams = () => {}, onOpen = () => {} }) {
  const scenario = params.scenario || ''
  const status = params.status || ''
  const page = Number(params.page) || 1
  const [expanded, setExpanded] = useState(() => new Set())

  // Relative times tick: every 30 s at rest, every second while a run on the
  // page is in flight (its duration cell is live elapsed time).
  const [now, setNow] = useState(() => Date.now())
  const anyRunning = useMemo(() => runs.some((r) => statusKey(r?.status) === 'running'), [runs])
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), anyRunning ? 1000 : 30_000)
    return () => clearInterval(t)
  }, [anyRunning])

  const scenarioOptions = useMemo(() => {
    const counts = new Map()
    for (const r of runs) if (r?.scenario_id) counts.set(r.scenario_id, (counts.get(r.scenario_id) || 0) + 1)
    return [...counts.entries()].sort((a, b) => a[0].localeCompare(b[0]))
  }, [runs])

  const filtered = useMemo(() => filterRuns(runs, { scenario, status }), [runs, scenario, status])
  const grouped = useMemo(() => groupRuns(filtered), [filtered])
  const paged = useMemo(() => pageOf(grouped, page, PAGE_SIZE), [grouped, page])

  const setFilter = (patch) => setParams({ ...patch, page: null }, { replace: true })
  const toggle = (key) => setExpanded((prev) => {
    const next = new Set(prev)
    if (next.has(key)) next.delete(key); else next.add(key)
    return next
  })

  if (!runs.length) {
    return (
      <div className="run-list-empty" data-testid="run-list-empty">
        no runs yet — launch a scenario from Simulate
      </div>
    )
  }

  const pagerLabel = `Page ${paged.page} of ${paged.pages} · ${filtered.length} run${filtered.length === 1 ? '' : 's'}`
    + (filtered.length !== runs.length ? ` of ${runs.length}` : '')

  return (
    <div className="run-list" data-testid="run-list">
      <div className="run-list__controls">
        <label className="run-list__filter">
          <span className="run-list__filter-k">Scenario</span>
          <select
            className="mono"
            value={scenario}
            data-testid="run-filter-scenario"
            onChange={(e) => setFilter({ scenario: e.target.value || null })}
          >
            <option value="">all scenarios</option>
            {scenarioOptions.map(([id, n]) => (
              <option key={id} value={id}>{id} ({n})</option>
            ))}
          </select>
        </label>
        <label className="run-list__filter">
          <span className="run-list__filter-k">Status</span>
          <select
            className="mono"
            value={status}
            data-testid="run-filter-status"
            onChange={(e) => setFilter({ status: e.target.value || null })}
          >
            <option value="">any status</option>
            {STATUS_OPTIONS.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        {(scenario || status) && (
          <button
            type="button"
            className="btn run-list__clear"
            data-testid="run-filter-clear"
            onClick={() => setFilter({ scenario: null, status: null })}
          >
            clear
          </button>
        )}
        <span className="run-list__spacer" />
        <span className="mono run-list__pager-label" data-testid="run-pager-label">{pagerLabel}</span>
        <button
          type="button"
          className="btn run-list__pager-btn"
          disabled={paged.page <= 1}
          data-testid="run-pager-prev"
          onClick={() => setParams({ page: paged.page - 1 > 1 ? String(paged.page - 1) : null }, { replace: true })}
        >
          ‹ prev
        </button>
        <button
          type="button"
          className="btn run-list__pager-btn"
          disabled={paged.page >= paged.pages}
          data-testid="run-pager-next"
          onClick={() => setParams({ page: String(paged.page + 1) }, { replace: true })}
        >
          next ›
        </button>
      </div>

      {filtered.length === 0 ? (
        <div className="run-list-empty" data-testid="run-list-filtered-empty">
          no runs match this filter — {runs.length} run{runs.length === 1 ? '' : 's'} on this SimCore
        </div>
      ) : (
        <div className="run-list__table" role="table" aria-label="Run history">
          <div role="row" className="run-list__head" aria-hidden="true">
            <span>Scenario</span>
            <span>Started</span>
            <span>Agent</span>
            <span>Duration</span>
            <span title="detected / expected, from this SimCore's Result rows">Detections</span>
            <span>Status</span>
          </div>
          {paged.items.map((entry) => (
            entry.kind === 'group' ? (
              <GroupRow
                key={entry.key}
                group={entry}
                now={now}
                onOpen={onOpen}
                expanded={expanded.has(entry.key)}
                onToggle={() => toggle(entry.key)}
              />
            ) : (
              <RunRow key={entry.key} run={entry.run} now={now} onOpen={onOpen} />
            )
          ))}
        </div>
      )}
      <div className="run-list__foot mono">
        detections are this SimCore's Result rows · tenant-verified: 0
      </div>
    </div>
  )
}

export { RunList }
