import React, { useEffect, useRef } from 'react'

/**
 * ObjectSheet — ONE pop-out object page, for every kind of object.
 *
 * WHY THIS EXISTS
 * ---------------
 * There used to be three different sidebars: a Library scenario inspector, a
 * package drawer, and a chain-step panel. Each showed a different subset of the
 * same shape (identity, status, fields, expected detections, actions), each
 * with its own layout and its own idea of where the primary action goes. A DC
 * learned the interaction three times and could not carry it to a fourth
 * object. This is that interaction once: accent bar, type chip, id, status and
 * the object's own actions in the header; grouped editable fields in the body;
 * detections and side facts on the right; Cancel / Save in the footer.
 *
 * Dropping the Composer's right rail in favour of this also gave the canvas its
 * full width back, which is the other reason the sheet is a pop-out rather than
 * an in-layout panel.
 *
 * TABS ARE COMPONENT-ONLY, ON PURPOSE.
 * A component is the one kind with real depth — how it is configured, whether
 * it is reachable, and what claims sit behind it are three different questions.
 * Scenarios, packages, steps, agents, targets, tenants, TTP cards, CLI items
 * and streams keep the single-form sheet, so the standard interaction is
 * unchanged everywhere depth is not needed.
 *
 * THE DETECTIONS HEADING IS GATED.
 * `hasDets` is derived once where the sheet is built, not set per branch. It
 * used to be an unconditional sibling in the aside, so six kinds that pass an
 * empty `dets` array rendered an "Expected detections" heading with nothing
 * under it — which reads as "this object expects nothing to fire", a very
 * different claim from "detections are not how this object is judged".
 *
 * Props:
 *   sheet    — the built sheet object (see sheets.js)
 *   onClose  — () => void
 *   onTab    — (index) => void
 *   onExplain— (detectionId) => void   opens the detection drill-down
 */
