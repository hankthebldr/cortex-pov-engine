import React, { useCallback, useState } from 'react'
import '../../../styles/destinations/component-page.css'
import { Button, ErrorBadge, PageIcon, StatusIcon } from '../kit/index.js'

/**
 * ComponentPage — the one page shape for all eight components (design v26
 * §4.2).
 *
 *   Header         PageIcon · name · StatusIcon + word · Configure · Test
 *   Issue line     one sentence, only when something is wrong
 *   Tabs           Overview · Setup · one management tab (when it has one)
 *   Overview       Services (left) and Configuration (right)
 *   Setup          the Configure frame
 *
 * Services rows: StatusIcon, name, since — the whole row is a button. Up opens
 * where it is configured ("View ›"); Degraded/Down opens the Setup step that
 * fixes it ("Troubleshoot ›"). Configuration rows read OK · Missing · Changed
 * · Can't check, failing first, and open the fixing step ("Fix ›") or the
 * page that shows it ("View ›"). A row with nowhere to go is not a button.
 *
 * Current state only: no poll timelines, no "last polled" strips, no
 * history (§4.2). A component never configured reads Off, says "Configure it
 * to unlock its scenarios." and makes Configure the one primary (§7).
 *
 * Tab, step and path live in the URL (`?tab=&step=&path=`), so every view of
 * a component is deep-linkable and the browser's back button works.
 *
 * Props:
 *   name, icon, status { status, word?, detail?, issue? }
 *   issueStep     the Setup step the issue line's badge opens
 *   services      [{ id, name, status, word?, since?, drill? }]
 *   servicesEmpty the line shown when there are no services to list
 *   config        [{ id, label, state: ok|missing|changed|cant_check|confirmed, detail?, drill? }]
 *   manageTab     { id, label, render() } | null
 *   setup()       renders the Configure frame
 *   onTest()      → Promise<{ state, issue?, step? }>; omitted = no Test button
 *   drill         { tab, step?, path? } | { destination, params? }
 */

const CONFIG_LOOK = {
  missing:    { status: 'down',     word: 'Missing',     rank: 0 },
  changed:    { status: 'degraded', word: 'Changed',     rank: 1 },
  cant_check: { status: 'unknown',  word: "Can't check", rank: 2 },
  // The DC ticked the step that sets it up; POVengine cannot read it. Never OK.
  confirmed:  { status: 'unknown',  word: 'You confirmed', rank: 3 },
  ok:         { status: 'up',       word: 'OK',          rank: 4 },
}

