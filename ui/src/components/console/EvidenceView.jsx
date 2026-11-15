import React, { useCallback, useEffect, useMemo, useState } from 'react'
import '../../styles/destinations/run-detail.css'
import useResultsData from './useResultsData.js'
import DetectionDrawer from './DetectionDrawer.jsx'
import DetectionTypeChip, { detectionTypeToken } from './DetectionTypeChip.jsx'
import MttdHistogram, { tidySeconds } from './MttdHistogram.jsx'
import { Button, StatusIcon } from './kit/index.js'
import AlsoRaised from './AlsoRaised.jsx'
import { isRunUnproven } from './runStatus.js'
import { detectionState, rowActions, MARK } from './detectionState.js'
import { outcomeClass, isBulkAttestable } from '../../outcomes.js'

/**
 * EvidenceView — Results › Detections: each expected detection of one run and
 * what became of it.
 *
 * Layout:
 *   ▸ one action: Validate all (with the count it will skip)
 *   ▸ four tiles: Coverage · Median MTTD · Analytics plane · Pending. Each is
 *     a label, one number, and at most one short line.
 *   ▸ the MTTD distribution (one line when there is none)
 *   ▸ the scorecard: each row's state is a StatusIcon and one word
 *     (detectionState.js), with its row actions as kit icon buttons
 *   ▸ "Also raised by Cortex (N)": the run's alerts that match no expected
 *     detection (AlsoRaised.jsx); absent when there are none
 *
 * The page head, the "Result of" bar, the downloads and the tenant evidence
 * panel belong to Results (ResultsView), which mounts this as one of its tabs.
 *
 * Props:
 *   pinnedRun  — { runId, scenarioId, status } the run on screen
 *   activeRun  — { runId, scenarioId, ... } used when no run is pinned
 *   lastRun    — fallback run when neither is given
 *   reviewed   — the run has been reviewed (default). A row nothing has
 *                judged reads Pending when it has, Not reviewed when not.
 */
export default function EvidenceView({
  activeRun,
  lastRun,
  pinnedRun = null,
  reviewed = true,
}) {
  // pinnedRun lets parents (e.g. a history-row click) override the
  // derived active/last selection — explicit picks always win.
  const targetRunId = pinnedRun?.runId || activeRun?.runId || lastRun?.runId || null

  // Same run-status truth the Storyline uses: a run that never executed has no
  // coverage to report, and "Validate all" on it would manufacture evidence.
  //
  // The status MUST come from the same run as `targetRunId`. It used to be
  // `pinnedRun?.status || activeRun?.status || lastRun?.status`, and the
  // provider's activeRun carried no status — so while run A was in flight the
  // page read the status of the PREVIOUS terminal run B, and a failed B
  // stamped "Coverage n/a — run failed — signal never generated" onto A.
  // activeRun only ever exists for a run in `running`, which is its fallback.
  const runStatus = pinnedRun
    ? pinnedRun.status || null
    : activeRun
      ? activeRun.status || 'running'
      : lastRun?.status || null
  const unproven = isRunUnproven(runStatus)

  const { rows, kpis, additionalDetections, loading, loaded, error, validate, refresh } =
    useResultsData(targetRunId)
  // A fetch that failed before any answer arrived is NOT an empty scorecard:
  // rendering it as one printed "Coverage 0 % · 0 / 0" and "no results yet —
  // ingestion typically takes 30–120s" for a SimCore that never answered.
  const loadFailed = !!error && !loaded
  const [selectedRowId, setSelectedRowId] = useState(null)

  // Reset drilldown selection when the underlying run changes.
  useEffect(() => { setSelectedRowId(null) }, [targetRunId])

  const selectedRow = useMemo(
    () => (selectedRowId == null ? null : rows.find((r) => r.id === selectedRowId) || null),
    [rows, selectedRowId],
  )

  const handleValidateAll = useCallback(async () => {
    // Attest every eligible row as detected (DC sweeps after a successful run):
    // the rows `PUT /validate` accepts, minus engine misses (outcomes.js::isBulkAttestable). A
    // step that never ran or a two-witness prevented row is skipped, and the
    // skip is counted next to the button.
    const pending = rows.filter((r) => isBulkAttestable(r) && r.id != null)
    for (const r of pending) {
      await validate(r.id, true, null)
    }
    refresh()
  }, [rows, validate, refresh])

  if (!targetRunId) {
    return (
      <div className="evidence evidence-empty">
        <p className="evidence-empty__desc">No run selected</p>
      </div>
    )
  }

  return (
    // The shell (.theme-console, AppShell.jsx) is theme-aware and sets
    // [data-theme] on the root, so the PANW token set (--ac/--s1/--tx/…)
    // resolves correctly here without pinning a local override.
    <div className="evidence">
      {loadFailed ? (
        <div className="evidence-empty" role="alert" data-testid="evidence-load-error">
          <h2 className="evidence-empty__title">Detections not loaded</h2>
          <p className="evidence-empty__desc mono">{error}</p>
          <Button verb="Refresh" onClick={refresh}>Retry</Button>
        </div>
      ) : (
        <>
      <div className="evidence-header__actions">
        <Button
          verb="Validate all"
          onClick={handleValidateAll}
          disabled={kpis.unvalidated === 0 || unproven}
          title={unproven ? `Run ${runStatus}: nothing to mark` : 'Mark every pending detection as detected'}
        >
          Validate all
          {kpis.unvalidated > 0 && (
            <span className="kbd">{kpis.unvalidated}</span>
          )}
        </Button>
        {kpis.skipped > 0 && (
          <span
            className="evidence-header__skipped"
            data-testid="validate-all-skipped"
            title="Not marked: the step never ran, it is a prevention, or the review found a miss. Mark those one at a time."
          >
            {kpis.skipped} skipped
          </span>
        )}
        {loading && <span className="evidence-header__sync">Syncing…</span>}
      </div>
      {error && (
        <div className="evidence-stale" role="status" data-testid="evidence-refresh-error" title={error}>
          Refresh failed · showing the last read
        </div>
      )}
      <KpiRow kpis={kpis} unproven={unproven} runStatus={runStatus} />

      <MttdHistogram rows={rows} />

      <Scorecard
        rows={rows}
        loading={loading}
        reviewed={reviewed}
        selectedRowId={selectedRowId}
        onSelectRow={setSelectedRowId}
        onValidate={validate}
      />

      <AlsoRaised alerts={additionalDetections} />

      <DetectionDrawer
        row={selectedRow}
        reviewed={reviewed}
        open={!!selectedRow}
        onClose={() => setSelectedRowId(null)}
        onValidate={(id, observed, notes) => {
          validate(id, observed, notes)
        }}
      />
        </>
      )}
    </div>
  )
}

