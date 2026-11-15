import React from 'react'
import PinButton from './PinButton.jsx'
import { formatAgo } from './useScenarioRunHistory.js'
import { isRunComplete } from './runStatus.js'
import DoorChip from './doors/DoorChip.jsx'
import { doorStateFor } from './doors/doorModel.js'
import Button from './kit/Button.jsx'
import { mappingFor } from './scenarioMapping.js'

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
 * title, a tag row, the mapping line, and a footer that carries the verbs.
 *
 * The body is a button that opens the scenario's sheet. The footer's Run goes
 * to the launch step with the scenario loaded; Customize opens Build a chain
 * with its chain loaded. Neither opens the sheet. Every tag is read from the
 * scenario SimCore returned: its planes and its door readiness. The mapping
 * line names its UC/TC, its detection source (the doors its detections land
 * on) and its MITRE techniques, each as the API reported it (see
 * scenarioMapping.js): a gap reads "Not mapped", an answer not given reads
 * "Not checked".
 *
 * A staged pair from the extended library (`scenario.staged`) is not a
 * scenario SimCore loaded: it has no sheet, no pin and no verbs, only what it
 * is and what it still needs.
 */
function ScenarioCard({
  scenario, isSelected, isPinned, isFirst, onSelect, onTogglePin, onRun = null, onCustomize = null,
  history = null, readiness, doorRows = null, doorAgentId = null, doorError = null,
}) {
  const id      = scenario.scenario_id || scenario.id
  const name    = scenario.name || '(unnamed scenario)'
  const staged  = scenario.staged || null
  const planes  = collectPlanes(scenario)
  const technique = scenario.mitre_technique || collectTechniques(scenario)[0] || null
  const difficulty = scenario.difficulty || scenario.tags?.find?.((t) => DIFFICULTY_LABEL[t]) || null
  const steps = Array.isArray(scenario.steps) ? scenario.steps : []
  const detections = steps.reduce((n, s) => n + (s.expected_detections || []).length, 0)

  const head = (
    <>
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
        {staged
          ? <span className="chip sc-staged-chip" title="A staged pair: not loaded by SimCore, not launchable, not counted as coverage">Staged draft</span>
          : <DoorChip scenarioId={id} readiness={readiness} doorRows={doorRows} agentId={doorAgentId} error={doorError} />}
        {staged && staged.platform && <span className="chip">{staged.platform}</span>}
      </span>
      <ScenarioMappingLine mapping={mappingFor(scenario, readiness)} doorRows={doorRows} />
    </>
  )

  if (staged) {
    const findings = Array.isArray(staged.findings) ? staged.findings : []
    return (
      <article data-testid={`scenario-card-${id}`} className="scenario-card scenario-card--staged">
        <div className="scenario-card__body scenario-card__body--static">{head}</div>
        <div className="sc-footer sc-foot" data-testid="scenario-card-foot">
          <span className="sc-count">steps <strong className="mono">{steps.length}</strong></span>
          <span className="sc-count">detections <strong className="mono">{detections}</strong></span>
          {staged.card && <span className="sc-count mono">{staged.card}</span>}
          <span className="sc-foot__spacer" />
          <span className="sc-count" title={findings.join('\n') || undefined}>
            findings <strong className="mono">{findings.length}</strong>
          </span>
        </div>
      </article>
    )
  }

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
        {head}
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

const MAP_AXES = ['uctc', 'source', 'mitre']
const MAP_LABEL = { uctc: 'UC/TC', source: 'Source', mitre: 'MITRE' }
const MAP_EMPTY = { missing: 'Not mapped', unknown: 'Not checked' }

/** `a · b +N`: the first values, and how many more the title lists. */
function shortList(values, shown = 2) {
  const head = values.slice(0, shown).join(' · ')
  return values.length > shown ? `${head} +${values.length - shown}` : head
}

/**
 * ScenarioMappingLine — UC/TC · Source · MITRE, each its values or why there
 * are none. It sits inside the card body (a button), so it is text only. A
 * source is a door id from the API, shown by the door's own label.
 */
function ScenarioMappingLine({ mapping, doorRows = null }) {
  return (
    <span className="sc-map" data-testid="scenario-card-map">
      {MAP_AXES.map((axis) => {
        const a = mapping[axis]
        const values = axis === 'source' ? a.values.map((d) => doorStateFor(doorRows, d).label || d) : a.values
        const text = a.state === 'mapped' ? shortList(values) : (MAP_EMPTY[a.state] || MAP_EMPTY.unknown)
        const title = [values.join(' · '), a.title].filter(Boolean).join('\n')
        return (
          <span
            key={axis}
            className={`sc-map__item sc-map__item--${a.state}`}
            data-map={axis}
            data-state={a.state}
            title={title || undefined}
          >
            <span className="sc-map__k">{MAP_LABEL[axis]}</span>
            <span className={'sc-map__v' + (a.state === 'mapped' && axis !== 'source' ? ' mono' : '')}>{text}</span>
          </span>
        )
      })}
    </span>
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
  emptyText = 'No scenarios match the current filter.',
}) {
  if (scenarios.length === 0) {
    return (
      <div className="scenario-grid-empty">
        {emptyText}
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
