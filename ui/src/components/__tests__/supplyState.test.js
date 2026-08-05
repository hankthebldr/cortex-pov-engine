import { describe, it, expect } from 'vitest'
import {
  SUPPLY, supplyOf, normalizeShelf, shelfCounts, hostOf, scenarioIdsFrom,
} from '../console/supplyState.js'

/**
 * The manufactured-green guard lives here, not in a comment.
 *
 * A green STAGED chip on a tool that is not on the shelf tells a DC the target
 * needs no egress when it does. The step then runs without its tool, produces
 * no detection, and the absent detection reads in a POV report as "Cortex
 * missed it". Every assertion below exists to make that unreachable.
 */

const shelfBody = ({ payloads = [], declared = [], writable = true } = {}) => ({
  payloads, total: payloads.length, dist_dir: '/app/payloads', auth: 'open', writable, declared,
})

const LINPEAS_DECLARED = {
  name: 'linpeas.sh',
  url: 'https://github.com/carlospolop/PEASS-ng/releases/download/20250601/linpeas.sh',
  kind: 'file',
  sha256: 'a'.repeat(64),
  pinned: true,
  pin_type: 'release-tag',
  pin_ref: '20250601',
  license: 'MIT',
  adapter_id: 'TOOL-LINPEAS',
  stage_path: '/tmp/linpeas.sh',
  mode: '0755',
  used_by: 'SIM-CDR-001, SIM-EDR-022',
}

const LINPEAS_STAGED = {
  name: 'linpeas.sh', filename: 'linpeas.sh', size_bytes: 1106683,
  sha256: 'a'.repeat(64), modified_at: '2026-08-05T13:05:00',
  source_url: LINPEAS_DECLARED.url, license: 'MIT', pinned: true, adapter_id: 'TOOL-LINPEAS',
}

const t4 = { adapter_id: 'TOOL-LINPEAS', tier: 4, name: 'LinPEAS', license: 'MIT' }
const t2 = { adapter_id: 'TOOL-SIGNALBENCH', tier: 2, name: 'signalbench' }

describe('supplyOf', () => {
  it('reports STAGED only when the bytes are actually on the shelf', () => {
    const shelf = normalizeShelf(shelfBody({ payloads: [LINPEAS_STAGED], declared: [LINPEAS_DECLARED] }))
    const s = supplyOf(t4, shelf)
    expect(s.state).toBe(SUPPLY.STAGED)
    expect(s.egress).toBe('none')
    expect(s.sha256).toBe('a'.repeat(64))
    expect(s.label).toBe('STAGED')
  })

  it('reports UNSTAGED — with the host the target would reach — when declared but absent', () => {
    const shelf = normalizeShelf(shelfBody({ payloads: [], declared: [LINPEAS_DECLARED] }))
    const s = supplyOf(t4, shelf)
    expect(s.state).toBe(SUPPLY.UNSTAGED)
    expect(s.egress).toBe('target_host')
    expect(s.egressLabel).toContain('github.com')
    expect(s.actionable).toBe(true)
  })

  it('reports RUNTIME_FETCH for a tier-4 pack that declares no artifact', () => {
    const shelf = normalizeShelf(shelfBody({ declared: [] }))
    const s = supplyOf({ adapter_id: 'TOOL-HYDRA', tier: 4 }, shelf)
    expect(s.state).toBe(SUPPLY.RUNTIME_FETCH)
    expect(s.egress).toBe('target_host')
    // Not offerable: the URL is never parsed out of runtime_install_command.
    expect(s.actionable).toBe(false)
  })

  it('reports NOT_APPLICABLE for a non-tier-4 adapter, naming the tier reason', () => {
    const shelf = normalizeShelf(shelfBody())
    const s = supplyOf(t2, shelf)
    expect(s.state).toBe(SUPPLY.NOT_APPLICABLE)
    expect(s.egress).toBe('none')
    expect(s.egressLabel).toMatch(/submodule/)
  })

  it('reports UNKNOWN — never STAGED or UNSTAGED — when the shelf could not be read', () => {
    const shelf = normalizeShelf(null)
    for (const adapter of [t4, t2, { adapter_id: 'X', tier: 4 }, { adapter_id: 'Y', tier: 1 }]) {
      const s = supplyOf(adapter, shelf)
      expect(s.state).toBe(SUPPLY.UNKNOWN)
      expect(s.state).not.toBe(SUPPLY.STAGED)
      expect(s.state).not.toBe(SUPPLY.UNSTAGED)
    }
  })

  it('reports UNKNOWN for tier 4 when the response carried no declaration at all', () => {
    // A legacy SimCore answers /api/k8s/payloads without `declared[]`. We cannot
    // tell an unstaged declared artifact from a pack that declares none, and
    // guessing either way is a claim we have not earned.
    const legacy = normalizeShelf({ payloads: [], dist_dir: '/app/payloads', auth: 'open', writable: true })
    const s = supplyOf(t4, legacy)
    expect(s.state).toBe(SUPPLY.UNKNOWN)
  })

  it('never coerces UNKNOWN into a definite state on ANY input shape', () => {
    const inputs = [
      undefined, null, {}, { payloads: null }, { declared: null }, { payloads: [], declared: null },
    ]
    for (const body of inputs) {
      const shelf = normalizeShelf(body)
      const s = supplyOf(t4, shelf)
      expect([SUPPLY.UNKNOWN, SUPPLY.RUNTIME_FETCH]).toContain(s.state)
      expect(s.state).not.toBe(SUPPLY.STAGED)
    }
  })

  it('carries both licences so a disagreement can be rendered rather than resolved', () => {
    const shelf = normalizeShelf(shelfBody({
      payloads: [{ ...LINPEAS_STAGED, license: 'GPL-3.0' }],
      declared: [{ ...LINPEAS_DECLARED, license: 'GPL-3.0' }],
    }))
    const s = supplyOf({ ...t4, license: 'MIT' }, shelf)
    expect(s.license).toBe('GPL-3.0')
    expect(s.packLicense).toBe('MIT')
  })
})

