import React, { useMemo, useState } from 'react'
import ObjectSheet from './ObjectSheet.jsx'
import { sheetFor } from './sheets.js'
import { cliCatalog } from './povdata/catalogs.js'
import { PLANES, planeTint } from './povdata/planes.js'

/**
 * CliItemsView — `2 · Compose`. Hand-authored shell steps.
 *
 * A CLI item is composed here, staged and digest-pinned exactly like a
 * package, then written to the host by the beacon and executed in place. That
 * is the whole reason it is a first-class object rather than a free-text field
 * on a chain step: an ad-hoc command typed into a step has no digest, no
 * declared teardown and no expected detections, so a run that used one cannot
 * be audited afterwards or repeated identically.
 *
 * THE PLANE CHIPS FILTER THIS GRID, NOT THE LIBRARY'S.
 * The chip row is shared with the sibling Compose destinations and recounts
 * per destination. It used to quote the scenario counts everywhere it
 * rendered, so CLI Items showed "EDR 22" above a grid that had one EDR item.
 * The UC/TC index gets no chips at all — it is a fixed index, not a filterable
 * grid, and a filter control that does nothing is worse than none.
 */
export default function CliItemsView({ onNavigate = () => {} }) {
  const all = cliCatalog()
  const [plane, setPlane] = useState(null)
  const [openId, setOpenId] = useState(null)

  const rows = useMemo(
    () => (plane ? all.filter((c) => c.plane === plane) : all),
    [all, plane],
  )

  // Counts are of THIS grid, per plane. A chip that promises items it cannot
  // show is how a DC concludes the corpus is missing content.
  const chips = useMemo(
    () => PLANES
      .map(([code]) => ({ code, n: all.filter((c) => c.plane === code).length }))
      .filter((c) => c.n > 0),
    [all],
  )

  return (
    <div className="pov-page">
      <div className="pov-page__rule" />
      <div className="pov-page__eyebrow">Phase 2 · Compose</div>
      <h1 className="pov-page__title">CLI items</h1>
      <p className="pov-page__lede">
        Shell and PowerShell steps authored here, digest-pinned on the shelf, and
        executed in place by the beacon. Each declares the identity it runs as, the
        detections it expects, and the teardown that removes it.
      </p>

      <div className="pov-section">
        <span className="pov-section__label">Filter by plane</span>
        <span className="pov-section__hr" />
        <button type="button" className="pov-btn" onClick={() => onNavigate('composer')}>
          + Add CLI item
        </button>
      </div>
      <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginBottom: 14 }}>
        <button
          type="button"
          className={'pov-chip-plane' + (plane === null ? ' pov-card--on' : '')}
          onClick={() => setPlane(null)}
          style={{ borderLeftColor: 'var(--bd2)', cursor: 'pointer' }}
        >
          ALL {all.length}
        </button>
        {chips.map((c) => (
          <button
            key={c.code}
            type="button"
            className={'pov-chip-plane' + (plane === c.code ? ' pov-card--on' : '')}
            style={{ borderLeftColor: planeTint(c.code), cursor: 'pointer' }}
            onClick={() => setPlane((p) => (p === c.code ? null : c.code))}
          >
            {c.code} {c.n}
          </button>
        ))}
      </div>

      <div className="pov-grid pov-grid--lg">
        {rows.map((c) => (
          <button
            key={c.id}
            type="button"
            className="pov-card"
            onClick={() => setOpenId(c.id)}
            data-testid={`cli-${c.id}`}
          >
            <span className="pov-card__head">
              <span className="pov-card__code">{c.id}</span>
              <span className="pov-card__spacer" />
              <span className={`pov-pill pov-pill--${c.state === 'ready' ? 'pos' : 'warn'}`}>
                {c.state.toUpperCase()}
              </span>
            </span>
            <span className="pov-card__title" style={{ display: 'block', marginBottom: 8 }}>{c.name}</span>
            {/* A preview, not the whole script — the object page is where it is
                read and edited. Three lines is enough to recognise it. */}
            <span className="pov-code pov-code--in" style={{ marginBottom: 10 }}>
              {c.body.split('\n').filter((l) => l.trim() && !l.startsWith('#!')).slice(0, 3).join('\n')}
            </span>
            <span className="pov-facts" style={{ marginBottom: 10 }}>
              <span className="pov-fact"><span className="pov-fact__k">Shell</span><span className="pov-fact__v">{c.shell}</span></span>
              <span className="pov-fact"><span className="pov-fact__k">Runs as</span><span className="pov-fact__v">{c.identity}</span></span>
              <span className="pov-fact"><span className="pov-fact__k">Digest</span><span className="pov-fact__v">{c.sha}</span></span>
            </span>
            <span className="pov-card__foot">
              <span className="pov-chip-plane" style={{ borderLeftColor: planeTint(c.plane) }}>{c.plane}</span>
              {c.dets.map((d) => <span className="pov-chip-det" key={d}>{d}</span>)}
              <span className="pov-card__spacer" />
              <span className="pov-fact__k" style={{ minWidth: 0 }}>{c.usedBy} chains</span>
            </span>
          </button>
        ))}

        {/* The add affordance is a card at the end of the grid AND a button in
            the section header. Two entry points for one action, because "how
            do I add a node" was a real question a DC asked of this console. */}
        <button
          type="button"
          className="pov-card"
          style={{ borderStyle: 'dashed', display: 'grid', placeItems: 'center', minHeight: 140 }}
          onClick={() => onNavigate('composer')}
        >
          <span className="pov-card__title">+ Add CLI item</span>
        </button>
      </div>

      {openId && (
        <ObjectSheet
          sheet={sheetFor('cli', openId, { go: (d) => () => { setOpenId(null); onNavigate(d) } })}
          onClose={() => setOpenId(null)}
        />
      )}
    </div>
  )
}
