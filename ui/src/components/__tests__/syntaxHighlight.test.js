/**
 * Unit tests for the XQL / YAML tokenisers in syntaxHighlight.js.
 *
 * The contract these tests lock in is that the tokens, when joined
 * back together, equal the original input byte-for-byte — the copy
 * button on the TTP detail panel relies on this round-trip.
 */
import { describe, it, expect } from 'vitest'
import {
  highlightXql,
  highlightYaml,
  tokeniserFor,
} from '../console/syntaxHighlight.js'

const joinTokens = (toks) => toks.map((t) => t.text).join('')

describe('highlightXql', () => {
  it('returns an empty array for empty input', () => {
    expect(highlightXql('')).toEqual([])
    expect(highlightXql(null)).toEqual([])
    expect(highlightXql(undefined)).toEqual([])
  })

  it('round-trips arbitrary input byte-for-byte', () => {
    const src =
      'preset = xdr_data\n' +
      '| filter event_type = ENUM.NETWORK and src_host != "10.0.0.1"\n' +
      '| fields _time, src_host, dst_host, rpc_opnum'
    const tokens = highlightXql(src)
    expect(joinTokens(tokens)).toBe(src)
  })

  it('tags XQL keywords with type=keyword', () => {
    const tokens = highlightXql('preset = xdr_data | filter event_type = "x"')
    const keywords = tokens.filter((t) => t.type === 'keyword').map((t) => t.text.toLowerCase())
    expect(keywords).toContain('preset')
    expect(keywords).toContain('filter')
  })

  it('preserves operator casing on the original input', () => {
    const tokens = highlightXql('PRESET = xdr_data | FILTER y = 1')
    const text = tokens.find((t) => t.text === 'PRESET')
    expect(text).toBeTruthy()
    expect(text.type).toBe('keyword')
  })

  it('classifies double + single quoted strings as type=string', () => {
    const dq = highlightXql('filter x = "hello"')
    const sq = highlightXql("filter x = 'hello'")
    expect(dq.find((t) => t.type === 'string').text).toBe('"hello"')
    expect(sq.find((t) => t.type === 'string').text).toBe("'hello'")
  })

  it('treats numbers as type=number', () => {
    const tokens = highlightXql('filter port = 8443 and ratio = 0.75')
    const nums = tokens.filter((t) => t.type === 'number').map((t) => t.text)
    expect(nums).toEqual(['8443', '0.75'])
  })

  it('emits comments as a single token', () => {
    const src = 'preset = xdr_data // DRSBind, DRSGetNCChanges\n| filter x = 1'
    const tokens = highlightXql(src)
    const comments = tokens.filter((t) => t.type === 'comment').map((t) => t.text)
    expect(comments).toEqual(['// DRSBind, DRSGetNCChanges'])
    expect(joinTokens(tokens)).toBe(src)
  })

  it('tags known XQL aggregate functions as type=builtin', () => {
    const tokens = highlightXql('comp count() as hits by src_host')
    const builtins = tokens.filter((t) => t.type === 'builtin').map((t) => t.text.toLowerCase())
    expect(builtins).toContain('count')
  })
})

describe('highlightYaml', () => {
  it('round-trips arbitrary input byte-for-byte', () => {
    const src = [
      'title: DCSync detection',
      'logsource:',
      '  product: windows',
      '  service: security',
      'detection:',
      '  selection:',
      '    EventID: 4662',
      '  condition: selection',
      'tags:',
      '  - attack.credential_access',
      '  - attack.t1003.006',
    ].join('\n')
    const tokens = highlightYaml(src)
    expect(joinTokens(tokens)).toBe(src)
  })

  it('tags YAML keys as type=keyword', () => {
    const tokens = highlightYaml('title: DCSync\nproduct: windows')
    const keywords = tokens.filter((t) => t.type === 'keyword').map((t) => t.text)
    expect(keywords).toContain('title')
    expect(keywords).toContain('product')
  })

  it('classifies YAML booleans + null as type=builtin', () => {
    const tokens = highlightYaml('enabled: true\nlimit: null\nfailing: false')
    const builtins = tokens.filter((t) => t.type === 'builtin').map((t) => t.text)
    expect(builtins).toEqual(expect.arrayContaining(['true', 'null', 'false']))
  })

  it('treats `# comment` lines as type=comment', () => {
    const src = 'title: A\n# this is a header\nbody: B'
    const tokens = highlightYaml(src)
    const comments = tokens.filter((t) => t.type === 'comment').map((t) => t.text)
    expect(comments).toEqual(['# this is a header'])
  })

  it('does not split colons inside quoted strings', () => {
    const src = 'description: "ratio: 1:2"'
    const tokens = highlightYaml(src)
    // The whole quoted region must round-trip
    expect(joinTokens(tokens)).toBe(src)
    expect(tokens.find((t) => t.text === 'description').type).toBe('keyword')
    expect(tokens.find((t) => t.type === 'string').text).toBe('"ratio: 1:2"')
  })

  it('handles list bullets', () => {
    const tokens = highlightYaml('tags:\n  - first\n  - second')
    const bullets = tokens.filter((t) => t.type === 'operator' && t.text.includes('-'))
    expect(bullets.length).toBeGreaterThanOrEqual(2)
  })
})

describe('tokeniserFor', () => {
  it('routes analytics_modules to YAML', () => {
    expect(tokeniserFor('analytics_modules')).toBe(highlightYaml)
  })

  it('routes BIOC / XQL / correlation / IOC to XQL', () => {
    expect(tokeniserFor('biocs')).toBe(highlightXql)
    expect(tokeniserFor('xql_queries')).toBe(highlightXql)
    expect(tokeniserFor('correlation_rules')).toBe(highlightXql)
    expect(tokeniserFor('iocs')).toBe(highlightXql)
  })

  it('falls back to XQL for unknown kinds', () => {
    expect(tokeniserFor('something_new')).toBe(highlightXql)
  })
})
