import React, { useEffect, useMemo, useRef, useState } from 'react'
import { downloadExecutionsReport, getExecutionsReport } from '../../api/reports.js'
import { Button, StatusIcon } from './kit/index.js'
import { planeTint } from './povdata/planes.js'
import { absoluteTime, relativeTime } from './runRows.js'
import { saveBlob } from './useLaunchScenario.js'
import {
  COLUMNS, DEFAULT_SORT, GROUPINGS, NOT_REVIEWED_WORD, RANGES, ariaSort, checkReport, clip,
  nextSort, outcomeChips, rowsOf, runStatusChips, runStatusMeta, sinceFor, sortRows, stepChips,
} from './reportsModel.js'

void React

/**
 * ReportsExecutions — Reports › Executions: everything fired on this engine.
 *
 * One read of GET /api/reports/executions per filter change. The totals strip,
 * the table and its three groupings (by scenario, by technique, by test case)
 * all draw that one response; the downloads ask the same route for the same
 * filters.
 *
 * What it never draws: a detection nobody reviewed as Missed, a scenario that
 * never ran as a row of zeros, an empty engine as a table, or a failed read as
 * an empty one.
 */
const errText = (err) => (err && (err.message || String(err))) || 'no reason given'
const fmt = (n) => (typeof n === 'number' ? n.toLocaleString('en-US') : '—')
const scenarioHref = (id) => `#/library?open=${encodeURIComponent(id)}`

export default function ReportsExecutions({ onNavigate = () => {} }) {
  const [range, setRange] = useState('all')
  const [plane, setPlane] = useState('')
  const [status, setStatus] = useState('')
  const [agent, setAgent] = useState('')
  const [includeDrafts, setIncludeDrafts] = useState(false)
  const [grouping, setGrouping] = useState('scenario')
  const [sort, setSort] = useState(DEFAULT_SORT)
  const [state, setState] = useState({ loading: true, error: null, report: null })
  const [downloadError, setDownloadError] = useState(null)
  const seq = useRef(0)

  const filters = useMemo(
    () => ({ range, plane, status, agent, includeDrafts }),
    [range, plane, status, agent, includeDrafts],
  )
  const query = (f) => ({
    since: sinceFor(f.range), plane: f.plane, status: f.status, agent: f.agent, includeDrafts: f.includeDrafts,
  })

  useEffect(() => {
    const mine = ++seq.current
    setState((s) => ({ ...s, loading: true }))
    getExecutionsReport(query(filters))
      .then((data) => {
        if (mine === seq.current) setState({ loading: false, error: null, report: checkReport(data) })
      })
      .catch((err) => {
        // A failed read is not an empty engine: the last table goes with it.
        if (mine === seq.current) setState({ loading: false, error: errText(err), report: null })
      })
  }, [filters])

  const download = async (format) => {
    setDownloadError(null)
    try {
      const { blob, filename } = await downloadExecutionsReport(query(filters), format)
      saveBlob(blob, filename)
    } catch (err) {
      setDownloadError(errText(err))
    }
  }

  const { report, loading, error } = state
  const options = report?.filter_options || { planes: [], statuses: [], agents: [] }
  // Nothing to save: no report, or one with no run in it.
  const nothing = !report || report.empty

  return (
    <section className="rep" data-testid="reports-executions" aria-busy={loading}>
      <div className="dash__filters" role="group" aria-label="Filters">
        <Segmented label="Time range" options={RANGES} value={range} onChange={setRange} testId="rep-range" />
        <FilterSelect label="Plane" all="All planes" value={plane} onChange={setPlane} testId="rep-filter-plane"
          options={options.planes.map((p) => [p, p])} />
        <FilterSelect label="Status" all="Any status" value={status} onChange={setStatus} testId="rep-filter-status"
          options={options.statuses.map((s) => [s, runStatusMeta(s).word])} />
        <FilterSelect label="Agent" all="Any agent" value={agent} onChange={setAgent} testId="rep-filter-agent"
          options={options.agents.map((a) => [a, a])} />
        <button
          type="button"
          className={'rep-toggle' + (includeDrafts ? ' rep-toggle--on' : '')}
          aria-pressed={includeDrafts}
          onClick={() => setIncludeDrafts((v) => !v)}
          title="Count runs of Build a chain drafts"
          data-testid="rep-filter-drafts"
        >
          Include drafts
        </button>
        <span className="dash__spacer" />
        <span className="rep__downloads">
          <Button verb="Download" aria-label="Download CSV" disabled={nothing} onClick={() => download('csv')}
            data-testid="rep-download-csv">CSV</Button>
          <Button verb="Download" aria-label="Download JSON" disabled={nothing} onClick={() => download('json')}
            data-testid="rep-download-json">JSON</Button>
        </span>
      </div>

      {downloadError && (
        <p className="dash__notice" role="alert" data-testid="rep-download-error">
          <StatusIcon status="down" word="Not saved" />
          <span>{downloadError}</span>
        </p>
      )}

      {error ? (
        <p className="dash__notice" role="alert" data-testid="rep-error">
          <StatusIcon status="down" word="Not loaded" />
          <span>{error}</span>
        </p>
      ) : !report ? (
        <p className="dash__empty" role="status">Loading…</p>
      ) : report.empty ? (
        <Empty report={report} onNavigate={onNavigate} />
      ) : (
        <>
          <Totals totals={report.totals} />
          <div className="rep__bar">
            <Segmented label="Group by" options={GROUPINGS} value={grouping} testId="rep-group"
              onChange={(g) => { setGrouping(g); setSort(DEFAULT_SORT) }} />
          </div>
          <ReportTable report={report} grouping={grouping} sort={sort} onSort={(id) => setSort((s) => nextSort(s, id))}
            onNavigate={onNavigate} />
        </>
      )}
    </section>
  )
}

