import React, { useEffect, useMemo, useState } from 'react'
import { getScenarios } from '../../api/client.js'
import '../../styles/destinations/coverage.css'

/**
 * StackCoverageView — PANW stack × MITRE tactic coverage matrix.
 *
 * Different lens than the ATT&CK Coverage view. Where the ATT&CK matrix
 * answers "which techniques does the library exercise," this view
 * answers "which Palo Alto products carry the detection load, and where
 * does XSIAM stitching reinforce the single-product signal."
 *
 * Rows: PANW products (Cortex XDR / XSIAM / Cloud / ITDR / XSOAR / Xpanse +
 * Strata NGFW + Prisma Cloud).
 * Cols: MITRE tactic ordered along the kill chain (TA0001 Initial Access →
 * TA0010 Exfiltration → TA0011 C2).
 * Cells: scenario count for that (product, tactic) pair. Intensity scales
 * with count. Click a cell to filter the scenario list in the panel below.
 *
 * Props:
 *   onFilterByCell — (productId, tactic, scenarioIds) => void
 */

// Detection plane → PANW product mapping. A scenario's primary plane maps
// to one product; cross-plane scenarios (plane: ANALYTICS) contribute to
// XSIAM stitching specifically.
const PRODUCT_DEFS = [
  { id: 'cortex-xdr',    label: 'Cortex XDR',    sub: 'endpoint detection',           planes: ['EDR'] },
  { id: 'cortex-xsiam',  label: 'Cortex XSIAM',  sub: 'analytics + stitching',        planes: ['ANALYTICS'] },
  { id: 'cortex-cloud',  label: 'Cortex Cloud',  sub: 'CDR + CSPM + CWP',              planes: ['CDR'] },
  { id: 'cortex-cloud-app', label: 'Cortex Cloud App', sub: 'SaaS / OAuth identity', planes: ['CLOUD_APP'] },
  { id: 'cortex-itdr',   label: 'Cortex ITDR',   sub: 'identity threat detection',     planes: ['ITDR'] },
  { id: 'cortex-xsoar',  label: 'Cortex XSOAR',  sub: 'auto-containment playbooks',    planes: ['ANALYTICS'], isResponse: true },
  { id: 'cortex-xpanse', label: 'Cortex Xpanse', sub: 'external exposure',             planes: [], soft: true },
  { id: 'strata-ngfw',   label: 'Strata NGFW',   sub: 'network security + URL+DNS',    planes: ['NDR'] },
  { id: 'prisma-cloud',  label: 'Prisma Cloud',  sub: 'workload + posture',            planes: ['CDR'], soft: true },
]

// Kill-chain ordering — column order matters for the visual "left to right
// = attacker progresses" reading.
const TACTIC_DEFS = [
  { id: 'TA0001', short: 'IA',  label: 'Initial Access' },
  { id: 'TA0002', short: 'EX',  label: 'Execution' },
  { id: 'TA0003', short: 'PER', label: 'Persistence' },
  { id: 'TA0004', short: 'PRIV',label: 'Privilege Esc' },
  { id: 'TA0005', short: 'DE',  label: 'Defense Evasion' },
  { id: 'TA0006', short: 'CR',  label: 'Cred Access' },
  { id: 'TA0007', short: 'DI',  label: 'Discovery' },
  { id: 'TA0008', short: 'LM',  label: 'Lateral Mvmt' },
  { id: 'TA0009', short: 'CO',  label: 'Collection' },
  { id: 'TA0011', short: 'C2',  label: 'C2' },
  { id: 'TA0010', short: 'EX',  label: 'Exfiltration' },
  { id: 'TA0040', short: 'IM',  label: 'Impact' },
]

// Continuous cell-fill ramp — replaces a discrete 3-tier bucket that
// normalized intensity against the MATRIX-WIDE max count. That scheme
// had a real collapse: whenever any other cell in the matrix ran busy
// enough (max >= ~8), a 1-scenario cell and a 3-scenario cell both fell
// under the same "low" tier (intensity < 0.4) and rendered pixel-
// identical — the exact "two different states read as the same thing"
// defect this project exists to catch, just relocated into its own
// coverage view. `cellFillPercent` is a pure function of the cell's OWN
// count only, never of the matrix's max, so a given count always paints
// the same shade no matter how busy any other cell is — the collapse
// can't recur because nothing here is relative anymore.
//
// The numeral inside every non-empty cell (`stack-coverage__cell-count`)
// stays the primary, always-present carrier of the exact count — the
// fill is a secondary, at-a-glance density cue, so colour is never the
// ONLY channel distinguishing two cells (a colour-blind operator still
// reads the number).
//
// FILL_MIN_PCT/FILL_MAX_PCT were chosen by computing the actual mixed
// color of `color-mix(in srgb, var(--ac-ink) X%, var(--s2))` against
// `var(--tx)` in both themes: dark-theme text contrast crosses below
// the 4.5:1 AA floor between X=56 and X=57 (4.545 -> 4.487), so capping
// at 55 keeps EVERY cell's numeral AA-legible in both themes without
// ever needing to swap text color per-cell. Light theme has far more
// headroom (6.4:1+ at X=68) so the dark-theme ceiling is the binding
// constraint. FILL_MIN_PCT keeps a 1-scenario cell visibly tinted
// against the (opacity-reduced, colorless) empty-cell background rather
// than fading toward invisible — the exact failure mode a DC most needs
// NOT to have on the thin cells.
const FILL_MIN_PCT = 14
const FILL_MAX_PCT = 55
const FILL_SOFTNESS = 2.5 // shapes how quickly the ramp climbs for small counts

