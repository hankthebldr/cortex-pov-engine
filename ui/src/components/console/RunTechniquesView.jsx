/**
 * RunTechniquesView — the Techniques tab on a run.
 *
 * A kill-chain strip (14 ATT&CK tactics in order, empty stages dimmed) and a
 * per-technique table, built from this run's `Result` rows and the TTP cards
 * the rows reference. Nothing from `povdata/` and nothing from
 * `/api/mitre/coverage` (see `runTechniques.js`).
 *
 * The run's verdict is not repeated here: the verdict line at the top of
 * Results says it once. Each explanation is one line, its detail in a tooltip.
 *
 * A count never renders before its fetch resolves; a failed fetch says so,
 * never `0`. A failed run never shows a miss.
 */
import React, { useEffect, useMemo, useState } from 'react'
import { getResultsForRun, getRun, getTtp } from '../../api/client.js'
import { buildTechniqueModel } from './runTechniques.js'
import { isRunUnproven } from './runStatus.js'
import { Button, Icon } from './kit/index.js'

// One line each; the detail is one hover away.
const EVIDENCE = {
  line: 'Counts are this SimCore’s result rows',
  detail: 'A detection counts once it is reviewed from the tenant or marked by hand.',
}
const UNPROVEN_DETAIL = 'The signal may never have been generated, so nothing here is a miss.'

function summary(counts) {
  const entries = Object.entries(counts || {})
  if (!entries.length) return '—'
  return entries.sort((a, b) => b[1] - a[1]).map(([k, n]) => `${n} ${k}`).join(' · ')
}