/* ─── Controls ─────────────────────────────────────────────────────────── */

function Segmented({ label, options, value, onChange, testId }) {
  return (
    <div className="rep-seg" role="group" aria-label={label} data-testid={testId}>
      {options.map(([id, text]) => (
        <button
          key={id}
          type="button"
          className={'rep-seg__opt' + (value === id ? ' rep-seg__opt--on' : '')}
          aria-pressed={value === id}
          onClick={() => onChange(id)}
        >
          {text}
        </button>
      ))}
    </div>
  )
}

function FilterSelect({ label, all, value, options, onChange, testId }) {
  return (
    <label className="dash__filter">
      {label}
      <select
        className={'dash__select' + (value ? ' dash__select--on' : '')}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        data-testid={testId}
      >
        <option value="">{all}</option>
        {/* A filter that is on stays listed even when nothing matches it. */}
        {value && !options.some(([v]) => v === value) && <option value={value}>{value}</option>}
        {options.map(([v, text]) => <option key={v} value={v}>{text}</option>)}
      </select>
    </label>
  )
}

/** A count beside its state: an icon and one word, never colour alone. */
function Chip({ chip }) {
  return (
    <span className="rep-chip" title={chip.title} data-testid={`rep-chip-${chip.key}`}>
      <span className="mono rep-chip__n">{fmt(chip.n)}</span>
      <StatusIcon status={chip.status} word={chip.word} />
    </span>
  )
}

function Chips({ chips }) {
  return <span className="rep-chips">{chips.map((c) => <Chip key={c.key} chip={c} />)}</span>
}

/* ─── Empty ────────────────────────────────────────────────────────────── */

function Empty({ report, onNavigate }) {
  if (report.engine_runs > 0) {
    return <p className="dash__empty" role="status" data-testid="rep-no-match">No runs match these filters.</p>
  }
  return (
    <div className="rep__empty" role="status" data-testid="rep-empty">
      <span>No runs yet.</span>
      <Button verb="Run" onClick={() => onNavigate('library')} />
    </div>
  )
}

/* ─── Totals ───────────────────────────────────────────────────────────── */

