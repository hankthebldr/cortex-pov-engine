import React from 'react'
import PinButton from './PinButton.jsx'
import { formatAgo } from './useScenarioRunHistory.js'

const PLANE_CHIP_CLASS = {
  EDR:        'chip--plane-edr',
  CDR:        'chip--plane-cdr',
  NDR:        'chip--plane-ndr',
  ITDR:       'chip--plane-itdr',
  CLOUD_APP:  'chip--plane-cdr',
  ANALYTICS:  'chip--plane-analytics',
}

const DIFFICULTY_LABEL = {
  basic:        'Basic',
  intermediate: 'Intermediate',
  advanced:     'Advanced',
  evasive:      'Evasive',
}

/**
 * ScenarioCard — single scenario card for the operations grid.
 */
function ScenarioCard({ scenario, isSelected, isPinned, onSelect, onTogglePin, history = null }) {
  const id      = scenario.scenario_id || scenario.id
  const planes  = collectPlanes(scenario)
  const tids    = collectTechniques(scenario).slice(0, 6)
  const actor   = scenario.threat_report
    ? scenario.threat_report.split(/\s*[—\-]\s*/)[0]
    : (scenario.tags && scenario.tags[0]) || '—'
  const difficulty = scenario.difficulty || scenario.tags?.find?.((t) => DIFFICULTY_LABEL[t]) || null

  return (
    <article
      className={
        'scenario-card' +
        (isSelected ? ' scenario-card--selected' : '') +
        (isPinned   ? ' scenario-card--pinned'   : '')
      }
      onClick={() => onSelect(scenario)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onSelect(scenario) }}
    >
      <div className="scenario-card__corner">
        <PinButton
          pinned={isPinned}
          onToggle={() => onTogglePin(id)}
          variant="card"
        />
      </div>

      <div className="sc-id">
        {id}
        {isPinned && <span className="sc-id__pin-marker" aria-hidden="true">◼</span>}
      </div>
      <div className="sc-title">{scenario.name || '(unnamed scenario)'}</div>

      <div className="sc-meta">
        {planes.map((p) => (
          <span
            key={p}
            className={'chip ' + (PLANE_CHIP_CLASS[p] || '')}
          >{p}</span>
        ))}
        {difficulty && (
          <span className="chip">{DIFFICULTY_LABEL[difficulty] || difficulty}</span>
        )}
      </div>

      {tids.length > 0 && (
        <div className="sc-tids">
          {tids.map((t) => <span key={t}>{t}</span>)}
        </div>
      )}

      <div className="sc-footer">
        <div className="sc-actor">
          Anchor · <strong>{actor}</strong>
        </div>
        <div className="sc-planes">
          {['EDR', 'CDR', 'NDR', 'ANALYTICS'].map((p) => (
            <div
              key={p}
              className={'plane-dot' + (planes.includes(p) ? ' plane-dot--on' : '')}
              title={`${p}${planes.includes(p) ? ' active' : ' idle'}`}
            />
          ))}
        </div>
      </div>

      {history && history.count > 0 && (
        <ScenarioHistoryBadge history={history} />
      )}
    </article>
  )
}

/**
 * ScenarioHistoryBadge — small footer strip surfacing whether this
 * scenario has been run before. Helps DCs prioritize untouched
 * scenarios during a POV.
 */
function ScenarioHistoryBadge({ history }) {
  const status = history.lastStatus || 'unknown'
  const statusClass = status === 'completed' ? 'is-ok'
    : status === 'failed'  ? 'is-fail'
    : status === 'running' ? 'is-run'
    : 'is-idle'
  return (
    <div className={'sc-history mono ' + statusClass}
         title={`Last status: ${status} · ${history.count} total run${history.count === 1 ? '' : 's'}`}>
      <span className="sc-history__dot" aria-hidden="true" />
      <span className="sc-history__count">{history.count}× run</span>
      {history.lastRunAt > 0 && (
        <span className="sc-history__ago">· {formatAgo(history.lastRunAt)}</span>
      )}
    </div>
  )
}

/**
 * ScenarioGrid — grid of scenario cards. Pinned scenarios render first.
 */
export default function ScenarioGrid({
  scenarios = [],
  selectedScenarioId = null,
  onSelectScenario = () => {},
  isPinned = () => false,
  onTogglePin = () => {},
  historyByScenario = null, // Map<scenario_id, { count, lastRunAt, lastStatus }>
}) {
  if (scenarios.length === 0) {
    return (
      <div style={{
        padding: 48,
        textAlign: 'center',
        color: 'var(--c-text-muted)',
        fontFamily: 'var(--font-mono)',
        fontSize: 12,
        letterSpacing: '0.04em',
      }}>
        No scenarios match the current filter.
      </div>
    )
  }

  // Stable sort: pinned first, otherwise preserve API order.
  const ordered = scenarios
    .map((s, i) => ({ s, i, p: isPinned(s.scenario_id || s.id) ? 0 : 1 }))
    .sort((a, b) => a.p - b.p || a.i - b.i)
    .map((x) => x.s)

  return (
    <div className="scenario-grid">
      {ordered.map((s) => {
        const id = s.scenario_id || s.id
        const history = historyByScenario && historyByScenario.get
          ? historyByScenario.get(id)
          : null
        return (
          <ScenarioCard
            key={id}
            scenario={s}
            isSelected={id === selectedScenarioId}
            isPinned={isPinned(id)}
            onSelect={onSelectScenario}
            onTogglePin={onTogglePin}
            history={history}
          />
        )
      })}
    </div>
  )
}

// ─── helpers ─────────────────────────────────────────────────────────────────

function collectPlanes(scenario) {
  const result = new Set()
  const primary = (scenario.plane || '').toUpperCase()
  if (primary) result.add(primary)
  // Walk steps for any cross-plane expected detections
  ;(scenario.steps || []).forEach((step) => {
    ;(step.expected_detections || []).forEach((d) => {
      const p = (d.plane || '').toUpperCase()
      if (p) result.add(p)
    })
  })
  return Array.from(result).filter(Boolean)
}

function collectTechniques(scenario) {
  const tids = new Set()
  if (scenario.mitre_technique) tids.add(scenario.mitre_technique)
  ;(scenario.additional_techniques || []).forEach((t) => {
    if (t.technique) tids.add(t.technique)
  })
  ;(scenario.steps || []).forEach((step) => {
    if (step.mitre_technique) tids.add(step.mitre_technique)
  })
  return Array.from(tids)
}
