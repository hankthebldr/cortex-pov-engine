/**
 * catalogBindings — how a TTP card relates to the FY27 UC / TC index, and how
 * Catalog › TTP cards sorts and groups by it. Pure: no fetch, no React.
 *
 * A card carries NO use-case or test-case ref of its own (a guard test fails if
 * a card id ever takes a `UC-` / `TC-` prefix). The binding is indirect and
 * AUTHORED — it says a scenario that cites the card binds the test case, not
 * that the detection ever fired:
 *
 *   card ◄── scenario.steps[].expected_detections[].ttp_ref
 *            scenario.tc_ref (primary) + tc_refs[] (full evidence set)
 *              └─► test case ─► UCS ─► use case ─► fy27_subdomain
 *
 * "Subdomain" is the index's own `fy27_subdomain` on the use-case row — the
 * FY27 Domain Consulting taxonomy — not the UCS level.
 *
 * THE RULE THIS FILE EXISTS TO KEEP: a join that could not run is not a join
 * that found nothing. Three inputs can each be missing, and every one of them
 * would otherwise render as "no scenario cites this card" for all 191 cards —
 * a false statement about coverage:
 *
 *   - the index snapshot is absent (`index_loaded: false`)   → INDEX_MISSING
 *   - the scenario list carries no `ttp_ref` at all (a SimCore
 *     older than the list projection that serves it), or no
 *     scenarios loaded                                        → LINKS_MISSING
 *   - a payload is not a list                                 → throws
 *
 * In every degraded state `organizeCards` REFUSES to group or index-sort and
 * says so (`groupingRefused` / `sortRefused`); it never invents a bucket.
 */

export const BINDING_STATUS = Object.freeze({
  LOADING: 'loading',
  OK: 'ok',
  INDEX_MISSING: 'index-missing',
  LINKS_MISSING: 'links-missing',
  ERROR: 'error',
})

/** Group keys for cards that sit under no index row. Never collide with an id. */
export const UNBOUND_KEYS = Object.freeze({
  UNRESOLVED: '__unresolved__',
  UNCITED: '__uncited__',
})

export const GROUP_OPTIONS = Object.freeze([
  { id: 'none', label: 'None' },
  { id: 'subdomain', label: 'Subdomain' },
  { id: 'uc', label: 'Use case' },
  { id: 'tc', label: 'Test case' },
])

export const SORT_OPTIONS = Object.freeze([
  { id: 'id', label: 'Card id' },
  { id: 'name', label: 'Name' },
  { id: 'subdomain', label: 'Subdomain', index: true },
  { id: 'uc', label: 'Use case', index: true },
  { id: 'tc', label: 'Test case', index: true },
  { id: 'detections', label: 'Detections' },
])

export const MEMBERSHIP_OPTIONS = Object.freeze([
  { id: 'all', label: 'Every test case evidenced' },
  { id: 'primary', label: 'Primary test case only' },
])

const INDEX_SORTS = new Set(SORT_OPTIONS.filter((o) => o.index).map((o) => o.id))
const oneOf = (options, value, fallback) =>
  (options.some((o) => o.id === value) ? value : fallback)

/** The organize state and its URL spelling, defined once: `parseOrganize` reads
 *  through these and the view writes through them, so the two cannot drift. */
export const ORGANIZE_DEFAULTS = Object.freeze({ groupBy: 'none', sortBy: 'id', membership: 'all' })
export const ORGANIZE_URL_KEYS = Object.freeze({ groupBy: 'group', sortBy: 'sort', membership: 'include' })

/** Read `?group=&sort=&include=` off the surface params; unknown values fall
 *  back to the default rather than selecting nothing. */
export function parseOrganize(params) {
  const p = params || {}
  const read = (field, options) => oneOf(options, p[ORGANIZE_URL_KEYS[field]], ORGANIZE_DEFAULTS[field])
  return {
    groupBy: read('groupBy', GROUP_OPTIONS),
    sortBy: read('sortBy', SORT_OPTIONS),
    membership: read('membership', MEMBERSHIP_OPTIONS),
  }
}

/** The `setParams` patch for one organize change. A default is written as
 *  null, which the router drops, so the hash only carries a real choice. */
export function organizeUrlPatch(field, value) {
  return { [ORGANIZE_URL_KEYS[field]]: value === ORGANIZE_DEFAULTS[field] ? null : value }
}

/** Ids carry numbers that must order numerically: TC-ITDR-2 before TC-ITDR-10. */
const natural = (a, b) =>
  String(a).localeCompare(String(b), 'en', { numeric: true, sensitivity: 'base' })

