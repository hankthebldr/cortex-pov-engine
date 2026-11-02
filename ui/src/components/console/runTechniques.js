/**
 * runTechniques — the pure model behind a run's Techniques tab.
 *
 * Inputs are live records only: the run's `Result` rows, the run itself
 * (`status`, `tc_verdict`) and the TTP cards those rows reference (for
 * `mitre_attack.techniques[].tactic_ids`). Nothing here reads `povdata/`, and
 * nothing reads `/api/mitre/coverage`, which files every step technique under
 * the scenario's single tactic.
 *
 * Honesty rules the model enforces:
 *   - a row reads `miss` ONLY when the run is proven (complete). On a failed,
 *     aborted or still-running run an un-fired detection is `pending`: the
 *     signal may never have been generated, so "Cortex missed it" is not a
 *     claim the row can make (`isRunUnproven`);
 *   - a technique no card places is listed under `unplaced` WITH the reason,
 *     never guessed into a stage from the scenario's tactic;
 *   - a stage with no technique is `empty` — dimmed, not "100% detected".
 */
import { ATTACK_TACTICS, tacticIndex, parentTechnique } from './attackTactics.js'
import { isRunComplete } from './runStatus.js'

export const HIT_OUTCOMES = new Set(['detected', 'prevented'])
export const EXCLUDED_OUTCOMES = new Set(['not_reached', 'not_executed'])

/**
 * Mirrors `engine.outcome.row_status` / `is_hit`:
 *   outcome ∈ {detected, prevented}          → hit
 *   outcome ∈ {not_reached, not_executed}    → excluded (leaves the denominator)
 *   outcome = missed                         → miss, only on a proven run
 *   no outcome yet                           → hit iff observed, else pending
 *   (a `pending` outcome is never a hit, observed or not)
 */
export function resultTone(row, proven) {
  const o = row?.outcome ? String(row.outcome).toLowerCase() : null
  if (o && HIT_OUTCOMES.has(o)) return 'hit'
  if (o && EXCLUDED_OUTCOMES.has(o)) return 'excluded'
  if (o === 'missed') return proven ? 'miss' : 'pending'
  if (o === null && row?.observed === true) return 'hit'
  return 'pending'
}

const TONE_RANK = { miss: 3, pending: 2, hit: 1, excluded: 0 }

/** The worst tone among `tones`; `empty` when there is nothing to judge. */
export function worstTone(tones = []) {
  let best = null
  for (const t of tones) {
    if (!(t in TONE_RANK)) continue
    if (best === null || TONE_RANK[t] > TONE_RANK[best]) best = t
  }
  return best === null ? 'empty' : best
}

/**
 * Where a card places a technique: the sub-technique entry first, then the
 * exact technique entry, then the parent technique's entry.
 * @returns {{ tacticIds: string[], name: string|null, via: string } | null}
 */
export function placeTechnique(techniqueId, card) {
  const list = card?.mitre_attack?.techniques
  if (!Array.isArray(list) || !list.length) return null
  const id = String(techniqueId || '').trim().toUpperCase()
  if (!id) return null
  const parent = parentTechnique(id)
  const up = (v) => String(v || '').toUpperCase()
  let via = 'subtechnique'
  let m = list.find((t) => up(t.subtechnique_id) === id)
  if (!m) { via = 'technique'; m = list.find((t) => up(t.technique_id) === id) }
  if (!m && parent !== id) { via = 'parent'; m = list.find((t) => up(t.technique_id) === parent) }
  if (!m) return null
  return { tacticIds: Array.isArray(m.tactic_ids) ? m.tactic_ids.filter(Boolean) : [], name: m.name || null, via }
}

function countBy(rows, keyOf) {
  const out = {}
  for (const r of rows) {
    const k = keyOf(r)
    out[k] = (out[k] || 0) + 1
  }
  return out
}

/**
 * @param {object} p
 * @param {Array}  p.results     Result rows for the run (`GET /api/results/{run_id}`)
 * @param {object} p.run         the run record (status, tc_verdict, tc_verdict_detail)
 * @param {object} p.cards       `{ [ttp_ref]: card }` — the cards that loaded
 * @param {object} p.cardErrors  `{ [ttp_ref]: message }` — the cards that did not
 */
