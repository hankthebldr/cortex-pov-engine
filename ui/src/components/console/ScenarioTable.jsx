import React from 'react'
import PinButton from './PinButton.jsx'
import { formatAgo } from './useScenarioRunHistory.js'
import { isRunComplete } from './runStatus.js'
import { collectPlanes, collectTechniques } from './ScenarioGrid.jsx'

const PLANE_CHIP_CLASS = {
  EDR:        'chip--plane-edr',
  CDR:        'chip--plane-cdr',
  NDR:        'chip--plane-ndr',
  ITDR:       'chip--plane-itdr',
  CLOUD_APP:  'chip--plane-cdr',
  ANALYTICS:  'chip--plane-analytics',
}

/**
 * ScenarioTable — high-density tabular view of the scenario library.
 *
 * Eliminates the friction of scrolling through dozens of large cards during
 * customer triage, rapid testing, and demo flows. Each row provides a direct
 * 1-click Launch button, MITRE technique tags, run history status, and quick
 * shortcuts to Composer and details.
 */
export default function ScenarioTable({
  scenarios = [],
  selectedScenarioId = null,
  onSelectScenario = () => {},
  isPinned = () => false,
  onTogglePin = () => {},
  historyByScenario = null,
  onQuickLaunch = () => {},
  onOpenInComposer = () => {},
  launchingId = null,
  recentLaunchId = null,
  targetAgentName = '',
}) {
  if (scenarios.length === 0) {
    return (
      <div className="scenario-grid-empty" role="status">
        No scenarios match the current filter.
      </div>
    )
  }

  // Stable sort: pinned first, then original list order
  const ordered = scenarios
    .map((s, i) => ({ s, i, p: isPinned(s.scenario_id || s.id) ? 0 : 1 }))
    .sort((a, b) => a.p - b.p || a.i - b.i)
    .map((x) => x.s)

  return (
    <div className="scenario-table-container" tabIndex={0} aria-label="Scenarios table">
      <table className="scenario-table">
        <thead>
          <tr>
            <th className="th-action" scope="col">Quick Run</th>
            <th className="th-id" scope="col">Scenario ID</th>
            <th className="th-title" scope="col">Title</th>
            <th className="th-plane" scope="col">Plane</th>
            <th className="th-techniques" scope="col">Techniques</th>
            <th className="th-actor" scope="col">Anchor / Actor</th>
            <th className="th-history" scope="col">History</th>
            <th className="th-actions" scope="col">Actions</th>
          </tr>
        </thead>
        <tbody>
          {ordered.map((scenario) => {
            const id = scenario.scenario_id || scenario.id
            const isSelected = id === selectedScenarioId
            const pinned = isPinned(id)
            const planes = collectPlanes(scenario)
            const tids = collectTechniques(scenario).slice(0, 4)
            const actor = scenario.threat_report
              ? scenario.threat_report.split(/\s*[—\-]\s*/)[0]
              : (scenario.tags && scenario.tags[0]) || '—'
            const history = historyByScenario && historyByScenario.get
              ? historyByScenario.get(id)
              : null
            const isLaunching = launchingId === id
            const isJustLaunched = recentLaunchId === id

            return (
              <tr
                key={id}
                className={
                  'sc-table-row' +
                  (isSelected ? ' sc-table-row--selected' : '') +
                  (pinned ? ' sc-table-row--pinned' : '')
                }
                onClick={() => onSelectScenario(scenario)}
                data-testid={`scenario-row-${id}`}
              >
                {/* 1-Click Launch */}
                <td className="td-run" onClick={(e) => e.stopPropagation()}>
                  <button
                    type="button"
                    className={
                      'btn sc-run-btn' +
                      (isLaunching ? ' sc-run-btn--launching' : '') +
                      (isJustLaunched ? ' sc-run-btn--success' : '')
                    }
                    disabled={isLaunching}
                    onClick={(e) => {
                      e.stopPropagation()
                      onQuickLaunch(scenario, e)
                    }}
                    title={
                      targetAgentName
                        ? `1-Click Launch on ${targetAgentName}`
                        : '1-Click Launch on active agent'
                    }
                    data-testid={`quick-launch-${id}`}
                  >
                    {isLaunching ? (
                      <span className="sc-run-btn__spin">⟳ Launching…</span>
                    ) : isJustLaunched ? (
                      <span className="sc-run-btn__ok">✓ Started</span>
                    ) : (
                      <>
                        <span className="sc-run-btn__bolt" aria-hidden="true">⚡</span>
                        <span>Run</span>
                      </>
                    )}
                  </button>
                </td>

                {/* Scenario ID */}
                <td className="td-id">
                  <span className="mono sc-table-id">{id}</span>
                  {pinned && (
                    <span className="sc-id__pin-marker" title="Pinned scenario" aria-hidden="true">
                      {' '}◼
                    </span>
                  )}
                </td>

                {/* Title */}
                <td className="td-title">
                  <div className="sc-table-title" title={scenario.name}>
                    {scenario.name || '(unnamed scenario)'}
                  </div>
                  {scenario.description && (
                    <div className="sc-table-desc">{scenario.description}</div>
                  )}
                </td>

                {/* Plane */}
                <td className="td-plane">
                  <div className="sc-table-planes">
                    {planes.map((p) => (
                      <span key={p} className={'chip ' + (PLANE_CHIP_CLASS[p] || '')}>
                        {p}
                      </span>
                    ))}
                  </div>
                </td>

                {/* Techniques */}
                <td className="td-techniques">
                  <div className="sc-table-tids">
                    {tids.map((t) => (
                      <span key={t} className="chip-tid mono">{t}</span>
                    ))}
                    {tids.length === 0 && <span className="text-muted">—</span>}
                  </div>
                </td>

                {/* Anchor / Threat Actor */}
                <td className="td-actor">
                  <span className="sc-table-actor" title={scenario.threat_report || actor}>
                    {actor}
                  </span>
                </td>

                {/* History */}
                <td className="td-history">
                  {history && history.count > 0 ? (
                    <TableHistoryBadge history={history} />
                  ) : (
                    <span className="text-muted mono text-xs">never run</span>
                  )}
                </td>

                {/* Actions */}
                <td className="td-actions" onClick={(e) => e.stopPropagation()}>
                  <div className="sc-table-actions">
                    <button
                      type="button"
                      className="btn btn--icon-sm"
                      onClick={(e) => {
                        e.stopPropagation()
                        onOpenInComposer(scenario, e)
                      }}
                      title="Open and edit in Simulation Composer"
                      data-testid={`compose-${id}`}
                    >
                      <span aria-hidden="true">✏️</span>
                      <span className="sr-only">Composer</span>
                    </button>
                    <PinButton
                      pinned={pinned}
                      onToggle={() => onTogglePin(id)}
                      variant="card"
                    />
                    <button
                      type="button"
                      className="btn btn--xs"
                      onClick={(e) => {
                        e.stopPropagation()
                        onSelectScenario(scenario)
                      }}
                      title="Open details in drawer"
                    >
                      Inspect
                    </button>
                  </div>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function TableHistoryBadge({ history }) {
  const status = history.lastStatus || 'unknown'
  const isOk = isRunComplete(status)
  const statusClass = isOk ? 'is-ok'
    : status === 'failed' || status === 'aborted' ? 'is-fail'
    : status === 'running' ? 'is-run'
    : 'is-idle'

  return (
    <div
      className={'sc-history mono ' + statusClass}
      title={`Last status: ${status} · ${history.count} total runs`}
    >
      <span className="sc-history__dot" aria-hidden="true" />
      <span className="sc-history__count">{history.count}×</span>
      {history.lastRunAt > 0 && (
        <span className="sc-history__ago">{formatAgo(history.lastRunAt)}</span>
      )}
    </div>
  )
}