/* ─── KPI row ─────────────────────────────────────────────────────────── */

const NOT_COUNTED = 'Not counted: the step never ran, or its detector is still soaking'

/** Coverage's one line: how many were detected, and how many were left out. */
function coverageLine(kpis) {
  const left = kpis.excluded > 0 ? `${kpis.excluded} not counted` : null
  return [`${kpis.detected} of ${kpis.total} detected`, left].filter(Boolean).join(' · ')
}

function KpiRow({ kpis, unproven = false, runStatus = null }) {
  return (
    <div className="kpi-row">
      {unproven ? (
        <Kpi
          label="Coverage"
          value="—"
          meta={`Run ${runStatus}`}
          title="The run did not finish, so no detection was put to the test"
          tone="tx"
        />
      ) : kpis.coverage == null ? (
        <Kpi
          label="Coverage"
          value="—"
          meta={kpis.coverageUnknown
            ? 'Count not sent'
            : kpis.seeded > 0
              ? `${kpis.excluded} not counted`
              : 'No detections expected'}
          title={kpis.coverageUnknown ? 'This SimCore sent no coverage count' : kpis.seeded > 0 ? NOT_COUNTED : undefined}
          tone="tx"
        />
      ) : (
        <Kpi
          label="Coverage"
          value={kpis.coverage}
          suffix="%"
          valueClass="kpi__value--detected"
          meta={coverageLine(kpis)}
          title={kpis.excluded > 0 ? NOT_COUNTED : undefined}
          tone="ac"
        />
      )}
      <Kpi
        label="Median MTTD"
        value={kpis.median != null ? tidySeconds(kpis.median) : '—'}
        suffix={kpis.median != null ? 's' : ''}
        valueClass="kpi__value--signal"
        title="Median time to detect, across detected rows"
        tone={kpis.median != null ? 'ac' : 'tx'}
      />
      {kpis.stitchDenominator > 0 ? (
        <Kpi
          label="Analytics plane"
          value={kpis.stitched}
          suffix={` / ${kpis.stitchDenominator}`}
          title="Analytics-plane detections observed, of those that could have fired"
          tone="info"
        />
      ) : (
        <Kpi
          label="Analytics plane"
          value="—"
          meta="None on this run"
          tone="tx"
        />
      )}
      <Kpi
        label="Pending"
        value={kpis.pending}
        valueClass="kpi__value--pending"
        title="Detections nothing has settled yet"
        tone={kpis.pending > 0 ? 'warn' : 'tx'}
      />
    </div>
  )
}

