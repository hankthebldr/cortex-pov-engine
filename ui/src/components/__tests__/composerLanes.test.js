/**
 * composerLanes — the Composer's swimlane model.
 *
 * A lane is a step's SIGNAL PATH: the mechanism that produces its evidence
 * (`composerSignal.signalOf`), not the plane that observes it. The rules pinned
 * here are the ones a drift would silently break: `laneOf` is the ONE
 * derivation the timeline, the canvas and the Runs topology all ask; the plane
 * never decides a lane; the Engine lane holds tenant content and no step; and
 * a lane is derived, so nothing in the draft can override it.
 *
 * Spec: docs/superpowers/specs/2026-11-10-composer-doors-design.md
 */
import { describe, it, expect } from 'vitest'
import {
  BANDS,
  LANE_COL_PITCH,
  LANE_COL_X,
  LANE_KEYS,
  STEP_LANES,
  laneAtY,
  laneOf,
  layoutLanes,
} from '../console/composerLanes.js'
import { SIGNAL_PATHS } from '../console/composerSignal.js'
import { LANE_BANDS } from '../console/povdata/corpus.js'
import { LAYOUT } from '../console/composerLayout.js'
import * as draftOps from '../console/composerDraft.js'

const step = (id, over = {}) => ({
  id, name: id, command: 'cat /etc/hostname', identity: '', technique: null, platforms: [],
  causalityParent: null, causalityPivot: null, detections: [], channel: null,
  target: null, eal: null, ...over,
})

const campaign = (plugin) => "cat > /tmp/c.yml <<'YML'\nsteps:\n  - step_id: s1\n"
  + `    plugin: ${plugin}\nYML\npython3 -m scripts.eal_simulator.cli run /tmp/c.yml --live`

describe('lane vocabulary', () => {
  it('bands the five signal paths between LAUNCH and the Engine and PROOF', () => {
    expect(LANE_KEYS).toEqual(['LAUNCH', 'ENDPOINT', 'COLLECTOR', 'WIRE', 'CLOUD', 'NONE', 'ENG', 'PROOF'])
  })

  it('a step may sit only in a signal-path lane: never the Engine, never a terminal', () => {
    expect(STEP_LANES).toEqual(SIGNAL_PATHS)
    expect(STEP_LANES).not.toContain('ENG')
  })

  it('labels each path by what the step does', () => {
    expect(BANDS.ENDPOINT.label).toBe('ENDPOINT RUNTIME')
    expect(BANDS.COLLECTOR.label).toBe('LOG INJECTION')
    expect(BANDS.WIRE.label).toBe('LIVE TRAFFIC')
    expect(BANDS.CLOUD.label).toBe('CLOUD CONTROL PLANE')
    expect(BANDS.NONE.label).toBe('NO ACTION')
    expect(BANDS.ENG.label).toBe('CORTEX ENGINE')
  })

  it('names the Broker VM only on log injection, and only as one destination', () => {
    const named = LANE_BANDS.filter((b) => /Broker VM/.test(b.sub)).map((b) => b.key)
    expect(named).toEqual(['COLLECTOR'])
    expect(BANDS.COLLECTOR.sub).toMatch(/Broker VM or HTTP collector/)
  })

  it('BANDS is keyed from LANE_BANDS verbatim', () => {
    for (const b of LANE_BANDS) expect(BANDS[b.key]).toBe(b)
  })
})

describe('laneOf — the signal path, and nothing else', () => {
  it('reads each path from what the step does', () => {
    expect(laneOf(step('s'))).toBe('ENDPOINT')
    expect(laneOf(step('s', { command: campaign('ngfw_eal_emitter') }))).toBe('COLLECTOR')
    expect(laneOf(step('s', { command: campaign('llm_provider_egress') }))).toBe('WIRE')
    expect(laneOf(step('s', { command: 'aws s3 ls' }))).toBe('CLOUD')
    expect(laneOf(step('s', { command: 'echo waiting; sleep 30' }))).toBe('NONE')
    expect(laneOf(step('s', { channel: 'eal', eal: { plugin: 'k8s_audit_emitter', params: {} } }))).toBe('COLLECTOR')
  })

  // The old derivation put these behind a door they are not launched through.
  it('the plane never decides a lane', () => {
    const plain = { command: 'curl -s http://c2.lab.test/beacon' }
    expect(laneOf(step('s', { ...plain, plane: 'NDR', detections: [{ plane: 'NDR' }] }))).toBe('ENDPOINT')
    expect(laneOf(step('s', { ...plain, detections: [{ plane: 'CDR' }] }))).toBe('ENDPOINT')
    expect(laneOf(step('s', { command: 'echo marker', detections: [{ plane: 'ANALYTICS' }] }))).toBe('NONE')
    expect(laneOf(step('s', { command: campaign('email_emitter'), detections: [{ plane: 'EMAIL' }] }))).toBe('COLLECTOR')
  })

  it('an unreadable step stays visible in ENDPOINT rather than being guessed elsewhere', () => {
    expect(laneOf(step('s', { command: campaign('made_up_emitter') }))).toBe('ENDPOINT')
    expect(laneOf(step('s', { command: null }))).toBe('ENDPOINT')
    expect(laneOf(null)).toBe('ENDPOINT')
  })

  it('takes no override: whatever is passed after the step is ignored', () => {
    const s = step('s', { command: 'aws s3 ls' })
    expect(laneOf(s, { s: 'ENG' }, 'NDR')).toBe('CLOUD')
  })
})

