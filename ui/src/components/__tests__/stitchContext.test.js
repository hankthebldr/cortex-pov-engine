/**
 * stitchContext — the pure authoring model behind the Composer's Stitch panel.
 *
 * The rules worth pinning are the HONESTY ones: the vocabularies MUST mirror the
 * backend byte-for-byte (a picker that offers a key/directive the backend
 * rejects authors a draft that 422s at save), the picker MUST refuse to author a
 * rejected value (an incompatible directive returns the same ref, like
 * setCausalityParent refusing a forward ref), and this module NEVER resolves a
 * value — it only decides what is a well-formed intent.
 */
import { describe, it, expect } from 'vitest'
import {
  ENTITY_KEYS,
  DIRECTIVES,
  NICE_GROUPS,
  DIRECTIVE_COMPAT,
  KEY_NICE,
  STITCH_PLACEHOLDER_RE,
  directivesForKey,
  parseStitchContext,
  emitStitchContext,
  setEntity,
  validateStitchContext,
  plantedKeys,
  fiveTupleComplete,
  stitchPlaceholdersIn,
  stitchInsertToken,
} from '../console/stitchContext.js'

// ─── Frozen vocabularies ──────────────────────────────────────────────────────

describe('vocabularies mirror the backend', () => {
  it('ENTITY_KEYS is the nine keys in exact order', () => {
    expect(ENTITY_KEYS).toEqual([
      'host', 'src_ip', 'dst_ip', 'src_port', 'dst_port',
      'protocol', 'container_id', 'account', 'cloud_resource',
    ])
    expect(ENTITY_KEYS).toHaveLength(9)
  })

  it('DIRECTIVES is the six Phase-2 directives', () => {
    expect(DIRECTIVES).toEqual([
      'auto_ip', 'auto_port', 'auto_5tuple',
      'canary_principal', 'from_agent', 'auto_container_id',
    ])
    expect(DIRECTIVES).toHaveLength(6)
  })

  it('NICE_GROUPS partitions all nine keys — container_id is Cloud, host is Endpoint', () => {
    const grouped = NICE_GROUPS.flatMap((g) => g.keys)
    expect(grouped.slice().sort()).toEqual(ENTITY_KEYS.slice().sort())
    expect(grouped).toHaveLength(9) // no key in two groups
    expect(KEY_NICE.container_id).toBe('Cloud')
    expect(KEY_NICE.host).toBe('Endpoint')
    expect(KEY_NICE.account).toBe('Identity')
    expect(KEY_NICE.src_ip).toBe('Network')
  })

  it('the frozen constants are actually frozen', () => {
    expect(Object.isFrozen(ENTITY_KEYS)).toBe(true)
    expect(Object.isFrozen(DIRECTIVES)).toBe(true)
    expect(Object.isFrozen(DIRECTIVE_COMPAT)).toBe(true)
  })
})

// ─── directivesForKey ─────────────────────────────────────────────────────────

describe('directivesForKey', () => {
  it('src_ip offers auto_ip, auto_5tuple, from_agent — in DIRECTIVES order', () => {
    expect(directivesForKey('src_ip')).toEqual(['auto_ip', 'auto_5tuple', 'from_agent'])
  })

  it('host offers only from_agent (auto_5tuple is REJECTED on host)', () => {
    expect(directivesForKey('host')).toEqual(['from_agent'])
  })

  it('account offers only canary_principal', () => {
    expect(directivesForKey('account')).toEqual(['canary_principal'])
  })

  it('container_id offers only auto_container_id', () => {
    expect(directivesForKey('container_id')).toEqual(['auto_container_id'])
  })

  it('dst_port offers auto_port and auto_5tuple', () => {
    expect(directivesForKey('dst_port')).toEqual(['auto_port', 'auto_5tuple'])
  })

  it('cloud_resource has NO directive — literal only', () => {
    expect(directivesForKey('cloud_resource')).toEqual([])
  })
})

