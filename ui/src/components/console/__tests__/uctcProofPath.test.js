/**
 * uctcProofPath — the proof path a detection test case walks (design C6):
 *
 *   Has a scenario › Components ready › Ran › Read back › Detected
 *
 * Only "Detected" (a read-back run that saw a detection) counts as covered.
 * A source that did not load reads "Can't check", never "missing", and a door
 * nobody has checked never makes a test case "not ready".
 */
import { describe, it, expect } from 'vitest'
import {
  buildProofPaths,
  componentForDoor,
  funnel,
  gapCounts,
  gaps,
  indexTiles,
  mechanismModel,
  runFacts,
  scenariosByTc,
  sourceState,
  useCaseVerdict,
} from '../uctcProofPath.js'

const OK = { scenarios: 'ok', readiness: 'ok', doors: 'ok', runs: 'ok' }

const tc = (id, uc, cls = 'DET') => ({ tc_id: id, uc_id: uc, validation_class: cls, title: `${id} title` })

const scen = (id, tcRef, refs = [tcRef], types = ['BIOC']) => ({
  scenario_id: id,
  tc_ref: tcRef,
  tc_refs: refs,
  steps: [{ id: 'step-01', expected_detections: types.map((type) => ({ type })) }],
})

const ready = (id) => ({ scenario_id: id, state: 'ready', scorable: 1, total: 1, not_open: [] })
const needs = (id, doorId = 'xdr_agent', n = 1) => ({
  scenario_id: id, state: 'needs_doors', scorable: 0, total: n, not_open: [{ door_id: doorId, detections: n }],
})

const done = (over = {}) => ({
  run_id: 'r-1', scenario_id: 'SIM-A', mode: 'pull', status: 'complete',
  delivered_at: '2026-11-13T10:00:05', started_at: '2026-11-13T10:00:00',
  tc_verdict: 'pending', tc_verdict_detail: { source: 'complete' },
  expected_detections: 3, detected_count: 0,
  ...over,
})
const readBack = (over = {}) => done({
  tc_verdict_detail: { source: 'reconcile:xsiam:acme', scored_at: '2026-11-13T11:00:00' },
  ...over,
})

function one(t, { scenarios = [], runs = [], readiness = {}, doorRows = [], sources = OK } = {}) {
  return buildProofPaths([t], { scenarios, runs, readiness, doorRows, sources })[0]
}

describe('scenariosByTc', () => {
  it('indexes every tc_ref a scenario names, the primary binding first', () => {
    const idx = scenariosByTc([
      scen('SIM-B', 'TC-2', ['TC-2', 'TC-1']),
      scen('SIM-A', 'TC-1'),
      { scenario_id: 'SIM-C', tc_ref: 'TC-3' }, // an older row with no tc_refs
    ])
    expect(idx.get('TC-1').map((s) => s.scenario_id)).toEqual(['SIM-A', 'SIM-B'])
    expect(idx.get('TC-3').map((s) => s.scenario_id)).toEqual(['SIM-C'])
  })
})

