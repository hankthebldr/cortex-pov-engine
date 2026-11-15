/**
 * catalogBindings — the pure join + sort/group logic behind
 * Catalog › TTP cards "Group by" / "Sort by".
 *
 * A TTP card carries no UC/TC ref of its own (and must not — see the
 * card-namespace guard). The binding is indirect:
 *
 *   card ◄─ scenario.steps[].expected_detections[].ttp_ref
 *           scenario.tc_ref / tc_refs[] ─► test case ─► use case ─► fy27_subdomain
 *
 * The cases below pin the two things a flat render cannot show: WHICH group a
 * card lands in, and — more importantly — when the join must refuse to answer
 * instead of answering "nothing".
 */
import { describe, it, expect } from 'vitest'
import {
  BINDING_STATUS,
  UNBOUND_KEYS,
  buildCardBindings,
  organizeCards,
  bindingSearchText,
  parseOrganize,
  organizeUrlPatch,
} from '../catalogBindings.js'

/* ── fixtures: the summary-projection shapes the API actually serves ── */

const det = (ttp_ref, extra = {}) => ({ type: 'BIOC', plane: 'EDR', ...(ttp_ref ? { ttp_ref } : {}), ...extra })

const scenarios = [
  {
    scenario_id: 'SIM-EDR-001', uc_ref: 'UCS-EDR-02', tc_ref: 'TC-EDR-03',
    tc_refs: ['TC-EDR-03', 'TC-IR-01'],
    steps: [
      { id: 'step-01', expected_detections: [det('TTP-2026-0032'), det('TTP-2026-0032')] },
      { id: 'step-02', expected_detections: [det(null)] },
    ],
  },
  {
    scenario_id: 'SIM-ITDR-006', uc_ref: 'UCS-ITDR-04', tc_ref: 'TC-ITDR-10',
    tc_refs: ['TC-ITDR-10'],
    steps: [{ id: 'step-01', expected_detections: [det('TTP-2026-0004')] }],
  },
  {
    // Second scenario citing the DCSync card, bound to a DIFFERENT use case.
    scenario_id: 'SIM-MP-002', uc_ref: 'UCS-IR-01', tc_ref: 'TC-IR-01',
    tc_refs: ['TC-IR-01', 'TC-ITDR-2'],
    steps: [{ id: 'step-01', expected_detections: [det('TTP-2026-0004')] }],
  },
]

const testCases = [
  { tc_id: 'TC-EDR-03', title: 'Multi-method malware detection', ucs_id: 'UCS-EDR-02', ucs_name: 'Malware Detection Gaps', uc_id: 'UC-EDR', use_case: 'Endpoint Detection & Response' },
  { tc_id: 'TC-IR-01', title: 'Auto-triage reduces manual review', ucs_id: 'UCS-IR-01', ucs_name: 'Analyst Fatigue', uc_id: 'UC-IR', use_case: 'Incident Response' },
  { tc_id: 'TC-ITDR-10', title: 'Kerberoast detection', ucs_id: 'UCS-ITDR-04', ucs_name: 'Roasting', uc_id: 'UC-ITDR', use_case: 'Identity Threat Detection & Response' },
  { tc_id: 'TC-ITDR-2', title: 'Impossible travel', ucs_id: 'UCS-ITDR-01', ucs_name: 'Sign-in risk', uc_id: 'UC-ITDR', use_case: 'Identity Threat Detection & Response' },
]

const useCases = [
  { uc_id: 'UC-EDR', use_case: 'Endpoint Detection & Response', fy27_subdomain: 'Endpoint Protection' },
  { uc_id: 'UC-IR', use_case: 'Incident Response', fy27_subdomain: 'SOC Transformation' },
  { uc_id: 'UC-ITDR', use_case: 'Identity Threat Detection & Response', fy27_subdomain: 'Identity Security' },
]

const cards = [
  { id: 'TTP-2026-0004', name: 'DCSync', detection_counts: { biocs: 3, xql_queries: 3, correlation_rules: 2 } },
  { id: 'TTP-2026-0032', name: 'Shadow file read', detection_counts: { biocs: 5, xql_queries: 6 } },
  { id: 'TTP-2026-0099', name: 'Authored, never cited', detection_counts: { iocs: 1 } },
]

