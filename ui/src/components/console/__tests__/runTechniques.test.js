/**
 * runTechniques — the model behind the run Techniques tab.
 *
 * Pins: placement goes through the TTP card (sub-technique, technique, then
 * parent), never the scenario's single tactic; a failed run never produces a
 * `miss`; an unplaceable technique is reported with its reason; an empty
 * stage is flagged empty, not green.
 */
import { describe, it, expect } from 'vitest'
import { buildTechniqueModel, resultTone, placeTechnique, worstTone } from '../runTechniques.js'
import { TACTIC_IDS } from '../attackTactics.js'

const CARD = {
  ttp_id: 'TTP-2026-0032',
  mitre_attack: {
    matrix: 'enterprise',
    techniques: [
      { technique_id: 'T1003', subtechnique_id: 'T1003.008', name: 'OS Credential Dumping: /etc/passwd and /etc/shadow', tactic_ids: ['TA0006'], tactic_names: ['Credential Access'] },
      { technique_id: 'T1087', subtechnique_id: 'T1087.001', name: 'Account Discovery: Local Account', tactic_ids: ['TA0007'], tactic_names: ['Discovery'] },
      { technique_id: 'T1078', name: 'Valid Accounts', tactic_ids: ['TA0001', 'TA0003'], tactic_names: ['Initial Access', 'Persistence'] },
    ],
  },
}

function row(over) {
  return { id: 1, run_id: 'r1', step_id: 'step-01', ttp_ref: 'TTP-2026-0032', observed: false, outcome: null, kpi_verdict: null, ...over }
}

describe('resultTone mirrors engine.outcome.row_status', () => {
  it('hit on detected/prevented, or on observed with no outcome', () => {
    expect(resultTone({ outcome: 'detected' }, false)).toBe('hit')
    expect(resultTone({ outcome: 'prevented' }, false)).toBe('hit')
    expect(resultTone({ outcome: null, observed: true }, false)).toBe('hit')
  })
  it('a pending outcome is never a hit, even when observed', () => {
    expect(resultTone({ outcome: 'pending', observed: true }, true)).toBe('pending')
  })
  it('missed reads miss ONLY on a proven run', () => {
    expect(resultTone({ outcome: 'missed' }, true)).toBe('miss')
    expect(resultTone({ outcome: 'missed' }, false)).toBe('pending')
  })
  it('not_reached / not_executed leave the denominator', () => {
    expect(resultTone({ outcome: 'not_reached' }, true)).toBe('excluded')
    expect(resultTone({ outcome: 'not_executed' }, true)).toBe('excluded')
  })
})

describe('placeTechnique', () => {
  it('matches the sub-technique entry, then the exact technique, then the parent', () => {
    expect(placeTechnique('T1003.008', CARD)).toMatchObject({ tacticIds: ['TA0006'], via: 'subtechnique' })
    expect(placeTechnique('T1078', CARD)).toMatchObject({ tacticIds: ['TA0001', 'TA0003'], via: 'technique' })
    expect(placeTechnique('T1078.004', CARD)).toMatchObject({ tacticIds: ['TA0001', 'TA0003'], via: 'parent' })
    expect(placeTechnique('T9999', CARD)).toBeNull()
    expect(placeTechnique('T1003.008', null)).toBeNull()
  })
})

describe('worstTone', () => {
  it('ranks miss > pending > hit, and is empty with nothing to judge', () => {
    expect(worstTone(['hit', 'miss', 'pending'])).toBe('miss')
    expect(worstTone(['hit', 'pending'])).toBe('pending')
    expect(worstTone(['hit', 'excluded'])).toBe('hit')
    expect(worstTone([])).toBe('empty')
    expect(worstTone(['excluded'])).toBe('excluded')
  })
})

