import React, { useMemo, useState } from 'react'
import { COLL_GROUPS } from './povdata/evidence.js'

/**
 * EvidenceCollection — the POV chip in the header, and the panel behind it.
 *
 * WHY THIS IS THE COLLECTION AND NOT A LABEL
 * ------------------------------------------
 * This chip used to read "POV · Acme Financial · FY27 Q3" and do nothing. But
 * a POVengine instance is deployed once for one POV and dies with the lab —
 * nothing in it survives teardown unless it is exported. That makes the
 * exported collection, not the running app, the actual deliverable, and makes
 * "how much have I got and how much of it is safely out" the thing worth
 * putting in permanent chrome.
 *
 * The count and the export meter live INSIDE the panel, not on the trigger.
 * They were on the trigger first; it measured 410px, pushed the header's
 * natural width to 1326, and the LIVE pill fell outside the frame at the
 * declared minimum. They read better in the panel header anyway — a large
 * count against "artefacts held", with the bar beside it.
 *
 * Props:
 *   povName    — "<customer> · <period>"
 *   onNavigate — (destinationId) => void; the export actions land on Proof
 */
export default function EvidenceCollection({
  povName = 'Acme Financial · FY27 Q3',
  collectionId = 'COL-7731',
  onNavigate = () => {},
}) {
  const [open, setOpen] = useState(false)
  // Which groups are included in the export. Every group with artefacts starts
  // included: the default has to be "everything you collected", because a
  // default that silently omitted a group would produce a bundle that is wrong
  // in a way nobody can see from the outside.
  const [excluded, setExcluded] = useState(() => new Set())

  const stats = useMemo(() => {
    const held = COLL_GROUPS.reduce((a, g) => a + g[2], 0)
    const bytes = COLL_GROUPS
      .filter((g) => !excluded.has(g[0]))
      .reduce((a, g) => a + g[4], 0)
    const included = COLL_GROUPS
      .filter((g) => !excluded.has(g[0]))
      .reduce((a, g) => a + g[2], 0)
    return { held, bytes: bytes.toFixed(1), included }
  }, [excluded])

  const toggle = (key) => setExcluded((prev) => {
    const next = new Set(prev)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    return next
  })

  return (
    <div className="pov-collection">
      <button
        type="button"
        className="pov-collection__trigger"
        aria-expanded={open}
        data-testid="pov-collection-trigger"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="pov-collection__kicker">POV</span>
        <span className="pov-collection__name">{povName}</span>
        <span className="pov-collection__rule" />
        {/* UNEXPORTED is a warning, not a status: it is the difference between
            a POV that produced evidence and a POV that still has it. */}
        <span className="pov-collection__state">UNEXPORTED</span>
      </button>

      {open && (
        <div className="pov-collection__panel" data-testid="pov-collection-panel">
          <div className="pov-collection__head">
            <div className="pov-collection__head-row">
              <span className="pov-rail__label">Evidence collection</span>
              <span className="pov-collection__id mono">{collectionId}</span>
            </div>
            <div className="pov-collection__head-row">
              <span className="pov-collection__count">{stats.held}</span>
              <span className="pov-collection__note">artefacts held</span>
              <span className="pov-collection__meter"><i style={{ width: '0%' }} /></span>
              <span className="pov-collection__pct">0% exported</span>
            </div>
            <div className="pov-collection__note">
              This instance is ephemeral — it is deployed once for this POV and dies
              with the lab. Nothing here survives teardown unless it is exported.
            </div>
          </div>

          <div className="pov-collection__list">
            {COLL_GROUPS.map(([key, label, n, sub, mb]) => {
              const on = !excluded.has(key)
              return (
                <button
                  key={key}
                  type="button"
                  className="pov-collection__group"
                  onClick={() => toggle(key)}
                  aria-pressed={on}
                >
                  <span className={'pov-collection__box' + (on ? ' pov-collection__box--on' : '')}>
                    <i />
                  </span>
                  <span>
                    <span className="pov-collection__glabel">{label}</span>
                    <span className="pov-collection__gsub">{sub}</span>
                  </span>
                  {/* A zero here is honest, not a failure: "nothing captured
                      yet" is a real state of the console-captures group. */}
                  <span className={'pov-collection__n' + (n === 0 ? ' pov-collection__n--zero' : '')}>{n}</span>
                  <span className="pov-collection__size">{mb ? `${mb} MB` : '—'}</span>
                </button>
              )
            })}
          </div>

          <div className="pov-collection__foot">
            <div className="pov-collection__head-row">
              <span className="pov-collection__total">
                {stats.included} artefacts · {stats.bytes} MB
              </span>
              <span className="pov-collection__last">last export: never</span>
            </div>
            <div className="pov-collection__actions">
              <button
                type="button"
                className="pov-btn pov-btn--primary pov-btn--flex"
                onClick={() => { setOpen(false); onNavigate('proof') }}
              >
                Export collection
              </button>
              <button type="button" className="pov-btn pov-btn--flex">
                Export &amp; seal manifest
              </button>
            </div>
            <div className="pov-collection__seal-note">
              Sealing writes a signed manifest of every digest, probe and verdict, so
              the bundle can be trusted after the lab is gone.
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