function Totals({ totals }) {
  const { runs, scenarios, steps, detections } = totals
  return (
    <div className="pov-stats dash__stats rep-stats" data-testid="rep-totals">
      <Stat k="Runs" n={runs.total} testId="rep-total-runs">
        <Chips chips={runStatusChips(runs)} />
        <span className="dash__stat-sub" title="Runs reviewed against the tenant">{fmt(runs.reviewed)} reviewed</span>
      </Stat>
      <Stat k="Scenarios" n={scenarios.exercised} testId="rep-total-scenarios">
        <span className="dash__stat-sub" title="Library scenarios with no run in this view" data-testid="rep-not-run">
          {fmt(scenarios.corpus_not_run)} of {fmt(scenarios.corpus)} not run
        </span>
      </Stat>
      <Stat k="Techniques" n={totals.techniques_exercised} testId="rep-total-techniques" />
      <Stat k="Test cases" n={totals.test_cases_touched} testId="rep-total-test-cases" />
      <Stat k="Steps" n={steps.recorded} testId="rep-total-steps">
        <Chips chips={stepChips(steps)} />
      </Stat>
      <Stat k="Detections" n={detections.expected} testId="rep-total-detections">
        <Chips chips={outcomeChips(detections)} />
      </Stat>
    </div>
  )
}

function Stat({ k, n, testId, children }) {
  return (
    <div className="pov-stat" data-testid={testId}>
      <span className="pov-stat__n mono">{fmt(n)}</span>
      <span className="dash__stat-k">{k}</span>
      {children}
    </div>
  )
}

/* ─── Table ────────────────────────────────────────────────────────────── */