export default function ObjectSheet({ sheet, onClose = () => {}, onTab = () => {}, onExplain = null }) {
  const closeRef = useRef(null)

  // Escape closes, and focus lands inside the sheet on open. Without the focus
  // move a keyboard user's next Tab continued from wherever they were on the
  // surface BEHIND the scrim, which is both confusing and a way to activate a
  // control you cannot see.
  useEffect(() => {
    if (!sheet) return undefined
    closeRef.current?.focus()
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [sheet, onClose])

  if (!sheet) return null

  return (
    <div
      className="pov-sheet-scrim"
      data-testid="object-sheet"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <aside
        className="pov-sheet"
        role="dialog"
        aria-modal="true"
        aria-label={`${sheet.kindLabel}: ${sheet.name}`}
      >
        <div className="pov-sheet__accent" />

        <header className="pov-sheet__head">
          <div className="pov-sheet__head-row">
            <span className="pov-pill">{String(sheet.kindLabel).toUpperCase()}</span>
            <span className="pov-sheet__id mono">{sheet.id}</span>
            {sheet.status && (
              <span className={`pov-pill pov-pill--${sheet.statusTone || 'dim'}`}>{sheet.status}</span>
            )}
            <div className="pov-sheet__actions">
              {(sheet.actions || []).map((a) => (
                <button
                  key={a.label}
                  type="button"
                  className={'pov-btn' + (a.primary ? ' pov-btn--primary' : '')}
                  onClick={a.go}
                >
                  {a.label}
                </button>
              ))}
              {/* A labelled button, not a ✕ glyph. The DS forbids Unicode-glyph
                  icons in brand material, and "Close" is also the only one of
                  these controls a screen reader can announce usefully. */}
              <button type="button" className="pov-btn" ref={closeRef} onClick={onClose}>Close</button>
            </div>
          </div>
          <h2 className="pov-sheet__title">{sheet.name}</h2>
          {sheet.hasTabs && (
            <div className="pov-tabs" style={{ marginTop: 12, borderBottom: 0 }}>
              {sheet.tabs.map((t, i) => (
                <button
                  key={t.label}
                  type="button"
                  className={'pov-tab' + (t.active ? ' pov-tab--on' : '')}
                  onClick={() => onTab(i)}
                >
                  {t.label}
                </button>
              ))}
            </div>
          )}
        </header>

        <div className="pov-sheet__body">
          <div>
            {(sheet.groups || []).map((g) => (
              <section key={g.label} style={{ marginBottom: 20 }}>
                <div className="pov-section" style={{ marginTop: 0 }}>
                  <span className="pov-section__label">{g.label}</span>
                  <span className="pov-section__hr" />
                </div>
                {g.fields.map((fld) => (
                  <label className="pov-field" key={fld.k}>
                    <span className="pov-field__k">{fld.k}</span>
                    {fld.edit ? (
                      fld.multiline ? (
                        <textarea className="pov-field__input" defaultValue={fld.v} spellCheck={false} />
                      ) : (
                        <input className="pov-field__input" defaultValue={fld.v} />
                      )
                    ) : (
                      // A locked field still renders as a field, not as prose:
                      // the point of the object page is that you can see the
                      // whole record in one shape and tell at a glance which
                      // parts of it you own.
                      <span className="pov-field__input" style={{ display: 'block', opacity: .72 }}>{fld.v}</span>
                    )}
                  </label>
                ))}
              </section>
            ))}

            {sheet.cmd && (
              <section>
                <div className="pov-section" style={{ marginTop: 0 }}>
                  <span className="pov-section__label">{sheet.cmdLabel || 'Command'}</span>
                  <span className="pov-section__hr" />
                </div>
                <textarea className="pov-field__input" defaultValue={sheet.cmd} spellCheck={false} />
              </section>
            )}
          </div>

          <div className="pov-sheet__aside">
            {/* Gated — see "THE DETECTIONS HEADING IS GATED" above. */}
            {sheet.hasDets && (
              <section>
                <div className="pov-section" style={{ marginTop: 0 }}>
                  <span className="pov-section__label">Expected detections</span>
                  <span className="pov-section__hr" />
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
                  {sheet.dets.map((d) => (
                    // Every detection chip is a drill-down into the detection
                    // OBJECT — the rule body, the fields it keys on, why it
                    // fires, the false-positive guard, the negative control. A
                    // claim whose logic you cannot read is not a claim a DC can
                    // defend in a room.
                    <button
                      key={d.id || d.label}
                      type="button"
                      className="pov-chip-det"
                      onClick={() => onExplain && onExplain(d.id)}
                      title={onExplain ? 'Explain this detection' : undefined}
                    >
                      {d.label}
                    </button>
                  ))}
                </div>
              </section>
            )}

            {(sheet.side || []).map((s) => (
              <section key={s.label}>
                <div className="pov-section" style={{ marginTop: 0 }}>
                  <span className="pov-section__label">{s.label}</span>
                  <span className="pov-section__hr" />
                </div>
                <div className="pov-facts">
                  {s.rows.map((r) => (
                    <div className="pov-fact" key={r.k}>
                      <span className="pov-fact__k">{r.k}</span>
                      <span className={'pov-fact__v' + (r.tone ? ` pov-fact__v--${r.tone}` : '')}>{r.v}</span>
                    </div>
                  ))}
                </div>
              </section>
            ))}

            {sheet.hint && (
              <p style={{ font: '400 10.5px/1.6 var(--font-ui)', color: 'var(--tx3)', margin: 0 }}>
                {sheet.hint}
              </p>
            )}
          </div>
        </div>

        <footer className="pov-sheet__foot">
          <button type="button" className="pov-btn" onClick={onClose}>Cancel</button>
          <button type="button" className="pov-btn pov-btn--primary" onClick={onClose}>Save</button>
        </footer>
      </aside>
    </div>
  )
}