describe('proofPath — each stage from its own source', () => {
  it('stalls at "No scenario" when no scenario names the test case', () => {
    const p = one(tc('TC-1', 'UC-1'), { scenarios: [scen('SIM-A', 'TC-9')] })
    expect(p.stages.scenario).toBe('todo')
    expect(p.stall).toBe('no_scenario')
    expect(p.stages.ran).toBe('na')
  })

  it('reads an unchecked door as "Can\'t check", never as not ready', () => {
    const p = one(tc('TC-1', 'UC-1'), {
      scenarios: [scen('SIM-A', 'TC-1')],
      readiness: { 'SIM-A': needs('SIM-A', 'ngfw_traffic') },
      doorRows: [{ key: 'ngfw_traffic', door_id: 'ngfw_traffic', state: 'unknown', rung: 'authored' }],
    })
    expect(p.stages.components).toBe('unknown')
    // It never ran, and that is the gap — not a component nobody checked.
    expect(p.stall).toBe('never_ran')
    // The door still names where the signal comes from.
    expect(p.component).toBe('ngfw')
  })

  it('stalls at "Component not ready" only when a door was checked and found closed', () => {
    const p = one(tc('TC-1', 'UC-1'), {
      scenarios: [scen('SIM-A', 'TC-1')],
      readiness: { 'SIM-A': needs('SIM-A', 'http_collector') },
      doorRows: [{ key: 'http_collector', door_id: 'http_collector', label: 'HTTP log collector', state: 'closed' }],
    })
    expect(p.stages.components).toBe('todo')
    expect(p.stall).toBe('component')
    expect(p.component).toBe('http')
    expect(p.door.label).toBe('HTTP log collector')
  })

  it('counts a ready or partial scenario as components ready', () => {
    const p = one(tc('TC-1', 'UC-1'), {
      scenarios: [scen('SIM-A', 'TC-1')],
      readiness: { 'SIM-A': ready('SIM-A') },
    })
    expect(p.stages.components).toBe('done')
    expect(p.stall).toBe('never_ran')
  })

  it('stalls at "Not read back" when a run finished and nobody read the tenant', () => {
    const p = one(tc('TC-1', 'UC-1'), {
      scenarios: [scen('SIM-A', 'TC-1')],
      runs: [done({ run_id: 'r-7' })],
    })
    expect(p.stages.ran).toBe('done')
    expect(p.stages.readBack).toBe('todo')
    expect(p.stall).toBe('not_read_back')
    expect(p.runId).toBe('r-7')
  })

  it('stalls at "Not seen yet" when the read back found no detection, carrying its reason', () => {
    const p = one(tc('TC-1', 'UC-1'), {
      scenarios: [scen('SIM-A', 'TC-1')],
      runs: [readBack({
        run_id: 'r-8',
        tc_verdict_detail: { source: 'reconcile:xsiam:acme', reason: 'soak_pending' },
      })],
    })
    expect(p.stages.readBack).toBe('done')
    expect(p.stages.detected).toBe('todo')
    expect(p.stall).toBe('not_seen')
    expect(p.reason).toBe('soak_pending')
    expect(p.covered).toBe(false)
  })

  it('counts as covered only after a read back that saw a detection', () => {
    const p = one(tc('TC-1', 'UC-1'), {
      scenarios: [scen('SIM-A', 'TC-1')],
      runs: [readBack({ detected_count: 2, tc_verdict: 'pass' })],
    })
    expect(p.stages.detected).toBe('done')
    expect(p.covered).toBe(true)
    expect(p.stall).toBeNull()
  })

  it('never calls a run detected that was not read back, whatever its count says', () => {
    const p = one(tc('TC-1', 'UC-1'), {
      scenarios: [scen('SIM-A', 'TC-1')],
      runs: [done({ detected_count: 3 })],
    })
    expect(p.covered).toBe(false)
    expect(p.stall).toBe('not_read_back')
  })

  it('reads a run where nothing executed as never ran, with the not_executed reason', () => {
    const p = one(tc('TC-1', 'UC-1'), {
      scenarios: [scen('SIM-A', 'TC-1')],
      runs: [done({ run_id: 'r-9', tc_verdict_detail: { source: 'complete', reason: 'not_executed' } })],
    })
    expect(p.stages.ran).toBe('todo')
    expect(p.stall).toBe('never_ran')
    expect(p.reason).toBe('not_executed')
    expect(p.runId).toBe('r-9')
  })

  it('does not count a staged push bundle as ran until it is read back', () => {
    const p = one(tc('TC-1', 'UC-1'), {
      scenarios: [scen('SIM-A', 'TC-1')],
      runs: [done({ run_id: 'r-s', mode: 'push', status: 'staged', delivered_at: null })],
    })
    expect(p.stages.ran).toBe('todo')
    expect(p.inFlight).toBe(true)
    // Waiting to be run (design C2b), not running.
    expect(p.facts.waiting.map((r) => r.run_id)).toEqual(['r-s'])
    expect(p.facts.running).toEqual([])
  })

  it('says "Can\'t check" — not "No scenario" — when the scenario list did not load', () => {
    const p = one(tc('TC-1', 'UC-1'), { sources: { ...OK, scenarios: 'error' } })
    expect(p.stages.scenario).toBe('unknown')
    expect(p.stall).toBe('unknown')
    expect(p.unknownSource).toBe('scenarios')
  })

  it('treats an empty scenario catalog as a fault, not as zero coverage', () => {
    expect(sourceState({ data: [], emptyIsFault: true })).toBe('empty')
    expect(sourceState({ data: [] })).toBe('ok') // zero runs is a real answer
    expect(sourceState({ data: [], loading: true })).toBe('loading')
    expect(sourceState({ data: [], error: 'boom' })).toBe('error')
    const p = one(tc('TC-1', 'UC-1'), { sources: { ...OK, scenarios: 'empty' } })
    expect(p.stall).toBe('unknown')
    expect(p.unknownSource).toBe('scenarios-empty')
  })

  it('says "Can\'t check" — not "Never ran" — when the run list did not load', () => {
    const p = one(tc('TC-1', 'UC-1'), {
      scenarios: [scen('SIM-A', 'TC-1')],
      sources: { ...OK, runs: 'error' },
    })
    expect(p.stages.ran).toBe('unknown')
    expect(p.stall).toBe('unknown')
    expect(p.unknownSource).toBe('runs')
  })

  it('says "Can\'t check" when a read-back run carries no detection count', () => {
    const p = one(tc('TC-1', 'UC-1'), {
      scenarios: [scen('SIM-A', 'TC-1')],
      runs: [readBack({ detected_count: undefined })],
    })
    expect(p.stages.detected).toBe('unknown')
    expect(p.stall).toBe('unknown')
    expect(p.unknownSource).toBe('counts')
  })

  it('leaves posture, platform and automation rows off the path', () => {
    const paths = buildProofPaths([tc('TC-1', 'UC-1'), tc('TC-2', 'UC-1', 'POS'), tc('TC-3', 'UC-1', 'HNT')], {
      scenarios: [], sources: OK,
    })
    expect(paths.map((p) => p.tcId)).toEqual(['TC-1', 'TC-3'])
  })
})

