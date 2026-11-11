/**
 * composerContent.js — what a chain needs to exist in the TENANT, derived from
 * the TTP cards its steps cite.
 *
 * No React, no fetch, no randomness: same convention as `composerDraft.js` and
 * `composerGraph.js`. The canvas, the Inspector and the chrome control all read
 * one result, so a rule cannot be drawn with one status and described with
 * another.
 *
 * TWO IMPLEMENTATIONS, ONE FIXTURE. The server derives the same facts for the
 * read-only export (`core/engine/tenant_content.py`). Both assert
 * `spec/tenant_content_cases.json`; change the logic here and that fixture, and
 * the Python side, change with it.
 *
 * TWO AXES, NEVER ONE COLOUR.
 *   content  — what the CARD holds: `intent-only` (name / rule_id / logic and
 *              nothing a tenant can import), `deployable` (a complete `deploy`
 *              block), or `unreadable` (a shape this module does not know).
 *   binding  — what the SCENARIO asks for: `declared` (a step expects the rule,
 *              so the run scores it) or `card-only` (on a cited card, expected
 *              by no step, not scored).
 *
 * HONESTY.
 *   - An unrecognised rule shape RAISES (`classifyRule`). The derivation turns
 *     that into an `unreadable` rule with the reason; it is never read as
 *     intent-only and never dropped.
 *   - `deployable` means structurally complete. It never means a tenant
 *     accepted the rule. The `deploy` field names are CANDIDATES until phase 3
 *     pins them to a rule exported from a real tenant; no card carries one today
 *     (the card schema forbids the key), and a test pins that count at zero.
 *   - A card that has not loaded is `cardsMissing`, never "no rules".
 *   - Nothing here is ever persisted: no step, no draft field, no layout key.
 */

export class TenantContentError extends Error {
  constructor(message) {
    super(message)
    this.name = 'TenantContentError'
  }
}

// ─── Detection-type vocabulary (closed) ──────────────────────────────────────

/** UPPERCASE type → [canonical spelling, ownership]. The seven `detection_type`
 *  values and nothing else; an unknown type raises rather than taking a default. */
const TYPES = {
  XQL: ['XQL', 'run'],
  BIOC: ['BIOC', 'deploy'],
  IOC: ['IOC', 'deploy'],
  CORRELATION: ['Correlation', 'deploy'],
  ABIOC: ['ABIOC', 'built-in'],
  ANALYTICS: ['Analytics', 'built-in'],
  PREVENTION: ['Prevention', 'built-in'],
}

/** Chip order: who owns the work, then a fixed type order inside each class. */
const CHIP_ORDER = ['XQL', 'BIOC', 'IOC', 'Correlation', 'ABIOC', 'Analytics', 'Prevention']

/** [singular, plural] noun each type reads as on a chip. */
const CHIP_NOUN = {
  XQL: ['query', 'queries'],
  BIOC: ['BIOC', 'BIOCs'],
  IOC: ['IOC', 'IOCs'],
  Correlation: ['rule', 'rules'],
  ABIOC: ['ABIOC', 'ABIOCs'],
  Analytics: ['Analytics', 'Analytics'],
  Prevention: ['Prevention', 'Prevention'],
}

/** What each ownership class asks of a DC. Shown as the chip's title text. */
export const OWNERSHIP_MEANING = {
  run: 'A query you run after the step to confirm the telemetry landed.',
  deploy: 'Must exist in the tenant before the run. This is what raises the alert.',
  'built-in': 'Ships with Cortex. Needs the module licensed and enabled; nothing to import.',
}

function typeEntry(type) {
  const entry = typeof type === 'string' ? TYPES[type.trim().toUpperCase()] : undefined
  if (!entry) throw new TenantContentError(`unknown detection type ${JSON.stringify(type ?? null)}`)
  return entry
}

/** The canonical spelling of a detection type (`xql` → `XQL`). Raises on unknown. */
export function canonicalType(type) {
  return typeEntry(type)[0]
}

/** Who owns the work for a detection type: `run` | `deploy` | `built-in`. Raises on unknown. */
export function ownershipOf(type) {
  return typeEntry(type)[1]
}

// ─── Detection ids ───────────────────────────────────────────────────────────

