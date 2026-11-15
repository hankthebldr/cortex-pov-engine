import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { getRuns, getResultsForRun, getRunPair } from '../../api/client.js'
import { runIdOf } from '../../api/ids.js'
import { arePaired } from '../../outcomes.js'
import { useEnvironment } from '../../context/EnvironmentContext.jsx'
import { runLifecycle, readBackOf, PHASE } from './runLifecycle.js'
import { relativeTime, absoluteTime } from './runRows.js'
import useReadBack from './useReadBack.jsx'
import {
  COMPARE_MAX, COMPARE_MIN, COMPARE_FULL, PHASE_STATUS,
  isComparable, togglePick, buildCompare, cellMeta,
} from './runCompare.js'
import { Button, StatusIcon } from './kit/index.js'
import '../../styles/destinations/run-compare.css'

void React

/** How many finished runs the picker shows before "Show all". */
const CHOICES_SHOWN = 24

/** The detection column's width, and the narrowest a run column may get. */
const DET_COL_PX = 240
const RUN_COL_MIN_PX = 190

/** Results opens on the Report page for that run. */
function openResultsByHash(id) {
  window.location.hash = `#/proof?run=${encodeURIComponent(id)}`
}

function shortId(id) {
  const s = String(id || '')
  return s.length > 12 ? s.slice(0, 8) : s
}

/**
 * MultiRunCompare — Runs · Compare (design change C2).
 *
 * Pick two or three finished runs; each expected detection lines up across
 * them, so a second attempt shows what changed. The model, and the honesty
 * rule, live in runCompare.js: only a run that has been read back may say
 * Detected or Not seen; any other run says "Ran — not read back" or "Did not
 * run". Each column header carries the run's next action — Read back while it
 * is unread, Open results once it is — through the console's one Read back
 * (useReadBack) and the Results route.
 *
 * Reached at Runs `?compare=1` and from the Compare toggle on Results.
 */
