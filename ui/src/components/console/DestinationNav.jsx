import React from 'react'

/**
 * DestinationNav — the persistent, grouped destination sidebar.
 *
 * Replaces ConsoleStepper as the console's PRIMARY navigation. Every
 * first-class destination is one click away at any time (no More▾ overflow,
 * no linear stepper). Reuses the existing rail tokens/classes
 * (`--w-rail` / `--w-rail-collapsed`, `.rail`, `.rail__group`,
 * `.rail__section-title`, `.plane-item*`) so it inherits the Cortex design
 * system without inventing new visuals.
 *
 * Props:
 *   groups        — [{ label, items: [{ id, label, icon, badge }] }]
 *   active        — current destination id
 *   onNavigate    — (destinationId) => void
 *   collapsed     — boolean (rail collapse persisted by the shell)
 *   onToggleCollapse — () => void
 */
export default function DestinationNav({
  groups = [],
  active = null,
  onNavigate = () => {},
  collapsed = false,
  onToggleCollapse = null,
}) {
  return (
    <nav
      className={'rail rail--nav' + (collapsed ? ' rail--collapsed' : '')}
      aria-label="Console destinations"
    >
      {onToggleCollapse && (
        <button
          type="button"
          className="rail__toggle"
          onClick={onToggleCollapse}
          aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
          title={collapsed ? 'Expand navigation' : 'Collapse navigation'}
        >
          {collapsed ? '▶' : '◀'}
        </button>
      )}

      {groups.map((group) => (
        <div className="rail__group" key={group.label}>
          {!collapsed && <div className="rail__section-title">{group.label}</div>}
          {group.items.map((item) => {
            const isActive = item.id === active
            return (
              <button
                key={item.id}
                type="button"
                data-testid={`dest-button-${item.id}`}
                data-tour-id={`nav-${item.id}`}
                className={'plane-item' + (isActive ? ' plane-item--active' : '')}
                aria-current={isActive ? 'page' : undefined}
                onClick={() => onNavigate(item.id)}
                title={item.label}
              >
                <span className="plane-item__code" aria-hidden="true">{item.icon || '▸'}</span>
                <span className="plane-item__name">{item.label}</span>
                {item.badge != null && item.badge !== '' && (
                  <span
                    className={
                      'plane-item__count' +
                      (item.badgeVariant === 'live' ? ' plane-item__count--live' : '')
                    }
                  >
                    {item.badge}
                  </span>
                )}
              </button>
            )
          })}
        </div>
      ))}
    </nav>
  )
}
