/**
 * composerContent — the tenant-content status logic the Composer draws from.
 *
 * The first half asserts `spec/tenant_content_cases.json`, the SAME fixture
 * `tests/engine/test_tenant_content.py` asserts against the server resolver, so
 * the canvas and the export cannot disagree about a rule's status. The second
 * half covers what only the browser has: the chip wording and the headline.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import {
  ownershipOf, slugDetectionId, classifyRule, parseLogic, deriveTenantContent,
  detectionChips, contentHeadline, ruleLines, TenantContentError,
} from '../console/composerContent.js'
import { normalizeStep } from '../console/composerDraft.js'

const here = dirname(fileURLToPath(import.meta.url))
const CASES = JSON.parse(
  readFileSync(resolve(here, '../../../../spec/tenant_content_cases.json'), 'utf8'),
)

/** The fixture speaks the API's snake_case; project the module's output onto it. */
function project(content) {
  return {
    cited_cards: content.citedCards,
    cards_missing: content.cardsMissing,
    no_card_binding: content.noCardBinding,
    rules: content.rules.map((r) => ({
      rule_id: r.ruleId,
      ttp_ref: r.ttpRef,
      name: r.name,
      content_status: r.contentStatus,
      binding: r.binding,
      logic_kind: r.logicKind,
      declared_by: r.declaredBy,
      inputs: r.inputs.map((i) => ({
        kind: i.kind, name: i.name, state: i.state, resolved_kind: i.resolvedKind, steps: i.steps,
      })),
      feeders: r.feeders.map((f) => ({ step: f.stepId, inputs: f.inputs, declares: f.declares })),
      anchor_step: r.anchorStep,
      edge_kind: r.edgeKind,
    })),
    unbound: content.unbound.map((u) => ({ step: u.stepId, index: u.index, reason: u.reason })),
    summary: {
      total: content.summary.total,
      deployable: content.summary.deployable,
      intent_only: content.summary.intentOnly,
      unreadable: content.summary.unreadable,
      declared: content.summary.declared,
      card_only: content.summary.cardOnly,
      unbound: content.summary.unbound,
    },
  }
}

describe('shared fixture — ownership', () => {
  for (const c of CASES.ownership) {
    if (c.error) {
      it(`raises on an unknown detection type ${JSON.stringify(c.type)}`, () => {
        expect(() => ownershipOf(c.type)).toThrow(TenantContentError)
      })
    } else {
      it(`${c.type} is ${c.ownership}`, () => {
        expect(ownershipOf(c.type)).toBe(c.ownership)
      })
    }
  }
})

describe('shared fixture — detection-id slugs match the catalog', () => {
  for (const c of CASES.slugs) {
    it(`${c.prefix}: ${c.name.slice(0, 40)}`, () => {
      expect(slugDetectionId(c.name, c.prefix)).toBe(c.slug)
    })
  }
})

describe('shared fixture — rule content status', () => {
  for (const c of CASES.classify) {
    if (c.error) {
      it(`raises: ${c.label}`, () => {
        expect(() => classifyRule(c.rule)).toThrow(TenantContentError)
      })
    } else {
      it(`${c.status}: ${c.label}`, () => {
        expect(classifyRule(c.rule)).toBe(c.status)
      })
    }
  }
})

describe('shared fixture — logic parser', () => {
  for (const c of CASES.logic) {
    if (c.error) {
      it(`raises: ${c.label}`, () => {
        expect(() => parseLogic(c.logic)).toThrow(TenantContentError)
      })
    } else {
      it(`${c.kind}: ${c.label}`, () => {
        expect(parseLogic(c.logic)).toEqual({ kind: c.kind, terms: c.terms })
      })
    }
  }
})

describe('shared fixture — a chain against its cards', () => {
  for (const c of CASES.scenarios) {
    it(c.label, () => {
      // Through the Composer's own step normaliser, so the test reads the
      // fields a real draft carries (`ttpRef` / `detectionId`).
      const steps = c.steps.map((s, i) => normalizeStep(s, i))
      expect(project(deriveTenantContent(steps, c.cards))).toEqual(c.expected)
    })
  }
})

describe('deriveTenantContent — what the fixture does not say', () => {
  const big = CASES.scenarios[0]
  const steps = big.steps.map((s, i) => normalizeStep(s, i))

  it('tallies each rule\'s inputs by state', () => {
    const rule = deriveTenantContent(steps, big.cards).rules[1]
    expect(rule.tally).toEqual({ total: 5, step: 2, cardOnly: 2, unknown: 1 })
  })

  it('keeps the reason a rule is unreadable', () => {
    const odd = deriveTenantContent(steps, big.cards).rules[3]
    expect(odd.contentStatus).toBe('unreadable')
    expect(odd.contentError).toMatch(/severity/)
  })

  it('keeps the reason a logic line could not be parsed', () => {
    const unclosed = deriveTenantContent(steps, big.cards).rules[4]
    expect(unclosed.logicKind).toBe('unparsed')
    expect(unclosed.logicError).toMatch(/unclosed|closing/i)
  })

  it('gives every rule a key no step id can take', () => {
    const { rules } = deriveTenantContent(steps, big.cards)
    expect(rules.map((r) => r.key)).toEqual([
      'rule:TTP-T-0001:CR-T-0001', 'rule:TTP-T-0001:CR-T-0002',
      'rule:TTP-T-0001:correlation-rule-prose', 'rule:TTP-T-0001:CR-T-0004',
      'rule:TTP-T-0001:CR-T-0005',
    ])
  })

  it('does not mutate the steps or the cards', () => {
    const before = JSON.stringify([steps, big.cards])
    deriveTenantContent(steps, big.cards)
    expect(JSON.stringify([steps, big.cards])).toBe(before)
  })

  it('reads a missing card map as "no cards loaded", never as "no rules"', () => {
    const content = deriveTenantContent(steps, null)
    expect(content.rules).toEqual([])
    expect(content.cardsMissing).toEqual(['TTP-T-0001', 'TTP-T-0002'])
    expect(content.noCardBinding).toBe(false)
  })
})

