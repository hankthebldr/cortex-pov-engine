/**
 * RunTechniquesView — the Techniques tab on a run.
 *
 * A kill-chain strip (14 ATT&CK tactics in order, empty stages dimmed) and a
 * per-technique verdict table, built from this run's `Result` rows, the run's
 * `tc_verdict`, and the TTP cards the rows reference. Nothing from `povdata/`
 * and nothing from `/api/mitre/coverage` (see `runTechniques.js`).
 *
 * A count never renders before its fetch resolves; a failed fetch says
 * "could not load", never `0`. A failed run never shows a miss.
 */
import React, { useEffect, useMemo, useState } from 'react'
import { getResultsForRun, getRun, getTtp } from '../../api/client.js'
import { buildTechniqueModel } from './runTechniques.js'
import { isRunUnproven } from './runStatus.js'

const EVIDENCE_SENTENCE =
  'Evidence is this SimCore’s Result rows; a detection counts only once reconciled from the tenant or validated by the DC. Authored is not proven.'

const TONE_LABEL = { hit: 'detected', miss: 'missed', pending: 'pending', excluded: 'not reached', empty: 'no technique' }

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

  if (!runId) return <div className="run-tech__empty mono">no run selected</div>
  if (error) return <div className="run-tech__empty mono" data-testid="technique-error">could not load results: {error}</div>
  if (!model) return <div className="run-tech__empty mono" role="status">loading results…</div>

  const unproven = isRunUnproven(model.runStatus)
  const verdict = model.tcVerdict || 'not scored'
  const verdictKey = model.tcVerdict || 'unscored'

  return (
    <div className="run-tech" data-testid="run-techniques" data-proven={model.proven ? 'true' : 'false'}>
      <div className="run-tech__head">
        <div className="run-tech__verdict">
          <span className="run-tech__k">Run verdict</span>
          <span className={`chip run-tech__verdict-chip run-tech__verdict-chip--${verdictKey}`} data-testid="technique-verdict">{verdict}</span>
          {model.tcVerdictDetail?.reason && <span className="mono run-tech__detail">{String(model.tcVerdictDetail.reason)}</span>}
        </div>
        <div className="run-tech__totals mono" data-testid="technique-totals">
          {model.totals.hits} of {model.totals.denominator} rows detected
          {model.totals.excluded ? ` · ${model.totals.excluded} not reached` : ''}
          {' · '}{model.techniques.length} technique{model.techniques.length === 1 ? '' : 's'} placed
          {model.unplaced.length ? ` · ${model.unplaced.length} unplaced` : ''}
          {' · tenant-verified: 0'}
        </div>
        <p className="run-tech__sentence">{EVIDENCE_SENTENCE}</p>
        {unproven && (
          <p className="run-tech__unproven" data-testid="technique-unproven">
            This run {model.runStatus}: an un-fired detection below is <b>pending</b>, not missed — the signal may never have been generated.
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
                {t.excluded ? <span className="run-tech__muted"> (+{t.excluded} not reached)</span> : null}
              </td>
              <td className="mono">{summary(t.outcomes)}</td>
              <td className="mono">{summary(t.kpi)}</td>
            </tr>
          ))}
          {model.techniques.length === 0 && (
            <tr><td colSpan={7} className="run-tech__empty mono">no technique placed — {model.totals.rows} result row{model.totals.rows === 1 ? '' : 's'} on this run</td></tr>
          )}
        </tbody>
      </table>

      {model.unplaced.length > 0 && (
        <div className="run-tech__unplaced" data-testid="technique-unplaced">
          <div className="run-tech__k">Unplaced — not drawn on the strip, with the reason</div>
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
        <span className="mono">{TONE_LABEL.hit} · {TONE_LABEL.miss} · {TONE_LABEL.pending} tones are this SimCore's rows, not the tenant's.</span>
        <button type="button" className="btn btn--xs" onClick={onOpenEvidence}>Open the evidence ▸</button>
      </div>
    </div>
  )
}
