// @vitest-environment jsdom
/**
 * Unit tests for the MITRE ATT&CK Navigator layer exporter.
 */
import React from 'react'
import { describe, it, expect } from 'vitest'
import { buildLayer } from '../console/exportNavigatorLayer.js'

void React

const fixtureCoverage = {
  summary: { total_techniques: 4, detected: 1, run_not_detected: 1, not_run: 1 },
  by_tactic: [
    {
      tactic_id: 'TA0006',
      tactic_name: 'Credential Access',
      techniques: [
        {
          technique_id: 'T1552.001',
          technique_name: 'Credentials In Files',
          status: 'detected',
          scenarios: ['SIM-MP-004'],
          observed_detections: 1,
          total_detections: 1,
          planes: ['EDR'],
        },
        {
          technique_id: 'T1003.001',
          technique_name: 'LSASS Memory',
          status: 'run_not_detected',
          scenarios: ['SIM-EDR-001'],
          observed_detections: 0,
          total_detections: 1,
          planes: ['EDR'],
        },
        {
          technique_id: 'T1078.004',
          technique_name: 'Cloud Accounts',
          status: 'not_run',
          scenarios: ['SIM-MP-004'],
          observed_detections: 0,
          total_detections: 2,
          planes: ['EDR', 'CDR'],
        },
      ],
    },
    {
      tactic_id: 'TA0011',
      tactic_name: 'C2',
      techniques: [
        {
          technique_id: 'T1071.001',
          technique_name: 'Web Protocols',
          status: 'no_scenario',
          scenarios: [],
          observed_detections: 0,
          total_detections: 0,
          planes: [],
        },
      ],
    },
  ],
}

describe('buildLayer (ATT&CK Navigator export)', () => {
  it('produces a Navigator v4.5 envelope with the right versions block', () => {
    const layer = buildLayer(fixtureCoverage)
    expect(layer.versions.layer).toBe('4.5')
    expect(layer.versions.attack).toBeDefined()
    expect(layer.versions.navigator).toBeDefined()
    expect(layer.domain).toBe('enterprise-attack')
  })

  it('skips no_scenario techniques (not included in techniques array)', () => {
    const layer = buildLayer(fixtureCoverage)
    const ids = layer.techniques.map((t) => t.techniqueID)
    expect(ids).toContain('T1552.001')
    expect(ids).toContain('T1003.001')
    expect(ids).toContain('T1078.004')
    // T1071.001 is no_scenario → omitted
    expect(ids).not.toContain('T1071.001')
  })

  it('colors detected techniques teal-green with score 100', () => {
    const layer = buildLayer(fixtureCoverage)
    const detected = layer.techniques.find((t) => t.techniqueID === 'T1552.001')
    expect(detected.score).toBe(100)
    expect(detected.color).toBe('#4FD1A1')
  })

  it('colors run-no-detect amber with score 60', () => {
    const layer = buildLayer(fixtureCoverage)
    const runNoDetect = layer.techniques.find((t) => t.techniqueID === 'T1003.001')
    expect(runNoDetect.score).toBe(60)
    expect(runNoDetect.color).toBe('#F5A524')
  })

  it('colors not-run muted with score 30', () => {
    const layer = buildLayer(fixtureCoverage)
    const notRun = layer.techniques.find((t) => t.techniqueID === 'T1078.004')
    expect(notRun.score).toBe(30)
    expect(notRun.color).toBe('#5A6B84')
  })

  it('maps tactic IDs to Navigator shortnames', () => {
    const layer = buildLayer(fixtureCoverage)
    const credAccess = layer.techniques.find((t) => t.techniqueID === 'T1552.001')
    expect(credAccess.tactic).toBe('credential-access')
    const c2 = layer.techniques.find((t) => t.techniqueID === 'T1071.001')
    expect(c2).toBeUndefined()  // skipped (no_scenario)
  })

  it('includes scenarios + detections + planes in the cell comment', () => {
    const layer = buildLayer(fixtureCoverage)
    const detected = layer.techniques.find((t) => t.techniqueID === 'T1552.001')
    expect(detected.comment).toContain('SIM-MP-004')
    expect(detected.comment).toContain('Detections: 1/1')
    expect(detected.comment).toContain('EDR')
  })

  it('embeds a 3-stop gradient + legend', () => {
    const layer = buildLayer(fixtureCoverage)
    expect(layer.gradient.colors).toHaveLength(3)
    expect(layer.gradient.minValue).toBe(0)
    expect(layer.gradient.maxValue).toBe(100)
    expect(layer.legendItems).toHaveLength(3)
    expect(layer.legendItems[0].label).toMatch(/Detected/)
  })

  it('returns an empty layer when no coverage data', () => {
    const layer = buildLayer(null)
    expect(layer.techniques).toEqual([])
  })

  it('embeds metadata (generator, exported_at, scenario count)', () => {
    const layer = buildLayer(fixtureCoverage)
    const gen = layer.metadata.find((m) => m.name === 'generator')
    expect(gen.value).toBe('CortexSim')
    const exportedAt = layer.metadata.find((m) => m.name === 'exported_at')
    expect(exportedAt.value).toMatch(/^\d{4}-\d{2}-\d{2}T/)
  })

  it('honors a custom name + description', () => {
    const layer = buildLayer(fixtureCoverage, {
      name: 'POV-Q3-2026',
      description: 'Acme Q3 POV coverage',
    })
    expect(layer.name).toBe('POV-Q3-2026')
    expect(layer.description).toBe('Acme Q3 POV coverage')
  })
})
