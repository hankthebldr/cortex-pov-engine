import React, { useState } from 'react'
import ObjectSheet from './ObjectSheet.jsx'
import { sheetFor } from './sheets.js'
import { componentCatalog, componentTally, FAMILY_LABEL } from './povdata/catalogs.js'

/**
 * ComponentsView — `1 · Scope`. One card per actual piece of the POV.
 *
 * This replaced a page that was trying to be three things at once: a lab
 * inventory, a tenant list and an agent list. Tenant and Agents are their own
 * destinations now; this is only the components — the agent, the Broker VM,
 * the collectors, the firewall, the Cortex connectors, the engine content and
 * the API — split into the simulation side and the Cortex side, because those
 * are the two things a DC stands up separately and the two things that fail
 * for completely different reasons.
 *
 * ACCENT FOLLOWS THE PRODUCT FAMILY, NOT THE STATE.
 * Cortex green for Cortex and its collectors, PANW orange for the NGFW,
 * neutral for POVengine's own ephemeral tooling — which is not a product at
 * all. The state is carried by the pill, so the card can say what a thing IS
 * and how it is DOING at the same time without the two competing for the same
 * channel.
 *
 * EVERY TALLY IS DERIVED FROM THE CATALOG.
 * The summary strip, the card pills, the object page and the flow-bar hint all
 * read `componentCatalog()`. An earlier revision quoted 6 ready / 3 partial /
 * 2 missing in the strip against a catalog that was really 4 / 6 / 2 — which
 * on a readiness screen is the one number that cannot be wrong, because it is
 * the number a DC uses to decide whether to run in front of a customer.
 */
export default function ComponentsView({ onNavigate = () => {} }) {
  const rows = componentCatalog()
  const tally = componentTally(rows)
  const [sheet, setSheet] = useState(null)
  const [sheetTab, setSheetTab] = useState(0)

  const open = (code) => { setSheet(code); setSheetTab(0) }

  const groups = [
    { key: 'sim', label: 'Simulation side', sub: 'stood up for this POV · dies with the lab' },
    { key: 'ctx', label: 'Cortex side', sub: 'the customer’s platform · persists beyond the POV' },
  ]

  return (
    <div className="pov-page">
      <div className="pov-page__rule" />
      <div className="pov-page__eyebrow">Phase 1 · Scope</div>
      <h1 className="pov-page__title">Components</h1>
      <p className="pov-page__lede">
        Every piece signal has to pass through. Each card names what it carries — so a
        component that cannot carry is a named gap in the readout rather than a silent
        one.
      </p>

      <div className="pov-stats" style={{ marginTop: 18 }} data-testid="component-tally">
        <div className="pov-stat"><span className="pov-stat__n">{tally.total}</span><span className="pov-stat__k">components</span></div>
        <div className="pov-stat pov-stat--pos"><span className="pov-stat__n">{tally.ready}</span><span className="pov-stat__k">ready</span></div>
        <div className="pov-stat pov-stat--warn"><span className="pov-stat__n">{tally.partial}</span><span className="pov-stat__k">partial</span></div>
        <div className="pov-stat pov-stat--crit"><span className="pov-stat__n">{tally.missing}</span><span className="pov-stat__k">missing</span></div>
      </div>

      {groups.map((g) => {
        const items = rows.filter((c) => c.group === g.key)
        return (
          <section key={g.key}>
            <div className="pov-section">
              <span className="pov-section__label">{g.label}</span>
              <span className="pov-section__hr" />
              {/* The family legend sits on the group header rather than on
                  every card: it explains a coding scheme once. */}
              <span className="pov-legend" style={{ marginTop: 0 }}>
                {['cortex', 'ngfw', 'pov'].map((fam) => (
                  <span className="pov-legend__item" key={fam}>
                    <span className="pov-dot" style={{ background: `var(--family-${fam === 'pov' ? 'none' : fam})` }} />
                    <span className="pov-legend__def">{FAMILY_LABEL[fam]}</span>
                  </span>
                ))}
              </span>
            </div>
            <div className="pov-grid pov-grid--lg">
              {items.map((c) => (
                <button
                  key={c.code}
                  type="button"
                  className="pov-card pov-card--tinted"
                  style={{ borderTopColor: c.tint }}
                  onClick={() => open(c.code)}
                  data-testid={`component-${c.code}`}
                >
                  <span className="pov-card__head">
                    {/* The 3px bar on the code chip is the family tint again —
                        a border, never the ink. */}
                    <span className="pov-chip-plane" style={{ borderLeftColor: c.tint }}>{c.code}</span>
                    <span className="pov-card__title">{c.name}</span>
                    <span className="pov-card__spacer" />
                    <span className={`pov-pill pov-pill--${c.state === 'ready' ? 'pos' : c.state === 'partial' ? 'warn' : 'crit'}`}>
                      {c.state.toUpperCase()}
                    </span>
                  </span>
                  <span className="pov-card__meta" style={{ display: 'block', marginBottom: 10 }}>
                    {c.endpoint} · {FAMILY_LABEL[c.family]}
                  </span>
                  <span className="pov-facts" style={{ marginBottom: 10 }}>
                    {c.facts.map(([k, v]) => (
                      <span className="pov-fact" key={k}>
                        <span className="pov-fact__k">{k}</span>
                        <span className="pov-fact__v">{v}</span>
                      </span>
                    ))}
                  </span>
                  <span className="pov-card__foot" style={{ display: 'block' }}>
                    <span className="pov-fact__k" style={{ display: 'block', marginBottom: 4 }}>Carries</span>
                    <span className="pov-card__blurb" style={{ marginBottom: 0 }}>{c.carries}</span>
                  </span>
                </button>
              ))}
            </div>
          </section>
        )
      })}

      {sheet && (
        <ObjectSheet
          sheet={sheetFor('component', sheet, { tab: sheetTab, go: (d) => () => { setSheet(null); onNavigate(d) } })}
          onClose={() => setSheet(null)}
          onTab={setSheetTab}
        />
      )}
    </div>
  )
}
