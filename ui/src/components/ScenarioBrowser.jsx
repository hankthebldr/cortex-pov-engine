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

function ModeBadge({ label, color }) {
  return (
    <span style={{
      fontSize: '10px',
      fontWeight: 700,
      padding: '2px 6px',
      borderRadius: '3px',
      background: `rgba(${color}, 0.1)`,
      color: `rgb(${color})`,
      border: `1px solid rgba(${color}, 0.25)`,
      letterSpacing: '0.04em',
      textTransform: 'uppercase',
    }}>
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
            <span style={{ marginLeft: '8px', fontWeight: 400, color: 'var(--cortex-teal)', textTransform: 'none' }}>
              — {selectedPlane}
            </span>
          )}
        </h3>
        <span style={{ fontSize: '12px', color: 'var(--cortex-steel)' }}>
          {loading ? 'Loading…' : `${filtered.length} scenario${filtered.length !== 1 ? 's' : ''}`}
        </span>
      </div>

      {/* Search bar */}
      <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--cortex-border)' }}>
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
      <div style={{ overflowX: 'auto' }}>
        {loading ? (
          <div className="empty-state">
            <div className="spinner" style={{ margin: '0 auto var(--space-4)' }} />
            <p>Loading scenarios…</p>
          </div>
        ) : error ? (
          <div className="empty-state">
            <div className="empty-state-icon">⚠</div>
            <p style={{ color: 'var(--cortex-danger)' }}>{error}</p>
          </div>
        ) : filtered.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon">📂</div>
            <p>No scenarios found{searchQuery ? ` for "${searchQuery}"` : ''}</p>
            {searchQuery && (
              <button
                className="btn btn-secondary btn-sm"
                onClick={() => setSearchQuery('')}
                style={{ marginTop: '12px' }}
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
                      <div style={{ fontWeight: 500, color: 'var(--cortex-navy)' }}>
                        {scenario.name}
                      </div>
                      <div style={{
                        fontSize: '11px',
                        color: 'var(--cortex-steel)',
                        fontFamily: 'var(--font-mono)',
                        marginTop: '2px',
                      }}>
                        {scenario.scenario_id}
                      </div>
                    </td>

                    {/* MITRE Tactic */}
                    <td>
                      <div style={{ fontSize: '12px' }}>{scenario.mitre_tactic_name || '—'}</div>
                      {scenario.mitre_tactic && (
                        <div style={{
                          fontSize: '10px',
                          fontFamily: 'var(--font-mono)',
                          color: 'var(--cortex-steel)',
                        }}>
                          {scenario.mitre_tactic}
                        </div>
                      )}
                    </td>

                    {/* UC Ref */}
                    <td>
                      <span className="text-mono" style={{ fontSize: '12px' }}>
                        {scenario.uc_ref || '—'}
                      </span>
                    </td>

                    {/* TC Ref */}
                    <td>
                      <span className="text-mono" style={{ fontSize: '12px' }}>
                        {scenario.tc_ref || '—'}
                      </span>
                    </td>

                    {/* Plane */}
                    <td>
                      <PlaneBadge plane={scenario.plane} />
                    </td>

                    {/* Push / Pull badges */}
                    <td>
                      <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap' }}>
                        {scenario.pull_supported && (
                          <ModeBadge label="Pull" color="0, 184, 148" />
                        )}
                        {scenario.push_supported && (
                          <ModeBadge label="Push" color="0, 192, 232" />
                        )}
                        {!scenario.pull_supported && !scenario.push_supported && (
                          <span style={{ fontSize: '12px', color: 'var(--cortex-steel)' }}>—</span>
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