const ok = () => buildCardBindings({ scenarios, testCases, useCases, indexLoaded: true })
const ids = (list) => list.map((c) => c.id)

/* ─────────────────────────── the join ─────────────────────────── */

describe('buildCardBindings — card → scenario → test case → use case → subdomain', () => {
  it('resolves a card to every test case its scenario evidences, with UC and subdomain', () => {
    const { status, byCard } = ok()
    expect(status).toBe(BINDING_STATUS.OK)

    const b = byCard.get('TTP-2026-0032')
    expect(b.map((x) => x.tcId)).toEqual(['TC-EDR-03', 'TC-IR-01'])
    expect(b[0]).toMatchObject({
      tcId: 'TC-EDR-03', ucId: 'UC-EDR', ucsId: 'UCS-EDR-02',
      useCase: 'Endpoint Detection & Response', subdomain: 'Endpoint Protection',
      primary: true, resolved: true, scenarioIds: ['SIM-EDR-001'],
    })
    // tc_refs[] beyond tc_ref is evidence, not the primary binding.
    expect(b[1]).toMatchObject({ tcId: 'TC-IR-01', subdomain: 'SOC Transformation', primary: false })
  })

  it('merges two scenarios citing one card: primary wins, scenario ids union', () => {
    const b = ok().byCard.get('TTP-2026-0004')
    const ir = b.find((x) => x.tcId === 'TC-IR-01')
    // SIM-MP-002 holds TC-IR-01 as PRIMARY even though it is nobody else's.
    expect(ir.primary).toBe(true)
    expect(b.find((x) => x.tcId === 'TC-ITDR-10').scenarioIds).toEqual(['SIM-ITDR-006'])
    expect(b.find((x) => x.tcId === 'TC-ITDR-2').primary).toBe(false)
  })

  it('counts a scenario once per card however many detections cite it', () => {
    const { scenariosByCard } = ok()
    expect(scenariosByCard.get('TTP-2026-0032')).toEqual(['SIM-EDR-001'])
    expect(scenariosByCard.get('TTP-2026-0004')).toEqual(['SIM-ITDR-006', 'SIM-MP-002'])
  })

  it('a card no scenario cites has NO bindings — and the status is still ok', () => {
    const { status, byCard, scenariosByCard } = ok()
    expect(status).toBe(BINDING_STATUS.OK)
    expect(byCard.has('TTP-2026-0032')).toBe(true)   // the join ran…
    expect(byCard.has('TTP-2026-0099')).toBe(false)  // …and found nothing for this one
    expect(scenariosByCard.has('TTP-2026-0099')).toBe(false)
  })

  it('keeps a ref the loaded index does not know VISIBLE as unresolved, never dropped', () => {
    const drifted = [{
      scenario_id: 'SIM-X-001', tc_ref: 'TC-GONE-01', tc_refs: ['TC-GONE-01'],
      steps: [{ expected_detections: [det('TTP-2026-0032')] }],
    }]
    const b = buildCardBindings({ scenarios: drifted, testCases, useCases, indexLoaded: true })
      .byCard.get('TTP-2026-0032')
    expect(b).toHaveLength(1)
    expect(b[0]).toMatchObject({ tcId: 'TC-GONE-01', resolved: false, ucId: null, subdomain: null })
  })
})

/* ───────────── refusing to answer: degraded is not zero ───────────── */

