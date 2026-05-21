import { useState, useMemo, useCallback } from 'react'

/**
 * useScenarioFilter — unified filter state for the Operations grid.
 *
 * The Operations view has three filter sources that previously lived in
 * different layers:
 *   1. Plane filter (rail click)
 *   2. Technique filter (Coverage→Operations cross-link, with optional
 *      explicit scenarioIds list from the heatmap)
 *   3. Multi-criteria filter palette (⌘F)
 *
 * This hook unifies all three under one filter object so a single chip strip
 * in the header can show every active filter, and "clear all" works in one
 * call. Each criterion narrows the visible set (AND across criteria); within
 * a single criterion that accepts multiple values (e.g. tactics), it's OR.
 *
 * Filter shape:
 *   {
 *     plane:        string | null              // single plane code (rail)
 *     technique:    { tid, scenarioIds } | null // technique filter (Coverage)
 *     tactics:      Set<string>                  // OR — MITRE tactic IDs
 *     techniques:   Set<string>                  // OR — MITRE technique IDs
 *     actors:       Set<string>                  // OR — threat actor anchors
 *     difficulties: Set<string>                  // OR — basic|intermediate|advanced|evasive
 *     identities:   Set<string>                  // OR — execution identity
 *     detTypes:     Set<string>                  // OR — BIOC|Analytics|IOC
 *     tags:         Set<string>                  // OR — free-form tags
 *   }
 *
 * Returns { filter, setPlane, setTechnique, toggle, clearAll, clearOne,
 *   activeCount, isEmpty, applyTo }.
 */

const SET_FIELDS = [
  'tactics', 'techniques', 'actors', 'difficulties',
  'identities', 'detTypes', 'tags',
]

const EMPTY_FILTER = () => ({
  plane:      null,
  technique:  null,
  tactics:      new Set(),
  techniques:   new Set(),
  actors:       new Set(),
  difficulties: new Set(),
  identities:   new Set(),
  detTypes:     new Set(),
  tags:         new Set(),
})

export default function useScenarioFilter() {
  const [filter, setFilter] = useState(EMPTY_FILTER)

  const setPlane = useCallback((plane) => {
    setFilter((prev) => ({ ...prev, plane: plane || null }))
  }, [])

  const setTechnique = useCallback((tid, scenarioIds) => {
    setFilter((prev) => ({
      ...prev,
      technique: tid ? { tid, scenarioIds: scenarioIds || [] } : null,
    }))
  }, [])

  // Toggle a value in one of the Set-shaped fields. No-ops for fields outside SET_FIELDS.
  const toggle = useCallback((field, value) => {
    if (!SET_FIELDS.includes(field)) return
    setFilter((prev) => {
      const next = new Set(prev[field])
      if (next.has(value)) next.delete(value)
      else next.add(value)
      return { ...prev, [field]: next }
    })
  }, [])

  const clearAll = useCallback(() => setFilter(EMPTY_FILTER()), [])

  // Clear a single criterion — used by header chip × buttons.
  const clearOne = useCallback((field) => {
    setFilter((prev) => {
      if (field === 'plane')     return { ...prev, plane: null }
      if (field === 'technique') return { ...prev, technique: null }
      if (SET_FIELDS.includes(field)) return { ...prev, [field]: new Set() }
      return prev
    })
  }, [])

  // Active filter count for surface badges (excludes plane since rail already shows it).
  const activeCount = useMemo(() => {
    let n = 0
    if (filter.technique) n += 1
    for (const f of SET_FIELDS) n += filter[f].size > 0 ? 1 : 0
    return n
  }, [filter])

  const isEmpty = activeCount === 0 && !filter.plane

  /**
   * applyTo(scenarios) — return a new array of scenarios matching every
   * active filter (AND across criteria, OR within a criterion).
   *
   * Each scenario is treated as a small bag of properties extracted from
   * its schema fields — we re-extract on every call rather than caching to
   * keep this hook stateless w.r.t. scenario identity. Cost is O(n×k) where
   * k is the number of active criteria; for a few hundred scenarios this is
   * effectively free.
   */
  const applyTo = useCallback((scenarios) => {
    if (!Array.isArray(scenarios) || scenarios.length === 0) return []
    return scenarios.filter((s) => scenarioMatches(s, filter))
  }, [filter])

  return {
    filter,
    setPlane,
    setTechnique,
    toggle,
    clearAll,
    clearOne,
    activeCount,
    isEmpty,
    applyTo,
  }
}