export default function MultiRunCompare({ onOpenResults = openResultsByHash }) {
  const { scenarios = [] } = useEnvironment()
  const { reading, outcomes, readBack } = useReadBack()
  const [runs, setRuns] = useState([])
  const [loadState, setLoadState] = useState({ loading: true, error: null })
  const [picked, setPicked] = useState([])
  const [full, setFull] = useState(false)
  const [showAll, setShowAll] = useState(false)
  const [results, setResults] = useState({})
  // Runs whose read back has landed but whose fresh row has not: they keep
  // reading as "Reading…" so the column does not flash back to Read back.
  const [settling, setSettling] = useState(() => new Set())

  const loadRuns = useCallback(() => {
    return getRuns({ fields: 'summary' })
      .then((data) => {
        setRuns(Array.isArray(data) ? data : [])
        setLoadState({ loading: false, error: null })
      })
      .catch((err) => setLoadState({ loading: false, error: err?.message || 'request failed' }))
  }, [])

  useEffect(() => { loadRuns() }, [loadRuns])

  const finished = useMemo(() => runs.filter(isComparable), [runs])
  const pickedRuns = useMemo(
    () => picked.map((id) => finished.find((r) => runIdOf(r) === id)).filter(Boolean),
    [picked, finished],
  )

  // Fetch the results of every picked run not yet held. A read back drops a
  // run's entry and bumps its generation, so an answer from before the read
  // back can never land over the one after it.
  const mounted = useRef(true)
  const generation = useRef({})
  const inFlight = useRef(new Set())
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])
  useEffect(() => {
    for (const id of picked) {
      const gen = generation.current[id] || 0
      const key = `${id}:${gen}`
      if (id in results || inFlight.current.has(key)) continue
      inFlight.current.add(key)
      getResultsForRun(id).then(
        (d) => ({
          rows: (d && d.results) || (Array.isArray(d) ? d : []),
          coverage: (d && !Array.isArray(d) && d.coverage) || null,
        }),
        (err) => ({ error: err?.message || 'request failed' }),
      ).then((entry) => {
        inFlight.current.delete(key)
        if (!mounted.current || (generation.current[id] || 0) !== gen) return
        setResults((prev) => ({ ...prev, [id]: entry }))
      })
    }
  }, [picked, results])

  // The latest pick, read synchronously: two clicks before a re-render must
  // both count, and the fourth must still be refused.
  const pickedRef = useRef(picked)
  const onToggle = (id) => {
    const next = togglePick(pickedRef.current, id)
    setFull(next.refused)
    if (next.refused) return
    pickedRef.current = next.picked
    setPicked(next.picked)
  }

  const onReadBack = async (id) => {
    setSettling((prev) => new Set(prev).add(id))
    try {
      await readBack(id)
      await loadRuns()
      // The read back changed the run's outcomes: read its results again.
      generation.current[id] = (generation.current[id] || 0) + 1
      setResults((prev) => {
        const next = { ...prev }
        delete next[id]
        return next
      })
    } finally {
      setSettling((prev) => {
        const next = new Set(prev)
        next.delete(id)
        return next
      })
    }
  }

  const isReading = (id) => reading.has(id) || settling.has(id)
  const lifecycleOf = (run) => runLifecycle(run, { reading: isReading(runIdOf(run)) })
  const nameOf = (run) => scenarios.find((s) => (s.scenario_id || s.id) === run.scenario_id)?.name
    || run.scenario_id || '(scenario unknown)'

  const grid = useMemo(() => {
    const lifecycles = {}
    for (const r of pickedRuns) lifecycles[runIdOf(r)] = lifecycleOf(r)
    return buildCompare({ runs: pickedRuns, lifecycles, results })
  }, [pickedRuns, results, reading, settling]) // eslint-disable-line react-hooks/exhaustive-deps

  const choices = showAll ? finished : finished.slice(0, CHOICES_SHOWN)

  return (
    <div className="run-compare" data-testid="run-compare">
      <p className="run-compare__intro">
        Pick two or three finished runs. Each detection lines up across them, so a second
        attempt shows what changed. A run that has not been read back makes no detection claim.
      </p>

      <section className="run-compare__picker" aria-labelledby="run-compare-pick">
        <h2 id="run-compare-pick" className="run-compare__label">
          Runs to compare · <span className="mono" data-testid="run-compare-count">{pickedRuns.length}</span> of {COMPARE_MAX}
        </h2>
        {loadState.loading ? (
          <p className="run-compare__note">Loading runs…</p>
        ) : loadState.error ? (
          <p className="run-compare__note run-compare__note--error" role="alert" data-testid="run-compare-error">
            Runs could not be loaded: <span className="mono">{loadState.error}</span>. Nothing is
            compared until SimCore answers.
          </p>
        ) : finished.length === 0 ? (
          <p className="run-compare__note" data-testid="run-compare-none">
            No run has finished on this POVengine yet. Compare needs two finished runs.
          </p>
        ) : (
          <>
            <div className="run-compare__chips">
              {choices.map((r) => {
                const id = runIdOf(r)
                const on = picked.includes(id)
                const ph = PHASE_STATUS[lifecycleOf(r).phase] || PHASE_STATUS[PHASE.UNREAD]
                return (
                  <button
                    key={id}
                    type="button"
                    className={'run-compare__chip' + (on ? ' is-on' : '')}
                    aria-pressed={on ? 'true' : 'false'}
                    data-testid="run-compare-chip"
                    data-run-id={id}
                    onClick={() => onToggle(id)}
                    title={absoluteTime(r.started_at) || undefined}
                  >
                    <span className="run-compare__chip-name">{nameOf(r)}</span>
                    <span className="run-compare__chip-meta mono">
                      {shortId(id)} · {relativeTime(r.started_at)} · {ph.word.toLowerCase()}
                    </span>
                  </button>
                )
              })}
            </div>
            {finished.length > CHOICES_SHOWN && (
              <button type="button" className="run-compare__more" onClick={() => setShowAll((v) => !v)}>
                {showAll ? `Show the newest ${CHOICES_SHOWN}` : `Show all ${finished.length} finished runs`}
              </button>
            )}
            <p className="run-compare__note" role="status" data-testid="run-compare-full">
              {full ? COMPARE_FULL : ''}
            </p>
            {grid.mixed && pickedRuns.length >= COMPARE_MIN && (
              <p className="run-compare__note" data-testid="run-compare-mixed">
                These runs come from different scenarios. Only the detections they share line up;
                the rest read “Not in this run”.
              </p>
            )}
          </>
        )}
      </section>

      {pickedRuns.length < COMPARE_MIN ? (
        !loadState.loading && !loadState.error && finished.length > 0 && (
          <p className="run-compare__empty" data-testid="run-compare-pick-more">
            {pickedRuns.length === 1
              ? 'Pick one more run to compare.'
              : 'Pick at least two runs above to compare them.'}
          </p>
        )
      ) : (
        <>
          <PairSection runs={pickedRuns} />
          <div className="run-compare__scroll">
            <table
              className="run-compare__grid"
              data-testid="run-compare-grid"
              style={{ minWidth: `${DET_COL_PX + grid.columns.length * RUN_COL_MIN_PX}px` }}
            >
              <colgroup>
                <col className="run-compare__col-det" />
                {grid.columns.map((c) => <col key={c.id} />)}
              </colgroup>
              <thead>
                <tr>
                  <th scope="col" className="run-compare__corner">Detection</th>
                  {grid.columns.map((c) => (
                    <th scope="col" key={c.id} className="run-compare__head" data-testid="run-compare-head" data-run-id={c.id}>
                      <RunHead
                        col={c}
                        name={nameOf(c.run)}
                        outcome={outcomes.get(c.id)}
                        onReadBack={() => onReadBack(c.id)}
                        onOpenResults={() => onOpenResults(c.id)}
                      />
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {grid.rows.length === 0 && (
                  <tr>
                    <th scope="row" className="run-compare__det">—</th>
                    <td colSpan={grid.columns.length} className="run-compare__cell run-compare__cell--quiet">
                      {grid.columns.some((c) => c.loading)
                        ? 'Loading results…'
                        : 'None of these runs has an expected detection.'}
                    </td>
                  </tr>
                )}
                {grid.rows.map((row) => (
                  <tr key={row.key} data-testid="run-compare-row" data-detection={row.slug}>
                    <th scope="row" className="run-compare__det" title={row.title || undefined}>
                      {row.type && <span className="run-compare__type mono">{row.type}</span>}
                      <span className="run-compare__slug mono">{row.slug}</span>
                      {row.step && <span className="run-compare__step mono">{row.step}</span>}
                    </th>
                    {row.cells.map((cell, i) => (
                      <td
                        key={grid.columns[i].id}
                        className="run-compare__cell"
                        data-testid="run-compare-cell"
                        data-state={cell.key}
                        title={cell.title || undefined}
                      >
                        <Cell cellKey={cell.key} />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
              <tbody className="run-compare__summary">
                {grid.summary.map((s) => (
                  <tr key={s.key} data-testid={`run-compare-sum-${s.key}`}>
                    <th scope="row" className="run-compare__sum-label">{s.label}</th>
                    {s.values.map((v, i) => (
                      <td
                        key={grid.columns[i].id}
                        className={'run-compare__sum mono' + (v.short ? ' run-compare__sum--short' : '')}
                        title={v.title || undefined}
                      >
                        {v.text}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {grid.columns.some((c) => c.error) && (
            <p className="run-compare__note run-compare__note--error" role="alert" data-testid="run-compare-results-error">
              {grid.columns.filter((c) => c.error).map((c) => (
                <span key={c.id}>Results for <span className="mono">{shortId(c.id)}</span> could not be loaded: {c.error}. </span>
              ))}
              Its column makes no claim until they load.
            </p>
          )}
        </>
      )}
    </div>
  )
}

/** A run's column header: what it is, where it is, and its next action. */
function RunHead({ col, name, outcome, onReadBack, onOpenResults }) {
  const ph = PHASE_STATUS[col.phase] || PHASE_STATUS[PHASE.UNREAD]
  // Why the last read back read nothing — only while the run is still unread.
  const why = outcome && !outcome.ok && col.phase === PHASE.UNREAD ? outcome.message : null
  return (
    <div className="run-compare__head-in">
      <span className="run-compare__head-name" title={name}>{name}</span>
      <span className="run-compare__head-meta mono" title={absoluteTime(col.run.started_at) || undefined}>
        {shortId(col.id)} · {relativeTime(col.run.started_at)}
      </span>
      <span className="run-compare__head-row">
        <StatusIcon status={ph.status} word={ph.word} />
        {col.phase === PHASE.UNREAD && (
          <Button variant="primary" className="run-compare__act" onClick={onReadBack}>Read back</Button>
        )}
        {col.phase === PHASE.READING && (
          <Button variant="secondary" className="run-compare__act" disabled>Reading…</Button>
        )}
        {col.phase === PHASE.READY && (
          <Button variant="secondary" className="run-compare__act" onClick={onOpenResults}>Open results</Button>
        )}
      </span>
      {why && <span className="run-compare__why" data-testid="run-compare-why">{why}</span>}
    </div>
  )
}

function Cell({ cellKey }) {
  if (cellKey === 'loading') return <span className="run-compare__quiet">…</span>
  if (cellKey === 'error') return <span className="run-compare__quiet">—</span>
  const meta = cellMeta(cellKey)
  if (!meta.status) return <span className="run-compare__quiet">{meta.word}</span>
  return <StatusIcon status={meta.status} word={meta.word} />
}

/* ─── Prevention delta (report → block) ──────────────────────────── */

/**
 * Exactly two picked runs that point at each other are a report/block pair:
 * the server builds the delta and the console lays it out. The delta names
 * detected / prevented outcomes, so it is shown only once both runs have been
 * read back — the same rule as the grid.
 */
function PairSection({ runs }) {
  const pairedRuns = runs.length === 2 && arePaired(runs[0], runs[1]) ? runs : null
  const bothRead = !!pairedRuns && pairedRuns.every((r) => readBackOf(r))
  const pairKey = pairedRuns && bothRead ? pairedRuns.map(runIdOf).join('+') : null
  const [pair, setPair] = useState(null) // { key, delta } | { key, error, code }
  useEffect(() => {
    if (!pairKey) { setPair(null); return undefined }
    let cancelled = false
    const anchor = runIdOf(pairedRuns.find((r) => r.paired_with_run_id) || pairedRuns[0])
    getRunPair(anchor)
      .then((body) => { if (!cancelled) setPair({ key: pairKey, delta: body?.delta || null }) })
      .catch((err) => {
        if (!cancelled) setPair({ key: pairKey, error: err?.detail && typeof err.detail === 'string' ? err.detail : (err?.message || 'pair delta unavailable'), code: err?.code || null })
      })
    return () => { cancelled = true }
  }, [pairKey]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!pairedRuns) return null
  if (!bothRead) {
    return (
      <p className="run-compare__note" data-testid="pair-delta-unread">
        These two runs are a report / block pair. The prevention delta appears once both have
        been read back.
      </p>
    )
  }
  if (!pair || pair.key !== pairKey) return null
  return <PairDelta pair={pair} />
}

const DASH = '—'

function sideText(side, withReason) {
  if (!side) return DASH
  let text = String(side.outcome || DASH)
  if (withReason && side.outcome_reason) text += ` (${side.outcome_reason})`
  if (side.mttd_seconds != null) text += ` ${side.mttd_seconds}s`
  return text
}

function PairDelta({ pair }) {
  if (pair.error) {
    return (
      <div className="coverage__empty mono" role="alert" data-testid="pair-delta-error">
        {pair.error}{pair.code ? ` [${pair.code}]` : ''}
      </div>
    )
  }
  const steps = pair.delta?.steps || []
  const rows = steps.reduce((n, st) => n + (st.detections || []).length, 0)
  if (rows === 0) {
    return (
      <div className="coverage__empty mono" data-testid="pair-delta-empty">
        No delta rows — the pair has no comparable detections.
      </div>
    )
  }
  return (
    <div className="multirun__pair" data-testid="pair-delta">
      <div className="competitive__filter-label mono compare-runs__avail-label">
        prevention delta · report mode → block mode
        {pair.delta?.severed_at_step ? ` · chain severed at ${pair.delta.severed_at_step}` : ''}
      </div>
      <div className="multirun__matrix-scroll">
        <table className="multirun__matrix" data-testid="pair-delta-table">
          <thead>
            <tr>
              <th>Step</th>
              <th>Detection</th>
              <th>Type</th>
              <th>Module</th>
              <th>Report-mode</th>
              <th>Block-mode</th>
            </tr>
          </thead>
          <tbody>
            {steps.flatMap((step) => (step.detections || []).map((d, i) => (
              <tr key={`${step.step_id}:${d.detection_id || i}:${i}`}>
                <td className="mono">{step.step_id}</td>
                <td className="mono">{d.detection_id || DASH}</td>
                <td>{d.signal_type || DASH}</td>
                <td>{d.prevention_module || DASH}</td>
                <td className="mono">{sideText(d.report, false)}</td>
                <td className="mono">{sideText(d.block, true)}</td>
              </tr>
            )))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
