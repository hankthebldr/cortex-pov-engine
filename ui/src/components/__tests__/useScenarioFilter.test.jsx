// @vitest-environment jsdom
/**
 * Unit tests for useScenarioFilter — the unified filter hook that powers
 * the FilterPalette + chip strip + rail/Coverage cross-links.
 */
import React from 'react'
import { describe, it, expect } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import useScenarioFilter from '../console/useScenarioFilter.js'

void React

const fixtures = [
  {
    scenario_id: 'SIM-EDR-001',
    name: 'LSASS dump',
    plane: 'EDR',
    mitre_tactic: 'TA0006',
    mitre_technique: 'T1003.001',
    additional_techniques: [],
    threat_report: 'Unit42 — Mimikatz analysis',
    difficulty: 'advanced',
    tags: ['lsass', 'advanced'],
    execution_identity: { default: 'root', options: ['root'] },
    detection_types: ['BIOC'],
    steps: [
      { mitre_technique: 'T1003.001', expected_detections: [{ plane: 'EDR', type: 'BIOC' }] },
    ],
  },
  {
    scenario_id: 'SIM-MP-004',
    name: 'APT29 Cloud',
    plane: 'ANALYTICS',
    mitre_tactic: 'TA0006',
    mitre_technique: 'T1552.001',
    additional_techniques: [{ technique: 'T1078.004' }],
    threat_report: 'Unit42 — APT29 Cloud TTPs',
    difficulty: 'intermediate',
    tags: ['apt29', 'intermediate'],
    execution_identity: { default: 'www-data', options: ['www-data'] },
    detection_types: ['BIOC', 'Analytics'],
    steps: [
      { mitre_technique: 'T1552.001', expected_detections: [{ plane: 'EDR', type: 'BIOC' }] },
      { mitre_technique: 'T1078.004', expected_detections: [{ plane: 'CDR', type: 'Analytics' }] },
    ],
  },
  {
    scenario_id: 'SIM-CDR-002',
    name: 'Container escape',
    plane: 'CDR',
    mitre_tactic: 'TA0004',
    mitre_technique: 'T1611',
    threat_report: 'Atomic Red Team',
    difficulty: 'basic',
    tags: ['k8s', 'basic'],
    execution_identity: { default: 'container-runtime' },
    detection_types: ['Analytics'],
    steps: [],
  },
]

