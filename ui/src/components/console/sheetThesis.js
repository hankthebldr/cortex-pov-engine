/**
 * sheetThesis — the "Why this matters" block a deep-dive sheet opens with
 * (design v33, change C10).
 *
 * A scenario, a TTP card and a single detection each open with four parts,
 * before any mechanics:
 *
 *   Thesis              what the attacker does, and why it is a risk
 *   Why we simulate it  what the POV gets out of running it
 *   What proves it      the evidence that would settle it
 *   What it needs       what has to be in place for it to run and be seen
 *
 * Every line is read off a field the document already carries: the scenario
 * from GET /api/scenarios/{id}, the card from GET /api/ttps/{id} (the raw
 * `detection_scanner/ttps/*.json` document), the detection from that card's
 * `detections` block. Nothing is written here. When none of a part's fields
 * is filled, the part says so plainly ("Not written for this card yet.")
 * instead of borrowing a sentence that would read as the document's own.
 *
 * Each part: { key, label, icon, written, lines, sources }. `sources` names
 * the fields the lines came from, so a reader of the code (and the tests) can
 * trace every sentence back to its document.
 */
import { OWNERSHIP_MEANING, ownershipOf } from './composerContent.js'

/** The four parts, in reading order. `icon` is a kit Icon name. */
export const THESIS_PARTS = Object.freeze([
  Object.freeze({ key: 'thesis', label: 'Thesis', icon: 'target' }),
  Object.freeze({ key: 'why', label: 'Why we simulate it', icon: 'play' }),
  Object.freeze({ key: 'proves', label: 'What proves it', icon: 'check' }),
  Object.freeze({ key: 'needs', label: 'What it needs', icon: 'gear' }),
])

/** The line a part shows when its document carries none of its fields. */
export function notWritten(noun) {
  return `Not written for this ${noun} yet.`
}

/** A card's detection-family key → its detection type (the closed vocabulary). */
export const KIND_TYPE = Object.freeze({
  biocs: 'BIOC',
  abiocs: 'ABIOC',
  xql_queries: 'XQL',
  correlation_rules: 'Correlation',
  iocs: 'IOC',
  analytics_modules: 'Analytics',
  prevention_modules: 'Prevention',
})

// ─── small text helpers ──────────────────────────────────────────────────────

/** A field as one line of text: trimmed, inner whitespace collapsed; '' when absent. */
function text(v) {
  if (typeof v === 'number' && Number.isFinite(v)) return String(v)
  return typeof v === 'string' ? v.replace(/\s+/g, ' ').trim() : ''
}

function list(v) {
  return Array.isArray(v) ? v.map(text).filter(Boolean) : []
}

/** End a line with a full stop unless it already ends a sentence. */
function sentence(s) {
  return /[.!?]$/.test(s) ? s : `${s}.`
}

