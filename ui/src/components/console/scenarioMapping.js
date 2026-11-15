/**
 * scenarioMapping.js — the three mappings every Scenarios card shows, and the
 * Unmapped filter that lists the scenarios missing any of them. Pure.
 *
 *   UC/TC   `uc_ref` plus `tc_ref` / `tc_refs`, as the row carries them
 *   source  the ingestion doors the scenario's detections land on: `doors` on
 *           its door readiness (GET /api/doors/readiness) for a corpus row, or
 *           on the row itself for a staged pair (GET /api/scenarios/staged)
 *   MITRE   the techniques the row names: the scenario's own, its additional
 *           ones and each step's
 *
 * Each axis reads `mapped`, `missing` or `unknown`. `missing` is a gap the API
 * stated: no ref, no technique, or a door list that came back empty. `unknown`
 * is what was not told — readiness not loaded, the registry absent, a SimCore
 * that sends no `doors` — and it never counts as unmapped, because "we were not
 * told" is not "there is none". Nothing here infers a mapping.
 */

export const AXES = Object.freeze(['uctc', 'source', 'mitre'])

const mapped = (values, title = '') => ({ state: 'mapped', values, title })
const missing = (title) => ({ state: 'missing', values: [], title })
const unknown = (title) => ({ state: 'unknown', values: [], title })

/** The scenario's test cases: its whole evidence set, else its primary one. */
export function testCasesOf(row) {
  if (Array.isArray(row.tc_refs) && row.tc_refs.length) return row.tc_refs.filter(Boolean)
  return row.tc_ref ? [row.tc_ref] : []
}

/** Every technique the row names, primary first. */
export function techniquesOf(row) {
  const out = []
  const add = (t) => { if (t && !out.includes(t)) out.push(t) }
  add(row.mitre_technique)
  ;(row.additional_techniques || []).forEach((t) => add(t && t.technique))
  ;(row.steps || []).forEach((s) => add(s && s.mitre_technique))
  return out
}

function uctcAxis(row) {
  const tcs = testCasesOf(row)
  if (row.uc_ref && tcs.length) {
    const title = [row.uc_name && `Use case · ${row.uc_name}`, row.tc_name && `Test case · ${row.tc_name}`]
      .filter(Boolean).join('\n')
    return mapped([row.uc_ref, ...tcs], title)
  }
  if (!row.uc_ref && !tcs.length) return missing('Names no use case and no test case')
  return missing(row.uc_ref ? 'Names no test case' : 'Names no use case')
}

const NO_DOOR = 'No detection reads a dataset a door is registered for'

function sourceAxis(row, readiness) {
  // A staged pair carries its own doors: it has no readiness row.
  if (row.staged) {
    if (Array.isArray(row.doors)) return row.doors.length ? mapped(row.doors) : missing(NO_DOOR)
    return unknown(`Detection source not derived${row.doors_code ? ` (${row.doors_code})` : ''}`)
  }
  if (readiness === undefined || readiness === null) return unknown('Door readiness not loaded')
  if (Array.isArray(readiness.doors)) return readiness.doors.length ? mapped(readiness.doors) : missing(NO_DOOR)
  if (readiness.reason) return unknown(`Detection source not derived (${readiness.reason})`)
  return unknown('This SimCore does not report the doors a scenario lands on')
}

function mitreAxis(row) {
  const tids = techniquesOf(row)
  return tids.length ? mapped(tids) : missing('Names no MITRE ATT&CK technique')
}

/**
 * @param {Object} row        a scenario row (corpus or staged)
 * @param {Object} [readiness] this row's entry in the door-readiness map, if any
 * @returns {{uctc, source, mitre, unmapped: boolean}}
 */
export function mappingFor(row, readiness = undefined) {
  const m = { uctc: uctcAxis(row), source: sourceAxis(row, readiness), mitre: mitreAxis(row) }
  m.unmapped = AXES.some((a) => m[a].state === 'missing')
  return m
}

const idOf = (s) => s.scenario_id || s.id

/** The row's mapping, looking its readiness up in the bulk map. */
export function mappingIn(row, readinessMap = null) {
  return mappingFor(row, readinessMap ? readinessMap[idOf(row)] : undefined)
}

/**
 * How many rows are unmapped, and how many of the rest could not be checked for
 * a detection source (so the count's caveat can say so).
 */
export function unmappedCounts(rows = [], readinessMap = null) {
  let unmapped = 0
  let sourceUnknown = 0
  for (const row of rows) {
    const m = mappingIn(row, readinessMap)
    if (m.unmapped) unmapped += 1
    else if (m.source.state === 'unknown') sourceUnknown += 1
  }
  return { unmapped, sourceUnknown }
}
