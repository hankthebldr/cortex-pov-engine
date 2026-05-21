import React, { useState, useEffect, useMemo, useRef } from 'react'

/**
 * FilterPalette — ⌘F overlay for multi-criteria scenario filtering.
 *
 * Unlike CommandPalette (which selects a single action), this palette toggles
 * checkboxes across several criteria. Each checkbox flip immediately updates
 * the filter — there's no "apply" button. Live result count surfaces at the
 * bottom so the DC sees exactly how restrictive the filter is.
 *
 * Sections are derived from the live scenario list — we don't hard-code the
 * facets because the scenario library is dynamic (new threat actors, new
 * tactics, new identities added by scenarios in the YAML tree).
 *
 * Props:
 *   open       — boolean
 *   onClose    — () => void
 *   scenarios  — current scenario list (used to derive facet values + counts)
 *   filter     — current filter state (from useScenarioFilter)
 *   onToggle   — (field, value) => void
 *   onClearAll — () => void
 *   matchCount — number of scenarios remaining after filter (for footer)
 *   totalCount — total scenarios available (for footer)
 */
export default function FilterPalette({
  open,
  onClose = () => {},
  scenarios = [],
  filter,
  onToggle = () => {},
  onClearAll = () => {},
  matchCount = 0,
  totalCount = 0,
}) {
  const [query, setQuery] = useState('')
  const inputRef = useRef(null)

  useEffect(() => {
    if (open) {
      setQuery('')
      setTimeout(() => inputRef.current && inputRef.current.focus(), 20)
    }
  }, [open])

  // Derive facet groups from the scenario list.
  const facets = useMemo(() => deriveFacets(scenarios), [scenarios])

  // Filter visible facet values by the query.
  const visibleFacets = useMemo(() => {
    if (!query) return facets
    const q = query.toLowerCase()
    return facets.map((group) => ({
      ...group,
      values: group.values.filter(
        (v) => v.value.toLowerCase().includes(q) || v.label.toLowerCase().includes(q),
      ),
    })).filter((g) => g.values.length > 0)
  }, [facets, query])

  const onBackdropClick = (e) => {
    if (e.target === e.currentTarget) onClose()
  }

  if (!filter) return null

  return (
    <div
      className={'cmd-palette-backdrop' + (open ? ' is-open' : '')}
      onClick={onBackdropClick}
      role="dialog"
      aria-modal="true"
      aria-label="Filter palette"
    >
      <div className="cmd-palette filter-palette">
        <div className="cmd-palette__input">
          <span className="cmd-palette__prompt">⌗</span>
          <input
            ref={inputRef}
            type="text"
            placeholder="Filter scenarios by tactic, technique, actor, identity…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <span className="kbd">esc</span>
        </div>

        <div className="cmd-palette__results filter-palette__results">
          {visibleFacets.length === 0 ? (
            <div className="cmd-section-label" style={{ paddingBottom: 24 }}>
              no facets match — try a different query
            </div>
          ) : (
            visibleFacets.map((group) => (
              <FacetGroup
                key={group.field}
                group={group}
                filter={filter}
                onToggle={onToggle}
              />
            ))
          )}
        </div>

        <div className="cmd-palette__footer filter-palette__footer">
          <span>
            <strong className="filter-palette__count">{matchCount}</strong>
            <span style={{ color: 'var(--c-text-muted)' }}>
              {' / '}{totalCount} scenarios match
            </span>
          </span>
          <span>
            <button
              type="button"
              className="filter-palette__clear-all"
              onClick={onClearAll}
              disabled={matchCount === totalCount}
            >
              clear all
            </button>
          </span>
        </div>
      </div>
    </div>
  )
}

/* ─── Facet rendering ───────────────────────────────────────────────── */

function FacetGroup({ group, filter, onToggle }) {
  const activeSet = filter[group.field] instanceof Set ? filter[group.field] : new Set()
  const activeIn = group.values.filter((v) => activeSet.has(v.value)).length

  return (
    <div className="filter-palette__group">
      <div className="filter-palette__group-head">
        <span className="cmd-section-label" style={{ padding: 0 }}>{group.label}</span>
        {activeIn > 0 && (
          <span className="filter-palette__active-badge mono">
            {activeIn} active
          </span>
        )}
      </div>
      <div className="filter-palette__values">
        {group.values.map((v) => {
          const isActive = activeSet.has(v.value)
          return (
            <button
              key={v.value}
              type="button"
              className={'filter-chip' + (isActive ? ' filter-chip--active' : '')}
              onClick={() => onToggle(group.field, v.value)}
              title={`${v.label} · ${v.count} scenario${v.count === 1 ? '' : 's'}`}
            >
              <span className="filter-chip__check" aria-hidden="true">
                {isActive ? '◼' : '◻'}
              </span>
              <span className="filter-chip__label">{v.label}</span>
              <span className="filter-chip__count mono">{v.count}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

/* ─── Facet derivation ──────────────────────────────────────────────── */

function deriveFacets(scenarios) {
  const counts = {
    tactics:      new Map(),
    techniques:   new Map(),
    actors:       new Map(),
    difficulties: new Map(),
    identities:   new Map(),
    detTypes:     new Map(),
    tags:         new Map(),
  }

  for (const s of scenarios) {
    bump(counts.tactics, s.mitre_tactic)
    bump(counts.techniques, s.mitre_technique)
    ;(s.additional_techniques || []).forEach((t) => bump(counts.techniques, t?.technique))
    ;(s.steps || []).forEach((step) => bump(counts.techniques, step?.mitre_technique))

    if (s.threat_report) {
      const actor = s.threat_report.split(/\s*[—\-]\s*/)[0].trim()
      bump(counts.actors, actor)
    }

    bump(counts.difficulties, (s.difficulty || '').toLowerCase())
    ;(s.tags || []).forEach((t) => {
      if (/^(basic|intermediate|advanced|evasive)$/.test(t)) {
        bump(counts.difficulties, t)
      } else {
        bump(counts.tags, t)
      }
    })

    bump(counts.identities, s.execution_identity?.default)
    ;(s.execution_identity?.options || []).forEach((i) => bump(counts.identities, i))

    ;(s.detection_types || []).forEach((t) => bump(counts.detTypes, t))
    ;(s.steps || []).forEach((step) => {
      ;(step.expected_detections || []).forEach((d) => bump(counts.detTypes, d?.type))
    })
  }

  return [
    { field: 'tactics',      label: 'MITRE Tactic',    values: mapToValues(counts.tactics) },
    { field: 'techniques',   label: 'MITRE Technique', values: mapToValues(counts.techniques) },
    { field: 'actors',       label: 'Threat Actor',    values: mapToValues(counts.actors) },
    { field: 'difficulties', label: 'Difficulty',      values: mapToValues(counts.difficulties) },
    { field: 'identities',   label: 'Identity',        values: mapToValues(counts.identities) },
    { field: 'detTypes',     label: 'Detection Type',  values: mapToValues(counts.detTypes) },
    { field: 'tags',         label: 'Tags',            values: mapToValues(counts.tags) },
  ].filter((g) => g.values.length > 0)
}

function bump(m, key) {
  if (!key) return
  m.set(key, (m.get(key) || 0) + 1)
}

function mapToValues(m) {
  return Array.from(m.entries())
    .map(([value, count]) => ({ value, label: value, count }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label))
}