describe('buildCardBindings — degraded states are not "nothing bound"', () => {
  it('LINKS_MISSING when no detection in the list carries ttp_ref (older SimCore)', () => {
    // The pre-change summary projection: {type, plane} only.
    const old = scenarios.map((s) => ({
      ...s,
      steps: s.steps.map((st) => ({
        ...st,
        expected_detections: st.expected_detections.map(({ type, plane }) => ({ type, plane })),
      })),
    }))
    const out = buildCardBindings({ scenarios: old, testCases, useCases, indexLoaded: true })
    expect(out.status).toBe(BINDING_STATUS.LINKS_MISSING)
    // The tolerant bug: an empty map that the view would render as
    // "191 cards, no scenario cites any of them".
    expect(out.byCard.size).toBe(0)
    expect(out.reason).toMatch(/ttp_ref/)
  })

  it('LINKS_MISSING when the scenario list is empty — zero scenarios is not zero bindings', () => {
    const out = buildCardBindings({ scenarios: [], testCases, useCases, indexLoaded: true })
    expect(out.status).toBe(BINDING_STATUS.LINKS_MISSING)
  })

  it('INDEX_MISSING on a snapshot-less deploy, even though the scenario refs exist', () => {
    const out = buildCardBindings({ scenarios, testCases: [], useCases: [], indexLoaded: false })
    expect(out.status).toBe(BINDING_STATUS.INDEX_MISSING)
    expect(out.byCard.size).toBe(0)
    expect(out.reason).toMatch(/index/i)
  })

  it('INDEX_MISSING when index_loaded is true but no test case rows arrived', () => {
    const out = buildCardBindings({ scenarios, testCases: [], useCases, indexLoaded: true })
    expect(out.status).toBe(BINDING_STATUS.INDEX_MISSING)
  })

  it('raises on a non-list payload instead of reading it as empty', () => {
    expect(() => buildCardBindings({ scenarios: { scenarios }, testCases, useCases, indexLoaded: true }))
      .toThrow(/scenarios/)
    expect(() => buildCardBindings({ scenarios, testCases: null, useCases, indexLoaded: true }))
      .toThrow(/testCases/)
  })
})

/* ─────────────────────────── grouping ─────────────────────────── */

