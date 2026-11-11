/**
 * composerSignal — a step's signal path: the MECHANISM that produces its
 * evidence (spec 2026-11-10-composer-doors-design.md).
 *
 * The cases are `spec/signal_path_cases.json`, the same fixture
 * `tests/engine/test_signal_path.py` asserts against the Python derivation,
 * so the Lanes lens and the loader's S-28 lint cannot disagree about a step.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import {
  signalOf, signalBadge, pluginClass, SIGNAL_PATHS, COLLECTOR_PLUGINS, WIRE_PLUGINS,
} from '../console/composerSignal.js'
import { normalizeStep } from '../console/composerDraft.js'

const here = dirname(fileURLToPath(import.meta.url))
const CASES = JSON.parse(
  readFileSync(resolve(here, '../../../../spec/signal_path_cases.json'), 'utf8'),
)

const project = (sig) => ({
  path: sig.path, runs_on: sig.runsOn, plugins: sig.plugins, unplaced: sig.unplaced,
})

describe('shared fixture — plugin classes', () => {
  it('the collector set is the fixture\'s, which a Python test pins to the registry', () => {
    expect([...COLLECTOR_PLUGINS].sort()).toEqual(CASES.plugins.collector)
  })
  it('the wire set is the fixture\'s', () => {
    expect([...WIRE_PLUGINS].sort()).toEqual(CASES.plugins.wire)
  })
  it('no plugin is in both', () => {
    expect(CASES.plugins.collector.filter((p) => WIRE_PLUGINS.has(p))).toEqual([])
  })
  it('classifies a known plugin and returns null for any other name', () => {
    expect(pluginClass('ngfw_eal_emitter')).toBe('collector')
    expect(pluginClass('llm_provider_egress')).toBe('wire')
    expect(pluginClass('made_up_emitter')).toBeNull()
    expect(pluginClass(null)).toBeNull()
  })
})

describe('shared fixture — paths', () => {
  it('names the five paths in lane order', () => {
    expect(SIGNAL_PATHS).toEqual(CASES.paths)
  })
})

describe('shared fixture — a step\'s signal path', () => {
  for (const c of CASES.cases) {
    it(c.label, () => {
      // The corpus/API shape, as the Runs topology reads it.
      expect(project(signalOf(c.step))).toEqual(c.expected)
    })
    it(`${c.label} (through the Composer's step model)`, () => {
      const step = normalizeStep({ id: 'step-01', ...c.step }, 0)
      expect(project(signalOf(step))).toEqual(c.expected)
    })
  }
})

describe('shared fixture — the door badge', () => {
  for (const b of CASES.badges) {
    it(`${b.signal.runs_on} ${b.signal.path}${b.signal.unplaced ? ` (${b.signal.unplaced})` : ''} → "${b.badge}"`, () => {
      expect(signalBadge({ path: b.signal.path, runsOn: b.signal.runs_on, unplaced: b.signal.unplaced })).toBe(b.badge)
    })
  }
})

describe('signalOf — what the fixture does not say', () => {
  it('a missing step is unplaced, never a lane by default', () => {
    expect(signalOf(null)).toMatchObject({ path: 'ENDPOINT', unplaced: 'no-step' })
  })

  it('says why a step is unplaced, in words', () => {
    const sig = signalOf({ command: "cat > /tmp/c.yml <<'YML'\nsteps:\n  - plugin: made_up_emitter\nYML\npython3 -m scripts.eal_simulator.cli run /tmp/c.yml" })
    expect(sig.reason).toMatch(/made_up_emitter/)
    expect(sig.reason).toMatch(/registry/)
  })

  it('reads a plugin line however the campaign YAML indents or quotes it', () => {
    const cmd = "cat > /tmp/c.yml <<'YML'\nsteps:\n- plugin: \"ssh_egress\"\n-   plugin: 'ftp_egress'\nYML\npython3 -m scripts.eal_simulator.cli run /tmp/c.yml"
    expect(signalOf({ command: cmd })).toMatchObject({ path: 'WIRE', plugins: ['ftp_egress', 'ssh_egress'] })
  })

  it('does not mutate the step', () => {
    const step = { command: 'aws s3 ls', channel: null }
    const before = JSON.stringify(step)
    signalOf(step)
    expect(JSON.stringify(step)).toBe(before)
  })
})
