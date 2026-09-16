import React, { useState } from 'react'
import { PLANES, SOURCES, MATRIX, CELL, planeTint } from './povdata/planes.js'
import { ANALYTICS_SOURCES } from './povdata/evidence.js'

/**
 * PlaneCoverageMatrix — the planes × doors cross-tab, and what feeds analytics.
 *
 * WHY A CROSS-TAB RATHER THAN A LIST
 * "Which planes do we cover?" and "which ingestion doors do we have?" are the
 * same question asked from two sides, and answering them separately hides the
 * only interesting cell: a plane that is covered, but only through a door this
 * POV does not have. Sixteen planes on one axis, six Cortex doors on the other,
 * and the cell says whether that pairing carries.
 *
 * EVERY MATRIX GETS A LEGEND, AND N/A IS A LABEL.
 * The '·' this used to print in an inapplicable cell was not read as neutral —
 * a DC read it as a gap, because an unexplained mark in a coverage matrix is
 * always read as the bad case. FULL / PART / GAP / N/A are spelled out with
 * one-line definitions underneath.
 *
 * THE TINT IS A BORDER, NEVER THE INK.
 * Row labels are --tx2 with the plane tint as a left border. The 16-hue
 * categorical palette is authored at the 3:1 non-text floor for fills and
 * borders; a whole revision of this console used it as 9px `color:` and the
 * labels stopped being readable.
 *
 * ANALYTICS ALERTS BY DATA SOURCE
 * The Engine scores analytics detections, but only over datasets that actually
 * arrive — so an absent source is the reason an ABIOC never fired, and it is a
 * DIFFERENT result from a detection that had its data and stayed quiet. The
 * first exports as not-addressable; only the second is a miss. That distinction
 * is the whole point of the panel.
 */