const DETECTION_KINDS = ['iocs', 'biocs', 'xql_queries', 'correlation_rules', 'analytics_modules']

/** The one definition of a card's "N dets" — the card face and the sort agree. */
export function detectionTotal(card) {
  const counts = card?.detection_counts || {}
  return DETECTION_KINDS.reduce((n, k) => n + (counts[k] || 0), 0)
}

function requireList(value, name) {
  if (!Array.isArray(value)) {
    // Tolerance hides bugs: `{scenarios: [...]}` handed in whole would read as
    // an empty list and every card would render as uncited.
    throw new TypeError(`catalogBindings: ${name} must be an array, got ${value === null ? 'null' : typeof value}`)
  }
  return value
}

/**
 * Join cards to the index.
 *
 * @param {Object}  input
 * @param {Array}   input.scenarios   GET /api/scenarios rows (summary projection)
 * @param {Array}   input.testCases   GET /api/uctc/test-cases rows
 * @param {Array}   input.useCases    GET /api/uctc/use-cases rows
 * @param {boolean} input.indexLoaded the `index_loaded` flag every /api/uctc response carries
 * @returns {{status: string, reason: string,
 *            byCard: Map<string, Array<Binding>>,
 *            scenariosByCard: Map<string, string[]>}}
 *
 * A `Binding` is one (card, test case) pair:
 *   { tcId, tcTitle, ucsId, ucsName, ucId, useCase, subdomain,
 *     primary, resolved, scenarioIds }
 * `primary` — some citing scenario holds this test case as its `tc_ref`.
 * `resolved` — the loaded index lists the test case.
 */
export function buildCardBindings({ scenarios, testCases, useCases, indexLoaded }) {
  requireList(scenarios, 'scenarios')
  requireList(testCases, 'testCases')
  requireList(useCases, 'useCases')

  const empty = (status, reason) => ({ status, reason, byCard: new Map(), scenariosByCard: new Map() })

  // A zero is degraded, not ok: an index that loaded no rows cannot classify
  // anything, and reporting that as "0 use cases" would read as a finding.
  if (!indexLoaded || testCases.length === 0 || useCases.length === 0) {
    return empty(
      BINDING_STATUS.INDEX_MISSING,
      'The UC / TC index is not loaded on this SimCore, so cards cannot be placed under a use case, test case or subdomain.',
    )
  }

  // card id -> scenario id -> scenario
  const citing = new Map()
  let sawCardRef = false
  for (const s of scenarios) {
    for (const step of s?.steps || []) {
      for (const d of step?.expected_detections || []) {
        if (!d || typeof d.ttp_ref !== 'string' || !d.ttp_ref) continue
        sawCardRef = true
        if (!citing.has(d.ttp_ref)) citing.set(d.ttp_ref, new Map())
        citing.get(d.ttp_ref).set(s.scenario_id, s)
      }
    }
  }
  if (!sawCardRef) {
    return empty(
      BINDING_STATUS.LINKS_MISSING,
      scenarios.length === 0
        ? 'No scenarios are loaded, so no card can be traced to a test case.'
        : 'The scenario list from this SimCore carries no card references (expected_detections[].ttp_ref), so no card can be traced to a test case. This build predates the list field the Catalog groups by.',
    )
  }

  const tcById = new Map(testCases.map((t) => [t.tc_id, t]))
  const ucById = new Map(useCases.map((u) => [u.uc_id, u]))

  const byCard = new Map()
  const scenariosByCard = new Map()
  for (const [cardId, byScenario] of citing) {
    const scenarioIds = [...byScenario.keys()].sort(natural)
    scenariosByCard.set(cardId, scenarioIds)

    const byTc = new Map()
    for (const sid of scenarioIds) {
      const s = byScenario.get(sid)
      const refs = [s.tc_ref, ...(Array.isArray(s.tc_refs) ? s.tc_refs : [])]
      for (const tcId of refs) {
        if (typeof tcId !== 'string' || !tcId) continue
        if (!byTc.has(tcId)) {
          const tc = tcById.get(tcId) || null
          const uc = tc ? ucById.get(tc.uc_id) || null : null
          byTc.set(tcId, {
            tcId,
            tcTitle: tc?.title || '',
            ucsId: tc?.ucs_id || null,
            ucsName: tc?.ucs_name || '',
            ucId: tc?.uc_id || null,
            useCase: tc?.use_case || uc?.use_case || '',
            // A resolved test case whose use case has a blank subdomain is
            // "Unclassified" — the label the UC / TC index rail already uses.
            subdomain: tc ? (uc?.fy27_subdomain || 'Unclassified') : null,
            primary: false,
            resolved: !!tc,
            scenarioIds: [],
          })
        }
        const b = byTc.get(tcId)
        if (tcId === s.tc_ref) b.primary = true
        if (!b.scenarioIds.includes(sid)) b.scenarioIds.push(sid)
      }
    }
    // Primary first, then natural id — the order a card face reads them in.
    const list = [...byTc.values()].sort(
      (a, b) => Number(b.primary) - Number(a.primary) || natural(a.tcId, b.tcId),
    )
    if (list.length) byCard.set(cardId, list)
  }

  return { status: BINDING_STATUS.OK, reason: '', byCard, scenariosByCard }
}

