import React from 'react'

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
function ScenarioCard({ scenario, isSelected, onSelect }) {
  const id      = scenario.scenario_id || scenario.id
  const planes  = collectPlanes(scenario)
  const tids    = collectTechniques(scenario).slice(0, 6)
  const actor   = scenario.threat_report
    ? scenario.threat_report.split(/\s*[—\-]\s*/)[0]
    : (scenario.tags && scenario.tags[0]) || '—'
  const difficulty = scenario.difficulty || scenario.tags?.find?.((t) => DIFFICULTY_LABEL[t]) || null

  return (
    <article
      className={'scenario-card' + (isSelected ? ' scenario-card--selected' : '')}
      onClick={() => onSelect(scenario)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onSelect(scenario) }}
    >
      <div className="sc-id">{id}</div>
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
    </article>
  )
}

/**
 * ScenarioGrid — grid of scenario cards.
 */
export default function ScenarioGrid({
  scenarios = [],
  selectedScenarioId = null,
  onSelectScenario = () => {},
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

  return (
    <div className="scenario-grid">
      {scenarios.map((s) => {
        const id = s.scenario_id || s.id
        return (
          <ScenarioCard
            key={id}
            scenario={s}
            isSelected={id === selectedScenarioId}
            onSelect={onSelectScenario}
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