/**
 * The id the catalog synthesises for a card object that carries none
 * (`core/engine/ttp_catalog._slug`): lowercased, every non-alphanumeric run
 * collapsed to one hyphen, prefix-tagged, cut at 120 characters. Per code
 * point, like Python, so a name outside ASCII slugs the same on both sides.
 */
export function slugDetectionId(name, prefix) {
  let clean = ''
  for (const ch of String(name).toLowerCase()) clean += /[\p{L}\p{N}]/u.test(ch) ? ch : '-'
  clean = clean.replace(/^-+|-+$/g, '').replace(/-{2,}/g, '-')
  return Array.from(`${prefix}-${clean}`).slice(0, 120).join('')
}

// ─── Content status ──────────────────────────────────────────────────────────

const RULE_KEYS = new Set(['name', 'rule_id', 'logic', 'deploy'])
const DEPLOY_KEYS = new Set([
  'xql', 'execution', 'schedule', 'search_window', 'severity', 'alert',
  'suppression', 'mitre', 'provenance',
])
const isObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v)
const isText = (v) => typeof v === 'string' && v.trim() !== ''

/**
 * A rule's content status: `intent-only` or `deployable`.
 *
 * RAISES `TenantContentError` on any shape it does not recognise: an unknown
 * key, a non-string `logic`, or a `deploy` block that is not complete. Half a
 * block never downgrades to intent-only.
 */
export function classifyRule(rule) {
  if (!isObject(rule)) throw new TenantContentError('rule is not an object')
  for (const key of Object.keys(rule)) {
    if (!RULE_KEYS.has(key)) throw new TenantContentError(`unknown key '${key}' on the rule`)
  }
  if (!isText(rule.name)) throw new TenantContentError('rule has no name')
  if (rule.rule_id != null && typeof rule.rule_id !== 'string') {
    throw new TenantContentError('rule_id is not a string')
  }
  if (rule.logic != null && typeof rule.logic !== 'string') {
    throw new TenantContentError('logic is not a string')
  }
  if (!('deploy' in rule)) return 'intent-only'

  const d = rule.deploy
  if (!isObject(d)) throw new TenantContentError('deploy is not an object')
  for (const key of Object.keys(d)) {
    if (!DEPLOY_KEYS.has(key)) throw new TenantContentError(`unknown key '${key}' in deploy`)
  }
  if (!isText(d.xql)) throw new TenantContentError('deploy has no xql')
  if (!/(^|[^A-Za-z0-9_])(dataset|preset)[ \t]*=/.test(d.xql)) {
    throw new TenantContentError('deploy xql has no dataset or preset anchor')
  }
  if (d.execution !== 'scheduled' && d.execution !== 'realtime') {
    throw new TenantContentError('deploy execution is not scheduled or realtime')
  }
  if (d.execution === 'scheduled' && !(isText(d.schedule) && isText(d.search_window))) {
    throw new TenantContentError('a scheduled rule needs a schedule and a search_window')
  }
  if (!isText(d.severity)) throw new TenantContentError('deploy has no severity')
  if (!isObject(d.alert) || !isText(d.alert.name)) {
    throw new TenantContentError('deploy has no alert name')
  }
  return 'deployable'
}

// ─── The logic line ──────────────────────────────────────────────────────────