/* ─── matchers ─────────────────────────────────────────────────────── */

function scenarioMatches(s, f) {
  // plane (single)
  if (f.plane && (s.plane || '').toUpperCase() !== f.plane.toUpperCase()) {
    return false
  }

  // technique (from Coverage tab — prefers explicit scenarioIds list)
  if (f.technique) {
    const ids = (f.technique.scenarioIds || []).map(String)
    const sid = String(s.scenario_id || s.id || '')
    if (ids.length > 0) {
      if (!ids.includes(sid)) return false
    } else {
      const tid = (f.technique.tid || '').toUpperCase()
      const tids = collectTechniques(s).map((t) => t.toUpperCase())
      if (!tids.includes(tid)) return false
    }
  }

  // tactics (OR within)
  if (f.tactics.size > 0) {
    const t = (s.mitre_tactic || '').toUpperCase()
    if (!f.tactics.has(t)) return false
  }

  // techniques (OR within) — match against any technique the scenario touches
  if (f.techniques.size > 0) {
    const all = collectTechniques(s).map((x) => x.toUpperCase())
    const wantUpper = new Set(Array.from(f.techniques).map((x) => x.toUpperCase()))
    let hit = false
    for (const t of all) { if (wantUpper.has(t)) { hit = true; break } }
    if (!hit) return false
  }

  // actors — match the threat_report prefix or tags
  if (f.actors.size > 0) {
    const actor = (s.threat_report || '').split(/\s*[—\-]\s*/)[0].trim()
    const tags  = s.tags || []
    let hit = false
    for (const a of f.actors) {
      if (actor === a) { hit = true; break }
      if (tags.includes(a)) { hit = true; break }
    }
    if (!hit) return false
  }

  // difficulties — match scenario.difficulty or recognized difficulty tag
  if (f.difficulties.size > 0) {
    const explicit = (s.difficulty || '').toLowerCase()
    const tagDifficulty = (s.tags || []).find((t) =>
      /^(basic|intermediate|advanced|evasive)$/.test(t)
    )
    const have = (explicit || tagDifficulty || '').toLowerCase()
    if (!f.difficulties.has(have)) return false
  }

  // identities — match execution_identity.default or any option
  if (f.identities.size > 0) {
    const def  = s.execution_identity?.default || ''
    const opts = s.execution_identity?.options || []
    let hit = false
    for (const i of f.identities) {
      if (def === i || opts.includes(i)) { hit = true; break }
    }
    if (!hit) return false
  }

  // detection types — match any expected detection type across steps
  if (f.detTypes.size > 0) {
    const types = collectDetectionTypes(s)
    let hit = false
    for (const t of types) {
      if (f.detTypes.has(t)) { hit = true; break }
    }
    if (!hit) return false
  }

  // tags — straight set intersection
  if (f.tags.size > 0) {
    const stags = new Set(s.tags || [])
    let hit = false
    for (const t of f.tags) if (stags.has(t)) { hit = true; break }
    if (!hit) return false
  }

  return true
}

function collectTechniques(s) {
  const tids = new Set()
  if (s.mitre_technique) tids.add(s.mitre_technique)
  ;(s.additional_techniques || []).forEach((t) => {
    if (t && t.technique) tids.add(t.technique)
  })
  ;(s.steps || []).forEach((step) => {
    if (step.mitre_technique) tids.add(step.mitre_technique)
  })
  return Array.from(tids)
}

function collectDetectionTypes(s) {
  const types = new Set()
  ;(s.detection_types || []).forEach((t) => types.add(t))
  ;(s.steps || []).forEach((step) => {
    ;(step.expected_detections || []).forEach((d) => {
      if (d.type) types.add(d.type)
    })
  })
  return Array.from(types)
}