function ReportTable({ report, grouping, sort, onSort, onNavigate }) {
  const columns = COLUMNS[grouping]
  const { rows, keyOf } = rowsOf(report, grouping)
  const sorted = useMemo(
    () => sortRows(rows, columns.find((c) => c.id === sort.id), sort.dir),
    [rows, columns, sort],
  )
  const label = GROUPINGS.find(([id]) => id === grouping)[1]
  return (
    <div className="dash__card dash__scroll rep-scroll">
      <table className={`rep-table rep-table--${grouping}`} data-testid="rep-table">
        <caption className="sr-only">Executions, {label.toLowerCase()}</caption>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.id} scope="col" aria-sort={ariaSort(c, sort)} title={c.title}
                className={`rep-cell--${c.id}`}>
                {c.sort ? (
                  <button
                    type="button"
                    className={'rep-table__sort' + (sort.id === c.id ? ` rep-table__sort--${sort.dir}` : '')}
                    onClick={() => onSort(c.id)}
                  >
                    {c.label}
                  </button>
                ) : c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => (
            <tr key={keyOf(row)} data-testid={`rep-row-${keyOf(row)}`}>
              {columns.map((c) => <Cell key={c.id} column={c.id} row={row} onNavigate={onNavigate} />)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** One line that ends in an ellipsis when its column is narrower; the tooltip has it whole. */
function Fit({ text, className = '' }) {
  return <span className={`rep-fit ${className}`.trim()} title={text || undefined}>{text || ''}</span>
}

/**
 * A short list: the first few, then "+N" with the rest in its tooltip. `fit`
 * keeps it on one line with an ellipsis, and the tooltip always has the list.
 */
function Clip({ list, shown = 2, render = (v) => v, fit = false, mono = true }) {
  const c = clip(list, shown)
  if (!c.all.length) return <span className="rep-quiet">—</span>
  return (
    <span className={'rep-clip' + (fit ? ' rep-fit' : '')} title={fit || c.more ? c.all.join(', ') : undefined}>
      {c.shown.map((v, i) => (
        <React.Fragment key={v}>
          {i > 0 && ' '}
          <span className={(mono ? 'mono ' : '') + 'rep-clip__item'}>{render(v)}{i < c.shown.length - 1 && ','}</span>
        </React.Fragment>
      ))}
      {c.more > 0 && <span className="rep-quiet rep-clip__item"> +{c.more}</span>}
    </span>
  )
}

/** A time as "3h ago"; the tooltip has the instant, and `also` adds a second line to it. */
function When({ value, column, also }) {
  const title = [absoluteTime(value), also].filter(Boolean).join('\n')
  return (
    <td className={`rep-cell--${column}`}>
      <span className="mono" title={title || undefined}>{relativeTime(value)}</span>
    </td>
  )
}

function Outcomes({ outcomes }) {
  if (!outcomes) return <StatusIcon status="unknown" word={NOT_REVIEWED_WORD} />
  const chips = outcomeChips(outcomes)
  if (!chips.length) return <span className="rep-quiet" title="No detection expected">—</span>
  return <Chips chips={chips} />
}

function Cell({ column, row, onNavigate }) {
  switch (column) {
    case 'scenario':
      return (
        <th scope="row" className="rep-cell--scenario">
          <a className="rep-table__id rep-fit mono" href={scenarioHref(row.scenario_id)} title={row.scenario_id}>
            {row.scenario_id}
          </a>
          <Fit text={row.name} className="rep-table__sub" />
        </th>
      )
    case 'technique':
      return (
        <th scope="row" className="rep-cell--technique">
          <span className="rep-table__id mono">{row.technique}</span>
          <Fit text={row.name} className="rep-table__sub" />
        </th>
      )
    case 'tc':
      return (
        <th scope="row" className="rep-cell--tc">
          <span className="rep-table__id mono">{row.tc_ref}</span>
          <Fit text={row.name} className="rep-table__sub" />
        </th>
      )
    case 'plane':
      return (
        <td className="rep-cell--plane">
          {row.plane ? (
            <span className="rep-plane" title={row.plane}>
              <span className="dash-grid__tint" style={{ background: planeTint(row.plane) }} aria-hidden="true" />
              <span className="rep-fit mono">{row.plane}</span>
            </span>
          ) : <span className="rep-quiet">—</span>}
        </td>
      )
    case 'techniques':
      return <td className="rep-cell--techniques"><Clip list={row.techniques} /></td>
    case 'uctc':
      return (
        <td className="rep-cell--uctc">
          <Fit text={row.uc_ref || '—'} className="mono" />
          <span className="rep-table__sub"><Clip list={row.tc_refs} shown={1} fit /></span>
        </td>
      )
    case 'uc':
      return <td className="rep-cell--uc"><Fit text={row.uc_ref || '—'} className="mono" /></td>
    case 'sources':
      if (row.detection_sources === null) {
        return <td className="rep-cell--sources"><StatusIcon status="unknown" word="Can't check" /></td>
      }
      return (
        <td className="rep-cell--sources">
          <Clip list={row.detection_sources.map((s) => s.label)} shown={1} fit mono={false} />
        </td>
      )
    case 'scenarios':
      return (
        <td className="rep-cell--scenarios">
          <Clip list={row.scenarios} render={(id) => <a className="rep-table__link" href={scenarioHref(id)}>{id}</a>} />
        </td>
      )
    case 'runs': {
      const by = row.runs_by_status
        ? Object.entries(row.runs_by_status).map(([s, n]) => `${n} ${runStatusMeta(s).word.toLowerCase()}`).join(', ')
        : undefined
      return <td className="rep-cell--runs mono" title={by}>{fmt(row.runs)}</td>
    }
    case 'first':
      return <When value={row.first_run_at} column="first" />
    case 'last':
      // The First run column gives way on a narrow window; the fact stays here.
      return (
        <When value={row.last_run_at} column="last"
          also={row.first_run_at ? `First run ${absoluteTime(row.first_run_at)}` : null} />
      )
    case 'status': {
      const meta = runStatusMeta(row.last_status)
      return <td className="rep-cell--status" title={meta.word}><StatusIcon status={meta.status} word={meta.word} /></td>
    }
    case 'reviewed':
      return (
        <td className="rep-cell--reviewed mono">
          {fmt(row.reviewed_runs ?? row.reviewed_scenarios)} of {fmt(row.scenarios ? row.scenarios.length : row.runs)}
        </td>
      )
    case 'outcomes':
      return <td className="rep-cell--outcomes"><Outcomes outcomes={row.outcomes} /></td>
    case 'action':
      return (
        <td className="rep-cell--action">
          {row.last_reviewed_run_id ? (
            <Button verb="Open results" aria-label={`Open results of ${row.scenario_id}`}
              onClick={() => onNavigate('proof', { run: row.last_reviewed_run_id })} />
          ) : (
            <Button verb="Open run" aria-label={`Open run of ${row.scenario_id}`}
              onClick={() => onNavigate('runs', { run: row.last_run_id })} />
          )}
        </td>
      )
    default:
      return <td className={`rep-cell--${column}`} />
  }
}