describe('laneAtY — where a dropped node would land', () => {
  it('snaps by the node centre to the band that contains it', () => {
    for (const key of STEP_LANES) {
      const b = BANDS[key]
      expect(laneAtY(b.nodeY)).toBe(key)
    }
  })

  it('a node dragged into a terminal or the Engine band reads as the nearest step lane', () => {
    expect(laneAtY(BANDS.LAUNCH.top - LAYOUT.nodeH)).toBe('ENDPOINT')
    expect(laneAtY(BANDS.PROOF.top + BANDS.PROOF.h + 40)).toBe('NONE')
  })
})

describe('layoutLanes — x by order, y by path', () => {
  const steps = [
    step('step-01'),
    step('step-02', { command: campaign('ngfw_eal_emitter') }),
    step('step-03', { command: 'kubectl get pods' }),
    step('step-04', { command: 'sleep 60' }),
  ]

  it('places each step in its lane band at its execution column', () => {
    const { nodes } = layoutLanes(steps)
    expect(nodes.map((n) => n.id)).toEqual(['step-01', 'step-02', 'step-03', 'step-04'])
    expect(nodes.map((n) => n.lane)).toEqual(['ENDPOINT', 'COLLECTOR', 'CLOUD', 'NONE'])
    nodes.forEach((n, i) => {
      expect(n.x).toBe(LANE_COL_X + i * LANE_COL_PITCH)
      expect(n.y).toBe(BANDS[n.lane].nodeY)
      expect(n.w).toBe(LAYOUT.nodeW)
      expect(n.h).toBe(LAYOUT.nodeH)
      expect(n.kind).toBe('step')
    })
  })

  it('carries each step\'s signal so the card can say why it sits there', () => {
    const { nodes } = layoutLanes([step('s', { command: campaign('made_up_emitter') })])
    expect(nodes[0].signal).toMatchObject({ path: 'ENDPOINT', unplaced: 'unknown-plugin' })
  })

  it('tallies per-lane counts; the Engine holds no step', () => {
    const { lanes } = layoutLanes(steps)
    const count = Object.fromEntries(lanes.map((l) => [l.key, l.count]))
    expect(count).toEqual({
      LAUNCH: 0, ENDPOINT: 1, COLLECTOR: 1, WIRE: 0, CLOUD: 1, NONE: 1, ENG: 0, PROOF: 0,
    })
  })

  it('bounds span every band and every column, and never collapse on an empty chain', () => {
    const totalH = LANE_BANDS.reduce((a, b) => a + b.h, 0)
    expect(layoutLanes(steps).bounds).toEqual({
      width: LANE_COL_X * 2 + 4 * LANE_COL_PITCH,
      height: totalH,
    })
    expect(layoutLanes([]).bounds.width).toBe(LANE_COL_X * 2 + LANE_COL_PITCH)
    expect(layoutLanes([]).nodes).toEqual([])
  })
})

describe('a lane is derived: the draft carries no override', () => {
  it('setStepLane is gone', () => {
    expect(draftOps.setStepLane).toBeUndefined()
  })

  it('a draft has no laneOverrides, and the save body no composer_lanes', () => {
    expect('laneOverrides' in draftOps.emptyDraft()).toBe(false)
    const d = draftOps.draftFromScenario({ steps: [], composer_lanes: { 'step-01': 'ENG' } })
    expect('laneOverrides' in d).toBe(false)
    expect(draftOps.draftToApi({ ...d, name: 'x' }).composer_lanes).toBeUndefined()
    expect('laneOverrides' in draftOps.draftSnapshot(d)).toBe(false)
  })
})