export default function ComponentPage({
  name,
  icon,
  status = { status: 'unknown' },
  issueStep = null,
  lede = null,
  services = [],
  servicesEmpty = 'Nothing to show yet.',
  config = [],
  manageTab = null,
  setup = () => null,
  onTest = null,
  params = {},
  setParams = () => {},
  onNavigate = () => {},
  testId = 'component-page',
}) {
  const tabIds = ['overview', 'setup', ...(manageTab ? [manageTab.id] : [])]
  const tab = tabIds.includes(params.tab) ? params.tab : 'overview'
  const off = status.status === 'off'
  const problem = status.status === 'degraded' || status.status === 'down'

  const [test, setTest] = useState({ state: 'idle' })
  const runTest = useCallback(async () => {
    if (!onTest) return
    setTest({ state: 'testing' })
    try {
      setTest((await onTest()) || { state: 'failed', issue: 'No result' })
    } catch (err) {
      setTest({ state: 'failed', issue: err?.code || 'Test failed', detail: err?.message })
    }
  }, [onTest])

  const openSetup = (step = null) => setParams({ tab: 'setup', step })
  const go = (drill) => {
    if (!drill) return
    if (drill.destination) onNavigate(drill.destination, drill.params)
    else setParams({ tab: drill.tab, ...(drill.step !== undefined ? { step: drill.step } : {}), ...(drill.path ? { path: drill.path } : {}) })
  }

  // One primary per view (v26 §4.1): Configure while the component is Off,
  // Test otherwise — and neither on Setup, where the current step's Test is
  // the action.
  const headPrimary = tab === 'setup' ? null : off ? 'configure' : 'test'

  const testLabel = test.state === 'testing' ? 'Testing…' : test.state === 'passed' ? 'Passed' : 'Test'
  // When the Test's failure is the page's issue (a check this session drives
  // the status), the issue line already carries the badge; one is enough.
  const testFailed = (test.state === 'failed' || test.state === 'degraded' || test.state === 'cant_check')
    && !(problem && status.issue && status.issue === test.issue)

  return (
    <div className="pov-page cmp-page" data-testid={testId}>
      <header className="cmp-page__head">
        <div className="cmp-page__id">
          <div className="cmp-page__title">
            <PageIcon icon={icon} size="tile" />
            <h1 className="pov-page__title">{name}</h1>
          </div>
          <div className="cmp-page__status">
            <StatusIcon status={status.status} word={status.word} size="list" />
            {status.detail && <span className="cmp-page__detail">{status.detail}</span>}
          </div>
          {lede && <p className="pov-page__lede cmp-page__lede">{lede}</p>}
        </div>
        <div className="cmp-page__actions">
          {testFailed && test.issue && (
            <ErrorBadge
              tone={test.state === 'failed' ? 'crit' : 'warn'}
              onClick={() => openSetup(test.step || issueStep)}
            >
              {test.issue}
            </ErrorBadge>
          )}
          <Button variant={headPrimary === 'configure' ? 'primary' : 'secondary'} onClick={() => openSetup(null)}>Configure</Button>
          {onTest && (
            <Button variant={headPrimary === 'test' ? 'primary' : 'secondary'} onClick={runTest} disabled={test.state === 'testing'}>
              {testLabel}
            </Button>
          )}
        </div>
      </header>

      {off && <p className="cmp-page__off">Configure it to unlock its scenarios.</p>}

      {problem && (
        <div className={`cmp-issue cmp-issue--${status.status}`} data-testid="cmp-issue">
          {status.issue && (
            <ErrorBadge tone={status.status === 'down' ? 'crit' : 'warn'} onClick={() => openSetup(issueStep)}>
              {status.issue}
            </ErrorBadge>
          )}
          <span className="cmp-issue__text">{status.detail}</span>
        </div>
      )}

      <div className="cmp-tabs" role="group" aria-label="Component pages">
        {[['overview', 'Overview'], ['setup', 'Setup'], ...(manageTab ? [[manageTab.id, manageTab.label]] : [])].map(([id, label]) => (
          <button
            key={id}
            type="button"
            className={'cmp-tab' + (tab === id ? ' cmp-tab--on' : '')}
            aria-pressed={tab === id}
            onClick={() => setParams({ tab: id === 'overview' ? null : id, step: null })}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'overview' && (
        <div className="cmp-overview">
          <section className="cmp-card" aria-label="Services">
            <h2 className="cmp-card__title">Services</h2>
            {services.length === 0
              ? <p className="cmp-card__empty">{servicesEmpty}</p>
              : (
                <ul className="cmp-rows">
                  {services.map((s) => <ServiceRow key={s.id} row={s} onOpen={() => go(s.drill)} />)}
                </ul>
              )}
          </section>
          <section className="cmp-card" aria-label="Configuration">
            <h2 className="cmp-card__title">
              Configuration
              {config.length > 0 && (
                <span className="cmp-card__count">
                  {config.filter((c) => c.state === 'ok').length} of {config.length} OK
                </span>
              )}
            </h2>
            <ul className="cmp-rows">
              {[...config]
                .sort((a, b) => (CONFIG_LOOK[a.state]?.rank ?? 2) - (CONFIG_LOOK[b.state]?.rank ?? 2))
                .map((c) => <ConfigRow key={c.id} row={c} onOpen={() => go(c.drill)} />)}
            </ul>
          </section>
        </div>
      )}

      {tab === 'setup' && <div className="cmp-setup">{setup()}</div>}
      {manageTab && tab === manageTab.id && <div className="cmp-manage">{manageTab.render()}</div>}
    </div>
  )
}

function RowInner({ status, word, title, sub, drillLabel }) {
  return (
    <>
      <StatusIcon status={status} word={word} />
      <span className="cmp-row__main">
        <span className="cmp-row__title">{title}</span>
        {sub && <span className="cmp-row__sub">{sub}</span>}
      </span>
      {drillLabel && <span className="cmp-row__drill" aria-hidden="true">{drillLabel}</span>}
    </>
  )
}

function ServiceRow({ row, onOpen }) {
  const bad = row.status === 'degraded' || row.status === 'down'
  const inner = (
    <RowInner status={row.status} word={row.word} title={row.name} sub={row.since}
      drillLabel={row.drill ? (bad ? 'Troubleshoot ›' : 'View ›') : null} />
  )
  return (
    <li className="cmp-rows__item">
      {row.drill
        ? <button type="button" className="cmp-row" onClick={onOpen}>{inner}</button>
        : <div className="cmp-row cmp-row--static">{inner}</div>}
    </li>
  )
}

function ConfigRow({ row, onOpen }) {
  const look = CONFIG_LOOK[row.state] || CONFIG_LOOK.cant_check
  const inner = (
    <RowInner status={look.status} word={look.word} title={row.label} sub={row.detail}
      drillLabel={row.drill ? (row.state === 'ok' ? 'View ›' : 'Fix ›') : null} />
  )
  return (
    <li className="cmp-rows__item">
      {row.drill
        ? <button type="button" className="cmp-row" onClick={onOpen}>{inner}</button>
        : <div className="cmp-row cmp-row--static">{inner}</div>}
    </li>
  )
}