describe('runFacts', () => {
  it('rolls pending reasons up the way /api/uctc does', () => {
    const f = runFacts([
      done({ run_id: 'a', tc_verdict_detail: { reason: 'not_executed' } }),
      done({ run_id: 'b', tc_verdict_detail: { reason: 'not_executed' } }),
      readBack({ run_id: 'c', tc_verdict: 'pass', detected_count: 1 }),
    ])
    expect(f.total).toBe(3)
    expect(f.pendingReasons).toEqual({ not_executed: 2 })
    expect(f.detected.map((r) => r.run_id)).toEqual(['c'])
  })
})

describe('componentForDoor', () => {
  it('sends each door to the component that owns it', () => {
    expect(componentForDoor('xdr_agent')).toBe('agent')
    expect(componentForDoor('xdr_agent_xth')).toBe('agent')
    expect(componentForDoor('ngfw_stitch')).toBe('ngfw')
    expect(componentForDoor('http_collector')).toBe('http')
    expect(componentForDoor('syslog_cisco_asa')).toBe('bvm')
    expect(componentForDoor('cloud_aws')).toBe('tenant')
    expect(componentForDoor('')).toBeNull()
  })
})

describe('funnel, gaps and counts', () => {
  const scenarios = [scen('SIM-A', 'TC-2'), scen('SIM-B', 'TC-3'), scen('SIM-C', 'TC-4')]
  const runs = [
    done({ run_id: 'r-b', scenario_id: 'SIM-B' }),
    readBack({ run_id: 'r-c', scenario_id: 'SIM-C', detected_count: 1 }),
  ]
  const paths = buildProofPaths(
    [tc('TC-1', 'UC-1'), tc('TC-2', 'UC-1'), tc('TC-3', 'UC-2'), tc('TC-4', 'UC-2')],
    { scenarios, runs, readiness: {}, doorRows: [], sources: OK },
  )

  it('counts each stage that is done, out of every detection test case', () => {
    const f = Object.fromEntries(funnel(paths).map((s) => [s.stage, s]))
    expect(f.scenario.done).toBe(3)
    expect(f.ran.done).toBe(2)
    expect(f.readBack.done).toBe(1)
    expect(f.detected.done).toBe(1)
    expect(f.detected.of).toBe(4)
    // No door was checked: components readiness has no number, only "can't check".
    expect(f.components.done).toBeNull()
    expect(f.components.unknown).toBe(3)
  })

  it('lists every test case not yet detected, earliest stall first', () => {
    expect(gaps(paths).map((p) => [p.tcId, p.stall])).toEqual([
      ['TC-1', 'no_scenario'],
      ['TC-2', 'never_ran'],
      ['TC-3', 'not_read_back'],
    ])
    expect(gapCounts(paths)).toMatchObject({ all: 3, no_scenario: 1, never_ran: 1, not_read_back: 1, not_seen: 0 })
  })

  it('rolls use cases up without calling a partly detected one proven', () => {
    const byUc = (id) => paths.filter((p) => p.ucId === id)
    expect(useCaseVerdict(byUc('UC-1'))).toBe('pending')
    expect(useCaseVerdict(byUc('UC-2'))).toBe('partial')
    expect(useCaseVerdict([])).toBe('none')
  })

  it('will not call a use case pending when one of its test cases could not be checked', () => {
    const mixed = buildProofPaths([tc('TC-1', 'UC-1'), tc('TC-2', 'UC-1')], {
      scenarios: [scen('SIM-A', 'TC-2')], runs: [], sources: { ...OK, runs: 'error' },
    })
    // TC-1 has no scenario (known); TC-2's runs could not be read (unknown).
    expect(mixed.map((p) => p.stall)).toEqual(['no_scenario', 'unknown'])
    expect(useCaseVerdict(mixed)).toBe('unknown')
  })

  it('withholds the tile counts when their sources did not load', () => {
    const t = indexTiles({ useCaseIds: ['UC-1', 'UC-2'], testCases: [], paths, sources: OK, doorsChecked: false })
    expect(t).toMatchObject({ useCases: 2, partlyProven: 1, pending: 1 })
    // No door was ever checked, so "0 blocked" would be a guess.
    expect(t.blocked).toBeNull()
    const down = indexTiles({ useCaseIds: ['UC-1'], paths, sources: { ...OK, runs: 'error' } })
    expect(down.partlyProven).toBeNull()
    expect(down.pending).toBeNull()
  })
})

