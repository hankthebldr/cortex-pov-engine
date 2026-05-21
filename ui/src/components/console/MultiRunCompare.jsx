import React, { useEffect, useMemo, useState } from 'react'
import { getRuns, getResultsForRun } from '../../api/client.js'

/**
 * MultiRunCompare — side-by-side scorecard for 2-N runs.
 *
 * DC use case: "we changed a detection rule, did it improve coverage?"
 * Pick the before-run + the after-run, compare. The comparison view
 * also drives the POV close narrative — "across these three runs over
 * the week, coverage went from 33% → 75% → 100%."
 *
 * Reached via a "Compare runs" toggle in the Evidence tab header.
 *
 * Row order: union of all (tid, plane, description) tuples across the
 * picked runs, grouped by step ordering. Each cell shows the run's
 * status for that detection — detected / missed / pending / no-row.
 * Delta highlights when the most-recent run improves vs an earlier
 * one (new detection = signal-teal pulse on the cell border).
 */
export default function MultiRunCompare() {
  const [availableRuns, setAvailableRuns] = useState([])
  const [selectedIds, setSelectedIds]     = useState([])
  const [resultsByRun, setResultsByRun]   = useState({})
  const [loadingRuns, setLoadingRuns]     = useState(true)
  const [loadingResults, setLoadingResults] = useState(false)

  // Fetch all available runs once for the picker.
  useEffect(() => {
    setLoadingRuns(true)
    getRuns()
      .then((data) => {
        const list = Array.isArray(data) ? data : (data && data.runs) || []
        setAvailableRuns(list)
      })
      .catch(() => setAvailableRuns([]))
      .finally(() => setLoadingRuns(false))
  }, [])

  // When the picked set changes, fetch any missing run's results.
  useEffect(() => {
    if (selectedIds.length === 0) return undefined
    const missing = selectedIds.filter((id) => !resultsByRun[id])
    if (missing.length === 0) return undefined
    setLoadingResults(true)
    Promise.all(
      missing.map((id) => getResultsForRun(id).then(
        (d) => [id, (d && d.results) || d || []],
        ()  => [id, []],
      )),
    ).then((pairs) => {
      setResultsByRun((prev) => {
        const next = { ...prev }
        for (const [id, rows] of pairs) next[id] = rows
        return next
      })
      setLoadingResults(false)
    })
  }, [selectedIds]) // eslint-disable-line react-hooks/exhaustive-deps

  const togglePick = (id) => {
    setSelectedIds((prev) => {
      if (prev.includes(id)) return prev.filter((x) => x !== id)
      // Cap at 4 to keep the matrix readable.
      if (prev.length >= 4) return prev
      return [...prev, id]
    })
  }

  // Build the merged detection table.
  const { columns, rows, kpis } = useMemo(
    () => buildComparison(selectedIds, availableRuns, resultsByRun),
    [selectedIds, availableRuns, resultsByRun],
  )

  return (
    <div className="multirun">
      <div className="multirun__intro">
        <p className="multirun__intro-prose">
          Compare 2–4 runs side by side. Pick the runs you want to compare
          from the list below — the merged detection table updates live.
          <strong> New detections</strong> in the latest run vs earlier
          runs are highlighted, and <strong>regressions</strong> (detected
          before, missed now) are flagged for follow-up.
        </p>
      </div>

      <div className="multirun__picker">
        <div className="competitive__filter-label mono" style={{ marginBottom: 8 }}>
          available runs · pick up to 4
        </div>
        {loadingRuns ? (
          <div className="coverage__empty mono">loading run history…</div>
        ) : availableRuns.length === 0 ? (
          <div className="coverage__empty mono">no runs in history — launch a scenario first</div>
        ) : (
          <div className="multirun__run-list">
            {availableRuns.slice(0, 16).map((r) => {
              const id = r.id || r.run_id
              const picked = selectedIds.includes(id)
              const order = picked ? selectedIds.indexOf(id) + 1 : null
              return (
                <button
                  key={id}
                  type="button"
                  className={'multirun__run-pill' + (picked ? ' is-picked' : '')}
                  onClick={() => togglePick(id)}
                  title={r.scenario_id || id}
                >
                  {picked && <span className="multirun__run-pill-order mono">#{order}</span>}
                  <span className="multirun__run-pill-id mono">
                    {String(id).slice(0, 10)}
                  </span>
                  <span className="multirun__run-pill-scenario">
                    {r.scenario_id || '—'}
                  </span>
                  <span className="multirun__run-pill-status mono">
                    {(r.status || 'unknown').slice(0, 1).toUpperCase()}
                  </span>
                </button>
              )
            })}
          </div>
        )}
      </div>

      {selectedIds.length >= 2 ? (
        <div className="multirun__matrix-wrap">
          <div className="multirun__kpi-row">
            {columns.map((c) => (
              <div key={c.id} className="multirun__kpi">
                <div className="multirun__kpi-runid mono">{c.label}</div>
                <div className="multirun__kpi-coverage">
                  <span className="multirun__kpi-pct">{c.coveragePct}</span>
                  <span className="multirun__kpi-unit">%</span>
                </div>
                <div className="multirun__kpi-meta mono">
                  {c.detected} / {c.total} detected
                </div>
                {c.delta != null && (
                  <div className={
                    'multirun__kpi-delta mono ' +
                    (c.delta > 0 ? 'multirun__kpi-delta--up' : c.delta < 0 ? 'multirun__kpi-delta--down' : '')
                  }>
                    {c.delta > 0 ? '▲ +' : c.delta < 0 ? '▼ ' : '—'}
                    {c.delta !== 0 ? Math.abs(c.delta) + ' pts' : ''}
                  </div>
                )}
              </div>
            ))}
          </div>

          <div className="multirun__matrix-scroll">
            <table className="multirun__matrix">
              <thead>
                <tr>
                  <th className="multirun__h-detection">Detection</th>
                  {columns.map((c) => (
                    <th key={c.id} className="multirun__h-run mono">
                      #{c.order} · {c.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.key}>
                    <th scope="row" className="multirun__row-detection">
                      <div className="multirun__row-tid mono">{row.tid}</div>
                      <div className="multirun__row-desc">{row.description}</div>
                      <div className="multirun__row-plane mono">
                        {row.plane}{row.detectionType && ' · ' + row.detectionType}
                      </div>
                    </th>
                    {columns.map((c) => {
                      const cell = row.byRun[c.id] || { state: 'absent' }
                      const isNew = cell.state === 'detected'
                        && c.order > 1
                        && (row.byRun[columns[c.order - 2].id]?.state !== 'detected')
                      const isRegression = cell.state !== 'detected'
                        && c.order > 1
                        && (row.byRun[columns[c.order - 2].id]?.state === 'detected')
                      return (
                        <td
                          key={c.id}
                          className={
                            'multirun__cell multirun__cell--' + cell.state +
                            (isNew        ? ' multirun__cell--new'        : '') +
                            (isRegression ? ' multirun__cell--regression' : '')
                          }
                          title={
                            cell.state === 'detected' ? 'Detected' + (cell.mttd != null ? ` · ${cell.mttd}s` : '')
                            : cell.state === 'missed'  ? 'Missed'
                            : cell.state === 'pending' ? 'Pending'
                            : 'Not in this run'
                          }
                        >
                          <span className="multirun__cell-glyph">
                            {cell.state === 'detected' ? '✓'
                             : cell.state === 'missed'  ? '✗'
                             : cell.state === 'pending' ? '○'
                             :                            '—'}
                          </span>
                          {cell.state === 'detected' && cell.mttd != null && (
                            <span className="multirun__cell-mttd mono">{cell.mttd}s</span>
                          )}
                          {isNew && <span className="multirun__flag multirun__flag--new" aria-label="New detection">+</span>}
                          {isRegression && <span className="multirun__flag multirun__flag--reg" aria-label="Regression">!</span>}
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="multirun__legend">
            <span className="multirun__legend-item multirun__cell--detected">
              <span className="multirun__cell-glyph">✓</span> Detected
            </span>
            <span className="multirun__legend-item multirun__cell--missed">
              <span className="multirun__cell-glyph">✗</span> Missed
            </span>
            <span className="multirun__legend-item multirun__cell--pending">
              <span className="multirun__cell-glyph">○</span> Pending
            </span>
            <span className="multirun__legend-item multirun__cell--absent">
              <span className="multirun__cell-glyph">—</span> Not in this run
            </span>
            <span className="multirun__legend-item">
              <span className="multirun__flag multirun__flag--new">+</span> New detection vs prior run
            </span>
            <span className="multirun__legend-item">
              <span className="multirun__flag multirun__flag--reg">!</span> Regression vs prior run
            </span>
          </div>
        </div>
      ) : (
        <div className="coverage__empty mono">
          {selectedIds.length === 1
            ? 'pick at least one more run to compare'
            : 'pick 2–4 runs above to start comparing'}
          {loadingResults && ' · loading results'}
        </div>
      )}
    </div>
  )
}

/* ─── Comparison builder ─────────────────────────────────────────── */

function buildComparison(selectedIds, availableRuns, resultsByRun) {
  if (selectedIds.length < 2) return { columns: [], rows: [], kpis: {} }

  const runById = new Map(availableRuns.map((r) => [r.id || r.run_id, r]))

  // Build per-run columns with summary stats.
  const columns = selectedIds.map((id, idx) => {
    const run = runById.get(id) || {}
    const results = resultsByRun[id] || []
    const total = results.length
    const detected = results.filter((r) => r.observed === true).length
    const coveragePct = total > 0 ? Math.round((detected / total) * 100) : 0
    return {
      id,
      order: idx + 1,
      label: (run.scenario_id || id || '').toString().slice(0, 14),
      total,
      detected,
      coveragePct,
      delta: null, // filled below
      results,
    }
  })

  // Coverage delta vs previous column.
  for (let i = 1; i < columns.length; i++) {
    columns[i].delta = columns[i].coveragePct - columns[i - 1].coveragePct
  }

  // Merge all detection rows across runs — keyed by tid + plane +
  // detection_type + expected_description so the same expected
  // detection across runs collapses into one row.
  const rowMap = new Map()
  for (const col of columns) {
    for (const r of col.results) {
      const key = [
        r.mitre_technique || r.tid || '',
        (r.plane || '').toUpperCase(),
        (r.detection_type || r.type || ''),
        (r.expected_description || r.description || ''),
      ].join('|')
      let row = rowMap.get(key)
      if (!row) {
        row = {
          key,
          tid:           r.mitre_technique || r.tid || '—',
          plane:         (r.plane || '').toUpperCase(),
          detectionType: r.detection_type || r.type || '',
          description:   r.expected_description || r.description || '(unnamed)',
          byRun:         {},
        }
        rowMap.set(key, row)
      }
      let state = 'pending'
      if (r.observed === true)  state = 'detected'
      if (r.observed === false) state = 'missed'
      row.byRun[col.id] = { state, mttd: r.mttd_seconds ?? null, alertId: r.alert_id }
    }
  }

  // Sort rows by tid then plane for deterministic display.
  const rows = Array.from(rowMap.values()).sort((a, b) => {
    if (a.tid !== b.tid) return a.tid.localeCompare(b.tid)
    return a.plane.localeCompare(b.plane)
  })

  return { columns, rows }
}