/** One tile: a label, one number, and at most one short line under it. */
function Kpi({ label, value, suffix = '', meta = '', title, valueClass = '', tone = 'tx' }) {
  return (
    <div className={`kpi kpi--${tone}`} title={title}>
      <div className="kpi__label">{label}</div>
      <div className={'kpi__value ' + valueClass}>
        {value}
        {suffix && (
          <span className="kpi__suffix">{suffix}</span>
        )}
      </div>
      {meta && <div className="kpi__meta">{meta}</div>}
    </div>
  )
}

/* ─── Scorecard ────────────────────────────────────────────────────────── */

function Scorecard({ rows, loading, reviewed, selectedRowId, onSelectRow, onValidate }) {
  if (rows.length === 0 && !loading) {
    return (
      <div className="scorecard">
        <div className="scorecard__head">
          <div>TID</div>
          <div>Plane</div>
          <div>Alert</div>
          <div className="scorecard__head-mttd">MTTD</div>
          <div>Alert ID</div>
          <div>Source</div>
          <div>State</div>
        </div>
        <div className="scorecard-empty">No expected detections</div>
      </div>
    )
  }

  return (
    <div className="scorecard">
      <div className="scorecard__head">
        <div>TID</div>
        <div>Plane</div>
        <div>Alert</div>
        <div className="scorecard__head-mttd">MTTD</div>
        <div>Alert ID</div>
        <div>Source</div>
        <div>State</div>
      </div>
      {rows.map((r, i) => (
        <ScorecardRow
          key={r.id ?? i}
          row={r}
          reviewed={reviewed}
          isSelected={r.id != null && r.id === selectedRowId}
          onSelect={() => r.id != null && onSelectRow(r.id)}
          onValidate={onValidate}
        />
      ))}
    </div>
  )
}

function ScorecardRow({ row, reviewed, isSelected, onSelect, onValidate }) {
  // What became of the detection, from the server's outcome axis
  // (outcomes.js::resultStatus), as a StatusIcon and one word.
  const state = detectionState(row, { reviewed })
  // The hand marks the API takes for this row (detectionState.js::rowActions).
  const actions = rowActions(row)

  // Inline-button clicks shouldn't propagate to the row click (avoid
  // double-firing validate + selecting the row at the same time).
  const stop = (fn) => (e) => { e.stopPropagation(); fn() }

  return (
    <div
      className={'scorecard__row' + (isSelected ? ' scorecard__row--selected' : '')}
      data-status={outcomeClass(row.status)}
      data-state={state.key}
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect() } }}
      aria-label={`Open detection detail for ${row.tid}`}
    >
      <div className="scorecard__tid">{row.tid}</div>
      <div className="scorecard__plane">
        <span>{row.plane}</span>
        {row.detectionType && (
          detectionTypeToken(row.detectionType)
            ? <DetectionTypeChip type={row.detectionType} />
            : <span>{` · ${row.detectionType}`}</span>
        )}
      </div>
      <div className="scorecard__alert">{row.alert}</div>
      <div className="scorecard__mttd">
        {row.mttd != null ? formatMttd(row.mttd) : '—'}
      </div>
      <div className="scorecard__id">{row.alertId || '—'}</div>
      <div className="scorecard__source">{row.alertSource || '—'}</div>
      <div className="scorecard__status-cell">
        <span className="scorecard__state" title={state.title}>
          <StatusIcon status={state.status} word={state.word} />
        </span>
        {actions.length > 0 && (
          <div className="scorecard__row-actions">
            {actions.map((verb) => (
              <Button key={verb} verb={verb} iconOnly onClick={stop(() => onValidate(row.id, MARK[verb], null))} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function formatMttd(seconds) {
  if (seconds == null) return '—'
  if (seconds < 60) return `0:${String(Math.floor(seconds)).padStart(2, '0')}`
  const mm = Math.floor(seconds / 60)
  const ss = Math.floor(seconds % 60)
  return `${mm}:${String(ss).padStart(2, '0')}`
}