export function buildTechniqueModel({ results = [], run = null, cards = {}, cardErrors = {} } = {}) {
  const proven = isRunComplete(run?.status)
  const rows = Array.isArray(results) ? results.filter(Boolean) : []

  // Group rows by technique id. Rows with no technique are unplaceable by
  // construction and are reported, not dropped.
  const groups = new Map()
  const noTechnique = []
  for (const r of rows) {
    const id = String(r.mitre_technique || '').trim().toUpperCase()
    if (!id) { noTechnique.push(r); continue }
    if (!groups.has(id)) groups.set(id, [])
    groups.get(id).push(r)
  }

  const placed = []
  const unplaced = []
  for (const [id, trows] of groups) {
    const tones = trows.map((r) => resultTone(r, proven))
    const denomRows = trows.filter((r, i) => tones[i] !== 'excluded')
    const hits = tones.filter((t) => t === 'hit').length
    const base = {
      id,
      rows: trows,
      steps: [...new Set(trows.map((r) => r.step_id).filter(Boolean))],
      ttpRefs: [...new Set(trows.map((r) => r.ttp_ref).filter(Boolean))],
      hits,
      denominator: denomRows.length,
      excluded: trows.length - denomRows.length,
      tone: worstTone(tones),
      outcomes: countBy(trows, (r) => (r.outcome ? String(r.outcome).toLowerCase() : r.observed ? 'observed' : 'pending')),
      kpi: countBy(trows, (r) => r.kpi_verdict || 'unscored'),
    }

    let placement = null
    let reason = null
    if (!base.ttpRefs.length) {
      reason = 'no TTP card referenced by its rows'
    } else {
      for (const ref of base.ttpRefs) {
        const card = cards[ref]
        if (card) {
          placement = placeTechnique(id, card)
          if (placement) break
        }
      }
      if (!placement) {
        const ref = base.ttpRefs[0]
        if (cardErrors[ref]) reason = `card ${ref} could not be loaded: ${cardErrors[ref]}`
        else if (!cards[ref]) reason = `card ${ref} not loaded`
        else reason = `card ${ref} does not list ${id}`
      }
    }
    if (placement && placement.tacticIds.length) {
      placed.push({ ...base, name: placement.name, tacticIds: placement.tacticIds, via: placement.via })
    } else if (placement) {
      unplaced.push({ ...base, name: placement.name, tacticIds: [], reason: 'card lists the technique with no tactic' })
    } else {
      unplaced.push({ ...base, name: null, tacticIds: [], reason })
    }
  }
  if (noTechnique.length) {
    unplaced.push({
      id: '(no technique)', rows: noTechnique, steps: [...new Set(noTechnique.map((r) => r.step_id).filter(Boolean))],
      ttpRefs: [], hits: 0, denominator: 0, excluded: 0, tone: 'pending', outcomes: {}, kpi: {},
      name: null, tacticIds: [], reason: `${noTechnique.length} row(s) carry no technique`,
    })
  }

  const firstTactic = (t) => Math.min(...t.tacticIds.map(tacticIndex).map((i) => (i < 0 ? 99 : i)))
  placed.sort((a, b) => firstTactic(a) - firstTactic(b) || a.id.localeCompare(b.id))
  unplaced.sort((a, b) => a.id.localeCompare(b.id))

  const stages = ATTACK_TACTICS.map((t) => {
    const techniques = placed.filter((p) => p.tacticIds.includes(t.id))
    return {
      ...t,
      techniques,
      empty: techniques.length === 0,
      tone: worstTone(techniques.map((p) => p.tone)),
      hits: techniques.reduce((n, p) => n + p.hits, 0),
      denominator: techniques.reduce((n, p) => n + p.denominator, 0),
    }
  })

  const allTones = rows.map((r) => resultTone(r, proven))
  return {
    proven,
    runStatus: run?.status || null,
    tcVerdict: run?.tc_verdict || null,
    tcVerdictDetail: run?.tc_verdict_detail || null,
    stages,
    techniques: placed,
    unplaced,
    totals: {
      rows: rows.length,
      hits: allTones.filter((t) => t === 'hit').length,
      denominator: allTones.filter((t) => t !== 'excluded').length,
      excluded: allTones.filter((t) => t === 'excluded').length,
    },
  }
}