/* ─────────────────────────── organize ─────────────────────────── */

const DIMENSION = {
  subdomain: (b) => b.subdomain,
  uc: (b) => b.ucId,
  tc: (b) => b.tcId,
}

/**
 * WHICH bindings place a card under a group.
 *
 * This is the one policy decision in the file, and it is a real trade:
 *
 *   'all'      every test case the citing scenario evidences (`tc_refs[]`).
 *              Complete — "what do I have for TC-X?" misses nothing — but a
 *              card repeats: on the 2026-11 corpus 163 of 191 cards sit under
 *              more than one test case and 93 under more than one subdomain.
 *   'primary'  only a scenario's `tc_ref`. A near-partition (190 of 191 cards
 *              land once) and easier to scan, but a test case that is only
 *              ever SECONDARY evidence shows an empty-looking group — the
 *              "nothing proves this" reading the corpus does not support.
 *
 * Default is 'all' because a sparse group is the costlier mistake; the view
 * offers the toggle and prints how many cards repeat.
 */
function placingBindings(bindings, membership) {
  const resolved = bindings.filter((b) => b.resolved)
  if (membership !== 'primary') return resolved
  const primary = resolved.filter((b) => b.primary)
  // A card whose only resolved bindings are secondary still belongs somewhere.
  return primary.length ? primary : resolved
}

function sortKey(card, bindings, sortBy) {
  const list = placingBindings(bindings || [], 'primary')
  if (!list.length) return null
  const pick = DIMENSION[sortBy]
  return list.map(pick).filter(Boolean).sort(natural)[0] ?? null
}

function comparator(sortBy, byCard) {
  const byId = (a, b) => natural(a.id, b.id)
  if (sortBy === 'name') return (a, b) => natural(a.name || a.id, b.name || b.id) || byId(a, b)
  if (sortBy === 'detections') return (a, b) => detectionTotal(b) - detectionTotal(a) || byId(a, b)
  if (INDEX_SORTS.has(sortBy)) {
    const keys = new Map()
    const keyOf = (c) => {
      if (!keys.has(c.id)) keys.set(c.id, sortKey(c, byCard.get(c.id), sortBy))
      return keys.get(c.id)
    }
    return (a, b) => {
      const ka = keyOf(a)
      const kb = keyOf(b)
      if (ka === null || kb === null) return (ka === null) - (kb === null) || byId(a, b) // unbound last
      return natural(ka, kb) || byId(a, b)
    }
  }
  return byId
}

function describeGroup(groupBy, key, bindings) {
  const b = bindings[0]
  if (groupBy === 'uc') {
    return { label: key, title: b.useCase, sublabel: b.subdomain || '', link: { uc: key } }
  }
  if (groupBy === 'tc') {
    return {
      label: key,
      title: b.tcTitle,
      sublabel: [b.ucId, b.subdomain].filter(Boolean).join(' · '),
      link: { tc: key },
    }
  }
  const ucs = new Set(bindings.map((x) => x.ucId).filter(Boolean))
  return {
    label: key,
    title: '',
    sublabel: `${ucs.size} use case${ucs.size === 1 ? '' : 's'}`,
    link: null,
  }
}

const UNBOUND_GROUPS = {
  [UNBOUND_KEYS.UNRESOLVED]: {
    label: 'Bound to a test case the loaded index does not list',
    title: '',
    sublabel: 'A scenario cites the card, but its tc_ref is not in this index version.',
    link: null,
  },
  [UNBOUND_KEYS.UNCITED]: {
    label: 'No scenario cites this card',
    title: '',
    sublabel: 'Nothing in the loaded scenario corpus references it, so it has no use case or test case.',
    link: null,
  },
}