describe('normalizeShelf', () => {
  it('surfaces staged-but-UNBOUND artifacts — bytes nothing can compose', () => {
    // The repo's real state today: two artifacts on the shelf, zero packs
    // declaring one. Counting them as coverage would be a lie.
    const shelf = normalizeShelf(shelfBody({
      payloads: [
        { name: 'deepce.sh', sha256: 'b'.repeat(64), adapter_id: null },
        { name: 'linpeas.sh', sha256: 'a'.repeat(64), adapter_id: null },
      ],
      declared: [],
    }))
    expect(shelf.unbound.map((u) => u.name)).toEqual(['deepce.sh', 'linpeas.sh'])
  })

  it('prefers /artifacts over the inline declared[] when both are present', () => {
    const shelf = normalizeShelf(
      shelfBody({ declared: [{ ...LINPEAS_DECLARED, pin_type: undefined }] }),
      { artifacts: [LINPEAS_DECLARED] },
    )
    expect(shelf.byAdapter['TOOL-LINPEAS'].declared.pin_type).toBe('release-tag')
  })
})

describe('shelfCounts', () => {
  it('sums the target-egress risk across mixed tiers', () => {
    const shelf = normalizeShelf(shelfBody({ payloads: [LINPEAS_STAGED], declared: [LINPEAS_DECLARED] }))
    const counts = shelfCounts(
      [t4, t2, { adapter_id: 'TOOL-HYDRA', tier: 4 }, { adapter_id: 'TOOL-NUCLEI', tier: 4 }],
      shelf,
    )
    expect(counts.staged).toBe(1)
    expect(counts.runtime_fetch).toBe(2)
    expect(counts.not_applicable).toBe(1)
    expect(counts.target_egress).toBe(2)
  })
})

describe('scenarioIdsFrom', () => {
  it('parses a comma-joined list', () => {
    expect(scenarioIdsFrom('SIM-CDR-001, SIM-EDR-022')).toEqual(['SIM-CDR-001', 'SIM-EDR-022'])
  })

  it('drops the prose the API returns when nothing references the adapter', () => {
    // Observed on a live SimCore: payload_shelf.py emits this literal sentence
    // in `used_by` rather than an empty string. A naive split() offered it as a
    // scenario id, and composing against it would 404.
    expect(scenarioIdsFrom('(no scenario references this adapter yet)')).toEqual([])
    expect(scenarioIdsFrom('')).toEqual([])
    expect(scenarioIdsFrom(undefined)).toEqual([])
  })
})

describe('hostOf', () => {
  it('extracts a hostname and never throws on rubbish', () => {
    expect(hostOf('https://github.com/a/b')).toBe('github.com')
    expect(hostOf('not a url')).toBeNull()
    expect(hostOf(null)).toBeNull()
  })
})