describe('mechanismModel', () => {
  it('counts the test cases that rely on each mechanism and never claims the tenant has it', () => {
    const scenarios = [
      scen('SIM-A', 'TC-1', ['TC-1'], ['BIOC', 'XQL', 'XQL']),
      scen('SIM-B', 'TC-2', ['TC-2'], ['Correlation', 'Analytics']),
    ]
    const paths = buildProofPaths([tc('TC-1', 'UC-1'), tc('TC-2', 'UC-1'), tc('TC-3', 'UC-1')], {
      scenarios, sources: OK,
    })
    const { cards, rows } = mechanismModel(paths, scenarios)
    expect(cards.map((c) => c.type)).toEqual(['BIOC', 'ABIOC', 'XQL', 'Correlation', 'IOC', 'Analytics'])
    const byType = Object.fromEntries(cards.map((c) => [c.type, c]))
    expect(byType.XQL).toMatchObject({ tcs: 1, objects: 2 })
    expect(byType.ABIOC).toMatchObject({ tcs: 0, objects: 0 })
    expect(cards.every((c) => c.tenant === 'unknown')).toBe(true)
    // TC-3 has no scenario, so it relies on no mechanism yet.
    expect(rows.map((r) => [r.path.tcId, r.mechanisms])).toEqual([
      ['TC-1', ['BIOC', 'XQL']],
      ['TC-2', ['Correlation', 'Analytics']],
    ])
    expect(rows.every((r) => r.tenant === 'unknown')).toBe(true)
  })
})
