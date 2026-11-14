import React from 'react'
import { sectionFor } from '../../app/destinations.jsx'

/**
 * SectionTabs — the tab strip across the top of a task's pages.
 *
 * Mounted ONCE, by the shell, above whichever surface is open. It used to be
 * imported by four individual views, which meant the strip existed on some of
 * a task's pages and not others, and each copy was one more place a rename
 * could miss. Every tab is a real destination, so the strip navigates and owns
 * no state — it cannot disagree with the rail about where you are.
 *
 * Renders nothing for a task without tabs (Get started, Runs, Agents, Tenant),
 * or with one page: a strip with a single tab is not a choice. Results is one
 * page since its Coverage tab became the Dashboards rail item.
 */
export default function SectionTabs({ destination, onNavigate = () => {} }) {
  const section = sectionFor(destination)
  if (!section || section.tabs.length < 2) return null
  return (
    <div className="pov-tabs pov-section-tabs" data-testid="section-tabs" role="tablist" aria-label="Pages in this task">
      {section.tabs.map(([id, label]) => {
        const on = id === section.active
        return (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={on}
            data-testid={`section-tab-${id}`}
            className={'pov-tab' + (on ? ' pov-tab--on' : '')}
            onClick={() => onNavigate(id)}
          >
            {label}
          </button>
        )
      })}
    </div>
  )
}