// ─── parse / emit round-trip ──────────────────────────────────────────────────

describe('parseStitchContext / emitStitchContext', () => {
  it('null and {} both normalise to null (context-less)', () => {
    expect(parseStitchContext(null)).toBeNull()
    expect(parseStitchContext({})).toBeNull()
    expect(parseStitchContext(undefined)).toBeNull()
    expect(emitStitchContext(null)).toBeNull()
    expect(emitStitchContext({})).toBeNull()
  })

  it('round-trips a mixed literal + resolve spec', () => {
    const raw = {
      dst_ip: { literal: '203.0.113.10' },
      dst_port: { literal: 443 },
      src_ip: { resolve: 'auto_ip' },
      account: { resolve: 'canary_principal' },
      cloud_resource: { literal: 'arn:aws:s3:::acme-logs' },
    }
    const model = parseStitchContext(raw)
    expect(emitStitchContext(model)).toEqual(raw)
  })

  it('emit drops an entry an incompatible directive would 422 on', () => {
    // A saved-but-invalid spec (auto_5tuple on host) parses losslessly for the
    // panel to render, but is NOT emitted onto the wire.
    const model = parseStitchContext({ host: { resolve: 'auto_5tuple' }, dst_port: { literal: 443 } })
    expect(emitStitchContext(model)).toEqual({ dst_port: { literal: 443 } })
  })

  it('emit drops a both-keys entry (validation is separate)', () => {
    const model = parseStitchContext({ src_ip: { literal: '10.0.0.1', resolve: 'auto_ip' } })
    expect(emitStitchContext(model)).toBeNull()
  })
})

// ─── setEntity immutability + refusal ─────────────────────────────────────────

describe('setEntity', () => {
  it('sets a literal immutably', () => {
    const next = setEntity(null, 'dst_port', { literal: 443 })
    expect(next).toEqual({ dst_port: { literal: 443 } })
  })

  it('returns the SAME ref on a no-op (identical literal)', () => {
    const model = { dst_port: { literal: 443 } }
    expect(setEntity(model, 'dst_port', { literal: 443 })).toBe(model)
  })

  it('REFUSES an incompatible directive — auto_5tuple on host returns same ref', () => {
    const model = { host: { resolve: 'from_agent' } }
    expect(setEntity(model, 'host', { resolve: 'auto_5tuple' })).toBe(model)
  })

  it('REFUSES an unknown key — returns same ref', () => {
    const model = { host: { resolve: 'from_agent' } }
    expect(setEntity(model, 'not_a_key', { literal: 'x' })).toBe(model)
  })

  it('clears a key with null, and returns same ref when the key was already absent', () => {
    const model = { host: { resolve: 'from_agent' }, dst_port: { literal: 443 } }
    expect(setEntity(model, 'host', null)).toEqual({ dst_port: { literal: 443 } })
    expect(setEntity(model, 'src_ip', null)).toBe(model)
  })

  it('clearing the last key yields null, not {}', () => {
    const model = { host: { resolve: 'from_agent' } }
    expect(setEntity(model, 'host', null)).toBeNull()
  })

  it('accepts a compatible resolve directive', () => {
    expect(setEntity(null, 'src_ip', { resolve: 'auto_ip' })).toEqual({ src_ip: { resolve: 'auto_ip' } })
  })
})

// ─── validation ───────────────────────────────────────────────────────────────

