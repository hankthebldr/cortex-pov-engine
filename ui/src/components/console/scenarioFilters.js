/**
 * scenarioFilters.js — the Scenarios filter row (design v26 §4.4). Pure.
 *
 * Filters: plane · component · use case · test case · status. Every option is
 * read from what SimCore returned — the scenarios' own planes and index refs,
 * the door registry and each scenario's door readiness, the run history —
 * so the row never offers a value no scenario has.
 *
 *   component  the doors a scenario is still WAITING ON (door readiness
 *              `not_open`). A door nobody has checked is not open either, so
 *              this reads "waiting on", never "uses".
 *   use case   `uc_ref` (labelled with `uc_name`)
 *   test case  `tc_refs` — the scenario's whole evidence set, so a test case
 *              two scenarios evidence matches both
 *   status     ready to run (readiness `ready`) · needs a component (some door
 *              not open) · never run · already run
 */
import { notOpenDoorIds, readinessKind } from './doors/doorModel.js'

export const EMPTY_SCENARIO_FILTERS = Object.freeze({ component: '', uc: '', tc: '', status: '' })

const idOf = (s) => s.scenario_id || s.id

function hasHistory(history, id) {
  const h = history && history.get ? history.get(id) : null
  return !!(h && h.count > 0)
}

/** True when the scenario passes every set filter. */
export function matchesScenarioFilters(s, f, { readiness = null, history = null } = {}) {
  const id = idOf(s)
  const r = readiness ? readiness[id] : undefined
  if (f.component && !notOpenDoorIds(r).includes(f.component)) return false
  if (f.uc && s.uc_ref !== f.uc) return false
  if (f.tc) {
    const refs = Array.isArray(s.tc_refs) && s.tc_refs.length ? s.tc_refs : [s.tc_ref].filter(Boolean)
    if (!refs.includes(f.tc)) return false
  }
  if (f.status === 'ready' && readinessKind(r) !== 'ready') return false
  if (f.status === 'needs' && notOpenDoorIds(r).length === 0) return false
  if (f.status === 'never' && hasHistory(history, id)) return false
  if (f.status === 'run' && !hasHistory(history, id)) return false
  return true
}

export function applyScenarioFilters(list, f, ctx) {
  if (!f || (!f.component && !f.uc && !f.tc && !f.status)) return list
  return list.filter((s) => matchesScenarioFilters(s, f, ctx))
}

/** The options each select offers, from the loaded scenarios and doors. */
export function scenarioFilterOptions(scenarios = [], { readiness = null, doorRows = null, history = null } = {}) {
  const planes = [...new Set(scenarios.map((s) => (s.plane || '').toUpperCase()).filter(Boolean))].sort()

  const ucs = new Map()
  for (const s of scenarios) if (s.uc_ref && !ucs.has(s.uc_ref)) ucs.set(s.uc_ref, s.uc_name || '')
  const useCases = [...ucs].sort(([a], [b]) => a.localeCompare(b))
    .map(([v, name]) => ({ v, label: name ? `${v} · ${name}` : v }))

  const tcNames = new Map()
  const tcs = new Set()
  for (const s of scenarios) {
    for (const t of (Array.isArray(s.tc_refs) && s.tc_refs.length ? s.tc_refs : [s.tc_ref])) if (t) tcs.add(t)
    if (s.tc_ref && s.tc_name && !tcNames.has(s.tc_ref)) tcNames.set(s.tc_ref, s.tc_name)
  }
  const testCases = [...tcs].sort().map((v) => ({ v, label: v, title: tcNames.get(v) || undefined }))

  // Only doors some scenario is actually waiting on — an option that matches
  // nothing would be a dead end.
  const waiting = new Set()
  for (const s of scenarios) for (const d of notOpenDoorIds(readiness ? readiness[idOf(s)] : undefined)) waiting.add(d)
  const label = new Map((doorRows || []).map((d) => [d.door_id || d.key, d.label || d.door_id]))
  const components = [...waiting].sort()
    .map((v) => ({ v, label: `Waiting on ${label.get(v) || v}` }))

  const ran = scenarios.filter((s) => hasHistory(history, idOf(s))).length
  const count = (f) => scenarios.filter((s) => matchesScenarioFilters(s, { ...EMPTY_SCENARIO_FILTERS, status: f }, { readiness, history })).length
  const statuses = [
    { v: 'ready', label: `Ready to run · ${count('ready')}` },
    { v: 'needs', label: `Needs a component · ${count('needs')}` },
    { v: 'never', label: `Never run · ${scenarios.length - ran}` },
    { v: 'run', label: `Already run · ${ran}` },
  ]

  return { planes, useCases, testCases, components, statuses }
}