describe('detectionChips — the step\'s proof contract in words', () => {
  const det = (type) => ({ type })

  it('groups three XQL expectations into one "run" chip', () => {
    expect(detectionChips([det('XQL'), det('XQL'), det('XQL')])).toEqual([
      { ownership: 'run', type: 'XQL', count: 3, label: 'run · 3 queries', error: false },
    ])
  })

  it('says one query, not one queries', () => {
    expect(detectionChips([det('XQL')])[0].label).toBe('run · 1 query')
  })

  it('orders run, then deploy, then built-in, whatever the step order', () => {
    const chips = detectionChips([
      det('ABIOC'), det('Correlation'), det('BIOC'), det('XQL'), det('BIOC'), det('IOC'),
      det('Analytics'), det('Prevention'),
    ])
    expect(chips.map((c) => c.label)).toEqual([
      'run · 1 query',
      'deploy · 2 BIOCs',
      'deploy · 1 IOC',
      'deploy · 1 rule',
      'built-in · 1 ABIOC',
      'built-in · 1 Analytics',
      'built-in · 1 Prevention',
    ])
  })

  it('renders an unknown type as an error chip, never under a default class', () => {
    const chips = detectionChips([det('XQL'), det('Sigma'), det(null)])
    expect(chips).toEqual([
      { ownership: 'run', type: 'XQL', count: 1, label: 'run · 1 query', error: false },
      { ownership: null, type: 'Sigma', count: 1, label: '? Sigma', error: true },
      { ownership: null, type: null, count: 1, label: '? untyped', error: true },
    ])
  })

  it('returns no chip for no detections', () => {
    expect(detectionChips([])).toEqual([])
    expect(detectionChips(null)).toEqual([])
  })
})

describe('contentHeadline — the chrome control never says more than is known', () => {
  const big = CASES.scenarios[0]
  const steps = big.steps.map((s, i) => normalizeStep(s, i))

  it('names the count and the worst status', () => {
    const content = deriveTenantContent(steps, big.cards)
    // 5 rules, one unreadable: unreadable outranks intent-only. The second
    // cited card never loaded, and the line says so instead of going quiet.
    expect(contentHeadline(content)).toBe('5 rules · 1 unreadable · 1 card not loaded')
  })

  it('reads "intent only" when every rule is a stub', () => {
    const cards = JSON.parse(JSON.stringify(big.cards))
    cards['TTP-T-0001'].detections.correlation_rules.splice(3, 1)
    const one = { ...cards, 'TTP-T-0002': { id: 'TTP-T-0002', detections: {} } }
    expect(contentHeadline(deriveTenantContent(steps, one))).toBe('4 rules · intent only')
  })

  it('says cards are still loading rather than "0 rules"', () => {
    const content = deriveTenantContent(steps, {})
    expect(contentHeadline(content, { loading: true })).toBe('loading cards…')
  })

  it('says a card could not be read rather than "0 rules"', () => {
    const content = deriveTenantContent(steps, {})
    expect(contentHeadline(content)).toBe('2 cards not loaded')
  })

  it('says a chain cites no card', () => {
    const none = CASES.scenarios[1]
    const s = none.steps.map((x, i) => normalizeStep(x, i))
    expect(contentHeadline(deriveTenantContent(s, {}))).toBe('no card cited')
  })

  it('says a cited card carries no rule', () => {
    const cards = { 'TTP-T-0001': { id: 'TTP-T-0001', detections: {} }, 'TTP-T-0002': { id: 'TTP-T-0002', detections: {} } }
    expect(contentHeadline(deriveTenantContent(steps, cards))).toBe('no rule on the cited cards')
  })
})

describe('ruleLines — a rule node prints both statuses as words', () => {
  const big = CASES.scenarios[0]
  const steps = big.steps.map((s, i) => normalizeStep(s, i))
  const [declared, cardOnly, prose, odd, unclosed] = deriveTenantContent(steps, big.cards).rules

  it('says an intent-only rule has nothing to import', () => {
    expect(ruleLines(cardOnly).content).toBe('INTENT ONLY · nothing to import')
  })

  it('says a card-only rule is not declared and not scored', () => {
    expect(ruleLines(cardOnly).binding).toBe('not declared by this scenario · not scored')
  })

  it('says a declared stub is scored although nothing can raise it', () => {
    expect(ruleLines(declared).binding).toBe('scored by this run · nothing here can raise it')
  })

  it('does not warn about a declared rule that is deployable', () => {
    const ready = { ...declared, contentStatus: 'deployable' }
    expect(ruleLines(ready).binding).toBe('scored by this run')
    expect(ruleLines(ready).content).toBe('authored · not imported into any tenant')
  })

  it('tallies the inputs that resolved to a step', () => {
    expect(ruleLines(declared).inputs).toBe('2 of 2 inputs on steps')
    expect(ruleLines(cardOnly).inputs).toBe('2 of 5 inputs on steps · 1 not on the card')
  })

  it('says prose inputs are not machine-readable', () => {
    expect(ruleLines(prose).inputs).toBe('inputs not machine-readable')
    expect(ruleLines(unclosed).inputs).toBe('inputs not machine-readable')
  })

  it('gives the reason an unreadable rule could not be read', () => {
    expect(ruleLines(odd).content).toBe('card shape not recognised')
    expect(ruleLines(odd).inputs).toMatch(/severity/)
  })
})