describe('validateStitchContext', () => {
  it('ok on a valid spec, with plantedKeys and byNice', () => {
    const model = { dst_ip: { literal: '203.0.113.10' }, account: { resolve: 'canary_principal' } }
    const v = validateStitchContext(model)
    expect(v.ok).toBe(true)
    expect(v.problems).toEqual([])
    expect(v.plantedKeys).toEqual(['dst_ip', 'account'])
    expect(v.byNice).toEqual({ Network: ['dst_ip'], Identity: ['account'] })
  })

  it('names a both-or-neither violation', () => {
    const both = validateStitchContext({ src_ip: { literal: '10.0.0.1', resolve: 'auto_ip' } })
    expect(both.ok).toBe(false)
    expect(both.problems.join(' ')).toMatch(/both a literal and a resolve/)

    const neither = validateStitchContext({ src_ip: {} })
    expect(neither.ok).toBe(false)
    expect(neither.problems.join(' ')).toMatch(/neither a literal nor a resolve/)
  })

  it('names an unknown directive and an incompatible directive', () => {
    const unknown = validateStitchContext({ src_ip: { resolve: 'auto_teleport' } })
    expect(unknown.problems.join(' ')).toMatch(/unknown directive "auto_teleport"/)

    const incompatible = validateStitchContext({ host: { resolve: 'auto_5tuple' } })
    expect(incompatible.problems.join(' ')).toMatch(/cannot resolve/)
  })

  it('names an unknown key', () => {
    const v = validateStitchContext({ not_a_key: { literal: 'x' } })
    expect(v.problems.join(' ')).toMatch(/Unknown entity key "not_a_key"/)
  })
})

// ─── fiveTupleComplete ────────────────────────────────────────────────────────

describe('fiveTupleComplete', () => {
  it('true when all five tuple keys are declared', () => {
    const model = {
      src_ip: { resolve: 'auto_ip' }, src_port: { resolve: 'auto_port' },
      dst_ip: { literal: '203.0.113.10' }, dst_port: { literal: 443 },
      protocol: { literal: 'tcp' },
    }
    expect(fiveTupleComplete(model)).toBe(true)
  })

  it('true when a single key carries auto_5tuple', () => {
    expect(fiveTupleComplete({ src_ip: { resolve: 'auto_5tuple' } })).toBe(true)
  })

  it('false when only some tuple keys are declared', () => {
    expect(fiveTupleComplete({ src_ip: { resolve: 'auto_ip' }, dst_ip: { literal: '1.1.1.1' } })).toBe(false)
    expect(fiveTupleComplete(null)).toBe(false)
  })
})

// ─── plantedKeys ──────────────────────────────────────────────────────────────

describe('plantedKeys', () => {
  it('returns declared keys in ENTITY_KEYS order', () => {
    const model = { account: { resolve: 'canary_principal' }, host: { resolve: 'from_agent' } }
    expect(plantedKeys(model)).toEqual(['host', 'account'])
    expect(plantedKeys(null)).toEqual([])
  })
})

// ─── the {stitch:*} grammar ───────────────────────────────────────────────────

describe('stitchPlaceholdersIn / stitchInsertToken', () => {
  it('extracts {stitch:*} keys, unique in appearance order, ignoring non-matches', () => {
    const cmd = 'curl --local-port {stitch:src_port} https://{stitch:dst_ip}:{stitch:dst_port}/beacon {stitch:src_port}'
    expect(stitchPlaceholdersIn(cmd)).toEqual(['src_port', 'dst_ip', 'dst_port'])
  })

  it('returns [] for a command with no placeholders or a non-string', () => {
    expect(stitchPlaceholdersIn('id -u')).toEqual([])
    expect(stitchPlaceholdersIn('')).toEqual([])
    expect(stitchPlaceholdersIn(null)).toEqual([])
  })

  it('is not confused by the shared global regex lastIndex across calls', () => {
    const cmd = 'echo {stitch:host}'
    expect(stitchPlaceholdersIn(cmd)).toEqual(['host'])
    expect(stitchPlaceholdersIn(cmd)).toEqual(['host'])
  })

  it('stitchInsertToken wraps a key', () => {
    expect(stitchInsertToken('dst_ip')).toBe('{stitch:dst_ip}')
  })

  it('STITCH_PLACEHOLDER_RE source mirrors the backend', () => {
    expect(STITCH_PLACEHOLDER_RE.source).toBe('\\{stitch:([a-z_]+)\\}')
  })
})
