import React from 'react'

/**
 * ConsoleRail — left-side 240px rail with plane filter + pinned + MITRE shortcuts.
 *
 * Props:
 *   planes         — [{ code, name, count, isActive }]
 *   pinned         — [{ id, name }] — pinned scenarios (most-recent first)
 *   mitreFilters   — [{ tactic, name, count }]
 *   onSelectPlane  — (planeCode) => void
 *   onSelectPinned — (scenarioId) => void
 *   onUnpin        — (scenarioId) => void  (optional; if provided, hover unpin appears)
 */
export default function ConsoleRail({
  planes = [],
  pinned = [],
  mitreFilters = [],
  onSelectPlane = () => {},
  onSelectPinned = () => {},
  onUnpin = null,
}) {
  return (
    <aside className="rail">
      <div className="rail__group">
        <div className="rail__section-title">Detection Planes</div>
        {planes.map((p) => (
          <button
            key={p.code}
            type="button"
            className={'plane-item' + (p.isActive ? ' plane-item--active' : '')}
            onClick={() => onSelectPlane(p.code)}
          >
            <span className="plane-item__code">{p.code}</span>
            <span className="plane-item__name">{p.name}</span>
            {p.count != null && <span className="plane-item__count">{p.count}</span>}
          </button>
        ))}
      </div>

      {pinned.length > 0 && (
        <div className="rail__group">
          <div className="rail__section-title">
            Pinned <span className="rail__hint">⌘L launch</span>
          </div>
          {pinned.map((s) => (
            <div key={s.id} className="plane-item plane-item--pinned" style={{ fontSize: 10 }}>
              <button
                type="button"
                className="plane-item__hit"
                onClick={() => onSelectPinned(s.id)}
                title={`Open ${s.id}`}
              >
                <span className="plane-item__code">{s.id}</span>
                <span className="plane-item__name">{s.name}</span>
              </button>
              {onUnpin && (
                <button
                  type="button"
                  className="plane-item__unpin"
                  onClick={(e) => { e.stopPropagation(); onUnpin(s.id) }}
                  aria-label={`Unpin ${s.id}`}
                  title="Unpin"
                >×</button>
              )}
            </div>
          ))}
        </div>
      )}

      {mitreFilters.length > 0 && (
        <div className="rail__group">
          <div className="rail__section-title">Filters · MITRE</div>
          {mitreFilters.map((f) => (
            <div key={f.tactic} className="plane-item" style={{ fontSize: 10 }}>
              <span className="plane-item__code">{f.tactic}</span>
              <span className="plane-item__name">{f.name}</span>
              {f.count != null && <span className="plane-item__count">{f.count}</span>}
            </div>
          ))}
        </div>
      )}
    </aside>
  )
}