const TERM = /(^|[^A-Za-z0-9_])(XQL|BIOC|ABIOC|IOC|ANALYTICS|CORRELATION|PREVENTION)[ \t]*\(/gi

/**
 * Read a rule's `logic` line as a composition of named detections.
 *
 * `XQL(name) OR BIOC(name)` yields its `KIND(name)` terms; a name may carry its
 * own balanced parentheses. A line with no term is `prose`; a missing or blank
 * line is `none`. RAISES on a term that never closes: half a term is not a
 * shorter term.
 *
 * @returns {{kind:'composition'|'prose'|'none', terms:Array<{kind:string,name:string}>}}
 */
export function parseLogic(logic) {
  if (logic == null) return { kind: 'none', terms: [] }
  if (typeof logic !== 'string') throw new TenantContentError('logic is not a string')
  if (logic.trim() === '') return { kind: 'none', terms: [] }

  const terms = []
  const re = new RegExp(TERM.source, 'gi')
  let m = re.exec(logic)
  while (m) {
    let depth = 1
    let i = re.lastIndex
    const start = i
    while (i < logic.length && depth > 0) {
      const ch = logic[i]
      if (ch === '(') depth += 1
      else if (ch === ')') depth -= 1
      i += 1
    }
    if (depth !== 0) {
      throw new TenantContentError(`${m[2].toUpperCase()}( has no closing parenthesis`)
    }
    let name = logic.slice(start, i - 1).trim()
    const q = name[0]
    if (name.length >= 2 && (q === '"' || q === "'") && name[name.length - 1] === q) {
      name = name.slice(1, -1).trim()
    }
    terms.push({ kind: m[2].toLowerCase(), name })
    // Resume AFTER the term: a KIND( inside a name is part of that name.
    re.lastIndex = i
    m = re.exec(logic)
  }
  return { kind: terms.length ? 'composition' : 'prose', terms }
}

// ─── Card index ──────────────────────────────────────────────────────────────

/** Card list → kind, in the order a term's name is looked up after its own kind. */
const KIND_LISTS = [
  ['xql', 'xql_queries'],
  ['bioc', 'biocs'],
  ['abioc', 'abiocs'],
  ['ioc', 'iocs'],
  ['analytics', 'analytics_modules'],
  ['correlation', 'correlation_rules'],
  ['prevention', 'prevention_modules'],
  ['modeling', 'modeling_rules'],
]

/** A rule's id and display name, by the catalog's own fallbacks. Never raises. */
function ruleIdentity(rule, index) {
  const obj = isObject(rule) ? rule : {}
  const name = isText(obj.name) ? obj.name : `correlation-${index + 1}`
  const id = (typeof obj.rule_id === 'string' && obj.rule_id)
    || (typeof obj.detection_id === 'string' && obj.detection_id)
    || slugDetectionId(name, 'correlation')
  return { id, name }
}

/**
 * Index one raw card: every detection object by id and by (kind, name), with
 * ids synthesised the way `ttp_catalog` does. Analytics modules are bare
 * strings or `{module}` objects; they index by name so a term can find them.
 */
function indexCard(card) {
  const byId = new Map()
  const byName = new Map(KIND_LISTS.map(([kind]) => [kind, new Map()]))
  const det = isObject(card) && isObject(card.detections) ? card.detections : {}
  const put = (kind, id, name) => {
    if (!byId.has(id)) byId.set(id, { kind, name })
    if (!byName.get(kind).has(name)) byName.get(kind).set(name, id)
  }
  for (const [kind, key] of KIND_LISTS) {
    const items = Array.isArray(det[key]) ? det[key] : []
    items.forEach((o, i) => {
      if (kind === 'analytics') {
        const name = typeof o === 'string' ? o : (isObject(o) ? (o.module || o.name) : null)
        if (isText(name)) put(kind, slugDetectionId(name, 'analytics'), name)
        return
      }
      if (kind === 'correlation') {
        const { id, name } = ruleIdentity(o, i)
        put(kind, id, name)
        return
      }
      if (!isObject(o)) return
      if (kind === 'ioc') {
        const type = o.ioc_type || 'ioc'
        const value = o.value || `ioc-${i + 1}`
        put(kind, o.detection_id || slugDetectionId(`${type}-${value}`, 'ioc'), `${type}: ${value}`)
        return
      }
      const name = o.name || `${kind}-${i + 1}`
      put(kind, o.detection_id || slugDetectionId(name, kind), name)
    })
  }
  return { byId, byName }
}

// ─── Derivation ──────────────────────────────────────────────────────────────

const isCorrelation = (type) => typeof type === 'string' && type.trim().toUpperCase() === 'CORRELATION'

/**
 * Everything a chain needs to exist in the tenant, from the cards its steps cite.
 *
 * @param {Array}  steps      draft steps (`detections[{type, ttpRef, detectionId}]`)
 * @param {Object} cardsById  `{ [ttpId]: rawCard }` — only the cards that have LOADED
 * @returns {{
 *   citedCards:string[], cardsMissing:string[], noCardBinding:boolean,
 *   rules:Array, unbound:Array, summary:Object
 * }}
 */
export function deriveTenantContent(steps, cardsById) {
  const list = Array.isArray(steps) ? steps : []
  const cards = isObject(cardsById) ? cardsById : {}
  const dets = (s) => (Array.isArray(s?.detections) ? s.detections : [])

  const citedCards = []
  for (const s of list) {
    for (const d of dets(s)) {
      if (d?.ttpRef && !citedCards.includes(d.ttpRef)) citedCards.push(d.ttpRef)
    }
  }
  const loaded = citedCards.filter((id) => isObject(cards[id]))
  const cardsMissing = citedCards.filter((id) => !isObject(cards[id]))
  const index = new Map(loaded.map((id) => [id, indexCard(cards[id])]))

  /** Step ids, in chain order, that declare (ttpRef, detectionId). */
  const declaring = (ttpRef, detectionId) => list
    .filter((s) => dets(s).some((d) => d?.ttpRef === ttpRef && d?.detectionId === detectionId))
    .map((s) => s.id)

  const rules = []
  for (const ttpRef of loaded) {
    const idx = index.get(ttpRef)
    const raw = cards[ttpRef].detections?.correlation_rules
    const citing = list.filter((s) => dets(s).some((d) => d?.ttpRef === ttpRef)).map((s) => s.id)
    ;(Array.isArray(raw) ? raw : []).forEach((rule, i) => {
      const { id: ruleId, name } = ruleIdentity(rule, i)

      let contentStatus
      let contentError = null
      try {
        contentStatus = classifyRule(rule)
      } catch (err) {
        if (!(err instanceof TenantContentError)) throw err
        contentStatus = 'unreadable'
        contentError = err.message
      }

      // An unreadable rule's logic is not trusted either: no inputs are claimed.
      let logicKind = null
      let logicError = null
      let inputs = []
      if (contentStatus !== 'unreadable') {
        try {
          const parsed = parseLogic(rule.logic)
          logicKind = parsed.kind
          inputs = parsed.terms.map((term) => {
            let resolvedKind = null
            let detectionId = null
            const order = [term.kind, ...KIND_LISTS.map(([k]) => k).filter((k) => k !== term.kind)]
            for (const kind of order) {
              const hit = idx.byName.get(kind)?.get(term.name)
              if (hit !== undefined) { resolvedKind = kind; detectionId = hit; break }
            }
            const on = detectionId ? declaring(ttpRef, detectionId) : []
            return {
              kind: term.kind,
              name: term.name,
              state: detectionId == null ? 'unknown' : (on.length ? 'step' : 'card-only'),
              resolvedKind,
              detectionId,
              steps: on,
            }
          })
        } catch (err) {
          if (!(err instanceof TenantContentError)) throw err
          logicKind = 'unparsed'
          logicError = err.message
        }
      }

      const declaredBy = declaring(ttpRef, ruleId)
      const feeders = []
      for (const s of list) {
        const n = inputs.filter((inp) => inp.steps.includes(s.id)).length
        const declares = declaredBy.includes(s.id)
        if (n > 0 || declares) feeders.push({ stepId: s.id, inputs: n, declares })
      }
      const anchorStep = feeders.length
        ? feeders[feeders.length - 1].stepId
        : (citing[citing.length - 1] || null)

      rules.push({
        key: `rule:${ttpRef}:${ruleId}`,
        ruleId,
        ttpRef,
        name,
        logic: isObject(rule) && typeof rule.logic === 'string' ? rule.logic : null,
        logicKind,
        logicError,
        contentStatus,
        contentError,
        binding: declaredBy.length ? 'declared' : 'card-only',
        declaredBy,
        inputs,
        tally: {
          total: inputs.length,
          step: inputs.filter((x) => x.state === 'step').length,
          cardOnly: inputs.filter((x) => x.state === 'card-only').length,
          unknown: inputs.filter((x) => x.state === 'unknown').length,
        },
        feeders,
        anchorStep,
        edgeKind: feeders.length ? 'feeds' : 'card',
      })
    })
  }

  // A Correlation expectation with no rule behind it. Judged only against a
  // card that LOADED: a card still in flight says nothing either way.
  const unbound = []
  for (const s of list) {
    dets(s).forEach((d, k) => {
      if (!isCorrelation(d?.type)) return
      const base = { stepId: s.id, index: k, ttpRef: d.ttpRef || null, detectionId: d.detectionId || null }
      if (!d.ttpRef || !d.detectionId) { unbound.push({ ...base, reason: 'no-reference' }); return }
      const idx = index.get(d.ttpRef)
      if (!idx) return
      const hit = idx.byId.get(d.detectionId)
      if (!hit) unbound.push({ ...base, reason: 'no-rule' })
      else if (hit.kind !== 'correlation') unbound.push({ ...base, reason: 'not-a-rule' })
    })
  }

  const count = (fn) => rules.filter(fn).length
  return {
    citedCards,
    cardsMissing,
    noCardBinding: citedCards.length === 0,
    rules,
    unbound,
    summary: {
      total: rules.length,
      deployable: count((r) => r.contentStatus === 'deployable'),
      intentOnly: count((r) => r.contentStatus === 'intent-only'),
      unreadable: count((r) => r.contentStatus === 'unreadable'),
      declared: count((r) => r.binding === 'declared'),
      cardOnly: count((r) => r.binding === 'card-only'),
      unbound: unbound.length,
    },
  }
}

// ─── Words for the canvas ────────────────────────────────────────────────────

/**
 * A step's expected detections as its proof contract: one chip per type, with a
 * count, in ownership order. A type outside the vocabulary gets its own error
 * chip (`? Sigma`); it never falls into a default class.
 *
 * @returns {Array<{ownership:string|null,type:string|null,count:number,label:string,error:boolean}>}
 */
export function detectionChips(detections) {
  const known = new Map()
  const unknown = new Map()
  for (const d of Array.isArray(detections) ? detections : []) {
    let type
    try {
      type = canonicalType(d?.type)
    } catch (err) {
      if (!(err instanceof TenantContentError)) throw err
      const raw = isText(d?.type) ? d.type : null
      unknown.set(raw, (unknown.get(raw) || 0) + 1)
      continue
    }
    known.set(type, (known.get(type) || 0) + 1)
  }
  const chips = CHIP_ORDER.filter((t) => known.has(t)).map((type) => {
    const count = known.get(type)
    const ownership = ownershipOf(type)
    return {
      ownership, type, count,
      label: `${ownership} · ${count} ${CHIP_NOUN[type][count === 1 ? 0 : 1]}`,
      error: false,
    }
  })
  for (const [type, count] of unknown) {
    chips.push({ ownership: null, type, count, label: `? ${type ?? 'untyped'}`, error: true })
  }
  return chips
}

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`

/**
 * One line for the canvas chrome: the rule count and the worst status. Never
 * reads as "nothing needed" while a card is loading, failed to load, or was
 * never cited.
 */
export function contentHeadline(content, { loading = false } = {}) {
  if (loading) return 'loading cards…'
  if (!content || content.noCardBinding) return 'no card cited'
  const missing = content.cardsMissing.length
  const { total, unreadable, intentOnly } = content.summary
  if (!total) {
    return missing ? `${plural(missing, 'card', 'cards')} not loaded` : 'no rule on the cited cards'
  }
  let worst
  if (unreadable) worst = `${unreadable} unreadable`
  else if (intentOnly === total) worst = 'intent only'
  else if (intentOnly) worst = `${intentOnly} intent only`
  else worst = 'authored, not imported'
  const tail = missing ? ` · ${plural(missing, 'card', 'cards')} not loaded` : ''
  return `${plural(total, 'rule', 'rules')} · ${worst}${tail}`
}

/**
 * The three lines a rule node prints. Both statuses are WORDS: colour alone
 * never carries either.
 *
 * The cell that matters most is declared + intent-only: the run seeds a result
 * for an alert no CortexSim content can raise, so it only confirms when the
 * tenant already raises a matching one. That is said on the node.
 *
 * @returns {{content:string, binding:string, inputs:string}}
 */
export function ruleLines(rule) {
  const content = {
    'intent-only': 'INTENT ONLY · nothing to import',
    deployable: 'authored · not imported into any tenant',
    unreadable: 'card shape not recognised',
  }[rule.contentStatus] || `? ${rule.contentStatus}`

  let binding = rule.binding === 'declared'
    ? 'scored by this run'
    : 'not declared by this scenario · not scored'
  if (rule.binding === 'declared' && rule.contentStatus !== 'deployable') {
    binding += ' · nothing here can raise it'
  }

  let inputs
  if (rule.contentStatus === 'unreadable') inputs = rule.contentError || 'reason not given'
  else if (rule.logicKind === 'composition') {
    const { total, step, unknown } = rule.tally
    inputs = `${step} of ${plural(total, 'input', 'inputs')} on steps`
    if (unknown) inputs += ` · ${unknown} not on the card`
  } else if (rule.logicKind === 'none') inputs = 'no logic on the card'
  else inputs = 'inputs not machine-readable'

  return { content, binding, inputs }
}