export function cellFillPercent(count) {
  if (!count || count <= 0) return 0
  const intensity = count / (count + FILL_SOFTNESS) // asymptotic 0..1, strictly increasing in count
  return Math.round(FILL_MIN_PCT + intensity * (FILL_MAX_PCT - FILL_MIN_PCT))
}

export default function StackCoverageView({ onFilterByCell = () => {} }) {
  const [scenarios, setScenarios] = useState([])
  const [loading, setLoading]     = useState(true)
  const [selectedCell, setSelectedCell] = useState(null) // { product, tactic, scenarioIds }

  useEffect(() => {
    setLoading(true)
    getScenarios({})
      .then((d) => {
        const list = Array.isArray(d) ? d : (d && d.scenarios) || []
        setScenarios(list)
      })
      .catch(() => setScenarios([]))
      .finally(() => setLoading(false))
  }, [])

  // Build the (product, tactic) → scenarios map.
  const matrix = useMemo(() => buildMatrix(scenarios), [scenarios])

  const totalScenarios = scenarios.length
  const coveredProducts = useMemo(
    () => PRODUCT_DEFS.filter((p) => {
      const row = matrix[p.id] || {}
      return Object.values(row).some((cell) => cell.length > 0)
    }).length,
    [matrix],
  )

  return (
    <div className="stack-coverage">
      <div className="stack-coverage__intro">
        <p className="stack-coverage__intro-prose">
          Where the ATT&CK matrix shows what techniques the library
          exercises, this view shows <strong>which PANW products carry the
          detection load</strong>, and where Cortex XSIAM stitching
          reinforces the single-product signal into a correlated incident.
        </p>
        <div className="stack-coverage__intro-stats">
          <div className="stack-coverage__stat">
            <div className="stack-coverage__stat-value mono">{coveredProducts}</div>
            <div className="stack-coverage__stat-label">products with coverage</div>
          </div>
          <div className="stack-coverage__stat">
            <div className="stack-coverage__stat-value mono">{totalScenarios}</div>
            <div className="stack-coverage__stat-label">scenarios in library</div>
          </div>
          <div className="stack-coverage__stat">
            <div className="stack-coverage__stat-value mono">{TACTIC_DEFS.length}</div>
            <div className="stack-coverage__stat-label">kill chain tactics</div>
          </div>
        </div>
      </div>

      {loading ? (
        <div className="coverage__empty mono">loading scenario library…</div>
      ) : (
        <div className="stack-coverage__matrix" role="grid" aria-label="PANW stack coverage matrix">
          {/* Header row: tactic columns */}
          <div className="stack-coverage__row stack-coverage__row--head">
            <div className="stack-coverage__product stack-coverage__product--head">
              Product / Plane
            </div>
            {TACTIC_DEFS.map((t) => (
              <div
                key={t.id}
                className="stack-coverage__col-head"
                title={`${t.id} · ${t.label}`}
              >
                <span className="stack-coverage__col-short mono">{t.short}</span>
                <span className="stack-coverage__col-label">{t.label}</span>
              </div>
            ))}
            <div className="stack-coverage__col-head stack-coverage__col-head--total">
              <span className="stack-coverage__col-short mono">∑</span>
              <span className="stack-coverage__col-label">total</span>
            </div>
          </div>

          {/* Product rows */}
          {PRODUCT_DEFS.map((p) => {
            const row = matrix[p.id] || {}
            const total = Object.values(row).reduce((n, cell) => n + cell.length, 0)
            return (
              <div
                key={p.id}
                className={
                  'stack-coverage__row' +
                  (p.soft ? ' stack-coverage__row--soft' : '') +
                  (p.isResponse ? ' stack-coverage__row--response' : '')
                }
              >
                <div className="stack-coverage__product">
                  <div className="stack-coverage__product-label">{p.label}</div>
                  <div className="stack-coverage__product-sub mono">{p.sub}</div>
                </div>
                {TACTIC_DEFS.map((t) => {
                  const cellScenarios = row[t.id] || []
                  const count = cellScenarios.length
                  const fillPct = cellFillPercent(count)
                  const isSelected =
                    selectedCell && selectedCell.product === p.id && selectedCell.tactic === t.id
                  return (
                    <button
                      type="button"
                      key={t.id}
                      className={
                        'stack-coverage__cell' +
                        (count === 0 ? ' stack-coverage__cell--empty' : '') +
                        (isSelected ? ' stack-coverage__cell--selected' : '')
                      }
                      style={count > 0
                        ? { background: `color-mix(in srgb, var(--ac-ink) ${fillPct}%, var(--s2))` }
                        : undefined}
                      disabled={count === 0}
                      onClick={() => {
                        const sel = { product: p.id, tactic: t.id, scenarioIds: cellScenarios }
                        setSelectedCell(sel)
                      }}
                      title={count > 0
                        ? `${p.label} · ${t.label} — ${count} scenario${count === 1 ? '' : 's'}`
                        : `${p.label} · ${t.label} — no coverage`}
                      aria-label={`${p.label} ${t.label} — ${count} scenarios`}
                    >
                      <span className="stack-coverage__cell-count mono">
                        {count > 0 ? count : '·'}
                      </span>
                    </button>
                  )
                })}
                <div className="stack-coverage__cell stack-coverage__cell--total mono">
                  {total}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {selectedCell && (
        <SelectedCellPanel
          cell={selectedCell}
          scenarios={scenarios}
          onClose={() => setSelectedCell(null)}
          onFilterByCell={(scenarioIds) => {
            onFilterByCell(selectedCell.product, selectedCell.tactic, scenarioIds)
            setSelectedCell(null)
          }}
        />
      )}
    </div>
  )
}

/* ─── Subcomponents ──────────────────────────────────────────────── */

function SelectedCellPanel({ cell, scenarios, onClose, onFilterByCell }) {
  const product = PRODUCT_DEFS.find((p) => p.id === cell.product)
  const tactic  = TACTIC_DEFS.find((t) => t.id === cell.tactic)

  const cellScenarios = useMemo(() => {
    const ids = new Set(cell.scenarioIds)
    return scenarios.filter((s) => ids.has(s.scenario_id || s.id))
  }, [scenarios, cell.scenarioIds])

  return (
    <div className="stack-coverage__panel">
      <div className="stack-coverage__panel-head">
        <div>
          <div className="stack-coverage__panel-eyebrow mono">
            {product?.label} × {tactic?.id}
          </div>
          <h3 className="stack-coverage__panel-title">{tactic?.label}</h3>
        </div>
        <div className="stack-coverage__cell-actions">
          <button
            type="button"
            className="btn btn--primary"
            onClick={() => onFilterByCell(cell.scenarioIds)}
            disabled={cell.scenarioIds.length === 0}
          >
            Filter Operations <span className="kbd">→</span>
          </button>
          <button type="button" className="btn" onClick={onClose}>
            Close
          </button>
        </div>
      </div>

      <div className="stack-coverage__panel-list">
        {cellScenarios.map((s) => (
          <div key={s.scenario_id || s.id} className="stack-coverage__panel-row">
            <span className="stack-coverage__panel-sid mono">{s.scenario_id || s.id}</span>
            <span className="stack-coverage__panel-name">{s.name || '(unnamed)'}</span>
            {s.threat_report && (
              <span className="stack-coverage__panel-actor mono">
                {s.threat_report.split(/\s*[—\-]\s*/)[0]}
              </span>
            )}
          </div>
        ))}
        {cellScenarios.length === 0 && (
          <div className="coverage__empty mono">no scenarios in this cell</div>
        )}
      </div>
    </div>
  )
}

/* ─── Matrix builder ─────────────────────────────────────────────── */

/**
 * Build a { productId: { tacticId: [scenarioId, ...] } } sparse map.
 *
 * A scenario contributes to a product if any of its detection planes
 * matches the product's `planes` list. We walk both the primary plane
 * and the expected_detections planes inside each step so multi-plane
 * scenarios show up in every relevant product row.
 */
function buildMatrix(scenarios) {
  const out = {}
  for (const p of PRODUCT_DEFS) out[p.id] = {}

  for (const s of scenarios) {
    const sid = s.scenario_id || s.id
    if (!sid) continue

    const tactic = (s.mitre_tactic || '').toUpperCase()
    if (!tactic) continue

    const planes = scenarioPlanes(s)

    for (const product of PRODUCT_DEFS) {
      if (product.planes.length === 0) continue   // soft entries with no plane link
      const hit = product.planes.some((p) => planes.has(p))
      if (!hit) continue
      if (!out[product.id][tactic]) out[product.id][tactic] = []
      if (!out[product.id][tactic].includes(sid)) {
        out[product.id][tactic].push(sid)
      }
    }
  }

  return out
}

function scenarioPlanes(s) {
  const planes = new Set()
  const primary = (s.plane || '').toUpperCase()
  if (primary) planes.add(primary)
  ;(s.steps || []).forEach((step) => {
    ;(step.expected_detections || []).forEach((d) => {
      const p = (d.plane || '').toUpperCase()
      if (p) planes.add(p)
    })
  })
  return planes
}