/** The first sentence of a paragraph, or the whole of it when it has one. */
export function firstSentence(v) {
  const s = text(v)
  if (!s) return ''
  const m = s.match(/^[\s\S]*?[.!?](?=\s+[A-Z0-9("'“]|$)/)
  return m ? m[0] : s
}

function plural(n, one, many = `${one}s`) {
  return n === 1 ? one : many
}

function unique(xs) {
  return [...new Set(xs)]
}

function idName(id, name) {
  return [text(id), text(name)].filter(Boolean).join(' ')
}

function part(key, lines, sources, noun) {
  const def = THESIS_PARTS.find((p) => p.key === key)
  const written = lines.length > 0
  return {
    key,
    label: def.label,
    icon: def.icon,
    written,
    lines: written ? lines : [notWritten(noun)],
    sources: written ? unique(sources) : [],
  }
}

// ─── scenario ────────────────────────────────────────────────────────────────

function fmtThreshold(th) {
  if (!th || typeof th !== 'object') return ''
  const kpi = text(th.kpi)
  const op = text(th.op)
  const value = text(th.value)
  if (!kpi || !op || !value) return ''
  const unit = text(th.unit)
  return `${kpi} ${op} ${value}${unit === '%' ? '%' : unit ? ` ${unit}` : ''}`
}

/**
 * "Why this matters" for a scenario document (GET /api/scenarios/{id}).
 * A scenario carries no prose description, so its thesis is the technique it
 * exercises and the report it is modeled on.
 */
export function scenarioThesis(scenario) {
  const s = scenario && typeof scenario === 'object' ? scenario : {}
  const noun = 'scenario'

  const thesis = []
  const thesisSrc = []
  const tech = idName(s.mitre_technique, s.mitre_technique_name)
  if (tech) {
    const tactic = text(s.mitre_tactic_name)
    thesis.push(sentence(tactic ? `${tech} (${tactic})` : tech))
    thesisSrc.push('mitre_technique', 'mitre_technique_name')
    if (tactic) thesisSrc.push('mitre_tactic_name')
  }
  if (text(s.threat_report)) {
    thesis.push(sentence(`Modeled on ${text(s.threat_report)}`))
    thesisSrc.push('threat_report')
  }

  const why = []
  const whySrc = []
  const tc = text(s.tc_ref)
  const uc = text(s.uc_ref)
  if (tc) {
    const under = uc ? `, under use case ${idName(uc, s.uc_name)}` : ''
    why.push(sentence(`Evidences test case ${idName(tc, s.tc_name)}${under}`))
    whySrc.push('tc_ref', 'tc_name')
    if (uc) whySrc.push('uc_ref', 'uc_name')
  } else if (uc) {
    why.push(sentence(`Evidences use case ${idName(uc, s.uc_name)}`))
    whySrc.push('uc_ref', 'uc_name')
  }
  const also = list(s.tc_refs).filter((t) => t !== tc)
  if (also.length) {
    why.push(`Also evidences ${unique(also).join(', ')}.`)
    whySrc.push('tc_refs')
  }
  const method = text(s.validation_methodology)
  if (method) {
    const family = text(s.methodology_family)
    why.push(sentence(`Method: ${method}${family ? ` (${family})` : ''}`))
    whySrc.push('validation_methodology')
    if (family) whySrc.push('methodology_family')
  }

  const proves = []
  const provesSrc = []
  if (text(s.success_criteria)) {
    proves.push(sentence(text(s.success_criteria)))
    provesSrc.push('success_criteria')
  }
  const threshold = fmtThreshold(s.threshold)
  if (threshold) {
    proves.push(`Passes when ${threshold}.`)
    provesSrc.push('threshold')
  }
  const steps = Array.isArray(s.steps) ? s.steps : []
  const expected = steps.reduce(
    (n, st) => n + (Array.isArray(st?.expected_detections) ? st.expected_detections.length : 0), 0,
  )
  if (expected > 0) {
    proves.push(`${expected} expected ${plural(expected, 'detection')} across ${steps.length} ${plural(steps.length, 'step')}.`)
    provesSrc.push('steps[].expected_detections')
  }

  const needs = []
  const needsSrc = []
  const tools = unique(
    (Array.isArray(s.external_tools) ? s.external_tools : [])
      .map((t) => text(t?.adapter_ref) || text(t?.name))
      .filter(Boolean),
  )
  if (tools.length) {
    needs.push(`Tools: ${tools.join(', ')}.`)
    needsSrc.push('external_tools')
  }
  const identity = text(s.execution_identity?.default)
  if (identity) {
    needs.push(`Runs as ${identity}.`)
    needsSrc.push('execution_identity.default')
  }
  const base = list(s.required_base_platform)
  if (base.length) {
    needs.push(`Cortex platform: ${base.join(', ')}.`)
    needsSrc.push('required_base_platform')
  }
  const addons = list(s.required_addons)
  if (addons.length) {
    needs.push(`Add-ons: ${addons.join(', ')}.`)
    needsSrc.push('required_addons')
  }
  const planes = list(s.required_planes_in_incident)
  if (planes.length) {
    needs.push(`One incident must span ${planes.join(', ')}.`)
    needsSrc.push('required_planes_in_incident')
  }

  return [
    part('thesis', thesis, thesisSrc, noun),
    part('why', why, whySrc, noun),
    part('proves', proves, provesSrc, noun),
    part('needs', needs, needsSrc, noun),
  ]
}

// ─── TTP card ────────────────────────────────────────────────────────────────

function threatScenarios(card) {
  const ts = card?.panw_mapping?.threat_scenarios
  return Array.isArray(ts) ? ts.filter((t) => t && typeof t === 'object') : []
}

function threatSteps(card) {
  return threatScenarios(card).flatMap((t) => (
    Array.isArray(t.threat_steps) ? t.threat_steps.filter((st) => st && typeof st === 'object') : []
  ))
}

/**
 * "Why this matters" for a TTP card document (GET /api/ttps/{id}).
 * The thesis is the first sentence of `identity.description`; the card's
 * `identity.summary` keeps its own section on the sheet, so it is not repeated.
 */
export function ttpThesis(card) {
  const c = card && typeof card === 'object' ? card : {}
  const noun = 'card'

  const thesis = []
  const lead = firstSentence(c.identity?.description)
  if (lead) thesis.push(sentence(lead))

  const why = threatScenarios(c).map((t) => text(t.description)).filter(Boolean).map(sentence)

  const proves = threatSteps(c).map((st) => text(st.objective)).filter(Boolean).map(sentence)

  const needs = []
  const needsSrc = []
  const target = text(c.execution?.target_platform)
  const privilege = text(c.execution?.privilege_required)
  if (target || privilege) {
    needs.push([target && `Target: ${target}`, privilege && `privilege: ${privilege}`].filter(Boolean).join('; ') + '.')
    if (target) needsSrc.push('execution.target_platform')
    if (privilege) needsSrc.push('execution.privilege_required')
  }
  const prereqs = (Array.isArray(c.execution?.prerequisites) ? c.execution.prerequisites : [])
    .map((p) => text(p?.description)).filter(Boolean).map(sentence)
  if (prereqs.length) {
    needs.push(...prereqs)
    needsSrc.push('execution.prerequisites[].description')
  }

  return [
    part('thesis', thesis, ['identity.description'], noun),
    part('why', why, ['panw_mapping.threat_scenarios[].description'], noun),
    part('proves', proves, ['panw_mapping.threat_scenarios[].threat_steps[].objective'], noun),
    part('needs', needs, needsSrc, noun),
  ]
}

// ─── detection ───────────────────────────────────────────────────────────────

function escapeRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/**
 * Does a card success criterion name this detection? A name or IOC value must
 * appear in backticks (the corpus convention: BIOC `name` fires); a rule or
 * detection id may appear bare (Correlation CR-NDR-0005 raises …), as a whole
 * token.
 */
function namedIn(criterion, obj) {
  for (const key of [text(obj.name), text(obj.value)]) {
    if (key && criterion.includes(`\`${key}\``)) return true
  }
  for (const id of [text(obj.rule_id), text(obj.detection_id)]) {
    if (id && new RegExp(`(^|[^A-Za-z0-9-])${escapeRe(id)}($|[^A-Za-z0-9-])`).test(criterion)) return true
  }
  return false
}

/** The `dataset =` / `preset =` anchor a query or rule body reads, if it has one. */
export function datasetAnchor(body) {
  if (typeof body !== 'string') return null
  const m = body.match(/\b(dataset|preset)\s*=\s*([A-Za-z0-9_.:-]+)/)
  return m ? { kind: m[1], name: m[2] } : null
}

function techniqueNames(card) {
  const out = new Map()
  for (const t of (Array.isArray(card?.mitre_attack?.techniques) ? card.mitre_attack.techniques : [])) {
    const name = text(t?.name)
    for (const id of [text(t?.subtechnique_id), text(t?.technique_id)]) {
      if (id && name && !out.has(id)) out.set(id, name)
    }
  }
  return out
}

/**
 * "Why this matters" for one detection object on a card.
 *
 * @param {string} kind  the card's detection-family key (`biocs`, `xql_queries`, …)
 * @param {object|string} item  the detection object (an Analytics entry may be a bare string)
 * @param {object} card  the card the detection ships on, for its threat steps and techniques
 */
export function detectionThesis(kind, item, card) {
  const obj = item && typeof item === 'object' ? item : {}
  const noun = 'detection'
  const type = KIND_TYPE[kind] || null

  const thesis = []
  const thesisSrc = []
  if (text(obj.description)) {
    thesis.push(sentence(text(obj.description)))
    thesisSrc.push('description')
  } else if (text(obj.context)) {
    thesis.push(sentence(text(obj.context)))
    thesisSrc.push('context')
  }

  // The card's threat steps whose success criteria name this detection: the
  // step's objective is why it is simulated, the criterion is what proves it.
  const steps = threatSteps(card).map((st) => ({
    objective: text(st.objective),
    criteria: (Array.isArray(st.success_criteria) ? st.success_criteria : []).map(text).filter((c) => c && namedIn(c, obj)),
  })).filter((st) => st.criteria.length > 0)

  const why = []
  const whySrc = []
  const objectives = unique(steps.map((st) => st.objective).filter(Boolean))
  if (objectives.length) {
    why.push(...objectives.map(sentence))
    whySrc.push('panw_mapping.threat_scenarios[].threat_steps[].objective')
  }
  const names = techniqueNames(card)
  const techIds = unique(list(obj.mitre_technique_ids).concat(list(obj.mitre_techniques)))
  if (techIds.length) {
    why.push(`Covers ${techIds.map((id) => idName(id, names.get(id))).join(', ')}.`)
    whySrc.push(obj.mitre_technique_ids ? 'mitre_technique_ids' : 'mitre_techniques')
  }
  if (text(obj.purpose)) {
    why.push(`Purpose on this card: ${text(obj.purpose)}.`)
    whySrc.push('purpose')
  }

  const proves = []
  const provesSrc = []
  const criteria = unique(steps.flatMap((st) => st.criteria))
  if (criteria.length) {
    proves.push(...criteria.map(sentence))
    provesSrc.push('panw_mapping.threat_scenarios[].threat_steps[].success_criteria')
  }
  const rows = typeof obj.expected_rows_min === 'number' ? obj.expected_rows_min : null
  const anchor = text(obj.dataset)
    ? { kind: 'dataset', name: text(obj.dataset) }
    : datasetAnchor(obj.query) || datasetAnchor(obj.logic)
  if (rows != null && rows > 0) {
    proves.push(`Returns at least ${rows} ${plural(rows, 'row')}${anchor ? ` from ${anchor.name}` : ''}.`)
    provesSrc.push('expected_rows_min')
  }

  const needs = []
  const needsSrc = []
  if (type) {
    needs.push(OWNERSHIP_MEANING[ownershipOf(type)])
    needsSrc.push(`detections.${kind}`)
  }
  if (anchor) {
    needs.push(`Reads ${anchor.kind} ${anchor.name}.`)
    needsSrc.push(text(obj.dataset) ? 'dataset' : obj.query ? 'query' : 'logic')
  }

  return [
    part('thesis', thesis, thesisSrc, noun),
    part('why', why, whySrc, noun),
    part('proves', proves, provesSrc, noun),
    part('needs', needs, needsSrc, noun),
  ]
}