describe('organizeCards — group by', () => {
  const group = (groupBy, extra = {}) =>
    organizeCards(cards, ok(), { groupBy, sortBy: 'id', membership: 'all', ...extra })

  it('groupBy none is one flat list with no groups', () => {
    const out = organizeCards([...cards].reverse(), ok(), { groupBy: 'none', sortBy: 'id', membership: 'all' })
    expect(out.groups).toBeNull()
    expect(ids(out.flat)).toEqual(['TTP-2026-0004', 'TTP-2026-0032', 'TTP-2026-0099'])
  })

  it('subdomain: alphabetical groups, the uncited card in its own bucket LAST', () => {
    const out = group('subdomain')
    expect(out.groups.map((g) => g.label)).toEqual([
      'Endpoint Protection', 'Identity Security', 'SOC Transformation',
      'No scenario cites this card',
    ])
    expect(out.groups.at(-1).key).toBe(UNBOUND_KEYS.UNCITED)
    expect(ids(out.groups.at(-1).cards)).toEqual(['TTP-2026-0099'])
  })

  it('membership "all" lists a card under every subdomain it evidences', () => {
    const by = Object.fromEntries(group('subdomain').groups.map((g) => [g.label, ids(g.cards)]))
    expect(by['Endpoint Protection']).toEqual(['TTP-2026-0032'])
    expect(by['SOC Transformation']).toEqual(['TTP-2026-0004', 'TTP-2026-0032'])
    expect(by['Identity Security']).toEqual(['TTP-2026-0004'])
  })

  it('each group says WHICH binding placed a card there, so a secondary placement explains itself', () => {
    const soc = group('subdomain').groups.find((g) => g.label === 'SOC Transformation')
    // 0032's own lead binding is TC-EDR-03 (Endpoint Protection); it is in
    // SOC Transformation only through the secondary TC-IR-01.
    expect(soc.via['TTP-2026-0032']).toHaveLength(1)
    expect(soc.via['TTP-2026-0032'][0]).toMatchObject({ tcId: 'TC-IR-01', primary: false })
    // 0004 is here through a PRIMARY binding.
    expect(soc.via['TTP-2026-0004'][0]).toMatchObject({ tcId: 'TC-IR-01', primary: true })
    // The uncited bucket has no binding to cite.
    expect(group('subdomain').groups.at(-1).via).toEqual({})
  })

  it('membership "primary" lists a card only under its primary test case(s)', () => {
    const out = group('subdomain', { membership: 'primary' })
    const by = Object.fromEntries(out.groups.map((g) => [g.label, ids(g.cards)]))
    // TTP-0032's TC-IR-01 is secondary evidence, so it leaves SOC Transformation.
    expect(by['SOC Transformation']).toEqual(['TTP-2026-0004'])
    expect(by['Endpoint Protection']).toEqual(['TTP-2026-0032'])
  })

  it('reports placements apart from unique cards so an overlapping total cannot pass as a count', () => {
    const out = group('subdomain')
    expect(out.uniqueCards).toBe(3)
    expect(out.placements).toBe(5)          // 0004 ×2, 0032 ×2, 0099 ×1
    expect(out.multiPlaced).toBe(2)
    expect(group('subdomain', { membership: 'primary' }).multiPlaced).toBe(1) // 0004: two primaries
  })

  it('use case: keyed by uc_id, labelled with the name and its subdomain', () => {
    const out = group('uc')
    expect(out.groups.map((g) => g.key)).toEqual(['UC-EDR', 'UC-IR', 'UC-ITDR', UNBOUND_KEYS.UNCITED])
    expect(out.groups[0]).toMatchObject({
      label: 'UC-EDR', title: 'Endpoint Detection & Response', sublabel: 'Endpoint Protection',
    })
    expect(out.groups[0].link).toEqual({ uc: 'UC-EDR' })
  })

  it('test case: natural id order (TC-ITDR-2 before TC-ITDR-10), titled, linked', () => {
    const out = group('tc')
    expect(out.groups.map((g) => g.key)).toEqual([
      'TC-EDR-03', 'TC-IR-01', 'TC-ITDR-2', 'TC-ITDR-10', UNBOUND_KEYS.UNCITED,
    ])
    const tc = out.groups.find((g) => g.key === 'TC-ITDR-10')
    expect(tc).toMatchObject({ title: 'Kerberoast detection', sublabel: 'UC-ITDR · Identity Security' })
    expect(tc.link).toEqual({ tc: 'TC-ITDR-10' })
  })

  it('an unresolved ref gets its own bucket, distinct from "no scenario cites this card"', () => {
    const drifted = [{
      scenario_id: 'SIM-X-001', tc_ref: 'TC-GONE-01', tc_refs: ['TC-GONE-01'],
      steps: [{ expected_detections: [det('TTP-2026-0032')] }],
    }]
    const b = buildCardBindings({ scenarios: drifted, testCases, useCases, indexLoaded: true })
    const out = organizeCards(cards, b, { groupBy: 'uc', sortBy: 'id', membership: 'all' })
    expect(out.groups.map((g) => g.key)).toEqual([UNBOUND_KEYS.UNRESOLVED, UNBOUND_KEYS.UNCITED])
    expect(ids(out.groups[0].cards)).toEqual(['TTP-2026-0032'])
    expect(ids(out.groups[1].cards)).toEqual(['TTP-2026-0004', 'TTP-2026-0099'])
  })

  it('REFUSES to group while the bindings are degraded — no "everything unbound" bucket', () => {
    const degraded = buildCardBindings({ scenarios: [], testCases, useCases, indexLoaded: true })
    const out = organizeCards(cards, degraded, { groupBy: 'subdomain', sortBy: 'id', membership: 'all' })
    expect(out.groups).toBeNull()
    expect(out.groupingRefused).toBe(true)
    expect(ids(out.flat)).toEqual(['TTP-2026-0004', 'TTP-2026-0032', 'TTP-2026-0099'])
  })
})

/* ─────────────────────────── sorting ─────────────────────────── */

