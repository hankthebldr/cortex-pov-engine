import React from 'react'
import PinButton from './PinButton.jsx'
import { formatAgo } from './useScenarioRunHistory.js'
import { isRunComplete } from './runStatus.js'
import DoorChip from './doors/DoorChip.jsx'
import Button from './kit/Button.jsx'

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
 * ScenarioCard — the v26 card anatomy (§4.1): a header (id, technique), the
 * title, a tag row, and a footer that carries the verbs.
 *
 * The body is a button that opens the scenario's sheet. The footer's Run goes
 * to the launch step with the scenario loaded; Customize opens Build a chain
 * with its chain loaded. Neither opens the sheet. Every tag is read from the
 * scenario SimCore returned: its planes, its door readiness and its real
 * use-case / test-case refs from the index.
 */
function ScenarioCard({
  scenario, isSelected, isPinned, isFirst, onSelect, onTogglePin, onRun = null, onCustomize = null,
  history = null, readiness, doorRows = null, doorAgentId = null, doorError = null,
}) {
  const id      = scenario.scenario_id || scenario.id
  const name    = scenario.name || '(unnamed scenario)'
  const planes  = collectPlanes(scenario)
  const technique = scenario.mitre_technique || collectTechniques(scenario)[0] || null
  const difficulty = scenario.difficulty || scenario.tags?.find?.((t) => DIFFICULTY_LABEL[t]) || null
  const steps = Array.isArray(scenario.steps) ? scenario.steps : []
  const detections = steps.reduce((n, s) => n + (s.expected_detections || []).length, 0)

  return (
    <article
      data-tour-id={isFirst ? 'scenario-card-first' : undefined}
      data-testid={`scenario-card-${id}`}
      className={
        'scenario-card' +
        (isSelected ? ' scenario-card--selected' : '') +
        (isPinned   ? ' scenario-card--pinned'   : '')
      }
    >
      <div className="scenario-card__corner">
        <PinButton
          pinned={isPinned}
          onToggle={() => onTogglePin(id)}
          variant="card"
        />
      </div>

      <button
        type="button"
        className="scenario-card__body"
        aria-label={`Open ${name}`}
        aria-pressed={isSelected}
        onClick={() => onSelect(scenario)}
      >
        <span className="sc-head" data-testid="scenario-card-head">
          <span className="sc-id">
            {id}
            {isPinned && <span className="sc-id__pin-marker" aria-hidden="true">◼</span>}
          </span>
          {technique && <span className="sc-technique mono">{technique}</span>}
        </span>
        <span className="sc-title">{name}</span>

      <span className="sc-meta sc-tags" data-testid="scenario-card-tags">
        {planes.map((p) => (
          <span
            key={p}
            className={'chip ' + (PLANE_CHIP_CLASS[p] || '')}
          >{p}</span>
        ))}
        {difficulty && (
          <span className="chip">{DIFFICULTY_LABEL[difficulty] || difficulty}</span>
        )}
        <DoorChip scenarioId={id} readiness={readiness} doorRows={doorRows} agentId={doorAgentId} error={doorError} />
        {scenario.uc_ref && (
          <span className="chip sc-ref mono" title={scenario.uc_name ? `Use case · ${scenario.uc_name}` : 'Use case'}>
            {scenario.uc_ref}
          </span>
        )}
        {scenario.tc_ref && (
          <span className="chip sc-ref mono" title={scenario.tc_name ? `Test case · ${scenario.tc_name}` : 'Test case'}>
            {scenario.tc_ref}
          </span>
        )}
      </span>
      </button>

      <div className="sc-footer sc-foot" data-testid="scenario-card-foot">
        <span className="sc-count">steps <strong className="mono">{steps.length}</strong></span>
        <span className="sc-count">detections <strong className="mono">{detections}</strong></span>
        {history && history.count > 0 && <ScenarioHistoryBadge history={history} />}
        <span className="sc-foot__spacer" />
        {onCustomize && <Button variant="secondary" onClick={() => onCustomize(id)}>Customize</Button>}
        {onRun && <Button variant="primary" onClick={() => onRun(id)}>Run</Button>}
      </div>
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
  // Backend terminal token is 'complete'; isRunComplete also accepts the legacy
  // 'completed' alias so the badge dot stays green for finished runs.
  const statusClass = isRunComplete(status) ? 'is-ok'
    : status === 'failed'  ? 'is-fail'
    : status === 'aborted' ? 'is-fail'
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
  onRunScenario = null,       // (id) => void — the launch step with it loaded
  onCustomizeScenario = null, // (id) => void — Build a chain with its chain loaded
  isPinned = () => false,
  onTogglePin = () => {},
  historyByScenario = null, // Map<scenario_id, { count, lastRunAt, lastStatus }>
  doorReadiness = null,     // {scenario_id: readiness} from GET /api/doors/readiness
  doorRows = null,          // GET /api/doors rows (real per-door state)
  doorAgentId = null,
  doorError = null,         // fetch failure text, shown in the chip tooltip
}) {
  if (scenarios.length === 0) {
    return (
      <div className="scenario-grid-empty">
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
      {ordered.map((s, i) => {
        const id = s.scenario_id || s.id
        const history = historyByScenario && historyByScenario.get
          ? historyByScenario.get(id)
          : null
        return (
          <ScenarioCard
            key={id}
            isFirst={i === 0}
            scenario={s}
            isSelected={id === selectedScenarioId}
            isPinned={isPinned(id)}
            onSelect={onSelectScenario}
            onRun={onRunScenario}
            onCustomize={onCustomizeScenario}
            onTogglePin={onTogglePin}
            history={history}
            readiness={doorReadiness ? doorReadiness[id] : undefined}
            doorRows={doorRows}
            doorAgentId={doorAgentId}
            doorError={doorError}
          />
        )
      })}
    </div>
  )
}

// ─── helpers ─────────────────────────────────────────────────────────────────

export function collectPlanes(scenario) {
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

export function collectTechniques(scenario) {
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
