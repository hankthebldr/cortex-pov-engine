import React from 'react'

/**
 * ComposeTabs — the sibling strip across the Compose destinations.
 *
 * IT NAVIGATES. IT DOES NOT HOLD LOCAL STATE.
 * Each tab is a real destination, so the rail and the strip cannot disagree by
 * construction — there is no second source of "which view am I on" to drift.
 * The earlier version was local tab state inside the Library, which meant the
 * rail could say Library while the strip said TTP Cards, and only one of them
 * was right about what was rendered.
 *
 * It is deliberately absent from UC / TC Index: that is a fixed index rather
 * than one of the browsable catalogs, and a strip implying it is a sibling view
 * of the Library would be claiming a symmetry that is not there.
 *
 * The control that got you here is still here on arrival — which is the whole
 * argument for repeating it on five surfaces rather than putting it on one.
 *
 * Props:
 *   active     — destination id
 *   onNavigate — (destinationId) => void
 *   counts     — { [destinationId]: number|string }  optional per-tab counts
 */
const TABS = [
  ['library', 'Scenarios'],
  ['cli', 'CLI items'],
  ['packages', 'Packages'],
  ['ttps', 'TTP cards'],
  ['streams', 'Data streams'],
]

export default function ComposeTabs({ active, onNavigate = () => {}, counts = {} }) {
  return (
    <div className="pov-tabs" data-testid="compose-tabs" role="tablist" aria-label="Compose catalogs">
      {TABS.map(([id, label]) => {
        const on = id === active
        return (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={on}
            data-testid={`compose-tab-${id}`}
            className={'pov-tab' + (on ? ' pov-tab--on' : '')}
            onClick={() => onNavigate(id)}
          >
            {label}
            {counts[id] != null && (
              <span className="pov-rail__badge" style={{ marginLeft: 6 }}>{counts[id]}</span>
            )}
          </button>
        )
      })}
    </div>
  )
}