describe('organizeCards — sort by', () => {
  const sort = (sortBy) =>
    ids(organizeCards(cards, ok(), { groupBy: 'none', sortBy, membership: 'all' }).flat)

  it('name', () => {
    expect(sort('name')).toEqual(['TTP-2026-0099', 'TTP-2026-0004', 'TTP-2026-0032'])
  })

  it('detections, most first', () => {
    // 0032 = 11, 0004 = 8, 0099 = 1 — deliberately NOT id order.
    expect(sort('detections')).toEqual(['TTP-2026-0032', 'TTP-2026-0004', 'TTP-2026-0099'])
  })

  it('use case: by the PRIMARY binding, unbound cards last', () => {
    // 0032 → UC-EDR; 0004 → primaries UC-IR + UC-ITDR, first by id is UC-IR.
    expect(sort('uc')).toEqual(['TTP-2026-0032', 'TTP-2026-0004', 'TTP-2026-0099'])
  })

  it('test case: natural order on the primary test case id', () => {
    expect(sort('tc')).toEqual(['TTP-2026-0032', 'TTP-2026-0004', 'TTP-2026-0099'])
  })

  it('subdomain: alphabetical on the primary binding', () => {
    // Endpoint Protection (0032) < Identity Security (0004's first primary by name).
    expect(sort('subdomain')).toEqual(['TTP-2026-0032', 'TTP-2026-0004', 'TTP-2026-0099'])
  })

  it('an index sort falls back to id order, flagged, while bindings are degraded', () => {
    const degraded = buildCardBindings({ scenarios: [], testCases, useCases, indexLoaded: true })
    const out = organizeCards(cards, degraded, { groupBy: 'none', sortBy: 'tc', membership: 'all' })
    expect(ids(out.flat)).toEqual(['TTP-2026-0004', 'TTP-2026-0032', 'TTP-2026-0099'])
    expect(out.sortRefused).toBe(true)
  })

  it('sorts cards inside each group too', () => {
    const out = organizeCards(cards, ok(), { groupBy: 'subdomain', sortBy: 'name', membership: 'all' })
    const soc = out.groups.find((g) => g.label === 'SOC Transformation')
    expect(ids(soc.cards)).toEqual(['TTP-2026-0004', 'TTP-2026-0032']) // DCSync < Shadow file read
  })
})

/* ─────────────────────────── search text ─────────────────────────── */

describe('bindingSearchText', () => {
  it('lets a DC find a card by test case id, use case name, subdomain or scenario id', () => {
    const text = bindingSearchText(ok().byCard.get('TTP-2026-0032')).toLowerCase()
    for (const needle of ['tc-edr-03', 'uc-edr', 'endpoint protection', 'incident response', 'sim-edr-001']) {
      expect(text).toContain(needle)
    }
    // An unbound card contributes nothing — and must not throw.
    expect(bindingSearchText(undefined)).toBe('')
  })
})

/* ─────────────────────────── URL state ─────────────────────────── */

describe('parseOrganize / organizeUrlPatch — ?group=&sort=&include=', () => {
  it('reads a deep link and falls back to the default on anything it does not know', () => {
    expect(parseOrganize({ group: 'tc', sort: 'detections', include: 'primary' }))
      .toEqual({ groupBy: 'tc', sortBy: 'detections', membership: 'primary' })
    // An unknown value must select the default, not "nothing".
    expect(parseOrganize({ group: 'plane', sort: '', include: 'everything' }))
      .toEqual({ groupBy: 'none', sortBy: 'id', membership: 'all' })
    expect(parseOrganize(undefined)).toEqual({ groupBy: 'none', sortBy: 'id', membership: 'all' })
  })

  it('writes a choice under its URL key, and a default as null so the hash drops it', () => {
    expect(organizeUrlPatch('groupBy', 'subdomain')).toEqual({ group: 'subdomain' })
    expect(organizeUrlPatch('membership', 'primary')).toEqual({ include: 'primary' })
    expect(organizeUrlPatch('groupBy', 'none')).toEqual({ group: null })
    expect(organizeUrlPatch('sortBy', 'id')).toEqual({ sort: null })
  })

  it('round-trips: what one writes the other reads', () => {
    const patch = { ...organizeUrlPatch('groupBy', 'uc'), ...organizeUrlPatch('sortBy', 'name') }
    expect(parseOrganize(patch)).toMatchObject({ groupBy: 'uc', sortBy: 'name' })
  })
})