describe('useScenarioFilter', () => {
  it('starts empty and returns all scenarios', () => {
    const { result } = renderHook(() => useScenarioFilter())
    expect(result.current.isEmpty).toBe(true)
    expect(result.current.activeCount).toBe(0)
    expect(result.current.applyTo(fixtures)).toHaveLength(3)
  })

  it('filters by plane (single value)', () => {
    const { result } = renderHook(() => useScenarioFilter())
    act(() => result.current.setPlane('CDR'))
    expect(result.current.isEmpty).toBe(false)
    expect(result.current.applyTo(fixtures)).toHaveLength(1)
    expect(result.current.applyTo(fixtures)[0].scenario_id).toBe('SIM-CDR-002')
  })

  it('filters by technique with explicit scenario id list (Coverage cross-link)', () => {
    const { result } = renderHook(() => useScenarioFilter())
    act(() => result.current.setTechnique('T1552.001', ['SIM-MP-004']))
    const out = result.current.applyTo(fixtures)
    expect(out).toHaveLength(1)
    expect(out[0].scenario_id).toBe('SIM-MP-004')
  })

  it('filters by technique without explicit ids — walks all scenario techniques', () => {
    const { result } = renderHook(() => useScenarioFilter())
    // T1078.004 is in additional_techniques and step.mitre_technique for SIM-MP-004
    act(() => result.current.setTechnique('T1078.004'))
    const out = result.current.applyTo(fixtures)
    expect(out).toHaveLength(1)
    expect(out[0].scenario_id).toBe('SIM-MP-004')
  })

  it('toggles a tactic and applies OR within criterion', () => {
    const { result } = renderHook(() => useScenarioFilter())
    act(() => result.current.toggle('tactics', 'TA0006'))
    expect(result.current.applyTo(fixtures).map((s) => s.scenario_id))
      .toEqual(['SIM-EDR-001', 'SIM-MP-004'])

    // Add a second tactic — OR'd in.
    act(() => result.current.toggle('tactics', 'TA0004'))
    expect(result.current.applyTo(fixtures)).toHaveLength(3)

    // Toggle back off the first — only TA0004 left.
    act(() => result.current.toggle('tactics', 'TA0006'))
    expect(result.current.applyTo(fixtures).map((s) => s.scenario_id))
      .toEqual(['SIM-CDR-002'])
  })

  it('ANDs across criteria — plane + difficulty', () => {
    const { result } = renderHook(() => useScenarioFilter())
    act(() => result.current.setPlane('ANALYTICS'))
    act(() => result.current.toggle('difficulties', 'intermediate'))
    const out = result.current.applyTo(fixtures)
    expect(out).toHaveLength(1)
    expect(out[0].scenario_id).toBe('SIM-MP-004')
  })

  it('filters by actor (matched against threat_report prefix)', () => {
    const { result } = renderHook(() => useScenarioFilter())
    act(() => result.current.toggle('actors', 'Unit42'))
    const out = result.current.applyTo(fixtures).map((s) => s.scenario_id)
    expect(out).toEqual(['SIM-EDR-001', 'SIM-MP-004'])
  })

  it('filters by execution identity (default or option)', () => {
    const { result } = renderHook(() => useScenarioFilter())
    act(() => result.current.toggle('identities', 'www-data'))
    const out = result.current.applyTo(fixtures)
    expect(out).toHaveLength(1)
    expect(out[0].scenario_id).toBe('SIM-MP-004')
  })

  it('filters by detection type (scenario- or step-declared)', () => {
    const { result } = renderHook(() => useScenarioFilter())
    act(() => result.current.toggle('detTypes', 'Analytics'))
    const out = result.current.applyTo(fixtures).map((s) => s.scenario_id)
    // SIM-MP-004 has Analytics in detection_types and step.
    // SIM-CDR-002 has Analytics in detection_types only.
    expect(out).toEqual(['SIM-MP-004', 'SIM-CDR-002'])
  })

  it('filters by tag (non-difficulty tags)', () => {
    const { result } = renderHook(() => useScenarioFilter())
    act(() => result.current.toggle('tags', 'apt29'))
    const out = result.current.applyTo(fixtures)
    expect(out).toHaveLength(1)
    expect(out[0].scenario_id).toBe('SIM-MP-004')
  })

  it('clearOne resets a single criterion without touching others', () => {
    const { result } = renderHook(() => useScenarioFilter())
    act(() => result.current.setPlane('EDR'))
    act(() => result.current.toggle('tactics', 'TA0006'))
    act(() => result.current.clearOne('tactics'))
    expect(result.current.filter.plane).toBe('EDR')
    expect(result.current.filter.tactics.size).toBe(0)
  })

  it('clearAll resets every criterion', () => {
    const { result } = renderHook(() => useScenarioFilter())
    act(() => result.current.setPlane('EDR'))
    act(() => result.current.setTechnique('T1003.001'))
    act(() => result.current.toggle('actors', 'Unit42'))
    act(() => result.current.clearAll())
    expect(result.current.isEmpty).toBe(true)
    expect(result.current.applyTo(fixtures)).toHaveLength(3)
  })

  it('activeCount counts non-empty criteria (excluding plane)', () => {
    const { result } = renderHook(() => useScenarioFilter())
    act(() => result.current.setPlane('EDR'))
    expect(result.current.activeCount).toBe(0)   // plane doesn't count
    act(() => result.current.toggle('tactics', 'TA0006'))
    expect(result.current.activeCount).toBe(1)
    act(() => result.current.toggle('actors', 'Unit42'))
    expect(result.current.activeCount).toBe(2)
  })
})