export default function RunTechniquesView({ runId, run: runProp = null, onOpenEvidence = () => {}, onError = () => {} }) {
  const [detail, setDetail] = useState(null)
  const [results, setResults] = useState(null)
  const [cards, setCards] = useState({})
  const [cardErrors, setCardErrors] = useState({})
  const [error, setError] = useState(null)

  // Keyed on the run id ONLY. The parent refreshes its run list on a cadence
  // and hands down a new row object each time; re-running this fetch on every
  // such tick would reset the page to "loading" while nothing changed.
  useEffect(() => {
    if (!runId) return undefined
    let cancelled = false
    setResults(null); setCards({}); setCardErrors({}); setError(null); setDetail(null)
    ;(async () => {
      try {
        const [res, r] = await Promise.all([
          getResultsForRun(runId),
          getRun(runId).catch(() => null),
        ])
        if (cancelled) return
        const rows = Array.isArray(res) ? res : res?.results || []
        setResults(rows)
        if (r) setDetail(r)
        const refs = [...new Set(rows.map((x) => x?.ttp_ref).filter(Boolean))]
        const settled = await Promise.allSettled(refs.map((ref) => getTtp(ref)))
        if (cancelled) return
        const ok = {}
        const bad = {}
        settled.forEach((s, i) => {
          if (s.status === 'fulfilled' && s.value) ok[refs[i]] = s.value
          else bad[refs[i]] = s.reason?.message || 'request failed'
        })
        setCards(ok)
        setCardErrors(bad)
      } catch (err) {
        if (cancelled) return
        const msg = err?.message || 'could not load results'
        setError(msg)
        onError(msg)
      }
    })()
    return () => { cancelled = true }
  }, [runId])

  // The parent's row is the fresher record (it is re-read while a run is
  // live); the one-shot detail read only fills what the row lacks.
  const run = useMemo(() => {
    if (!detail && !runProp) return null
    return { ...(detail || {}), ...(runProp || {}) }
  }, [detail, runProp])

  const model = useMemo(
    () => (results ? buildTechniqueModel({ results, run, cards, cardErrors }) : null),
    [results, run, cards, cardErrors],
  )

  if (!runId) return <div className="run-tech__empty mono">No run selected</div>
  if (error) return <div className="run-tech__empty mono" data-testid="technique-error">Results not loaded · {error}</div>
  if (!model) return <div className="run-tech__empty mono" role="status">Loading…</div>

  const unproven = isRunUnproven(model.runStatus)

  return (
    <div className="run-tech" data-testid="run-techniques" data-proven={model.proven ? 'true' : 'false'}>
      <div className="run-tech__head">
        <div className="run-tech__totals mono" data-testid="technique-totals">
          {model.totals.hits} of {model.totals.denominator} detected
          {model.totals.excluded ? ` · ${model.totals.excluded} never ran` : ''}
          {' · '}{model.techniques.length} technique{model.techniques.length === 1 ? '' : 's'}
          {model.unplaced.length ? ` · ${model.unplaced.length} unplaced` : ''}
        </div>
        <p className="run-tech__sentence" title={EVIDENCE.detail}>{EVIDENCE.line}</p>
        {unproven && (
          <p className="run-tech__unproven" data-testid="technique-unproven" title={UNPROVEN_DETAIL}>
            Run {model.runStatus} · unfired detections read pending
          </p>
        )}
      </div>

      <ol className="run-tech__strip" data-testid="technique-strip" aria-label="Kill chain">
        {model.stages.map((s) => (
          <li
            key={s.id}
            className={`run-tech__stage run-tech__stage--${s.tone}${s.empty ? ' run-tech__stage--empty' : ''}`}
            data-testid="technique-stage"
            data-tactic={s.id}
            data-tone={s.tone}
            title={s.empty ? `${s.name}: no technique in this run` : `${s.name}: ${s.hits} of ${s.denominator} rows detected`}
          >
            <span className="run-tech__stage-name">{s.name}</span>
            <span className="mono run-tech__stage-count">{s.empty ? '—' : `${s.hits}/${s.denominator}`}</span>
            <span className="run-tech__stage-techs">
              {s.techniques.map((t) => (
                <span key={t.id} className={`mono run-tech__tech-chip run-tech__tech-chip--${t.tone}`} title={t.name || t.id}>{t.id}</span>
              ))}
            </span>
          </li>
        ))}
      </ol>

      <table className="run-tech__table" data-testid="technique-table">
        <thead>
          <tr>
            <th>Technique</th><th>Name</th><th>Tactic</th><th>Steps</th><th>Hits / rows</th><th>Outcomes</th><th>KPI</th>
          </tr>
        </thead>
        <tbody>
          {model.techniques.map((t) => (
            <tr key={t.id} data-testid="technique-row" data-technique={t.id} data-tone={t.tone} className={`run-tech__row--${t.tone}`}>
              <td className="mono">{t.id}</td>
              <td>{t.name || '—'}</td>
              <td className="mono">{t.tacticIds.join(', ')}</td>
              <td className="mono">{t.steps.join(', ') || '—'}</td>
              <td className="mono">
                <span className={`run-tech__tone run-tech__tone--${t.tone}`}>{t.hits} / {t.denominator}</span>
                {t.excluded ? <span className="run-tech__muted"> (+{t.excluded} never ran)</span> : null}
              </td>
              <td className="mono">{summary(t.outcomes)}</td>
              <td className="mono">{summary(t.kpi)}</td>
            </tr>
          ))}
          {model.techniques.length === 0 && (
            <tr><td colSpan={7} className="run-tech__empty mono">No technique placed · {model.totals.rows} result row{model.totals.rows === 1 ? '' : 's'}</td></tr>
          )}
        </tbody>
      </table>

      {model.unplaced.length > 0 && (
        <div className="run-tech__unplaced" data-testid="technique-unplaced">
          <div className="run-tech__k" title="Not drawn on the strip; each row says why">Unplaced</div>
          <ul>
            {model.unplaced.map((u) => (
              <li key={u.id} className="mono" data-testid="technique-unplaced-row">
                <span className="run-tech__unplaced-id">{u.id}</span> · {u.rows.length} row{u.rows.length === 1 ? '' : 's'} · {u.reason}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="run-tech__foot">
        <Button onClick={onOpenEvidence}>Detections<Icon name="forward" size={12} /></Button>
      </div>
    </div>
  )
}