export default function PlaneCoverageMatrix() {
  const [cell, setCell] = useState(null)

  const totalAlerts = ANALYTICS_SOURCES.reduce((a, s) => a + s[3], 0)
  const feeding = ANALYTICS_SOURCES.filter((s) => s[4] === 'feeding').length

  const tally = { c: 0, p: 0, g: 0, '-': 0 }
  Object.values(MATRIX).forEach((row) => row.forEach((v) => { tally[v] += 1 }))

  return (
    <div className="pov-page" data-testid="plane-coverage">
      <div className="pov-section" style={{ marginTop: 0 }}>
        <span className="pov-section__label">
          {PLANES.length} planes × {SOURCES.length} doors · {tally.g} gaps
        </span>
        <span className="pov-section__hr" />
      </div>

      <div className="pov-table__scroll">
        <table className="pov-table">
          <thead>
            <tr>
              <th style={{ minWidth: 168 }}>Detection plane</th>
              {SOURCES.map(([code, name, blurb]) => (
                <th key={code} title={`${name} — ${blurb}`}>{code}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {PLANES.map(([code, label, n]) => (
              <tr key={code}>
                <td className="pov-td-strong">
                  <span
                    className="pov-chip-plane"
                    style={{ borderLeftColor: planeTint(code) }}
                  >
                    {code}
                  </span>
                  <span style={{ marginLeft: 8, color: 'var(--tx2)' }}>{label}</span>
                  <span style={{ marginLeft: 8, color: 'var(--tx3)' }}>{n}</span>
                </td>
                {MATRIX[code].map((v, i) => {
                  const def = CELL[v]
                  const src = SOURCES[i][0]
                  const on = cell && cell.plane === code && cell.src === src
                  return (
                    <td key={src} style={{ padding: 4 }}>
                      <button
                        type="button"
                        onClick={() => setCell(on ? null : { plane: code, src, v })}
                        aria-pressed={on}
                        style={{
                          width: '100%',
                          padding: '6px 8px',
                          background: def.bg,
                          color: def.fg,
                          border: `1px solid ${on ? 'var(--ac)' : 'transparent'}`,
                          borderBottom: `2px solid ${def.underline}`,
                          borderRadius: 4,
                          font: '700 8.5px/1 var(--font-mono)',
                          letterSpacing: '.07em',
                          cursor: 'pointer',
                        }}
                      >
                        {def.label}
                      </button>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="pov-legend">
        {[
          ['FULL', 'c', 'this door carries the plane on its own'],
          ['PART', 'p', 'carried, but only for some of the plane’s detections'],
          ['GAP', 'g', 'the plane needs this door and it is not there'],
          ['n/a', '-', 'this door is not how that plane arrives at all'],
        ].map(([label, key, def]) => (
          <span className="pov-legend__item" key={key}>
            <span
              className="pov-pill"
              style={{ background: CELL[key].bg, color: CELL[key].fg }}
            >
              {label}
            </span>
            <span className="pov-legend__def">{def}</span>
          </span>
        ))}
      </div>

      {cell && (
        <div className="pov-panel" style={{ marginTop: 14 }}>
          <div className="pov-panel__head">
            <div className="pov-panel__title">{cell.plane} × {cell.src}</div>
            <div className="pov-panel__sub">{CELL[cell.v].label} · what carries it</div>
          </div>
          <div className="pov-panel__body">
            <p style={{ font: '400 11.5px/1.6 var(--font-ui)', color: 'var(--tx2)', margin: 0 }}>
              {SOURCES.find((s) => s[0] === cell.src)[2]} —{' '}
              {cell.v === 'c'
                ? `${SOURCES.find((s) => s[0] === cell.src)[1]} carries the ${cell.plane} plane on its own.`
                : cell.v === 'p'
                  ? `${SOURCES.find((s) => s[0] === cell.src)[1]} carries part of the ${cell.plane} plane; the rest needs another door.`
                  : cell.v === 'g'
                    ? `${cell.plane} needs this door and it is not configured. Claims behind it export as not-executed.`
                    : `${cell.plane} does not arrive through this door at all. Not a gap — a different path.`}
            </p>
          </div>
        </div>
      )}

      <div className="pov-section">
        <span className="pov-section__label">
          Analytics alerts by data source · {totalAlerts} across {ANALYTICS_SOURCES.length} sources, {feeding} feeding
        </span>
        <span className="pov-section__hr" />
      </div>

      <div className="pov-panel" data-testid="analytics-sources">
        {ANALYTICS_SOURCES.map(([name, door, dataset, n, state, drives]) => (
          <div className="pov-panel__row" key={dataset}>
            <div className="pov-card__head">
              <span className={`pov-dot pov-dot--${state === 'feeding' ? 'pos' : state === 'partial' ? 'warn' : 'crit'}`} />
              <span className="pov-card__title">{name}</span>
              <span className="pov-chip-plane" style={{ borderLeftColor: planeTint(door === 'AGT' ? 'EDR' : door === 'CC' ? 'CDR' : door === 'BVM' ? 'NDR' : 'ITDR') }}>
                {door}
              </span>
              <span className="pov-card__spacer" />
              <span className="pov-card__code">{dataset}</span>
              <span className="pov-fact__v" style={{ minWidth: 34, textAlign: 'right' }}>{n}</span>
              <span className={`pov-pill pov-pill--${state === 'feeding' ? 'pos' : state === 'partial' ? 'warn' : 'crit'}`}>
                {state.toUpperCase()}
              </span>
            </div>
            {/* Share-of-analytics bar. Zero-width is the honest rendering for an
                absent source — it is carrying none of the load, and that is the
                fact the panel is here to show. */}
            <div style={{ height: 4, background: 'var(--s3)', borderRadius: 2, overflow: 'hidden', margin: '7px 0' }}>
              <div style={{
                height: 4,
                width: `${totalAlerts ? (n / totalAlerts) * 100 : 0}%`,
                background: state === 'feeding' ? 'var(--pos)' : state === 'partial' ? 'var(--warn)' : 'var(--crit)',
              }} />
            </div>
            <div className="pov-card__blurb" style={{ marginBottom: 0 }}>{drives}</div>
          </div>
        ))}
      </div>

      <p style={{ font: '400 11px/1.7 var(--font-ui)', color: 'var(--tx2)', margin: '14px 0 0', maxWidth: 760 }}>
        An absent source exports as <strong>not-addressable</strong>, because the Engine
        was never given the data to score. That is a different result from a detection
        that had its data and stayed quiet, and only the second one is a miss. No NGFW
        traffic means no egress-volume analytics; no intel pack means no IOC-backed
        enrichment.
      </p>
    </div>
  )
}