/**
 * Sort, and optionally group, the (already filtered) cards.
 *
 * @param {Array}  cards     card summaries (GET /api/ttps rows)
 * @param {Object} bindings  the object `buildCardBindings` returned
 * @param {Object} opts
 * @param {'none'|'subdomain'|'uc'|'tc'} opts.groupBy
 * @param {'id'|'name'|'subdomain'|'uc'|'tc'|'detections'} opts.sortBy
 * @param {'all'|'primary'} opts.membership
 * @returns {{flat: Array, groups: ?Array, uniqueCards: number, placements: number,
 *            multiPlaced: number, groupingRefused: boolean, sortRefused: boolean}}
 *
 * A group is `{key, kind: 'bound'|'unbound', label, title, sublabel, link,
 * cards, via}` — `link` is the `#/uctc` param that opens it in the index,
 * `via[cardId]` the bindings that put that card in this group.
 *
 * `placements` counts card-in-group pairs and is what the group counts add up
 * to; `uniqueCards` is the number of cards. They differ whenever a card sits in
 * more than one group, and the view must never print one as the other.
 */
export function organizeCards(cards, bindings, { groupBy = 'none', sortBy = 'id', membership = 'all' } = {}) {
  const ready = bindings?.status === BINDING_STATUS.OK
  const byCard = ready ? bindings.byCard : new Map()

  const sortRefused = !ready && INDEX_SORTS.has(sortBy)
  const compare = comparator(sortRefused ? 'id' : sortBy, byCard)
  const flat = [...cards].sort(compare)

  const base = {
    flat,
    groups: null,
    uniqueCards: flat.length,
    placements: flat.length,
    multiPlaced: 0,
    groupingRefused: false,
    sortRefused,
  }
  if (groupBy === 'none' || !DIMENSION[groupBy]) return base
  if (!ready) return { ...base, groupingRefused: true }

  const pick = DIMENSION[groupBy]
  const buckets = new Map() // key -> { cards: [], bindings: [], via: {cardId: Binding[]} }
  const put = (key, card, binding) => {
    if (!buckets.has(key)) buckets.set(key, { cards: [], bindings: [], via: {} })
    const bucket = buckets.get(key)
    if (!bucket.cards.includes(card)) bucket.cards.push(card)
    if (binding) {
      bucket.bindings.push(binding)
      ;(bucket.via[card.id] ||= []).push(binding)
    }
  }

  let placements = 0
  let multiPlaced = 0
  for (const card of flat) { // `flat` is already sorted, so each bucket is too
    const all = byCard.get(card.id) || []
    const placing = placingBindings(all, membership)
    const keys = new Set()
    for (const b of placing) {
      const key = pick(b)
      if (!key) continue
      keys.add(key)
      put(key, card, b)
    }
    if (keys.size === 0) {
      // Cited-but-unresolved and never-cited are different facts; one bucket
      // for both would hide an index drift behind an authoring gap.
      keys.add(all.length ? UNBOUND_KEYS.UNRESOLVED : UNBOUND_KEYS.UNCITED)
      put([...keys][0], card, null)
    }
    placements += keys.size
    if (keys.size > 1) multiPlaced += 1
  }

  const unboundOrder = [UNBOUND_KEYS.UNRESOLVED, UNBOUND_KEYS.UNCITED]
  const boundKeys = [...buckets.keys()].filter((k) => !unboundOrder.includes(k)).sort(natural)
  const groups = [...boundKeys, ...unboundOrder.filter((k) => buckets.has(k))].map((key) => {
    const bucket = buckets.get(key)
    const meta = UNBOUND_GROUPS[key] || describeGroup(groupBy, key, bucket.bindings)
    return {
      key,
      kind: UNBOUND_GROUPS[key] ? 'unbound' : 'bound',
      ...meta,
      cards: bucket.cards,
      // WHY each card is here: the binding(s) that placed it in this group,
      // primary first. Under "every test case evidenced" a card can sit in a
      // group that is not its primary use case; the face shows this binding,
      // not the card's lead one, so the placement explains itself.
      via: bucket.via,
    }
  })

  return { ...base, groups, placements, multiPlaced }
}

/** Extra free-text haystack for a card: lets a DC search the Catalog by
 *  `TC-EDR-03`, a use-case name, a subdomain or a scenario id. */
export function bindingSearchText(bindings) {
  if (!bindings || !bindings.length) return ''
  const parts = new Set()
  for (const b of bindings) {
    for (const v of [b.tcId, b.tcTitle, b.ucsId, b.ucsName, b.ucId, b.useCase, b.subdomain, ...b.scenarioIds]) {
      if (v) parts.add(v)
    }
  }
  return [...parts].join(' ')
}
