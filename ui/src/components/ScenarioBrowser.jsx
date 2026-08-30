import React, { useState, useEffect, useMemo } from 'react'
import { getScenarios } from '../api/client.js'

// ─── Helpers ──────────────────────────────────────────────────────────────────

function PlaneBadge({ plane }) {
  const map = {
    EDR:        'badge-navy',
    CDR:        'badge-teal',
    NDR:        'badge-steel',
    ITDR:       'badge-warning',
    CLOUD_APP:  'badge-success',
    ANALYTICS:  'badge-danger',
  }
  const cls = map[(plane || '').toUpperCase()] || 'badge-steel'
  return <span className={`badge ${cls}`}>{plane}</span>
}

function ModeBadge({ label, variant }) {
  return (
    <span className={`scn-browser__mode-badge scn-browser__mode-badge--${variant}`}>
      {label}
    </span>
  )
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function ScenarioBrowser({ selectedPlane, selectedScenario, onSelectScenario }) {
  const [scenarios, setScenarios]     = useState([])
  const [loading, setLoading]         = useState(true)
  const [error, setError]             = useState(null)
  const [searchQuery, setSearchQuery] = useState('')

  // Re-fetch when plane filter changes
  useEffect(() => {
    setLoading(true)
    setError(null)
    const params = selectedPlane ? { plane: selectedPlane } : {}
    getScenarios(params)
      .then(data => setScenarios(Array.isArray(data) ? data : []))
      .catch(err => setError(err.message))
      .finally(() => setLoading(false))
  }, [selectedPlane])

  // Client-side text filter
  const filtered = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    if (!q) return scenarios
    return scenarios.filter(s =>
      (s.name            || '').toLowerCase().includes(q) ||
      (s.mitre_tactic    || '').toLowerCase().includes(q) ||
      (s.uc_ref          || '').toLowerCase().includes(q) ||
      (s.tc_ref          || '').toLowerCase().includes(q) ||
      (s.plane           || '').toLowerCase().includes(q) ||
      (s.scenario_id     || '').toLowerCase().includes(q)
    )
  }, [scenarios, searchQuery])

  return (
    <div className="panel-card">
      {/* Header */}
      <div className="panel-card-header">
        <h3>
          Scenario Library
          {selectedPlane && (
            <span className="scn-browser__header-suffix">
              — {selectedPlane}
            </span>
          )}
        </h3>
        <span className="scn-browser__meta-sm">
          {loading ? 'Loading…' : `${filtered.length} scenario${filtered.length !== 1 ? 's' : ''}`}
        </span>
      </div>

      {/* Search bar */}
      <div className="scn-browser__search-bar">
        <div className="search-input-wrapper">
          <span className="search-icon">&#128269;</span>
          <input
            type="text"
            placeholder="Filter by name, tactic, UC/TC ref, plane…"
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            aria-label="Filter scenarios"
          />
        </div>
      </div>

      {/* Table */}
      <div className="scn-browser__table-scroll">
        {loading ? (
          <div className="empty-state">
            <div className="spinner scn-browser__spinner" />
            <p>Loading scenarios…</p>
          </div>
        ) : error ? (
          <div className="empty-state">
            <div className="empty-state-icon">⚠</div>
            <p className="scn-browser__error-text">{error}</p>
          </div>
        ) : filtered.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon">📂</div>
            <p>No scenarios found{searchQuery ? ` for "${searchQuery}"` : ''}</p>
            {searchQuery && (
              <button
                className="btn btn-secondary btn-sm scn-browser__clear-btn"
                onClick={() => setSearchQuery('')}
              >
                Clear search
              </button>
            )}
          </div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Scenario Name</th>
                <th>MITRE Tactic</th>
                <th>UC Ref</th>
                <th>TC Ref</th>
                <th>Plane</th>
                <th>Mode</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(scenario => {
                const isSelected = selectedScenario?.scenario_id === scenario.scenario_id ||
                                   selectedScenario?.id === scenario.id
                return (
                  <tr
                    key={scenario.scenario_id || scenario.id}
                    onClick={() => onSelectScenario(scenario)}
                    className={isSelected ? 'row-selected' : ''}
                    title={`Select scenario: ${scenario.name}`}
                  >
                    {/* Scenario Name */}
                    <td>
                      <div className="scn-browser__name">
                        {scenario.name}
                      </div>
                      <div className="scn-browser__id">
                        {scenario.scenario_id}
                      </div>
                    </td>

                    {/* MITRE Tactic */}
                    <td>
                      <div className="scn-browser__tactic-name">{scenario.mitre_tactic_name || '—'}</div>
                      {scenario.mitre_tactic && (
                        <div className="scn-browser__tactic-code">
                          {scenario.mitre_tactic}
                        </div>
                      )}
                    </td>

                    {/* UC Ref */}
                    <td>
                      <span className="text-mono scn-browser__ref">
                        {scenario.uc_ref || '—'}
                      </span>
                    </td>

                    {/* TC Ref */}
                    <td>
                      <span className="text-mono scn-browser__ref">
                        {scenario.tc_ref || '—'}
                      </span>
                    </td>

                    {/* Plane */}
                    <td>
                      <PlaneBadge plane={scenario.plane} />
                    </td>

                    {/* Push / Pull badges */}
                    <td>
                      <div className="scn-browser__badges">
                        {scenario.pull_supported && (
                          <ModeBadge label="Pull" variant="pull" />
                        )}
                        {scenario.push_supported && (
                          <ModeBadge label="Push" variant="push" />
                        )}
                        {!scenario.pull_supported && !scenario.push_supported && (
                          <span className="scn-browser__meta-sm">—</span>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