describe('buildTechniqueModel', () => {
  const RESULTS = [
    row({ id: 1, step_id: 'step-01', mitre_technique: 'T1087.001', outcome: 'detected', observed: true, kpi_verdict: 'pass' }),
    row({ id: 2, step_id: 'step-02', mitre_technique: 'T1003.008', outcome: 'missed' }),
    row({ id: 3, step_id: 'step-02', mitre_technique: 'T1003.008', outcome: null, observed: true }),
    row({ id: 4, step_id: 'step-03', mitre_technique: 'T1078.004', outcome: 'not_reached' }),
    row({ id: 5, step_id: 'step-04', mitre_technique: 'T1566', ttp_ref: 'TTP-2026-0999' }),
    row({ id: 6, step_id: 'step-05', mitre_technique: '' }),
  ]

  it('places techniques through the card and draws every other stage empty', () => {
    const m = buildTechniqueModel({ results: RESULTS, run: { status: 'complete', tc_verdict: 'fail' }, cards: { 'TTP-2026-0032': CARD } })
    expect(m.stages.map((s) => s.id)).toEqual(TACTIC_IDS)
    const by = Object.fromEntries(m.stages.map((s) => [s.id, s]))
    expect(by.TA0007.techniques.map((t) => t.id)).toEqual(['T1087.001'])
    expect(by.TA0006.techniques.map((t) => t.id)).toEqual(['T1003.008'])
    // T1078.004 is placed through its PARENT T1078, in both of its tactics.
    expect(by.TA0001.techniques.map((t) => t.id)).toEqual(['T1078.004'])
    expect(by.TA0003.techniques.map((t) => t.id)).toEqual(['T1078.004'])
    expect(m.stages.filter((s) => s.empty).map((s) => s.id)).toEqual(
      TACTIC_IDS.filter((id) => !['TA0007', 'TA0006', 'TA0001', 'TA0003'].includes(id)),
    )
    expect(by.TA0002.tone).toBe('empty')
  })

  it('on a PROVEN run a missed row makes its technique and stage read miss', () => {
    const m = buildTechniqueModel({ results: RESULTS, run: { status: 'complete' }, cards: { 'TTP-2026-0032': CARD } })
    const t = m.techniques.find((x) => x.id === 'T1003.008')
    expect(t.tone).toBe('miss')
    expect(t.hits).toBe(1)
    expect(t.denominator).toBe(2)
    expect(m.stages.find((s) => s.id === 'TA0006').tone).toBe('miss')
    expect(m.stages.find((s) => s.id === 'TA0007').tone).toBe('hit')
  })

  it('on a FAILED run nothing ever reads miss', () => {
    const m = buildTechniqueModel({ results: RESULTS, run: { status: 'failed' }, cards: { 'TTP-2026-0032': CARD } })
    expect(m.proven).toBe(false)
    expect(m.techniques.every((t) => t.tone !== 'miss')).toBe(true)
    expect(m.stages.every((s) => s.tone !== 'miss')).toBe(true)
    expect(m.techniques.find((x) => x.id === 'T1003.008').tone).toBe('pending')
  })

  it('on a RUNNING run a missed row is still pending', () => {
    const m = buildTechniqueModel({ results: RESULTS, run: { status: 'running' }, cards: { 'TTP-2026-0032': CARD } })
    expect(m.techniques.find((x) => x.id === 'T1003.008').tone).toBe('pending')
  })

  it('reports unplaceable techniques with the reason, never guesses a stage', () => {
    const m = buildTechniqueModel({
      results: RESULTS, run: { status: 'complete' },
      cards: { 'TTP-2026-0032': CARD }, cardErrors: { 'TTP-2026-0999': 'HTTP 404' },
    })
    const ids = m.unplaced.map((u) => [u.id, u.reason])
    expect(ids).toEqual([
      ['(no technique)', '1 row(s) carry no technique'],
      ['T1566', 'card TTP-2026-0999 could not be loaded: HTTP 404'],
    ])
    // and a technique the card simply does not list
    const m2 = buildTechniqueModel({ results: [row({ mitre_technique: 'T1566' })], run: { status: 'complete' }, cards: { 'TTP-2026-0032': CARD } })
    expect(m2.unplaced[0].reason).toBe('card TTP-2026-0032 does not list T1566')
    const m3 = buildTechniqueModel({ results: [row({ mitre_technique: 'T1566', ttp_ref: null })], run: { status: 'complete' } })
    expect(m3.unplaced[0].reason).toBe('no TTP card referenced by its rows')
  })

  it('keeps excluded rows out of the denominator and counts them', () => {
    const m = buildTechniqueModel({ results: RESULTS, run: { status: 'complete' }, cards: { 'TTP-2026-0032': CARD } })
    expect(m.totals).toEqual({ rows: 6, hits: 2, denominator: 5, excluded: 1 })
    expect(m.techniques.find((x) => x.id === 'T1078.004')).toMatchObject({ hits: 0, denominator: 0, excluded: 1, tone: 'excluded' })
  })

  it('summarises outcomes and kpi verdicts per technique', () => {
    const m = buildTechniqueModel({ results: RESULTS, run: { status: 'complete' }, cards: { 'TTP-2026-0032': CARD } })
    expect(m.techniques.find((x) => x.id === 'T1003.008').outcomes).toEqual({ missed: 1, observed: 1 })
    expect(m.techniques.find((x) => x.id === 'T1087.001').kpi).toEqual({ pass: 1 })
    expect(m.techniques.find((x) => x.id === 'T1003.008').kpi).toEqual({ unscored: 2 })
  })

  it('is empty, not wrong, with no rows', () => {
    const m = buildTechniqueModel({ results: [], run: { status: 'complete' } })
    expect(m.stages.every((s) => s.empty)).toBe(true)
    expect(m.totals.rows).toBe(0)
  })
})
