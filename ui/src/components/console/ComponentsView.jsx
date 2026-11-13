import React, { useEffect, useRef } from 'react'
import '../../styles/destinations/components.css'
import { Button, ErrorBadge, PageIcon, StatusIcon } from './kit/index.js'
import { COMPONENTS } from './componentModel.js'
import useComponentStatus from './useComponentStatus.js'
import { componentConfigure, componentFix } from '../../app/componentRoutes.js'

/**
 * ComponentsView — the hub for the eight components a POV runs on (design
 * v26 §9.2): what is up, what is down, and the one thing to fix on each.
 *
 * The minimal pass retired the previous Components page because every row on
 * it was seed data. This one is built on real state only (componentModel.js):
 * a status is read from SimCore, or it reads Unknown — "Can't check", or "You
 * confirmed" — and is never green. There is no history here, only now (§4.2).
 *
 * A problem is an ErrorBadge: a button that opens the page that fixes it.
 * Configure opens where the component is configured today; a component with
 * nowhere to send the DC yet gets no button rather than a dead one.
 *
 * `?open=<key>` marks one component (the rail opens a row this way for the
 * components that have no page of their own yet).
 */
export default function ComponentsView({ params = {}, onNavigate = () => {} }) {
  const { statuses } = useComponentStatus()
  const open = params.open || null
  const openRef = useRef(null)

  useEffect(() => {
    const el = openRef.current
    if (el && typeof el.scrollIntoView === 'function') el.scrollIntoView({ block: 'nearest' })
  }, [open])

  const go = (route) => route && onNavigate(route.destination, route.params)

  return (
    <div className="pov-page cmp-hub" data-testid="components-view">
      <header className="cmp-hub__head">
        <div className="cmp-hub__title">
          <PageIcon icon="settings-edit" size="tile" />
          <h1 className="pov-page__title">Components</h1>
        </div>
        <p className="pov-page__lede">
          What is up, what is down, and the one thing to fix on each. A component POVengine cannot
          see reads Can&apos;t check — never green.
        </p>
      </header>

      <ul className="cmp-list">
        {COMPONENTS.map((c) => {
          const st = statuses[c.key] || { status: 'unknown' }
          const problem = st.status === 'degraded' || st.status === 'down'
          const configure = componentConfigure(c.key)
          const fix = componentFix(c.key)
          const isOpen = open === c.key
          return (
            <li
              key={c.key}
              ref={isOpen ? openRef : undefined}
              aria-label={c.name}
              aria-current={isOpen ? 'true' : undefined}
              className={'cmp-row' + (isOpen ? ' cmp-row--open' : '')}
              data-testid={`cmp-row-${c.key}`}
            >
              <PageIcon icon={c.icon} size="tile" />
              <div className="cmp-row__name">
                <span className="cmp-row__title">{c.name}</span>
                <span className="cmp-row__detail" title={st.detail}>{st.detail}</span>
              </div>
              <StatusIcon status={st.status} word={st.word} size="list" className="cmp-row__status" />
              <div className="cmp-row__issue">
                {problem && st.issue && (
                  fix ? (
                    <ErrorBadge tone={st.status === 'down' ? 'crit' : 'warn'} onClick={() => go(fix)}>
                      {st.issue}
                    </ErrorBadge>
                  ) : (
                    <span className={`cmp-row__issue-text cmp-row__issue-text--${st.status}`}>{st.issue}</span>
                  )
                )}
              </div>
              <div className="cmp-row__actions">
                {configure && <Button onClick={() => go(configure)}>Configure</Button>}
              </div>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
