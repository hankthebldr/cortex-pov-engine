import React from 'react'

const TABS = [
  { id: 'operations', label: 'Operations' },
  { id: 'inflight',   label: 'In-Flight' },
  { id: 'evidence',   label: 'Evidence'  },
  { id: 'lab',        label: 'Lab'       },
  { id: 'coverage',   label: 'ATT\u0026CK Coverage' },
  { id: 'tenants',    label: 'Tenants'  },
]

/**
 * ConsoleTabs — six-tab workspace switcher.
 *
 * Props:
 *   activeTab    — string id
 *   onTabChange  — (id) => void
 *   badges       — { [id]: string | { text, variant } }
 */
export default function ConsoleTabs({ activeTab, onTabChange, badges = {} }) {
  return (
    <nav className="tabs" role="tablist" aria-label="Console views">
      {TABS.map((t) => {
        const badge = badges[t.id]
        const badgeText = typeof badge === 'object' && badge ? badge.text : badge
        const badgeVariant = typeof badge === 'object' && badge ? badge.variant : null
        return (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={activeTab === t.id}
            className={'tab' + (activeTab === t.id ? ' tab--active' : '')}
            onClick={() => onTabChange(t.id)}
          >
            {t.label}
            {badgeText && (
              <span
                className={'tab__badge' + (badgeVariant === 'live' ? ' tab__badge--live' : '')}
              >
                {badgeText}
              </span>
            )}
          </button>
        )
      })}
    </nav>
  )
}
